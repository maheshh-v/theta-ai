"""Agent loop: approval gating, token injection, and reserved-param hiding."""

import json

import pytest

import integrations.google._http as http
from agent.llm import BaseLLM
from agent.orchestrator import Agent, AgentResult, PendingApproval, _render_tools
from tools import catalog
from tools.mcp_client import ToolContext


class _Resp:
    def __init__(self, code, data):
        self.status_code = code
        self._data = data
        self.content = b"{}"

    def json(self):
        return self._data


class _ScriptLLM(BaseLLM):
    label = "ScriptLLM"
    name = "script"

    def __init__(self, tool_json):
        self.tool_json = tool_json

    def complete(self, system, user):
        import json as _json
        if "(none yet)" not in user.split("WORK SO FAR:", 1)[1]:
            return _json.dumps({"action": "FINAL", "action_input": "Done."})
        return self.tool_json


def _script(tool_json):
    return _ScriptLLM(tool_json)


# ---- token injection ---------------------------------------------------- #
def test_auth_tool_without_token_is_not_connected(manager):
    ctx = ToolContext(google_token_provider=lambda: None)
    r = manager.call_tool("gmail_list", {"unread_only": True}, ctx)
    assert r.ok is False
    assert r.content["error"] == "not_connected"


def test_auth_tool_injects_token(manager, monkeypatch):
    seen = {}

    def request(method, url, headers=None, timeout=None, params=None, json=None):
        seen["auth"] = headers["Authorization"]
        if url.endswith("/messages"):
            return _Resp(200, {"messages": []})
        return _Resp(200, {})
    monkeypatch.setattr(http.requests, "request", request)

    ctx = ToolContext(google_token_provider=lambda: "FRESH")
    r = manager.call_tool("gmail_list", {}, ctx)
    assert r.ok is True
    assert seen["auth"] == "Bearer FRESH"


def test_local_tool_needs_no_token(manager):
    r = manager.call_tool("tasks_list", {})
    assert r.ok is True
    assert isinstance(r.content, list)


# ---- reserved param hidden from the LLM --------------------------------- #
def test_reserved_param_hidden_but_present_in_schema(manager):
    tools = {t.name: t for t in manager.list_tools()}
    assert "access_token" in tools["gmail_list"].input_schema["properties"]
    rendered = _render_tools(list(tools.values()))
    assert "access_token" not in rendered
    assert "requires approval" in rendered  # confirm tools flagged


def test_tags_assigned(manager):
    tools = {t.name: t for t in manager.list_tools()}
    assert tools["gmail_send_reply"].tag == "confirm"
    assert tools["gmail_list"].tag == "read"
    assert catalog.tag_for("calendar_add") == "confirm"


# ---- approval gating ---------------------------------------------------- #
def _send_action():
    return json.dumps({"thought": "send", "action": "gmail_send_reply",
                       "action_input": {"message_id": "m1", "body": "hi"}})


def test_read_only_completes_without_pause(manager):
    llm = _script(json.dumps({"thought": "list", "action": "notes_list",
                              "action_input": {}}))
    out = Agent(manager, llm).start("list notes").advance()
    assert isinstance(out, AgentResult)
    assert out.steps[0].tool == "notes_list"


def test_confirm_tool_pauses_then_approves(manager, monkeypatch):
    def request(method, url, headers=None, timeout=None, params=None, json=None):
        if url.endswith("/profile"):
            return _Resp(200, {"emailAddress": "me@x.com"})
        if "/messages/m1" in url:
            return _Resp(200, {"id": "m1", "threadId": "t1", "payload": {"headers": [
                {"name": "From", "value": "P <p@x.com>"},
                {"name": "Subject", "value": "Hi"},
                {"name": "Message-ID", "value": "<a>"}]}})
        if url.endswith("/messages/send"):
            return _Resp(200, {"id": "sent1"})
        return _Resp(200, {})
    monkeypatch.setattr(http.requests, "request", request)

    ctx = ToolContext(google_token_provider=lambda: "TOKEN")
    run = Agent(manager, _script(_send_action())).start("reply and send", ctx)

    pending = run.advance()
    assert isinstance(pending, PendingApproval)
    assert pending.tool == "gmail_send_reply"

    result = run.advance(approved=True)
    assert isinstance(result, AgentResult)
    assert run.result.steps[0].status == "done"
    assert run.result.steps[0].summary == "Reply sent"


def test_confirm_tool_reject_declines(manager):
    ctx = ToolContext(google_token_provider=lambda: "TOKEN")
    run = Agent(manager, _script(_send_action())).start("reply and send", ctx)
    run.advance()
    result = run.advance(approved=False)
    assert result.steps[0].status == "declined"
    assert result.steps[0].ok is False


def test_run_convenience_auto_declines_confirm(manager):
    ctx = ToolContext(google_token_provider=lambda: "TOKEN")
    result = Agent(manager, _script(_send_action())).run("reply and send", ctx)
    assert result.steps[0].status == "declined"  # never sends without approval
