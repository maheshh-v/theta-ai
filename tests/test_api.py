"""
The HTTP surface: status, streaming runs, approvals, playbooks, run history,
workspace files and settings.
"""

from __future__ import annotations

import json

import pytest

from automation.playbooks import from_run, playbooks
from automation.runs import RunStep, runs


def ndjson(resp) -> list[dict]:
    return [json.loads(line) for line in resp.text.splitlines() if line.strip()]


def action(tool: str, **args) -> str:
    return json.dumps({"thought": "t", "action": tool, "action_input": args})


FINAL = json.dumps({"thought": "t", "action": "FINAL", "action_input": "Done."})


@pytest.fixture
def use_llm(monkeypatch):
    def install(llm):
        monkeypatch.setattr("server.chat.preferences.build_llm", lambda s: llm)
        return llm
    return install


@pytest.fixture
def a_playbook():
    record = runs.create(goal="Search the site")
    record.steps = [
        RunStep(index=1, tool="browser_navigate", args={"url": "https://e.example"},
                ok=True, status="done"),
        RunStep(index=2, tool="browser_type", args={"text": "solar"}, ok=True, status="done",
                target={"name": "Search", "tag": "input", "selectors": ["#q"]}),
    ]
    record.status = "done"
    runs.save(record)
    return playbooks.save(from_run(record, name="Site search")), record


# --------------------------------------------------------------------------- #
# Status                                                                      #
# --------------------------------------------------------------------------- #
def test_health(client):
    assert client.get("/api/health").json()["status"] == "ok"


def test_status_reports_readiness(client):
    body = client.get("/api/status").json()

    assert body["ready"] is True
    assert "Gemini" in body["model"]
    assert body["tool_count"] >= 15


def test_status_flags_a_missing_model(client, monkeypatch):
    from config import settings
    monkeypatch.setattr(settings, "gemini_api_key", "", raising=False)
    body = client.get("/api/status").json()

    assert body["ready"] is False
    assert "API key" in body["model_error"]


def test_tools_hide_reserved_params_and_replay_internals(client):
    body = client.get("/api/tools").json()
    names = {t["name"] for t in body["tools"]}

    assert {"browser_navigate", "browser_click", "file_write", "web_search"} <= names
    assert "browser_step" not in names          # replay-only
    click = next(t for t in body["tools"] if t["name"] == "browser_click")
    assert "shot_path" not in click["params"]


def test_index_page_is_served(client):
    resp = client.get("/")
    assert resp.status_code == 200 and "Theta" in resp.text


# --------------------------------------------------------------------------- #
# Do                                                                          #
# --------------------------------------------------------------------------- #
def test_do_without_a_model_returns_a_setup_message(client, monkeypatch):
    from config import settings
    monkeypatch.setattr(settings, "gemini_api_key", "", raising=False)

    final = next(e for e in ndjson(client.post("/api/do", json={"goal": "hi"}))
                 if e["type"] == "final")
    assert final["setup_required"] is True
    assert "Settings" in final["answer"]


def test_empty_goal_is_handled(client):
    events = ndjson(client.post("/api/do", json={"goal": "   "}))
    assert next(e for e in events if e["type"] == "final")["answer"]


def test_do_streams_a_run(client, use_llm, script_llm, page):
    use_llm(script_llm(action("browser_navigate", url="https://e.example"), FINAL))

    events = ndjson(client.post("/api/do", json={"goal": "open it"}))
    kinds = [e["type"] for e in events]

    assert kinds[0] == "run_started"
    assert "tool_end" in kinds
    assert kinds[-2:] == ["final", "done"]
    assert next(e for e in events if e["type"] == "final")["answer"] == "Done."


def test_a_run_is_recorded_with_screenshots(client, use_llm, script_llm):
    use_llm(script_llm(action("browser_navigate", url="https://e.example"), FINAL))
    events = ndjson(client.post("/api/do", json={"goal": "open it"}))
    record_id = next(e for e in events if e["type"] == "run_started")["record_id"]

    record = runs.get(record_id)
    assert record.status == "done"
    assert record.steps[0].tool == "browser_navigate"
    assert record.steps[0].screenshot == "step-1.jpg"


