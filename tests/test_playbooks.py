"""
Playbooks: recording a successful run, parameterising it, replaying it without a
model, and healing it when the page changes.

The claim under test is the product's central one — that replay needs no model
calls — so the replay tests pass `llm=None`, which makes a model call impossible
rather than merely unlikely.
"""

from __future__ import annotations

import json

import pytest

from automation.playbooks import Playbook, PlaybookStep, from_run, playbooks
from automation.replay import ReplayError, replay
from automation.runs import RunStep, runs
from tools.mcp_client import ToolContext


def make_run(steps):
    record = runs.create(goal="Find quotes and save them")
    for i, (tool, args, target) in enumerate(steps, start=1):
        record.steps.append(RunStep(index=i, tool=tool, args=args, target=target or {},
                                    ok=True, status="done", summary="ok"))
    record.status = "done"
    return runs.save(record)


SEARCH_BOX = {"name": "Search", "tag": "input", "type": "text",
              "selectors": ["#q"], "describe": 'field "Search"'}
SEARCH_BTN = {"name": "Search", "tag": "button", "type": "submit",
              "selectors": ["#go"], "describe": 'button "Search"'}


@pytest.fixture
def recorded_run():
    return make_run([
        ("browser_navigate", {"url": "https://e.example"}, None),
        ("browser_type", {"text": "solar panels", "submit": False}, SEARCH_BOX),
        ("browser_click", {}, SEARCH_BTN),
        ("browser_read", {}, None),
        ("file_write", {"name": "out.csv", "content": "a,b"}, None),
    ])


# --------------------------------------------------------------------------- #
# Recording                                                                   #
# --------------------------------------------------------------------------- #
def test_a_run_becomes_a_playbook(recorded_run):
    pb = from_run(recorded_run, name="Quote search")

    assert pb.name == "Quote search"
    assert [s.action for s in pb.steps] == ["navigate", "type", "click", "read", "file_write"]
    assert pb.source_run == recorded_run.id


def test_typed_values_become_named_inputs(recorded_run):
    pb = from_run(recorded_run)

    assert len(pb.params) == 1
    param = pb.params[0]
    assert param.default == "solar panels"
    assert param.label == "Search"
    # The step now references the parameter rather than hard-coding the value.
    typed = next(s for s in pb.steps if s.action == "type")
    assert typed.value == "{{%s}}" % param.name


def test_steps_keep_the_selectors_that_make_replay_possible(recorded_run):
    pb = from_run(recorded_run)
    click = next(s for s in pb.steps if s.action == "click")

    assert click.target["selectors"] == ["#go"]


def test_failed_and_declined_steps_are_not_recorded():
    record = runs.create(goal="x")
    record.steps = [
        RunStep(index=1, tool="browser_navigate", args={"url": "https://e.example"},
                ok=True, status="done"),
        RunStep(index=2, tool="browser_click", args={}, target=SEARCH_BTN,
                ok=False, status="error"),
        RunStep(index=3, tool="browser_click", args={}, target=SEARCH_BTN,
                ok=False, status="declined"),
    ]
    pb = from_run(runs.save(record))

    assert [s.action for s in pb.steps] == ["navigate"]


def test_a_run_with_nothing_repeatable_yields_no_steps():
    record = make_run([("browser_snapshot", {}, None)])
    assert from_run(record).steps == []


def test_steps_describe_themselves_for_the_ui(recorded_run):
    described = [s.describe() for s in from_run(recorded_run).steps]

    assert described[0] == "Open https://e.example"
    assert "Search" in described[2]


# --------------------------------------------------------------------------- #
# Parameter substitution                                                      #
# --------------------------------------------------------------------------- #
def test_inputs_are_substituted_at_run_time(recorded_run):
    pb = from_run(recorded_run)
    name = pb.params[0].name

    resolved = pb.resolve({name: "heat pumps"})
    typed = next(s for s in resolved if s.action == "type")
    assert typed.value == "heat pumps"


def test_missing_inputs_fall_back_to_the_recorded_default(recorded_run):
    pb = from_run(recorded_run)
    typed = next(s for s in pb.resolve({}) if s.action == "type")

    assert typed.value == "solar panels"


def test_resolving_does_not_mutate_the_stored_playbook(recorded_run):
    pb = from_run(recorded_run)
    pb.resolve({pb.params[0].name: "something else"})

    assert next(s for s in pb.steps if s.action == "type").value.startswith("{{")


def test_filenames_can_be_parameterised():
    """Output names take inputs too, so one playbook can write dated files."""
    from automation.playbooks import Param

    pb = Playbook(
        id="p1", name="n",
        params=[Param(name="stem", default="out")],
        steps=[PlaybookStep(action="file_write", value="data",
                            target={"name": "{{stem}}.csv"})],
    )
    assert pb.resolve({"stem": "quotes"})[0].target["name"] == "quotes.csv"
    assert pb.resolve({})[0].target["name"] == "out.csv"


