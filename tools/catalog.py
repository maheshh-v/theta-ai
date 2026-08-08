"""
Tool metadata and — more importantly — **when Theta must stop and ask**.

The approval gate here is dynamic, which is the whole point. A static list of
"dangerous tools" is useless for a browser agent: `browser_click` is harmless on
a search button and irreversible on "Place order". So the browser layer
classifies each *control* as it observes the page (`browser/guard.py`) and ships
the verdict back with the observation as `gates`. This module reads those gates
to decide whether the next proposed action needs a human.

Approval is reserved for actions that change the world — submitting, paying,
deleting, sending — plus deep research, which is gated because it is slow and
worth steering rather than because it is dangerous.
"""

from __future__ import annotations

SAFE = "safe"
CONFIRM = "confirm"

# Injected server-side and hidden from the model: the screenshot destination, the
# search credentials, and the tokens for connected accounts. The model neither
# sees nor chooses these, so no prompt can talk it into leaking one.
RESERVED_PARAMS = {
    "shot_path", "search_provider", "search_api_key", "notion_token", "access_token",
}

# Tools gated regardless of arguments. `gmail_send_reply` is here for the obvious
# reason: it is the only tool Theta has that puts words in the user's name in
# front of another person, and that is not undoable.
ALWAYS_CONFIRM = {"research", "gmail_send_reply"}

# Tools whose risk depends on which element they touch.
GATED_BY_ELEMENT = {"browser_click", "browser_type", "browser_select"}

SEARCH_SERVERS = {"web"}
BROWSER_SERVERS = {"browser"}

# Servers whose tools run against a connected account, and the reserved parameter
# carrying that account's credential.
CREDENTIAL_PARAMS = {"notion": "notion_token", "gmail": "access_token"}

# What to say when a tool is called for a service that has not been connected.
CONNECTION_HINTS = {
    "notion": "Notion isn't connected yet. Open Settings → Connections, paste a "
              "Notion integration token, and share the pages you want Theta to "
              "reach with that integration.",
    "gmail": "Gmail isn't connected yet. Open Settings → Connections and choose "
             "Connect Gmail — you'll sign in on Google's own page, and Theta "
             "never sees your password.",
}

# Tools that exist for Playbook replay, not for the agent to choose. They are
# still real MCP tools — they are simply left out of the list the model sees.
HIDDEN_TOOLS = {"browser_step"}

ACTION_LABELS = {
    "browser_navigate": "Opening page",
    "browser_snapshot": "Looking at the page",
    "browser_click": "Clicking",
    "browser_type": "Typing",
    "browser_select": "Choosing",
    "browser_scroll": "Scrolling",
    "browser_back": "Going back",
    "browser_wait_for": "Waiting",
    "browser_read": "Reading the page",
    "browser_reset": "Resetting the browser",
    "file_write": "Saving file",
    "file_read": "Reading file",
    "file_list": "Listing files",
    "web_search": "Searching the web",
    "web_read": "Reading a page",
    "research": "Researching",
    "brief_list": "Searching past research",
    "brief_read": "Opening a brief",
    "notion_search": "Searching Notion",
    "notion_read_page": "Reading a Notion page",
    "notion_read_database": "Reading a Notion database",
    "notion_create_page": "Creating a Notion page",
    "notion_update_page": "Editing a Notion page",
    "notion_update_properties": "Updating Notion properties",
    "gmail_search": "Searching Gmail",
    "gmail_read": "Opening an email",
    "gmail_thread": "Reading a thread",
    "gmail_draft_reply": "Drafting a reply",
    "gmail_send_reply": "Sending a reply",
}


def tag_for(name: str) -> str:
    """Static tag, used for display and for the tool list the model sees. Element
    gating is decided per call by `risk()`."""
    if name in ALWAYS_CONFIRM:
        return "confirm"
    if name in GATED_BY_ELEMENT:
        return "maybe"
    return "read"


def needs_search_config(server: str | None) -> bool:
    return server in SEARCH_SERVERS


def needs_screenshot(server: str | None) -> bool:
    return server in BROWSER_SERVERS


def credential_param(server: str | None) -> str:
    """The reserved parameter this server's tools need, if it needs one."""
    return CREDENTIAL_PARAMS.get(server or "", "")


def connection_hint(server: str | None) -> str:
    return CONNECTION_HINTS.get(server or "", "That service isn't connected yet.")


def risk(name: str, args: dict, gates: dict | None = None) -> tuple[str, str]:
    """Decide whether this specific call needs human approval.

    `gates` maps element ref -> why it is consequential, as reported by the most
    recent page observation.
    """
    args = args or {}
    if name in ALWAYS_CONFIRM:
        return CONFIRM, describe_action(name, args)

    if name in GATED_BY_ELEMENT:
        # Typing then pressing Enter submits a form even when the field itself
        # is innocuous, so it is gated on the action rather than the element.
        if name == "browser_type" and args.get("submit"):
            return CONFIRM, "Submit the form after typing"
        ref = str(args.get("ref", ""))
        reason = (gates or {}).get(ref)
        if reason:
            return CONFIRM, reason[:1].upper() + reason[1:]
    return SAFE, ""


