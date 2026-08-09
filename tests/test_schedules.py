"""
Schedules: Playbooks that run themselves.

Two claims are load-bearing and both are tested here rather than asserted in a
README. First, a scheduled run makes **no model calls** — the scheduler is given
a session with no model configured, so a call is impossible rather than merely
unobserved. Second, an unattended run **cannot send an email**, which is not a
policy but a consequence of `replayable_tools()` subtracting `ALWAYS_CONFIRM`
before a Playbook is ever recorded.

The rest is about the states a background job can get into. "It broke" is not
good enough: a run that collided with a live task, one whose account was
disconnected and one whose site changed are three different situations, and the
UI can only tell them apart if the scheduler does.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from automation.gate import RunGate
from automation.playbooks import (
    Playbook, PlaybookStep, from_run, playbooks, replayable_tools, required_services,
)
from automation.runs import RunStep, runs
from automation.schedules import (
    BLOCKED, DONE, FAILED, SKIPPED, Schedule, apply_edit, build, schedules,
)
from automation.scheduler import Scheduler
from server.session import SessionStore

IST = -330          # JS getTimezoneOffset() for UTC+5:30
UTC = 0


def at(y, m, d, hh, mm=0):
    return datetime(y, m, d, hh, mm, tzinfo=timezone.utc)


# --------------------------------------------------------------------------- #
# When does it fire?                                                          #
# --------------------------------------------------------------------------- #
class TestNextRun:
    def test_daily_later_today(self):
        s = Schedule(id="s", playbook_id="p", cadence="daily", hour=8, tz_offset=IST)
        # 01:00 UTC is 06:30 IST, so 08:00 IST is still ahead.
        assert s.next_after(at(2026, 8, 9, 1)) == at(2026, 8, 9, 2, 30)

    def test_daily_rolls_to_tomorrow_once_passed(self):
        s = Schedule(id="s", playbook_id="p", cadence="daily", hour=8, tz_offset=IST)
        # 04:00 UTC is 09:30 IST — today's slot has gone.
        assert s.next_after(at(2026, 8, 9, 4)) == at(2026, 8, 10, 2, 30)

    def test_weekdays_skips_the_weekend(self):
        s = Schedule(id="s", playbook_id="p", cadence="weekdays", hour=8, tz_offset=UTC)
        friday_after_slot = at(2026, 8, 7, 9)
        nxt = s.next_after(friday_after_slot)
        assert nxt.weekday() == 0                      # Monday
        assert nxt == at(2026, 8, 10, 8)

    def test_weekly_lands_on_the_chosen_day(self):
        s = Schedule(id="s", playbook_id="p", cadence="weekly", weekday=2, hour=8,
                     tz_offset=UTC)
        nxt = s.next_after(at(2026, 8, 9, 12))         # a Sunday
        assert nxt.weekday() == 2 and nxt == at(2026, 8, 12, 8)

    def test_hourly_uses_the_minute_only(self):
        s = Schedule(id="s", playbook_id="p", cadence="hourly", minute=15, tz_offset=UTC)
        assert s.next_after(at(2026, 8, 9, 1, 20)) == at(2026, 8, 9, 2, 15)
        assert s.next_after(at(2026, 8, 9, 1, 10)) == at(2026, 8, 9, 1, 15)

    def test_timezone_offset_keeps_wall_clock_meaning(self):
        """8am means 8am locally, whatever the server's timezone is."""
        ist = Schedule(id="a", playbook_id="p", cadence="daily", hour=8, tz_offset=IST)
        utc = Schedule(id="b", playbook_id="p", cadence="daily", hour=8, tz_offset=UTC)
        assert ist.next_after(at(2026, 8, 9, 0)) == at(2026, 8, 9, 2, 30)
        assert utc.next_after(at(2026, 8, 9, 0)) == at(2026, 8, 9, 8)

    def test_defer_never_pushes_past_the_regular_slot(self):
        s = Schedule(id="s", playbook_id="p", cadence="hourly", minute=15, tz_offset=UTC)
        now = at(2026, 8, 9, 1, 14)
        s.defer(5, now)                                # 5 min would overshoot 01:15
        assert s.due_at() == at(2026, 8, 9, 1, 15)

    def test_disabled_is_never_due(self):
        s = Schedule(id="s", playbook_id="p", cadence="daily", tz_offset=UTC, enabled=False)
        s.reschedule(at(2026, 8, 1, 0))
        assert s.is_due(at(2030, 1, 1, 0)) is False

    def test_cadence_labels_read_like_english(self):
        mk = lambda **kw: Schedule(id="s", playbook_id="p", tz_offset=UTC, **kw)
        assert mk(cadence="daily", hour=8, minute=30).cadence_label() == "Every day at 08:30"
        assert mk(cadence="weekdays", hour=9).cadence_label() == "Weekdays at 09:00"
        assert mk(cadence="weekly", weekday=2, hour=9).cadence_label() == "Wednesdays at 09:00"
        assert mk(cadence="hourly", minute=5).cadence_label() == "Every hour at :05"


