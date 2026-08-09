"""
The background loop that fires due Schedules.

Deliberately boring: wake up, ask the store what is due, replay it, write down
what happened. All the interesting decisions were made when the Playbook was
recorded, which is exactly the property that makes running it unattended
reasonable.

Three failure modes get distinct treatment, because collapsing them into
"failed" would make the UI lie:

* **skipped** — Theta was already driving the browser. Not a failure; the slot
  is deferred by a few minutes and tried again.
* **blocked** — the schedule *cannot* run as configured: its Playbook was
  deleted, the session that owns it is gone, or a service it needs has been
  disconnected. The schedule pauses itself rather than failing hourly forever.
* **failed** — it ran and the automation itself did not work. That is the only
  case where the site, not the setup, is the problem.

Catch-up is intentionally not implemented. If Theta was off for two days, a
daily schedule runs once when it comes back, not forty-eight times.
"""

from __future__ import annotations

import logging
import threading

from automation import playbooks as pb_mod
from automation.gate import run_gate
from automation.playbooks import playbooks
from automation.replay import ReplayError, replay
from automation.schedules import BLOCKED, DONE, FAILED, SKIPPED, Schedule, schedules, utcnow
from config import settings
from tools import catalog

_log = logging.getLogger("theta.scheduler")


class Scheduler:
    """Owns the timer thread. `tick()` is the whole behaviour and is sync, so
    the tests drive it directly instead of waiting on a clock."""

    def __init__(self, mcp, store, gate=run_gate) -> None:
        self.mcp = mcp
        self.store = store          # SessionStore: resolves a schedule's owner
        self.gate = gate
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()

    # -- lifecycle ---------------------------------------------------------- #
    def start(self) -> None:
        if not settings.scheduler_enabled or self._thread is not None:
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, name="theta-scheduler",
                                        daemon=True)
        self._thread.start()
        _log.info("Scheduler started (tick %ss)", settings.scheduler_tick)

    def stop(self) -> None:
        self._stop.set()
        thread, self._thread = self._thread, None
        if thread is not None:
            thread.join(timeout=5)

    def _loop(self) -> None:
        while not self._stop.wait(settings.scheduler_tick):
            try:
                self.tick()
            except Exception:  # pragma: no cover - the loop must never die
                _log.exception("Scheduler tick failed")

    # -- the work ----------------------------------------------------------- #
    def tick(self, moment=None) -> list[dict]:
        """Fire everything due at `moment`. Returns one outcome dict per fire."""
        moment = moment or utcnow()
        outcomes = []
        for schedule in schedules.due(moment):
            try:
                outcomes.append(self._fire(schedule, moment))
            except Exception as ex:  # pragma: no cover - defensive
                _log.exception("Schedule %s crashed", schedule.id)
                outcomes.append(self._finish(schedule, FAILED, moment,
                                             error=f"Unexpected error: {ex}"))
        return outcomes

    def _fire(self, schedule: Schedule, moment) -> dict:
        playbook = playbooks.get(schedule.playbook_id)
        if playbook is None:
            return self._finish(
                schedule, BLOCKED, moment, pause=True,
                error="The Playbook this schedule runs has been deleted.",
            )

        session = self.store.existing(schedule.owner_sid)
        if session is None:
            return self._finish(
                schedule, BLOCKED, moment, pause=True,
                error="The session that created this schedule has expired. "
                      "Open Theta and re-create it.",
            )

        missing = self._missing_services(playbook, session)
        if missing:
            names = " and ".join(sorted(s.capitalize() for s in missing))
            return self._finish(
                schedule, BLOCKED, moment, pause=True,
                error=f"{names} is no longer connected, and this automation needs it. "
                      f"Reconnect it and resume this schedule.",
            )

        # Never queue behind a live task — give up the slot and retry shortly.
        if not self.gate.acquire(f"schedule:{schedule.id}"):
            schedule.last_status = SKIPPED
            schedule.defer(settings.scheduler_defer, moment)
            schedules.save(schedule)
            _log.info("Schedule %s deferred — %s holds the browser",
                      schedule.id, self.gate.holder or "another run")
            return {"id": schedule.id, "status": SKIPPED, "run_id": ""}

        try:
            return self._replay(schedule, playbook, session, moment)
        finally:
            self.gate.release()

    def _replay(self, schedule: Schedule, playbook, session, moment) -> dict:
        from agent.llm import LLMError
        from server import preferences

        # A model is only needed to re-find an element whose selector broke.
        # Without one, replay stays strict rather than refusing to start.
        try:
            llm = preferences.build_llm(session)
        except LLMError:
            llm = None
        context = preferences.tool_context(session, llm)

        try:
            record = replay(playbook, schedule.values, self.mcp, context, llm,
                            trigger="schedule")
        except ReplayError as ex:
            return self._finish(schedule, FAILED, moment, error=str(ex))

        status = DONE if record.status == "done" else FAILED
        return self._finish(schedule, status, moment, run_id=record.id,
                            error="" if status == DONE else _why(record))

    @staticmethod
    def _missing_services(playbook, session) -> set[str]:
        """Services this playbook needs that the owner is no longer connected to."""
        from server import preferences

        held = preferences.credentials(session)
        return {
            service
            for service in pb_mod.required_services(playbook)
            if not held.get(catalog.credential_param(service))
        }

    @staticmethod
    def _finish(schedule: Schedule, status: str, moment, run_id: str = "",
                error: str = "", pause: bool = False) -> dict:
        schedule.last_status = status
        schedule.last_run = moment.isoformat(timespec="seconds")
        schedule.last_run_id = run_id
        schedule.last_error = error[:400]
        schedule.run_count += 1
        if status == FAILED:
            schedule.fail_count += 1
        elif status == DONE:
            schedule.fail_count = 0
        if pause:
            # A misconfigured schedule that keeps firing is noise, not a warning.
            schedule.enabled = False
        else:
            schedule.reschedule(moment)
        schedules.save(schedule)
        _log.info("Schedule %s → %s%s", schedule.id, status,
                  f" ({error})" if error else "")
        return {"id": schedule.id, "status": status, "run_id": run_id, "error": error}


def _why(record) -> str:
    """One line explaining a failed replay, from the step that broke."""
    failed = [s for s in record.steps if not s.ok]
    if not failed:
        return "The automation did not complete."
    last = failed[-1]
    return f"Stopped at step {last.index} ({last.label}): {last.summary}"[:400]
