"""
Local tool logic for Theta's own memory: **Notes** and **Tasks**, stored as JSON
in ../data. (Email and Calendar are real Google integrations — see
integrations/google/.) The same functions back both the MCP servers and the
in-process fallback, and start empty on a fresh install.
"""

from __future__ import annotations

import json
import threading
from datetime import datetime
from pathlib import Path
from typing import Any

DATA_DIR = Path(__file__).resolve().parent.parent / "data"

# Guards the read-modify-write cycles. MCP servers run in their own processes,
# so this is best-effort; for a single-user app it is more than enough.
_LOCK = threading.Lock()


def _load(name: str, default: Any) -> Any:
    """Load JSON from ../data/<name>, returning `default` if it's missing or
    unreadable (keeps a fresh install working with no seed files)."""
    path = DATA_DIR / name
    try:
        with open(path, "r", encoding="utf-8") as fh:
            return json.load(fh)
    except (FileNotFoundError, json.JSONDecodeError):
        return default


def _save(name: str, data: Any) -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
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
# Tasks (local to-do list)                                                    #
# --------------------------------------------------------------------------- #
def tasks_list(include_done: bool = False) -> list[dict]:
    """List to-do tasks. By default only open (not-done) tasks are returned."""
    tasks = _load("tasks.json", {"tasks": []}).get("tasks", [])
    if not include_done:
        tasks = [t for t in tasks if not t.get("done")]
    return sorted(tasks, key=lambda t: t.get("due", ""))


def tasks_add(title: str, due: str = "", priority: str = "medium") -> dict:
    """Add a new to-do task. `due` is YYYY-MM-DD; priority is low/medium/high."""
    with _LOCK:
        data = _load("tasks.json", {"tasks": []})
        tasks = data.setdefault("tasks", [])
        task = {
            "id": _next_id(tasks, "t"),
            "title": title,
            "due": due,
            "done": False,
            "priority": priority,
        }
        tasks.append(task)
        _save("tasks.json", data)
    return {"status": "created", "task": task}


def tasks_complete(task_id: str) -> dict:
    """Mark a task as done by its id."""
    with _LOCK:
        data = _load("tasks.json", {"tasks": []})
        for t in data.get("tasks", []):
            if t["id"] == task_id:
                t["done"] = True
                _save("tasks.json", data)
                return {"status": "completed", "task": t}
    return {"error": f"No task with id '{task_id}'."}


# --------------------------------------------------------------------------- #
# Notes                                                                       #
# --------------------------------------------------------------------------- #
def notes_list() -> list[dict]:
    """List all saved notes (id, title, tags)."""
    notes = _load("notes.json", [])
    return [
        {"id": n["id"], "title": n["title"], "tags": n.get("tags", [])} for n in notes
    ]


def notes_add(title: str, content: str, tags: list[str] | None = None) -> dict:
    """Save a new note with a title, body content, and optional tags."""
    with _LOCK:
        notes = _load("notes.json", [])
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
    notes = _load("notes.json", [])
    return [
        n
        for n in notes
        if q in n["title"].lower()
        or q in n.get("content", "").lower()
        or any(q in tag.lower() for tag in n.get("tags", []))
    ]
