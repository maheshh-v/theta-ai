"""
The research pipeline — Theta's core capability.

Five stages turn a question into a citable Brief:

1. **Plan**    – split the question into focused sub-questions.
2. **Gather**  – search each sub-question, then fetch the pages in parallel.
3. **Extract** – per source, pull findings *with the exact supporting quote*.
4. **Compose** – write the brief from those findings, citing sources as `[n]`.
5. **Verify**  – check every `[n]` against a real source, renumber, and report
                 which sources went uncited.

The important design choice is that **compose never sees raw pages** — only
extracted claims that are already tied to a quote and a source. A claim without a
source cannot reach the brief, which is what separates this from asking a chatbot
to "search the web". Stage 5 then enforces it mechanically rather than trusting
the model to have behaved.

Every stage reports progress through `on_progress` so the UI can show the work as
it happens.
"""

from __future__ import annotations

import logging
import re
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Callable

from agent.llm import BaseLLM, LLMError
from agent.parsing import parse_list, parse_object
from config import settings
from research.briefs import Brief, Section, Source, store
from web.fetch import Page, fetch_many
from web.search import SearchError, provider_label, search

_log = logging.getLogger("theta.research")

# Sources per extraction call. Batching keeps the number of LLM round-trips (and
# so the chance of hitting a free-tier rate limit) low without giving any single
# prompt so much text that the model starts skimming.
EXTRACT_BATCH = 3
EXTRACT_WORKERS = 3

Progress = Callable[[dict], None]


class ResearchError(RuntimeError):
    """Raised when research cannot produce a brief at all."""


@dataclass
class Finding:
    claim: str
    quote: str
    source_n: int
    subquestion: str = ""


# --------------------------------------------------------------------------- #
# 1. Plan                                                                     #
# --------------------------------------------------------------------------- #
_PLAN_SYSTEM = """\
You are a research planner. You break a question into the specific sub-questions \
someone would need to answer before they could respond well.

Reply with ONLY a JSON array of strings — no prose, no code fence.

Rules:
- Between 2 and {breadth} sub-questions.
- Each must be a self-contained web-searchable question, not a keyword phrase.
- Together they should cover the question: definitions, current state, evidence, \
counter-arguments, and numbers where relevant.
- No duplicates and nothing the question did not ask about.
"""


def plan_questions(question: str, llm: BaseLLM, breadth: int | None = None) -> list[str]:
    """Split a question into sub-questions. Falls back to the question itself so a
    planning hiccup degrades to a narrower search rather than a failure."""
    breadth = breadth or settings.research_breadth
    try:
        raw = llm.complete(
            _PLAN_SYSTEM.format(breadth=breadth),
            f"Question: {question}\n\nToday is {_today()}.",
            max_tokens=2000,
        )
    except LLMError as ex:
        _log.warning("Planning failed, falling back to the raw question: %s", ex)
        return [question]

    items = parse_list(raw) or []
    cleaned = _clean_questions(items, breadth)
    return cleaned or [question]


def _clean_questions(items: list, breadth: int) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for item in items:
        text = str(item).strip() if not isinstance(item, dict) else str(
            item.get("question") or item.get("q") or ""
        ).strip()
        key = text.lower()
        if text and key not in seen and len(text) > 8:
            seen.add(key)
            out.append(text[:300])
    return out[:breadth]


# --------------------------------------------------------------------------- #
# 2. Gather                                                                   #
# --------------------------------------------------------------------------- #
def _gather(
    subquestions: list[str],
    on_progress: Progress,
    per_question: int,
    max_sources: int,
    search_provider: str | None,
    search_key: str | None,
) -> tuple[list[Page], dict[str, list[str]]]:
    """Search every sub-question, pick a balanced set of URLs, fetch in parallel.

    Returns the readable pages plus a map of url -> the sub-questions it answers.
    """
    ranked: list[list[str]] = []      # candidate urls, per sub-question
    url_meta: dict[str, dict] = {}
    url_subqs: dict[str, list[str]] = {}
    errors: list[str] = []

    for subq in subquestions:
        try:
            results = search(
                subq, max_results=per_question, provider=search_provider, api_key=search_key
            )
        except SearchError as ex:
            errors.append(str(ex))
            on_progress({"stage": "search", "query": subq, "found": 0, "error": str(ex)})
            continue
        on_progress({"stage": "search", "query": subq, "found": len(results)})
        urls = []
        for r in results:
            url_meta.setdefault(r.url, {"title": r.title, "snippet": r.snippet})
            url_subqs.setdefault(r.url, [])
            if subq not in url_subqs[r.url]:
                url_subqs[r.url].append(subq)
            urls.append(r.url)
        ranked.append(urls)

    chosen = _interleave(ranked, max_sources)
    if not chosen:
        detail = errors[0] if errors else "no results were returned."
        raise ResearchError(f"Web search found nothing to read — {detail}")

    on_progress({"stage": "reading", "total": len(chosen)})
    pages = fetch_many(chosen)

    readable: list[Page] = []
    for page in pages:
        if not page.title and url_meta.get(page.url, {}).get("title"):
            page.title = url_meta[page.url]["title"]
        on_progress({
            "stage": "read", "url": page.url, "site": page.site,
            "title": page.title or page.url, "ok": page.ok, "error": page.error,
        })
        if page.ok and page.text:
            readable.append(page)

    if not readable:
        raise ResearchError(
            f"Found {len(chosen)} results but none could be read (paywalls, bot "
            "checks, or JavaScript-only pages). Try rephrasing the question."
        )
    return readable, url_subqs


