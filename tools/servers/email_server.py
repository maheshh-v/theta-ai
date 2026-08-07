"""
MCP server: Email tool (mock inbox).

Reads sample emails from ../../data/emails.json and can draft replies. Nothing
is ever actually sent — drafts are saved locally, keeping the demo send-safe.

Run standalone for a quick smoke test:
    python tools/servers/email_server.py
"""

from _common import make_server  # noqa: E402  (adds project root to sys.path)
from tools import backends  # noqa: E402

mcp = make_server("email")


@mcp.tool()
def email_list(unread_only: bool = False) -> list:
    """List inbox emails. Set unread_only=True to see only unread messages."""
    return backends.email_list(unread_only)


@mcp.tool()
def email_read(email_id: str) -> dict:
    """Read the full contents of a single email by its id (e.g. 'e1')."""
    return backends.email_read(email_id)


@mcp.tool()
def email_search(query: str) -> list:
    """Search emails by sender, subject, or body text."""
    return backends.email_search(query)


@mcp.tool()
def email_draft_reply(email_id: str, message: str) -> dict:
    """Draft a reply to an email (saved locally, NOT sent). Provide the reply text."""
    return backends.email_draft_reply(email_id, message)


if __name__ == "__main__":
    mcp.run(transport="stdio")
