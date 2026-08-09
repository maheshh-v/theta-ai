"""
Streaming a run to the browser.

Drives an `AgentRun` (or a Playbook replay) and streams its trace as
newline-delimited JSON over a POST `fetch()` — which, unlike the browser's
GET-only EventSource, keeps the goal out of the URL.

Every step is recorded into a `Run` as it happens, so a task that dies halfway
still leaves a reviewable trail, and a successful one can be turned into a
Playbook afterwards. A run that reaches a consequential action pauses and is
parked in the `RunRegistry` until the user decides.
"""

from __future__ import annotations

import asyncio
import json
import logging
import threading
from secrets import token_urlsafe
from time import time

from starlette.responses import StreamingResponse

from agent.llm import LLMError
from agent.orchestrator import Agent, AgentResult, PendingApproval
from automation.gate import run_gate
from automation.runs import RunStep, runs
from config import settings
from server import preferences
from server.session import Session
from tools import catalog

_log = logging.getLogger("theta.chat")

_HISTORY_KEY = "history"
_RUN_TTL = 60 * 60  # a parked run expires after an hour

# How long a live run waits for the shared browser when a scheduled run has it.
# It proceeds anyway afterwards: making a person watch a spinner indefinitely
# because of a background job is worse than the collision this avoids.
_GATE_WAIT = 45.0


class RunRegistry:
    """Parks paused runs between the initial request and the approval decision."""

    def __init__(self) -> None:
        self._runs: dict[str, tuple[str, object, float]] = {}
        self._lock = threading.Lock()

    def add(self, sid: str, run) -> str:
        rid = token_urlsafe(12)
        with self._lock:
            self._sweep()
            self._runs[rid] = (sid, run, time())
        return rid

    def get(self, rid: str, sid: str):
        with self._lock:
            entry = self._runs.get(rid)
        return entry[1] if entry and entry[0] == sid else None

    def remove(self, rid: str) -> None:
        with self._lock:
            self._runs.pop(rid, None)

    def _sweep(self) -> None:
        cutoff = time() - _RUN_TTL
        for rid in [r for r, (_s, _run, ts) in self._runs.items() if ts < cutoff]:
            self._runs.pop(rid, None)


# --------------------------------------------------------------------------- #
# Conversation history                                                        #
# --------------------------------------------------------------------------- #
def history(session: Session) -> list[dict]:
    return list(session.get(_HISTORY_KEY, []) or [])


def remember(session: Session, role: str, content: str) -> None:
    turns = history(session)
    turns.append({"role": role, "content": (content or "")[:4000]})
    session.set(_HISTORY_KEY, turns[-(settings.history_turns * 2):])


def clear_history(session: Session) -> None:
    session.set(_HISTORY_KEY, [])


# --------------------------------------------------------------------------- #
# Building a run                                                              #
# --------------------------------------------------------------------------- #
def make_agent(app, session: Session):
    """Build a per-session agent and its tool context. Raises LLMError when no
    model is configured."""
    llm = preferences.build_llm(session)
    agent = Agent(app.state.mcp, llm)
    return agent, preferences.tool_context(session, llm)


def shot_factory(run_id: str):
    """Hand the browser a fresh screenshot path for each step of this run."""
    counter = {"n": 0}

    def next_shot() -> str:
        counter["n"] += 1
        return runs.shot_path(run_id, counter["n"])

    return next_shot


# --------------------------------------------------------------------------- #
# Streaming                                                                   #
# --------------------------------------------------------------------------- #
def stream_run(app, session: Session, run, run_id: str,
               approved: bool | None, args: dict | None = None) -> StreamingResponse:
    """Advance `run` to completion or its next approval pause, streaming as NDJSON."""
    loop = asyncio.get_running_loop()
    q: asyncio.Queue = asyncio.Queue()
    record = runs.get(getattr(run, "record_id", "")) or None

    def emit(event: dict) -> None:
        if event.get("type") == "tool_end" and record is not None:
            _append_step(record, event)
            event = {**event, "screenshot": _shot_name(str(event.get("screenshot", ""))),
                     "record_id": record.id}
        loop.call_soon_threadsafe(q.put_nowait, event)

    run.on_event = emit
    if run.context is not None:
        run.context.on_progress = emit

    def worker() -> None:
        if run_gate.busy:
            emit({"type": "notice", "kind": "queued",
                  "message": "A scheduled automation is using the browser right now — "
                             "starting yours as soon as it finishes."})
        try:
            with run_gate.hold(f"run:{run_id}", timeout=_GATE_WAIT, force=True):
                outcome = run.advance(approved, args)
        except Exception as ex:  # pragma: no cover - defensive
            _log.exception("Agent run failed")
            outcome = AgentResult(answer=f"⚠️ Something went wrong: {ex}")
        loop.call_soon_threadsafe(q.put_nowait, {"__outcome__": outcome})

    threading.Thread(target=worker, daemon=True).start()

    async def gen():
        started = time()
        # The live view needs the run id before the first screenshot lands.
        if record is not None:
            yield _line({"type": "run_started", "record_id": record.id, "goal": record.goal})
        while True:
            item = await q.get()
            if isinstance(item, dict) and "__outcome__" in item:
                outcome = item["__outcome__"]
                if isinstance(outcome, PendingApproval):
                    if record is not None:
                        record.approvals += 1
                        runs.save(record)
                    yield _line({
                        "type": "awaiting_approval",
                        "run_id": run_id,
                        "index": outcome.index,
                        "tool": outcome.tool,
                        "label": outcome.label,
                        "description": outcome.description,
                        "args": outcome.args,
                        "detail": outcome.detail,
                    })
                else:
                    app.state.runs.remove(run_id)
                    remember(session, "assistant", outcome.answer)
                    if record is not None:
                        record.answer = outcome.answer
                        record.status = "failed" if outcome.error else "done"
                        record.seconds = round(record.seconds + (time() - started), 1)
                        record.model = outcome.llm_label
                        runs.save(record)
                    yield _line({
                        "type": "final",
                        "answer": outcome.answer,
                        "record_id": record.id if record else "",
                        "steps": [_step_dict(s) for s in outcome.steps],
                        "outputs": record.outputs if record else [],
                        "transport": outcome.transport,
                        "llm": outcome.llm_label,
                        "error": outcome.error,
                        "can_save_playbook": _worth_saving(record),
                    })
                yield _line({"type": "done"})
                break
            yield _line(item)

    return StreamingResponse(gen(), media_type="application/x-ndjson")