def _interleave(ranked: list[list[str]], limit: int) -> list[str]:
    """Take URLs round-robin across sub-questions so every one gets covered even
    when the total budget is small, and no single sub-question monopolises it."""
    chosen: list[str] = []
    seen: set[str] = set()
    for depth in range(max((len(r) for r in ranked), default=0)):
        for urls in ranked:
            if depth >= len(urls):
                continue
            url = urls[depth]
            if url in seen:
                continue
            seen.add(url)
            chosen.append(url)
            if len(chosen) >= limit:
                return chosen
    return chosen


# --------------------------------------------------------------------------- #
# 3. Extract                                                                  #
# --------------------------------------------------------------------------- #
_EXTRACT_SYSTEM = """\
You extract evidence from source documents. You never infer, summarise loosely, \
or add knowledge of your own.

You are given the research question, its sub-questions, and several numbered \
SOURCES. For each source, pull out only the facts that genuinely help answer the \
question.

Reply with ONLY a JSON array — no prose, no code fence:
[
  {"source": <the source number>,
   "findings": [
     {"claim": "<one specific, self-contained factual statement>",
      "quote": "<the exact sentence(s) from that source supporting the claim>"}
   ]}
]

Rules:
- The quote MUST be copied verbatim from that source. If you cannot quote it, drop the finding.
- Keep claims specific: names, numbers, dates, outcomes. No vague statements.
- At most 4 findings per source; return "findings": [] for a source that is \
off-topic, an index page, or content-free.
- Never attribute a finding to a source it did not come from.
"""


def _extract(
    question: str,
    subquestions: list[str],
    pages: list[Page],
    llm: BaseLLM,
    on_progress: Progress,
    url_subqs: dict[str, list[str]],
) -> list[Finding]:
    """Pull quote-backed findings out of every page, batching and parallelising
    the LLM calls."""
    batches = [
        list(enumerate(pages))[i : i + EXTRACT_BATCH]
        for i in range(0, len(pages), EXTRACT_BATCH)
    ]
    done = 0
    findings: list[Finding] = []

    def run(batch) -> list[Finding]:
        blocks = []
        for idx, page in batch:
            blocks.append(
                f"--- SOURCE {idx + 1} ---\n"
                f"Title: {page.title}\nSite: {page.site}\nURL: {page.url}\n\n"
                f"{page.text[: settings.source_chars]}"
            )
        user = (
            f"RESEARCH QUESTION: {question}\n\n"
            f"SUB-QUESTIONS:\n" + "\n".join(f"- {s}" for s in subquestions) + "\n\n"
            + "\n\n".join(blocks)
        )
        try:
            raw = llm.complete(_EXTRACT_SYSTEM, user, max_tokens=8000)
        except LLMError as ex:
            _log.warning("Extraction batch failed: %s", ex)
            return []
        return _parse_findings(raw, {idx + 1 for idx, _ in batch}, pages, url_subqs)

    workers = max(1, min(EXTRACT_WORKERS, len(batches)))
    with ThreadPoolExecutor(max_workers=workers, thread_name_prefix="extract") as pool:
        for result in pool.map(run, batches):
            findings.extend(result)
            done += 1
            on_progress({"stage": "extract", "done": done, "total": len(batches),
                         "findings": len(findings)})
    return findings


def _parse_findings(
    raw: str, valid: set[int], pages: list[Page], url_subqs: dict[str, list[str]]
) -> list[Finding]:
    items = parse_list(raw) or []
    out: list[Finding] = []
    for entry in items:
        if not isinstance(entry, dict):
            continue
        try:
            n = int(entry.get("source"))
        except (TypeError, ValueError):
            continue
        if n not in valid:  # a hallucinated source number never enters the brief
            continue
        subqs = url_subqs.get(pages[n - 1].url, [])
        for f in entry.get("findings") or []:
            if not isinstance(f, dict):
                continue
            claim = str(f.get("claim", "")).strip()
            quote = str(f.get("quote", "")).strip()
            if len(claim) < 10:
                continue
            out.append(Finding(
                claim=claim[:600], quote=quote[:800], source_n=n,
                subquestion=subqs[0] if subqs else "",
            ))
    return out


