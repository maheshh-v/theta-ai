"""
Runs: the record of what Theta actually did.

Every execution — ad-hoc or playbook replay — becomes a Run: the goal, each
action with its outcome, a screenshot per step, and the final answer. Two reasons
this matters beyond nostalgia:

* **Accountability.** An agent that clicks buttons on your behalf needs a
  reviewable trail. "What did it actually do at 14:02?" must have an answer.
* **Playbooks are built from it.** A run that worked is the raw material for an
  automation, so the trace keeps the element selectors, not just prose.

One directory per run: `data/runs/<id>/run.json` plus `step-N.jpg`.
"""

from __future__ import annotations

import json
import re
import shutil
import threading
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from secrets import token_urlsafe

from config import settings

_ID_RE = re.compile(r"[A-Za-z0-9_\-]{1,64}")


@dataclass
class RunStep:
    index: int
    tool: str
    args: dict = field(default_factory=dict)
    label: str = ""
    summary: str = ""
    ok: bool = True
    status: str = "done"          # done | declined | error
    url: str = ""
    screenshot: str = ""          # bare filename, served via the run
    target: dict = field(default_factory=dict)   # element acted on, for recording
    healed: bool = False          # replay had to re-find this element

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class Run:
    id: str
    goal: str
    created: str = ""
    status: str = "running"       # running | done | failed | cancelled
    answer: str = ""
    seconds: float = 0.0
    steps: list[RunStep] = field(default_factory=list)
    outputs: list[str] = field(default_factory=list)   # workspace files produced
    playbook_id: str = ""         # set when this run was a replay
    # What started this run: a person typing ("task"), a person clicking replay
    # ("playbook"), or the scheduler ("schedule"). Runs nobody watched are the
    # ones most worth being able to find later, so Activity filters on it.
    trigger: str = "task"
    model: str = ""
    approvals: int = 0

    @staticmethod
    def new_id() -> str:
        return datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S-") + token_urlsafe(3)

    @property
    def browser_steps(self) -> list[RunStep]:
        return [s for s in self.steps if s.tool.startswith("browser_")]

    def to_dict(self) -> dict:
        data = asdict(self)
        data["steps"] = [s.to_dict() for s in self.steps]
        return data

    @classmethod
    def from_dict(cls, data: dict) -> "Run":
        data = dict(data)
        data["steps"] = [RunStep(**s) for s in data.get("steps", [])]
        known = set(cls.__dataclass_fields__)
        return cls(**{k: v for k, v in data.items() if k in known})

    def summary_dict(self) -> dict:
        shots = [s.screenshot for s in self.steps if s.screenshot]
        return {
            "id": self.id,
            "goal": self.goal,
            "created": self.created,
            "status": self.status,
            "steps": len(self.steps),
            "seconds": self.seconds,
            "answer": self.answer[:300],
            "playbook_id": self.playbook_id,
            "trigger": self.trigger,
            "approvals": self.approvals,
            "outputs": self.outputs,
            "thumbnail": shots[-1] if shots else "",
        }


class RunStore:
    """One directory per run: the JSON trace beside its screenshots."""

    def __init__(self, directory=None) -> None:
        self._dir = directory
        self._lock = threading.Lock()

    @property
    def dir(self):
        return self._dir or settings.runs_dir

    def run_dir(self, run_id: str):
        if not _ID_RE.fullmatch(run_id or ""):
            raise ValueError(f"Invalid run id: {run_id!r}")
        return self.dir / run_id

    def create(self, goal: str, playbook_id: str = "", model: str = "",
               trigger: str = "task") -> Run:
        run = Run(
            id=Run.new_id(),
            goal=goal,
            created=datetime.now(timezone.utc).isoformat(timespec="seconds"),
            playbook_id=playbook_id,
            trigger=trigger,
            model=model,
        )
        self.run_dir(run.id).mkdir(parents=True, exist_ok=True)
        self.save(run)
        return run

    def shot_path(self, run_id: str, index: int) -> str:
        return str(self.run_dir(run_id) / f"step-{index}.jpg")

    def save(self, run: Run) -> Run:
        with self._lock:
            directory = self.run_dir(run.id)
            directory.mkdir(parents=True, exist_ok=True)
            path = directory / "run.json"
            tmp = path.with_suffix(".json.tmp")
            tmp.write_text(json.dumps(run.to_dict(), ensure_ascii=False, indent=2),
                           encoding="utf-8")
            tmp.replace(path)
        return run

    def get(self, run_id: str) -> Run | None:
        try:
            path = self.run_dir(run_id) / "run.json"
        except ValueError:
            return None
        if not path.exists():
            return None
        try:
            return Run.from_dict(json.loads(path.read_text(encoding="utf-8")))
        except (json.JSONDecodeError, TypeError, ValueError):
            return None

    def list(self, limit: int = 40) -> list[Run]:
        directory = self.dir
        if not directory.exists():
            return []
        out: list[Run] = []
        for child in sorted(directory.iterdir(), reverse=True):
            if not child.is_dir():
                continue
            run = self.get(child.name)
            if run is not None:
                out.append(run)
            if len(out) >= limit:
                break
        return out

    def delete(self, run_id: str) -> bool:
        try:
            directory = self.run_dir(run_id)
        except ValueError:
            return False
        if directory.exists():
            shutil.rmtree(directory, ignore_errors=True)
            return True
        return False

    def screenshot(self, run_id: str, name: str):
        """Resolve a screenshot path, refusing anything that escapes the run."""
        if not re.fullmatch(r"step-\d{1,4}\.jpg", name or ""):
            return None
        try:
            path = self.run_dir(run_id) / name
        except ValueError:
            return None
        return path if path.exists() else None


runs = RunStore()
