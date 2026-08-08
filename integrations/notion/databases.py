"""
Notion databases: the schema, and the rows.

In the 2025-09-03 API a database is a container for one or more *data sources*,
and rows are queried from the data source rather than the database. The id a user
copies out of a Notion URL is the database's, so this module resolves one to the
other rather than making the agent know the difference.

Rows come back flattened — `{"Status": "Done", "Owner": ["Priya"]}` — because the
agent's next move is almost always to compare or summarise them, and the tagged
union Notion returns makes that needlessly hard.
"""

from __future__ import annotations

from integrations.notion.api import NotionError, normalise_id, plain, request, title_of

MAX_ROWS = 100


def read_database(token: str, database_id: str, limit: int = 25) -> dict:
    """A database's schema plus its first `limit` rows."""
    did = normalise_id(database_id)
    database, source_id = _resolve(token, did)
    source = request("GET", f"/v1/data_sources/{source_id}", token)

    page_size = max(1, min(int(limit or 25), MAX_ROWS))
    result = request("POST", f"/v1/data_sources/{source_id}/query", token,
                     json={"page_size": page_size})

    rows = [_row(item) for item in result.get("results", [])]
    return {
        "ok": True,
        "id": did,
        "data_source_id": source_id,
        "title": title_of(database) or title_of(source) or "(untitled)",
        "url": (database or {}).get("url", ""),
        "schema": {
            name: spec.get("type", "")
            for name, spec in (source.get("properties") or {}).items()
        },
        "count": len(rows),
        "has_more": bool(result.get("has_more")),
        "rows": rows,
    }


def _resolve(token: str, did: str) -> tuple[dict, str]:
    """Return (database, data_source_id) from either kind of id."""
    try:
        database = request("GET", f"/v1/databases/{did}", token)
    except NotionError as ex:
        if ex.status not in (403, 404):
            raise
        # The id may already name a data source; let the caller's next request
        # surface a clear error if it names neither.
        return {}, did

    sources = database.get("data_sources") or []
    if not sources:
        raise NotionError(
            "That database has no data source Theta can read.", "no_data_source"
        )
    return database, sources[0].get("id", did)


def _row(page: dict) -> dict:
    properties = {
        name: plain(prop) for name, prop in (page.get("properties") or {}).items()
    }
    return {
        "id": page.get("id", ""),
        "title": title_of(page) or "(untitled)",
        "url": page.get("url", ""),
        "properties": properties,
    }
