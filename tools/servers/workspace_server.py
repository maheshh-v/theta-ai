"""
MCP server: the agent's workspace.

Sandboxed text file read/write/list under `data/workspace`. This is where a run's
output becomes something you can keep — a CSV of extracted rows, a Markdown
summary, a JSON dump.

Run standalone:  python tools/servers/workspace_server.py
"""

from __future__ import annotations

import sys
from pathlib import Path

# Launched as a standalone script, so sys.path[0] is this directory — the project
# root has to go on the path before any `tools.*` import can resolve.
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from tools.servers._common import make_server  # noqa: E402

mcp = make_server("theta-workspace")


@mcp.tool()
def file_write(name: str, content: str, append: bool = False) -> dict:
    """Save a text file (CSV, Markdown, JSON, txt) into Theta's workspace."""
    from tools import workspace_tools

    return workspace_tools.file_write(name, content, append)


@mcp.tool()
def file_read(name: str, max_chars: int = 20000) -> dict:
    """Read a file back out of Theta's workspace."""
    from tools import workspace_tools

    return workspace_tools.file_read(name, max_chars)


@mcp.tool()
def file_list() -> dict:
    """List the files saved in Theta's workspace."""
    from tools import workspace_tools

    return workspace_tools.file_list()


if __name__ == "__main__":
    mcp.run(transport="stdio")
