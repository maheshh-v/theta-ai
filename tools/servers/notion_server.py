"""
MCP server: Notion.

Stateless by design — each tool receives the caller's Notion token, which the MCP
client manager injects at call time (it is never provided by the LLM). Content is
Markdown in both directions, so reading a page and editing it use the same
representation the model reasons in.

Run standalone:  python tools/servers/notion_server.py
"""

from __future__ import annotations

import sys
from pathlib import Path

# Launched as a standalone script, so sys.path[0] is this directory — the project
# root has to go on the path before any `tools.*` import can resolve.
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from tools.servers._common import make_server  # noqa: E402

mcp = make_server("theta-notion")


@mcp.tool()
def notion_search(notion_token: str, query: str = "", kind: str = "",
                  limit: int = 10) -> dict:
    """Find Notion pages and databases by title. Leave `query` blank to list
    recently edited items. Set kind="page" or kind="database" to narrow it."""
    from tools import notion_tools

    return notion_tools.notion_search(notion_token, query, kind, limit)


@mcp.tool()
def notion_read_page(notion_token: str, page_id: str) -> dict:
    """Read a Notion page: its full content as Markdown, plus its database
    properties. Accepts a page id or a Notion URL."""
    from tools import notion_tools

    return notion_tools.notion_read_page(notion_token, page_id)


@mcp.tool()
def notion_read_database(notion_token: str, database_id: str, limit: int = 25) -> dict:
    """Read a Notion database: its property schema and its rows, with each row's
    values flattened to plain text."""
    from tools import notion_tools

    return notion_tools.notion_read_database(notion_token, database_id, limit)


@mcp.tool()
def notion_create_page(notion_token: str, parent_id: str, title: str,
                       markdown: str = "") -> dict:
    """Create a Notion page under a parent page or database. `markdown` is the
    page body. Returns the new page's URL, and confirms it by reading it back."""
    from tools import notion_tools

    return notion_tools.notion_create_page(notion_token, parent_id, title, markdown)


@mcp.tool()
def notion_update_page(notion_token: str, page_id: str, content: str = "",
                       find: str = "", replace: str = "",
                       replace_all: bool = False) -> dict:
    """Edit a Notion page's content. Prefer `find`/`replace` to change part of a
    page; `content` overwrites the whole page. Read the page first, and confirm
    the returned `verified` flag."""
    from tools import notion_tools

    return notion_tools.notion_update_page(
        notion_token, page_id, content, find, replace, replace_all
    )


@mcp.tool()
def notion_update_properties(notion_token: str, page_id: str, properties: dict) -> dict:
    """Set database properties on a Notion page using plain values, e.g.
    {"Status": "Done", "Priority": 2, "Tags": ["urgent"]}. Values are read back
    afterwards and reported in `verified`."""
    from tools import notion_tools

    return notion_tools.notion_update_properties(notion_token, page_id, properties)


if __name__ == "__main__":
    mcp.run(transport="stdio")
