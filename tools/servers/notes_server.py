"""
MCP server: Notes tool.

Exposes save/search/list operations over the Model Context Protocol (stdio
transport). Data lives in ../../data/notes.json. This is a real MCP server —
the agent connects to it as an MCP client and discovers these tools at runtime.

Run standalone for a quick smoke test:
    python tools/servers/notes_server.py
"""

from _common import make_server  # noqa: E402  (adds project root to sys.path)
from tools import backends  # noqa: E402

mcp = make_server("notes")


@mcp.tool()
def notes_list() -> list:
    """List all saved notes with their id, title, and tags."""
    return backends.notes_list()


@mcp.tool()
def notes_add(title: str, content: str, tags: list[str] | None = None) -> dict:
    """Save a new note. Provide a short title, the body content, and optional tags."""
    return backends.notes_add(title, content, tags)


@mcp.tool()
def notes_search(query: str) -> list:
    """Search saved notes by title, content, or tag. Returns matching notes."""
    return backends.notes_search(query)


if __name__ == "__main__":
    mcp.run(transport="stdio")
