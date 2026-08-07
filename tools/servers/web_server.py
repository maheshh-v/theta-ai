"""
MCP server: live web access.

Exposes `web_search` and `web_read` over stdio. This server is self-contained —
point any MCP client (Claude Desktop, another agent) at it and it works, which is
the point of putting Theta's world-facing tools behind the protocol rather than
calling them directly.

Run standalone:  python tools/servers/web_server.py
"""

from __future__ import annotations

import sys
from pathlib import Path

# Launched as a standalone script, so sys.path[0] is this directory — the project
# root has to go on the path before any `tools.*` import can resolve.
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from tools.servers._common import make_server  # noqa: E402

mcp = make_server("theta-web")


@mcp.tool()
def web_search(
    query: str,
    max_results: int = 5,
    search_provider: str = "",
    search_api_key: str = "",
) -> dict:
    """Search the live web. Returns ranked results with title, url, site and snippet."""
    from tools import web_tools

    return web_tools.web_search(query, max_results, search_provider, search_api_key)


@mcp.tool()
def web_read(url: str, search_provider: str = "", search_api_key: str = "") -> dict:
    """Read one web page or PDF and return its main text with the boilerplate stripped."""
    from tools import web_tools

    return web_tools.web_read(url)


if __name__ == "__main__":
    mcp.run(transport="stdio")
