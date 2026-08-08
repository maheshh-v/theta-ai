"""
Notion pages: find them, read them as Markdown, create them, edit them.

Every function that changes something **re-reads the page afterwards and reports
whether the change is actually there**. That is not belt-and-braces: an agent
that reports success from an HTTP 200 will happily tell you it updated a roadmap
it never touched, and a `verified` field is the difference between a trace you
can trust and one you have to check by hand.

Content is Markdown throughout, via Notion's `/markdown` endpoints — see
`api.py` for why, and for the attribution that led here.
"""

from __future__ import annotations

from integrations.notion.api import (
    MARKDOWN_API_VERSION,
    NotionError,
    normalise_id,
    plain,
    request,
    same_value,
    title_of,
    to_value,
)

MAX_MARKDOWN = 100_000


# --------------------------------------------------------------------------- #
# Read                                                                        #
# --------------------------------------------------------------------------- #
def search(token: str, query: str = "", kind: str = "", limit: int = 10) -> dict:
    """Search the workspace by title. `kind` narrows to "page" or "database"."""
    body: dict = {"page_size": max(1, min(int(limit or 10), 100))}
    if query:
        body["query"] = str(query)
    if kind:
        wanted = "data_source" if str(kind).lower() in ("database", "data_source", "db") else "page"
        body["filter"] = {"property": "object", "value": wanted}

    data = request("POST", "/v1/search", token, json=body)
    results = [_reference(item) for item in data.get("results", [])]
    return {
        "ok": True,
        "count": len(results),
        "results": results,
        "has_more": bool(data.get("has_more")),
    }


def read_page(token: str, page_id: str) -> dict:
    """A page's metadata and its full content as Markdown."""
    pid = normalise_id(page_id)
    page = request("GET", f"/v1/pages/{pid}", token)
    content = _read_markdown(token, pid)
    return {
        "ok": True,
        "id": pid,
        "title": title_of(page),
        "url": page.get("url", ""),
        "last_edited": page.get("last_edited_time", ""),
        "properties": _properties(page),
        "markdown": content["markdown"],
        "truncated": content["truncated"],
    }


def _read_markdown(token: str, page_id: str) -> dict:
    data = request(
        "GET", f"/v1/pages/{page_id}/markdown", token, version=MARKDOWN_API_VERSION
    )
    return {
        "markdown": str(data.get("markdown", ""))[:MAX_MARKDOWN],
        "truncated": bool(data.get("truncated")),
    }


# --------------------------------------------------------------------------- #
# Write                                                                       #
# --------------------------------------------------------------------------- #
def create_page(token: str, parent_id: str, title: str, markdown: str = "") -> dict:
    """Create a page under a page or a database, then confirm it exists."""
    if not str(title or "").strip():
        raise NotionError("A new page needs a title.", "bad_request")

    parent, title_property = _resolve_parent(token, parent_id)
    created = request("POST", "/v1/pages", token, json={
        "parent": parent,
        "properties": {title_property: to_value("title", title)},
    })
    page_id = created.get("id", "")
    if not page_id:
        raise NotionError("Notion accepted the request but returned no page id.",
                          "no_page_id")

    if markdown:
        _replace_markdown(token, page_id, markdown)

    # Verify by reading it back, not by trusting the create response.
    check = read_page(token, page_id)
    body_ok = (not markdown) or bool(check["markdown"].strip())
    verified = same_value(title, check["title"]) and body_ok
    return {
        "ok": True,
        "id": check["id"],
        "title": check["title"],
        "url": check["url"],
        "verified": verified,
        "verified_by": "re-read the page after creating it",
        "message": (
            f"Created “{check['title']}”" if verified
            else f"Created “{check['title']}”, but reading it back didn't match what was sent"
        ),
    }


def update_page(token: str, page_id: str, content: str = "", find: str = "",
                replace: str = "", replace_all: bool = False) -> dict:
    """Edit a page's content: whole-page `content`, or a `find`/`replace` pair.

    Find-and-replace is the safer of the two and is what the agent should reach
    for — rewriting a whole page means regenerating text it did not intend to
    change, which is how an agent quietly deletes half a document.
    """
    pid = normalise_id(page_id)
    if content and find:
        raise NotionError(
            "Give either `content` (replace the whole page) or `find`/`replace` "
            "(edit part of it), not both.", "bad_request",
        )

    if content:
        _replace_markdown(token, pid, content)
        expect, gone = content, ""
    elif find:
        request("PATCH", f"/v1/pages/{pid}/markdown", token,
                version=MARKDOWN_API_VERSION,
                json={"type": "update_content", "update_content": {"content_updates": [{
                    "old_str": find,
                    "new_str": replace,
                    "replace_all_matches": bool(replace_all),
                }]}})
        expect, gone = replace, find
    else:
        raise NotionError("Nothing to write: pass `content`, or `find` and `replace`.",
                          "bad_request")

    after = _read_markdown(token, pid)["markdown"]
    verified = _contains(after, expect)
    if verified and gone and not replace_all:
        pass  # one occurrence replaced; the rest may legitimately remain
    elif verified and gone and replace_all:
        verified = not _contains(after, gone)

    return {
        "ok": True,
        "id": pid,
        "verified": verified,
        "verified_by": "re-read the page markdown after editing",
        "chars": len(after),
        "message": (
            "Edit applied and confirmed on the page" if verified
            else "Notion accepted the edit, but the new text is not on the page — "
                 "check that `find` matched the page text exactly"
        ),
    }


