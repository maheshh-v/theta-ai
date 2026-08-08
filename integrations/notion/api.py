"""
The Notion REST surface, and the errors it produces said in English.

Theta talks to eight Notion endpoints. The interesting choice is *which* eight:
the block-tree API (`/v1/blocks/...`) models a page as a nested JSON document,
which is faithful and almost unusable for an agent — reading a page costs a
paginated tree walk and writing one means hand-assembling block objects. The
`/v1/pages/{id}/markdown` pair collapses both into text, which is the one format
a language model is actually good at. Everything here is built on that.

The other thing this module does is translate Notion's HTTP codes into
instructions. A 404 from Notion almost never means "no such page" — it means the
page exists and your integration has not been invited to it. Saying so saves the
user a support search.

ATTRIBUTION
    The endpoint set, the operation shapes and the per-operation API versions
    below were derived from the OpenAPI specification published in Notion's own
    MCP server (`scripts/notion-openapi.json`), MIT-licensed,
    © 2025 Notion Labs, Inc. — https://github.com/makenotion/notion-mcp-server
    That project generates its tools from the spec at runtime; Theta instead
    hand-picks the operations it needs. No code from it is reproduced here.
"""

from __future__ import annotations

import logging
import re
from typing import Any

import requests

BASE = "https://api.notion.com"

# Notion pins behaviour to a dated version. The markdown endpoints are newer than
# the rest of the API and reject the older version outright, so the version is
# per-request rather than global.
API_VERSION = "2025-09-03"
MARKDOWN_API_VERSION = "2026-03-11"

TIMEOUT = 30

_log = logging.getLogger("theta.notion")

# A Notion id is 32 hex characters, usually dashed, and usually arrives glued to
# the end of a URL the user pasted.
_ID_RE = re.compile(r"([0-9a-fA-F]{32})")
_DASHED_RE = re.compile(
    r"([0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12})"
)


class NotionError(RuntimeError):
    """A Notion API call failed. The message is written to be shown to the user."""

    def __init__(self, message: str, code: str = "notion_error", status: int = 0) -> None:
        super().__init__(message)
        self.code = code
        self.status = status


# --------------------------------------------------------------------------- #
# Identifiers                                                                 #
# --------------------------------------------------------------------------- #
def normalise_id(raw: str) -> str:
    """Accept anything that identifies a Notion object and return a bare id.

    Models and users both hand over full URLs
    (`https://notion.so/Roadmap-1f2a…`), so pulling the id out of one is not a
    nicety — it is the common case.
    """
    text = str(raw or "").strip()
    if not text:
        raise NotionError("No Notion page or database id was given.", "bad_id")
    dashed = _DASHED_RE.search(text)
    if dashed:
        return dashed.group(1).lower()
    plain = _ID_RE.findall(text.replace("-", ""))
    if plain:
        h = plain[-1].lower()
        return f"{h[:8]}-{h[8:12]}-{h[12:16]}-{h[16:20]}-{h[20:]}"
    raise NotionError(
        f"“{text[:60]}” doesn't contain a Notion id. Paste the page URL or its id.",
        "bad_id",
    )


# --------------------------------------------------------------------------- #
# Transport                                                                   #
# --------------------------------------------------------------------------- #
def request(
    method: str,
    path: str,
    token: str,
    *,
    json: dict | None = None,
    params: dict | None = None,
    version: str = API_VERSION,
) -> Any:
    """One Notion call. Raises `NotionError` with a message worth reading."""
    if not token:
        raise NotionError("Notion isn't connected. Add a token in Settings.", "not_connected")

    url = path if path.startswith("http") else f"{BASE}{path}"
    headers = {
        "Authorization": f"Bearer {token}",
        "Notion-Version": version,
        "Content-Type": "application/json",
    }
    try:
        resp = requests.request(
            method, url, headers=headers, json=json, params=params, timeout=TIMEOUT
        )
    except requests.RequestException as ex:
        raise NotionError(f"Could not reach Notion: {ex}", "network") from ex

    if resp.status_code < 400:
        if resp.status_code == 204 or not resp.content:
            return {}
        try:
            return resp.json()
        except ValueError as ex:
            raise NotionError("Notion returned a response Theta could not read.",
                              "bad_response", resp.status_code) from ex

    raise _error_for(resp)


