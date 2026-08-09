"""
What Theta can do — described once, in one place.

The UI has to answer "what is this thing actually capable of?" in about ten
seconds, and it has to answer it *honestly*: a capability that needs a Notion
token must say so, and must say what connecting it would unlock. The only way
the description and the connection state stay in step is to keep them together,
so this module is the single source of truth for three surfaces at once —
the home screen's capability strip, the Connections page, and the example
prompts a new user starts from.

Two kinds of capability:

* **connection** — reaches a service through its own API and needs an account
  (Notion, Gmail). These have a real state machine: unavailable → connect →
  ready, and the UI must handle all three.
* **builtin** — always there because it ships with Theta (the browser, the
  workspace, web search, Playbooks, Schedules).

`enables` is deliberately written in plain user language rather than tool names.
The tool names live in `tools`, shown only when someone opens the details — the
product should be legible without them, not defined by them.
"""

from __future__ import annotations

from server import accounts
from server.session import Session

# State values. The UI keys its styling off these, so they are part of the API.
READY = "ready"              # usable right now
CONNECT = "connect"          # the user can connect it themselves
UNAVAILABLE = "unavailable"  # the deployment has not configured it


def _browser() -> dict:
    return {
        "key": "browser",
        "name": "Browser",
        "kind": "builtin",
        "tagline": "Operates real websites for you",
        "enables": [
            "Open any site and work through it click by click",
            "Fill in forms, search, filter and page through results",
            "Pull data off a page into a file you can download",
            "Stop and ask you first before anything irreversible",
        ],
        "safety": (
            "Theta never types passwords, card numbers or one-time codes, and "
            "will not touch a CAPTCHA. When a task needs one, it hands back to you."
        ),
        "examples": [
            {
                "title": "Collect data into a file",
                "prompt": "Go to https://quotes.toscrape.com and collect the first 10 "
                          "quotes with their authors and tags. Save it as quotes.csv",
            },
            {
                "title": "Search and filter a site",
                "prompt": "On https://quotes.toscrape.com/search.aspx, find quotes by "
                          "Albert Einstein tagged 'change' and tell me what they are",
            },
        ],
    }


def _files() -> dict:
    return {
        "key": "files",
        "name": "Files",
        "kind": "builtin",
        "tagline": "Saves results you can download",
        "enables": [
            "Write CSV, Markdown, JSON or plain text",
            "Read a file back to carry it into the next step",
            "Keep every output in one workspace, nowhere else on your disk",
        ],
        "safety": "Theta can only write inside its own workspace folder.",
        "examples": [
            {
                "title": "Save a summary",
                "prompt": "Read https://news.ycombinator.com and save the top 5 stories "
                          "with their points as hn.md",
            },
        ],
    }


def _web() -> dict:
    return {
        "key": "web",
        "name": "Web research",
        "kind": "builtin",
        "tagline": "Finds and reads pages without opening the browser",
        "enables": [
            "Search the web when you don't give Theta a URL",
            "Read a single page's text quickly, without a full browser session",
            "Run deeper multi-source research when you ask for it",
        ],
        "safety": "Deep research pauses so you can edit the plan before it runs.",
        "examples": [
            {
                "title": "Find and read",
                "prompt": "Find the official Playwright docs page about locators and "
                          "summarise how text= and role= differ",
            },
        ],
    }


def _notion(session: Session) -> dict:
    status = accounts.status(session)["notion"]
    connected = bool(status.get("connected"))
    return {
        "key": "notion",
        "name": "Notion",
        "kind": "connection",
        "tagline": "Reads and writes your Notion workspace",
        "state": READY if connected else CONNECT,
        # The phrasing a connected service gets in the UI. "Connected" alone
        # tells a user nothing about what just became possible.
        "summary": (
            "With Notion connected, Theta can search your workspace, read pages "
            "and databases, create new pages and update existing content."
        ),
        "enables": [
            "Search your workspace by title",
            "Read a page as Markdown, or a database with all its rows",
            "Create a new page under any page or database",
            "Update page content and database properties",
        ],
        "safety": (
            "Theta only sees pages you have explicitly shared with the integration. "
            "Every write is read back afterwards and reported as verified or not."
        ),
        "account": status.get("token_masked", "") if connected else "",
        "account_source": status.get("token_source", ""),
        "setup": (
            "Create an internal integration at notion.so/my-integrations, paste its "
            "secret here, then open each page or database you want Theta to reach "
            "and use ⋯ → Connections to share it."
        ),
        "action": "notion_token",
        "examples": [
            {
                "title": "Find something in Notion",
                "prompt": "Search my Notion for a page about onboarding and summarise it",
            },
            {
                "title": "Write results into Notion",
                "prompt": "Read https://news.ycombinator.com, then create a Notion page "
                          "called 'HN today' under my notes page with the top 5 stories",
            },
        ],
    }


