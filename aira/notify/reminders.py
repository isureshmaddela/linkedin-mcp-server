"""Reminders made on the server, so they arrive with every tab closed: a dose that is due, an appointment a staff
member is about to give, a Careers follow-up or interview, a stock price alert that fired.

:func:`run` checks one database (an account, an organization's apps, a member's own space). It only reads the
tables an app has already made and never creates them. Every reminder has a ``key``, so running it every minute
(the single-user scheduler) and again in the five-minute suite tick tells each person once.

Missed doses are told to caregivers by Care itself (``care.service.notify_missed``), which calls
:func:`aira.notify.notify` for caregivers in the household.
"""

from __future__ import annotations

import logging
import sqlite3
from collections.abc import Callable
from datetime import datetime, timedelta, timezone
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from aira import notify, suite
from aira.notify import store

logger = logging.getLogger("aira.notify")

SOON = timedelta(
    minutes=60
)  # an interview or an appointment this close gets its reminder
CATCH_UP = timedelta(
    days=2
)  # a follow-up due longer ago than this was seen in the app already


def _tables(connection: sqlite3.Connection, *names: str) -> bool:
    found = {
        row[0]
        for row in connection.execute(
            f"SELECT name FROM sqlite_master WHERE type = 'table' AND name IN ({','.join('?' for _ in names)})",  # noqa: S608
            names,
        )
    }
    return found == set(names)


def _when(value: Any) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def _clock(connection: sqlite3.Connection, key: str, moment: datetime) -> str:
    """``moment`` as HH:MM in the person's time zone."""
    zone = store.zone_name(connection, store.prefs(connection, key))
    try:
        return moment.astimezone(ZoneInfo(zone)).strftime("%H:%M")
    except (ZoneInfoNotFoundError, ValueError):
        return moment.strftime("%H:%M UTC")


def _profiles(connection: sqlite3.Connection) -> list[dict[str, Any]]:
    if not _tables(connection, "life_profiles"):
        return []
    from aira.suite import kit

    return kit.fetch_all(
        connection,
        "SELECT * FROM life_profiles WHERE archived_at IS NULL ORDER BY created_at",
    )


# ---------------------------------------------------------------------------
# Health: a dose that is due
# ---------------------------------------------------------------------------


def medicines(connection: sqlite3.Connection, now: datetime) -> int:
    if not suite.app_enabled("care") or not _tables(
        connection, "cr_medicines", "life_profiles"
    ):
        return 0
    from aira.suite.care import service as care

    owners = {
        row[0]
        for row in connection.execute(
            "SELECT DISTINCT owner_id FROM cr_medicines WHERE active = 1 AND as_needed = 0"
        )
    }
    told = 0
    for person in _profiles(connection):
        if person["id"] not in owners:
            continue
        today = care.local_today(connection, now)
        for slot in care.day_slots(connection, person["id"], today, now):
            if slot["status"] != "due":
                continue
            item = notify.notify(
                connection,
                person,
                "medicine",
                "Time for your medicine",
                f"A scheduled medicine is due ({slot['time']}).",
                "/#/care",
                urgency="high",
                detail=f"{slot['name']} {slot.get('dose') or ''}".strip()
                + (f". {slot['with_food']}" if slot.get("with_food") else ""),
                key=f"dose:{slot['medicine_id']}:{slot['date']}:{slot['time']}",
                email=False,  # the push and the app; a missed dose is what caregivers get by email
                now=now,
            )
            told += item is not None
    return told


def _has_column(connection: sqlite3.Connection, table: str, column: str) -> bool:
    return any(
        row[1] == column for row in connection.execute(f"PRAGMA table_info({table})")
    )


# ---------------------------------------------------------------------------
# Bookings: the staff member who gives the appointment
# ---------------------------------------------------------------------------


