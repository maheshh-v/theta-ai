"""
Chat streaming: drives an `AgentRun` and streams its trace to the browser as
newline-delimited JSON (NDJSON) over a POST `fetch()` stream — which, unlike the
browser's GET-only EventSource, keeps the (potentially sensitive) message out of
the URL.

A run that hits an approval-gated action pauses and is parked in the
`RunRegistry`; the client approves/rejects via `/api/chat/resume`, which resumes
the same run and streams the rest.
"""

from __future__ import annotations

import asyncio
import json
import threading
from secrets import token_urlsafe

from starlette.responses import StreamingResponse

from agent.orchestrator import Agent, AgentResult, PendingApproval
from server import accounts, llm_settings
from server.session import Session
from tools.mcp_client import ToolContext


class RunRegistry:
    """Parks paused runs between the initial request and the approval decision."""

    def __init__(self) -> None:
        self._runs: dict[str, tuple[str, object]] = {}

    def add(self, sid: str, run) -> str:
        rid = token_urlsafe(12)
        self._runs[rid] = (sid, run)
        return rid

    def get(self, rid: str, sid: str):
        entry = self._runs.get(rid)
        return entry[1] if entry and entry[0] == sid else None

    def remove(self, rid: str) -> None:
        self._runs.pop(rid, None)


def make_agent(app, session: Session) -> tuple[Agent, ToolContext]:
    """Build a per-session agent (its own LLM config) and a tool context whose
    Google-token provider always returns a freshly-refreshed token."""
    llm = llm_settings.build_session_llm(session)
    agent = Agent(app.state.mcp, llm)
    ctx = ToolContext(google_token_provider=lambda: accounts.get_access_token(session))
    return agent, ctx


def _step_dict(s) -> dict:
    return {
        "index": s.index,
        "tool": s.tool,
        "label": s.label,
        "summary": s.summary,
        "ok": s.ok,
        "status": s.status,
        "tag": s.tag,
        "source": s.source,
        "args": s.args,
        "observation": s.observation,
    }


def stream_run(app, session: Session, run, run_id: str,
               approved: bool | None) -> StreamingResponse:
    """Return a streaming response that advances `run` once (to completion or the
    next approval pause), emitting trace events as NDJSON lines."""
    loop = asyncio.get_running_loop()
    q: asyncio.Queue = asyncio.Queue()

    def emit(event: dict) -> None:
        loop.call_soon_threadsafe(q.put_nowait, event)

    run.on_event = emit

    def worker() -> None:
        try:
            outcome = run.advance(approved)
        except Exception as ex:  # pragma: no cover - defensive
            outcome = AgentResult(answer=f"⚠️ Something went wrong: {ex}")
        loop.call_soon_threadsafe(q.put_nowait, {"__outcome__": outcome})

    threading.Thread(target=worker, daemon=True).start()

    async def gen():
        while True:
            item = await q.get()
            if isinstance(item, dict) and "__outcome__" in item:
                outcome = item["__outcome__"]
                if isinstance(outcome, PendingApproval):
                    yield _line({
                        "type": "awaiting_approval",
                        "run_id": run_id,
                        "index": outcome.index,
                        "tool": outcome.tool,
                        "label": outcome.label,
                        "description": outcome.description,
                        "args": outcome.args,
                    })
                else:
                    app.state.runs.remove(run_id)
                    yield _line({
                        "type": "final",
                        "answer": outcome.answer,
                        "steps": [_step_dict(s) for s in outcome.steps],
                        "transport": outcome.transport,
                        "llm": outcome.llm_label,
                        "error": outcome.error,
                    })
                yield _line({"type": "done"})
                break
            yield _line(item)

    return StreamingResponse(gen(), media_type="application/x-ndjson")


def _line(payload: dict) -> str:
    return json.dumps(payload, ensure_ascii=False) + "\n"
