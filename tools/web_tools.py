"""
Plain functions behind the `web` MCP server.

Kept separate from the server file so the same code backs the MCP tool, the
in-process fallback, and the research pipeline's own calls — one implementation,
three callers.

`search_provider` / `search_api_key` are reserved parameters: the manager injects
the session's choice at call time so the key is never visible to the model.
"""

from __future__ import annotations

from config import settings
from web.fetch import fetch
from web.search import SearchError, search

# How much of a page to hand back to the model. Enough to answer from, small
# enough not to blow the context window on one call.
READ_CHARS = 12000


def web_search(
    query: str,
    max_results: int = 5,
    search_provider: str = "",
    search_api_key: str = "",
) -> dict:
    """Search the live web and return ranked results (title, url, site, snippet)."""
    try:
        results = search(
            query,
            max_results=max(1, min(int(max_results or 5), 10)),
            provider=search_provider or None,
            api_key=search_api_key or None,
        )
    except SearchError as ex:
        return {"error": "search_failed", "message": str(ex), "query": query}
    return {
        "query": query,
        "count": len(results),
        "results": [r.to_dict() for r in results],
    }


def web_read(url: str, search_provider: str = "", search_api_key: str = "") -> dict:
    """Fetch one web page or PDF and return its readable text."""
    page = fetch(url)
    if not page.ok:
        return {"error": "fetch_failed", "message": page.error, "url": page.url}
    return page.to_dict(max_chars=READ_CHARS)


def search_status(provider: str = "", api_key: str = "") -> tuple[bool, str]:
    """Run a probe query so Settings can report whether search actually works."""
    from web.search import provider_label

    try:
        results = search("test query", max_results=3, provider=provider or None,
                         api_key=api_key or None)
    except SearchError as ex:
        return False, str(ex)
    label = provider_label(provider or settings.search_provider)
    if not results:
        return False, f"{label} returned no results."
    return True, f"Connected — {label} returned {len(results)} results."