# --------------------------------------------------------------------------- #
# Building and editing                                                        #
# --------------------------------------------------------------------------- #
@pytest.fixture
def simple_playbook():
    pb = Playbook(id="pb-simple", name="Collect quotes", goal="collect quotes")
    pb.steps = [PlaybookStep(action="navigate", value="https://e.example")]
    return playbooks.save(pb)


class TestBuild:
    def test_clamps_out_of_range_values(self, simple_playbook):
        s = build(simple_playbook, {"hour": 99, "minute": -5, "weekday": 41,
                                    "tz_offset": 99999}, owner_sid="sid")
        assert (s.hour, s.minute, s.weekday) == (23, 0, 6)
        assert s.tz_offset == 840

    def test_unknown_cadence_falls_back_to_daily(self, simple_playbook):
        assert build(simple_playbook, {"cadence": "fortnightly"}, "sid").cadence == "daily"

    def test_values_are_filtered_to_declared_params(self, simple_playbook):
        """A hand-made request must not smuggle an argument into an unattended run."""
        simple_playbook.params = [type("P", (), {"name": "query"})()]
        s = build(simple_playbook, {"values": {"query": "ok", "evil": "rm -rf"}}, "sid")
        assert s.values == {"query": "ok"}

    def test_records_its_owner_and_first_slot(self, simple_playbook):
        s = build(simple_playbook, {"cadence": "daily", "hour": 8}, owner_sid="sid-42")
        assert s.owner_sid == "sid-42" and s.next_run

    def test_owner_is_never_sent_to_the_browser(self, simple_playbook):
        s = build(simple_playbook, {}, owner_sid="secret-session")
        assert "owner_sid" not in s.summary_dict()
        assert "secret-session" not in str(s.summary_dict())


class TestEdit:
    def test_changing_the_time_moves_the_next_run(self, simple_playbook):
        s = build(simple_playbook, {"cadence": "daily", "hour": 8, "tz_offset": UTC}, "sid")
        before = s.next_run
        apply_edit(s, simple_playbook, {"hour": 17})
        assert s.hour == 17 and s.next_run != before

    def test_renaming_alone_leaves_the_slot_alone(self, simple_playbook):
        s = build(simple_playbook, {"cadence": "daily", "hour": 8}, "sid")
        before = s.next_run
        apply_edit(s, simple_playbook, {"name": "New name"})
        assert s.name == "New name" and s.next_run == before

    def test_resuming_reschedules_rather_than_firing_for_every_missed_slot(
            self, simple_playbook):
        s = build(simple_playbook, {"cadence": "hourly", "minute": 0}, "sid")
        s.enabled = False
        s.next_run = at(2020, 1, 1, 0).isoformat()      # long overdue
        apply_edit(s, simple_playbook, {"enabled": True})
        assert s.enabled is True
        assert s.due_at() > datetime.now(timezone.utc)