def _gmail(session: Session) -> dict:
    status = accounts.status(session)["google"]
    configured = bool(status.get("configured"))
    connected = bool(status.get("connected"))
    state = READY if connected else (CONNECT if configured else UNAVAILABLE)
    return {
        "key": "gmail",
        "name": "Gmail",
        "kind": "connection",
        "tagline": "Reads your mail and drafts replies",
        "state": state,
        "summary": (
            "With Gmail connected, Theta can search your emails, read whole threads, "
            "draft replies, and send a reply once you have read and approved it."
        ),
        "enables": [
            "Search with real Gmail syntax — from:, subject:, is:unread, newer_than:7d",
            "Read one message in full, or a whole conversation in order",
            "Draft a reply straight into your Drafts folder",
            "Send a reply — but only after you approve the exact wording",
        ],
        "safety": (
            "Sending always stops for your approval, and you can rewrite the message "
            "on the approval card first. Theta can never delete mail, and a saved "
            "automation is not allowed to send at all."
        ),
        "account": status.get("email") or status.get("name") or "",
        "capabilities": status.get("capabilities", {}),
        "setup": (
            "Gmail needs a Google OAuth client, which belongs to this deployment "
            "rather than to you. Set GOOGLE_CLIENT_ID and GOOGLE_CLIENT_SECRET, "
            "then restart Theta — see .env.example."
            if not configured else
            "You sign in on Google's own page. Theta asks only to read mail, save "
            "drafts and send — never to delete or modify anything — and never sees "
            "your password."
        ),
        "action": "connect_google" if configured else "configure_google",
        "examples": [
            {
                "title": "Catch up on mail",
                "prompt": "Summarise my unread emails from the last 3 days, grouped by "
                          "who they're from and what they need from me",
            },
            {
                "title": "Draft a reply",
                "prompt": "Find the most recent email asking me for a meeting and draft "
                          "a polite reply proposing Thursday afternoon",
            },
        ],
    }


def _playbooks() -> dict:
    from automation.playbooks import playbooks as store

    count = len(store.list())
    return {
        "key": "playbooks",
        "name": "Playbooks",
        "kind": "builtin",
        "tagline": "Turns a run that worked into a one-click automation",
        "count": count,
        "enables": [
            "Save any successful task as a repeatable automation",
            "Re-run it with zero model calls, in a fraction of the time",
            "Change the inputs each run without redoing the work",
            "Repair itself when a site changes, and remember the fix",
        ],
        "safety": (
            "Actions that need your approval are never recorded into a Playbook, so "
            "replaying one can never send an email on your behalf."
        ),
        "examples": [],
    }


def _schedules() -> dict:
    from automation.schedules import schedules as store

    items = store.list()
    return {
        "key": "schedules",
        "name": "Schedules",
        "kind": "builtin",
        "tagline": "Runs your automations on their own, on a timetable",
        "count": len([s for s in items if s.enabled]),
        "enables": [
            "Run any Playbook hourly, daily, on weekdays or weekly",
            "Costs nothing to repeat — scheduled runs make no model calls",
            "Every run lands in Activity with its full trace",
            "Pause or change the timetable whenever you like",
        ],
        "safety": (
            "A schedule only replays steps you already watched and approved. It "
            "cannot make new decisions, and it can never send an email."
        ),
        "examples": [],
    }


def all_capabilities(session: Session) -> list[dict]:
    """Every capability with its live state, ordered as the UI shows them."""
    items = [
        _browser(),
        _gmail(session),
        _notion(session),
        _files(),
        _web(),
        _playbooks(),
        _schedules(),
    ]
    for item in items:
        item.setdefault("state", READY)
        item.setdefault("summary", item["tagline"])
        item.setdefault("account", "")
        item.setdefault("setup", "")
        item.setdefault("action", "")
        item.setdefault("examples", [])
        item["tools"] = _tools_for(item["key"])
    return items


def connections(session: Session) -> list[dict]:
    """Just the capabilities backed by an account the user connects."""
    return [c for c in all_capabilities(session) if c["kind"] == "connection"]


def starters(session: Session, limit: int = 6) -> list[dict]:
    """Example prompts for the home screen.

    Two rules, both about what a new user should conclude in ten seconds:

    * Runnable suggestions come first. One that fails because nothing is
      connected is worse than no suggestion at all.
    * Capabilities take turns rather than one filling the list. Notion's example
      is what tells someone Notion exists, so it has to survive the cut.
    """
    def queue(capability_filter) -> list[list[dict]]:
        out = []
        for cap in all_capabilities(session):
            if not capability_filter(cap):
                continue
            out.append([
                {**example, "capability": cap["key"],
                 "capability_name": cap["name"], "state": cap["state"]}
                for example in cap.get("examples", [])
            ])
        return out

    picked: list[dict] = []
    for group in (queue(lambda c: c["state"] == READY),
                  queue(lambda c: c["state"] != READY)):
        # Round-robin: one example from each capability, then a second, ...
        for row in range(max((len(g) for g in group), default=0)):
            for examples in group:
                if row < len(examples):
                    picked.append(examples[row])
    return picked[:limit]


# Which server's tools back each capability. Derived from the live tool list so
# a capability can never advertise a tool that is not actually loaded.
_SERVERS = {
    "browser": {"browser"},
    "gmail": {"gmail"},
    "notion": {"notion"},
    "files": {"workspace"},
    "web": {"web", "briefs"},
}


def _tools_for(key: str) -> list[str]:
    from tools import catalog, tool_specs

    servers = _SERVERS.get(key)
    if not servers:
        return []
    return sorted(
        spec["name"]
        for spec in tool_specs.TOOL_SPECS
        if spec["server"] in servers and spec["name"] not in catalog.HIDDEN_TOOLS
    )


def model_state(session: Session) -> dict:
    """The one prerequisite everything else depends on."""
    from server import preferences

    config = preferences.public_config(session)
    return {
        "ready": config["model_ready"],
        "label": config["active_label"],
        "error": config["model_error"],
        "provider": config["provider"],
    }


__all__ = [
    "READY", "CONNECT", "UNAVAILABLE",
    "all_capabilities", "connections", "starters", "model_state",
]