def _append_step(record, event: dict) -> None:
    """Persist one completed step as it happens."""
    try:
        record.steps.append(RunStep(
            index=int(event.get("index", len(record.steps) + 1)),
            tool=str(event.get("tool", "")),
            # Reserved parameters are injected server-side, so they should never
            # be here — but a model can put anything in `action_input`, and a run
            # trace is not the place for a value shaped like a credential.
            args={k: v for k, v in (event.get("args") or {}).items()
                  if k not in catalog.RESERVED_PARAMS},
            label=str(event.get("label", "")),
            summary=str(event.get("summary", "")),
            ok=bool(event.get("ok")),
            status=str(event.get("status", "done")),
            url=str(event.get("url", "")),
            screenshot=_shot_name(str(event.get("screenshot", ""))),
            target=event.get("target") or {},
        ))
        if event.get("output"):
            record.outputs.append(str(event["output"]))
        runs.save(record)
    except Exception:  # pragma: no cover - recording must never break a run
        _log.debug("Could not record step", exc_info=True)


def _worth_saving(record) -> bool:
    """Only offer to save a Playbook when there is an automation worth keeping."""
    if record is None or record.status != "done":
        return False
    from automation.playbooks import replayable_tools

    allowed = replayable_tools()
    useful = [s for s in record.steps if s.tool in allowed and s.ok]
    return len(useful) >= 2


def _shot_name(path: str) -> str:
    return path.replace("\\", "/").rsplit("/", 1)[-1] if path else ""


def stream_replay(app, session: Session, playbook, values: dict) -> StreamingResponse:
    """Run a Playbook and stream its progress.

    No approval pauses here: the consequential actions in this sequence were
    already approved the first time round, when the user watched them happen and
    chose to save them. Re-approving every run would make automation pointless.
    """
    from automation.replay import ReplayError, replay

    loop = asyncio.get_running_loop()
    q: asyncio.Queue = asyncio.Queue()

    def emit(event: dict) -> None:
        loop.call_soon_threadsafe(q.put_nowait, event)

    # A model is only needed if a step has to be re-found; missing one degrades
    # replay to strict mode rather than failing outright.
    try:
        llm = preferences.build_llm(session)
    except LLMError:
        llm = None
    context = preferences.tool_context(session, llm)

    def worker() -> None:
        if run_gate.busy:
            emit({"type": "notice", "kind": "queued",
                  "message": "A scheduled automation is using the browser right now — "
                             "starting this replay as soon as it finishes."})
        try:
            with run_gate.hold(f"replay:{playbook.id}", timeout=_GATE_WAIT, force=True):
                record = replay(playbook, values, app.state.mcp, context, llm, emit)
            payload = {
                "type": "final", "answer": record.answer, "record_id": record.id,
                "outputs": record.outputs, "error": None if record.status == "done" else record.status,
                "steps": [], "can_save_playbook": False,
            }
        except ReplayError as ex:
            payload = {"type": "final", "answer": f"⚠️ {ex}", "steps": [], "error": str(ex)}
        except Exception as ex:  # pragma: no cover - defensive
            _log.exception("Replay failed")
            payload = {"type": "final", "answer": f"⚠️ Replay failed: {ex}",
                       "steps": [], "error": str(ex)}
        loop.call_soon_threadsafe(q.put_nowait, {"__outcome__": payload})

    threading.Thread(target=worker, daemon=True).start()

    async def gen():
        while True:
            item = await q.get()
            if isinstance(item, dict) and "__outcome__" in item:
                yield _line(item["__outcome__"])
                yield _line({"type": "done"})
                break
            yield _line(item)

    return StreamingResponse(gen(), media_type="application/x-ndjson")


def error_stream(message: str, hint: str = "") -> StreamingResponse:
    """A one-shot stream carrying a setup error, so the client needs no special
    path for 'Theta isn't configured yet'."""

    async def gen():
        yield _line({"type": "final", "answer": message, "steps": [],
                     "error": hint or message, "setup_required": True})
        yield _line({"type": "done"})

    return StreamingResponse(gen(), media_type="application/x-ndjson")


_NO_MODEL = (
    "⚠️ **No language model is configured**, so I can't work out how to do that yet.\n\n"
    "{reason}\n\n"
    "Open **Settings → Model**: paste a free "
    "[Google AI Studio key](https://aistudio.google.com/app/apikey), or point Theta "
    "at a local Ollama."
)


def no_model_stream(ex: LLMError) -> StreamingResponse:
    return error_stream(_NO_MODEL.format(reason=ex), str(ex))


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
        "args": {k: v for k, v in (s.args or {}).items()
                 if k not in catalog.RESERVED_PARAMS},
    }


def _line(payload: dict) -> str:
    return json.dumps(payload, ensure_ascii=False, default=str) + "\n"