def test_approval_round_trip(client, use_llm, script_llm, page):
    use_llm(script_llm(action("browser_navigate", url="https://e.example"),
                       action("browser_click", ref=5), FINAL))

    events = ndjson(client.post("/api/do", json={"goal": "place the order"}))
    pause = next(e for e in events if e["type"] == "awaiting_approval")
    assert "Place order" in pause["description"]

    resumed = ndjson(client.post("/api/do/resume",
                                 json={"run_id": pause["run_id"], "approved": True}))
    assert next(e for e in resumed if e["type"] == "final")["answer"] == "Done."
    assert page.clicked == ["Place order"]


def test_rejecting_an_approval_completes_the_run(client, use_llm, script_llm, page):
    use_llm(script_llm(action("browser_navigate", url="https://e.example"),
                       action("browser_click", ref=5), FINAL))
    events = ndjson(client.post("/api/do", json={"goal": "place the order"}))
    pause = next(e for e in events if e["type"] == "awaiting_approval")

    resumed = ndjson(client.post("/api/do/resume",
                                 json={"run_id": pause["run_id"], "approved": False}))

    assert next(e for e in resumed if e["type"] == "final")["answer"] == "Done."
    assert page.clicked == []


def test_resuming_an_unknown_run_is_a_404(client):
    assert client.post("/api/do/resume",
                       json={"run_id": "nope", "approved": True}).status_code == 404


def test_history_is_remembered_then_cleared(client, use_llm, script_llm):
    llm = use_llm(script_llm(FINAL))
    client.post("/api/do", json={"goal": "first goal"})
    client.post("/api/do", json={"goal": "second goal"})

    assert "first goal" in llm.calls[-1][1]
    assert client.post("/api/do/clear").json()["status"] == "cleared"


def test_a_saveable_run_is_flagged(client, use_llm, script_llm):
    use_llm(script_llm(action("browser_navigate", url="https://e.example"),
                       action("browser_click", ref=3), FINAL))
    events = ndjson(client.post("/api/do", json={"goal": "browse"}))

    assert next(e for e in events if e["type"] == "final")["can_save_playbook"] is True


def test_a_run_with_nothing_repeatable_is_not_flagged(client, use_llm, script_llm):
    use_llm(script_llm(FINAL))
    events = ndjson(client.post("/api/do", json={"goal": "just say hi"}))

    assert next(e for e in events if e["type"] == "final")["can_save_playbook"] is False


# --------------------------------------------------------------------------- #
# Playbooks                                                                   #
# --------------------------------------------------------------------------- #
def test_playbook_is_created_from_a_run(client, use_llm, script_llm):
    use_llm(script_llm(action("browser_navigate", url="https://e.example"),
                       action("browser_type", ref=1, text="solar"), FINAL))
    events = ndjson(client.post("/api/do", json={"goal": "search"}))
    record_id = next(e for e in events if e["type"] == "run_started")["record_id"]

    body = client.post("/api/playbooks", json={"run_id": record_id, "name": "My search"}).json()

    assert body["name"] == "My search"
    assert body["steps"] == 2
    assert len(body["params"]) == 1


def test_creating_from_a_barren_run_is_refused(client, use_llm, script_llm):
    use_llm(script_llm(FINAL))
    events = ndjson(client.post("/api/do", json={"goal": "hi"}))
    record_id = next(e for e in events if e["type"] == "run_started")["record_id"]

    resp = client.post("/api/playbooks", json={"run_id": record_id})
    assert resp.status_code == 400
    assert "no repeatable actions" in resp.json()["message"]


def test_creating_from_a_missing_run_is_a_404(client):
    assert client.post("/api/playbooks", json={"run_id": "nope"}).status_code == 404


def test_playbooks_are_listed_with_their_inputs(client, a_playbook):
    pb, _ = a_playbook
    body = client.get("/api/playbooks").json()

    assert body["count"] == 1
    assert body["playbooks"][0]["name"] == pb.name
    assert body["playbooks"][0]["params"][0]["default"] == "solar"


def test_playbook_detail_describes_each_step(client, a_playbook):
    pb, _ = a_playbook
    body = client.get(f"/api/playbooks/{pb.id}").json()

    assert body["step_descriptions"][0] == "Open https://e.example"


