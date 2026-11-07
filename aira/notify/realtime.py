"""Live updates for open pages: one channel per account, served as Server-Sent Events.

The code that changes something (a chat message, a match, a trivia room, a new notification) publishes a short
*hint* to the account it concerns: a topic, the person it is for and ids, never the content. The page then asks
the usual routes for the data, so every permission check still applies. A household shares one account, so a
hint names the person (``p:<profile id>``) and each page keeps only its own.

Each channel keeps its last :data:`BUFFER` hints for a few minutes, so a page that reconnects with
``Last-Event-ID`` gets what it missed; an id from before a restart, or older than the buffer, gets ``resync``
(fetch everything once). Event ids are ``<epoch>-<sequence>``: the epoch changes with every process.

Streams are capped per channel and in total. A new stream on a full channel ends that channel's oldest one (a
page that navigated away holds its slot until a write fails, up to a heartbeat or two later), so moving between
pages never locks the account out; past the total cap a page gets 429 and keeps polling.
"""

from __future__ import annotations

import json
import secrets
import threading
import time
from collections import deque
from dataclasses import dataclass
from typing import Any

BUFFER = 200
KEEP_SECONDS = 600
PER_CHANNEL = 4
TOTAL = 200
HEARTBEAT_SECONDS = 15.0
STREAM_SECONDS = 300.0  # a stream ends after this and the browser reconnects (bounded thread lifetime)
RETRY_MS = 3000  # how long a browser waits before it reconnects
TOPICS = frozenset({"notify", "chat", "dating", "trivia", "pulse", "resync"})


class Busy(Exception):
    """Too many open streams (per account or in total)."""


@dataclass(frozen=True)
class Event:
    seq: int
    id: str
    topic: str
    person: str
    data: dict[str, Any]
    at: float

    def wire(self) -> str:
        payload = {"topic": self.topic, "person": self.person, **self.data}
        return f"id: {self.id}\ndata: {json.dumps(payload, separators=(',', ':'))}\n\n"


class _Channel:
    def __init__(self, lock: threading.Lock) -> None:
        self.events: deque[Event] = deque(maxlen=BUFFER)
        self.cv = threading.Condition(lock)
        self.streams = 0
        self.touched = time.monotonic()
        self.dropped = 0  # the newest sequence number this channel no longer holds
        self.tickets: deque[int] = deque()  # the open streams, oldest first
        self.evicted: set[int] = (
            set()
        )  # streams told to end because newer ones needed their slot