class TestStore:
    def test_round_trips(self, simple_playbook):
        s = schedules.save(build(simple_playbook, {"name": "Nightly"}, "sid"))
        assert schedules.get(s.id).name == "Nightly"

    def test_rejects_a_traversal_id(self):
        assert schedules.get("../../etc/passwd") is None
        assert schedules.delete("../../etc/passwd") is False

    def test_due_returns_only_enabled_and_ripe(self, simple_playbook):
        ripe = build(simple_playbook, {}, "sid")
        ripe.next_run = at(2020, 1, 1, 0).isoformat()
        schedules.save(ripe)
        later = build(simple_playbook, {}, "sid")
        later.next_run = at(2030, 1, 1, 0).isoformat()
        schedules.save(later)
        paused = build(simple_playbook, {}, "sid")
        paused.next_run, paused.enabled = at(2020, 1, 1, 0).isoformat(), False
        schedules.save(paused)

        assert [s.id for s in schedules.due()] == [ripe.id]

    def test_deleting_a_playbook_takes_its_schedules(self, simple_playbook):
        schedules.save(build(simple_playbook, {}, "sid"))
        schedules.save(build(simple_playbook, {}, "sid"))
        assert schedules.delete_for_playbook(simple_playbook.id) == 2
        assert schedules.list() == []


# --------------------------------------------------------------------------- #
# The safety property                                                         #
# --------------------------------------------------------------------------- #
class TestUnattendedCannotSend:
    def test_send_is_not_a_replayable_tool(self):
        assert "gmail_send_reply" not in replayable_tools()
        assert "research" not in replayable_tools()

    def test_a_recorded_run_drops_the_send_step(self):
        """The guarantee holds at recording time, so no schedule can ever carry one."""
        record = runs.create(goal="reply to Priya")
        record.steps = [
            RunStep(index=1, tool="gmail_search", args={"query": "from:priya"},
                    ok=True, status="done"),
            RunStep(index=2, tool="gmail_send_reply",
                    args={"message_id": "m1", "body": "Sure!"}, ok=True, status="done"),
        ]
        record.status = "done"
        runs.save(record)

        pb = from_run(record, name="Reply to Priya")
        actions = [s.action for s in pb.steps]
        assert "api:gmail_search" in actions
        assert not any("send" in a for a in actions)


# --------------------------------------------------------------------------- #
# The scheduler                                                               #
# --------------------------------------------------------------------------- #
@pytest.fixture
def owned(simple_playbook):
    """A store, a real session in it, and a schedule that is already due."""
    store = SessionStore()
    session = store.session(None)
    schedule = build(simple_playbook, {"cadence": "daily", "hour": 8},
                     owner_sid=session.sid)
    schedule.next_run = at(2020, 1, 1, 0).isoformat()
    schedules.save(schedule)
    return store, session, schedule, simple_playbook


