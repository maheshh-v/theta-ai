"""
MCP server: the brief library.

Exposes `brief_list` and `brief_read` over stdio, giving the agent access to
research it has already done.

Run standalone:  python tools/servers/briefs_server.py
"""

from __future__ import annotations

import sys
from pathlib import Path

# Launched as a standalone script, so sys.path[0] is this directory — the project
# root has to go on the path before any `tools.*` import can resolve.
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from tools.servers._common import make_server  # noqa: E402

mcp = make_server("theta-briefs")


@mcp.tool()
def brief_list(query: str = "", limit: int = 10) -> dict:
    """List saved research briefs, newest first. Pass a query to filter by keyword."""
    from tools import brief_tools

    return brief_tools.brief_list(query, limit)


@mcp.tool()
def brief_read(brief_id: str) -> dict:
    """Read a saved brief in full: summary, key findings, sections and sources."""
    from tools import brief_tools

    return brief_tools.brief_read(brief_id)


if __name__ == "__main__":
    mcp.run(transport="stdio")
