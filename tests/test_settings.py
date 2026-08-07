"""Per-session LLM settings: masking, persistence, and connection tests."""

import agent.llm as llm
from config import settings
from server import llm_settings
from server.session import SessionStore


def _session():
    return SessionStore().session(None)


def test_public_config_masks_key(monkeypatch):
    monkeypatch.setattr(settings, "gemini_api_key", "", raising=False)
    s = _session()
    llm_settings.update(s, {"provider": "gemini", "api_key": "SECRETKEY-1234567890"})
    cfg = llm_settings.public_config(s)
    assert cfg["has_api_key"] is True
    assert cfg["api_key_source"] == "session"
    assert "SECRETKEY-1234567890" not in str(cfg)  # never returns the raw key
    assert "•" in cfg["api_key_masked"]


def test_update_keeps_key_on_resave(monkeypatch):
    monkeypatch.setattr(settings, "gemini_api_key", "", raising=False)
    s = _session()
    llm_settings.update(s, {"provider": "gemini", "api_key": "KEEP-ME-9876543210"})
    llm_settings.update(s, {"provider": "gemini", "gemini_model": "gemini-2.0-flash"})
    r = llm_settings.resolve(s)
    assert r["api_key"] == "KEEP-ME-9876543210"
    assert r["gemini_model"] == "gemini-2.0-flash"


def test_clear_api_key():
    s = _session()
    llm_settings.update(s, {"provider": "gemini", "api_key": "TEMP-KEY-0001112223"})
    llm_settings.update(s, {"provider": "mock", "clear_api_key": True})
    assert llm_settings.resolve(s)["key_from_session"] is False


def test_test_connection_success(monkeypatch):
    monkeypatch.setattr(llm.GeminiLLM, "complete", lambda self, s, u: "OK")
    s = _session()
    ok, msg = llm_settings.test(s, {"provider": "gemini", "api_key": "GOODKEY"})
    assert ok is True and "responded" in msg


def test_test_connection_failure(monkeypatch):
    def boom(self, s, u):
        raise llm.LLMError("Gemini rejected the request (HTTP 400).")
    monkeypatch.setattr(llm.GeminiLLM, "complete", boom)
    s = _session()
    ok, msg = llm_settings.test(s, {"provider": "gemini", "api_key": "BADKEY"})
    assert ok is False and "400" in msg


def test_test_connection_mock_always_ok():
    ok, msg = llm_settings.test(_session(), {"provider": "mock"})
    assert ok is True
