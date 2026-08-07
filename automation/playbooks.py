"""
Playbooks: a successful run, distilled into something you can run again.

The idea this project is built around is that **figuring out a task and doing a
task are different jobs, and only the first one needs a language model.** The
first time, Theta reasons its way through a site step by step. That is slow and
costs tokens. But once it works, the sequence of actions is just… a sequence of
actions.

So a Playbook stores each step addressed by *durable* selectors (`#search`,
`[data-testid=submit]`, role+name) rather than the throwaway element numbers a
live run uses, and lifts the values you typed out into named inputs. Replaying it
costs **zero model calls**. When a step stops resolving because the site changed,
that one step escalates to the model, and the repair is written back — so the
playbook heals instead of rotting.
"""

from __future__ import annotations

import json
import re
import threading
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from secrets import token_urlsafe

from config import settings

_ID_RE = re.compile(r"[A-Za-z0-9_\-]{1,64}")
PARAM_RE = re.compile(r"\{\{\s*([a-zA-Z0-9_]+)\s*\}\}")

# Browser tools that become replayable playbook steps, and their parameter shape.
REPLAYABLE = {
    "browser_navigate": "navigate",
    "browser_click": "click",
    "browser_type": "type",
    "browser_select": "select",
    "browser_scroll": "scroll",
    "browser_read": "read",
    "browser_wait_for": "wait_for",
    "browser_back": "back",
    "file_write": "file_write",
}


@dataclass
class Param:
    """An input the user supplies at run time."""

    name: str
    label: str = ""
    default: str = ""
    example: str = ""

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class PlaybookStep:
    action: str                                   # navigate | click | type | ...
    value: str = ""                               # may contain {{param}}
    submit: bool = False
    target: dict = field(default_factory=dict)    # name/role/tag/selectors
    note: str = ""                                # what this step is for

    def describe(self) -> str:
        what = self.target.get("describe") or self.target.get("name") or ""
        if self.action == "navigate":
            return f"Open {self.value}"
        if self.action == "type":
            return f'Type "{self.value}" into {what}' + (" and submit" if self.submit else "")
        if self.action == "select":
            return f'Choose "{self.value}" in {what}'
        if self.action == "click":
            return f"Click {what}"
        if self.action == "file_write":
            return f"Save {self.target.get('name', 'a file')}"
        if self.action == "wait_for":
            return f'Wait for "{self.value}"'
        return self.action.replace("_", " ").capitalize()

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class Playbook:
    id: str
    name: str
    goal: str = ""                                 # the original request
    description: str = ""
    created: str = ""
    source_run: str = ""
    params: list[Param] = field(default_factory=list)
    steps: list[PlaybookStep] = field(default_factory=list)
    run_count: int = 0
    last_run: str = ""
    last_status: str = ""

    @staticmethod
    def new_id() -> str:
        return datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S-") + token_urlsafe(3)

    def to_dict(self) -> dict:
        data = asdict(self)
        data["params"] = [p.to_dict() for p in self.params]
        data["steps"] = [s.to_dict() for s in self.steps]
        return data

    @classmethod
    def from_dict(cls, data: dict) -> "Playbook":
        data = dict(data)
        data["params"] = [Param(**p) for p in data.get("params", [])]
        data["steps"] = [PlaybookStep(**s) for s in data.get("steps", [])]
        known = set(cls.__dataclass_fields__)
        return cls(**{k: v for k, v in data.items() if k in known})

    def summary_dict(self) -> dict:
        return {
            "id": self.id,
            "name": self.name,
            "goal": self.goal,
            "description": self.description,
            "created": self.created,
            "steps": len(self.steps),
            "params": [p.to_dict() for p in self.params],
            "run_count": self.run_count,
            "last_run": self.last_run,
            "last_status": self.last_status,
        }

    def resolve(self, values: dict) -> list[PlaybookStep]:
        """Substitute the user's inputs into every step's value."""
        supplied = {p.name: str(values.get(p.name, p.default) or "") for p in self.params}

        def fill(text: str) -> str:
            return PARAM_RE.sub(lambda m: supplied.get(m.group(1), m.group(0)), text or "")

        out = []
        for step in self.steps:
            filled = PlaybookStep(**step.to_dict())
            filled.value = fill(step.value)
            if filled.action == "file_write":
                filled.target = dict(step.target)
                filled.target["name"] = fill(str(step.target.get("name", "")))
            out.append(filled)
        return out


