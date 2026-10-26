"""Health tables (``cr_*``) and input validation. Every row carries ``owner_id``: the person it belongs to."""

from __future__ import annotations

import re
import sqlite3
from typing import Any

from aira.suite import kit, life

APP = "care"
FORMS = (
    "tablet",
    "capsule",
    "liquid",
    "inhaler",
    "injection",
    "drops",
    "cream",
    "patch",
    "other",
)
WEEKDAYS = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")
BLOOD_TYPES = ("", "A+", "A-", "B+", "B-", "AB+", "AB-", "O+", "O-", "unknown")
MOODS = {1: "😢", 2: "🙁", 3: "😐", 4: "🙂", 5: "😄"}
_TIME = re.compile(r"^([01]?\d|2[0-3]):([0-5]\d)$")

SCHEMA = (
    """CREATE TABLE IF NOT EXISTS cr_medicines (
        id TEXT PRIMARY KEY,
        owner_id TEXT NOT NULL,
        name TEXT NOT NULL,
        dose TEXT NOT NULL DEFAULT '',
        form TEXT NOT NULL DEFAULT 'tablet',
        times TEXT NOT NULL DEFAULT '[]',
        days TEXT NOT NULL DEFAULT '[]',
        start_date TEXT NOT NULL DEFAULT '',
        end_date TEXT NOT NULL DEFAULT '',
        as_needed INTEGER NOT NULL DEFAULT 0,
        min_gap_hours INTEGER NOT NULL DEFAULT 0,
        with_food TEXT NOT NULL DEFAULT '',
        stock INTEGER,
        refill_at INTEGER NOT NULL DEFAULT 0,
        active INTEGER NOT NULL DEFAULT 1,
        notes TEXT NOT NULL DEFAULT '',
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL
    )""",
    "CREATE INDEX IF NOT EXISTS cr_medicines_owner ON cr_medicines (owner_id, active)",
    """CREATE TABLE IF NOT EXISTS cr_doses (
        id TEXT PRIMARY KEY,
        owner_id TEXT NOT NULL,
        medicine_id TEXT NOT NULL,
        slot_date TEXT NOT NULL,
        slot_time TEXT NOT NULL DEFAULT '',
        status TEXT NOT NULL,
        at TEXT NOT NULL,
        reason TEXT NOT NULL DEFAULT '',
        snooze_until TEXT,
        stock_taken INTEGER NOT NULL DEFAULT 0,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL
    )""",
    # One row per scheduled slot; as-needed doses (slot_time '') may repeat.
    "CREATE UNIQUE INDEX IF NOT EXISTS cr_doses_slot ON cr_doses (medicine_id, slot_date, slot_time) WHERE slot_time != ''",
    "CREATE INDEX IF NOT EXISTS cr_doses_owner ON cr_doses (owner_id, slot_date)",
    """CREATE TABLE IF NOT EXISTS cr_caregivers (
        id TEXT PRIMARY KEY,
        owner_id TEXT NOT NULL,
        caregiver_id TEXT,
        email TEXT NOT NULL DEFAULT '',
        name TEXT NOT NULL DEFAULT '',
        notify_missed INTEGER NOT NULL DEFAULT 1,
        created_at TEXT NOT NULL
    )""",
    "CREATE INDEX IF NOT EXISTS cr_caregivers_owner ON cr_caregivers (owner_id)",
    "CREATE INDEX IF NOT EXISTS cr_caregivers_caregiver ON cr_caregivers (caregiver_id)",
    """CREATE TABLE IF NOT EXISTS cr_missed_notices (
        medicine_id TEXT NOT NULL,
        slot_date TEXT NOT NULL,
        slot_time TEXT NOT NULL,
        owner_id TEXT NOT NULL,
        notified_at TEXT NOT NULL,
        PRIMARY KEY (medicine_id, slot_date, slot_time)
    )""",
    """CREATE TABLE IF NOT EXISTS cr_appointments (
        id TEXT PRIMARY KEY,
        owner_id TEXT NOT NULL,
        title TEXT NOT NULL,
        purpose TEXT NOT NULL DEFAULT '',
        place TEXT NOT NULL DEFAULT '',
        at TEXT NOT NULL,
        notes TEXT NOT NULL DEFAULT '',
        questions TEXT NOT NULL DEFAULT '',
        done INTEGER NOT NULL DEFAULT 0,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL
    )""",
    "CREATE INDEX IF NOT EXISTS cr_appointments_owner ON cr_appointments (owner_id, at)",
    """CREATE TABLE IF NOT EXISTS cr_readings (
        id TEXT PRIMARY KEY,
        owner_id TEXT NOT NULL,
        kind TEXT NOT NULL,
        value1 REAL NOT NULL,
        value2 REAL,
        value3 REAL,
        unit TEXT NOT NULL DEFAULT '',
        at TEXT NOT NULL,
        note TEXT NOT NULL DEFAULT '',
        created_at TEXT NOT NULL
    )""",
    "CREATE INDEX IF NOT EXISTS cr_readings_owner ON cr_readings (owner_id, kind, at)",
    """CREATE TABLE IF NOT EXISTS cr_diary (
        id TEXT PRIMARY KEY,
        owner_id TEXT NOT NULL,
        day TEXT NOT NULL,
        mood INTEGER NOT NULL,
        symptoms TEXT NOT NULL DEFAULT '[]',
        note TEXT NOT NULL DEFAULT '',
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL,
        UNIQUE (owner_id, day)
    )""",
    """CREATE TABLE IF NOT EXISTS cr_cards (
        owner_id TEXT PRIMARY KEY,
        blood_type TEXT NOT NULL DEFAULT '',
        allergies TEXT NOT NULL DEFAULT '',
        conditions TEXT NOT NULL DEFAULT '',
        contacts TEXT NOT NULL DEFAULT '[]',
        doctor TEXT NOT NULL DEFAULT '',
        doctor_phone TEXT NOT NULL DEFAULT '',
        notes TEXT NOT NULL DEFAULT '',
        updated_at TEXT NOT NULL
    )""",
)


