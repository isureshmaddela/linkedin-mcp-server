"""Finish in browser (F14): hand one step of an application to the person, then carry on.

Some steps of an application are the person's to do: a CAPTCHA, a sign-in on the employer's site, a code the site
sent to their email or phone. Monosage never solves, bypasses or reads those. When a run is held for one of them,
Needs you offers *Finish in browser*: the local build runs the application again in a visible browser window
(with the cookies the held run kept), fills the form the same way, and stops on that step. The form engine then
watches the page: once the CAPTCHA or sign-in is gone, a CAPTCHA has its answer, or the page moved on, it carries
on by itself and submits. "I'm done" (the person finished but the page gives no sign) and "Give up" are the
fallbacks, and the window closes after :data:`TIMEOUT_SECONDS` whatever happens.

A :class:`Handoff` is the live session between the web request that started it, the worker thread that drives the
browser, and the person's "I'm done" / "Give up". It is in memory only: one process owns the browser window, and a
restart ends the run like any other interrupted run.
"""

from __future__ import annotations

import threading
import time
from collections.abc import Callable
from datetime import datetime, timezone
from typing import Any

__all__ = [
    "HOLD_KINDS",
    "OUTCOMES",
    "TIMEOUT_SECONDS",
    "Handoff",
    "HandoffClosed",
    "active",
    "close",
    "open_session",
    "sessions_for",
]

# The steps a person must do themselves (``ApplyOutcome.hold``).
HOLD_KINDS = ("captcha", "sign_in", "verification")
# How a wait ends: the page shows the step is done, the person said so, gave up, the time ran out, or the window
# was closed.
OUTCOMES = ("cleared", "done", "gave_up", "timed_out", "closed")
# Finished in time, or the window closes and the run waits in Needs you again.
TIMEOUT_SECONDS = 15 * 60
POLL_MILLISECONDS = 500
STATES = ("opening", "waiting", "resuming", "finished")
CHOICES = ("done", "give_up")


class HandoffClosed(Exception):
    """The browser window was closed while Monosage waited for the person."""


def _iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class Handoff:
    """One Finish in browser session for one application run."""

    def __init__(
        self,
        run_id: str,
        *,
        owner: str = "",
        timeout_seconds: float | None = None,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.run_id = run_id
        self.owner = owner
        self.timeout_seconds = float(
            TIMEOUT_SECONDS if timeout_seconds is None else timeout_seconds
        )
        self.clock = clock
        self.started_at = _iso()
        self.deadline = clock() + self.timeout_seconds
        self.state = "opening"
        self.kind = ""
        self.message = ""
        self.outcome = ""
        self.waits: list[dict[str, Any]] = []
        self._choice = ""
        self._lock = threading.Lock()

    # -- the person's side ---------------------------------------------------------------

    def decide(self, choice: str) -> None:
        """ "I'm done" (``done``) or "Give up" (``give_up``); taken by the next poll of the wait."""
        if choice not in CHOICES:
            raise ValueError("Choose done or give_up.")
        with self._lock:
            if self.state == "finished":
                raise ValueError(
                    "Finish in browser has already ended for this application."
                )
            if choice == "done" and self.state != "waiting":
                raise ValueError(
                    "Monosage is not waiting for you in the browser right now."
                )
            self._choice = choice

    def seconds_left(self) -> int:
        return max(0, int(self.deadline - self.clock()))

    def view(self) -> dict[str, Any]:
        """What Needs you shows while the session lives (no page content, no cookies)."""
        with self._lock:
            return {
                "state": self.state,
                "kind": self.kind,
                "message": self.message,
                "started_at": self.started_at,
                "seconds_left": self.seconds_left(),
                "timeout_seconds": int(self.timeout_seconds),
                "outcome": self.outcome,
            }

    # -- the browser's side ----------------------------------------------------------------

    def wait(
        self,
        kind: str,
        message: str,
        cleared: Callable[[], bool],
        sleep: Callable[[int], Any],
        *,
        poll_milliseconds: int = POLL_MILLISECONDS,
    ) -> str:
        """Wait on the browser's thread until the step is done; return one of :data:`OUTCOMES`.

        ``cleared`` reads the page (it raises :class:`HandoffClosed` when the window is gone); ``sleep`` waits in
        a way that keeps the browser driver serviced (``page.wait_for_timeout``).
        """
        with self._lock:
            self.state = "waiting"
            self.kind = kind if kind in HOLD_KINDS else ""
            self.message = message
            if self._choice == "done":
                self._choice = (
                    ""  # a "done" from an earlier step does not finish this one
                )
        started = self.clock()
        result = ""
        while not result:
            with self._lock:
                choice, self._choice = self._choice, ""
            if choice == "give_up":
                result = "gave_up"
                break
            if choice == "done":
                result = "done"
                break
            try:
                if cleared():
                    result = "cleared"
                    break
            except HandoffClosed:
                result = "closed"
                break
            if self.clock() >= self.deadline:
                result = "timed_out"
                break
            try:
                sleep(poll_milliseconds)
            except Exception:  # noqa: BLE001 - the window went away while waiting
                result = "closed"
        with self._lock:
            self.waits.append(
                {
                    "kind": self.kind,
                    "outcome": result,
                    "seconds": int(max(0.0, self.clock() - started)),
                }
            )
            self.state = "resuming" if result in ("cleared", "done") else "finished"
            if result not in ("cleared", "done"):
                self.outcome = result
        return result

    def finish(self, outcome: str = "") -> None:
        with self._lock:
            self.state = "finished"
            if outcome and not self.outcome:
                self.outcome = outcome

    @property
    def last_outcome(self) -> str:
        """How the session went: the outcome that ended it, else how the last wait ended."""
        if self.outcome:
            return self.outcome
        return self.waits[-1]["outcome"] if self.waits else ""


# ------------------------------------------------------------------------- the live sessions

_SESSIONS: dict[str, Handoff] = {}
_LOCK = threading.Lock()


def open_session(
    run_id: str, *, owner: str = "", timeout_seconds: float | None = None
) -> Handoff:
    """Start the session for ``run_id``; refused while one is already open for it."""
    with _LOCK:
        current = _SESSIONS.get(run_id)
        if current is not None and current.state != "finished":
            raise ValueError("Finish in browser is already open for this application.")
        session = Handoff(run_id, owner=owner, timeout_seconds=timeout_seconds)
        _SESSIONS[run_id] = session
        return session


def active(run_id: str, *, owner: str | None = None) -> Handoff | None:
    """The open session of ``run_id`` (of ``owner`` when given), or None."""
    with _LOCK:
        session = _SESSIONS.get(run_id)
    if session is None or session.state == "finished":
        return None
    if owner is not None and session.owner != owner:
        return None
    return session


def sessions_for(owner: str) -> list[Handoff]:
    with _LOCK:
        return [
            session
            for session in _SESSIONS.values()
            if session.owner == owner and session.state != "finished"
        ]


def close(run_id: str, session: Handoff | None = None) -> None:
    """End the session (only ``session`` itself when given, so a newer one is never removed)."""
    with _LOCK:
        current = _SESSIONS.get(run_id)
        if current is None or (session is not None and current is not session):
            return
        current.finish()
        del _SESSIONS[run_id]
