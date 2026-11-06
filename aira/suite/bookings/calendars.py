"""Each staff member's own calendar, so a time they are busy elsewhere is not offered to customers.

A person pastes their calendar's private address in iCal format (Google Calendar: Settings > the calendar >
"Secret address in iCal format"; Outlook: Settings > Calendar > Shared calendars > Publish a calendar > ICS). It is
read with :mod:`aira.suite.webfetch` (https, public hosts only, capped), on save, on "Sync now", and by the
background task every :data:`SYNC_MINUTES`. Its busy times from yesterday to the end of the booking window are
kept in ``bk_calendar_busy`` (replaced on every good read; a failed read keeps the last good ones and records the
error), and :func:`busy_between` hands them to availability. The address itself is a secret: the app only ever
shows it masked.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Callable, Mapping
from datetime import datetime, timedelta
from typing import Any

from aira.suite import kit, webfetch
from aira.suite.bookings import ical, store
from aira.suite.bookings.store import APP

SYNC_MINUTES = 30
MAX_BUSY = 5000  # busy ranges kept per person
STALE_DAYS = 3  # a calendar not read for this long is still used, and the person is told it is out of date

Fetcher = Callable[[str], bytes]


def _fetch(url: str) -> bytes:
    return webfetch.fetch(
        url, accept="text/calendar, text/plain;q=0.8, */*;q=0.5"
    )


fetcher: Fetcher = _fetch  # tests may point this at something else; the default is the safe reader


def check_address(value: Any) -> str:
    text = kit.text(value, "the calendar address", max_len=2000, required=True)
    try:
        return webfetch.check_url(text)
    except webfetch.FetchError as error:
        raise ValueError(f"{error} Paste the calendar's private address in iCal format.") from None


def set_feed(
    connection: sqlite3.Connection, staff_id: str, url: Any, *, sync: bool = True
) -> dict[str, Any] | None:
    """Connect (or replace) a person's calendar and read it at once. A first read that fails is kept with its
    error, so the person sees what went wrong."""
    kit.require_row(connection, "bk_staff", staff_id, "person")
    address = check_address(url)
    stamp = kit.now()
    connection.execute(
        """INSERT INTO bk_calendar_feeds (staff_id, url, last_error, created_at, updated_at) VALUES (?, ?, '', ?, ?)
        ON CONFLICT(staff_id) DO UPDATE SET url = excluded.url, last_error = '', last_synced_at = NULL,
        last_attempt_at = NULL, busy_count = 0, updated_at = excluded.updated_at""",
        (staff_id, address, stamp, stamp),
    )
    connection.execute("DELETE FROM bk_calendar_busy WHERE staff_id = ?", (staff_id,))
    kit.log(connection, APP, "calendar", "A staff calendar was connected", ref_type="staff", ref_id=staff_id)
    connection.commit()
    if sync:
        sync_feed(connection, staff_id)
    return store.calendar_feed(connection, staff_id)


def remove_feed(connection: sqlite3.Connection, staff_id: str) -> bool:
    kit.require_row(connection, "bk_staff", staff_id, "person")
    removed = connection.execute(
        "DELETE FROM bk_calendar_feeds WHERE staff_id = ?", (staff_id,)
    ).rowcount
    connection.execute("DELETE FROM bk_calendar_busy WHERE staff_id = ?", (staff_id,))
    connection.commit()
    return bool(removed)


def _window(connection: sqlite3.Connection, now: datetime) -> tuple[datetime, datetime]:
    days = int(store.get_rules(connection)["max_days_ahead"])
    return now - timedelta(days=1), now + timedelta(days=days + 2)


def sync_feed(
    connection: sqlite3.Connection, staff_id: str, *, now: datetime | None = None
) -> dict[str, Any]:
    """Read one person's calendar now. Never raises for a calendar that cannot be read: the error is recorded."""
    row = kit.fetch_one(
        connection, "SELECT * FROM bk_calendar_feeds WHERE staff_id = ?", (staff_id,)
    )
    if not row:
        raise LookupError("This person has no calendar connected.")
    moment = now or kit.utcnow()
    stamp = kit.iso(moment)
    connection.execute(
        "UPDATE bk_calendar_feeds SET last_attempt_at = ? WHERE staff_id = ?", (stamp, staff_id)
    )
    connection.commit()
    start, end = _window(connection, moment)
    try:
        body = fetcher(row["url"])
        text = body.decode("utf-8", "replace")
        busy = ical.busy_times(
            text,
            window_start=start,
            window_end=end,
            fallback=store.zone(connection),
            limit=MAX_BUSY,
        )
    except (webfetch.FetchError, ValueError) as error:
        message = str(error)[:300] or "The calendar could not be read."
        connection.execute(
            "UPDATE bk_calendar_feeds SET last_error = ? WHERE staff_id = ?", (message, staff_id)
        )
        connection.commit()
        return {"ok": False, "error": message}
    connection.execute("DELETE FROM bk_calendar_busy WHERE staff_id = ?", (staff_id,))
    connection.executemany(
        "INSERT INTO bk_calendar_busy (staff_id, start_at, end_at) VALUES (?, ?, ?)",
        [(staff_id, kit.iso(begin), kit.iso(finish)) for begin, finish in busy],
    )
    connection.execute(
        "UPDATE bk_calendar_feeds SET last_synced_at = ?, last_error = '', busy_count = ? WHERE staff_id = ?",
        (stamp, len(busy), staff_id),
    )
    connection.commit()
    return {"ok": True, "busy": len(busy)}


