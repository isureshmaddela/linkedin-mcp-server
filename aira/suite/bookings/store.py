"""Bookings tables and the plain create/read/update/delete of services, staff, hours, time off and packages."""

from __future__ import annotations

import re
import sqlite3
from collections.abc import Mapping
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from aira.suite import kit

APP = "bookings"
STATUSES = ("booked", "confirmed", "completed", "cancelled", "no_show")
ACTIVE_STATUSES = ("booked", "confirmed")  # appointments that hold their time
WEEKDAYS = (
    "Monday",
    "Tuesday",
    "Wednesday",
    "Thursday",
    "Friday",
    "Saturday",
    "Sunday",
)

SCHEMA = (
    """CREATE TABLE IF NOT EXISTS bk_services (
        id TEXT PRIMARY KEY,
        name TEXT NOT NULL,
        category TEXT NOT NULL DEFAULT '',
        description TEXT NOT NULL DEFAULT '',
        minutes INTEGER NOT NULL,
        buffer_minutes INTEGER NOT NULL DEFAULT 0,
        price_cents INTEGER NOT NULL DEFAULT 0,
        deposit_cents INTEGER NOT NULL DEFAULT 0,
        active INTEGER NOT NULL DEFAULT 1,
        position INTEGER NOT NULL DEFAULT 0,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL
    )""",
    """CREATE TABLE IF NOT EXISTS bk_staff (
        id TEXT PRIMARY KEY,
        name TEXT NOT NULL,
        email TEXT NOT NULL DEFAULT '',
        color TEXT NOT NULL DEFAULT '#2563eb',
        active INTEGER NOT NULL DEFAULT 1,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL
    )""",
    """CREATE TABLE IF NOT EXISTS bk_service_staff (
        service_id TEXT NOT NULL,
        staff_id TEXT NOT NULL,
        PRIMARY KEY (service_id, staff_id)
    )""",
    """CREATE TABLE IF NOT EXISTS bk_hours (
        id TEXT PRIMARY KEY,
        staff_id TEXT NOT NULL,
        weekday INTEGER NOT NULL,
        start TEXT NOT NULL,
        end TEXT NOT NULL
    )""",
    "CREATE INDEX IF NOT EXISTS bk_hours_staff ON bk_hours (staff_id, weekday)",
    """CREATE TABLE IF NOT EXISTS bk_time_off (
        id TEXT PRIMARY KEY,
        staff_id TEXT NOT NULL DEFAULT '',
        start_day TEXT NOT NULL,
        end_day TEXT NOT NULL,
        reason TEXT NOT NULL DEFAULT '',
        created_at TEXT NOT NULL
    )""",
    """CREATE TABLE IF NOT EXISTS bk_customers (
        id TEXT PRIMARY KEY,
        name TEXT NOT NULL,
        email TEXT NOT NULL DEFAULT '',
        phone TEXT NOT NULL DEFAULT '',
        notes TEXT NOT NULL DEFAULT '',
        tags TEXT NOT NULL DEFAULT '[]',
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL
    )""",
    "CREATE INDEX IF NOT EXISTS bk_customers_email ON bk_customers (email)",
    """CREATE TABLE IF NOT EXISTS bk_bookings (
        id TEXT PRIMARY KEY,
        reference TEXT NOT NULL UNIQUE,
        service_id TEXT NOT NULL,
        staff_id TEXT NOT NULL,
        customer_id TEXT NOT NULL,
        service_name TEXT NOT NULL DEFAULT '',
        staff_name TEXT NOT NULL DEFAULT '',
        customer_name TEXT NOT NULL DEFAULT '',
        customer_email TEXT NOT NULL DEFAULT '',
        customer_phone TEXT NOT NULL DEFAULT '',
        start_at TEXT NOT NULL,
        end_at TEXT NOT NULL,
        busy_until TEXT NOT NULL,
        status TEXT NOT NULL DEFAULT 'booked',
        price_cents INTEGER NOT NULL DEFAULT 0,
        deposit_cents INTEGER NOT NULL DEFAULT 0,
        paid_cents INTEGER NOT NULL DEFAULT 0,
        paid_at TEXT,
        payment_kind TEXT NOT NULL DEFAULT '',
        customer_package_id TEXT NOT NULL DEFAULT '',
        source TEXT NOT NULL DEFAULT 'manual',
        note TEXT NOT NULL DEFAULT '',
        internal_note TEXT NOT NULL DEFAULT '',
        cancel_reason TEXT NOT NULL DEFAULT '',
        cancelled_at TEXT,
        completed_at TEXT,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL
    )""",
    "CREATE INDEX IF NOT EXISTS bk_bookings_start ON bk_bookings (start_at)",
    "CREATE INDEX IF NOT EXISTS bk_bookings_staff ON bk_bookings (staff_id, start_at)",
    "CREATE INDEX IF NOT EXISTS bk_bookings_customer ON bk_bookings (customer_id)",
    """CREATE TABLE IF NOT EXISTS bk_packages (
        id TEXT PRIMARY KEY,
        name TEXT NOT NULL,
        service_id TEXT NOT NULL DEFAULT '',
        sessions INTEGER NOT NULL,
        price_cents INTEGER NOT NULL DEFAULT 0,
        valid_days INTEGER NOT NULL DEFAULT 0,
        active INTEGER NOT NULL DEFAULT 1,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL
    )""",
    """CREATE TABLE IF NOT EXISTS bk_customer_packages (
        id TEXT PRIMARY KEY,
        package_id TEXT NOT NULL,
        customer_id TEXT NOT NULL,
        name TEXT NOT NULL,
        service_id TEXT NOT NULL DEFAULT '',
        sessions_total INTEGER NOT NULL,
        sessions_used INTEGER NOT NULL DEFAULT 0,
        price_cents INTEGER NOT NULL DEFAULT 0,
        status TEXT NOT NULL DEFAULT 'active',
        paid_at TEXT,
        expires_on TEXT NOT NULL DEFAULT '',
        created_at TEXT NOT NULL
    )""",
    "CREATE INDEX IF NOT EXISTS bk_customer_packages_customer ON bk_customer_packages (customer_id)",
)


