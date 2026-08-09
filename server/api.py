"""
HTTP API for Theta (FastAPI router, mounted at /api).

Five groups: status, do (streaming agent runs + approvals), playbooks (saved
automations), runs (the audit trail), and settings. No accounts and no OAuth —
Theta needs nothing but a model key.
"""

from __future__ import annotations

import json
import logging
import secrets
from urllib.parse import quote

from fastapi import APIRouter, Request
from fastapi.responses import (
    FileResponse,
    JSONResponse,
    PlainTextResponse,
    RedirectResponse,
)

from agent.llm import LLMError
from automation.playbooks import from_run, playbooks
from automation.runs import runs
from automation.schedules import apply_edit, build as build_schedule, schedules
from config import settings
from integrations.google import oauth
from research.briefs import store as briefs
from research.render import to_markdown
from server import accounts, capabilities, chat, preferences
from server.session import current_session
from tools import catalog

router = APIRouter()
_log = logging.getLogger("theta.api")


# --------------------------------------------------------------------------- #
# Status                                                                      #
# --------------------------------------------------------------------------- #
@router.get("/health")
def health() -> dict:
    return {"status": "ok", "app": "theta"}


@router.get("/status")
def status(request: Request) -> dict:
    """Everything the UI needs to decide whether Theta is ready to work."""
    session = current_session(request)
    mgr = request.app.state.mcp
    config = preferences.public_config(session)
    all_schedules = schedules.list()
    connected = [
        c["key"] for c in capabilities.connections(session)
        if c["state"] == capabilities.READY
    ]
    return {
        "ready": config["model_ready"],
        "model": config["active_label"],
        "model_ready": config["model_ready"],
        "model_error": config["model_error"],
        "search": config["search_label"],
        "transport": mgr.transport,
        "tool_count": len([t for t in mgr.list_tools() if t.name not in catalog.HIDDEN_TOOLS]),
        "playbooks": len(playbooks.list()),
        "runs": len(runs.list(limit=200)),
        "schedules": len([s for s in all_schedules if s.enabled]),
        # Schedules that paused themselves and need a human — the one thing in
        # this app that can go wrong while nobody is looking.
        "schedules_need_attention": len([
            s for s in all_schedules if not s.enabled and s.last_status == "blocked"
        ]),
        "connected": connected,
        "headless": config["browser_headless"],
    }


@router.get("/capabilities")
def capabilities_index(request: Request) -> dict:
    """What Theta can do, what is connected, and what to try first."""
    session = current_session(request)
    return {
        "model": capabilities.model_state(session),
        "capabilities": capabilities.all_capabilities(session),
        "starters": capabilities.starters(session),
    }


@router.get("/tools")
def tools_list(request: Request) -> dict:
    mgr = request.app.state.mcp
    tools = [
        {
            "name": t.name,
            "server": t.server,
            "description": t.description,
            "tag": t.tag,
            "params": [
                p for p in (t.input_schema or {}).get("properties", {})
                if p not in catalog.RESERVED_PARAMS
            ],
        }
        for t in mgr.list_tools()
        if t.name not in catalog.HIDDEN_TOOLS
    ]
    return {"transport": mgr.transport, "count": len(tools), "tools": tools}


# --------------------------------------------------------------------------- #
# Do — streaming agent runs                                                   #
# --------------------------------------------------------------------------- #
@router.post("/do")
async def do_start(request: Request):
    data = await _json_body(request)
    goal = (data.get("goal") or data.get("message") or "").strip()
    session = current_session(request)
    if not goal:
        return chat.error_stream("Tell me what you'd like done.")

    try:
        agent, ctx = chat.make_agent(request.app, session)
    except LLMError as ex:
        return chat.no_model_stream(ex)

    record = runs.create(goal=goal, model=agent.llm.label)
    ctx.shot_path_factory = chat.shot_factory(record.id)

    turns = chat.history(session)
    chat.remember(session, "user", goal)
    run = agent.start(goal, ctx, history=turns)
    run.record_id = record.id
    run_id = request.app.state.runs.add(session.sid, run)
    return chat.stream_run(request.app, session, run, run_id, approved=None)