def test_playbook_can_be_renamed(client, a_playbook):
    pb, _ = a_playbook
    body = client.patch(f"/api/playbooks/{pb.id}", json={"name": "Renamed"}).json()

    assert body["name"] == "Renamed"
    assert playbooks.get(pb.id).name == "Renamed"


def test_playbook_replay_streams_and_uses_inputs(client, a_playbook, page):
    pb, _ = a_playbook
    param = pb.params[0].name

    events = ndjson(client.post(f"/api/playbooks/{pb.id}/run",
                                json={"values": {param: "wind"}}))

    assert next(e for e in events if e["type"] == "run_started")
    final = next(e for e in events if e["type"] == "final")
    assert "no model needed" in final["answer"]
    assert page.typed == [("Search", "wind")]


def test_playbook_delete(client, a_playbook):
    pb, _ = a_playbook
    assert client.delete(f"/api/playbooks/{pb.id}").json()["status"] == "deleted"
    assert client.get("/api/playbooks").json()["count"] == 0
    assert client.delete(f"/api/playbooks/{pb.id}").status_code == 404


# --------------------------------------------------------------------------- #
# Runs                                                                        #
# --------------------------------------------------------------------------- #
def test_runs_are_listed_and_readable(client, a_playbook):
    _pb, record = a_playbook
    body = client.get("/api/runs").json()

    assert body["count"] >= 1
    detail = client.get(f"/api/runs/{record.id}").json()
    assert detail["goal"] == "Search the site"
    assert len(detail["steps"]) == 2


def test_missing_run_is_a_404(client):
    assert client.get("/api/runs/does-not-exist").status_code == 404


def test_screenshot_paths_cannot_escape_the_run(client, a_playbook):
    _pb, record = a_playbook
    assert client.get(f"/api/runs/{record.id}/shot/..%2F..%2Frun.json").status_code == 404
    assert client.get(f"/api/runs/{record.id}/shot/step-9.jpg").status_code == 404


def test_run_delete(client, a_playbook):
    _pb, record = a_playbook
    assert client.delete(f"/api/runs/{record.id}").json()["status"] == "deleted"
    assert client.get(f"/api/runs/{record.id}").status_code == 404


# --------------------------------------------------------------------------- #
# Workspace                                                                   #
# --------------------------------------------------------------------------- #
def test_files_are_listed_and_downloadable(client):
    from tools import workspace_tools

    workspace_tools.file_write("out.csv", "a,b\n1,2")
    listed = client.get("/api/files").json()
    assert listed["count"] == 1

    resp = client.get("/api/files/out.csv")
    assert resp.status_code == 200 and "a,b" in resp.text


def test_downloads_cannot_escape_the_workspace(client):
    assert client.get("/api/files/../../.env").status_code in (400, 404)


def test_writing_outside_the_workspace_is_refused():
    from tools import workspace_tools

    out = workspace_tools.file_write("../escape.txt", "nope")
    assert out["error"] == "refused"


def test_executable_files_are_refused():
    from tools import workspace_tools

    assert workspace_tools.file_write("payload.exe", "MZ")["error"] == "refused"
    assert workspace_tools.file_write("script.ps1", "rm")["error"] == "refused"


# --------------------------------------------------------------------------- #
# Settings                                                                    #
# --------------------------------------------------------------------------- #
def test_settings_never_leak_a_key(client):
    client.post("/api/settings", json={"provider": "gemini", "api_key": "AIzaSECRETVALUE123"})
    body = client.get("/api/settings").json()

    assert "AIzaSECRETVALUE123" not in json.dumps(body)
    assert body["has_api_key"] is True and "•" in body["api_key_masked"]


def test_saving_without_a_key_keeps_the_existing_one(client):
    client.post("/api/settings", json={"api_key": "AIzaKEEPME1234567"})
    client.post("/api/settings", json={"provider": "gemini", "api_key": ""})

    assert client.get("/api/settings").json()["has_api_key"] is True


