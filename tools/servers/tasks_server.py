"""
MCP server: Tasks (local to-do list).

Theta's own lightweight task memory, stored in ../../data/calendar.json. Kept
local (not Google Tasks) on purpose — it's the agent's working memory and needs
no external account. Data starts empty.

Run standalone for a smoke test:  python tools/servers/tasks_server.py
"""

from _common import make_server  # noqa: E402  (adds project root to sys.path)
from tools import backends  # noqa: E402

mcp = make_server("tasks")


@mcp.tool()
def tasks_list(include_done: bool = False) -> list:
    """List to-do tasks. Set include_done=True to also show completed tasks."""
    return backends.tasks_list(include_done)


@mcp.tool()
def tasks_add(title: str, due: str = "", priority: str = "medium") -> dict:
    """Add a to-do task. due=YYYY-MM-DD; priority is low/medium/high."""
    return backends.tasks_add(title, due, priority)


@mcp.tool()
def tasks_complete(task_id: str) -> dict:
    """Mark a to-do task as done by its id (e.g. 't1')."""
    return backends.tasks_complete(task_id)


if __name__ == "__main__":
    mcp.run(transport="stdio")
