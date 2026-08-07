"""
MCP server: Gmail (real, via the Google API).

Stateless by design — each tool receives the caller's OAuth access token, which
the MCP client manager injects at call time (it is never provided by the LLM).
The reply tools resolve recipient/subject/thread from the original message, so
the agent only supplies a message id and the reply text.

Run standalone for a smoke test (tools will report "not connected" without a
token):  python tools/servers/gmail_server.py
"""

from _common import make_server  # noqa: E402  (adds project root to sys.path)
from tools import google_tools  # noqa: E402

mcp = make_server("gmail")


@mcp.tool()
def gmail_list(access_token: str, unread_only: bool = False) -> list:
    """List recent inbox emails (id, from, subject, date, snippet). Set
    unread_only=True to see only unread messages."""
    return google_tools.gmail_list(access_token, unread_only)


@mcp.tool()
def gmail_search(access_token: str, query: str) -> list:
    """Search email using Gmail query syntax (e.g. 'from:priya', 'invoice',
    'subject:roadmap'). Returns matching messages."""
    return google_tools.gmail_search(access_token, query)


@mcp.tool()
def gmail_read(access_token: str, message_id: str) -> dict:
    """Read one email in full (headers plus the plain-text body) by its id."""
    return google_tools.gmail_read(access_token, message_id)


@mcp.tool()
def gmail_draft_reply(access_token: str, message_id: str, body: str) -> dict:
    """Draft a reply to the email with the given id (saved to Drafts, NOT sent).
    Provide the reply text as `body`."""
    return google_tools.gmail_draft_reply(access_token, message_id, body)


@mcp.tool()
def gmail_send_reply(access_token: str, message_id: str, body: str) -> dict:
    """Send a reply to the email with the given id. Provide the reply text as
    `body`. Sending requires the user's explicit approval."""
    return google_tools.gmail_send_reply(access_token, message_id, body)


if __name__ == "__main__":
    mcp.run(transport="stdio")
