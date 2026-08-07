"""HTTP API: health/session, accounts, settings, tools, and streaming chat."""

import json

import agent.llm as llm
import server.chat as chatmod
from config import settings


def _lines(resp):
    return [json.loads(l) for l in resp.iter_lines() if l.strip()]


def test_health(client):
    assert client.get("/api/health").json()["status"] == "ok"


def test_session_cookie_set(client):
    r = client.get("/api/health")
    sc = r.headers.get("set-cookie", "")
    assert "theta_session=" in sc and "httponly" in sc.lower()


def test_accounts_unconfigured(client):
    g = client.get("/api/accounts").json()["google"]
    assert g["configured"] is False and g["connected"] is False


def test_google_login_unconfigured_returns_400(client):
    r = client.get("/api/auth/google/login", follow_redirects=False)
    assert r.status_code == 400
    assert r.json()["error"] == "google_not_configured"


def test_tools_endpoint_hides_access_token(client):
    data = client.get("/api/tools").json()
    assert data["count"] >= 14
    assert all("access_token" not in t["params"] for t in data["tools"])


def test_settings_get_and_save_masks(client, monkeypatch):
    monkeypatch.setattr(settings, "gemini_api_key", "", raising=False)
    r = client.post("/api/settings", json={"provider": "gemini",
                                           "api_key": "SAVED-KEY-12345678"})
    body = r.json()
    assert body["has_api_key"] is True
    assert "SAVED-KEY-12345678" not in r.text  # raw key never returned
    assert body["api_key_source"] == "session"


def test_settings_test_endpoint(client, monkeypatch):
    monkeypatch.setattr(llm.GeminiLLM, "complete", lambda self, s, u: "OK")
    r = client.post("/api/settings/test", json={"provider": "gemini", "api_key": "GOOD"})
    assert r.json()["ok"] is True


def test_chat_stream_read_only(client, monkeypatch, script_llm):
    action = json.dumps({"thought": "list", "action": "notes_list", "action_input": {}})
    monkeypatch.setattr(chatmod.llm_settings, "build_session_llm",
                        lambda s: script_llm(action))
    with client.stream("POST", "/api/chat", json={"message": "list notes"}) as r:
        evs = _lines(r)
    types = [e["type"] for e in evs]
    assert types == ["tool_start", "tool_end", "final", "done"]
    final = next(e for e in evs if e["type"] == "final")
    assert final["steps"][0]["tool"] == "notes_list"


def test_chat_approval_pause_and_resume(client, monkeypatch, script_llm):
    action = json.dumps({"thought": "send", "action": "gmail_send_reply",
                         "action_input": {"message_id": "m1", "body": "hi"}})
    monkeypatch.setattr(chatmod.llm_settings, "build_session_llm",
                        lambda s: script_llm(action))
    with client.stream("POST", "/api/chat", json={"message": "reply"}) as r:
        evs = _lines(r)
    appr = next(e for e in evs if e["type"] == "awaiting_approval")
    assert appr["run_id"] and appr["tool"] == "gmail_send_reply"

    with client.stream("POST", "/api/chat/resume",
                       json={"run_id": appr["run_id"], "approved": False}) as r:
        evs2 = _lines(r)
    final = next(e for e in evs2 if e["type"] == "final")
    assert final["steps"][0]["status"] == "declined"


def test_resume_unknown_run_404(client):
    r = client.post("/api/chat/resume", json={"run_id": "nope", "approved": True})
    assert r.status_code == 404