# --------------------------------------------------------------------------- #
# 4. Compose                                                                  #
# --------------------------------------------------------------------------- #
_COMPOSE_SYSTEM = """\
You write research briefs. You are given a question and a list of FINDINGS, each \
already tied to a numbered source. Write the brief using only those findings.

Reply with ONLY a JSON object — no prose, no code fence:
{
  "title": "<a specific 4-10 word title>",
  "summary": "<3-5 sentences answering the question directly>",
  "key_findings": ["<one-sentence takeaway>", ...],
  "sections": [{"heading": "<short heading>", "body": "<2-4 paragraphs of markdown>"}],
  "gaps": "<what the sources did not settle, disagreed on, or could not confirm>"
}

Rules:
- EVERY factual sentence ends with its source marker(s): "... fell by 12% in 2024 [3]."
- Use only source numbers that appear in FINDINGS. Never invent one.
- Answer the question in the summary — lead with the answer, not with background.
- Where sources conflict, say so explicitly and cite both.
- 2 to 5 sections. No bullet-only sections; write prose.
- Do not pad. If the evidence is thin, say so in "gaps" rather than filling space.
"""


def _compose(
    question: str, subquestions: list[str], findings: list[Finding],
    pages: list[Page], llm: BaseLLM,
) -> dict:
    lines = []
    for f in findings:
        page = pages[f.source_n - 1]
        lines.append(
            f"[{f.source_n}] ({page.site}) {f.claim}"
            + (f'\n      quote: "{f.quote}"' if f.quote else "")
        )
    user = (
        f"QUESTION: {question}\n\n"
        f"SUB-QUESTIONS:\n" + "\n".join(f"- {s}" for s in subquestions) + "\n\n"
        f"FINDINGS:\n" + "\n".join(lines) + f"\n\nToday is {_today()}."
    )
    try:
        raw = llm.complete(_COMPOSE_SYSTEM, user, max_tokens=8000)
    except LLMError as ex:
        raise ResearchError(f"The model could not write the brief: {ex}") from ex

    data = parse_object(raw)
    if data is None:
        # One retry with a blunter instruction before falling back.
        try:
            raw = llm.complete(
                _COMPOSE_SYSTEM,
                user + "\n\nReturn ONLY the JSON object. Start your reply with '{'.",
                max_tokens=8000,
            )
            data = parse_object(raw)
        except LLMError:
            data = None
    return data or _fallback_compose(question, findings, pages)


def _fallback_compose(question: str, findings: list[Finding], pages: list[Page]) -> dict:
    """Build a brief directly from the findings when the model will not return
    usable JSON. Less polished, but every claim still carries its citation."""
    by_subq: dict[str, list[Finding]] = {}
    for f in findings:
        by_subq.setdefault(f.subquestion or "Findings", []).append(f)
    sections = [
        {"heading": subq[:120],
         "body": "\n".join(f"- {f.claim} [{f.source_n}]" for f in group)}
        for subq, group in by_subq.items()
    ]
    return {
        "title": question[:80],
        "summary": " ".join(f"{f.claim} [{f.source_n}]" for f in findings[:4]),
        "key_findings": [f"{f.claim} [{f.source_n}]" for f in findings[:5]],
        "sections": sections,
        "gaps": "This brief was assembled directly from extracted findings because "
                "the model did not return a well-formed brief. The citations are "
                "reliable; the structure is not.",
    }


# --------------------------------------------------------------------------- #
# 5. Verify                                                                   #
# --------------------------------------------------------------------------- #
_CITE_RE = re.compile(r"\[(\d+(?:\s*,\s*\d+)*)\]")


