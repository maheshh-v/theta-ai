"""
Declarative specs for every tool — the in-process fallback used when the MCP
stdio transport is unavailable (locked-down host, sandbox). In the normal path
the agent discovers tools live over MCP; this list is the safety net so the app
still works, and doubles as a single readable catalogue of what Theta can do.

`access_token` on the Gmail/Calendar tools is a reserved parameter injected by
the manager at call time — it is hidden from the LLM (see tools/catalog.py).
"""

from __future__ import annotations

from tools import backends, google_tools

# Each spec mirrors the corresponding @mcp.tool() in tools/servers/*.py.
TOOL_SPECS: list[dict] = [
    # --- gmail (real, needs Google auth) ---
    {
        "name": "gmail_list",
        "server": "gmail",
        "fn": google_tools.gmail_list,
        "description": "List recent inbox emails. Set unread_only=true for only unread.",
        "parameters": {
            "access_token": {"type": "string", "required": True},
            "unread_only": {"type": "boolean", "default": False},
        },
    },
    {
        "name": "gmail_search",
        "server": "gmail",
        "fn": google_tools.gmail_search,
        "description": "Search email with Gmail query syntax (from:, subject:, keywords).",
        "parameters": {
            "access_token": {"type": "string", "required": True},
            "query": {"type": "string", "required": True},
        },
    },
    {
        "name": "gmail_read",
        "server": "gmail",
        "fn": google_tools.gmail_read,
        "description": "Read one email in full (headers + plain-text body) by its id.",
        "parameters": {
            "access_token": {"type": "string", "required": True},
            "message_id": {"type": "string", "required": True},
        },
    },
    {
        "name": "gmail_draft_reply",
        "server": "gmail",
        "fn": google_tools.gmail_draft_reply,
        "description": "Draft a reply to an email id (saved to Drafts, NOT sent).",
        "parameters": {
            "access_token": {"type": "string", "required": True},
            "message_id": {"type": "string", "required": True},
            "body": {"type": "string", "required": True},
        },
    },
    {
        "name": "gmail_send_reply",
        "server": "gmail",
        "fn": google_tools.gmail_send_reply,
        "description": "Send a reply to an email id. Requires explicit user approval.",
        "parameters": {
            "access_token": {"type": "string", "required": True},
            "message_id": {"type": "string", "required": True},
            "body": {"type": "string", "required": True},
        },
    },
    # --- calendar (real, needs Google auth) ---
    {
        "name": "calendar_list",
        "server": "calendar",
        "fn": google_tools.calendar_list,
        "description": "List upcoming events, or all events on one YYYY-MM-DD date.",
        "parameters": {
            "access_token": {"type": "string", "required": True},
            "date": {"type": "string", "default": ""},
        },
    },
    {
        "name": "calendar_add",
        "server": "calendar",
        "fn": google_tools.calendar_add,
        "description": "Add a calendar event. date=YYYY-MM-DD, time=HH:MM (24h). Approval-gated.",
        "parameters": {
            "access_token": {"type": "string", "required": True},
            "title": {"type": "string", "required": True},
            "date": {"type": "string", "required": True},
            "time": {"type": "string", "default": ""},
            "duration_min": {"type": "integer", "default": 60},
            "location": {"type": "string", "default": ""},
            "description": {"type": "string", "default": ""},
        },
    },
    {
        "name": "calendar_update",
        "server": "calendar",
        "fn": google_tools.calendar_update,
        "description": "Update an existing event by id (only provided fields change). Approval-gated.",
        "parameters": {
            "access_token": {"type": "string", "required": True},
            "event_id": {"type": "string", "required": True},
            "title": {"type": "string", "default": ""},
            "date": {"type": "string", "default": ""},
            "time": {"type": "string", "default": ""},
            "location": {"type": "string", "default": ""},
            "description": {"type": "string", "default": ""},
        },
    },
    # --- tasks (local) ---
    {
        "name": "tasks_list",
        "server": "tasks",
        "fn": backends.tasks_list,
        "description": "List to-do tasks. Set include_done=true to also show completed tasks.",
        "parameters": {"include_done": {"type": "boolean", "default": False}},
    },
    {
        "name": "tasks_add",
        "server": "tasks",
        "fn": backends.tasks_add,
        "description": "Add a to-do task. due=YYYY-MM-DD; priority is low/medium/high.",
        "parameters": {
            "title": {"type": "string", "required": True},
            "due": {"type": "string", "default": ""},
            "priority": {"type": "string", "default": "medium"},
        },
    },
    {
        "name": "tasks_complete",
        "server": "tasks",
        "fn": backends.tasks_complete,
        "description": "Mark a to-do task as done by its id (e.g. 't1').",
        "parameters": {"task_id": {"type": "string", "required": True}},
    },
    # --- notes (local) ---
    {
        "name": "notes_list",
        "server": "notes",
        "fn": backends.notes_list,
        "description": "List all saved notes with their id, title, and tags.",
        "parameters": {},
    },
    {
        "name": "notes_add",
        "server": "notes",
        "fn": backends.notes_add,
        "description": "Save a new note with a title, body content, and optional tags.",
        "parameters": {
            "title": {"type": "string", "required": True},
            "content": {"type": "string", "required": True},
            "tags": {"type": "array", "default": []},
        },
    },
    {
        "name": "notes_search",
        "server": "notes",
        "fn": backends.notes_search,
        "description": "Search saved notes by title, content, or tag.",
        "parameters": {"query": {"type": "string", "required": True}},
    },
]


def spec_to_input_schema(spec: dict) -> dict:
    """Convert a fallback spec's `parameters` into a JSON-Schema object, matching
    the shape MCP returns from list_tools()."""
    props = {}
    required = []
    for pname, pinfo in spec["parameters"].items():
        entry = {"type": pinfo.get("type", "string")}
        if "default" in pinfo:
            entry["default"] = pinfo["default"]
        props[pname] = entry
        if pinfo.get("required"):
            required.append(pname)
    return {"type": "object", "properties": props, "required": required}
