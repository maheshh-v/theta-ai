"""
Plain functions behind the `briefs` MCP server.

These give the agent memory of its own past work: it can find an earlier brief
and answer a follow-up from it instead of re-researching a question it already
settled.
"""

from __future__ import annotations

from research.briefs import store

# Section bodies can be long; cap what one tool call returns to the model.
BRIEF_CHARS = 9000


def brief_list(query: str = "", limit: int = 10) -> dict:
    """List saved research briefs, newest first, optionally filtered by keyword."""
    briefs = store.list(query=query, limit=max(1, min(int(limit or 10), 50)))
    return {
        "query": query,
        "count": len(briefs),
        "briefs": [b.summary_dict() for b in briefs],
    }


def brief_read(brief_id: str) -> dict:
    """Read one saved brief in full, including its sections and sources."""
    brief = store.get(brief_id)
    if brief is None:
        return {"error": "not_found",
                "message": f"No brief with id '{brief_id}'. Use brief_list to find one."}

    body = "\n\n".join(f"## {s.heading}\n{s.body}" for s in brief.sections)
    return {
        "id": brief.id,
        "title": brief.title,
        "question": brief.question,
        "created": brief.created,
        "summary": brief.summary,
        "key_findings": brief.key_findings,
        "body": body[:BRIEF_CHARS],
        "gaps": brief.gaps,
        "sources": [
            {"n": s.n, "title": s.title, "url": s.url, "site": s.site}
            for s in brief.sources if s.used
        ],
    }
