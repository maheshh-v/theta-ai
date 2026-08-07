"""
MCP server: Calendar + Tasks tool.

Manages local events and to-do tasks stored in ../../data/calendar.json.
Supports viewing/adding events and viewing/adding/completing tasks.

Run standalone for a quick smoke test:
    python tools/servers/calendar_server.py
"""

from _common import make_server  # noqa: E402  (adds project root to sys.path)
from tools import backends  # noqa: E402

mcp = make_server("calendar")


@mcp.tool()
def calendar_list_events(date: str = "") -> list:
    """List calendar events. Optionally filter to one date (YYYY-MM-DD)."""
    return backends.calendar_list_events(date)


@mcp.tool()
def calendar_add_event(
    title: str, date: str, time: str = "", location: str = "", notes: str = ""
) -> dict:
    """Add a calendar event. date=YYYY-MM-DD, time=HH:MM (24h)."""
    return backends.calendar_add_event(title, date, time, location, notes)


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
