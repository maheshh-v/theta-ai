"""
Replaying a Playbook — and repairing it when the web moves underneath it.

Replay is deliberately dumb: walk the recorded steps, resolve each element by its
stored selectors, act. **No model calls.** That is the entire economic argument
for Playbooks — the expensive reasoning happened once, during the run that
created it.

The interesting part is what happens when a step stops resolving because the site
was redesigned. Rather than failing the whole automation, that *one* step
escalates: take a fresh snapshot, ask the model which element on the page matches
the recorded description, act on it, and write the new selectors back into the
playbook. The automation heals in place and the next run is deterministic again.
"""

from __future__ import annotations

import json
import logging
import time
from datetime import datetime, timezone
from typing import Callable

from agent.llm import BaseLLM, LLMError
from agent.parsing import parse_object
from automation.playbooks import API_PREFIX, Playbook, PlaybookStep, playbooks
from automation.runs import Run, RunStep, runs
from tools import catalog
from tools.mcp_client import MCPManager, ToolContext

_log = logging.getLogger("theta.replay")

Progress = Callable[[dict], None]

_HEAL_SYSTEM = """\
You repair a broken step in a browser automation. A recorded step can no longer \
find the element it used to act on, because the page has changed.

You are given what the step was trying to do and the page's current interactive \
elements. Pick the element that serves the same purpose.

Reply with ONLY a JSON object:
{"ref": <the element number>, "confidence": "high"|"low", "why": "<a few words>"}

If nothing on this page serves that purpose, reply {"ref": 0, "confidence": "low", \
"why": "<what is missing>"}. Never guess a number that is not listed.
"""


class ReplayError(RuntimeError):
    """Raised when a playbook cannot be replayed at all."""


def replay(
    playbook: Playbook,
    values: dict,
    mgr: MCPManager,
    context: ToolContext,
    llm: BaseLLM | None = None,
    on_progress: Progress | None = None,
    allow_heal: bool = True,
    trigger: str = "playbook",
) -> Run:
    """Run a playbook end to end and return the Run that records it."""
    emit: Progress = on_progress or (lambda _e: None)
    started = time.time()
    steps = playbook.resolve(values)
    if not steps:
        raise ReplayError("This playbook has no steps to run.")

    run = runs.create(
        goal=f"Playbook: {playbook.name}",
        playbook_id=playbook.id,
        model=(llm.label if llm else ""),
        trigger=trigger,
    )
    context.shot_path_factory = _shot_factory(run.id)
    emit({"type": "run_started", "record_id": run.id, "goal": run.goal})

    healed_any = False
    failed = False
    read_text = ""

    for i, step in enumerate(steps, start=1):
        emit({"type": "replay", "stage": "step", "index": i, "total": len(steps),
              "description": step.describe()})

        result, healed = _execute(step, mgr, context, llm if allow_heal else None,
                                  playbook, i - 1, emit)
        healed_any = healed_any or healed
        content = result.content if isinstance(result.content, dict) else {}

        rs = RunStep(
            index=i,
            tool=(step.action[len(API_PREFIX):] if step.action.startswith(API_PREFIX)
                  else f"playbook_{step.action}"),
            args={"value": step.value} if step.value else {},
            label=step.describe(),
            summary=_summarise(step, content, healed),
            ok=result.ok,
            status="done" if result.ok else "error",
            url=content.get("url", ""),
            screenshot=_shot_name(content.get("screenshot", "")),
            healed=healed,
        )
        run.steps.append(rs)
        runs.save(run)
        event = {"type": "tool_end", "index": i, "tool": rs.tool, "ok": rs.ok,
                 "label": rs.label, "status": rs.status, "summary": rs.summary,
                 "screenshot": rs.screenshot, "record_id": run.id, "url": rs.url}
        if "verified" in content:
            event["verified"] = bool(content["verified"])
        emit(event)

        if step.action == "read" and content.get("text"):
            read_text = str(content["text"])
        if step.action == "file_write" and result.ok:
            path = content.get("path")
            if path:
                run.outputs.append(path)

        if not result.ok:
            failed = True
            break

    run.status = "failed" if failed else "done"
    run.seconds = round(time.time() - started, 1)
    run.answer = _final_message(playbook, run, healed_any, read_text)
    runs.save(run)

    playbook.run_count += 1
    playbook.last_run = datetime.now(timezone.utc).isoformat(timespec="seconds")
    playbook.last_status = run.status
    playbooks.save(playbook)

    emit({"type": "replay", "stage": "done", "run_id": run.id, "status": run.status,
          "healed": healed_any, "seconds": run.seconds})
    return run