def test_settings_round_trip(client):
    body = client.post("/api/settings", json={
        "provider": "ollama", "ollama_model": "llama3.1",
        "search_provider": "tavily", "approve_research": False,
    }).json()

    assert body["provider"] == "ollama"
    assert body["search_provider"] == "tavily"
    assert body["approve_research"] is False


def test_out_of_range_values_are_clamped(client):
    assert client.post("/api/settings", json={"max_sources": 9999}).json()["max_sources"] == 40


def test_unknown_providers_are_ignored(client):
    body = client.post("/api/settings", json={"provider": "not-real"}).json()
    assert body["provider"] in {"gemini", "ollama", "openai"}


# --------------------------------------------------------------------------- #
# Connections                                                                 #
# --------------------------------------------------------------------------- #
def test_nothing_is_connected_to_begin_with(client):
    body = client.get("/api/connections").json()
    assert body["notion"]["connected"] is False
    assert body["google"]["connected"] is False


def test_a_notion_token_is_stored_masked_and_never_returned(client):
    client.post("/api/settings", json={"notion_token": "ntn_SUPERSECRETVALUE"})
    body = client.get("/api/connections").json()

    assert "ntn_SUPERSECRETVALUE" not in json.dumps(body)
    assert body["notion"]["connected"] is True
    assert "•" in body["notion"]["token_masked"]
    assert body["notion"]["token_source"] == "session"


def test_saving_settings_without_a_token_keeps_the_stored_one(client):
    client.post("/api/settings", json={"notion_token": "ntn_KEEPME12345678"})
    client.post("/api/settings", json={"provider": "gemini", "notion_token": ""})

    assert client.get("/api/connections").json()["notion"]["connected"] is True


def test_disconnecting_notion_forgets_the_token(client):
    client.post("/api/settings", json={"notion_token": "ntn_FORGETME1234567"})
    client.post("/api/connections/notion/disconnect")

    assert client.get("/api/connections").json()["notion"]["connected"] is False


def test_testing_notion_without_a_token_says_so(client):
    body = client.post("/api/settings/test-notion", json={}).json()
    assert body["ok"] is False and "No Notion token" in body["message"]


def test_testing_notion_reports_the_workspace_and_the_sharing_caveat(client, fake_notion):
    body = client.post("/api/settings/test-notion",
                       json={"notion_token": fake_notion.TOKEN}).json()

    assert body["ok"] is True
    assert "Acme" in body["message"] or "Theta" in body["message"]
    assert "shared with this integration" in body["message"]


def test_connecting_gmail_without_an_oauth_client_explains_why(client):
    resp = client.get("/api/auth/google/login")
    assert resp.status_code == 400
    assert "GOOGLE_CLIENT_ID" in resp.json()["message"]


def test_the_oauth_callback_rejects_a_state_it_did_not_issue(client, monkeypatch):
    """Without the state check any site could complete a sign-in into this
    session — it is the CSRF defence, not a formality."""
    from config import settings

    monkeypatch.setattr(settings, "google_client_id", "id", raising=False)
    monkeypatch.setattr(settings, "google_client_secret", "secret", raising=False)

    resp = client.get("/api/auth/google/callback?code=abc&state=forged",
                      follow_redirects=False)

    assert resp.status_code == 302
    assert "auth_error=state_mismatch" in resp.headers["location"]


def test_a_cancelled_sign_in_returns_to_settings_with_the_reason(client):
    resp = client.get("/api/auth/google/callback?error=access_denied",
                      follow_redirects=False)

    assert resp.headers["location"].startswith("/?view=settings")
    assert "auth_error=access_denied" in resp.headers["location"]


def test_the_tool_list_never_exposes_a_credential_parameter(client):
    body = client.get("/api/tools").json()
    names = {t["name"] for t in body["tools"]}
    assert {"notion_search", "gmail_search", "gmail_send_reply"} <= names

    for tool in body["tools"]:
        assert "access_token" not in tool["params"]
        assert "notion_token" not in tool["params"]


def test_sending_email_is_the_only_new_tool_that_asks_first(client):
    body = client.get("/api/tools").json()
    gated = {t["name"] for t in body["tools"]
             if t["tag"] == "confirm" and (t["name"].startswith(("gmail_", "notion_")))}
    assert gated == {"gmail_send_reply"}