def _error_for(resp) -> NotionError:
    """Turn a Notion error response into something a person can act on."""
    detail, code = "", ""
    try:
        body = resp.json()
        detail = str(body.get("message", "") or "")
        code = str(body.get("code", "") or "")
    except ValueError:
        pass

    status = resp.status_code
    if status == 401:
        return NotionError(
            "Notion rejected the token. Reconnect Notion in Settings.",
            code or "unauthorized", status,
        )
    if status in (403, 404):
        # Both mean the same thing in practice: the integration was never given
        # access. Notion's own wording ("Could not find page") sends people
        # hunting for a typo instead of into the share menu.
        return NotionError(
            "Notion can't see that page or database. Open it in Notion, then "
            "“⋯ → Connections → Connect to” and pick your Theta integration. "
            + (f"({detail})" if detail else ""),
            code or "no_access", status,
        )
    if status == 429:
        return NotionError("Notion is rate-limiting Theta. Try again in a moment.",
                           code or "rate_limited", status)
    if status == 400 and code == "validation_error":
        return NotionError(f"Notion rejected the request: {detail}", code, status)
    return NotionError(
        detail or f"Notion returned HTTP {status}.", code or "notion_error", status
    )


# --------------------------------------------------------------------------- #
# Connection check                                                            #
# --------------------------------------------------------------------------- #
def whoami(token: str) -> dict:
    """Identify the integration behind a token — used by the Settings test."""
    me = request("GET", "/v1/users/me", token)
    bot = me.get("bot") or {}
    owner = (bot.get("owner") or {}).get("user") or {}
    return {
        "id": me.get("id", ""),
        "name": me.get("name", "") or bot.get("workspace_name", ""),
        "workspace": bot.get("workspace_name", ""),
        "owner": owner.get("name", ""),
    }


# --------------------------------------------------------------------------- #
# Property values                                                             #
# --------------------------------------------------------------------------- #
def plain(prop: dict) -> Any:
    """Flatten one Notion property *value* into something readable.

    Notion property values are tagged unions with a dozen arms. The agent does
    not need that structure — it needs "Status is Done" — so every arm collapses
    to a string, number or bool here, and `to_value()` inverts it on the way back.
    """
    if not isinstance(prop, dict):
        return ""
    kind = prop.get("type", "")
    value = prop.get(kind)

    if kind in ("title", "rich_text"):
        return "".join(part.get("plain_text", "") for part in value or [])
    if kind in ("number", "checkbox", "url", "email", "phone_number"):
        return value
    if kind in ("select", "status"):
        return (value or {}).get("name", "")
    if kind == "multi_select":
        return [item.get("name", "") for item in value or []]
    if kind == "date":
        if not value:
            return ""
        start, end = value.get("start", ""), value.get("end")
        return f"{start} → {end}" if end else start
    if kind == "people":
        return [p.get("name", "") or p.get("id", "") for p in value or []]
    if kind == "relation":
        return [r.get("id", "") for r in value or []]
    if kind == "files":
        return [f.get("name", "") for f in value or []]
    if kind == "formula":
        inner = value or {}
        return inner.get(inner.get("type", ""), "")
    if kind == "rollup":
        inner = value or {}
        if inner.get("type") == "array":
            return [plain(item) for item in inner.get("array", [])]
        return inner.get(inner.get("type", ""), "")
    if kind in ("created_time", "last_edited_time"):
        return value or ""
    if kind in ("created_by", "last_edited_by"):
        return (value or {}).get("name", "")
    if kind == "unique_id":
        inner = value or {}
        prefix = inner.get("prefix") or ""
        return f"{prefix}-{inner.get('number')}" if prefix else inner.get("number")
    return value if isinstance(value, (str, int, float, bool)) else ""