def ensure(connection: sqlite3.Connection) -> None:
    life.ensure(connection)
    kit.ensure(connection, "care.v1", SCHEMA)


# ---------------------------------------------------------------------------
# validation
# ---------------------------------------------------------------------------


def clock(value: Any, name: str = "a time") -> str:
    """``8:00`` / ``08:00`` -> ``08:00``."""
    text = str(value or "").strip()
    match = _TIME.match(text)
    if not match:
        raise ValueError(f"{text or 'The time'} is not a time like 08:00.")
    return f"{int(match.group(1)):02d}:{match.group(2)}"


def times(value: Any) -> list[str]:
    items = (
        value
        if isinstance(value, list)
        else str(value or "").replace(";", ",").split(",")
    )
    out = sorted({clock(item) for item in items if str(item).strip()})
    if len(out) > 12:
        raise ValueError("A medicine can have at most 12 times a day.")
    return out


def days(value: Any) -> list[int]:
    """Days of the week as 0 (Monday) to 6; empty means every day. Accepts numbers or names."""
    items = (
        value
        if isinstance(value, list)
        else str(value or "").replace(";", ",").split(",")
    )
    out: set[int] = set()
    for item in items:
        text = str(item).strip().lower()
        if not text:
            continue
        if text.isdigit() and 0 <= int(text) <= 6:
            out.add(int(text))
        elif text[:3] in WEEKDAYS:
            out.add(WEEKDAYS.index(text[:3]))
        elif text in ("every day", "daily", "all"):
            return []
        else:
            raise ValueError(f"{item} is not a day of the week.")
    return [] if len(out) == 7 else sorted(out)


def clean_medicine(
    raw: dict[str, Any], current: dict[str, Any] | None = None
) -> dict[str, Any]:
    merged = {**(current or {}), **raw}
    as_needed = kit.flag(merged.get("as_needed"))
    values: dict[str, Any] = {
        "name": kit.text(
            merged.get("name"), "the medicine's name", max_len=80, required=True
        ),
        "dose": kit.text(merged.get("dose"), "the dose", max_len=80),
        "form": kit.choice(merged.get("form"), "the form", FORMS, default="tablet"),
        "times": [] if as_needed else times(merged.get("times")),
        "days": days(merged.get("days")),
        "start_date": kit.day(merged.get("start_date"), "the start date"),
        "end_date": kit.day(merged.get("end_date"), "the end date"),
        "as_needed": as_needed,
        "min_gap_hours": kit.integer(
            merged.get("min_gap_hours"),
            "the hours between doses",
            default=0,
            minimum=0,
            maximum=72,
        ),
        "with_food": kit.text(merged.get("with_food"), "the food note", max_len=80),
        "stock": None
        if merged.get("stock") in (None, "")
        else kit.integer(
            merged.get("stock"), "the number left", minimum=0, maximum=100000
        ),
        "refill_at": kit.integer(
            merged.get("refill_at"),
            "when to refill",
            default=0,
            minimum=0,
            maximum=10000,
        ),
        "active": kit.flag(merged.get("active", True)),
        "notes": kit.text(merged.get("notes"), "notes", max_len=500, multiline=True),
    }
    if not as_needed and not values["times"]:
        raise ValueError(
            "Add at least one time of day, or mark the medicine 'as needed'."
        )
    if (
        values["start_date"]
        and values["end_date"]
        and values["end_date"] < values["start_date"]
    ):
        raise ValueError("The end date is before the start date.")
    return values


def medicine_row(row: dict[str, Any]) -> dict[str, Any]:
    row["times"] = kit.loads(row.get("times"), []) or []
    row["days"] = kit.loads(row.get("days"), []) or []
    row["as_needed"] = bool(row.get("as_needed"))
    row["active"] = bool(row.get("active"))
    return row


def get_medicine(
    connection: sqlite3.Connection, medicine_id: str, owner_id: str
) -> dict[str, Any]:
    row = kit.fetch_one(
        connection,
        "SELECT * FROM cr_medicines WHERE id = ? AND owner_id = ?",
        (medicine_id, owner_id),
    )
    if not row:
        raise LookupError("That medicine was not found.")
    return medicine_row(row)


def medicines(
    connection: sqlite3.Connection, owner_id: str, *, active_only: bool = False
) -> list[dict[str, Any]]:
    where = "AND active = 1" if active_only else ""
    rows = kit.fetch_all(
        connection,
        f"SELECT * FROM cr_medicines WHERE owner_id = ? {where} ORDER BY active DESC, name COLLATE NOCASE",  # noqa: S608
        (owner_id,),
    )
    return [medicine_row(row) for row in rows]
