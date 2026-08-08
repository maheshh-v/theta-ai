"""
Declarative specs for the MCP-backed tools — the in-process fallback used when
the stdio transport is unavailable (a locked-down host, a sandbox that cannot
spawn subprocesses). In the normal path the agent discovers these live over MCP.

Each spec mirrors the corresponding `@mcp.tool()` in `tools/servers/*.py`, and
the list doubles as a readable catalogue of everything Theta can do.

`shot_path`, `search_provider` and `search_api_key` are reserved parameters
injected by the manager at call time and hidden from the model.
"""

from __future__ import annotations

from tools import brief_tools, browser_tools, google_tools, notion_tools, web_tools, workspace_tools

_SHOT = {"shot_path": {"type": "string", "default": ""}}
# Reserved: injected by the manager from the session's connected account.
_NOTION = {"notion_token": {"type": "string", "default": ""}}
_GOOGLE = {"access_token": {"type": "string", "default": ""}}

TOOL_SPECS: list[dict] = [
    # --- browser: the action surface ---
    {
        "name": "browser_navigate",
        "server": "browser",
        "fn": browser_tools.browser_navigate,
        "description": "Open a URL. Returns the page title and its numbered interactive elements.",
        "parameters": {"url": {"type": "string", "required": True}, **_SHOT},
    },
    {
        "name": "browser_snapshot",
        "server": "browser",
        "fn": browser_tools.browser_snapshot,
        "description": "Re-read the current page and list its interactive elements, without acting.",
        "parameters": dict(_SHOT),
    },
    {
        "name": "browser_click",
        "server": "browser",
        "fn": browser_tools.browser_click,
        "description": "Click an element by its ref number from the most recent observation.",
        "parameters": {"ref": {"type": "integer", "required": True}, **_SHOT},
    },
    {
        "name": "browser_type",
        "server": "browser",
        "fn": browser_tools.browser_type,
        "description": (
            "Type text into a field by ref. submit=true presses Enter afterwards. "
            "Never used for passwords or card numbers."
        ),
        "parameters": {
            "ref": {"type": "integer", "required": True},
            "text": {"type": "string", "required": True},
            "submit": {"type": "boolean", "default": False},
            **_SHOT,
        },
    },
    {
        "name": "browser_select",
        "server": "browser",
        "fn": browser_tools.browser_select,
        "description": "Choose an option in a dropdown by ref, using the option's visible label.",
        "parameters": {
            "ref": {"type": "integer", "required": True},
            "option": {"type": "string", "required": True},
            **_SHOT,
        },
    },
    {
        "name": "browser_scroll",
        "server": "browser",
        "fn": browser_tools.browser_scroll,
        "description": 'Scroll the page by whole screens. direction is "down" or "up".',
        "parameters": {
            "direction": {"type": "string", "default": "down"},
            "amount": {"type": "integer", "default": 1},
            **_SHOT,
        },
    },
    {
        "name": "browser_back",
        "server": "browser",
        "fn": browser_tools.browser_back,
        "description": "Go back to the previous page.",
        "parameters": dict(_SHOT),
    },
    {
        "name": "browser_wait_for",
        "server": "browser",
        "fn": browser_tools.browser_wait_for,
        "description": "Wait for some text to appear on the page before continuing.",
        "parameters": {
            "text": {"type": "string", "required": True},
            "timeout": {"type": "integer", "default": 10},
            **_SHOT,
        },
    },
    {
        "name": "browser_read",
        "server": "browser",
        "fn": browser_tools.browser_read,
        "description": "Read the visible text of the current page, to extract information from it.",
        "parameters": {"max_chars": {"type": "integer", "default": 12000}},
    },
    {
        "name": "browser_reset",
        "server": "browser",
        "fn": browser_tools.browser_reset,
        "description": "Close the browser and start a clean one, clearing cookies and any login.",
        "parameters": {},
    },
    {
        # Hidden from the model (see catalog.HIDDEN_TOOLS) — this is the Playbook
        # replay path, addressing elements by recorded selector instead of by ref.
        "name": "browser_step",
        "server": "browser",
        "fn": browser_tools.browser_step,
        "description": "Replay a recorded step by selector. Used by Playbook replay.",
        "parameters": {
            "action": {"type": "string", "required": True},
            "target": {"type": "string", "default": ""},
            "value": {"type": "string", "default": ""},
            "submit": {"type": "boolean", "default": False},
            **_SHOT,
        },
    },
    # --- workspace: where results are kept ---
    {
        "name": "file_write",
        "server": "workspace",
        "fn": workspace_tools.file_write,
        "description": "Save a text file (CSV, Markdown, JSON, txt) into Theta's workspace.",
        "parameters": {
            "name": {"type": "string", "required": True},
            "content": {"type": "string", "required": True},
            "append": {"type": "boolean", "default": False},
        },
    },
    {
        "name": "file_read",
        "server": "workspace",
        "fn": workspace_tools.file_read,
        "description": "Read a file back out of Theta's workspace.",
        "parameters": {
            "name": {"type": "string", "required": True},
            "max_chars": {"type": "integer", "default": 20000},
        },
    },
    {
        "name": "file_list",
        "server": "workspace",
        "fn": workspace_tools.file_list,
        "description": "List the files saved in Theta's workspace.",
        "parameters": {},
    },
    # --- web: supporting facts (optional capability) ---
    {
        "name": "web_search",
        "server": "web",
        "fn": web_tools.web_search,
        "description": (
            "Search the web for pages. Use this to find a URL when you don't know it; "
            "use browser_navigate to actually go there."
        ),
        "parameters": {
            "query": {"type": "string", "required": True},
            "max_results": {"type": "integer", "default": 5},
            "search_provider": {"type": "string", "default": ""},
            "search_api_key": {"type": "string", "default": ""},
        },
    },
    {
        "name": "web_read",
        "server": "web",
        "fn": web_tools.web_read,
        "description": (
            "Fetch one page's text without opening the browser. Faster than navigating "
            "when you only need to read something."
        ),
        "parameters": {
            "url": {"type": "string", "required": True},
            "search_provider": {"type": "string", "default": ""},
            "search_api_key": {"type": "string", "default": ""},
        },
    },
    # --- notion: pages and databases, as Markdown ---
    {
        "name": "notion_search",
        "server": "notion",
        "fn": notion_tools.notion_search,
        "description": (
            "Find Notion pages and databases by title. Leave query blank to list "
            'recently edited items. kind="page" or kind="database" narrows it.'
        ),
        "parameters": {
            "query": {"type": "string", "default": ""},
            "kind": {"type": "string", "default": ""},
            "limit": {"type": "integer", "default": 10},
            **_NOTION,
        },
    },
    {
        "name": "notion_read_page",
        "server": "notion",
        "fn": notion_tools.notion_read_page,
        "description": (
            "Read a Notion page: its full content as Markdown, plus its database "
            "properties. Accepts a page id or a Notion URL."
        ),
        "parameters": {"page_id": {"type": "string", "required": True}, **_NOTION},
    },
    {
        "name": "notion_read_database",
        "server": "notion",
        "fn": notion_tools.notion_read_database,
        "description": (
            "Read a Notion database: its property schema and its rows, with each "
            "row's values flattened to plain text."
        ),
        "parameters": {
            "database_id": {"type": "string", "required": True},
            "limit": {"type": "integer", "default": 25},
            **_NOTION,
        },
    },
    {
        "name": "notion_create_page",
        "server": "notion",
        "fn": notion_tools.notion_create_page,
        "description": (
            "Create a Notion page under a parent page or database. markdown is the "
            "page body. Returns the new page's URL and confirms it by reading it back."
        ),
        "parameters": {
            "parent_id": {"type": "string", "required": True},
            "title": {"type": "string", "required": True},
            "markdown": {"type": "string", "default": ""},
            **_NOTION,
        },
    },
    {
        "name": "notion_update_page",
        "server": "notion",
        "fn": notion_tools.notion_update_page,
        "description": (
            "Edit a Notion page's content. Prefer find/replace to change part of a "
            "page; content overwrites the whole page. Read the page first, then "
            "check the returned `verified` flag."
        ),
        "parameters": {
            "page_id": {"type": "string", "required": True},
            "content": {"type": "string", "default": ""},
            "find": {"type": "string", "default": ""},
            "replace": {"type": "string", "default": ""},
            "replace_all": {"type": "boolean", "default": False},
            **_NOTION,
        },
    },
    {
        "name": "notion_update_properties",
        "server": "notion",
        "fn": notion_tools.notion_update_properties,
        "description": (
            'Set database properties on a Notion page using plain values, e.g. '
            '{"Status": "Done", "Priority": 2}. Values are read back afterwards '
            "and reported in `verified`."
        ),
        "parameters": {
            "page_id": {"type": "string", "required": True},
            "properties": {"type": "object", "required": True},
            **_NOTION,
        },
    },
    # --- gmail: read, draft, and (with approval) send ---
    {
        "name": "gmail_search",
        "server": "gmail",
        "fn": google_tools.gmail_search,
        "description": (
            "Search email with Gmail query syntax ('from:priya', 'subject:invoice', "
            "'is:unread', 'newer_than:7d'). Blank searches the inbox. Returns ids, "
            "senders, subjects and snippets."
        ),
        "parameters": {
            "query": {"type": "string", "default": ""},
            "max_results": {"type": "integer", "default": 10},
            **_GOOGLE,
        },
    },
    {
        "name": "gmail_read",
        "server": "gmail",
        "fn": google_tools.gmail_read,
        "description": "Read one email in full — headers plus the plain-text body — by its id.",
        "parameters": {"message_id": {"type": "string", "required": True}, **_GOOGLE},
    },
    {
        "name": "gmail_thread",
        "server": "gmail",
        "fn": google_tools.gmail_thread,
        "description": (
            "Read a whole email conversation, oldest first. Use this to summarise a "
            "thread instead of reading each message separately."
        ),
        "parameters": {"thread_id": {"type": "string", "required": True}, **_GOOGLE},
    },
    {
        "name": "gmail_draft_reply",
        "server": "gmail",
        "fn": google_tools.gmail_draft_reply,
        "description": (
            "Draft a reply to an email. Saved to Drafts and NOT sent. Recipient, "
            "subject and threading come from the original message."
        ),
        "parameters": {
            "message_id": {"type": "string", "required": True},
            "body": {"type": "string", "required": True},
            **_GOOGLE,
        },
    },
    {
        "name": "gmail_send_reply",
        "server": "gmail",
        "fn": google_tools.gmail_send_reply,
        "description": (
            "Send a reply to an email. The run pauses so the user can read and edit "
            "the message before anything leaves the mailbox."
        ),
        "parameters": {
            "message_id": {"type": "string", "required": True},
            "body": {"type": "string", "required": True},
            **_GOOGLE,
        },
    },
    # --- briefs: past research ---
    {
        "name": "brief_list",
        "server": "briefs",
        "fn": brief_tools.brief_list,
        "description": "List saved research briefs, newest first. Pass a query to filter.",
        "parameters": {
            "query": {"type": "string", "default": ""},
            "limit": {"type": "integer", "default": 10},
        },
    },
    {
        "name": "brief_read",
        "server": "briefs",
        "fn": brief_tools.brief_read,
        "description": "Read a saved brief in full: summary, key findings, sections and sources.",
        "parameters": {"brief_id": {"type": "string", "required": True}},
    },
]


def spec_to_input_schema(spec: dict) -> dict:
    """Convert a spec's `parameters` into a JSON Schema object, matching the shape
    MCP returns from list_tools()."""
    props: dict = {}
    required: list[str] = []
    for name, info in spec["parameters"].items():
        entry = {"type": info.get("type", "string")}
        if "default" in info:
            entry["default"] = info["default"]
        props[name] = entry
        if info.get("required"):
            required.append(name)
    return {"type": "object", "properties": props, "required": required}