def sync_due(connection: sqlite3.Connection, *, now: datetime | None = None) -> dict[str, int]:
    """The background pass: every active person's calendar not read in the last :data:`SYNC_MINUTES`."""
    moment = now or kit.utcnow()
    cutoff = kit.iso(moment - timedelta(minutes=SYNC_MINUTES))
    due = [
        row[0]
        for row in connection.execute(
            """SELECT f.staff_id FROM bk_calendar_feeds f JOIN bk_staff s ON s.id = f.staff_id
            WHERE s.active = 1 AND (f.last_attempt_at IS NULL OR f.last_attempt_at <= ?)
            ORDER BY f.last_attempt_at LIMIT 50""",
            (cutoff,),
        )
    ]
    out = {"synced": 0, "failed": 0}
    for staff_id in due:
        result = sync_feed(connection, staff_id, now=moment)
        out["synced" if result["ok"] else "failed"] += 1
    return out


def busy_between(
    connection: sqlite3.Connection, staff_id: str, start: datetime, end: datetime
) -> list[tuple[datetime, datetime]]:
    """A person's calendar busy times overlapping ``[start, end)`` (UTC)."""
    return [
        (kit.parse_time(row[0]), kit.parse_time(row[1]))
        for row in connection.execute(
            "SELECT start_at, end_at FROM bk_calendar_busy WHERE staff_id = ? AND start_at < ? AND end_at > ?",
            (staff_id, kit.iso(end), kit.iso(start)),
        )
    ]


def overview(connection: sqlite3.Connection, *, now: datetime | None = None) -> list[dict[str, Any]]:
    """Every connected calendar with when it was last read and any problem (for the Staff tab)."""
    moment = now or kit.utcnow()
    stale = kit.iso(moment - timedelta(days=STALE_DAYS))
    out = []
    for row in kit.fetch_all(
        connection,
        """SELECT f.*, s.name FROM bk_calendar_feeds f JOIN bk_staff s ON s.id = f.staff_id ORDER BY s.name""",
    ):
        view: Mapping[str, Any] = store.calendar_feed(connection, row["staff_id"]) or {}
        out.append(
            {
                **view,
                "staff_id": row["staff_id"],
                "name": row["name"],
                "stale": bool(row["last_synced_at"]) and row["last_synced_at"] < stale,
            }
        )
    return out
