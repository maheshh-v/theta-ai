"""
Schedules: a Playbook that runs itself.

This is the third act of the idea the project is built around. A run teaches
Theta how to do something (expensive, one model call per step); a Playbook
turns that into a deterministic replay (free); a Schedule means you stop
clicking replay. "Every weekday at 8am, pull last week's invoices out of Gmail
and append them to the Notion tracker" is the whole point of having Gmail,
Notion and a browser in one agent.

Two properties make unattended running defensible rather than reckless:

* **A schedule cannot make a new decision.** It replays recorded steps and
  nothing else. There is no model in the loop, so there is no room for it to
  improvise into something you did not sanction.
* **A schedule cannot send an email.** Not by policy but by construction:
  `playbooks.replayable_tools()` subtracts `catalog.ALWAYS_CONFIRM`, so an
  approval-gated action never makes it into a Playbook in the first place, and
  a Schedule can only run Playbooks.

Times are stored as local wall-clock plus the browser's UTC offset, which keeps
"8am" meaning 8am without shipping a timezone database. The offset is captured
when the schedule is written, so a run crossing a DST boundary drifts by an
hour until the schedule is edited — a deliberate trade for having no dependency.
"""

from __future__ import annotations

import json
import re
import threading
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta, timezone
from secrets import token_urlsafe

from config import settings

_ID_RE = re.compile(r"[A-Za-z0-9_\-]{1,64}")

CADENCES = ("hourly", "daily", "weekdays", "weekly")
WEEKDAY_NAMES = ("Monday", "Tuesday", "Wednesday", "Thursday",
                 "Friday", "Saturday", "Sunday")

# Outcomes of the most recent attempt.
DONE = "done"
FAILED = "failed"
SKIPPED = "skipped"          # Theta was busy; deferred rather than run
BLOCKED = "blocked"          # the owning session or its credentials are gone


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


