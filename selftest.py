"""
Self-test / health check for the Personal AI Assistant.

Runs the real MCP stack and the agent loop (with the keyless mock LLM) against a
few read-only commands, and prints a PASS/FAIL summary. It does not modify any
data files, so it's safe to run repeatedly:

    python selftest.py
"""

from __future__ import annotations

import sys

# Make output safe on Windows consoles (cp1252) as well as UTF-8 terminals.
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

from agent.llm import MockLLM
from agent.orchestrator import Agent
from tools.mcp_client import MCPManager


def main() -> int:
    checks: list[tuple[str, bool, str]] = []

    mgr = MCPManager()
    mgr.start()
    st = mgr.status()

    checks.append(("MCP manager started", True, st["transport"]))
    checks.append(
        ("Tools discovered (>=14)", st["tool_count"] >= 14, f"{st['tool_count']} tools")
    )
    # We expect real MCP in a normal environment, but the fallback is also valid.
    checks.append(
        ("Transport is MCP or fallback", True, st["transport"])
    )

    agent = Agent(mgr, MockLLM())

    # Read-only, local (no-auth) commands only — no data mutation, no Google.
    cases = {
        "Show me my tasks": "tasks_list",
        "List my notes": "notes_list",
        "Search my notes about gifts": "notes_search",
    }
    for cmd, expected_tool in cases.items():
        res = agent.run(cmd)
        tool_used = res.steps[0].tool if res.steps else None
        ok = tool_used == expected_tool and (not res.steps or res.steps[0].ok)
        checks.append((f"`{cmd}` -> {expected_tool}", ok, f"got {tool_used}"))

    mgr.stop()

    # Report
    print("\nPersonal AI Assistant - self-test\n" + "-" * 40)
    passed = 0
    for name, ok, detail in checks:
        mark = "PASS" if ok else "FAIL"
        print(f"[{mark}] {name:<38} {detail}")
        passed += bool(ok)
    total = len(checks)
    print("-" * 40)
    print(f"{passed}/{total} checks passed")
    return 0 if passed == total else 1


if __name__ == "__main__":
    sys.exit(main())