def bookings(connection: sqlite3.Connection, now: datetime) -> int:
    """An hour before each appointment its staff member hears about it: in the app when they have a profile here
    (matched by email), by email otherwise. Customers have their own reminder email the day before."""
    if not suite.app_enabled("bookings") or not _tables(
        connection, "bk_bookings", "bk_staff"
    ):
        return 0
    from aira.suite import kit

    rows = kit.fetch_all(
        connection,
        """SELECT b.id, b.reference, b.service_name, b.customer_name, b.start_at, b.staff_id, s.name AS staff_name, s.email AS staff_email
        FROM bk_bookings b JOIN bk_staff s ON s.id = b.staff_id
        WHERE b.status IN ('booked', 'confirmed') AND b.start_at > ? AND b.start_at <= ?""",
        (kit.iso(now), kit.iso(now + SOON)),
    )
    if not rows:
        return 0
    people = {
        str(person.get("email") or "").lower(): person
        for person in _profiles(connection)
        if person.get("email")
    }
    told = 0
    for booking in rows:
        start = _when(booking["start_at"])
        if start is None:
            continue
        first = str(booking["customer_name"] or "a customer").split(" ")[0]
        email = str(booking["staff_email"] or "").lower()
        person = people.get(email) if email else None
        if person is not None:
            at = _clock(connection, store.person_key(person), start)
            item = notify.notify(
                connection,
                person,
                "bookings",
                f"Booking at {at}",
                f"{booking['service_name']} with {first}.",
                "/#/bookings",
                detail=f"{booking['customer_name']} · {booking['reference']}",
                key=f"booking:{booking['id']}:{booking['start_at']}:staff",
                now=now,
            )
            told += item is not None
        elif email:
            told += _staff_email(connection, booking, start, first)
    return told


def _staff_email(
    connection: sqlite3.Connection, booking: dict[str, Any], start: datetime, first: str
) -> int:
    from aira.suite import kit

    if kit.fetch_one(
        connection,
        "SELECT 1 AS x FROM suite_outbox WHERE kind = 'booking_staff_reminder' AND ref_id = ? AND body LIKE ?",
        (booking["id"], f"%{booking['start_at']}%"),
    ):
        return 0
    try:
        from aira.suite.bookings import store as bookings_store

        local = start.astimezone(bookings_store.zone(connection)).strftime("%A %H:%M")
    except Exception:  # noqa: BLE001
        local = start.strftime("%A %H:%M UTC")
    message = kit.queue_email(
        connection,
        "bookings",
        "booking_staff_reminder",
        to=booking["staff_email"],
        subject=f"In an hour: {booking['service_name']} with {first}",
        body=(
            f"Hello {booking['staff_name']},\n\n{booking['service_name']} with {booking['customer_name']} "
            f"starts {local} ({booking['reference']}).\n\nStarts at: {booking['start_at']}"
        ),
        ref_type="booking",
        ref_id=booking["id"],
    )
    if message and message["status"] != "approved":
        # a reminder to the business's own staff that waited for approval would arrive after the appointment
        connection.execute(
            "UPDATE suite_outbox SET status = 'approved', approved_at = ? WHERE id = ?",
            (kit.now(), message["id"]),
        )
    connection.commit()
    return 1 if message else 0


# ---------------------------------------------------------------------------
# Careers: follow-ups and interviews (the account owner's job search)
# ---------------------------------------------------------------------------


