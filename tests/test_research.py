"""
The research pipeline — the part of Theta that must not lie.

The citation-verification tests matter most: they are what stops a fabricated
`[7]` from reaching a brief and looking exactly as authoritative as a real one.
"""

from __future__ import annotations

import json

import pytest

from research import pipeline
from research.briefs import Brief, BriefStore, Section, Source
from research.pipeline import ResearchError, research
from research.render import to_markdown
from tests.conftest import ScriptLLM

PLAN = json.dumps(["What did solar cost in 2025?", "What did wind cost in 2025?"])

EXTRACT = json.dumps([
    {"source": 1, "findings": [
        {"claim": "Utility solar reached $28/MWh in 2025.", "quote": "Utility solar fell to $28/MWh in 2025."}]},
    {"source": 2, "findings": [
        {"claim": "Offshore wind costs rose 12%.", "quote": "Offshore wind rose 12% on financing costs."}]},
    {"source": 3, "findings": [
        {"claim": "Battery storage additions doubled.", "quote": "Battery storage additions doubled in 2025."}]},
])

COMPOSE = json.dumps({
    "title": "Renewable cost trends in 2025",
    "summary": "Solar kept falling [1] while offshore wind rose [2].",
    "key_findings": ["Solar hit $28/MWh [1]", "Wind rose 12% [2]"],
    "sections": [{"heading": "Costs", "body": "Solar fell [1]. Wind rose [2]. Storage doubled [3]."}],
    "gaps": "Nothing on grid connection queues.",
})


def run_pipeline(compose=COMPOSE, **kwargs):
    llm = ScriptLLM(PLAN, EXTRACT, compose)
    return research("What happened to renewable costs?", llm=llm, **kwargs)


# --------------------------------------------------------------------------- #
# Happy path                                                                  #
# --------------------------------------------------------------------------- #
def test_produces_a_cited_brief(fake_web):
    brief = run_pipeline()

    assert brief.title == "Renewable cost trends in 2025"
    assert brief.stats["sources_read"] == 3
    assert brief.stats["sources_cited"] == 3
    assert [s.n for s in brief.sources] == [1, 2, 3]
    assert all(s.used for s in brief.sources)
    # Each cited source carries the quote that backed its finding.
    assert "28/MWh" in brief.sources[0].quote


def test_brief_is_persisted_and_listable(fake_web):
    brief = run_pipeline()
    from research.briefs import store

    assert store.get(brief.id).title == brief.title
    assert [b.id for b in store.list()] == [brief.id]
    assert store.list(query="renewable")
    assert not store.list(query="unrelated topic")


def test_supplied_plan_skips_planning(fake_web):
    llm = ScriptLLM(EXTRACT, COMPOSE)   # no plan reply: planning must not be called
    brief = research("q", llm=llm, subquestions=["Sub one here?", "Sub two here?"])

    assert brief.subquestions == ["Sub one here?", "Sub two here?"]
    assert fake_web.searched == ["Sub one here?", "Sub two here?"]


# --------------------------------------------------------------------------- #
# Citation verification — the integrity guarantee                             #
# --------------------------------------------------------------------------- #
def test_invented_citations_are_stripped(fake_web):
    compose = json.dumps({
        "title": "T", "summary": "Real claim [1]. Invented claim [9].",
        "key_findings": [], "sections": [], "gaps": "",
    })
    brief = run_pipeline(compose)

    assert "[9]" not in brief.summary          # no source 9 exists
    assert "[1]" in brief.summary


def test_citations_are_renumbered_in_reading_order(fake_web):
    # The model cites source 3 first, then 1. They should become [1] and [2].
    compose = json.dumps({
        "title": "T", "summary": "Storage doubled [3]. Solar fell [1].",
        "key_findings": [], "sections": [], "gaps": "",
    })
    brief = run_pipeline(compose)

    assert brief.summary == "Storage doubled [1]. Solar fell [2]."
    cited = [s for s in brief.sources if s.used]
    assert [s.url for s in cited] == ["https://c.example/3", "https://a.example/1"]
    # The source that was read but never cited is kept, and marked as such.
    unused = [s for s in brief.sources if not s.used]
    assert [s.url for s in unused] == ["https://b.example/2"]