def cents_field(
    value: Any, name: str, *, default: int | None = 0, minimum: int = 0
) -> int:
    """A ``*_cents`` field: integer cents, as the app's forms send them (``kit.cents`` parses "12.50")."""
    return kit.integer(
        value, name, default=default, minimum=minimum, maximum=1_000_000_000
    )


def ensure(connection: sqlite3.Connection) -> None:
    kit.ensure_core(connection)
    kit.ensure(connection, "bookings.v1", SCHEMA)


# ---------------------------------------------------------------------------
# rules
# ---------------------------------------------------------------------------

RULE_DEFAULTS: dict[str, Any] = {
    "slot_interval": 15,  # minutes between possible start times
    "min_notice_hours": 2,  # how soon a customer may book
    "max_days_ahead": 60,  # how far ahead
    "cancel_notice_hours": 24,  # customers may cancel or reschedule until this long before
    "timezone": "",  # empty: the business profile's time zone
    "auto_confirm": True,  # public bookings start confirmed (otherwise "booked", for you to confirm)
    "max_active_per_customer": 3,  # upcoming bookings one email may hold from the public page
    "review_link": "",  # where a review request sends customers (Google, Yelp, ...)
    "welcome": "",  # a line at the top of the booking page
}


def zone_name(connection: sqlite3.Connection) -> str:
    rules = get_rules(connection)
    return rules["timezone"] or kit.business(connection).get("timezone") or "UTC"


def zone(connection: sqlite3.Connection) -> ZoneInfo:
    try:
        return ZoneInfo(zone_name(connection))
    except (ZoneInfoNotFoundError, ValueError):
        return ZoneInfo("UTC")


def _check_zone(name: str) -> str:
    if not name:
        return ""
    try:
        ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError):
        raise ValueError(
            f"{name} is not a time zone (use a name like Europe/London)."
        ) from None
    return name


def get_rules(connection: sqlite3.Connection) -> dict[str, Any]:
    stored = kit.get_setting(connection, APP, "rules", {}) or {}
    return {**RULE_DEFAULTS, **{k: v for k, v in stored.items() if k in RULE_DEFAULTS}}