def careers(connection: sqlite3.Connection, now: datetime) -> int:
    """Due follow-ups (tasks and an application's follow-up date) and an interview about to start. The push and
    email never name the employer: a job search is often private from the person's current one."""
    if not _tables(connection, "tasks", "jobs"):
        return 0
    from aira.suite import kit

    told = 0
    for task in kit.fetch_all(
        connection,
        "SELECT task_id, kind, title, due_at, interview_id FROM tasks WHERE status = 'open' AND due_at IS NOT NULL",
    ):
        due = _when(task["due_at"])
        if due is None or due > now or now - due > CATCH_UP:
            continue
        prep = task["kind"] == "interview_prep"
        item = notify.notify(
            connection,
            "owner",
            "careers",
            "Prepare for your interview" if prep else "Follow-up due",
            "An interview you have coming up: time to prepare."
            if prep
            else "Something you planned in your job search is due.",
            "/#/interviews" if task["interview_id"] else "/#/dashboard",
            detail=str(task["title"] or ""),
            key=f"task:{task['task_id']}:{task['due_at']}",
            now=now,
        )
        told += item is not None
    if _has_column(connection, "jobs", "follow_up_at"):
        archived = (
            " AND archived_at IS NULL"
            if _has_column(connection, "jobs", "archived_at")
            else ""
        )
        for job in kit.fetch_all(
            connection,
            f"SELECT job_id, title, company, follow_up_at FROM jobs WHERE follow_up_at IS NOT NULL AND follow_up_at != ''{archived}",  # noqa: S608
        ):
            due = _when(job["follow_up_at"])
            if due is None or due > now or now - due > CATCH_UP:
                continue
            item = notify.notify(
                connection,
                "owner",
                "careers",
                "Follow up on an application",
                "An application you are tracking is due for a follow-up.",
                "/#/applications",
                detail=f"{job['title']} · {job['company']}".strip(" ·"),
                key=f"job:{job['job_id']}:{job['follow_up_at']}",
                now=now,
            )
            told += item is not None
    if _tables(connection, "interviews"):
        for interview in kit.fetch_all(
            connection,
            "SELECT interview_id, company, role, interview_type, start_at, meeting_link FROM interviews WHERE status = 'scheduled'",
        ):
            start = _when(interview["start_at"])
            if start is None or not now < start <= now + SOON:
                continue
            key = store.canonical(connection, "owner")
            item = notify.notify(
                connection,
                "owner",
                "careers",
                f"Interview at {_clock(connection, key, start)}",
                "Your interview starts within the hour.",
                "/#/interviews",
                urgency="high",
                detail=" · ".join(
                    part
                    for part in (
                        interview["company"],
                        interview["role"],
                        str(interview["interview_type"] or "").replace("_", " "),
                        interview["meeting_link"],
                    )
                    if part
                ),
                key=f"interview:{interview['interview_id']}:{interview['start_at']}",
                now=now,
            )
            told += item is not None
    return told


# ---------------------------------------------------------------------------
# Stocks: an alert that fired
# ---------------------------------------------------------------------------


def stocks(connection: sqlite3.Connection, now: datetime) -> int:
    if not suite.app_enabled("stocks") or not _tables(
        connection, "stk_alerts", "life_profiles"
    ):
        return 0
    from aira.suite import kit
    from aira.suite.stocks import service as stocks_service

    since = kit.iso(now - timedelta(hours=24))
    rows = kit.fetch_all(
        connection,
        "SELECT * FROM stk_alerts WHERE triggered_at IS NOT NULL AND triggered_at >= ?",
        (since,),
    )
    if not rows:
        return 0
    people = {person["id"]: person for person in _profiles(connection)}
    currencies = stocks_service._meta_currency(
        connection, (row["symbol"] for row in rows)
    )
    told = 0
    for row in rows:
        person = people.get(row["owner_id"])
        if person is None:
            continue
        view = stocks_service._alert_view(row, currencies.get(row["symbol"], ""))
        item = notify.notify(
            connection,
            person,
            "stocks",
            f"Price alert: {row['symbol']}",
            stocks_service.fired_text(view),
            "/#/stocks",
            detail=str(row.get("note") or ""),
            key=f"stock:{row['id']}:{row['triggered_at']}",
            now=now,
        )
        told += item is not None
    return told


CHECKS: tuple[tuple[str, Callable[[sqlite3.Connection, datetime], int]], ...] = (
    ("medicine", medicines),
    ("bookings", bookings),
    ("careers", careers),
    ("stocks", stocks),
)


def run(
    connection: sqlite3.Connection,
    now: datetime | None = None,
    *,
    only: tuple[str, ...] | None = None,
) -> dict[str, Any]:
    """Every reminder this database has; never raises (one failing check leaves the others)."""
    moment = now or datetime.now(timezone.utc)
    out: dict[str, Any] = {}
    for name, check in CHECKS:
        if only is not None and name not in only:
            continue
        try:
            out[name] = check(connection, moment)
            connection.commit()
        except Exception:  # noqa: BLE001
            logger.exception("Reminders for %s failed", name)
            out[name] = "failed"
    if _tables(connection, "nt_items"):
        try:
            out["held"] = notify.flush_held(connection, moment)
        except Exception:  # noqa: BLE001
            logger.exception("Quiet-hours digest failed")
            out["held"] = "failed"
    return out