class TestScheduler:
    def test_fires_a_due_schedule_with_no_model(self, owned, manager, page):
        """The session has no model configured, so a model call cannot happen."""
        store, _session, schedule, _pb = owned
        outcomes = Scheduler(manager, store, RunGate()).tick()

        assert [o["status"] for o in outcomes] == [DONE]
        assert page.url == "https://e.example"
        after = schedules.get(schedule.id)
        assert after.run_count == 1 and after.last_status == DONE
        assert after.enabled is True
        assert after.due_at() > datetime.now(timezone.utc)   # rescheduled, not stuck

    def test_the_run_is_marked_as_scheduled(self, owned, manager):
        store, _s, _sc, _pb = owned
        run_id = Scheduler(manager, store, RunGate()).tick()[0]["run_id"]
        assert runs.get(run_id).trigger == "schedule"

    def test_skips_rather_than_fails_when_the_browser_is_busy(self, owned, manager, page):
        store, _session, schedule, _pb = owned
        gate = RunGate()
        gate.acquire("run:live")                       # a live task holds it

        outcomes = Scheduler(manager, store, gate).tick()

        assert [o["status"] for o in outcomes] == [SKIPPED]
        assert page.url == "about:blank"               # nothing was driven
        after = schedules.get(schedule.id)
        assert after.enabled is True                   # not punished for a collision
        assert after.run_count == 0
        assert after.due_at() <= datetime.now(timezone.utc) + timedelta(minutes=6)

    def test_pauses_itself_when_the_playbook_is_gone(self, owned, manager):
        store, _session, schedule, pb = owned
        playbooks.delete(pb.id)

        assert Scheduler(manager, store, RunGate()).tick()[0]["status"] == BLOCKED
        after = schedules.get(schedule.id)
        assert after.enabled is False
        assert "deleted" in after.last_error

    def test_pauses_itself_when_the_owning_session_is_gone(self, owned, manager):
        _store, _session, schedule, _pb = owned
        empty = SessionStore()                         # a store that never saw this sid

        assert Scheduler(manager, empty, RunGate()).tick()[0]["status"] == BLOCKED
        after = schedules.get(schedule.id)
        assert after.enabled is False and "expired" in after.last_error

    def test_pauses_itself_when_a_needed_account_is_disconnected(self, owned, manager):
        store, _session, schedule, pb = owned
        pb.steps.append(PlaybookStep(action="api:notion_search",
                                     target={"args": {"query": "notes"}}))
        playbooks.save(pb)

        outcome = Scheduler(manager, store, RunGate()).tick()[0]
        assert outcome["status"] == BLOCKED
        after = schedules.get(schedule.id)
        assert after.enabled is False
        assert "Notion" in after.last_error and "Reconnect" in after.last_error

    def test_records_a_failure_and_keeps_the_schedule_alive(self, owned, manager, page):
        """A site that broke is not a misconfiguration — keep trying tomorrow."""
        store, _session, schedule, pb = owned
        pb.steps = [PlaybookStep(action="click",
                                 target={"name": "Gone", "selectors": ["#gone"],
                                         "describe": 'button "Gone"'})]
        playbooks.save(pb)
        page.broken.add("#gone")

        assert Scheduler(manager, store, RunGate()).tick()[0]["status"] == FAILED
        after = schedules.get(schedule.id)
        assert after.enabled is True and after.fail_count == 1
        assert after.due_at() > datetime.now(timezone.utc)

    def test_does_not_catch_up_on_missed_slots(self, owned, manager):
        """Two days offline must not become forty-eight runs."""
        store, _session, schedule, _pb = owned
        scheduler = Scheduler(manager, store, RunGate())

        scheduler.tick()
        assert scheduler.tick() == []                  # nothing due any more
        assert schedules.get(schedule.id).run_count == 1

    def test_a_paused_schedule_is_left_alone(self, owned, manager, page):
        _store, _session, schedule, _pb = owned
        schedule.enabled = False
        schedules.save(schedule)

        assert Scheduler(manager, SessionStore(), RunGate()).tick() == []
        assert page.url == "about:blank"


class TestRunGate:
    def test_only_one_holder_at_a_time(self):
        gate = RunGate()
        assert gate.acquire("a") is True
        assert gate.acquire("b") is False
        gate.release()
        assert gate.acquire("b") is True

    def test_hold_releases_even_when_the_body_raises(self):
        gate = RunGate()
        with pytest.raises(ValueError):
            with gate.hold("a"):
                raise ValueError("boom")
        assert gate.busy is False

    def test_force_runs_the_body_without_exclusivity(self):
        """A live run must never deadlock behind a background job."""
        gate = RunGate()
        gate.acquire("schedule:x")
        ran = []
        with gate.hold("run:live", force=True) as exclusive:
            ran.append(exclusive)
        assert ran == [False]
        assert gate.holder == "schedule:x"   # the scheduler still holds it


class TestRequiredServices:
    def test_reads_them_off_the_recorded_steps(self):
        pb = Playbook(id="p", name="mix")
        pb.steps = [
            PlaybookStep(action="navigate", value="https://e.example"),
            PlaybookStep(action="api:notion_create_page", target={"args": {}}),
            PlaybookStep(action="api:gmail_search", target={"args": {}}),
        ]
        assert required_services(pb) == {"notion", "gmail"}

    def test_a_browser_only_playbook_needs_nothing(self):
        pb = Playbook(id="p", name="web")
        pb.steps = [PlaybookStep(action="navigate", value="https://e.example")]
        assert required_services(pb) == set()


