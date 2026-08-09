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
from tools import catalog

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

# Account-backed tools replay verbatim: they address records by id rather than by
# position on a page, so there is nothing to re-find and nothing to heal. This is
# what makes a cross-app routine — pull last week's invoices out of Gmail, append
# them to the Notion tracker — cost nothing to repeat.
#
# `gmail_send_reply` is deliberately absent. Replay skips approval gates by
# design, and one approved send must not become standing permission to send.
API_REPLAYABLE = {
    "notion_search", "notion_read_page", "notion_read_database",
    "notion_create_page", "notion_update_page", "notion_update_properties",
    "gmail_search", "gmail_read", "gmail_thread", "gmail_draft_reply",
}

# Arguments that are the user's *input* rather than the automation's fixed
# target, so they become editable parameters. Ids stay literal — they are what
# the playbook points at.
TEMPLATED_ARGS = ("query", "title", "markdown", "content", "body", "find", "replace")

API_PREFIX = "api:"


def required_services(pb: "Playbook") -> set[str]:
    """Which connected accounts a playbook needs in order to replay.

    A schedule uses this to fail *before* it runs with a message worth reading
    ("Reconnect Gmail") rather than after, with a stack of tool errors.
    """
    out: set[str] = set()
    for step in pb.steps:
        if not step.action.startswith(API_PREFIX):
            continue
        server = step.action[len(API_PREFIX):].split("_", 1)[0]
        if server in catalog.CREDENTIAL_PARAMS:
            out.add(server)
    return out


def replayable_tools() -> set[str]:
    """Every tool a completed run can contribute to a playbook.

    Approval-gated tools are removed here rather than merely omitted from the
    lists above, so adding one to `ALWAYS_CONFIRM` is enough to keep it out of
    unattended replay for good.
    """
    return (set(REPLAYABLE) | API_REPLAYABLE) - catalog.ALWAYS_CONFIRM


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
        if self.action.startswith(API_PREFIX):
            return _describe_api(self.action[len(API_PREFIX):],
                                 (self.target or {}).get("args") or {})
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
            elif filled.action.startswith(API_PREFIX):
                args = dict((step.target or {}).get("args") or {})
                filled.target = dict(step.target or {})
                filled.target["args"] = {
                    k: (fill(v) if isinstance(v, str) else v) for k, v in args.items()
                }
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
    allowed = replayable_tools()

    for rs in run.steps:
        if rs.tool not in allowed or rs.status != "done" or not rs.ok:
            continue
        if rs.tool in API_REPLAYABLE:
            steps.append(_api_step(rs, params, seen_params))
            continue

        action = REPLAYABLE[rs.tool]
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


def _api_step(rs, params: list, seen: set) -> PlaybookStep:
    """Record one Notion/Gmail call as a step that replays verbatim.

    The credential is not stored — it is a reserved parameter, injected fresh
    from the session at replay time, so a playbook never carries a token.
    """
    args = {k: v for k, v in (rs.args or {}).items() if k not in catalog.RESERVED_PARAMS}
    for key in TEMPLATED_ARGS:
        value = args.get(key)
        if isinstance(value, str) and value.strip():
            pname = _param_name({"name": f"{rs.tool}_{key}"}, len(params) + 1, seen)
            params.append(Param(
                name=pname,
                label=f"{catalog.label_for(rs.tool)} — {key}",
                default=value, example=value,
            ))
            args[key] = "{{%s}}" % pname
    return PlaybookStep(
        action=f"{API_PREFIX}{rs.tool}",
        target={"args": args},
        note=catalog.label_for(rs.tool),
    )


def _describe_api(tool: str, args: dict) -> str:
    label = catalog.label_for(tool)
    for key in ("query", "title", "find"):
        value = str(args.get(key) or "").strip()
        if value:
            return f'{label}: “{value[:60]}”'
    for key in ("page_id", "database_id", "thread_id", "message_id", "parent_id"):
        value = str(args.get(key) or "").strip()
        if value:
            return f"{label} ({value[:12]}…)"
    return label


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