@router.post("/do/resume")
async def do_resume(request: Request):
    data = await _json_body(request)
    run_id = data.get("run_id") or ""
    approved = bool(data.get("approved"))
    session = current_session(request)
    run = request.app.state.runs.get(run_id, session.sid)
    if run is None:
        return JSONResponse({"error": "unknown_or_expired_run"}, status_code=404)
    edited = data.get("args")
    return chat.stream_run(
        request.app, session, run, run_id, approved=approved,
        args=edited if isinstance(edited, dict) else None,
    )


@router.post("/do/clear")
def do_clear(request: Request) -> dict:
    chat.clear_history(current_session(request))
    return {"status": "cleared"}


# --------------------------------------------------------------------------- #
# Playbooks                                                                   #
# --------------------------------------------------------------------------- #
@router.get("/playbooks")
def playbooks_index() -> dict:
    items = playbooks.list()
    return {"count": len(items), "playbooks": [p.summary_dict() for p in items]}


@router.get("/playbooks/{pid}")
def playbook_detail(pid: str):
    pb = playbooks.get(pid)
    if pb is None:
        return JSONResponse({"error": "not_found"}, status_code=404)
    data = pb.to_dict()
    data["step_descriptions"] = [s.describe() for s in pb.steps]
    return data


@router.post("/playbooks")
async def playbook_create(request: Request):
    """Distil a completed run into a reusable automation."""
    data = await _json_body(request)
    record = runs.get(str(data.get("run_id", "")))
    if record is None:
        return JSONResponse({"error": "run_not_found"}, status_code=404)

    pb = from_run(record, name=str(data.get("name", "")),
                  description=str(data.get("description", "")))
    if not pb.steps:
        return JSONResponse(
            {"error": "nothing_to_save",
             "message": "That run had no repeatable actions to turn into a playbook."},
            status_code=400,
        )
    playbooks.save(pb)
    return pb.summary_dict()


@router.post("/playbooks/{pid}/run")
async def playbook_run(pid: str, request: Request):
    pb = playbooks.get(pid)
    if pb is None:
        return JSONResponse({"error": "not_found"}, status_code=404)
    data = await _json_body(request)
    values = data.get("values") if isinstance(data.get("values"), dict) else {}
    return chat.stream_replay(request.app, current_session(request), pb, values or {})


@router.patch("/playbooks/{pid}")
async def playbook_update(pid: str, request: Request):
    pb = playbooks.get(pid)
    if pb is None:
        return JSONResponse({"error": "not_found"}, status_code=404)
    data = await _json_body(request)
    if data.get("name"):
        pb.name = str(data["name"])[:120]
    if data.get("description") is not None:
        pb.description = str(data["description"])[:600]
    playbooks.save(pb)
    return pb.summary_dict()


@router.delete("/playbooks/{pid}")
def playbook_delete(pid: str):
    if not playbooks.delete(pid):
        return JSONResponse({"error": "not_found"}, status_code=404)
    # A schedule whose Playbook is gone can never run again; drop it with the
    # Playbook rather than leaving it to fail on its next tick.
    removed = schedules.delete_for_playbook(pid)
    return {"status": "deleted", "schedules_removed": removed}


# --------------------------------------------------------------------------- #
# Schedules — Playbooks that run themselves                                   #
# --------------------------------------------------------------------------- #
@router.get("/schedules")
def schedules_index() -> dict:
    items = schedules.list()
    names = {p.id: p.name for p in playbooks.list()}
    payload = []
    for schedule in items:
        data = schedule.summary_dict()
        data["playbook_name"] = names.get(schedule.playbook_id, "")
        data["playbook_missing"] = schedule.playbook_id not in names
        payload.append(data)
    return {"count": len(payload), "schedules": payload}


@router.post("/schedules")
async def schedule_create(request: Request):
    data = await _json_body(request)
    playbook = playbooks.get(str(data.get("playbook_id", "")))
    if playbook is None:
        return JSONResponse({"error": "playbook_not_found"}, status_code=404)

    session = current_session(request)
    # The schedule runs later, with nobody watching, using this session's
    # connected accounts — so it records whose they are.
    schedule = build_schedule(playbook, data, owner_sid=session.sid)
    schedules.save(schedule)
    return schedule.summary_dict()


