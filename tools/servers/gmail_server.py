"""
MCP server: Gmail.

Stateless by design — each tool receives the caller's OAuth access token, which
the MCP client manager injects at call time (it is never provided by the LLM).
The reply tools resolve recipient, subject and threading headers from the message
being replied to, so the agent supplies only a message id and the reply text and
cannot invent an address.

Run standalone:  python tools/servers/gmail_server.py
"""

from __future__ import annotations

import sys
from pathlib import Path

# Launched as a standalone script, so sys.path[0] is this directory — the project
# root has to go on the path before any `tools.*` import can resolve.
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from tools.servers._common import make_server  # noqa: E402

mcp = make_server("theta-gmail")


@mcp.tool()
def gmail_search(access_token: str, query: str = "", max_results: int = 10) -> dict:
    """Search email using Gmail query syntax ('from:priya', 'invoice',
    'subject:roadmap', 'is:unread', 'newer_than:7d'). Leave query blank for the
    inbox. Returns ids, senders, subjects and snippets."""
    from tools import google_tools

    return google_tools.gmail_search(access_token, query, max_results)


@mcp.tool()
def gmail_read(access_token: str, message_id: str) -> dict:
    """Read one email in full — headers plus the plain-text body — by its id."""
    from tools import google_tools

    return google_tools.gmail_read(access_token, message_id)


@mcp.tool()
def gmail_thread(access_token: str, thread_id: str) -> dict:
    """Read a whole email conversation, oldest first. Use this to summarise a
    thread rather than reading each message separately."""
    from tools import google_tools

    return google_tools.gmail_thread(access_token, thread_id)


@mcp.tool()
def gmail_draft_reply(access_token: str, message_id: str, body: str) -> dict:
    """Draft a reply to an email. Saved to Drafts and NOT sent, so it needs no
    approval. Recipient, subject and threading come from the original message."""
    from tools import google_tools

    return google_tools.gmail_draft_reply(access_token, message_id, body)


@mcp.tool()
def gmail_send_reply(access_token: str, message_id: str, body: str) -> dict:
    """Send a reply to an email. The run pauses for the user to read and edit the
    message before anything leaves the mailbox."""
    from tools import google_tools

    return google_tools.gmail_send_reply(access_token, message_id, body)


if __name__ == "__main__":
    mcp.run(transport="stdio")