@dataclass
class Schedule:
    """One recurring, unattended replay of a Playbook."""

    id: str
    playbook_id: str
    name: str = ""
    cadence: str = "daily"           # hourly | daily | weekdays | weekly
    hour: int = 9                    # local wall-clock
    minute: int = 0
    weekday: int = 0                 # 0 = Monday, for the weekly cadence
    tz_offset: int = 0               # JS getTimezoneOffset(): minutes behind UTC
    values: dict = field(default_factory=dict)   # the Playbook's inputs
    enabled: bool = True
    owner_sid: str = ""              # whose credentials the run uses
    created: str = ""
    next_run: str = ""               # ISO UTC
    last_run: str = ""
    last_status: str = ""
    last_run_id: str = ""
    last_error: str = ""
    run_count: int = 0
    fail_count: int = 0

    @staticmethod
    def new_id() -> str:
        return datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S-") + token_urlsafe(3)

    # -- time ------------------------------------------------------------- #
    def _to_local(self, moment: datetime) -> datetime:
        return moment - timedelta(minutes=self.tz_offset)

    def _to_utc(self, local: datetime) -> datetime:
        return local + timedelta(minutes=self.tz_offset)

    def next_after(self, moment: datetime | None = None) -> datetime:
        """The next firing strictly after `moment`, in UTC."""
        moment = (moment or utcnow()).replace(microsecond=0)
        local = self._to_local(moment)

        if self.cadence == "hourly":
            candidate = local.replace(minute=self.minute, second=0, microsecond=0)
            if candidate <= local:
                candidate += timedelta(hours=1)
            return self._to_utc(candidate)

        candidate = local.replace(hour=self.hour, minute=self.minute,
                                  second=0, microsecond=0)
        if candidate <= local:
            candidate += timedelta(days=1)

        if self.cadence == "weekdays":
            while candidate.weekday() > 4:          # Sat/Sun
                candidate += timedelta(days=1)
        elif self.cadence == "weekly":
            while candidate.weekday() != self.weekday:
                candidate += timedelta(days=1)

        return self._to_utc(candidate)

    def reschedule(self, moment: datetime | None = None) -> None:
        self.next_run = self.next_after(moment).isoformat(timespec="seconds")

    def defer(self, minutes: int, moment: datetime | None = None) -> None:
        """Push the next attempt back without losing the slot entirely.

        Used when Theta is already driving the browser: a scheduled run that
        collided with a live one should try again shortly, not be marked failed
        and not wait a whole day.
        """
        soon = (moment or utcnow()).replace(microsecond=0) + timedelta(minutes=minutes)
        regular = self.next_after(moment)
        self.next_run = min(soon, regular).isoformat(timespec="seconds")

    def due_at(self) -> datetime | None:
        if not self.next_run:
            return None
        try:
            parsed = datetime.fromisoformat(self.next_run)
        except ValueError:
            return None
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)

    def is_due(self, moment: datetime | None = None) -> bool:
        if not self.enabled:
            return False
        when = self.due_at()
        return when is not None and when <= (moment or utcnow())

    # -- display ------------------------------------------------------------ #
    def cadence_label(self) -> str:
        clock = f"{self.hour:02d}:{self.minute:02d}"
        if self.cadence == "hourly":
            return f"Every hour at :{self.minute:02d}"
        if self.cadence == "weekdays":
            return f"Weekdays at {clock}"
        if self.cadence == "weekly":
            return f"{WEEKDAY_NAMES[self.weekday % 7]}s at {clock}"
        return f"Every day at {clock}"

    # -- serialisation ------------------------------------------------------ #
    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> "Schedule":
        known = set(cls.__dataclass_fields__)
        return cls(**{k: v for k, v in dict(data).items() if k in known})

    def summary_dict(self) -> dict:
        """The UI's view. `owner_sid` is deliberately absent — it identifies a
        session, and nothing in the browser needs it."""
        return {
            "id": self.id,
            "playbook_id": self.playbook_id,
            "name": self.name,
            "cadence": self.cadence,
            "cadence_label": self.cadence_label(),
            "hour": self.hour,
            "minute": self.minute,
            "weekday": self.weekday,
            "tz_offset": self.tz_offset,
            "values": dict(self.values),
            "enabled": self.enabled,
            "created": self.created,
            "next_run": self.next_run if self.enabled else "",
            "last_run": self.last_run,
            "last_status": self.last_status,
            "last_run_id": self.last_run_id,
            "last_error": self.last_error,
            "run_count": self.run_count,
            "fail_count": self.fail_count,
        }


def build(playbook, data: dict, owner_sid: str) -> Schedule:
    """Create a schedule for `playbook` from a request body, clamping everything.

    Values are filtered to the playbook's declared parameters so a hand-made
    request cannot smuggle an extra argument into an unattended run.
    """
    allowed = {p.name for p in playbook.params}
    values = {k: str(v) for k, v in (data.get("values") or {}).items() if k in allowed}

    cadence = str(data.get("cadence", "daily")).lower()
    if cadence not in CADENCES:
        cadence = "daily"

    schedule = Schedule(
        id=Schedule.new_id(),
        playbook_id=playbook.id,
        name=str(data.get("name") or playbook.name)[:120],
        cadence=cadence,
        hour=_clamp(data.get("hour"), 0, 23, 9),
        minute=_clamp(data.get("minute"), 0, 59, 0),
        weekday=_clamp(data.get("weekday"), 0, 6, 0),
        # A browser offset is minutes behind UTC and never exceeds ±14 hours.
        tz_offset=_clamp(data.get("tz_offset"), -840, 840, 0),
        values=values,
        enabled=bool(data.get("enabled", True)),
        owner_sid=owner_sid,
        created=utcnow().isoformat(timespec="seconds"),
    )
    schedule.reschedule()
    return schedule


