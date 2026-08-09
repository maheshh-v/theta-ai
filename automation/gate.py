"""
One browser, one run at a time.

Theta drives a single long-lived Chromium page (see `browser/session.py`) — that
is what lets a login, a cookie banner or a filled-in form survive between tool
calls. It also means two runs cannot be in flight at once: they would take turns
clicking on each other's page and both would be wrong.

Until schedules existed this was true for free, because a person only starts one
task at a time. A scheduler firing in the background breaks that assumption, so
the exclusion has to become explicit.

The asymmetry is deliberate. A person waiting at the screen wins: a live run
waits a little for the gate and then goes ahead regardless, because hanging the
UI behind a background job is worse than the collision it avoids. A scheduled
run never waits — if the gate is taken it gives up its slot and tries again in a
few minutes, which nobody is watching and nobody misses.
"""

from __future__ import annotations

import logging
import threading
from contextlib import contextmanager

_log = logging.getLogger("theta.gate")


class RunGate:
    """Mutual exclusion over the shared browser, with a named holder."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._holder = ""

    @property
    def busy(self) -> bool:
        return self._lock.locked()

    @property
    def holder(self) -> str:
        return self._holder

    def acquire(self, holder: str, timeout: float = 0.0) -> bool:
        """Take the gate. `timeout=0` means "only if it is free right now"."""
        got = self._lock.acquire(timeout=timeout) if timeout > 0 else self._lock.acquire(
            blocking=False
        )
        if got:
            self._holder = holder
        return got

    def release(self) -> None:
        if self._lock.locked():
            self._holder = ""
            try:
                self._lock.release()
            except RuntimeError:  # pragma: no cover - released from another thread
                _log.debug("Run gate released twice")

    @contextmanager
    def hold(self, holder: str, timeout: float = 0.0, force: bool = False):
        """Hold the gate for the duration of a run.

        With `force`, the block runs even if the gate could not be taken — the
        live-run path, which must never deadlock behind a background job. The
        yielded value says whether exclusivity was actually obtained, so a caller
        can tell the user what is going on.
        """
        got = self.acquire(holder, timeout=timeout)
        if not got and not force:
            yield False
            return
        try:
            yield got
        finally:
            if got:
                self.release()


# Process-wide: there is exactly one browser behind it.
run_gate = RunGate()
