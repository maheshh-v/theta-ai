"""
Small compatibility shim so each MCP server file works across SDK versions.

The MCP Python SDK renamed the high-level server class from `FastMCP`
(mcp < 2.0) to `MCPServer` (mcp >= 2.0). Both expose the same ergonomics we rely
on: a `.tool()` decorator and `.run(transport="stdio")`. This helper hides the
difference.

Each server script puts the project root on sys.path itself, before importing
this module (e.g. `python tools/servers/browser_server.py`) — doing it here would
already be too late.
"""

from __future__ import annotations

import sys
from pathlib import Path

# Ensure the project root is importable when this file is run as a script
# (e.g. `python tools/servers/notes_server.py`).
_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))


def make_server(name: str):
    """Return a high-level MCP server instance, whichever SDK is installed."""
    try:  # mcp >= 2.0
        from mcp.server.mcpserver import MCPServer

        return MCPServer(name=name)
    except ImportError:  # mcp < 2.0
        from mcp.server.fastmcp import FastMCP

        return FastMCP(name)