# Properties Notion computes for itself. Writing one is always an error, and a
# clear refusal beats a validation_error the model will try to work around.
READ_ONLY_TYPES = {
    "formula", "rollup", "created_time", "created_by",
    "last_edited_time", "last_edited_by", "unique_id",
}


def to_value(kind: str, value: Any) -> dict:
    """Build the Notion property value for `kind` from a plain Python value.

    This is what lets the agent write `{"Status": "Done"}` instead of
    `{"Status": {"select": {"name": "Done"}}}` — the schema is known from the
    page itself, so making the model restate it would only invite mistakes.
    """
    if kind in READ_ONLY_TYPES:
        raise NotionError(f"“{kind}” is computed by Notion and cannot be set.",
                          "read_only_property")

    empty = value in (None, "", [])
    if kind in ("title", "rich_text"):
        return {kind: [{"type": "text", "text": {"content": str(value)[:2000]}}]}
    if kind == "number":
        if empty:
            return {"number": None}
        try:
            number = float(value)
        except (TypeError, ValueError):
            raise NotionError(f"“{value}” is not a number.", "bad_value") from None
        return {"number": int(number) if number.is_integer() else number}
    if kind in ("select", "status"):
        return {kind: None if empty else {"name": str(value)}}
    if kind == "multi_select":
        items = value if isinstance(value, list) else [v.strip() for v in str(value).split(",")]
        return {"multi_select": [{"name": str(v)} for v in items if str(v).strip()]}
    if kind == "checkbox":
        if isinstance(value, str):
            return {"checkbox": value.strip().lower() in ("true", "yes", "1", "done", "checked")}
        return {"checkbox": bool(value)}
    if kind == "date":
        if empty:
            return {"date": None}
        if isinstance(value, dict):
            return {"date": value}
        return {"date": {"start": str(value)}}
    if kind in ("url", "email", "phone_number"):
        return {kind: None if empty else str(value)}
    if kind == "people":
        ids = value if isinstance(value, list) else [value]
        return {"people": [{"object": "user", "id": normalise_id(i)} for i in ids if i]}
    if kind == "relation":
        ids = value if isinstance(value, list) else [value]
        return {"relation": [{"id": normalise_id(i)} for i in ids if i]}
    raise NotionError(f"Theta can't set “{kind}” properties yet.", "unsupported_property")


def same_value(written: Any, found: Any) -> bool:
    """Did a property end up holding what we asked for?

    Deliberately forgiving: Notion normalises whitespace, returns numbers as
    floats and orders multi-selects itself, so an exact comparison would report
    failure on a write that plainly worked.
    """
    def norm(v):
        if isinstance(v, list):
            return sorted(str(x).strip().lower() for x in v)
        if isinstance(v, bool):
            return v
        if isinstance(v, (int, float)):
            return float(v)
        return str(v or "").strip().lower()

    a, b = norm(written), norm(found)
    if isinstance(a, float) or isinstance(b, float):
        try:
            return float(a) == float(b)
        except (TypeError, ValueError):
            return False
    if isinstance(a, str) and isinstance(b, str) and a and b:
        # A date written as "2026-08-08" comes back as "2026-08-08T00:00:00.000+01:00".
        return a == b or b.startswith(a) or a.startswith(b)
    return a == b


def title_of(obj: dict) -> str:
    """The title of a page or data source, wherever it happens to live."""
    if not isinstance(obj, dict):
        return ""
    direct = obj.get("title")
    if isinstance(direct, list):
        text = "".join(part.get("plain_text", "") for part in direct)
        if text:
            return text
    for prop in (obj.get("properties") or {}).values():
        if isinstance(prop, dict) and prop.get("type") == "title":
            text = plain(prop)
            if text:
                return text
    return ""
