"""
In-process tool functions for Gmail and Calendar, named exactly like the tools
the agent calls. Both the MCP servers (`tools/servers/gmail_server.py`,
`calendar_server.py`) and the in-process fallback (`tool_specs.py`) import these,
so there is one implementation per tool.

Every function takes `access_token` as its first argument; the `MCPManager`
injects the current session's token at call time (it is never supplied by the
LLM). Reply helpers resolve the recipient / subject / thread from the original
message so the agent only needs a message id and the reply text.
"""

from __future__ import annotations

from integrations.google import calendar, gmail


# --- Gmail ---------------------------------------------------------------- #
def gmail_list(access_token: str, unread_only: bool = False) -> list:
    return gmail.list_messages(access_token, unread_only=unread_only)


def gmail_search(access_token: str, query: str) -> list:
    return gmail.search_messages(access_token, query)


def gmail_read(access_token: str, message_id: str) -> dict:
    return gmail.read_message(access_token, message_id)


def gmail_draft_reply(access_token: str, message_id: str, body: str) -> dict:
    ctx = gmail.reply_context(access_token, message_id)
    return gmail.create_draft(
        access_token, ctx["to"], ctx["subject"], body,
        thread_id=ctx["thread_id"], in_reply_to=ctx["in_reply_to"],
    )


def gmail_send_reply(access_token: str, message_id: str, body: str) -> dict:
    ctx = gmail.reply_context(access_token, message_id)
    return gmail.send_message(
        access_token, ctx["to"], ctx["subject"], body,
        thread_id=ctx["thread_id"], in_reply_to=ctx["in_reply_to"],
    )


# --- Calendar ------------------------------------------------------------- #
def calendar_list(access_token: str, date: str = "") -> list:
    return calendar.list_events(access_token, date)


def calendar_add(access_token: str, title: str, date: str, time: str = "",
                 duration_min: int = 60, location: str = "",
                 description: str = "") -> dict:
    return calendar.add_event(access_token, title, date, time, duration_min,
                              location, description)


def calendar_update(access_token: str, event_id: str, title: str = "",
                    date: str = "", time: str = "", location: str = "",
                    description: str = "") -> dict:
    return calendar.update_event(access_token, event_id, title, date, time,
                                 location, description)
