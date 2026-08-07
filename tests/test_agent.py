"""
The agent loop: tool dispatch, the dynamic approval gate, context compression,
and history.

The gate tests are the interesting ones. `browser_click` is the same tool
whether it opens an "About us" link or places an order, so approval cannot be a
property of the tool — it is decided per call from the live page.
"""

from __future__ import annotations

import json

from agent.orchestrator import Agent, PendingApproval
from tools.mcp_client import ToolContext


def action(tool: str, **args) -> str:
    return json.dumps({"thought": "t", "action": tool, "action_input": args})


FINAL = json.dumps({"thought": "t", "action": "FINAL", "action_input": "Here you go."})
NAV = action("browser_navigate", url="https://e.example")


# --------------------------------------------------------------------------- #
# Dispatch                                                                    #
# --------------------------------------------------------------------------- #
def test_runs_a_browser_action_then_answers(manager, tool_then_final, page):
    result = Agent(manager, tool_then_final(NAV)).run("open the site")

    assert result.answer == "All done."
    assert [s.tool for s in result.steps] == ["browser_navigate"]
    assert page.url == "https://e.example"


def test_final_without_any_tool(manager, script_llm):
    result = Agent(manager, script_llm(FINAL)).run("hello")

    assert result.answer == "Here you go."
    assert result.steps == []


def test_prose_reply_becomes_the_answer(manager, script_llm):
    assert Agent(manager, script_llm("Just talking.")).run("hi").answer == "Just talking."


def test_unknown_tool_is_reported_back_to_the_model(manager, script_llm):
    result = Agent(manager, script_llm(action("teleport"), FINAL)).run("go")

    assert result.steps[0].status == "error"
    assert "not an available tool" in result.steps[0].observation["error"]
    assert result.answer == "Here you go."


def test_step_cap_ends_the_run(manager, script_llm, monkeypatch):
    from config import settings
    monkeypatch.setattr(settings, "max_agent_steps", 3, raising=False)

    result = Agent(manager, script_llm(NAV)).run("loop forever")

    assert len(result.steps) == 3
    assert "3-step limit" in result.answer


def test_llm_failure_is_explained_not_swallowed(manager):
    from agent.llm import BaseLLM, LLMError

    class Broken(BaseLLM):
        label = "Broken"

        def complete(self, system, user, max_tokens=4096):
            raise LLMError("quota exceeded")

    result = Agent(manager, Broken()).run("anything")

    assert result.error == "quota exceeded"
    assert "quota exceeded" in result.answer and "Settings" in result.answer


# --------------------------------------------------------------------------- #
# The dynamic approval gate                                                   #
# --------------------------------------------------------------------------- #
def test_a_harmless_click_does_not_interrupt(manager, script_llm, page):
    """Ref 3 is an 'About us' link — no reason to stop the user."""
    llm = script_llm(NAV, action("browser_click", ref=3), FINAL)
    result = Agent(manager, llm).run("open the about page")

    assert not any(s.status == "declined" for s in result.steps)
    assert page.clicked == ["About us"]


def test_a_consequential_click_pauses(manager, script_llm):
    """Ref 5 is 'Place order' — the same tool, but this one waits for a human."""
    llm = script_llm(NAV, action("browser_click", ref=5), FINAL)
    run = Agent(manager, llm).start("buy it")

    outcome = run.advance()
    while not isinstance(outcome, PendingApproval) and outcome is not run.result:
        outcome = run.advance()

    assert isinstance(outcome, PendingApproval)
    assert outcome.tool == "browser_click"
    assert "Place order" in outcome.description


def test_rejecting_a_consequential_click_skips_it(manager, script_llm, page):
    llm = script_llm(NAV, action("browser_click", ref=5), FINAL)
    run = Agent(manager, llm).start("buy it")
    outcome = run.advance()
    while isinstance(outcome, PendingApproval):
        outcome = run.advance(approved=False)

    assert any(s.status == "declined" for s in outcome.steps)
    assert page.clicked == []          # nothing was ordered


