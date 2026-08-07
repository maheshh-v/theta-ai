"""
Shared tool logic (the "business logic" behind every MCP tool).

Each MCP server (email/calendar/notes) is a thin wrapper that exposes these
functions over the Model Context Protocol. The same functions are also used as
an in-process fallback if the MCP subprocess transport is unavailable, so the
demo degrades gracefully instead of crashing.

All data is local JSON in ../data — no external accounts or auth required.
"""

from __future__ import annotations

import json
import threading
from datetime import datetime
from pathlib import Path
from typing import Any

DATA_DIR = Path(__file__).resolve().parent.parent / "data"

# One lock per process guards the read-modify-write cycles. MCP servers run in
# their own processes, so this is best-effort; for a single-user demo it is
# more than enough.
_LOCK = threading.Lock()


def _load(name: str) -> Any:
    path = DATA_DIR / name
    with open(path, "r", encoding="utf-8") as fh:
        return json.load(fh)


def _save(name: str, data: Any) -> None:
    path = DATA_DIR / name
    tmp = path.with_suffix(path.suffix + ".tmp")
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(data, fh, indent=2, ensure_ascii=False)
    tmp.replace(path)


def _next_id(items: list[dict], prefix: str) -> str:
    n = 1
    existing = {it.get("id") for it in items}
    while f"{prefix}{n}" in existing:
        n += 1
    return f"{prefix}{n}"


# --------------------------------------------------------------------------- #
# Email                                                                       #
# --------------------------------------------------------------------------- #
def email_list(unread_only: bool = False) -> list[dict]:
    """Return inbox emails (id, from, subject, date, unread)."""
    emails = _load("emails.json")
    if unread_only:
        emails = [e for e in emails if e.get("unread")]
    return [
        {
            "id": e["id"],
            "from": e["from"],
            "subject": e["subject"],
            "date": e["date"],
            "unread": e.get("unread", False),
        }
        for e in emails
    ]


def email_read(email_id: str) -> dict:
    """Return the full body of a single email by id."""
    emails = _load("emails.json")
    for e in emails:
        if e["id"] == email_id:
            return e
    return {"error": f"No email with id '{email_id}'."}


def email_search(query: str) -> list[dict]:
    """Search emails by sender, subject, or body text (case-insensitive)."""
    q = (query or "").lower()
    emails = _load("emails.json")
    hits = [
        e
        for e in emails
        if q in e["from"].lower()
        or q in e["subject"].lower()
        or q in e.get("body", "").lower()
    ]
    return [{"id": e["id"], "from": e["from"], "subject": e["subject"]} for e in hits]


def email_draft_reply(email_id: str, message: str) -> dict:
    """Draft (but do NOT send) a reply to an email. Saves to the drafts folder."""
    with _LOCK:
        emails = _load("emails.json")
        original = next((e for e in emails if e["id"] == email_id), None)
        if original is None:
            return {"error": f"No email with id '{email_id}'."}
        draft = {
            "id": _next_id(emails, "draft"),
            "to": original["from"],
            "subject": "Re: " + original["subject"],
            "body": message,
            "in_reply_to": email_id,
            "date": datetime.now().isoformat(timespec="seconds"),
            "folder": "drafts",
        }
        emails.append(draft)
        _save("emails.json", emails)
    return {
        "status": "drafted",
        "note": "Draft saved locally — not sent (demo is send-safe).",
        "draft": draft,
    }


# --------------------------------------------------------------------------- #
# Calendar + Tasks                                                            #
# --------------------------------------------------------------------------- #
def calendar_list_events(date: str = "") -> list[dict]:
    """List calendar events, optionally filtered to a single YYYY-MM-DD date."""
    cal = _load("calendar.json")
    events = cal.get("events", [])
    if date:
        events = [e for e in events if e.get("date") == date]
    return sorted(events, key=lambda e: (e.get("date", ""), e.get("time", "")))


def calendar_add_event(
    title: str, date: str, time: str = "", location: str = "", notes: str = ""
) -> dict:
    """Add a new calendar event. `date` is YYYY-MM-DD, `time` is HH:MM."""
    with _LOCK:
        cal = _load("calendar.json")
        event = {
            "id": _next_id(cal.get("events", []), "ev"),
            "title": title,
            "date": date,
            "time": time,
            "duration_min": 60,
            "location": location,
            "notes": notes,
        }
        cal.setdefault("events", []).append(event)
        _save("calendar.json", cal)
    return {"status": "created", "event": event}


def tasks_list(include_done: bool = False) -> list[dict]:
    """List to-do tasks. By default only open (not-done) tasks are returned."""
    cal = _load("calendar.json")
    tasks = cal.get("tasks", [])
    if not include_done:
        tasks = [t for t in tasks if not t.get("done")]
    return sorted(tasks, key=lambda t: t.get("due", ""))


def tasks_add(title: str, due: str = "", priority: str = "medium") -> dict:
    """Add a new to-do task. `due` is YYYY-MM-DD; priority is low/medium/high."""
    with _LOCK:
        cal = _load("calendar.json")
        task = {
            "id": _next_id(cal.get("tasks", []), "t"),
            "title": title,
            "due": due,
            "done": False,
            "priority": priority,
        }
        cal.setdefault("tasks", []).append(task)
        _save("calendar.json", cal)
    return {"status": "created", "task": task}


def tasks_complete(task_id: str) -> dict:
    """Mark a task as done by its id."""
    with _LOCK:
        cal = _load("calendar.json")
        for t in cal.get("tasks", []):
            if t["id"] == task_id:
                t["done"] = True
                _save("calendar.json", cal)
                return {"status": "completed", "task": t}
    return {"error": f"No task with id '{task_id}'."}


# --------------------------------------------------------------------------- #
# Notes                                                                       #
# --------------------------------------------------------------------------- #
def notes_list() -> list[dict]:
    """List all saved notes (id, title, tags)."""
    notes = _load("notes.json")
    return [
        {"id": n["id"], "title": n["title"], "tags": n.get("tags", [])} for n in notes
    ]


def notes_add(title: str, content: str, tags: list[str] | None = None) -> dict:
    """Save a new note with a title, body content, and optional tags."""
    with _LOCK:
        notes = _load("notes.json")
        note = {
            "id": _next_id(notes, "n"),
            "title": title,
            "content": content,
            "tags": tags or [],
            "created": datetime.now().isoformat(timespec="seconds"),
        }
        notes.append(note)
        _save("notes.json", notes)
    return {"status": "saved", "note": note}


def notes_search(query: str) -> list[dict]:
    """Search notes by title, content, or tag (case-insensitive)."""
    q = (query or "").lower()
    notes = _load("notes.json")
    hits = [
        n
        for n in notes
        if q in n["title"].lower()
        or q in n.get("content", "").lower()
        or any(q in tag.lower() for tag in n.get("tags", []))
    ]
    return hits