# --------------------------------------------------------------------------- #
# Replay — the "no model" claim                                               #
# --------------------------------------------------------------------------- #
def test_replay_runs_without_a_model(recorded_run, manager, page):
    pb = playbooks.save(from_run(recorded_run))

    record = replay(pb, {}, manager, ToolContext(), llm=None, allow_heal=False)

    assert record.status == "done"
    assert len(record.steps) == 5
    assert page.clicked == ["Search"]
    assert page.typed == [("Search", "solar panels")]
    assert "no model needed" in record.answer


def test_replay_uses_the_supplied_inputs(recorded_run, manager, page):
    pb = playbooks.save(from_run(recorded_run))
    name = pb.params[0].name

    replay(pb, {name: "wind turbines"}, manager, ToolContext(), llm=None, allow_heal=False)

    assert page.typed == [("Search", "wind turbines")]


def test_replay_updates_the_playbook_statistics(recorded_run, manager):
    pb = playbooks.save(from_run(recorded_run))
    replay(pb, {}, manager, ToolContext(), llm=None, allow_heal=False)

    stored = playbooks.get(pb.id)
    assert stored.run_count == 1
    assert stored.last_status == "done"


def test_replay_records_its_own_run(recorded_run, manager):
    pb = playbooks.save(from_run(recorded_run))
    record = replay(pb, {}, manager, ToolContext(), llm=None, allow_heal=False)

    assert runs.get(record.id).playbook_id == pb.id


def test_replay_reports_where_it_stopped(recorded_run, manager, page):
    pb = playbooks.save(from_run(recorded_run))
    page.broken.add("#go")            # the Search button has gone

    record = replay(pb, {}, manager, ToolContext(), llm=None, allow_heal=False)

    assert record.status == "failed"
    assert "stopped at step 3" in record.answer
    assert "Do mode" in record.answer          # tells the user how to recover


def test_an_empty_playbook_is_refused(manager):
    pb = playbooks.save(Playbook(id="empty1", name="Nothing"))
    with pytest.raises(ReplayError, match="no steps"):
        replay(pb, {}, manager, ToolContext(), llm=None)


# --------------------------------------------------------------------------- #
# Self-healing                                                                #
# --------------------------------------------------------------------------- #
def test_a_broken_step_is_re_found_and_written_back(recorded_run, manager, page, script_llm):
    pb = playbooks.save(from_run(recorded_run))
    page.broken.add("#go")            # recorded selector no longer resolves
    llm = script_llm(json.dumps({"ref": 2, "confidence": "high", "why": "the search button"}))

    record = replay(pb, {}, manager, ToolContext(), llm=llm, on_progress=lambda e: None)

    assert record.status == "done"
    assert any(s.healed for s in record.steps)
    # The repaired selectors are persisted, so the next run needs no model again.
    stored = playbooks.get(pb.id)
    assert "#go" in next(s for s in stored.steps if s.action == "click").target["selectors"]


def test_healing_reports_when_nothing_on_the_page_matches(recorded_run, manager, page, script_llm):
    pb = playbooks.save(from_run(recorded_run))
    page.broken.add("#go")
    llm = script_llm(json.dumps({"ref": 0, "confidence": "low", "why": "no such control"}))

    events = []
    record = replay(pb, {}, manager, ToolContext(), llm=llm, on_progress=events.append)

    assert record.status == "failed"
    assert any(e.get("stage") == "heal_failed" for e in events)


def test_healing_is_skipped_when_disabled(recorded_run, manager, page, script_llm):
    pb = playbooks.save(from_run(recorded_run))
    page.broken.add("#go")
    llm = script_llm(json.dumps({"ref": 2}))

    record = replay(pb, {}, manager, ToolContext(), llm=llm, allow_heal=False)

    assert record.status == "failed"
    assert llm.calls == []            # the model was never consulted


def test_progress_is_reported_for_every_step(recorded_run, manager):
    pb = playbooks.save(from_run(recorded_run))
    events = []
    replay(pb, {}, manager, ToolContext(), llm=None, on_progress=events.append, allow_heal=False)

    stages = [e.get("stage") for e in events if e.get("type") == "replay"]
    assert stages.count("step") == 5
    assert "done" in stages


# --------------------------------------------------------------------------- #
# Storage                                                                     #
# --------------------------------------------------------------------------- #
def test_playbook_round_trips_through_json(recorded_run):
    pb = from_run(recorded_run, name="Round trip")
    assert Playbook.from_dict(pb.to_dict()) == pb


def test_store_rejects_path_traversal_ids():
    assert playbooks.get("../../etc/passwd") is None
    assert playbooks.delete("../secrets") is False


def test_runs_store_rejects_bad_screenshot_names():
    record = runs.create(goal="x")
    assert runs.screenshot(record.id, "../../run.json") is None
    assert runs.screenshot(record.id, "step-1.jpg") is None    # not written yet