def test_approving_lets_it_through(manager, script_llm, page):
    llm = script_llm(NAV, action("browser_click", ref=5), FINAL)
    run = Agent(manager, llm).start("buy it")
    outcome = run.advance()
    while isinstance(outcome, PendingApproval):
        outcome = run.advance(approved=True)

    assert page.clicked == ["Place order"]


def test_typing_and_submitting_pauses_even_in_a_plain_field(manager, script_llm):
    """Pressing Enter submits the form, whatever the field is called."""
    llm = script_llm(NAV, action("browser_type", ref=1, text="hi", submit=True), FINAL)
    run = Agent(manager, llm).start("search")
    outcome = run.advance()
    while not isinstance(outcome, PendingApproval) and outcome is not run.result:
        outcome = run.advance()

    assert isinstance(outcome, PendingApproval)
    assert "Submit" in outcome.description


def test_typing_without_submitting_does_not_pause(manager, script_llm, page):
    llm = script_llm(NAV, action("browser_type", ref=1, text="solar"), FINAL)
    Agent(manager, llm).run("type into the box")

    assert page.typed == [("Search", "solar")]


def test_a_password_field_is_refused_by_the_tool(manager, script_llm, page):
    llm = script_llm(NAV, action("browser_type", ref=4, text="hunter2"), FINAL)
    result = Agent(manager, llm).run("log in")

    typed_step = next(s for s in result.steps if s.tool == "browser_type")
    assert not typed_step.ok
    assert "credential" in typed_step.observation["error"]
    assert page.typed == []


# --------------------------------------------------------------------------- #
# Context compression                                                         #
# --------------------------------------------------------------------------- #
def test_only_the_latest_page_is_carried_in_the_prompt(manager, script_llm):
    """Old snapshots are dead weight and their refs are actively misleading."""
    llm = script_llm(NAV, action("browser_click", ref=3), action("browser_read"), FINAL)
    Agent(manager, llm).run("do things")

    last_prompt = llm.calls[-1][1]
    assert last_prompt.count("CURRENT PAGE:") <= 1
    assert "Step 1: browser_navigate" in last_prompt      # history is a one-liner


def test_reserved_parameters_are_hidden_from_the_model(manager, script_llm):
    llm = script_llm(FINAL)
    Agent(manager, llm).run("hi")
    _system, user = llm.calls[0]

    assert "browser_click(ref" in user
    assert "shot_path" not in user
    assert "search_api_key" not in user


def test_replay_only_tools_are_hidden_from_the_model(manager, script_llm):
    llm = script_llm(FINAL)
    Agent(manager, llm).run("hi")

    assert "browser_step" not in llm.calls[0][1]


def test_tools_are_grouped_by_capability(manager, script_llm):
    llm = script_llm(FINAL)
    Agent(manager, llm).run("hi")
    user = llm.calls[0][1]

    assert "[browser]" in user and "[workspace]" in user


def test_history_reaches_the_prompt(manager, script_llm):
    llm = script_llm(FINAL)
    Agent(manager, llm).run(
        "and the next one?",
        history=[{"role": "user", "content": "open example.com"},
                 {"role": "assistant", "content": "Opened it."}],
    )

    assert "CONVERSATION SO FAR" in llm.calls[0][1]
    assert "open example.com" in llm.calls[0][1]


def test_events_carry_what_the_recorder_needs(manager, script_llm, page):
    events: list[dict] = []
    llm = script_llm(NAV, action("browser_click", ref=3), FINAL)
    Agent(manager, llm).start("go", ToolContext(), on_event=events.append).advance()

    ends = [e for e in events if e["type"] == "tool_end"]
    click = next(e for e in ends if e["tool"] == "browser_click")
    assert click["target"]["selectors"] == ["#about"]      # for Playbook recording
    assert click["url"] == "https://e.example"