def test_grouped_citations_expand(fake_web):
    compose = json.dumps({
        "title": "T", "summary": "Both agree [1, 2].",
        "key_findings": [], "sections": [], "gaps": "",
    })
    assert run_pipeline(compose).summary == "Both agree [1][2]."


def test_a_brief_with_no_valid_citation_is_refused(fake_web):
    compose = json.dumps({
        "title": "T", "summary": "Confident but unsourced.",
        "key_findings": [], "sections": [], "gaps": "",
    })
    with pytest.raises(ResearchError, match="without a single usable citation"):
        run_pipeline(compose)


def test_findings_attributed_to_a_nonexistent_source_are_dropped(fake_web):
    extract = json.dumps([
        {"source": 1, "findings": [{"claim": "A real claim here.", "quote": "q"}]},
        {"source": 99, "findings": [{"claim": "From nowhere at all.", "quote": "q"}]},
    ])
    llm = ScriptLLM(PLAN, extract, COMPOSE)
    brief = research("q", llm=llm)

    assert brief.stats["findings"] == 1


# --------------------------------------------------------------------------- #
# Degradation                                                                 #
# --------------------------------------------------------------------------- #
def test_unparseable_compose_falls_back_to_cited_findings(fake_web):
    llm = ScriptLLM(PLAN, EXTRACT, "I'm afraid I can't do that.")
    brief = research("q", llm=llm)

    # Structure is lost, but every claim still carries its citation.
    assert brief.sources and any(s.used for s in brief.sources)
    assert "[1]" in brief.summary
    assert "did not return a well-formed brief" in brief.gaps


def test_search_failure_is_reported_not_faked(fake_web):
    fake_web.search_error = "DuckDuckGo is rate-limiting"
    with pytest.raises(ResearchError, match="rate-limiting"):
        run_pipeline()


def test_all_pages_unreadable_is_reported(fake_web):
    fake_web.unreadable = set(fake_web.pages)
    with pytest.raises(ResearchError, match="none could be read"):
        run_pipeline()


def test_no_evidence_found_is_reported(fake_web):
    llm = ScriptLLM(PLAN, "[]", COMPOSE)
    with pytest.raises(ResearchError, match="none contained evidence"):
        research("q", llm=llm)


def test_planning_failure_degrades_to_the_raw_question(fake_web):
    llm = ScriptLLM("not json at all", EXTRACT, COMPOSE)
    brief = research("Why is the sky blue?", llm=llm)

    assert brief.subquestions == ["Why is the sky blue?"]


def test_progress_events_cover_every_stage(fake_web):
    events: list[dict] = []
    run_pipeline(on_progress=events.append)

    stages = [e["stage"] for e in events]
    for expected in ("plan", "search", "reading", "read", "extract", "compose", "done"):
        assert expected in stages, f"missing progress stage: {expected}"


# --------------------------------------------------------------------------- #
# Source selection                                                            #
# --------------------------------------------------------------------------- #
def test_sources_are_interleaved_across_subquestions():
    ranked = [["a1", "a2", "a3"], ["b1", "b2"], ["c1"]]
    # Round-robin, so a budget of 4 covers all three sub-questions.
    assert pipeline._interleave(ranked, 4) == ["a1", "b1", "c1", "a2"]


def test_interleave_drops_duplicate_urls():
    assert pipeline._interleave([["x", "y"], ["x", "z"]], 10) == ["x", "y", "z"]


def test_max_sources_is_respected(fake_web):
    brief = run_pipeline(max_sources=2)
    assert brief.stats["sources_read"] == 2


# --------------------------------------------------------------------------- #
# Storage and rendering                                                       #
# --------------------------------------------------------------------------- #
def test_store_rejects_path_traversal_ids(tmp_path):
    store = BriefStore(tmp_path)
    assert store.get("../../etc/passwd") is None
    assert store.delete("../secrets") is False


def test_brief_round_trips_through_json():
    brief = Brief(id="x1", question="q", title="t",
                  sections=[Section("H", "B [1]")],
                  sources=[Source(1, "T", "https://e.example", "e.example")])
    assert Brief.from_dict(brief.to_dict()) == brief


def test_markdown_export_includes_claims_and_sources(fake_web):
    md = to_markdown(run_pipeline())

    assert "# Renewable cost trends in 2025" in md
    assert "Solar kept falling [1]" in md
    assert "1. [Solar costs 2026](https://a.example/1)" in md
    assert "## What this does not settle" in md