class Hub:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._channels: dict[str, _Channel] = {}
        self._seq = 0
        self.epoch = secrets.token_hex(4)
        self._streams = 0
        self._tickets = 0
        self._closed = False
        self.enabled = True

    def _channel(self, name: str) -> _Channel:
        channel = self._channels.get(name)
        if channel is None:
            channel = self._channels[name] = _Channel(self._lock)
        channel.touched = time.monotonic()
        return channel

    def _prune(self) -> None:
        cutoff = time.monotonic() - KEEP_SECONDS
        for name in [
            name
            for name, channel in self._channels.items()
            if not channel.streams and channel.touched < cutoff
        ]:
            del self._channels[name]

    def publish(
        self,
        channel: str,
        topic: str,
        data: dict[str, Any] | None = None,
        *,
        person: str = "",
    ) -> str:
        if not channel or not self.enabled:
            return ""
        with self._lock:
            self._seq += 1
            event = Event(
                self._seq,
                f"{self.epoch}-{self._seq}",
                topic,
                person,
                {k: v for k, v in (data or {}).items() if k not in {"topic", "person"}},
                time.monotonic(),
            )
            target = self._channel(channel)
            if len(target.events) == target.events.maxlen:
                target.dropped = max(target.dropped, target.events[0].seq)
            target.events.append(event)
            target.cv.notify_all()
            if self._seq % 500 == 0:
                self._prune()
        return event.id

    def open(self, channel: str) -> int:
        """Count one stream on ``channel``; returns the newest sequence number. Raises :class:`Busy`."""
        return self.open_stream(channel)[0]

    def open_stream(self, channel: str) -> tuple[int, int]:
        """Open a stream on ``channel``: ``(newest sequence number, ticket)``. On a full channel the oldest stream
        is told to end (:meth:`ended`) and this one takes its slot. Raises :class:`Busy` past the total cap."""
        with self._lock:
            target = self._channel(channel)
            if target.streams >= PER_CHANNEL and target.tickets:
                oldest = target.tickets.popleft()
                target.evicted.add(oldest)
                target.streams -= 1
                self._streams -= 1
                target.cv.notify_all()
            if self._closed or target.streams >= PER_CHANNEL or self._streams >= TOTAL:
                raise Busy(
                    "Too many live connections. The page keeps checking for changes instead."
                )
            self._tickets += 1
            target.tickets.append(self._tickets)
            target.streams += 1
            self._streams += 1
            return self._seq, self._tickets

    def ended(self, channel: str, ticket: int) -> bool:
        """Whether stream ``ticket`` was told to end (a newer stream took its slot)."""
        with self._lock:
            target = self._channels.get(channel)
            return bool(target is not None and ticket in target.evicted)

    def close(self, channel: str, ticket: int = 0) -> None:
        with self._lock:
            target = self._channels.get(channel)
            if target is None:
                return
            if ticket:
                if (
                    ticket in target.evicted
                ):  # its slot was already given to a newer stream
                    target.evicted.discard(ticket)
                    return
                try:
                    target.tickets.remove(ticket)
                except ValueError:
                    return
            elif target.tickets:
                target.tickets.popleft()
            if target.streams:
                target.streams -= 1
                self._streams -= 1

    def since(self, channel: str, last_id: str) -> tuple[list[Event], bool]:
        """Events after ``last_id`` and whether the page must resync (unknown id, or older than the buffer)."""
        epoch, _, number = str(last_id or "").partition("-")
        with self._lock:
            target = self._channel(channel)
            cutoff = time.monotonic() - KEEP_SECONDS
            if not last_id:
                return [], False
            if epoch != self.epoch or not number.isdigit():
                return [], True
            seq = int(number)
            dropped = max(
                [
                    target.dropped,
                    *(event.seq for event in target.events if event.at < cutoff),
                ]
            )
            newer = [
                event
                for event in target.events
                if event.seq > seq and event.at >= cutoff
            ]
            return newer, seq < dropped

    def wait(
        self, channel: str, after: int, timeout: float, ticket: int = 0
    ) -> list[Event]:
        """Block until ``channel`` has events after sequence ``after`` (or the timeout, :meth:`shutdown`, or stream
        ``ticket`` being told to end)."""
        with self._lock:
            target = self._channel(channel)
            end = time.monotonic() + timeout
            while not self._closed and not (ticket and ticket in target.evicted):
                newer = [event for event in target.events if event.seq > after]
                if newer:
                    return newer
                left = end - time.monotonic()
                if left <= 0:
                    return []
                target.cv.wait(timeout=left)
            return []

    @property
    def closed(self) -> bool:
        return self._closed

    def shutdown(self) -> None:
        """Wake and end every stream (the server is stopping)."""
        with self._lock:
            self._closed = True
            for channel in self._channels.values():
                channel.cv.notify_all()

    def reset(self) -> None:
        """Forget everything (tests)."""
        with self._lock:
            self._channels.clear()
            self._streams = 0
            self._closed = False
            self.epoch = secrets.token_hex(4)

    def stats(self) -> dict[str, int]:
        with self._lock:
            return {
                "channels": len(self._channels),
                "streams": self._streams,
                "seq": self._seq,
            }


HUB = Hub()


def resync_wire(after: int) -> str:
    """Tell a page it missed hints (it fetches everything once); its id resumes from now."""
    payload = json.dumps({"topic": "resync", "person": ""}, separators=(",", ":"))
    return f"id: {HUB.epoch}-{after}\ndata: {payload}\n\n"


def publish(
    channel: str, topic: str, data: dict[str, Any] | None = None, *, person: str = ""
) -> str:
    return HUB.publish(channel, topic, data, person=person)


def serve(
    channel: str,
    last: str,
    after: int,
    write: Any,
    *,
    stop: Any = None,
    ticket: int = 0,
) -> None:
    """Write one channel's stream until it times out, the hub closes or ``stop()`` is true (a draining server).

    ``write(text)`` sends text to the page and raises ``OSError`` once the page went away. The caller opened the
    channel (:meth:`Hub.open`, which gave ``after``) and closes it afterwards. Both the local server and the hosted
    gateway serve the same stream with this.
    """
    hub = HUB
    write(f"retry: {RETRY_MS}\n\n")
    backlog, resync = hub.since(channel, last)
    seq = after
    if resync:
        write(resync_wire(after))
    for event in backlog:
        write(event.wire())
        seq = max(seq, event.seq)
    deadline = time.monotonic() + STREAM_SECONDS
    while not hub.closed and not (stop is not None and stop()):
        if ticket and hub.ended(channel, ticket):
            break  # a newer page of this account took the slot
        left = deadline - time.monotonic()
        if left <= 0:
            break
        events = hub.wait(channel, seq, min(HEARTBEAT_SECONDS, left), ticket)
        if events:
            for event in events:
                write(event.wire())
                seq = event.seq
        else:
            write(": ping\n\n")
