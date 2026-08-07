"""
Declarative specs for every tool, used as the in-process fallback if the MCP
stdio transport is unavailable (e.g. a locked-down host). In the normal path
the agent discovers tools live over MCP; this list is the safety net so the
demo always runs.

Keeping the fallback here also gives a single, readable catalogue of what the
assistant can do — handy when documenting "how to add a tool".
"""

from __future__ import annotations

from tools import backends

# Each spec mirrors the corresponding @mcp.tool() in tools/servers/*.py.
# `parameters` uses JSON-Schema-style entries so the fallback presents tools to
# the LLM in exactly the same shape as MCP's discovered inputSchema.
TOOL_SPECS: list[dict] = [
    # --- email ---
    {
        "name": "email_list",
        "server": "email",
        "fn": backends.email_list,
        "description": "List inbox emails. Set unread_only=true for only unread messages.",
        "parameters": {"unread_only": {"type": "boolean", "default": False}},
    },
    {
        "name": "email_read",
        "server": "email",
        "fn": backends.email_read,
        "description": "Read the full contents of a single email by its id (e.g. 'e1').",
        "parameters": {"email_id": {"type": "string", "required": True}},
    },
    {
        "name": "email_search",
        "server": "email",
        "fn": backends.email_search,
        "description": "Search emails by sender, subject, or body text.",
        "parameters": {"query": {"type": "string", "required": True}},
    },
    {
        "name": "email_draft_reply",
        "server": "email",
        "fn": backends.email_draft_reply,
        "description": "Draft a reply to an email (saved locally, NOT sent).",
        "parameters": {
            "email_id": {"type": "string", "required": True},
            "message": {"type": "string", "required": True},
        },
    },
    # --- calendar + tasks ---
    {
        "name": "calendar_list_events",
        "server": "calendar",
        "fn": backends.calendar_list_events,
        "description": "List calendar events. Optionally filter to one date (YYYY-MM-DD).",
        "parameters": {"date": {"type": "string", "default": ""}},
    },
    {
        "name": "calendar_add_event",
        "server": "calendar",
        "fn": backends.calendar_add_event,
        "description": "Add a calendar event. date=YYYY-MM-DD, time=HH:MM (24h).",
        "parameters": {
            "title": {"type": "string", "required": True},
            "date": {"type": "string", "required": True},
            "time": {"type": "string", "default": ""},
            "location": {"type": "string", "default": ""},
            "notes": {"type": "string", "default": ""},
        },
    },
    {
        "name": "tasks_list",
        "server": "calendar",
        "fn": backends.tasks_list,
        "description": "List to-do tasks. Set include_done=true to also show completed tasks.",
        "parameters": {"include_done": {"type": "boolean", "default": False}},
    },
    {
        "name": "tasks_add",
        "server": "calendar",
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
        "server": "calendar",
        "fn": backends.tasks_complete,
        "description": "Mark a to-do task as done by its id (e.g. 't1').",
        "parameters": {"task_id": {"type": "string", "required": True}},
    },
    # --- notes ---
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
    """Convert a fallback spec's `parameters` into a JSON-Schema object,
    matching the shape MCP returns from list_tools()."""
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
