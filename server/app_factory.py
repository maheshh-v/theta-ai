"""
FastAPI application factory.

Wires together the session middleware, the API router, and the static SPA, and
manages the lifecycle of the shared `MCPManager` (started once, kept alive for
the process). The layered design means this is the *only* place that knows about
both the web layer and the agent/tool layer.
"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from pathlib import Path

import anyio
from fastapi import FastAPI
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from config import settings
from server import security
from server.api import router as api_router
from server.chat import RunRegistry
from server.session import SessionMiddleware, SessionStore
from tools.mcp_client import MCPManager

_STATIC_DIR = Path(__file__).resolve().parent / "static"
_log = logging.getLogger("theta")


@asynccontextmanager
async def _lifespan(app: FastAPI):
    for directory in (settings.briefs_dir, settings.runs_dir,
                      settings.playbooks_dir, settings.workspace_dir):
        directory.mkdir(parents=True, exist_ok=True)
    # Start the MCP tool transport off the event loop (start() blocks briefly).
    mgr = MCPManager()
    await anyio.to_thread.run_sync(mgr.start)
    app.state.mcp = mgr
    _log.info("Tool transport: %s (%d tools)", mgr.transport, len(mgr.list_tools()))
    if not settings.llm_configured:
        _log.warning(
            "No language model configured — set GEMINI_API_KEY in .env or add a key "
            "in Settings. Theta cannot research without one."
        )
    try:
        yield
    finally:
        mgr.stop()


def create_app() -> FastAPI:
    security.install_log_scrubber()

    app = FastAPI(title="Theta", version="2.0.0", lifespan=_lifespan,
                  docs_url=None, redoc_url=None)

    store = SessionStore()
    app.state.store = store
    app.state.runs = RunRegistry()
    app.add_middleware(SessionMiddleware, store=store)

    app.include_router(api_router, prefix="/api")

    # Static SPA: assets under /static, index at /.
    if _STATIC_DIR.exists():
        app.mount("/static", StaticFiles(directory=str(_STATIC_DIR)), name="static")

    @app.get("/", include_in_schema=False)
    def index():
        idx = _STATIC_DIR / "index.html"
        if idx.exists():
            return HTMLResponse(_render_index(idx))
        return JSONResponse({"app": "theta", "ui": "not built yet"})

    return app


def _asset_version() -> str:
    """A token that changes whenever the SPA's assets change.

    Without it, a browser holding a cached `app.js` keeps running the old UI
    against a new backend after an update — which looks like a bug and is
    miserable to diagnose.
    """
    stamps = [
        int(p.stat().st_mtime)
        for p in (_STATIC_DIR / "app.js", _STATIC_DIR / "styles.css")
        if p.exists()
    ]
    return str(max(stamps)) if stamps else "0"


def _render_index(path: Path) -> str:
    html = path.read_text(encoding="utf-8")
    version = _asset_version()
    return html.replace("/static/app.js", f"/static/app.js?v={version}").replace(
        "/static/styles.css", f"/static/styles.css?v={version}"
    )
