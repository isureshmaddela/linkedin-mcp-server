"""Deliveries that leave the server (Web Push, platform mail) run on one background thread, never inside the
request or tick that created them.

A job is a callable; it returns ``None`` when it is done or a number of seconds to wait before it runs again (a
busy push service). A job is tried at most :data:`MAX_ATTEMPTS` times. The queue lives in memory: a notification
is always stored in the person's notification centre first, so a delivery lost to a restart costs a buzz on the
phone, not the notification.
"""

from __future__ import annotations

import heapq
import itertools
import logging
import threading
import time
from collections.abc import Callable
from typing import Any

logger = logging.getLogger("aira.notify")

Job = Callable[[], "float | None"]
MAX_ATTEMPTS = 3
MAX_QUEUED = 5000


class Dispatcher:
    def __init__(self) -> None:
        self._heap: list[tuple[float, int, int, Job]] = []
        self._order = itertools.count()
        self._cv = threading.Condition()
        self._thread: threading.Thread | None = None
        self._busy = 0
        self._stopping = False

    def submit(self, job: Job, *, delay: float = 0.0, attempt: int = 1) -> bool:
        with self._cv:
            if len(self._heap) >= MAX_QUEUED:
                logger.warning("Notification queue is full; a delivery was dropped")
                return False
            heapq.heappush(
                self._heap, (time.monotonic() + delay, next(self._order), attempt, job)
            )
            self._start()
            self._cv.notify()
        return True

    def _start(self) -> None:
        if self._thread is None or not self._thread.is_alive():
            self._stopping = False
            self._thread = threading.Thread(
                target=self._run, name="aira-notify", daemon=True
            )
            self._thread.start()

    def _run(self) -> None:
        while True:
            with self._cv:
                while not self._stopping and (
                    not self._heap or self._heap[0][0] > time.monotonic()
                ):
                    wait = self._heap[0][0] - time.monotonic() if self._heap else None
                    self._cv.wait(timeout=wait)
                if self._stopping:
                    return
                _due, _order, attempt, job = heapq.heappop(self._heap)
                self._busy += 1
            try:
                again = job()
            except Exception:  # noqa: BLE001 - one delivery must not stop the others
                logger.exception("A notification delivery failed")
                again = None
            with self._cv:
                self._busy -= 1
                if again is not None and attempt < MAX_ATTEMPTS:
                    heapq.heappush(
                        self._heap,
                        (
                            time.monotonic() + max(0.0, float(again)),
                            next(self._order),
                            attempt + 1,
                            job,
                        ),
                    )
                self._cv.notify_all()

    def pending(self) -> int:
        with self._cv:
            return len(self._heap) + self._busy

    def drain(self, timeout: float = 10.0, *, include_delayed: bool = False) -> bool:
        """Wait until every job that is due has run (tests). ``include_delayed`` runs retries at once."""
        end = time.monotonic() + timeout
        with self._cv:
            if include_delayed:
                self._heap = [(0.0, order, a, j) for _d, order, a, j in self._heap]
                heapq.heapify(self._heap)
                self._cv.notify_all()
            while time.monotonic() < end:
                due = [item for item in self._heap if item[0] <= time.monotonic()]
                if not due and not self._busy:
                    return True
                self._cv.wait(timeout=0.05)
        return False

    def clear(self) -> None:
        with self._cv:
            self._heap.clear()

    def stop(self) -> None:
        with self._cv:
            self._stopping = True
            self._cv.notify_all()


DISPATCHER = Dispatcher()


def submit(job: Job, **kwargs: Any) -> bool:
    return DISPATCHER.submit(job, **kwargs)
