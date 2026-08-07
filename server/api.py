"""
HTTP API for Theta (FastAPI router, mounted at /api).

Sections: health/session, Google OAuth (login/callback/disconnect), and
connected accounts. Later phases add chat (SSE), settings, and tools.
"""

from __future__ import annotations

import logging
import secrets
from urllib.parse import quote

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, RedirectResponse

from config import settings
from integrations.google import oauth
from server import accounts, chat, llm_settings
from server.session import current_session
from tools import catalog

router = APIRouter()
_log = logging.getLogger("theta.api")

_GOOGLE_SETUP_HINT = (
    "Google isn't configured on this server. Set GOOGLE_CLIENT_ID and "
    "GOOGLE_CLIENT_SECRET (see README) to enable connecting Gmail and Calendar."
)


# --------------------------------------------------------------------------- #
# Health / session                                                            #
# --------------------------------------------------------------------------- #
@router.get("/health")
def health() -> dict:
    return {"status": "ok", "app": "theta"}


@router.get("/session")
def session_info(request: Request) -> dict:
    s = current_session(request)
    return {
        "session": s.sid[:8],
        "keys": sorted(s.data.keys()),
        "google_configured": settings.google_configured,
    }


# --------------------------------------------------------------------------- #
# Google OAuth                                                                 #
# --------------------------------------------------------------------------- #
@router.get("/auth/google/login")
def google_login(request: Request):
    if not settings.google_configured:
        return JSONResponse(
            {"error": "google_not_configured", "message": _GOOGLE_SETUP_HINT},
            status_code=400,
        )
    s = current_session(request)
    state = secrets.token_urlsafe(24)
    s.set("oauth_state", state)
    return RedirectResponse(oauth.build_auth_url(state), status_code=302)


@router.get("/auth/google/callback")
def google_callback(
    request: Request,
    code: str | None = None,
    state: str | None = None,
    error: str | None = None,
):
    s = current_session(request)
    expected = s.pop("oauth_state", None)

    def back(**params) -> RedirectResponse:
        query = "&".join(f"{k}={quote(str(v))}" for k, v in params.items())
        return RedirectResponse(f"/?tab=accounts&{query}", status_code=302)

    if error:
        return back(error=error)
    if not code or not state or state != expected:
        return back(error="state_mismatch")
    try:
        token = oauth.exchange_code(code)
    except oauth.GoogleAuthError as ex:
        _log.warning("Google OAuth exchange failed: %s", ex)
        return back(error="exchange_failed")

    s.set(accounts.GOOGLE_KEY, token)
    _log.info("Connected Google account for %s", token.get("email") or "(unknown)")
    return back(connected="google")


@router.post("/auth/google/disconnect")
def google_disconnect(request: Request) -> dict:
    accounts.disconnect(current_session(request))
    return {"status": "disconnected"}


# --------------------------------------------------------------------------- #
# Connected accounts                                                          #
# --------------------------------------------------------------------------- #
@router.get("/accounts")
def accounts_status(request: Request) -> dict:
    return accounts.status(current_session(request))


# --------------------------------------------------------------------------- #
# LLM settings                                                                #
# --------------------------------------------------------------------------- #
@router.get("/settings")
def get_settings(request: Request) -> dict:
    return llm_settings.public_config(current_session(request))


@router.post("/settings")
async def post_settings(request: Request) -> dict:
    data = await _json_body(request)
    session = current_session(request)
    llm_settings.update(session, data)
    return llm_settings.public_config(session)


@router.post("/settings/test")
async def test_settings(request: Request) -> dict:
    data = await _json_body(request)
    ok, message = llm_settings.test(current_session(request), data)
    return {"ok": ok, "message": message}


# --------------------------------------------------------------------------- #
# Chat (streaming) + approvals                                                #
# --------------------------------------------------------------------------- #
@router.post("/chat")
async def chat_start(request: Request):
    data = await _json_body(request)
    message = (data.get("message") or "").strip()
    session = current_session(request)
    agent, ctx = chat.make_agent(request.app, session)
    run = agent.start(message, ctx)
    run_id = request.app.state.runs.add(session.sid, run)
    return chat.stream_run(request.app, session, run, run_id, approved=None)


@router.post("/chat/resume")
async def chat_resume(request: Request):
    data = await _json_body(request)
    run_id = data.get("run_id") or ""
    approved = bool(data.get("approved"))
    session = current_session(request)
    run = request.app.state.runs.get(run_id, session.sid)
    if run is None:
        return JSONResponse({"error": "unknown_or_expired_run"}, status_code=404)
    return chat.stream_run(request.app, session, run, run_id, approved=approved)


# --------------------------------------------------------------------------- #
# Tools catalogue                                                             #
# --------------------------------------------------------------------------- #
@router.get("/tools")
def tools_list(request: Request) -> dict:
    mgr = request.app.state.mcp
    tools = []
    for t in mgr.list_tools():
        params = [
            p for p in (t.input_schema or {}).get("properties", {})
            if p not in catalog.RESERVED_PARAMS
        ]
        tools.append({
            "name": t.name,
            "server": t.server,
            "description": t.description,
            "tag": t.tag,
            "params": params,
        })
    return {"transport": mgr.transport, "count": len(tools), "tools": tools}


async def _json_body(request: Request) -> dict:
    try:
        body = await request.body()
        if not body:
            return {}
        import json
        parsed = json.loads(body)
        return parsed if isinstance(parsed, dict) else {}
    except Exception:
        return {}
