"""Shared fixtures: isolated data dir, deterministic encryption, a fallback
tool manager, a TestClient, and a scriptable LLM."""

from __future__ import annotations

import json

import pytest

from agent.llm import BaseLLM
from config import settings
from tools.mcp_client import MCPManager


@pytest.fixture(autouse=True)
def isolate(tmp_path, monkeypatch):
    """Point storage at a temp dir and use a fixed secret so each test is clean."""
    monkeypatch.setattr(settings, "data_dir", tmp_path, raising=False)
    monkeypatch.setattr(settings, "session_persist", False, raising=False)
    monkeypatch.setattr(settings, "secret_key", "unit-test-secret", raising=False)
    monkeypatch.setattr("tools.backends.DATA_DIR", tmp_path, raising=False)

    import server.security as sec
    sec._fernet = None            # rebuild Fernet from the test secret
    sec._registered.clear()
    yield
    sec._fernet = None


@pytest.fixture
def manager():
    """A manager using the in-process fallback (no MCP subprocesses spawned)."""
    m = MCPManager()
    m._use_fallback_tools()
    return m


@pytest.fixture
def client(manager, monkeypatch):
    """A TestClient over a freshly built app (no lifespan → no subprocesses)."""
    # Google unconfigured by default; individual tests can override.
    monkeypatch.setattr(settings, "google_client_id", "", raising=False)
    monkeypatch.setattr(settings, "google_client_secret", "", raising=False)
    from starlette.testclient import TestClient
    from server.app_factory import create_app

    app = create_app()
    app.state.mcp = manager
    return TestClient(app)


class ScriptLLM(BaseLLM):
    """Returns `tool_json` on the first turn, then a FINAL answer once a tool ran."""

    label = "ScriptLLM"
    name = "script"

    def __init__(self, tool_json: str, final: str = "All done.") -> None:
        self.tool_json = tool_json
        self.final = final

    def complete(self, system: str, user: str) -> str:
        work = user.split("WORK SO FAR:", 1)[1]
        if "(none yet)" not in work:
            return json.dumps({"thought": "wrap", "action": "FINAL",
                               "action_input": self.final})
        return self.tool_json


@pytest.fixture
def script_llm():
    return ScriptLLM