# --------------------------------------------------------------------------- #
# The HTTP surface                                                            #
# --------------------------------------------------------------------------- #
@pytest.fixture
def api_playbook():
    """A saved playbook, reachable through the API, with one input."""
    record = runs.create(goal="Search the site")
    record.steps = [
        RunStep(index=1, tool="browser_navigate", args={"url": "https://e.example"},
                ok=True, status="done"),
        RunStep(index=2, tool="browser_type", args={"text": "solar"}, ok=True,
                status="done",
                target={"name": "Search", "tag": "input", "selectors": ["#q"]}),
    ]
    record.status = "done"
    runs.save(record)
    return playbooks.save(from_run(record, name="Site search"))


class TestScheduleAPI:
    def test_create_list_and_delete(self, client, api_playbook):
        created = client.post("/api/schedules", json={
            "playbook_id": api_playbook.id, "name": "Every morning",
            "cadence": "daily", "hour": 8, "minute": 30, "tz_offset": IST,
        }).json()

        assert created["cadence_label"] == "Every day at 08:30"
        assert created["enabled"] is True and created["next_run"]

        listed = client.get("/api/schedules").json()
        assert listed["count"] == 1
        assert listed["schedules"][0]["playbook_name"] == "Site search"
        assert listed["schedules"][0]["playbook_missing"] is False

        assert client.delete(f"/api/schedules/{created['id']}").json()["status"] == "deleted"
        assert client.get("/api/schedules").json()["count"] == 0

    def test_create_for_a_missing_playbook_is_a_404(self, client):
        assert client.post("/api/schedules", json={"playbook_id": "nope"}).status_code == 404

    def test_pause_and_resume(self, client, api_playbook):
        sid = client.post("/api/schedules",
                          json={"playbook_id": api_playbook.id}).json()["id"]

        paused = client.patch(f"/api/schedules/{sid}", json={"enabled": False}).json()
        assert paused["enabled"] is False and paused["next_run"] == ""

        resumed = client.patch(f"/api/schedules/{sid}", json={"enabled": True}).json()
        assert resumed["enabled"] is True and resumed["next_run"]

    def test_editing_the_cadence_updates_the_label(self, client, api_playbook):
        sid = client.post("/api/schedules",
                          json={"playbook_id": api_playbook.id}).json()["id"]
        body = client.patch(f"/api/schedules/{sid}",
                            json={"cadence": "weekly", "weekday": 4, "hour": 17,
                                  "minute": 0, "tz_offset": UTC}).json()
        assert body["cadence_label"] == "Fridays at 17:00"

    def test_patching_an_unknown_schedule_is_a_404(self, client):
        assert client.patch("/api/schedules/nope", json={"enabled": False}).status_code == 404

    def test_deleting_the_playbook_removes_its_schedules(self, client, api_playbook):
        client.post("/api/schedules", json={"playbook_id": api_playbook.id})
        body = client.delete(f"/api/playbooks/{api_playbook.id}").json()

        assert body["schedules_removed"] == 1
        assert client.get("/api/schedules").json()["count"] == 0

    def test_run_now_streams_a_replay(self, client, api_playbook, page):
        sid = client.post("/api/schedules",
                          json={"playbook_id": api_playbook.id}).json()["id"]

        resp = client.post(f"/api/schedules/{sid}/run", json={})
        events = [__import__("json").loads(line)
                  for line in resp.text.splitlines() if line.strip()]

        assert events[-1]["type"] == "done"
        assert page.url == "https://e.example"

    def test_status_counts_schedules_and_flags_blocked_ones(self, client, api_playbook):
        sid = client.post("/api/schedules",
                          json={"playbook_id": api_playbook.id}).json()["id"]
        assert client.get("/api/status").json()["schedules"] == 1

        blocked = schedules.get(sid)
        blocked.enabled, blocked.last_status = False, BLOCKED
        schedules.save(blocked)

        body = client.get("/api/status").json()
        assert body["schedules"] == 0 and body["schedules_need_attention"] == 1