@router.patch("/schedules/{sid}")
async def schedule_update(sid: str, request: Request):
    schedule = schedules.get(sid)
    if schedule is None:
        return JSONResponse({"error": "not_found"}, status_code=404)
    data = await _json_body(request)
    playbook = playbooks.get(schedule.playbook_id)

    # Resuming a schedule is also how a user clears a "blocked" state, so it
    # re-homes to the session doing the resuming — whose accounts are connected.
    if data.get("enabled") and not schedule.enabled:
        schedule.owner_sid = current_session(request).sid
        schedule.last_error = ""

    apply_edit(schedule, playbook, data)
    schedules.save(schedule)
    return schedule.summary_dict()


@router.delete("/schedules/{sid}")
def schedule_delete(sid: str):
    if not schedules.delete(sid):
        return JSONResponse({"error": "not_found"}, status_code=404)
    return {"status": "deleted"}


@router.post("/schedules/{sid}/run")
async def schedule_run_now(sid: str, request: Request):
    """Run a schedule immediately, streamed like any other replay, so a user can
    see what the unattended version will actually do before trusting it.

    Async because `stream_replay` needs the running event loop to marshal events
    back from its worker thread; a sync handler is dispatched to a threadpool
    where there is none.
    """
    schedule = schedules.get(sid)
    if schedule is None:
        return JSONResponse({"error": "not_found"}, status_code=404)
    playbook = playbooks.get(schedule.playbook_id)
    if playbook is None:
        return JSONResponse({"error": "playbook_not_found"}, status_code=404)
    return chat.stream_replay(request.app, current_session(request),
                              playbook, dict(schedule.values))


# --------------------------------------------------------------------------- #
# Runs — the audit trail                                                      #
# --------------------------------------------------------------------------- #
@router.get("/runs")
def runs_index(limit: int = 40) -> dict:
    items = runs.list(limit=max(1, min(limit, 200)))
    return {"count": len(items), "runs": [r.summary_dict() for r in items]}


@router.get("/runs/{run_id}")
def run_detail(run_id: str):
    record = runs.get(run_id)
    if record is None:
        return JSONResponse({"error": "not_found"}, status_code=404)
    return record.to_dict()


@router.get("/runs/{run_id}/shot/{name}")
def run_screenshot(run_id: str, name: str):
    path = runs.screenshot(run_id, name)
    if path is None:
        return JSONResponse({"error": "not_found"}, status_code=404)
    return FileResponse(str(path), media_type="image/jpeg")


@router.delete("/runs/{run_id}")
def run_delete(run_id: str):
    if not runs.delete(run_id):
        return JSONResponse({"error": "not_found"}, status_code=404)
    return {"status": "deleted"}


# --------------------------------------------------------------------------- #
# Workspace files                                                             #
# --------------------------------------------------------------------------- #
@router.get("/files")
def files_index() -> dict:
    from tools import workspace_tools

    return workspace_tools.file_list()


@router.get("/files/{name:path}")
def file_download(name: str):
    from tools.workspace_tools import WorkspaceError, _resolve

    try:
        path = _resolve(name)
    except WorkspaceError as ex:
        return JSONResponse({"error": "refused", "message": str(ex)}, status_code=400)
    if not path.exists():
        return JSONResponse({"error": "not_found"}, status_code=404)
    return FileResponse(str(path), filename=path.name,
                        media_type="text/plain; charset=utf-8")


# --------------------------------------------------------------------------- #
# Briefs (optional research capability)                                       #
# --------------------------------------------------------------------------- #
@router.get("/briefs")
def briefs_index(q: str = "", limit: int = 50) -> dict:
    items = briefs.list(query=q, limit=max(1, min(limit, 200)))
    return {"count": len(items), "briefs": [b.summary_dict() for b in items]}


@router.get("/briefs/{brief_id}")
def brief_detail(brief_id: str):
    brief = briefs.get(brief_id)
    if brief is None:
        return JSONResponse({"error": "not_found"}, status_code=404)
    return brief.to_dict()


@router.get("/briefs/{brief_id}/markdown")
def brief_markdown(brief_id: str):
    brief = briefs.get(brief_id)
    if brief is None:
        return JSONResponse({"error": "not_found"}, status_code=404)
    return PlainTextResponse(to_markdown(brief), media_type="text/markdown")


