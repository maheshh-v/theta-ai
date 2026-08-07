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

# Injected server-side and hidden from the model: the screenshot destination and
# the search credentials. The model neither sees nor chooses these.
RESERVED_PARAMS = {"shot_path", "search_provider", "search_api_key"}

# Tools gated regardless of arguments.
ALWAYS_CONFIRM = {"research"}

# Tools whose risk depends on which element they touch.
GATED_BY_ELEMENT = {"browser_click", "browser_type", "browser_select"}

SEARCH_SERVERS = {"web"}
BROWSER_SERVERS = {"browser"}

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


def _plural(n, word: str) -> str:
    if n is None:
        return word.capitalize()
    return f"{n} {word}{'s' if n != 1 else ''}"


def _trim(text, n: int) -> str:
    text = str(text or "")
    return text if len(text) <= n else text[: n - 1] + "…"
