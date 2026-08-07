"""
Central tool metadata — the single source of truth the rest of the app reads,
regardless of whether a tool arrives via live MCP discovery or the in-process
fallback.

* `AUTH_SERVERS`   — servers whose tools need a Google access token (injected by
                     the manager at call time, never chosen by the LLM).
* `RESERVED_PARAMS`— parameters hidden from the LLM and injected server-side.
* `CONFIRM_TOOLS`  — tools that require explicit human approval before running
                     (they send mail or change a real calendar).
* `ACTION_LABELS`  — short human verbs for the execution trace.
* helpers          — `describe_action` (approval card) and `summarize` (trace).
"""

from __future__ import annotations

AUTH_SERVERS = {"gmail", "calendar"}
RESERVED_PARAMS = {"access_token"}
CONFIRM_TOOLS = {"gmail_send_reply", "calendar_add", "calendar_update"}

ACTION_LABELS = {
    "gmail_list": "Reading Gmail",
    "gmail_search": "Searching Gmail",
    "gmail_read": "Opening email",
    "gmail_draft_reply": "Drafting reply",
    "gmail_send_reply": "Sending reply",
    "calendar_list": "Checking calendar",
    "calendar_add": "Adding event",
    "calendar_update": "Updating event",
    "tasks_list": "Reading tasks",
    "tasks_add": "Adding task",
    "tasks_complete": "Completing task",
    "notes_list": "Reading notes",
    "notes_add": "Saving note",
    "notes_search": "Searching notes",
}


def tag_for(name: str) -> str:
    return "confirm" if name in CONFIRM_TOOLS else "read"


def needs_auth(server: str | None) -> bool:
    return server in AUTH_SERVERS


def label_for(name: str) -> str:
    return ACTION_LABELS.get(name, name.replace("_", " ").capitalize())


def describe_action(name: str, args: dict) -> str:
    """One-line, human-readable description of a pending action for the approval
    card. Uses only the arguments (no network calls)."""
    a = args or {}
    if name == "gmail_send_reply":
        preview = (a.get("body") or "").strip().replace("\n", " ")
        if len(preview) > 140:
            preview = preview[:140] + "…"
        return f"Send this reply:\n\n“{preview}”"
    if name == "calendar_add":
        when = a.get("date", "")
        if a.get("time"):
            when += f" {a['time']}"
        return f"Add “{a.get('title', 'event')}” to your calendar on {when}."
    if name == "calendar_update":
        return f"Update calendar event {a.get('event_id', '')}."
    return f"Run {label_for(name)}."


def summarize(name: str, result) -> str:
    """Short status line for the trace, e.g. 'Found 3 emails'."""
    if isinstance(result, dict) and result.get("error"):
        msg = result.get("message") or result.get("error")
        return f"⚠️ {msg}"
    n = len(result) if isinstance(result, list) else None

    if name in ("gmail_list", "gmail_search"):
        return f"Found {n} email{'s' if n != 1 else ''}" if n is not None else "Searched Gmail"
    if name == "gmail_read":
        subj = result.get("subject", "") if isinstance(result, dict) else ""
        return f"Read “{subj}”" if subj else "Read email"
    if name == "gmail_draft_reply":
        return "Draft saved"
    if name == "gmail_send_reply":
        return "Reply sent"
    if name == "calendar_list":
        return f"{n} event{'s' if n != 1 else ''}" if n is not None else "Checked calendar"
    if name == "calendar_add":
        return "Event created"
    if name == "calendar_update":
        return "Event updated"
    if name == "tasks_list":
        return f"{n} task{'s' if n != 1 else ''}" if n is not None else "Read tasks"
    if name == "tasks_add":
        return "Task added"
    if name == "tasks_complete":
        return "Task completed"
    if name == "notes_list":
        return f"{n} note{'s' if n != 1 else ''}" if n is not None else "Read notes"
    if name == "notes_add":
        return "Note saved"
    if name == "notes_search":
        return f"{n} match{'es' if n != 1 else ''}" if n is not None else "Searched notes"
    return "Done"