def apply_edit(schedule: Schedule, playbook, data: dict) -> Schedule:
    """Update an existing schedule in place from a PATCH body."""
    if data.get("name"):
        schedule.name = str(data["name"])[:120]

    cadence = str(data.get("cadence", "")).lower()
    timing_changed = cadence in CADENCES and cadence != schedule.cadence
    if cadence in CADENCES:
        schedule.cadence = cadence

    for attr, lo, hi in (("hour", 0, 23), ("minute", 0, 59), ("weekday", 0, 6),
                         ("tz_offset", -840, 840)):
        if data.get(attr) is None:
            continue
        value = _clamp(data[attr], lo, hi, getattr(schedule, attr))
        timing_changed = timing_changed or value != getattr(schedule, attr)
        setattr(schedule, attr, value)

    if isinstance(data.get("values"), dict) and playbook is not None:
        allowed = {p.name for p in playbook.params}
        schedule.values = {k: str(v) for k, v in data["values"].items() if k in allowed}

    if data.get("enabled") is not None:
        was_enabled = schedule.enabled
        schedule.enabled = bool(data["enabled"])
        # Resuming should not fire once for every slot that passed while paused.
        timing_changed = timing_changed or (schedule.enabled and not was_enabled)

    if timing_changed:
        schedule.reschedule()
    return schedule


def _clamp(value, lo: int, hi: int, default: int) -> int:
    try:
        return max(lo, min(hi, int(value)))
    except (TypeError, ValueError):
        return default


# --------------------------------------------------------------------------- #
# Storage                                                                     #
# --------------------------------------------------------------------------- #
class ScheduleStore:
    """One JSON file per schedule, beside the playbooks they run."""

    def __init__(self, directory=None) -> None:
        self._dir = directory
        self._lock = threading.Lock()

    @property
    def dir(self):
        return self._dir or settings.schedules_dir

    def _path(self, sid: str):
        if not _ID_RE.fullmatch(sid or ""):
            raise ValueError(f"Invalid schedule id: {sid!r}")
        return self.dir / f"{sid}.json"

    def save(self, schedule: Schedule) -> Schedule:
        with self._lock:
            self.dir.mkdir(parents=True, exist_ok=True)
            path = self._path(schedule.id)
            tmp = path.with_suffix(".json.tmp")
            tmp.write_text(json.dumps(schedule.to_dict(), ensure_ascii=False, indent=2),
                           encoding="utf-8")
            tmp.replace(path)
        return schedule

    def get(self, sid: str) -> Schedule | None:
        try:
            path = self._path(sid)
        except ValueError:
            return None
        if not path.exists():
            return None
        try:
            return Schedule.from_dict(json.loads(path.read_text(encoding="utf-8")))
        except (json.JSONDecodeError, TypeError, ValueError):
            return None

    def list(self, playbook_id: str = "") -> list[Schedule]:
        directory = self.dir
        if not directory.exists():
            return []
        out: list[Schedule] = []
        for path in sorted(directory.glob("*.json")):
            schedule = self.get(path.stem)
            if schedule is None:
                continue
            if playbook_id and schedule.playbook_id != playbook_id:
                continue
            out.append(schedule)
        return sorted(out, key=lambda s: (not s.enabled, s.next_run or "~"))

    def due(self, moment: datetime | None = None) -> list[Schedule]:
        moment = moment or utcnow()
        return [s for s in self.list() if s.is_due(moment)]

    def delete(self, sid: str) -> bool:
        try:
            path = self._path(sid)
        except ValueError:
            return False
        if path.exists():
            path.unlink()
            return True
        return False

    def delete_for_playbook(self, playbook_id: str) -> int:
        """Remove the schedules of a deleted Playbook, so nothing is orphaned."""
        removed = 0
        for schedule in self.list(playbook_id=playbook_id):
            removed += 1 if self.delete(schedule.id) else 0
        return removed


schedules = ScheduleStore()
