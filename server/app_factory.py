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
from fastapi.responses import FileResponse, JSONResponse
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
    # Start the MCP tool transport off the event loop (start() blocks briefly).
    mgr = MCPManager()
    await anyio.to_thread.run_sync(mgr.start)
    app.state.mcp = mgr
    _log.info("MCP transport: %s", mgr.transport)
    try:
        yield
    finally:
        mgr.stop()


def create_app() -> FastAPI:
    security.install_log_scrubber()

    app = FastAPI(title="Theta", version="1.0.0", lifespan=_lifespan,
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
            return FileResponse(str(idx))
        return JSONResponse({"app": "theta", "ui": "not built yet"})

    return app
