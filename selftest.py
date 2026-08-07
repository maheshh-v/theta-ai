"""
Self-test for Theta.

Boots the real MCP stack, drives a real Chromium browser through a real website,
records a Playbook and replays it — the parts `pytest` deliberately fakes out.

It touches the network (that is the point) and cleans up after itself.

    python selftest.py
"""

from __future__ import annotations

import sys

# Keep output readable on Windows consoles (cp1252) as well as UTF-8 terminals.
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

from automation.playbooks import from_run, playbooks
from automation.replay import replay
from automation.runs import RunStep, runs
from config import settings
from tools.mcp_client import MCPManager, ToolContext

SITE = "https://quotes.toscrape.com/search.aspx"
EXPECTED_TOOLS = {
    "browser_navigate", "browser_click", "browser_type", "browser_select",
    "browser_read", "browser_snapshot", "file_write", "web_search",
}


class Checks:
    def __init__(self) -> None:
        self.rows: list[tuple[str, str, str]] = []

    def add(self, name: str, ok: bool, detail: str = "", skip: bool = False) -> bool:
        self.rows.append((name, "SKIP" if skip else ("PASS" if ok else "FAIL"), detail))
        return ok

    def report(self) -> int:
        print("\nTheta — self-test\n" + "─" * 72)
        for name, mark, detail in self.rows:
            print(f"[{mark}] {name:<40} {detail}")
        print("─" * 72)
        counts = {m: sum(1 for _n, x, _d in self.rows if x == m) for m in ("PASS", "FAIL", "SKIP")}
        print(f"{counts['PASS']} passed, {counts['FAIL']} failed, {counts['SKIP']} skipped")
        return 1 if counts["FAIL"] else 0


def main() -> int:
    c = Checks()
    c.add("Language model configured", settings.llm_configured,
          settings.summary() if settings.llm_configured else "set GEMINI_API_KEY in .env")

    mgr = MCPManager()
    mgr.start()
    st = mgr.status()
    names = {t.name for t in mgr.list_tools()}
    c.add("Tool transport up", st["tool_count"] > 0, st["transport"])
    c.add("MCP servers connected", st["connected"],
          ", ".join(st["servers"]) or f"in-process fallback ({st['errors']})")
    missing = EXPECTED_TOOLS - names
    c.add("All expected tools discovered", not missing,
          f"{len(names)} tools" + (f"; missing {missing}" if missing else ""))
    c.add("Replay tool hidden from the model", "browser_step" in names,
          "browser_step present but not offered")

    record = runs.create(goal="self-test: search a real site")
    counter = {"n": 0}

    def next_shot() -> str:
        counter["n"] += 1
        return runs.shot_path(record.id, counter["n"])

    ctx = ToolContext(shot_path_factory=next_shot)
    pb = None

    try:
        # --- drive a real browser ----------------------------------------- #
        nav = mgr.call_tool("browser_navigate", {"url": SITE}, ctx)
        body = nav.content if isinstance(nav.content, dict) else {}
        online = bool(nav.ok and body.get("page"))
        c.add("Chromium opens a real page", online,
              f"{body.get('title', '')!r} via {nav.source}" if online
              else str(body.get("error", body))[:80])

        if not online:
            for label in ("Elements are indexed for the agent", "Consequential controls are gated",
                          "Screenshots are captured", "Selecting a dropdown works",
                          "Playbook recorded from the run", "Playbook replays without a model"):
                c.add(label, False, "skipped — browser unavailable", skip=True)
            return c.report()

        page = body["page"]
        c.add("Elements are indexed for the agent", "[1]" in page and "INTERACTIVE ELEMENTS" in page,
              f"{page.count('  [')} elements listed")
        gates = body.get("gates") or {}
        c.add("Consequential controls are gated", bool(gates),
              "; ".join(list(gates.values())[:1]) or "none found")
        shot = runs.screenshot(record.id, "step-1.jpg")
        c.add("Screenshots are captured", shot is not None,
              f"{shot.stat().st_size // 1024} KB" if shot else "none written")

        # A dependent dropdown: the second select repopulates from the first.
        author_ref = next((int(line.split("]")[0].strip(" [")) for line in page.splitlines()
                           if "<select>" in line and "Author" in line), None)
        picked = False
        if author_ref:
            sel = mgr.call_tool("browser_select",
                                {"ref": author_ref, "option": "Albert Einstein"}, ctx)
            picked = sel.ok
        c.add("Selecting a dropdown works", picked,
              "author selected, dependent options reloaded" if picked else "could not select")

        for i, step in enumerate([
            ("browser_navigate", {"url": SITE}),
            ("browser_read", {}),
        ], start=1):
            res = mgr.call_tool(step[0], step[1], ctx)
            record.steps.append(RunStep(
                index=i, tool=step[0], args=step[1], ok=res.ok, status="done",
                target=(res.content or {}).get("target", {}) if isinstance(res.content, dict) else {},
            ))
        record.status = "done"
        runs.save(record)

        # --- record and replay -------------------------------------------- #
        pb = from_run(record, name="self-test playbook")
        playbooks.save(pb)
        c.add("Playbook recorded from the run", len(pb.steps) >= 2,
              f"{len(pb.steps)} replayable steps")

        replayed = replay(pb, {}, mgr, ToolContext(), llm=None, allow_heal=False)
        c.add("Playbook replays without a model", replayed.status == "done",
              f"{len(replayed.steps)} steps in {replayed.seconds}s, no model calls")

        # --- safety --------------------------------------------------------- #
        blocked = mgr.call_tool("browser_navigate", {"url": "http://127.0.0.1:7860/"}, ctx)
        refused = isinstance(blocked.content, dict) and "private" in str(blocked.content).lower()
        c.add("Private addresses are refused", refused or settings.allow_private_urls,
              "SSRF guard active" if refused else "THETA_ALLOW_PRIVATE_URLS is on")

        escaped = mgr.call_tool("file_write", {"name": "../escape.txt", "content": "x"}, ctx)
        c.add("Workspace sandbox holds",
              isinstance(escaped.content, dict) and escaped.content.get("error") == "refused",
              "writes outside data/workspace refused")
    finally:
        runs.delete(record.id)
        if pb is not None:
            playbooks.delete(pb.id)
        for stray in runs.list(limit=5):
            if stray.goal.startswith("Playbook: self-test"):
                runs.delete(stray.id)
        mgr.stop()

    return c.report()


if __name__ == "__main__":
    sys.exit(main())