def label_for(name: str) -> str:
    return ACTION_LABELS.get(name, name.replace("_", " ").capitalize())


def describe_action(name: str, args: dict) -> str:
    """Human-readable description of a pending action for the approval card."""
    a = args or {}
    if name == "research":
        plan = [str(q).strip() for q in (a.get("subquestions") or []) if str(q).strip()]
        lines = [f"Research: “{a.get('question', '')}”"]
        if plan:
            lines += ["", "Plan:"] + [f"{i}. {q}" for i, q in enumerate(plan, 1)]
        else:
            lines += ["", "Theta will plan the sub-questions itself."]
        return "\n".join(lines)
    if name == "gmail_send_reply":
        # The recipient, subject and thread are resolved from the message being
        # replied to, so the body is the part a human actually has to check —
        # and the part they can edit on the card before approving.
        return (
            "Send this reply from your Gmail account. The recipient, subject and "
            "thread come from the message being replied to.\n\n"
            f"{(a.get('body') or '').strip() or '(empty message)'}"
        )
    if name == "browser_click":
        return f"Click element [{a.get('ref')}] on the page."
    if name == "browser_type":
        return f'Type “{_trim(a.get("text", ""), 80)}” into element [{a.get("ref")}] and submit.'
    return f"Run {label_for(name)}."


def summarize(name: str, result) -> str:
    """Short status line for the execution trace."""
    if isinstance(result, dict):
        if result.get("error"):
            return f"⚠️ {_trim(result.get('message') or result.get('error'), 90)}"
        if name.startswith("notion_") or name.startswith("gmail_"):
            return _summarize_connected(name, result)
        if name.startswith("browser_"):
            message = result.get("message") or ""
            title = result.get("title") or ""
            if name == "browser_read":
                return f"Read {result.get('chars', 0):,} characters"
            if name in ("browser_navigate", "browser_back") and title:
                return f"{message} — “{_trim(title, 46)}”" if message else _trim(title, 60)
            return _trim(message, 70) or "Done"
        if name == "file_write":
            return f"Saved {result.get('path', '')} ({result.get('bytes', 0):,} bytes)"
        if name == "file_read":
            return f"Read {result.get('path', '')}"
        if name == "file_list":
            return _plural(result.get("count"), "file")
        if name == "research":
            title, cited = result.get("title"), result.get("sources_cited")
            if title:
                return f"“{_trim(title, 42)}” · {_plural(cited, 'source')}"
            return "Brief written"
        if name == "brief_read":
            return f"Opened “{_trim(result.get('title', ''), 44)}”"
        if name in ("web_search", "brief_list"):
            word = "result" if name == "web_search" else "brief"
            return _plural(result.get("count"), word)
        if name == "web_read":
            return f"Read “{_trim(result.get('title', ''), 44)}”"
    if isinstance(result, list):
        return _plural(len(result), "item")
    return "Done"


def _summarize_connected(name: str, result: dict) -> str:
    """Trace line for a Notion or Gmail step.

    Every write tool re-reads what it wrote and reports `verified`. That flag is
    the whole point, so it is the thing the trace shows: "unverified" has to be
    visible in the run, not buried in the observation JSON.
    """
    def verified(text: str) -> str:
        if "verified" not in result:
            return text
        return text if result.get("verified") else f"{text} — ⚠️ not verified"

    if name == "notion_search":
        return _plural(result.get("count"), "match")
    if name == "notion_read_page":
        return f"Read “{_trim(result.get('title', ''), 40)}” ({len(result.get('markdown', '')):,} chars)"
    if name == "notion_read_database":
        return f"“{_trim(result.get('title', ''), 30)}” · {_plural(result.get('count'), 'row')}"
    if name == "notion_create_page":
        return verified(f"Created “{_trim(result.get('title', ''), 40)}”")
    if name == "notion_update_page":
        return verified("Edited the page")
    if name == "notion_update_properties":
        return verified(f"Set {_trim(', '.join(result.get('properties') or {}), 46)}")
    if name == "gmail_search":
        return _plural(result.get("count"), "email")
    if name == "gmail_read":
        return f"“{_trim(result.get('subject', ''), 44)}” from {_trim(result.get('from', ''), 28)}"
    if name == "gmail_thread":
        return f"“{_trim(result.get('subject', ''), 38)}” · {_plural(result.get('count'), 'message')}"
    if name == "gmail_draft_reply":
        return verified(f"Drafted a reply to {_trim(result.get('to', ''), 34)}")
    if name == "gmail_send_reply":
        return verified(f"Sent to {_trim(result.get('to', ''), 38)}")
    return "Done"


def _plural(n, word: str) -> str:
    if n is None:
        return word.capitalize()
    return f"{n} {word}{'s' if n != 1 else ''}"


def _trim(text, n: int) -> str:
    text = str(text or "")
    return text if len(text) <= n else text[: n - 1] + "…"