def save_rules(
    connection: sqlite3.Connection, raw: Mapping[str, Any]
) -> dict[str, Any]:
    merged = {
        **get_rules(connection),
        **{k: v for k, v in raw.items() if k in RULE_DEFAULTS},
    }
    clean = {
        "slot_interval": kit.integer(
            merged["slot_interval"],
            "the slot interval",
            default=15,
            minimum=5,
            maximum=240,
        ),
        "min_notice_hours": kit.integer(
            merged["min_notice_hours"],
            "the minimum notice",
            default=0,
            minimum=0,
            maximum=24 * 30,
        ),
        "max_days_ahead": kit.integer(
            merged["max_days_ahead"],
            "how far ahead customers may book",
            default=60,
            minimum=1,
            maximum=365,
        ),
        "cancel_notice_hours": kit.integer(
            merged["cancel_notice_hours"],
            "the cancellation notice",
            default=24,
            minimum=0,
            maximum=24 * 30,
        ),
        "timezone": _check_zone(
            kit.text(merged["timezone"], "a time zone", max_len=60)
        ),
        "auto_confirm": kit.flag(merged["auto_confirm"]),
        "max_active_per_customer": kit.integer(
            merged["max_active_per_customer"],
            "the bookings per customer",
            default=3,
            minimum=1,
            maximum=50,
        ),
        "review_link": kit.url(merged["review_link"], "the review link"),
        "welcome": kit.text(merged["welcome"], "the welcome line", max_len=300),
    }
    kit.set_setting(connection, APP, "rules", clean)
    connection.commit()
    return clean


# ---------------------------------------------------------------------------
# services
# ---------------------------------------------------------------------------


def _service_values(
    raw: Mapping[str, Any], current: Mapping[str, Any] | None = None
) -> dict[str, Any]:
    base = dict(current or {})
    merged = {**base, **raw}
    price = cents_field(merged.get("price_cents"), "the price")
    deposit = cents_field(merged.get("deposit_cents"), "the deposit")
    if deposit > price and price > 0:
        raise ValueError("The deposit cannot be more than the price.")
    return {
        "name": kit.text(
            merged.get("name"), "the service name", max_len=120, required=True
        ),
        "category": kit.text(merged.get("category"), "a category", max_len=60),
        "description": kit.text(
            merged.get("description"), "a description", max_len=1000, multiline=True
        ),
        "minutes": kit.integer(
            merged.get("minutes"),
            "the length in minutes",
            default=60,
            minimum=5,
            maximum=12 * 60,
        ),
        "buffer_minutes": kit.integer(
            merged.get("buffer_minutes"),
            "the buffer",
            default=0,
            minimum=0,
            maximum=240,
        ),
        "price_cents": price,
        "deposit_cents": deposit,
        "active": 1 if kit.flag(merged.get("active", True)) else 0,
        "position": kit.integer(
            merged.get("position"), "the position", default=0, minimum=0, maximum=10000
        ),
    }


def _staff_ids(connection: sqlite3.Connection, raw: Any) -> list[str]:
    ids = []
    for item in raw if isinstance(raw, list) else []:
        staff_id = str(item)
        kit.require_row(connection, "bk_staff", staff_id, "person")
        if staff_id not in ids:
            ids.append(staff_id)
    return ids


def _set_service_staff(
    connection: sqlite3.Connection, service_id: str, staff_ids: list[str]
) -> None:
    connection.execute(
        "DELETE FROM bk_service_staff WHERE service_id = ?", (service_id,)
    )
    for staff_id in staff_ids:
        connection.execute(
            "INSERT INTO bk_service_staff (service_id, staff_id) VALUES (?, ?)",
            (service_id, staff_id),
        )


def service_staff(connection: sqlite3.Connection, service_id: str) -> list[str]:
    return [
        row[0]
        for row in connection.execute(
            "SELECT staff_id FROM bk_service_staff WHERE service_id = ?", (service_id,)
        )
    ]


def _service_out(connection: sqlite3.Connection, row: dict[str, Any]) -> dict[str, Any]:
    row["active"] = bool(row["active"])
    row["staff_ids"] = service_staff(connection, row["id"])
    return row


def list_services(
    connection: sqlite3.Connection, *, active_only: bool = False
) -> list[dict[str, Any]]:
    where = "WHERE active = 1" if active_only else ""
    rows = kit.fetch_all(
        connection,
        f"SELECT * FROM bk_services {where} ORDER BY position, category, name",  # noqa: S608
    )
    return [_service_out(connection, row) for row in rows]


def get_service(connection: sqlite3.Connection, service_id: str) -> dict[str, Any]:
    return _service_out(
        connection, kit.require_row(connection, "bk_services", service_id, "service")
    )


