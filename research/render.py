"""
Render a Brief as Markdown — the export format.

Markdown keeps a brief useful after it leaves Theta: it pastes into Notion, Obsidian,
GitHub or a doc with the citations intact, and the numbered source list at the
bottom stays clickable.
"""

from __future__ import annotations

from research.briefs import Brief


def to_markdown(brief: Brief) -> str:
    out: list[str] = [f"# {brief.title}", ""]
    out.append(f"> **Question:** {brief.question}")
    out.append(">")
    cited = [s for s in brief.sources if s.used]
    meta = [
        _date(brief.created),
        f"{len(cited)} sources cited of {brief.stats.get('sources_read', len(brief.sources))} read",
    ]
    if brief.model:
        meta.append(brief.model)
    out += [f"> *{' · '.join(m for m in meta if m)}*", ""]

    if brief.summary:
        out += ["## Summary", "", brief.summary, ""]

    if brief.key_findings:
        out += ["## Key findings", ""]
        out += [f"- {finding}" for finding in brief.key_findings]
        out.append("")

    for section in brief.sections:
        out += [f"## {section.heading}", "", section.body, ""]

    if brief.gaps:
        out += ["## What this does not settle", "", brief.gaps, ""]

    if brief.sources:
        out += ["## Sources", ""]
        for source in brief.sources:
            suffix = "" if source.used else "  *(read, not cited)*"
            out.append(f"{source.n}. [{source.title}]({source.url}) — {source.site}{suffix}")
        out.append("")

    if brief.subquestions:
        out += ["<details><summary>Research plan</summary>", ""]
        out += [f"{i}. {q}" for i, q in enumerate(brief.subquestions, 1)]
        out += ["", "</details>", ""]

    out.append("---")
    out.append("*Researched with [Theta](https://github.com/) — every claim above "
               "is traceable to a numbered source.*")
    return "\n".join(out)


def _date(iso: str) -> str:
    from datetime import datetime

    try:
        return datetime.fromisoformat(iso).strftime("%d %b %Y")
    except (ValueError, TypeError):
        return ""