# --------------------------------------------------------------------------- #
# Recording                                                                   #
# --------------------------------------------------------------------------- #
def from_run(run, name: str = "", description: str = "") -> Playbook:
    """Distil a completed run into a replayable Playbook.

    Only the steps that actually changed something are kept: reads and snapshots
    were the agent working out what to do, and replaying them would just slow the
    automation down without affecting the outcome.
    """
    steps: list[PlaybookStep] = []
    params: list[Param] = []
    seen_params: set[str] = set()

    for rs in run.steps:
        action = REPLAYABLE.get(rs.tool)
        if action is None or rs.status != "done" or not rs.ok:
            continue
        args = dict(rs.args or {})

        if action == "navigate":
            steps.append(PlaybookStep(action="navigate", value=str(args.get("url", "")),
                                      note="Open the starting page"))
        elif action == "type":
            text = str(args.get("text", ""))
            pname = _param_name(rs.target, len(params) + 1, seen_params)
            params.append(Param(
                name=pname,
                label=(rs.target.get("name") or "Text to type").strip()[:60],
                default=text, example=text,
            ))
            steps.append(PlaybookStep(
                action="type", value="{{%s}}" % pname, submit=bool(args.get("submit")),
                target=rs.target or {}, note=f"Fill {rs.target.get('name', 'the field')}",
            ))
        elif action == "select":
            steps.append(PlaybookStep(action="select", value=str(args.get("option", "")),
                                      target=rs.target or {},
                                      note=f"Choose in {rs.target.get('name', 'the dropdown')}"))
        elif action == "click":
            steps.append(PlaybookStep(action="click", target=rs.target or {},
                                      note=f"Click {rs.target.get('name', 'the control')}"))
        elif action == "scroll":
            steps.append(PlaybookStep(action="scroll", value=str(args.get("direction", "down"))))
        elif action == "wait_for":
            steps.append(PlaybookStep(action="wait_for", value=str(args.get("text", ""))))
        elif action == "back":
            steps.append(PlaybookStep(action="back"))
        elif action == "read":
            steps.append(PlaybookStep(action="read", note="Read the page for the result"))
        elif action == "file_write":
            steps.append(PlaybookStep(
                action="file_write", value=str(args.get("content", "")),
                target={"name": str(args.get("name", "output.txt"))},
                note="Save the result",
            ))

    return Playbook(
        id=Playbook.new_id(),
        name=(name or _default_name(run.goal))[:120],
        goal=run.goal,
        description=description,
        created=datetime.now(timezone.utc).isoformat(timespec="seconds"),
        source_run=run.id,
        params=params,
        steps=steps,
    )


def _param_name(target: dict, n: int, seen: set) -> str:
    base = re.sub(r"[^a-z0-9]+", "_", str(target.get("name", "")).lower()).strip("_")
    base = (base or f"input_{n}")[:28]
    name = base
    i = 2
    while name in seen:
        name = f"{base}_{i}"
        i += 1
    seen.add(name)
    return name


def _default_name(goal: str) -> str:
    text = " ".join((goal or "Automation").split())
    return text[:60] + ("…" if len(text) > 60 else "")


# --------------------------------------------------------------------------- #
# Storage                                                                     #
# --------------------------------------------------------------------------- #
class PlaybookStore:
    def __init__(self, directory=None) -> None:
        self._dir = directory
        self._lock = threading.Lock()

    @property
    def dir(self):
        return self._dir or settings.playbooks_dir

    def _path(self, pid: str):
        if not _ID_RE.fullmatch(pid or ""):
            raise ValueError(f"Invalid playbook id: {pid!r}")
        return self.dir / f"{pid}.json"

    def save(self, pb: Playbook) -> Playbook:
        with self._lock:
            self.dir.mkdir(parents=True, exist_ok=True)
            path = self._path(pb.id)
            tmp = path.with_suffix(".json.tmp")
            tmp.write_text(json.dumps(pb.to_dict(), ensure_ascii=False, indent=2),
                           encoding="utf-8")
            tmp.replace(path)
        return pb

    def get(self, pid: str) -> Playbook | None:
        try:
            path = self._path(pid)
        except ValueError:
            return None
        if not path.exists():
            return None
        try:
            return Playbook.from_dict(json.loads(path.read_text(encoding="utf-8")))
        except (json.JSONDecodeError, TypeError, ValueError):
            return None

    def list(self, limit: int = 100) -> list[Playbook]:
        directory = self.dir
        if not directory.exists():
            return []
        out = []
        for path in sorted(directory.glob("*.json"), reverse=True):
            pb = self.get(path.stem)
            if pb is not None:
                out.append(pb)
        return out[:limit]

    def delete(self, pid: str) -> bool:
        try:
            path = self._path(pid)
        except ValueError:
            return False
        if path.exists():
            path.unlink()
            return True
        return False


playbooks = PlaybookStore()