# --------------------------------------------------------------------------- #
# One step                                                                    #
# --------------------------------------------------------------------------- #
def _execute(step: PlaybookStep, mgr, context, llm, playbook, step_index, emit):
    """Run one recorded step, healing it if its element has gone."""
    # Account-backed steps (Notion, Gmail) address records by id, so they replay
    # exactly as recorded. Nothing can drift out from under them, and there is
    # correspondingly nothing to heal.
    if step.action.startswith(API_PREFIX):
        tool = step.action[len(API_PREFIX):]
        args = dict((step.target or {}).get("args") or {})
        return mgr.call_tool(tool, args, context), False

    if step.action == "navigate":
        return mgr.call_tool("browser_navigate", {"url": step.value}, context), False
    if step.action == "scroll":
        return mgr.call_tool("browser_scroll", {"direction": step.value or "down"}, context), False
    if step.action == "back":
        return mgr.call_tool("browser_back", {}, context), False
    if step.action == "read":
        return mgr.call_tool("browser_read", {}, context), False
    if step.action == "wait_for":
        return mgr.call_tool("browser_wait_for", {"text": step.value}, context), False
    if step.action == "file_write":
        return mgr.call_tool(
            "file_write",
            {"name": step.target.get("name", "output.txt"), "content": step.value},
            context,
        ), False

    # Element-addressed actions: try the recorded selectors first.
    result = mgr.call_tool("browser_step", {
        "action": step.action,
        "target": json.dumps(step.target or {}),
        "value": step.value,
        "submit": bool(step.submit),
    }, context)
    if result.ok:
        return result, False

    if llm is None:
        return result, False

    emit({"type": "replay", "stage": "healing", "description": step.describe()})
    healed = _heal(step, mgr, context, llm, playbook, step_index, emit)
    if healed is None:
        return result, False
    return healed, True


def _heal(step: PlaybookStep, mgr, context, llm, playbook, step_index, emit):
    """Ask the model to re-identify this step's element on the current page, act,
    and write the repaired selectors back into the playbook."""
    snap = mgr.call_tool("browser_snapshot", {}, context)
    page = (snap.content or {}).get("page", "") if isinstance(snap.content, dict) else ""
    if not page:
        return None

    want = step.describe()
    prompt = (
        f"THE STEP THAT BROKE: {want}\n"
        f"RECORDED ELEMENT: {json.dumps(step.target or {}, ensure_ascii=False)[:600]}\n\n"
        f"THE PAGE NOW:\n{page}"
    )
    try:
        raw = llm.complete(_HEAL_SYSTEM, prompt, max_tokens=1200)
    except LLMError as ex:
        _log.warning("Self-heal failed: %s", ex)
        return None

    choice = parse_object(raw) or {}
    try:
        ref = int(choice.get("ref", 0))
    except (TypeError, ValueError):
        ref = 0
    if ref <= 0:
        emit({"type": "replay", "stage": "heal_failed",
              "why": str(choice.get("why", "no matching element"))[:160]})
        return None

    args = {"ref": ref}
    if step.action == "type":
        args.update({"text": step.value, "submit": bool(step.submit)})
        tool = "browser_type"
    elif step.action == "select":
        args.update({"option": step.value})
        tool = "browser_select"
    else:
        tool = "browser_click"

    result = mgr.call_tool(tool, args, context)
    if not result.ok:
        return None

    # Write the repair back so the next run is deterministic again.
    target = (result.content or {}).get("target") if isinstance(result.content, dict) else None
    if target:
        playbook.steps[step_index].target = target
        playbooks.save(playbook)
        emit({"type": "replay", "stage": "healed", "description": want,
              "why": str(choice.get("why", ""))[:120]})
    return result


# --------------------------------------------------------------------------- #
# Helpers                                                                     #
# --------------------------------------------------------------------------- #
def _shot_factory(run_id: str):
    counter = {"n": 0}

    def next_shot() -> str:
        counter["n"] += 1
        return runs.shot_path(run_id, counter["n"])

    return next_shot


def _shot_name(path: str) -> str:
    return path.replace("\\", "/").rsplit("/", 1)[-1] if path else ""


def _summarise(step: PlaybookStep, content: dict, healed: bool) -> str:
    if content.get("error"):
        return f"⚠️ {str(content.get('message') or content['error'])[:90]}"
    if step.action.startswith(API_PREFIX):
        return catalog.summarize(step.action[len(API_PREFIX):], content)
    base = catalog.summarize(f"browser_{step.action}", content) if step.action not in (
        "file_write", "read"
    ) else None
    if step.action == "file_write":
        base = f"Saved {content.get('path', '')}"
    elif step.action == "read":
        base = f"Read {content.get('chars', 0):,} characters"
    return (base or step.describe()) + (" (re-found after a page change)" if healed else "")


def _final_message(playbook: Playbook, run: Run, healed: bool, read_text: str) -> str:
    if run.status == "failed":
        last = run.steps[-1] if run.steps else None
        where = f" at step {last.index} ({last.label})" if last else ""
        return (
            f"“{playbook.name}” stopped{where}. "
            f"{last.summary if last else ''}\n\nThe site may have changed in a way I "
            "could not repair on my own — open it in Do mode and I'll work it out again."
        )
    parts = [f"Ran “{playbook.name}” — {len(run.steps)} steps in {run.seconds}s, no model needed"
             + (" (one step had to be re-found after a page change, and I've updated the "
                "playbook)" if healed else "") + "."]
    if run.outputs:
        parts.append("Saved: " + ", ".join(run.outputs))
    return "\n\n".join(parts)