def _verify(data: dict, pages: list[Page], findings: list[Finding]) -> tuple[dict, list[Source]]:
    """Drop citations that point at nothing, then renumber the survivors 1..k in
    order of first appearance so the brief reads cleanly."""
    valid = set(range(1, len(pages) + 1))
    texts = _brief_texts(data)

    order: list[int] = []
    for text in texts:
        for group in _CITE_RE.findall(text):
            for part in group.split(","):
                try:
                    n = int(part.strip())
                except ValueError:
                    continue
                if n in valid and n not in order:
                    order.append(n)

    remap = {old: new for new, old in enumerate(order, start=1)}

    def rewrite(text: str) -> str:
        def sub(match: re.Match) -> str:
            kept = []
            for part in match.group(1).split(","):
                try:
                    n = int(part.strip())
                except ValueError:
                    continue
                if n in remap:
                    kept.append(f"[{remap[n]}]")
            return "".join(kept)

        return _CITE_RE.sub(sub, text)

    data = dict(data)
    data["summary"] = rewrite(str(data.get("summary", "")))
    data["gaps"] = rewrite(str(data.get("gaps", "")))
    data["key_findings"] = [rewrite(str(k)) for k in (data.get("key_findings") or [])]
    data["sections"] = [
        {"heading": str(s.get("heading", "")), "body": rewrite(str(s.get("body", "")))}
        for s in (data.get("sections") or [])
        if isinstance(s, dict)
    ]

    quotes = {f.source_n: f.quote for f in reversed(findings) if f.quote}
    sources: list[Source] = []
    for old in order:  # cited sources first, in citation order
        page = pages[old - 1]
        sources.append(Source(
            n=remap[old], title=page.title or page.url, url=page.url,
            site=page.site, quote=quotes.get(old, "")[:400], used=True,
        ))
    next_n = len(order) + 1
    for i, page in enumerate(pages, start=1):  # then everything else that was read
        if i not in remap:
            sources.append(Source(
                n=next_n, title=page.title or page.url, url=page.url,
                site=page.site, quote="", used=False,
            ))
            next_n += 1
    return data, sources


def _brief_texts(data: dict) -> list[str]:
    texts = [str(data.get("summary", ""))]
    texts += [str(k) for k in (data.get("key_findings") or [])]
    for s in data.get("sections") or []:
        if isinstance(s, dict):
            texts.append(str(s.get("body", "")))
    texts.append(str(data.get("gaps", "")))
    return texts


# --------------------------------------------------------------------------- #
# Orchestration                                                               #
# --------------------------------------------------------------------------- #
def research(
    question: str,
    llm: BaseLLM,
    subquestions: list[str] | None = None,
    on_progress: Progress | None = None,
    breadth: int | None = None,
    per_question: int | None = None,
    max_sources: int | None = None,
    search_provider: str | None = None,
    search_key: str | None = None,
) -> Brief:
    """Run the full pipeline and return a saved Brief.

    `subquestions` is the plan the user approved; when empty we plan internally.
    """
    question = (question or "").strip()
    if not question:
        raise ResearchError("No research question was given.")

    emit: Progress = on_progress or (lambda _event: None)
    started = time.time()
    breadth = breadth or settings.research_breadth
    per_question = per_question or settings.sources_per_question
    max_sources = max_sources or settings.max_sources

    # 1. Plan
    plan = _clean_questions(subquestions or [], breadth + 2)
    if not plan:
        emit({"stage": "planning"})
        plan = plan_questions(question, llm, breadth)
    emit({"stage": "plan", "subquestions": plan})

    # 2. Gather
    pages, url_subqs = _gather(
        plan, emit, per_question, max_sources, search_provider, search_key
    )

    # 3. Extract
    findings = _extract(question, plan, pages, llm, emit, url_subqs)
    if not findings:
        raise ResearchError(
            f"Read {len(pages)} sources but none contained evidence bearing on the "
            "question. Try a more specific question."
        )

    # 4. Compose
    emit({"stage": "compose", "findings": len(findings), "sources": len(pages)})
    data = _compose(question, plan, findings, pages, llm)

    # 5. Verify
    data, sources = _verify(data, pages, findings)
    cited = [s for s in sources if s.used]
    if not cited:
        raise ResearchError(
            "The brief came back without a single usable citation, so it cannot be "
            "trusted. Try again — this is usually a transient model failure."
        )

    brief = Brief(
        id=Brief.new_id(),
        question=question,
        title=str(data.get("title") or question)[:160],
        summary=str(data.get("summary", "")),
        key_findings=[str(k) for k in (data.get("key_findings") or [])][:8],
        sections=[Section(heading=s["heading"], body=s["body"]) for s in data["sections"]],
        sources=sources,
        gaps=str(data.get("gaps", "")),
        subquestions=plan,
        created=datetime.now(timezone.utc).isoformat(timespec="seconds"),
        model=llm.label,
        search_provider=provider_label(search_provider),
        stats={
            "sources_read": len(pages),
            "sources_cited": len(cited),
            "findings": len(findings),
            "seconds": round(time.time() - started, 1),
        },
    )
    store.save(brief)
    emit({"stage": "done", "brief_id": brief.id, "title": brief.title,
          "sources_cited": len(cited), "seconds": brief.stats["seconds"]})
    return brief


def _today() -> str:
    return datetime.now().strftime("%A, %d %B %Y")