@router.delete("/briefs/{brief_id}")
def brief_delete(brief_id: str):
    if not briefs.delete(brief_id):
        return JSONResponse({"error": "not_found"}, status_code=404)
    return {"status": "deleted"}


# --------------------------------------------------------------------------- #
# Settings                                                                    #
# --------------------------------------------------------------------------- #
@router.get("/settings")
def get_settings(request: Request) -> dict:
    return preferences.public_config(current_session(request))


@router.post("/settings")
async def post_settings(request: Request) -> dict:
    data = await _json_body(request)
    session = current_session(request)
    preferences.update(session, data)
    return preferences.public_config(session)


@router.post("/settings/test")
async def test_model(request: Request) -> dict:
    data = await _json_body(request)
    ok, message = preferences.test_model(current_session(request), data)
    return {"ok": ok, "message": message}


@router.post("/settings/test-search")
async def test_search(request: Request) -> dict:
    data = await _json_body(request)
    ok, message = preferences.test_search(current_session(request), data)
    return {"ok": ok, "message": message}


@router.post("/settings/test-notion")
async def test_notion(request: Request) -> dict:
    data = await _json_body(request)
    ok, message = preferences.test_notion(current_session(request), data)
    return {"ok": ok, "message": message}


# --------------------------------------------------------------------------- #
# Connections — Notion and Gmail                                              #
# --------------------------------------------------------------------------- #
@router.get("/connections")
def connections(request: Request) -> dict:
    """Which accounts are connected. Never carries a credential."""
    return accounts.status(current_session(request))


@router.post("/connections/notion/disconnect")
def notion_disconnect(request: Request) -> dict:
    accounts.notion_disconnect(current_session(request))
    return {"status": "disconnected"}


@router.get("/auth/google/login")
def google_login(request: Request):
    """Start the OAuth flow. The password is typed on Google's page, not ours."""
    if not settings.google_configured:
        return JSONResponse(
            {"error": "google_not_configured", "message": _GOOGLE_SETUP_HINT},
            status_code=400,
        )
    session = current_session(request)
    state = secrets.token_urlsafe(24)
    session.set("oauth_state", state)
    return RedirectResponse(oauth.build_auth_url(state), status_code=302)


@router.get("/auth/google/callback")
def google_callback(request: Request, code: str = "", state: str = "", error: str = ""):
    session = current_session(request)
    expected = session.pop("oauth_state", None)

    def back(**params) -> RedirectResponse:
        query = "&".join(f"{k}={quote(str(v))}" for k, v in params.items())
        return RedirectResponse(f"/?view=settings&{query}", status_code=302)

    if error:
        return back(auth_error=error)
    # A missing or mismatched state means this callback did not come from a flow
    # this session started — the CSRF check, and it is not optional.
    if not code or not state or state != expected:
        return back(auth_error="state_mismatch")
    try:
        token = oauth.exchange_code(code)
    except oauth.GoogleAuthError as ex:
        _log.warning("Google OAuth exchange failed: %s", ex)
        return back(auth_error="exchange_failed")

    session.set(accounts.GOOGLE_KEY, token)
    _log.info("Connected Gmail for %s", token.get("email") or "(unknown account)")
    return back(connected="google")


@router.post("/auth/google/disconnect")
def google_disconnect(request: Request) -> dict:
    accounts.google_disconnect(current_session(request))
    return {"status": "disconnected"}


_GOOGLE_SETUP_HINT = (
    "Gmail needs a Google OAuth client, which belongs to the deployment rather "
    "than to you. Create one at console.cloud.google.com (Web application), add "
    f"{oauth.redirect_uri()} as an authorised redirect URI, enable the Gmail API, "
    "then set GOOGLE_CLIENT_ID and GOOGLE_CLIENT_SECRET and restart Theta."
)


async def _json_body(request: Request) -> dict:
    try:
        body = await request.body()
        if not body:
            return {}
        parsed = json.loads(body)
        return parsed if isinstance(parsed, dict) else {}
    except (json.JSONDecodeError, UnicodeDecodeError):
        return {}
