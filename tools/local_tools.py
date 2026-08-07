"""
In-process tools.

Almost everything Theta does reaches the world through an MCP server. `research`
is the exception, and deliberately so: it is not a call to an outside system but
Theta's own engine, and it needs two things a stdio subprocess cannot give it —
the session's language model, and a live progress channel so the UI can show the
plan, the sources and the writing as they happen.

So the world-facing tools stay behind MCP, and the thinking stays here.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Callable

from research.pipeline import ResearchError, research as run_research

_log = logging.getLogger("theta.local_tools")


@dataclass
class LocalTool:
    name: str
    description: str
    input_schema: dict
    fn: Callable                       # fn(args: dict, ctx) -> object
    server: str = "theta"
    tag: str = "read"
    meta: dict = field(default_factory=dict)


def _research(args: dict, ctx) -> dict:
    question = str(args.get("question") or "").strip()
    if not question:
        return {"error": "no_question", "message": "research needs a `question`."}
    if ctx is None or getattr(ctx, "llm", None) is None:
        return {"error": "no_model",
                "message": "No language model is configured. Open Settings → Model."}

    subquestions = [str(q) for q in (args.get("subquestions") or []) if str(q).strip()]
    opts = dict(getattr(ctx, "research_opts", None) or {})
    try:
        brief = run_research(
            question,
            llm=ctx.llm,
            subquestions=subquestions,
            on_progress=_progress_bridge(ctx),
            search_provider=getattr(ctx, "search_provider", None),
            search_key=getattr(ctx, "search_key", None),
            **opts,
        )
    except ResearchError as ex:
        return {"error": "research_failed", "message": str(ex)}
    except Exception as ex:  # pragma: no cover - defensive
        _log.exception("Research failed unexpectedly")
        return {"error": "research_failed", "message": f"Unexpected failure: {ex}"}

    # Compact result for the agent transcript. The full brief is already rendered
    # in the UI, so the agent should introduce it, not repeat it.
    return {
        "brief_id": brief.id,
        "title": brief.title,
        "summary": brief.summary,
        "key_findings": brief.key_findings,
        "gaps": brief.gaps,
        "sources_cited": brief.stats.get("sources_cited", 0),
        "sources_read": brief.stats.get("sources_read", 0),
        "sources": [
            {"n": s.n, "title": s.title, "site": s.site}
            for s in brief.sources if s.used
        ],
        "note": "The full brief is already displayed to the user. Introduce it in "
                "1-3 sentences and mention anything the sources left unsettled.",
    }


def _progress_bridge(ctx):
    """Forward pipeline progress into the agent's event stream."""
    emit = getattr(ctx, "on_progress", None)
    if emit is None:
        return None

    def bridge(event: dict) -> None:
        try:
            emit({"type": "research", **event})
        except Exception:  # pragma: no cover - a broken UI must not stop research
            pass

    return bridge


RESEARCH_TOOL = LocalTool(
    name="research",
    description=(
        "Run deep research on a question: search the web, read many sources, and "
        "write a cited brief. Pass 3-5 focused `subquestions` as the plan. Use this "
        "for anything needing evidence, comparison, current facts, or depth."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "question": {"type": "string"},
            "subquestions": {"type": "array", "items": {"type": "string"}, "default": []},
        },
        "required": ["question"],
    },
    fn=_research,
    server="theta",
    tag="confirm",
)

LOCAL_TOOLS: list[LocalTool] = [RESEARCH_TOOL]
