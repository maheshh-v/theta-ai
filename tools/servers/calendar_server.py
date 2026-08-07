"""
MCP server: Google Calendar (real, via the Google API).

Stateless — the caller's OAuth access token is injected by the MCP client
manager at call time. Adding or updating events changes the user's real
calendar, so those tools are approval-gated by the agent.

Run standalone for a smoke test:  python tools/servers/calendar_server.py
"""

from _common import make_server  # noqa: E402  (adds project root to sys.path)
from tools import google_tools  # noqa: E402

mcp = make_server("calendar")


@mcp.tool()
def calendar_list(access_token: str, date: str = "") -> list:
    """List upcoming calendar events, or all events on one YYYY-MM-DD date."""
    return google_tools.calendar_list(access_token, date)


@mcp.tool()
def calendar_add(access_token: str, title: str, date: str, time: str = "",
                 duration_min: int = 60, location: str = "",
                 description: str = "") -> dict:
    """Add an event. date=YYYY-MM-DD, time=HH:MM (24h; omit for an all-day
    event). Creates a real event on the user's primary calendar."""
    return google_tools.calendar_add(access_token, title, date, time,
                                     duration_min, location, description)


@mcp.tool()
def calendar_update(access_token: str, event_id: str, title: str = "",
                    date: str = "", time: str = "", location: str = "",
                    description: str = "") -> dict:
    """Update an existing event by id. Only the fields you provide are changed."""
    return google_tools.calendar_update(access_token, event_id, title, date,
                                        time, location, description)


if __name__ == "__main__":
    mcp.run(transport="stdio")