def update_properties(token: str, page_id: str, properties: dict) -> dict:
    """Set database properties on a page, using plain values, then confirm them."""
    pid = normalise_id(page_id)
    if not isinstance(properties, dict) or not properties:
        raise NotionError("Pass a `properties` object, e.g. {\"Status\": \"Done\"}.",
                          "bad_request")

    page = request("GET", f"/v1/pages/{pid}", token)
    schema = page.get("properties") or {}

    payload: dict = {}
    for name, value in properties.items():
        existing = _match_property(schema, name)
        if existing is None:
            raise NotionError(
                f"“{name}” isn't a property on that page. It has: "
                f"{', '.join(sorted(schema)) or '(none)'}.",
                "unknown_property",
            )
        real_name, kind = existing
        payload[real_name] = to_value(kind, value)

    request("PATCH", f"/v1/pages/{pid}", token, json={"properties": payload})

    # Verify each property individually, so a partial write is reported as one.
    after = request("GET", f"/v1/pages/{pid}", token)
    found = after.get("properties") or {}
    checked, missed = {}, []
    for name, value in properties.items():
        real_name, _kind = _match_property(schema, name)
        actual = plain(found.get(real_name, {}))
        checked[real_name] = actual
        if not same_value(value, actual):
            missed.append(real_name)

    return {
        "ok": True,
        "id": pid,
        "title": title_of(after),
        "url": after.get("url", ""),
        "properties": checked,
        "verified": not missed,
        "verified_by": "re-read the page properties after writing them",
        "message": (
            f"Set {', '.join(checked)} — confirmed on the page" if not missed
            else f"Notion accepted the write, but {', '.join(missed)} did not take the "
                 "new value (a select option may not exist yet)"
        ),
    }


# --------------------------------------------------------------------------- #
# Internals                                                                   #
# --------------------------------------------------------------------------- #
def _replace_markdown(token: str, page_id: str, markdown: str) -> None:
    request("PATCH", f"/v1/pages/{page_id}/markdown", token,
            version=MARKDOWN_API_VERSION,
            json={"type": "replace_content",
                  "replace_content": {"new_str": str(markdown)[:MAX_MARKDOWN]}})


def _resolve_parent(token: str, parent_id: str) -> tuple[dict, str]:
    """Work out whether `parent_id` names a page or a database, and where a new
    child's title goes.

    The agent has one id and no idea which kind it is — it copied it from a URL.
    Asking it to tell us would just move the guess upstream, so we look.
    """
    pid = normalise_id(parent_id)

    try:
        request("GET", f"/v1/pages/{pid}", token)
        return {"page_id": pid}, "title"
    except NotionError as ex:
        if ex.status not in (403, 404):
            raise

    # Not a page: a database, or a data source inside one. A database holds one
    # or more data sources, and pages are created against the data source.
    try:
        database = request("GET", f"/v1/databases/{pid}", token)
        sources = database.get("data_sources") or []
        if not sources:
            raise NotionError(
                "That database has no data source to add pages to.", "no_data_source"
            )
        source_id = sources[0].get("id", "")
    except NotionError as ex:
        if ex.status not in (403, 404):
            raise
        source_id = pid  # perhaps the id *is* a data source

    source = request("GET", f"/v1/data_sources/{source_id}", token)
    for name, spec in (source.get("properties") or {}).items():
        if isinstance(spec, dict) and spec.get("type") == "title":
            return {"data_source_id": source_id}, name
    return {"data_source_id": source_id}, "Name"


def _match_property(schema: dict, name: str) -> tuple[str, str] | None:
    """Find a property by name, tolerating case and spacing differences."""
    if name in schema:
        return name, schema[name].get("type", "")
    wanted = str(name).strip().lower()
    for real, spec in schema.items():
        if str(real).strip().lower() == wanted:
            return real, spec.get("type", "")
    return None


def _properties(page: dict) -> dict:
    return {name: plain(prop) for name, prop in (page.get("properties") or {}).items()}


def _reference(item: dict) -> dict:
    """One search hit, flattened to what the agent needs to pick between them."""
    kind = "database" if item.get("object") == "data_source" else "page"
    return {
        "id": item.get("id", ""),
        "kind": kind,
        "title": title_of(item) or "(untitled)",
        "url": item.get("url", ""),
        "last_edited": item.get("last_edited_time", ""),
    }


def _contains(haystack: str, needle: str) -> bool:
    """Whitespace-tolerant containment — Notion reflows Markdown as it stores it."""
    if not needle:
        return True
    squash = lambda s: " ".join(str(s).split())  # noqa: E731
    return squash(needle) in squash(haystack)