def create_service(
    connection: sqlite3.Connection, raw: Mapping[str, Any]
) -> dict[str, Any]:
    values = _service_values(raw)
    staff_ids = _staff_ids(connection, raw.get("staff_ids"))
    service_id = kit.new_id()
    now = kit.now()
    connection.execute(
        """INSERT INTO bk_services (id, name, category, description, minutes, buffer_minutes, price_cents, deposit_cents,
        active, position, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            service_id,
            values["name"],
            values["category"],
            values["description"],
            values["minutes"],
            values["buffer_minutes"],
            values["price_cents"],
            values["deposit_cents"],
            values["active"],
            values["position"],
            now,
            now,
        ),
    )
    _set_service_staff(connection, service_id, staff_ids)
    connection.commit()
    return get_service(connection, service_id)


def update_service(
    connection: sqlite3.Connection, service_id: str, raw: Mapping[str, Any]
) -> dict[str, Any]:
    current = kit.require_row(connection, "bk_services", service_id, "service")
    values = _service_values(raw, current)
    values["updated_at"] = kit.now()
    kit.update_row(connection, "bk_services", service_id, values)
    if "staff_ids" in raw:
        _set_service_staff(
            connection, service_id, _staff_ids(connection, raw.get("staff_ids"))
        )
    connection.commit()
    return get_service(connection, service_id)


def delete_service(connection: sqlite3.Connection, service_id: str) -> dict[str, Any]:
    """A service with bookings is deactivated (its history stays); one never booked is removed."""
    kit.require_row(connection, "bk_services", service_id, "service")
    used = kit.count(
        connection,
        "SELECT COUNT(*) FROM bk_bookings WHERE service_id = ?",
        (service_id,),
    )
    if used:
        connection.execute(
            "UPDATE bk_services SET active = 0, updated_at = ? WHERE id = ?",
            (kit.now(), service_id),
        )
        connection.commit()
        return {"deleted": False, "deactivated": True}
    connection.execute("DELETE FROM bk_services WHERE id = ?", (service_id,))
    connection.execute(
        "DELETE FROM bk_service_staff WHERE service_id = ?", (service_id,)
    )
    connection.commit()
    return {"deleted": True, "deactivated": False}


# ---------------------------------------------------------------------------
# staff, hours, time off
# ---------------------------------------------------------------------------

_HHMM = re.compile(r"^([01]\d|2[0-3]):([0-5]\d)$|^24:00$")


def hhmm(value: Any, name: str) -> str:
    text = kit.text(value, name, max_len=5, required=True)
    if len(text) == 4 and text[1] == ":":
        text = "0" + text
    if not _HHMM.match(text):
        raise ValueError(f"{name[:1].upper()}{name[1:]} must be a time like 09:00.")
    return text


def minutes_of(value: str) -> int:
    hours, minutes = value.split(":")
    return int(hours) * 60 + int(minutes)


def _staff_values(
    raw: Mapping[str, Any], current: Mapping[str, Any] | None = None
) -> dict[str, Any]:
    merged = {**dict(current or {}), **raw}
    return {
        "name": kit.text(merged.get("name"), "the name", max_len=120, required=True),
        "email": kit.email(merged.get("email")),
        "color": kit.color(merged.get("color")),
        "active": 1 if kit.flag(merged.get("active", True)) else 0,
    }


def _staff_out(connection: sqlite3.Connection, row: dict[str, Any]) -> dict[str, Any]:
    row["active"] = bool(row["active"])
    row["hours"] = get_hours(connection, row["id"])
    return row


def list_staff(
    connection: sqlite3.Connection, *, active_only: bool = False
) -> list[dict[str, Any]]:
    where = "WHERE active = 1" if active_only else ""
    rows = kit.fetch_all(connection, f"SELECT * FROM bk_staff {where} ORDER BY name")  # noqa: S608
    return [_staff_out(connection, row) for row in rows]


def get_staff(connection: sqlite3.Connection, staff_id: str) -> dict[str, Any]:
    return _staff_out(
        connection, kit.require_row(connection, "bk_staff", staff_id, "person")
    )


def create_staff(
    connection: sqlite3.Connection, raw: Mapping[str, Any]
) -> dict[str, Any]:
    values = _staff_values(raw)
    staff_id = kit.new_id()
    now = kit.now()
    connection.execute(
        "INSERT INTO bk_staff (id, name, email, color, active, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
        (
            staff_id,
            values["name"],
            values["email"],
            values["color"],
            values["active"],
            now,
            now,
        ),
    )
    if "hours" in raw:
        set_hours(connection, staff_id, raw.get("hours"))
    connection.commit()
    return get_staff(connection, staff_id)


def update_staff(
    connection: sqlite3.Connection, staff_id: str, raw: Mapping[str, Any]
) -> dict[str, Any]:
    current = kit.require_row(connection, "bk_staff", staff_id, "person")
    values = _staff_values(raw, current)
    values["updated_at"] = kit.now()
    kit.update_row(connection, "bk_staff", staff_id, values)
    if "hours" in raw:
        set_hours(connection, staff_id, raw.get("hours"))
    connection.commit()
    return get_staff(connection, staff_id)


def delete_staff(connection: sqlite3.Connection, staff_id: str) -> dict[str, Any]:
    kit.require_row(connection, "bk_staff", staff_id, "person")
    upcoming = kit.count(
        connection,
        "SELECT COUNT(*) FROM bk_bookings WHERE staff_id = ? AND status IN ('booked', 'confirmed') AND start_at > ?",
        (staff_id, kit.now()),
    )
    if upcoming:
        raise ValueError(
            f"This person has {upcoming} upcoming booking{'s' if upcoming != 1 else ''}. Move or cancel them first, or mark the person inactive."
        )
    used = kit.count(
        connection, "SELECT COUNT(*) FROM bk_bookings WHERE staff_id = ?", (staff_id,)
    )
    if used:
        connection.execute(
            "UPDATE bk_staff SET active = 0, updated_at = ? WHERE id = ?",
            (kit.now(), staff_id),
        )
        connection.commit()
        return {"deleted": False, "deactivated": True}
    for table, column in (
        ("bk_staff", "id"),
        ("bk_hours", "staff_id"),
        ("bk_service_staff", "staff_id"),
        ("bk_time_off", "staff_id"),
    ):
        connection.execute(f"DELETE FROM {table} WHERE {column} = ?", (staff_id,))  # noqa: S608 - fixed names
    connection.commit()
    return {"deleted": True, "deactivated": False}


def get_hours(connection: sqlite3.Connection, staff_id: str) -> list[dict[str, Any]]:
    return kit.fetch_all(
        connection,
        "SELECT weekday, start, end FROM bk_hours WHERE staff_id = ? ORDER BY weekday, start",
        (staff_id,),
    )


def set_hours(
    connection: sqlite3.Connection, staff_id: str, raw: Any
) -> list[dict[str, Any]]:
    """Replace a person's weekly hours: ``[{"weekday": 0-6 (Monday 0), "start": "09:00", "end": "17:00"}, ...]``."""
    kit.require_row(connection, "bk_staff", staff_id, "person")
    if not isinstance(raw, list):
        raise ValueError("Send the hours as a list of {weekday, start, end}.")
    ranges: list[tuple[int, str, str]] = []
    for item in raw[:100]:
        if not isinstance(item, Mapping):
            raise ValueError("Each range needs a weekday, a start and an end.")
        weekday = kit.integer(item.get("weekday"), "the weekday", minimum=0, maximum=6)
        start = hhmm(item.get("start"), "the start time")
        end = hhmm(item.get("end"), "the end time")
        if minutes_of(end) <= minutes_of(start):
            raise ValueError(
                f"On {WEEKDAYS[weekday]}, the end ({end}) must be after the start ({start})."
            )
        for other_day, other_start, other_end in ranges:
            if (
                other_day == weekday
                and minutes_of(start) < minutes_of(other_end)
                and minutes_of(other_start) < minutes_of(end)
            ):
                raise ValueError(
                    f"On {WEEKDAYS[weekday]}, {start}-{end} overlaps {other_start}-{other_end}."
                )
        ranges.append((weekday, start, end))
    connection.execute("DELETE FROM bk_hours WHERE staff_id = ?", (staff_id,))
    for weekday, start, end in ranges:
        connection.execute(
            "INSERT INTO bk_hours (id, staff_id, weekday, start, end) VALUES (?, ?, ?, ?, ?)",
            (kit.new_id(), staff_id, weekday, start, end),
        )
    connection.commit()
    return get_hours(connection, staff_id)


def list_time_off(
    connection: sqlite3.Connection, *, from_day: str = ""
) -> list[dict[str, Any]]:
    if from_day:
        return kit.fetch_all(
            connection,
            "SELECT * FROM bk_time_off WHERE end_day >= ? ORDER BY start_day",
            (from_day,),
        )
    return kit.fetch_all(
        connection, "SELECT * FROM bk_time_off ORDER BY start_day DESC"
    )


def add_time_off(
    connection: sqlite3.Connection, raw: Mapping[str, Any]
) -> dict[str, Any]:
    staff_id = str(raw.get("staff_id") or "")
    if staff_id:
        kit.require_row(connection, "bk_staff", staff_id, "person")
    start = kit.day(raw.get("start_day"), "the first day", required=True)
    end = kit.day(raw.get("end_day") or start, "the last day")
    if end < start:
        raise ValueError("The last day must be on or after the first day.")
    entry = {
        "id": kit.new_id(),
        "staff_id": staff_id,
        "start_day": start,
        "end_day": end,
        "reason": kit.text(raw.get("reason"), "a reason", max_len=200),
        "created_at": kit.now(),
    }
    connection.execute(
        "INSERT INTO bk_time_off (id, staff_id, start_day, end_day, reason, created_at) VALUES (:id, :staff_id, :start_day, :end_day, :reason, :created_at)",
        entry,
    )
    connection.commit()
    return entry


def delete_time_off(connection: sqlite3.Connection, entry_id: str) -> None:
    kit.require_row(connection, "bk_time_off", entry_id, "time off")
    connection.execute("DELETE FROM bk_time_off WHERE id = ?", (entry_id,))
    connection.commit()


# ---------------------------------------------------------------------------
# packages (what is sold; customer packages live in service.py)
# ---------------------------------------------------------------------------


def _package_values(
    connection: sqlite3.Connection,
    raw: Mapping[str, Any],
    current: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    merged = {**dict(current or {}), **raw}
    service_id = str(merged.get("service_id") or "")
    if service_id:
        kit.require_row(connection, "bk_services", service_id, "service")
    return {
        "name": kit.text(
            merged.get("name"), "the package name", max_len=120, required=True
        ),
        "service_id": service_id,
        "sessions": kit.integer(
            merged.get("sessions"),
            "the number of sessions",
            default=5,
            minimum=1,
            maximum=500,
        ),
        "price_cents": cents_field(merged.get("price_cents"), "the price"),
        "valid_days": kit.integer(
            merged.get("valid_days"),
            "how many days it is valid",
            default=0,
            minimum=0,
            maximum=3650,
        ),
        "active": 1 if kit.flag(merged.get("active", True)) else 0,
    }


def list_packages(
    connection: sqlite3.Connection, *, active_only: bool = False
) -> list[dict[str, Any]]:
    where = "WHERE active = 1" if active_only else ""
    rows = kit.fetch_all(connection, f"SELECT * FROM bk_packages {where} ORDER BY name")  # noqa: S608
    for row in rows:
        row["active"] = bool(row["active"])
        row["sold"] = kit.count(
            connection,
            "SELECT COUNT(*) FROM bk_customer_packages WHERE package_id = ?",
            (row["id"],),
        )
    return rows


def create_package(
    connection: sqlite3.Connection, raw: Mapping[str, Any]
) -> dict[str, Any]:
    values = _package_values(connection, raw)
    package_id = kit.new_id()
    now = kit.now()
    connection.execute(
        """INSERT INTO bk_packages (id, name, service_id, sessions, price_cents, valid_days, active, created_at, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            package_id,
            values["name"],
            values["service_id"],
            values["sessions"],
            values["price_cents"],
            values["valid_days"],
            values["active"],
            now,
            now,
        ),
    )
    connection.commit()
    return kit.require_row(connection, "bk_packages", package_id, "package")


def update_package(
    connection: sqlite3.Connection, package_id: str, raw: Mapping[str, Any]
) -> dict[str, Any]:
    current = kit.require_row(connection, "bk_packages", package_id, "package")
    values = _package_values(connection, raw, current)
    values["updated_at"] = kit.now()
    kit.update_row(connection, "bk_packages", package_id, values)
    connection.commit()
    return kit.require_row(connection, "bk_packages", package_id, "package")


def delete_package(connection: sqlite3.Connection, package_id: str) -> dict[str, Any]:
    kit.require_row(connection, "bk_packages", package_id, "package")
    if kit.count(
        connection,
        "SELECT COUNT(*) FROM bk_customer_packages WHERE package_id = ?",
        (package_id,),
    ):
        connection.execute(
            "UPDATE bk_packages SET active = 0, updated_at = ? WHERE id = ?",
            (kit.now(), package_id),
        )
        connection.commit()
        return {"deleted": False, "deactivated": True}
    connection.execute("DELETE FROM bk_packages WHERE id = ?", (package_id,))
    connection.commit()
    return {"deleted": True, "deactivated": False}