class TestCapabilitiesAPI:
    def test_describes_every_capability_with_a_state(self, client):
        body = client.get("/api/capabilities").json()
        keys = {c["key"]: c for c in body["capabilities"]}

        assert set(keys) == {"browser", "gmail", "notion", "files", "web",
                             "playbooks", "schedules"}
        assert keys["browser"]["state"] == "ready"
        # Nothing is connected in tests, and no OAuth client is configured.
        assert keys["notion"]["state"] == "connect"
        assert keys["gmail"]["state"] == "unavailable"

    def test_connections_explain_what_they_unlock(self, client):
        caps = {c["key"]: c for c in client.get("/api/capabilities").json()["capabilities"]}

        assert "search your emails" in caps["gmail"]["summary"]
        assert "search your workspace" in caps["notion"]["summary"]
        assert len(caps["gmail"]["enables"]) >= 3
        assert len(caps["notion"]["enables"]) >= 3

    def test_advertised_tools_are_real(self, client):
        from tools import tool_specs

        real = {spec["name"] for spec in tool_specs.TOOL_SPECS}
        for cap in client.get("/api/capabilities").json()["capabilities"]:
            assert set(cap["tools"]) <= real

    def test_hidden_replay_tool_is_never_advertised(self, client):
        for cap in client.get("/api/capabilities").json()["capabilities"]:
            assert "browser_step" not in cap["tools"]

    def test_starters_put_runnable_suggestions_first(self, client):
        starters = client.get("/api/capabilities").json()["starters"]
        states = [s["state"] for s in starters]

        assert states == sorted(states, key=lambda s: s != "ready")
        assert starters[0]["state"] == "ready"

    def test_starters_represent_more_than_one_capability(self, client):
        """Notion's example is what tells a new user Notion exists."""
        starters = client.get("/api/capabilities").json()["starters"]
        assert len({s["capability"] for s in starters}) >= 4

    def test_no_credential_ever_reaches_the_capabilities_payload(self, client):
        client.post("/api/settings", json={"notion_token": "ntn_supersecret_value"})
        body = client.get("/api/capabilities").text
        assert "ntn_supersecret_value" not in body


class TestApprovalDetail:
    """The approval card renders entirely from this payload, so its shape is a
    contract: a missing `reversible` silently becomes "this can be undone"."""

    def test_a_gated_click_reaches_the_card_with_its_context(
            self, client, script_llm, monkeypatch):
        """The page says "Place order", so the gate fires and the card must say
        where it is and that it cannot be undone."""
        import json as _json

        steps = [
            _json.dumps({"thought": "open", "action": "browser_navigate",
                         "action_input": {"url": "https://shop.example"}}),
            _json.dumps({"thought": "buy", "action": "browser_click",
                         "action_input": {"ref": 5}}),
        ]
        monkeypatch.setattr("server.chat.preferences.build_llm",
                            lambda s: script_llm(*steps))

        resp = client.post("/api/do", json={"goal": "place the order"})
        events = [_json.loads(l) for l in resp.text.splitlines() if l.strip()]
        pause = next(e for e in events if e["type"] == "awaiting_approval")
        detail = pause["detail"]

        assert detail["service"] == "browser"
        assert detail["reversible"] is False
        assert detail["target"] == "https://shop.example"      # where it lands
        assert detail["why"]                                    # why it stopped
        assert detail["on_skip"]

    def test_a_send_is_described_as_irreversible(self):
        from tools import catalog

        detail = catalog.approval_detail("gmail_send_reply", {"body": "Sounds good."})
        assert detail["service"] == "gmail"
        assert detail["reversible"] is False
        assert detail["edit_field"] == "body"
        assert detail["confirm_label"] == "Send it"
        assert "cannot be recalled" in detail["consequence"]

    def test_every_gate_supplies_the_keys_the_card_needs(self):
        from tools import catalog

        required = {"service_label", "title", "why", "reversible", "on_skip",
                    "confirm_label", "decline_label", "edit_field"}
        for name, args in (("gmail_send_reply", {"body": "hi"}),
                           ("research", {"question": "q", "subquestions": ["a"]}),
                           ("browser_click", {"ref": 5}),
                           ("browser_type", {"ref": 1, "text": "x", "submit": True})):
            detail = catalog.approval_detail(name, args, why="because", url="https://e.example")
            assert required <= set(detail), f"{name} is missing {required - set(detail)}"
            assert isinstance(detail["reversible"], bool)
