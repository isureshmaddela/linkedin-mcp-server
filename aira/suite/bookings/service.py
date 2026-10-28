"""Bookings logic: available slots, booking with a re-check, cancel and reschedule rules, payments, packages,
customers, the messages customers get, and the overview numbers.

Times are stored as ISO 8601 UTC. Slots are computed in the business's time zone (``zoneinfo``), stepping in
real elapsed time so a day with a clock change still has correct start times.
"""

from __future__ import annotations

import secrets
import sqlite3
from collections.abc import Mapping
from datetime import date, datetime, time, timedelta, timezone
from typing import Any
from zoneinfo import ZoneInfo

from aira.suite import kit, public
from aira.suite.bookings import store
from aira.suite.bookings.store import ACTIVE_STATUSES, APP

UTC = timezone.utc
REFERENCE_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
PAGE_KIND = "booking_page"
BOOKING_KIND = "booking"
PAGE_REF = "main"


# ---------------------------------------------------------------------------
# time helpers
# ---------------------------------------------------------------------------


def local_at(day: date, hhmm: str, tz: ZoneInfo) -> datetime:
    """A wall-clock time on a local day, as a UTC instant (``24:00`` is the next midnight)."""
    hours, minutes = (int(part) for part in hhmm.split(":"))
    if hours == 24:
        return datetime.combine(
            day + timedelta(days=1), time(0, 0), tzinfo=tz
        ).astimezone(UTC)
    return datetime.combine(day, time(hours, minutes), tzinfo=tz).astimezone(UTC)


def _parse(value: str) -> datetime:
    return kit.parse_time(value).astimezone(UTC)


def local_label(value: str, tz: ZoneInfo) -> str:
    moment = _parse(value).astimezone(tz)
    return f"{moment:%A} {moment.day} {moment:%B %Y at %H:%M}"


def local_today(connection: sqlite3.Connection, now: datetime | None = None) -> str:
    """Today in the business's time zone (the calendar's first day when none is asked for)."""
    return (now or kit.utcnow()).astimezone(store.zone(connection)).date().isoformat()


def _local_day_bounds(day: date, tz: ZoneInfo) -> tuple[datetime, datetime]:
    return (
        datetime.combine(day, time(0, 0), tzinfo=tz).astimezone(UTC),
        datetime.combine(day + timedelta(days=1), time(0, 0), tzinfo=tz).astimezone(
            UTC
        ),
    )


# ---------------------------------------------------------------------------
# availability
# ---------------------------------------------------------------------------


def eligible_staff(
    connection: sqlite3.Connection, service: Mapping[str, Any]
) -> list[dict[str, Any]]:
    """Active staff who offer the service (everyone active when the service names nobody)."""
    staff = kit.fetch_all(
        connection,
        "SELECT id, name, color FROM bk_staff WHERE active = 1 ORDER BY name",
    )
    chosen = set(service.get("staff_ids") or [])
    return [person for person in staff if not chosen or person["id"] in chosen]


def _off(connection: sqlite3.Connection, staff_id: str, day: str) -> bool:
    return bool(
        kit.count(
            connection,
            "SELECT COUNT(*) FROM bk_time_off WHERE (staff_id = ? OR staff_id = '') AND start_day <= ? AND end_day >= ?",
            (staff_id, day, day),
        )
    )


def _busy(
    connection: sqlite3.Connection,
    staff_id: str,
    start: datetime,
    end: datetime,
    exclude: str = "",
) -> list[tuple[datetime, datetime]]:
    rows = connection.execute(
        f"""SELECT start_at, busy_until FROM bk_bookings WHERE staff_id = ? AND id != ?
        AND status IN ({",".join("?" for _ in ACTIVE_STATUSES)}) AND start_at < ? AND busy_until > ?""",  # noqa: S608
        (staff_id, exclude, *ACTIVE_STATUSES, kit.iso(end), kit.iso(start)),
    ).fetchall()
    return [(_parse(row[0]), _parse(row[1])) for row in rows]


def slots_for_day(
    connection: sqlite3.Connection,
    service_id: str,
    day: str,
    *,
    staff_id: str = "",
    now: datetime | None = None,
    exclude_booking: str = "",
    customer_rules: bool = True,
    ignore_bookings: bool = False,
) -> list[dict[str, Any]]:
    """Free start times for a service on a local day: inside someone's hours, not on their time off, clear of
    their other bookings (including buffers). ``customer_rules`` adds the minimum notice and the horizon;
    ``ignore_bookings`` gives the times the day would have if nothing were booked (is it full, or closed?)."""
    store.ensure(connection)
    service = store.get_service(connection, service_id)
    if customer_rules and not service["active"]:
        return []
    rules = store.get_rules(connection)
    tz = store.zone(connection)
    local_day = date.fromisoformat(kit.day(day, "the day", required=True))
    moment = (now or kit.utcnow()).astimezone(UTC)
    today = moment.astimezone(tz).date()
    earliest: datetime | None = moment
    if customer_rules:
        if local_day < today or local_day > today + timedelta(
            days=int(rules["max_days_ahead"])
        ):
            return []
        earliest = moment + timedelta(hours=int(rules["min_notice_hours"]))
    staff = eligible_staff(connection, service)
    if staff_id:
        staff = [person for person in staff if person["id"] == staff_id]
    duration = timedelta(minutes=int(service["minutes"]))
    buffer = timedelta(minutes=int(service["buffer_minutes"]))
    step = timedelta(minutes=int(rules["slot_interval"]))
    day_start, day_end = _local_day_bounds(local_day, tz)
    found: dict[datetime, list[str]] = {}
    for person in staff:
        if _off(connection, person["id"], local_day.isoformat()):
            continue
        ranges = kit.fetch_all(
            connection,
            "SELECT start, end FROM bk_hours WHERE staff_id = ? AND weekday = ? ORDER BY start",
            (person["id"], local_day.weekday()),
        )
        if not ranges:
            continue
        busy = (
            []
            if ignore_bookings
            else _busy(
                connection,
                person["id"],
                day_start - timedelta(days=1),
                day_end + timedelta(days=1),
                exclude_booking,
            )
        )
        for hours in ranges:
            cursor = local_at(local_day, hours["start"], tz)
            closes = local_at(local_day, hours["end"], tz)
            while cursor + duration <= closes:
                if earliest is None or cursor >= earliest:
                    held_until = cursor + duration + buffer
                    if not any(
                        cursor < busy_end and busy_start < held_until
                        for busy_start, busy_end in busy
                    ):
                        found.setdefault(cursor, []).append(person["id"])
                cursor += step
    return [
        {
            "start": kit.iso(start),
            "end": kit.iso(start + duration),
            "local": start.astimezone(tz).strftime("%H:%M"),
            "staff_ids": staff_ids,
        }
        for start, staff_ids in sorted(found.items())
    ]


def slots_for_days(
    connection: sqlite3.Connection,
    service_id: str,
    first_day: str,
    days: int = 7,
    **options: Any,
) -> list[dict[str, Any]]:
    start = date.fromisoformat(kit.day(first_day, "the day", required=True))
    return [
        {
            "day": (start + timedelta(days=offset)).isoformat(),
            "slots": slots_for_day(
                connection,
                service_id,
                (start + timedelta(days=offset)).isoformat(),
                **options,
            ),
        }
        for offset in range(max(1, min(int(days), 14)))
    ]


DAY_STATES = ("open", "full", "closed", "past", "not_yet")


def day_states(
    connection: sqlite3.Connection,
    service_id: str,
    first_day: str,
    days: int = 7,
    *,
    staff_id: str = "",
    now: datetime | None = None,
    exclude_booking: str = "",
) -> list[dict[str, Any]]:
    """The days customers see, each with its free times and a ``state``: ``open`` (times to book), ``full``
    (it has times, all booked: the waiting list), ``closed`` (nobody works then, or its times are over or too
    soon), ``past``, or ``not_yet`` (beyond how far ahead customers may book)."""
    rules = store.get_rules(connection)
    today = date.fromisoformat(local_today(connection, now))
    horizon = today + timedelta(days=int(rules["max_days_ahead"]))
    out = []
    for entry in slots_for_days(
        connection,
        service_id,
        first_day,
        days,
        staff_id=staff_id,
        now=now,
        exclude_booking=exclude_booking,
    ):
        day = date.fromisoformat(entry["day"])
        if entry["slots"]:
            state = "open"
        elif day < today:
            state = "past"
        elif day > horizon:
            state = "not_yet"
        elif slots_for_day(
            connection,
            service_id,
            entry["day"],
            staff_id=staff_id,
            now=now,
            ignore_bookings=True,
        ):
            state = "full"
        else:
            state = "closed"
        out.append({**entry, "state": state})
    return out


# ---------------------------------------------------------------------------
# customers
# ---------------------------------------------------------------------------


def upsert_customer(
    connection: sqlite3.Connection, *, name: str, email: str = "", phone: str = ""
) -> dict[str, Any]:
    """The customer with this email (else phone), created on first booking; fills in what was missing."""
    found = None
    if email:
        found = kit.fetch_one(
            connection,
            "SELECT * FROM bk_customers WHERE email = ? ORDER BY created_at LIMIT 1",
            (email,),
        )
    if not found and phone:
        found = kit.fetch_one(
            connection,
            "SELECT * FROM bk_customers WHERE phone = ? ORDER BY created_at LIMIT 1",
            (phone,),
        )
    now = kit.now()
    if found:
        updates = {"updated_at": now}
        if email and not found["email"]:
            updates["email"] = email
        if phone and not found["phone"]:
            updates["phone"] = phone
        if name and not found["name"]:
            updates["name"] = name
        kit.update_row(connection, "bk_customers", found["id"], updates)
        return {**found, **updates}
    customer = {
        "id": kit.new_id(),
        "name": name,
        "email": email,
        "phone": phone,
        "notes": "",
        "tags": "[]",
        "created_at": now,
        "updated_at": now,
    }
    connection.execute(
        "INSERT INTO bk_customers (id, name, email, phone, notes, tags, created_at, updated_at) VALUES (:id, :name, :email, :phone, :notes, :tags, :created_at, :updated_at)",
        customer,
    )
    return customer


_CUSTOMER_STATS = """SELECT c.*,
    (SELECT COUNT(*) FROM bk_bookings b WHERE b.customer_id = c.id) AS bookings,
    (SELECT COUNT(*) FROM bk_bookings b WHERE b.customer_id = c.id AND b.status = 'completed') AS visits,
    (SELECT COUNT(*) FROM bk_bookings b WHERE b.customer_id = c.id AND b.status = 'no_show') AS no_shows,
    (SELECT COALESCE(SUM(b.paid_cents), 0) FROM bk_bookings b WHERE b.customer_id = c.id)
      + (SELECT COALESCE(SUM(p.price_cents), 0) FROM bk_customer_packages p WHERE p.customer_id = c.id AND p.paid_at IS NOT NULL) AS spend_cents,
    (SELECT MAX(b.start_at) FROM bk_bookings b WHERE b.customer_id = c.id AND b.status = 'completed') AS last_visit,
    (SELECT MIN(b.start_at) FROM bk_bookings b WHERE b.customer_id = c.id AND b.status IN ('booked', 'confirmed') AND b.start_at > ?) AS next_booking
    FROM bk_customers c"""


def phone_key(value: Any) -> str:
    """A phone number as its digits, so "+1 555 0199" and "+15550199" are the same customer."""
    return "".join(ch for ch in str(value or "") if ch.isdigit())


def _same_person(
    row: Mapping[str, Any], email: str, phone: str, *, fields: tuple[str, str]
) -> bool:
    email_field, phone_field = fields
    if email and str(row[email_field] or "").lower() == email.lower():
        return True
    key = phone_key(phone)
    return bool(key) and phone_key(row[phone_field]) == key


def _customer_out(row: dict[str, Any]) -> dict[str, Any]:
    row["tags"] = kit.loads(row.get("tags"), []) or []
    return row


def list_customers(
    connection: sqlite3.Connection, *, q: str = "", limit: int = 500
) -> list[dict[str, Any]]:
    params: list[Any] = [kit.now()]
    where = ""
    if q:
        where = " WHERE c.name LIKE ? OR c.email LIKE ? OR c.phone LIKE ?"
        like = f"%{q.strip()}%"
        params += [like, like, like]
    rows = kit.fetch_all(
        connection,
        f"{_CUSTOMER_STATS}{where} ORDER BY COALESCE(last_visit, c.created_at) DESC LIMIT ?",  # noqa: S608
        (*params, max(1, min(limit, 5000))),
    )
    return [_customer_out(row) for row in rows]


def get_customer(connection: sqlite3.Connection, customer_id: str) -> dict[str, Any]:
    row = kit.fetch_one(
        connection, f"{_CUSTOMER_STATS} WHERE c.id = ?", (kit.now(), customer_id)
    )  # noqa: S608
    if not row:
        raise LookupError("That customer was not found.")
    customer = _customer_out(row)
    customer["history"] = kit.fetch_all(
        connection,
        "SELECT id, reference, service_name, staff_name, start_at, status, price_cents, paid_cents, payment_kind FROM bk_bookings WHERE customer_id = ? ORDER BY start_at DESC LIMIT 100",
        (customer_id,),
    )
    customer["packages"] = list_customer_packages(connection, customer_id=customer_id)
    return customer


def update_customer(
    connection: sqlite3.Connection, customer_id: str, raw: Mapping[str, Any]
) -> dict[str, Any]:
    current = kit.require_row(connection, "bk_customers", customer_id, "customer")
    values: dict[str, Any] = {"updated_at": kit.now()}
    if "name" in raw:
        values["name"] = kit.text(raw["name"], "the name", max_len=120, required=True)
    if "email" in raw:
        values["email"] = kit.email(raw["email"])
    if "phone" in raw:
        values["phone"] = kit.phone(raw["phone"])
    if "notes" in raw:
        values["notes"] = kit.text(raw["notes"], "notes", max_len=4000, multiline=True)
    if "tags" in raw:
        values["tags"] = kit.dumps(kit.tags(raw["tags"]))
    kit.update_row(connection, "bk_customers", current["id"], values)
    connection.commit()
    return get_customer(connection, customer_id)


def customers_csv(connection: sqlite3.Connection) -> bytes:
    rows = list_customers(connection, limit=5000)
    for row in rows:
        row["tags"] = ", ".join(row["tags"])
    return kit.csv_bytes(
        rows,
        (
            "name",
            "email",
            "phone",
            "visits",
            "bookings",
            "no_shows",
            "spend_cents",
            "last_visit",
            "next_booking",
            "tags",
            "notes",
            "created_at",
        ),
    )


# ---------------------------------------------------------------------------
# bookings
# ---------------------------------------------------------------------------


def _reference(connection: sqlite3.Connection) -> str:
    for _ in range(20):
        code = "BK-" + "".join(secrets.choice(REFERENCE_ALPHABET) for _ in range(6))
        if not kit.count(
            connection, "SELECT COUNT(*) FROM bk_bookings WHERE reference = ?", (code,)
        ):
            return code
    raise RuntimeError("Could not make a booking reference.")


def get_booking(connection: sqlite3.Connection, booking_id: str) -> dict[str, Any]:
    row = kit.require_row(connection, "bk_bookings", booking_id, "booking")
    tz = store.zone(connection)
    row["local_start"] = local_label(row["start_at"], tz)
    row["due_cents"] = (
        max(0, int(row["price_cents"]) - int(row["paid_cents"]))
        if row["payment_kind"] != "package"
        else 0
    )
    return row


def _begin(connection: sqlite3.Connection) -> None:
    """Take the write lock before checking a slot, so two bookings of the same time cannot both pass the check."""
    if connection.in_transaction:
        connection.commit()
    connection.execute("BEGIN IMMEDIATE")


def _choose(
    connection: sqlite3.Connection,
    service_id: str,
    start: datetime,
    staff_id: str,
    *,
    now: datetime | None,
    exclude: str,
    customer_rules: bool,
    prefer: str = "",
) -> str:
    tz = store.zone(connection)
    day = start.astimezone(tz).date().isoformat()
    slots = slots_for_day(
        connection,
        service_id,
        day,
        staff_id=staff_id,
        now=now,
        exclude_booking=exclude,
        customer_rules=customer_rules,
    )
    match = next((slot for slot in slots if slot["start"] == kit.iso(start)), None)
    if match is None:
        raise ValueError("That time is no longer available. Pick another time.")
    if prefer and prefer in match["staff_ids"]:
        return prefer
    if len(match["staff_ids"]) == 1:
        return match["staff_ids"][0]
    day_start, day_end = _local_day_bounds(start.astimezone(tz).date(), tz)
    load = {
        person: kit.count(
            connection,
            "SELECT COUNT(*) FROM bk_bookings WHERE staff_id = ? AND status IN ('booked', 'confirmed') AND start_at >= ? AND start_at < ?",
            (person, kit.iso(day_start), kit.iso(day_end)),
        )
        for person in match["staff_ids"]
    }
    return min(
        match["staff_ids"],
        key=lambda person: (load[person], match["staff_ids"].index(person)),
    )


def _conflict_free(
    connection: sqlite3.Connection,
    staff_id: str,
    start: datetime,
    held_until: datetime,
    exclude: str = "",
) -> None:
    if any(
        start < busy_end and busy_start < held_until
        for busy_start, busy_end in _busy(
            connection, staff_id, start, held_until, exclude
        )
    ):
        raise ValueError("That person already has a booking at that time.")


def book(
    connection: sqlite3.Connection,
    *,
    service_id: str,
    start: str,
    customer: Mapping[str, Any],
    staff_id: str = "",
    note: str = "",
    internal_note: str = "",
    source: str = "manual",
    status: str = "",
    outside_hours: bool = False,
    now: datetime | None = None,
    origin: Any = None,
) -> dict[str, Any]:
    """Book a service. A public booking must be a free slot under the customer rules; a booking you make yourself
    may ignore notice and horizon, and (``outside_hours``) the hours too, but never overlaps another booking."""
    store.ensure(connection)
    service = store.get_service(connection, service_id)
    if source == "public" and not service["active"]:
        raise ValueError("That service is not available to book.")
    start_at = _parse(kit.moment(start, "the start time", required=True))
    name = kit.text(customer.get("name"), "your name", max_len=120, required=True)
    email = kit.email(customer.get("email"))
    phone = kit.phone(customer.get("phone"))
    if source == "public" and not (email or phone):
        raise ValueError(
            "Enter an email address or a phone number so the business can reach you."
        )
    rules = store.get_rules(connection)
    if source == "public":
        # The same person by email or by phone, however the number is written (a customer who gives only a
        # phone number is limited too).
        rows = kit.fetch_all(
            connection,
            "SELECT customer_email, customer_phone FROM bk_bookings WHERE status IN ('booked', 'confirmed')"
            " AND start_at > ? AND ((? != '' AND customer_email = ?) OR customer_phone != '')",
            (kit.iso(now or kit.utcnow()), email, email),
        )
        active = sum(
            1
            for row in rows
            if _same_person(
                row, email, phone, fields=("customer_email", "customer_phone")
            )
        )
        if active >= int(rules["max_active_per_customer"]):
            raise ValueError(
                "You already have the most upcoming bookings allowed. Cancel one, or contact the business."
            )
    chosen_status = status or (
        "confirmed" if source != "public" or rules["auto_confirm"] else "booked"
    )
    if chosen_status not in ACTIVE_STATUSES + ("completed",):
        raise ValueError("A new booking is booked, confirmed or completed.")
    duration = timedelta(minutes=int(service["minutes"]))
    held_until = start_at + duration + timedelta(minutes=int(service["buffer_minutes"]))
    _begin(connection)
    try:
        if outside_hours and source != "public":
            if not staff_id:
                people = eligible_staff(connection, service)
                if not people:
                    raise ValueError("Add a person who offers this service first.")
                staff_id = people[0]["id"]
            kit.require_row(connection, "bk_staff", staff_id, "person")
            _conflict_free(connection, staff_id, start_at, held_until)
            chosen = staff_id
        else:
            chosen = _choose(
                connection,
                service_id,
                start_at,
                staff_id,
                now=now,
                exclude="",
                customer_rules=source == "public",
            )
        person = kit.require_row(connection, "bk_staff", chosen, "person")
        who = upsert_customer(connection, name=name, email=email, phone=phone)
        booking_id = kit.new_id()
        reference = _reference(connection)
        created = kit.now()
        connection.execute(
            """INSERT INTO bk_bookings (id, reference, service_id, staff_id, customer_id, service_name, staff_name, customer_name,
            customer_email, customer_phone, start_at, end_at, busy_until, status, price_cents, deposit_cents, source, note,
            internal_note, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                booking_id,
                reference,
                service_id,
                chosen,
                who["id"],
                service["name"],
                person["name"],
                name,
                email,
                phone,
                kit.iso(start_at),
                kit.iso(start_at + duration),
                kit.iso(held_until),
                chosen_status,
                int(service["price_cents"]),
                int(service["deposit_cents"]),
                "public" if source == "public" else "manual",
                kit.text(note, "a note", max_len=1000, multiline=True),
                kit.text(
                    internal_note, "an internal note", max_len=2000, multiline=True
                ),
                created,
                created,
            ),
        )
        tz = store.zone(connection)
        kit.log(
            connection,
            APP,
            "booked",
            f"{name} booked {service['name']}",
            detail=f"{local_label(kit.iso(start_at), tz)} with {person['name']} ({reference})",
            ref_type="booking",
            ref_id=booking_id,
            public=source == "public",
        )
        _waitlist_booked(
            connection,
            {
                "service_id": service_id,
                "start_at": kit.iso(start_at),
                "customer_email": email,
                "customer_phone": phone,
            },
        )
        connection.commit()
    except BaseException:
        connection.rollback()
        raise
    link = public.create_link(
        connection,
        APP,
        BOOKING_KIND,
        booking_id,
        title=f"{service['name']} · {reference}",
    )
    booking = get_booking(connection, booking_id)
    _message(connection, booking, "booking_confirmation", link, origin)
    _queue_reminder(connection, booking, link, origin, now=now)
    connection.commit()
    booking["manage_path"] = link["path"]
    return booking


def booking_link(connection: sqlite3.Connection, booking_id: str) -> dict[str, Any]:
    booking = get_booking(connection, booking_id)
    return public.create_link(
        connection,
        APP,
        BOOKING_KIND,
        booking_id,
        title=f"{booking['service_name']} · {booking['reference']}",
    )


def _deadline(connection: sqlite3.Connection, booking: Mapping[str, Any]) -> datetime:
    hours = int(store.get_rules(connection)["cancel_notice_hours"])
    return _parse(booking["start_at"]) - timedelta(hours=hours)


def customer_may_change(
    connection: sqlite3.Connection,
    booking: Mapping[str, Any],
    now: datetime | None = None,
) -> bool:
    return booking["status"] in ACTIVE_STATUSES and (now or kit.utcnow()) <= _deadline(
        connection, booking
    )


def _cancel_reminders(connection: sqlite3.Connection, booking_id: str) -> None:
    ids = [
        row[0]
        for row in connection.execute(
            "SELECT id FROM suite_outbox WHERE app = ? AND ref_type = 'booking' AND ref_id = ? AND kind = 'booking_reminder' AND status IN ('pending', 'approved')",
            (APP, booking_id),
        )
    ]
    if ids:
        kit.set_outbox_status(connection, ids, "cancelled")


def _release_package(
    connection: sqlite3.Connection, booking: Mapping[str, Any]
) -> None:
    if booking["payment_kind"] == "package" and booking["customer_package_id"]:
        connection.execute(
            "UPDATE bk_customer_packages SET sessions_used = MAX(0, sessions_used - 1), status = CASE WHEN status = 'used_up' THEN 'active' ELSE status END WHERE id = ?",
            (booking["customer_package_id"],),
        )


def cancel(
    connection: sqlite3.Connection,
    booking_id: str,
    *,
    by: str = "business",
    reason: str = "",
    now: datetime | None = None,
    origin: Any = None,
) -> dict[str, Any]:
    """Cancel a booking. A customer may cancel until the cancellation notice; the business always may."""
    booking = get_booking(connection, booking_id)
    if booking["status"] not in ACTIVE_STATUSES:
        raise ValueError(
            f"This booking is {booking['status'].replace('_', ' ')} and cannot be cancelled."
        )
    if by == "customer" and not customer_may_change(connection, booking, now):
        hours = store.get_rules(connection)["cancel_notice_hours"]
        raise ValueError(
            f"Online cancellation closes {hours} hours before the appointment. Please contact the business."
        )
    text = kit.text(reason, "a reason", max_len=500)
    _release_package(connection, booking)
    connection.execute(
        "UPDATE bk_bookings SET status = 'cancelled', cancelled_at = ?, cancel_reason = ?, payment_kind = CASE WHEN payment_kind = 'package' THEN '' ELSE payment_kind END, customer_package_id = CASE WHEN payment_kind = 'package' THEN '' ELSE customer_package_id END, updated_at = ? WHERE id = ?",
        (kit.now(), text, kit.now(), booking_id),
    )
    _cancel_reminders(connection, booking_id)
    kit.log(
        connection,
        APP,
        "cancelled",
        f"{booking['customer_name']} {'cancelled' if by == 'customer' else 'was cancelled'}: {booking['service_name']}",
        detail=f"{booking['local_start']} ({booking['reference']}){': ' + text if text else ''}",
        ref_type="booking",
        ref_id=booking_id,
        public=by == "customer",
    )
    updated = get_booking(connection, booking_id)
    if by == "customer":
        owner = kit.business(connection).get("email") or ""
        if owner:
            kit.queue_email(
                connection,
                APP,
                "booking_cancelled_notice",
                to=owner,
                subject=f"Cancelled: {booking['service_name']} with {booking['customer_name']}",
                body=f"{booking['customer_name']} cancelled {booking['service_name']} on {booking['local_start']} ({booking['reference']}).{chr(10) + 'Reason: ' + text if text else ''}",
                ref_type="booking",
                ref_id=booking_id,
            )
    else:
        _message(connection, updated, "booking_cancelled", None, None)
    _offer_opening(connection, booking, origin, now=now)
    connection.commit()
    return updated


def reschedule(
    connection: sqlite3.Connection,
    booking_id: str,
    start: str,
    *,
    staff_id: str = "",
    by: str = "business",
    now: datetime | None = None,
    origin: Any = None,
) -> dict[str, Any]:
    """Move a booking to another free time (the customer within the cancellation notice, the business always)."""
    booking = get_booking(connection, booking_id)
    if booking["status"] not in ACTIVE_STATUSES:
        raise ValueError(
            f"This booking is {booking['status'].replace('_', ' ')} and cannot be moved."
        )
    if by == "customer" and not customer_may_change(connection, booking, now):
        hours = store.get_rules(connection)["cancel_notice_hours"]
        raise ValueError(
            f"Online changes close {hours} hours before the appointment. Please contact the business."
        )
    service = store.get_service(connection, booking["service_id"])
    start_at = _parse(kit.moment(start, "the new time", required=True))
    duration = timedelta(minutes=int(service["minutes"]))
    held_until = start_at + duration + timedelta(minutes=int(service["buffer_minutes"]))
    _begin(connection)
    try:
        chosen = _choose(
            connection,
            booking["service_id"],
            start_at,
            staff_id if by == "business" else "",
            now=now,
            exclude=booking_id,
            customer_rules=by == "customer",
            prefer=booking["staff_id"],
        )
        person = kit.require_row(connection, "bk_staff", chosen, "person")
        connection.execute(
            "UPDATE bk_bookings SET start_at = ?, end_at = ?, busy_until = ?, staff_id = ?, staff_name = ?, updated_at = ? WHERE id = ?",
            (
                kit.iso(start_at),
                kit.iso(start_at + duration),
                kit.iso(held_until),
                chosen,
                person["name"],
                kit.now(),
                booking_id,
            ),
        )
        tz = store.zone(connection)
        kit.log(
            connection,
            APP,
            "rescheduled",
            f"{booking['customer_name']} moved {booking['service_name']}",
            detail=f"from {booking['local_start']} to {local_label(kit.iso(start_at), tz)} ({booking['reference']})",
            ref_type="booking",
            ref_id=booking_id,
            public=by == "customer",
        )
        connection.commit()
    except BaseException:
        connection.rollback()
        raise
    _cancel_reminders(connection, booking_id)
    link = booking_link(connection, booking_id)
    updated = get_booking(connection, booking_id)
    _message(connection, updated, "booking_rescheduled", link, origin)
    _queue_reminder(connection, updated, link, origin, now=now)
    if booking["start_at"] != updated["start_at"]:
        _offer_opening(connection, booking, origin, now=now)
    connection.commit()
    return updated


def set_status(
    connection: sqlite3.Connection, booking_id: str, status: str
) -> dict[str, Any]:
    """Confirm, complete or mark a no-show (cancelling goes through ``cancel``)."""
    booking = get_booking(connection, booking_id)
    status = kit.choice(
        status, "the status", ("booked", "confirmed", "completed", "no_show")
    )
    if booking["status"] == "cancelled":
        raise ValueError(
            "A cancelled booking cannot change status. Book again instead."
        )
    if status == booking["status"]:
        return booking
    connection.execute(
        "UPDATE bk_bookings SET status = ?, completed_at = CASE WHEN ? = 'completed' THEN ? ELSE completed_at END, updated_at = ? WHERE id = ?",
        (status, status, kit.now(), kit.now(), booking_id),
    )
    if status in ("completed", "no_show"):
        _cancel_reminders(connection, booking_id)
    kit.log(
        connection,
        APP,
        status,
        f"{booking['customer_name']}: {booking['service_name']} {status.replace('_', ' ')}",
        detail=booking["reference"],
        ref_type="booking",
        ref_id=booking_id,
    )
    updated = get_booking(connection, booking_id)
    if status == "completed":
        _review_request(connection, updated)
    connection.commit()
    return updated


def update_notes(
    connection: sqlite3.Connection, booking_id: str, raw: Mapping[str, Any]
) -> dict[str, Any]:
    kit.require_row(connection, "bk_bookings", booking_id, "booking")
    values: dict[str, Any] = {"updated_at": kit.now()}
    if "note" in raw:
        values["note"] = kit.text(raw["note"], "a note", max_len=1000, multiline=True)
    if "internal_note" in raw:
        values["internal_note"] = kit.text(
            raw["internal_note"], "an internal note", max_len=2000, multiline=True
        )
    kit.update_row(connection, "bk_bookings", booking_id, values)
    connection.commit()
    return get_booking(connection, booking_id)


def list_bookings(
    connection: sqlite3.Connection,
    *,
    when: str = "upcoming",
    status: str = "",
    q: str = "",
    staff_id: str = "",
    customer_id: str = "",
    from_day: str = "",
    to_day: str = "",
    limit: int = 300,
) -> list[dict[str, Any]]:
    tz = store.zone(connection)
    clauses: list[str] = []
    params: list[Any] = []
    now = kit.now()
    if when == "upcoming":
        clauses.append("start_at >= ?")
        params.append(now)
    elif when == "past":
        clauses.append("start_at < ?")
        params.append(now)
    if status:
        clauses.append("status = ?")
        params.append(kit.choice(status, "the status", store.STATUSES))
    if staff_id:
        clauses.append("staff_id = ?")
        params.append(staff_id)
    if customer_id:
        clauses.append("customer_id = ?")
        params.append(customer_id)
    if from_day:
        clauses.append("start_at >= ?")
        params.append(
            kit.iso(_local_day_bounds(date.fromisoformat(kit.day(from_day)), tz)[0])
        )
    if to_day:
        clauses.append("start_at < ?")
        params.append(
            kit.iso(_local_day_bounds(date.fromisoformat(kit.day(to_day)), tz)[1])
        )
    if q:
        clauses.append(
            "(customer_name LIKE ? OR customer_email LIKE ? OR customer_phone LIKE ? OR reference LIKE ? OR service_name LIKE ?)"
        )
        like = f"%{q.strip()}%"
        params += [like] * 5
    where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    order = "ASC" if when == "upcoming" else "DESC"
    rows = kit.fetch_all(
        connection,
        f"SELECT * FROM bk_bookings {where} ORDER BY start_at {order} LIMIT ?",  # noqa: S608
        (*params, max(1, min(limit, 2000))),
    )
    for row in rows:
        row["local_start"] = local_label(row["start_at"], tz)
    return rows


def bookings_csv(connection: sqlite3.Connection) -> bytes:
    rows = list_bookings(connection, when="all", limit=2000)
    return kit.csv_bytes(
        rows,
        (
            "reference",
            "local_start",
            "service_name",
            "staff_name",
            "customer_name",
            "customer_email",
            "customer_phone",
            "status",
            "price_cents",
            "paid_cents",
            "payment_kind",
            "source",
            "note",
            "created_at",
        ),
    )


def agenda(
    connection: sqlite3.Connection, first_day: str, days: int = 7, *, staff_id: str = ""
) -> dict[str, Any]:
    tz = store.zone(connection)
    start = date.fromisoformat(kit.day(first_day, "the first day", required=True))
    count = max(1, min(int(days), 31))
    rows = list_bookings(
        connection,
        when="all",
        staff_id=staff_id,
        from_day=start.isoformat(),
        to_day=(start + timedelta(days=count - 1)).isoformat(),
        limit=2000,
    )
    rows.sort(key=lambda row: row["start_at"])
    out = []
    for offset in range(count):
        day = start + timedelta(days=offset)
        items = [
            row for row in rows if _parse(row["start_at"]).astimezone(tz).date() == day
        ]
        for row in items:
            row["local_time"] = _parse(row["start_at"]).astimezone(tz).strftime("%H:%M")
            row["local_end"] = _parse(row["end_at"]).astimezone(tz).strftime("%H:%M")
        out.append(
            {
                "day": day.isoformat(),
                "label": day.strftime("%a %d %b"),
                "bookings": items,
            }
        )
    staff = kit.fetch_all(
        connection, "SELECT id, name, color, active FROM bk_staff ORDER BY name"
    )
    return {"days": out, "staff": staff, "timezone": store.zone_name(connection)}


# ---------------------------------------------------------------------------
# payments and packages
# ---------------------------------------------------------------------------


def mark_paid(
    connection: sqlite3.Connection,
    booking_id: str,
    *,
    kind: str = "full",
    amount_cents: Any = None,
) -> dict[str, Any]:
    """Record money received for a booking: the deposit, the full price or an amount. Counted once in revenue."""
    booking = get_booking(connection, booking_id)
    if booking["status"] == "cancelled" and kind != "amount":
        raise ValueError(
            "This booking is cancelled. Record a cancellation fee as an amount."
        )
    if booking["payment_kind"] == "package":
        raise ValueError("This booking is paid from a package.")
    kind = kit.choice(kind, "the payment", ("deposit", "full", "amount"))
    if kind == "deposit":
        amount = int(booking["deposit_cents"])
        if amount <= 0:
            raise ValueError(
                "This service has no deposit. Record the full price or an amount."
            )
    elif kind == "full":
        amount = int(booking["price_cents"])
        if amount <= 0:
            raise ValueError(
                "This service is free. Record an amount if the customer paid something."
            )
    else:
        amount = store.cents_field(amount_cents, "the amount", default=None, minimum=1)
    connection.execute(
        "UPDATE bk_bookings SET paid_cents = ?, paid_at = ?, payment_kind = ?, updated_at = ? WHERE id = ?",
        (amount, kit.now(), kind, kit.now(), booking_id),
    )
    kit.record_revenue(
        connection,
        APP,
        source_type="booking",
        source_id=booking_id,
        amount_cents=amount,
        label=f"{booking['service_name']} · {booking['reference']}",
        customer=booking["customer_name"],
    )
    kit.log(
        connection,
        APP,
        "paid",
        f"{booking['customer_name']} paid {kit.fmt_money(amount, kit.business(connection)['currency'])}",
        detail=f"{booking['service_name']} ({booking['reference']}, {kind})",
        ref_type="booking",
        ref_id=booking_id,
        amount_cents=amount,
    )
    connection.commit()
    return get_booking(connection, booking_id)


def mark_unpaid(connection: sqlite3.Connection, booking_id: str) -> dict[str, Any]:
    booking = get_booking(connection, booking_id)
    if booking["payment_kind"] == "package":
        _release_package(connection, booking)
    connection.execute(
        "UPDATE bk_bookings SET paid_cents = 0, paid_at = NULL, payment_kind = '', customer_package_id = '', updated_at = ? WHERE id = ?",
        (kit.now(), booking_id),
    )
    kit.void_revenue(connection, APP, "booking", booking_id)
    kit.log(
        connection,
        APP,
        "unpaid",
        f"Payment removed: {booking['service_name']}",
        detail=booking["reference"],
        ref_type="booking",
        ref_id=booking_id,
    )
    connection.commit()
    return get_booking(connection, booking_id)


def list_customer_packages(
    connection: sqlite3.Connection, *, customer_id: str = ""
) -> list[dict[str, Any]]:
    where, params = "", []
    if customer_id:
        where, params = "WHERE p.customer_id = ?", [customer_id]
    rows = kit.fetch_all(
        connection,
        f"""SELECT p.*, c.name AS customer_name, c.email AS customer_email FROM bk_customer_packages p
        LEFT JOIN bk_customers c ON c.id = p.customer_id {where} ORDER BY p.created_at DESC LIMIT 1000""",  # noqa: S608
        params,
    )
    for row in rows:
        row["remaining"] = max(
            0, int(row["sessions_total"]) - int(row["sessions_used"])
        )
    return rows


def sell_package(
    connection: sqlite3.Connection,
    *,
    customer_id: str,
    package_id: str,
    paid: bool = False,
) -> dict[str, Any]:
    customer = kit.require_row(connection, "bk_customers", customer_id, "customer")
    package = kit.require_row(connection, "bk_packages", package_id, "package")
    if not package["active"]:
        raise ValueError("That package is no longer sold.")
    sold_id = kit.new_id()
    expires = (
        (kit.utcnow().date() + timedelta(days=int(package["valid_days"]))).isoformat()
        if package["valid_days"]
        else ""
    )
    connection.execute(
        """INSERT INTO bk_customer_packages (id, package_id, customer_id, name, service_id, sessions_total, price_cents, status, expires_on, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, 'active', ?, ?)""",
        (
            sold_id,
            package_id,
            customer_id,
            package["name"],
            package["service_id"],
            package["sessions"],
            package["price_cents"],
            expires,
            kit.now(),
        ),
    )
    kit.log(
        connection,
        APP,
        "package_sold",
        f"{customer['name']} bought {package['name']}",
        ref_type="customer_package",
        ref_id=sold_id,
    )
    connection.commit()
    if paid:
        return pay_package(connection, sold_id)
    return _sold(connection, sold_id)


def _sold(connection: sqlite3.Connection, sold_id: str) -> dict[str, Any]:
    row = kit.require_row(connection, "bk_customer_packages", sold_id, "package")
    row["remaining"] = max(0, int(row["sessions_total"]) - int(row["sessions_used"]))
    return row


def pay_package(connection: sqlite3.Connection, sold_id: str) -> dict[str, Any]:
    sold = _sold(connection, sold_id)
    if sold["status"] == "cancelled":
        raise ValueError("This package was cancelled.")
    customer = kit.fetch_one(
        connection, "SELECT name FROM bk_customers WHERE id = ?", (sold["customer_id"],)
    ) or {"name": ""}
    connection.execute(
        "UPDATE bk_customer_packages SET paid_at = COALESCE(paid_at, ?) WHERE id = ?",
        (kit.now(), sold_id),
    )
    if int(sold["price_cents"]) > 0:
        kit.record_revenue(
            connection,
            APP,
            source_type="package",
            source_id=sold_id,
            amount_cents=int(sold["price_cents"]),
            label=sold["name"],
            customer=customer["name"],
        )
        kit.log(
            connection,
            APP,
            "paid",
            f"{customer['name']} paid for {sold['name']}",
            ref_type="customer_package",
            ref_id=sold_id,
            amount_cents=int(sold["price_cents"]),
        )
    connection.commit()
    return _sold(connection, sold_id)


def cancel_package(
    connection: sqlite3.Connection, sold_id: str, *, refund: bool = False
) -> dict[str, Any]:
    _sold(connection, sold_id)
    connection.execute(
        "UPDATE bk_customer_packages SET status = 'cancelled' WHERE id = ?", (sold_id,)
    )
    if refund:
        kit.void_revenue(connection, APP, "package", sold_id)
    connection.commit()
    return _sold(connection, sold_id)


def redeem(
    connection: sqlite3.Connection, booking_id: str, sold_id: str
) -> dict[str, Any]:
    """Pay a booking with one session of the customer's package."""
    booking = get_booking(connection, booking_id)
    sold = _sold(connection, sold_id)
    if booking["status"] == "cancelled":
        raise ValueError("This booking is cancelled.")
    if booking["payment_kind"] == "package":
        raise ValueError("This booking is already paid from a package.")
    if int(booking["paid_cents"]):
        raise ValueError("This booking is already paid. Mark it unpaid first.")
    if sold["customer_id"] != booking["customer_id"]:
        raise ValueError("That package belongs to another customer.")
    if sold["status"] != "active" or sold["remaining"] <= 0:
        raise ValueError("That package has no sessions left.")
    if sold["expires_on"] and sold["expires_on"] < kit.today():
        raise ValueError(f"That package expired on {sold['expires_on']}.")
    if sold["service_id"] and sold["service_id"] != booking["service_id"]:
        raise ValueError("That package is for a different service.")
    used = int(sold["sessions_used"]) + 1
    connection.execute(
        "UPDATE bk_customer_packages SET sessions_used = ?, status = ? WHERE id = ?",
        (used, "used_up" if used >= int(sold["sessions_total"]) else "active", sold_id),
    )
    connection.execute(
        "UPDATE bk_bookings SET payment_kind = 'package', customer_package_id = ?, paid_at = ?, updated_at = ? WHERE id = ?",
        (sold_id, kit.now(), kit.now(), booking_id),
    )
    kit.log(
        connection,
        APP,
        "redeemed",
        f"{booking['customer_name']} used a session of {sold['name']}",
        detail=f"{int(sold['sessions_total']) - used} left",
        ref_type="booking",
        ref_id=booking_id,
    )
    connection.commit()
    return get_booking(connection, booking_id)


# ---------------------------------------------------------------------------
# messages (the shared outbox)
# ---------------------------------------------------------------------------


def _url(link: Mapping[str, Any] | None, origin: Any) -> str:
    """The full address of a customer's page for an email (``origin``: the route's request or the visitor)."""
    return kit.public_url(link["path"], origin) if link else ""


def _money(connection: sqlite3.Connection, cents: int) -> str:
    return kit.fmt_money(int(cents), kit.business(connection)["currency"])


def _message(
    connection: sqlite3.Connection,
    booking: Mapping[str, Any],
    kind: str,
    link: Mapping[str, Any] | None,
    origin: Any,
) -> None:
    if not booking["customer_email"]:
        return
    business = kit.business(connection)
    first = booking["customer_name"].split(" ")[0]
    manage = _url(link, origin)
    sign = business["name"] or "See you soon"
    lines: list[str]
    if kind == "booking_confirmation":
        subject = f"Booked: {booking['service_name']} on {booking['local_start']}"
        lines = [
            f"Hi {first},",
            "",
            f"You're booked for {booking['service_name']} with {booking['staff_name']} on {booking['local_start']} ({store.zone_name(connection)}).",
            f"Reference: {booking['reference']}",
        ]
        if int(booking["price_cents"]):
            lines.append(f"Price: {_money(connection, booking['price_cents'])}")
        if int(booking["deposit_cents"]):
            lines.append(
                f"Deposit to secure your time: {_money(connection, booking['deposit_cents'])}"
            )
            if business["payment_link"]:
                lines.append(f"Pay here: {business['payment_link']}")
    elif kind == "booking_rescheduled":
        subject = f"Moved: {booking['service_name']} is now {booking['local_start']}"
        lines = [
            f"Hi {first},",
            "",
            f"Your {booking['service_name']} is now on {booking['local_start']} with {booking['staff_name']}.",
            f"Reference: {booking['reference']}",
        ]
    elif kind == "booking_cancelled":
        subject = f"Cancelled: {booking['service_name']} on {booking['local_start']}"
        lines = [
            f"Hi {first},",
            "",
            f"Your {booking['service_name']} on {booking['local_start']} ({booking['reference']}) is cancelled.",
        ]
        if booking.get("cancel_reason"):
            lines.append(f"Reason: {booking['cancel_reason']}")
        lines.append("Reply to this email if you would like a new time.")
        manage = ""
    else:
        return
    if manage:
        lines += ["", f"To cancel or change it: {manage}"]
    lines += ["", sign]
    kit.queue_email(
        connection,
        APP,
        kind,
        to=booking["customer_email"],
        subject=subject,
        body="\n".join(lines),
        ref_type="booking",
        ref_id=booking["id"],
    )


def _queue_reminder(
    connection: sqlite3.Connection,
    booking: Mapping[str, Any],
    link: Mapping[str, Any] | None,
    origin: Any,
    *,
    now: datetime | None = None,
) -> None:
    if not booking["customer_email"]:
        return
    send_at = _parse(booking["start_at"]) - timedelta(hours=24)
    if send_at <= (now or kit.utcnow()):
        return  # booked less than a day ahead: the confirmation is the reminder
    business = kit.business(connection)
    first = booking["customer_name"].split(" ")[0]
    lines = [
        f"Hi {first},",
        "",
        f"A reminder: {booking['service_name']} with {booking['staff_name']} tomorrow, {booking['local_start']}.",
        f"Reference: {booking['reference']}",
    ]
    manage = _url(link, origin)
    if manage:
        lines += ["", f"Need to change it? {manage}"]
    lines += ["", business["name"] or ""]
    kit.queue_email(
        connection,
        APP,
        "booking_reminder",
        to=booking["customer_email"],
        subject=f"Tomorrow: {booking['service_name']} at {_parse(booking['start_at']).astimezone(store.zone(connection)).strftime('%H:%M')}",
        body="\n".join(lines).rstrip(),
        ref_type="booking",
        ref_id=booking["id"],
        send_after=kit.iso(send_at),
    )


def _review_request(connection: sqlite3.Connection, booking: Mapping[str, Any]) -> None:
    link = store.get_rules(connection)["review_link"]
    if not booking["customer_email"] or not link:
        return
    business = kit.business(connection)
    first = booking["customer_name"].split(" ")[0]
    kit.queue_email(
        connection,
        APP,
        "review_request",
        to=booking["customer_email"],
        subject=f"How was your {booking['service_name']}?",
        body=f"Hi {first},\n\nThank you for coming in. If you have a minute, a short review helps us a lot:\n{link}\n\n{business['name'] or ''}".rstrip(),
        ref_type="booking",
        ref_id=booking["id"],
        send_after=kit.iso(kit.utcnow() + timedelta(hours=2)),
    )


# ---------------------------------------------------------------------------
# the waiting list: a full day, and a message when a time opens
# ---------------------------------------------------------------------------

MAX_WAITING_PER_CUSTOMER = 5


def day_open(
    connection: sqlite3.Connection,
    service: Mapping[str, Any],
    day: str,
    *,
    staff_id: str = "",
) -> bool:
    """Someone who offers the service works that day (it may still be fully booked)."""
    weekday = date.fromisoformat(day).weekday()
    for person in eligible_staff(connection, service):
        if staff_id and person["id"] != staff_id:
            continue
        if _off(connection, person["id"], day):
            continue
        if kit.count(
            connection,
            "SELECT COUNT(*) FROM bk_hours WHERE staff_id = ? AND weekday = ?",
            (person["id"], weekday),
        ):
            return True
    return False


def join_waitlist(
    connection: sqlite3.Connection,
    *,
    service_id: str,
    day: str,
    customer: Mapping[str, Any],
    staff_id: str = "",
    note: str = "",
    now: datetime | None = None,
) -> dict[str, Any]:
    """A customer asks to be told when a time opens on a day (a booking there is cancelled or moved)."""
    store.ensure(connection)
    service = store.get_service(connection, service_id)
    if not service["active"]:
        raise ValueError("That service is not available to book.")
    rules = store.get_rules(connection)
    wanted = kit.day(day, "the day", required=True)
    today = date.fromisoformat(local_today(connection, now))
    if date.fromisoformat(wanted) < today:
        raise ValueError("That day has passed. Pick another day.")
    if date.fromisoformat(wanted) > today + timedelta(
        days=int(rules["max_days_ahead"])
    ):
        raise ValueError("That day is too far ahead to book yet.")
    if staff_id:
        if not any(p["id"] == staff_id for p in eligible_staff(connection, service)):
            raise ValueError("That person does not offer this service.")
    if not day_open(connection, service, wanted, staff_id=staff_id):
        raise ValueError("We are closed that day. Pick another day.")
    if not slots_for_day(
        connection, service_id, wanted, staff_id=staff_id, now=now, ignore_bookings=True
    ):
        # Its times are over, or too soon to book: no cancellation could ever free one for this customer.
        raise ValueError("No more times can be booked that day. Pick another day.")
    name = kit.text(customer.get("name"), "your name", max_len=120, required=True)
    email = kit.email(customer.get("email"))
    phone = kit.phone(customer.get("phone"))
    if not (email or phone):
        raise ValueError(
            "Enter an email address or a phone number so we can tell you when a time opens."
        )
    mine = [
        row
        for row in kit.fetch_all(
            connection,
            "SELECT * FROM bk_waitlist WHERE status = 'waiting' AND day >= ? ORDER BY created_at",
            (today.isoformat(),),
        )
        if _same_person(row, email, phone, fields=("email", "phone"))
    ]
    existing = next(
        (
            row
            for row in mine
            if row["service_id"] == service_id and row["day"] == wanted
        ),
        None,
    )
    if existing:
        return existing
    held = len(mine)
    if held >= MAX_WAITING_PER_CUSTOMER:
        raise ValueError(
            "You are already on the waiting list for several days. We will be in touch."
        )
    entry = {
        "id": kit.new_id(),
        "service_id": service_id,
        "staff_id": staff_id,
        "day": wanted,
        "name": name,
        "email": email,
        "phone": phone,
        "note": kit.text(note, "a note", max_len=500, multiline=True),
        "status": "waiting",
        "created_at": kit.now(),
    }
    connection.execute(
        """INSERT INTO bk_waitlist (id, service_id, staff_id, day, name, email, phone, note, status, created_at)
        VALUES (:id, :service_id, :staff_id, :day, :name, :email, :phone, :note, :status, :created_at)""",
        entry,
    )
    kit.log(
        connection,
        APP,
        "waitlist",
        f"{name} joined the waiting list for {service['name']}",
        detail=f"{date.fromisoformat(wanted):%A} {date.fromisoformat(wanted).day} {date.fromisoformat(wanted):%B}",
        ref_type="waitlist",
        ref_id=entry["id"],
        public=True,
    )
    connection.commit()
    return entry


def list_waitlist(
    connection: sqlite3.Connection,
    *,
    status: str = "",
    upcoming: bool = True,
    now: datetime | None = None,
) -> list[dict[str, Any]]:
    """The waiting list with the service name, and whether a time is free that day right now."""
    clauses, params = [], []
    if status:
        clauses.append("w.status = ?")
        params.append(kit.choice(status, "the status", store.WAITLIST_STATUSES))
    else:
        clauses.append("w.status != 'removed'")
    if upcoming:
        clauses.append("w.day >= ?")
        params.append(local_today(connection, now))
    rows = kit.fetch_all(
        connection,
        f"""SELECT w.*, s.name AS service_name, st.name AS staff_name FROM bk_waitlist w
        LEFT JOIN bk_services s ON s.id = w.service_id LEFT JOIN bk_staff st ON st.id = w.staff_id
        WHERE {" AND ".join(clauses)} ORDER BY w.day, w.created_at LIMIT 500""",  # noqa: S608
        params,
    )
    for row in rows[:200]:
        row["free_now"] = False
        if row["status"] in ("waiting", "notified"):
            try:
                row["free_now"] = bool(
                    slots_for_day(
                        connection,
                        row["service_id"],
                        row["day"],
                        staff_id=row["staff_id"],
                        now=now,
                        customer_rules=False,
                    )
                )
            except (LookupError, ValueError):
                row["free_now"] = False
    return rows


def set_waitlist_status(
    connection: sqlite3.Connection, entry_id: str, status: str
) -> dict[str, Any]:
    kit.require_row(connection, "bk_waitlist", entry_id, "waiting list entry")
    connection.execute(
        "UPDATE bk_waitlist SET status = ? WHERE id = ?",
        (kit.choice(status, "the status", store.WAITLIST_STATUSES), entry_id),
    )
    connection.commit()
    return kit.require_row(connection, "bk_waitlist", entry_id, "waiting list entry")


def _offer_opening(
    connection: sqlite3.Connection,
    freed: Mapping[str, Any],
    origin: Any = None,
    *,
    now: datetime | None = None,
) -> int:
    """A booking was cancelled or moved away: tell the people waiting for that day (by email) that a time is
    free, with the booking page. That is everyone whose service now has a free time with the person whose
    booking it was (a freed hour may fit a shorter service too). First come, first served; each is told once.
    Returns how many were told."""
    moment = (now or kit.utcnow()).astimezone(UTC)
    start = _parse(freed["start_at"])
    if start <= moment:
        return 0
    tz = store.zone(connection)
    day = start.astimezone(tz).date().isoformat()
    waiting = kit.fetch_all(
        connection,
        """SELECT w.*, s.name AS service_name FROM bk_waitlist w JOIN bk_services s ON s.id = w.service_id
        WHERE w.day = ? AND w.status = 'waiting' AND (w.staff_id = '' OR w.staff_id = ?) ORDER BY w.created_at""",
        (day, freed["staff_id"]),
    )
    if not waiting:
        return 0
    business = kit.business(connection)
    page = public.link_for(connection, APP, PAGE_KIND, PAGE_REF)
    url = _url(page, origin)
    label = f"{start.astimezone(tz):%A} {start.astimezone(tz).day} {start.astimezone(tz):%B}"
    free: dict[str, list[dict[str, Any]]] = {}
    told = 0
    for entry in waiting:
        if not entry["email"]:
            continue  # phone only: the business sees "a time is free" on its waiting list and calls
        if entry["service_id"] not in free:
            try:
                free[entry["service_id"]] = slots_for_day(
                    connection,
                    entry["service_id"],
                    day,
                    staff_id=freed["staff_id"],
                    now=moment,
                )
            except (LookupError, ValueError):
                free[entry["service_id"]] = []
        times = free[entry["service_id"]]
        if not times:
            continue  # the freed time is too short (or too soon) for what this person wants
        shown = ", ".join(slot["local"] for slot in times[:3])
        first = entry["name"].split(" ")[0]
        lines = [
            f"Hi {first},",
            "",
            f"Good news: a time just opened for {entry['service_name']} on {label} "
            f"({shown}). It goes to whoever books first.",
        ]
        if url:
            lines += ["", f"Book it here: {url}"]
        else:
            lines += ["", "Reply to this email to take it."]
        lines += ["", business["name"] or ""]
        kit.queue_email(
            connection,
            APP,
            "waitlist_opening",
            to=entry["email"],
            subject=f"A time opened: {entry['service_name']} on {label}",
            body="\n".join(lines).rstrip(),
            ref_type="waitlist",
            ref_id=entry["id"],
        )
        connection.execute(
            "UPDATE bk_waitlist SET status = 'notified', notified_at = ? WHERE id = ?",
            (kit.now(), entry["id"]),
        )
        told += 1
    if told:
        kit.log(
            connection,
            APP,
            "waitlist_opening",
            f"A time opened on {label}: {told} waiting customer{'s' if told != 1 else ''} told",
            detail=f"{freed['service_name']} with {freed['staff_name']} was freed",
            ref_type="booking",
            ref_id=str(freed["id"]),
        )
    return told


def _waitlist_booked(
    connection: sqlite3.Connection, booking: Mapping[str, Any]
) -> None:
    """Someone on the waiting list booked that day: they are off the list."""
    day = _parse(booking["start_at"]).astimezone(store.zone(connection)).date()
    email, phone = booking["customer_email"], booking["customer_phone"]
    for row in kit.fetch_all(
        connection,
        "SELECT id, email, phone FROM bk_waitlist WHERE service_id = ? AND day = ? AND status IN ('waiting', 'notified')",
        (booking["service_id"], day.isoformat()),
    ):
        if _same_person(row, email, phone, fields=("email", "phone")):
            connection.execute(
                "UPDATE bk_waitlist SET status = 'booked' WHERE id = ?", (row["id"],)
            )


# ---------------------------------------------------------------------------
# team pay: what each person brought in and their commission
# ---------------------------------------------------------------------------


def _month_bounds(connection: sqlite3.Connection) -> tuple[str, str]:
    today = date.fromisoformat(local_today(connection))
    first = today.replace(day=1)
    following = (first + timedelta(days=32)).replace(day=1)
    return first.isoformat(), (following - timedelta(days=1)).isoformat()


def earnings(
    connection: sqlite3.Connection, from_day: str = "", to_day: str = ""
) -> dict[str, Any]:
    """Completed appointments between two local days, per person: takings (money recorded for them, and a
    package session at its share of the package price) and the commission owed at each person's rate."""
    default_from, default_to = _month_bounds(connection)
    first = kit.day(from_day, "the first day") or default_from
    last = kit.day(to_day, "the last day") or default_to
    if last < first:
        raise ValueError("The last day must be on or after the first day.")
    if (date.fromisoformat(last) - date.fromisoformat(first)).days > 366:
        raise ValueError("Pick at most a year at a time.")
    tz = store.zone(connection)
    # Every completed appointment and no-show in the range (no row limit: a year's payroll must be complete).
    rows = kit.fetch_all(
        connection,
        """SELECT * FROM bk_bookings WHERE status IN ('completed', 'no_show') AND start_at >= ? AND start_at < ?
        ORDER BY start_at""",
        (
            kit.iso(_local_day_bounds(date.fromisoformat(first), tz)[0]),
            kit.iso(_local_day_bounds(date.fromisoformat(last), tz)[1]),
        ),
    )
    rates = store.commission_rates(connection)
    package_price = {
        row["id"]: (int(row["price_cents"]), max(1, int(row["sessions_total"])))
        for row in kit.fetch_all(
            connection,
            "SELECT id, price_cents, sessions_total FROM bk_customer_packages",
        )
    }
    staff = {
        row["id"]: row
        for row in kit.fetch_all(
            connection, "SELECT id, name, color, active FROM bk_staff ORDER BY name"
        )
    }
    people: dict[str, dict[str, Any]] = {}
    lines: list[dict[str, Any]] = []
    for row in rows:
        row["local_start"] = local_label(row["start_at"], tz)
        person = people.setdefault(
            row["staff_id"],
            {
                "staff_id": row["staff_id"],
                "name": (staff.get(row["staff_id"]) or {}).get("name")
                or row["staff_name"],
                "color": (staff.get(row["staff_id"]) or {}).get("color") or "",
                "appointments": 0,
                "no_shows": 0,
                "takings_cents": 0,
                "commission_bp": store.commission_bp(connection, row["staff_id"]),
            },
        )
        if row["status"] == "no_show":
            person["no_shows"] += 1
            continue
        if row["payment_kind"] == "package" and row["customer_package_id"]:
            price, sessions = package_price.get(row["customer_package_id"], (0, 1))
            takings = round(price / sessions)
        else:
            takings = int(row["paid_cents"] or 0)
        # The rate that applied when the appointment took place (a later change does not rewrite it).
        rate = store.rate_at(
            rates.get(row["staff_id"], []), row["start_at"], person["commission_bp"]
        )
        commission = kit.percent_of(takings, rate)
        person["appointments"] += 1
        person["takings_cents"] += takings
        lines.append(
            {
                "day": row["local_start"],
                "staff_id": row["staff_id"],
                "staff_name": person["name"],
                "customer_name": row["customer_name"],
                "service_name": row["service_name"],
                "reference": row["reference"],
                "payment_kind": row["payment_kind"] or "unpaid",
                "takings_cents": takings,
                "commission_percent": rate / 100,
                "commission_cents": commission,
            }
        )
    team = []
    for person in people.values():
        person["commission_percent"] = person["commission_bp"] / 100
        # The rates these appointments were paid at (more than one: the rate changed within these days).
        person["rates"] = list(
            dict.fromkeys(
                line["commission_percent"]
                for line in lines
                if line["staff_id"] == person["staff_id"]
            )
        )  # in the order they applied
        person["commission_cents"] = sum(
            line["commission_cents"]
            for line in lines
            if line["staff_id"] == person["staff_id"]
        )
        team.append(person)
    team.sort(key=lambda item: (-item["takings_cents"], item["name"].lower()))
    return {
        "from": first,
        "to": last,
        "team": team,
        "lines": lines,
        "totals": {
            "appointments": sum(p["appointments"] for p in team),
            "no_shows": sum(p["no_shows"] for p in team),
            "takings_cents": sum(p["takings_cents"] for p in team),
            "commission_cents": sum(p["commission_cents"] for p in team),
        },
        "currency": kit.business(connection)["currency"],
    }


def earnings_csv(
    connection: sqlite3.Connection, from_day: str = "", to_day: str = ""
) -> bytes:
    report = earnings(connection, from_day, to_day)
    return kit.csv_bytes(
        report["lines"],
        (
            "day",
            "staff_name",
            "customer_name",
            "service_name",
            "reference",
            "payment_kind",
            "takings_cents",
            "commission_percent",
            "commission_cents",
        ),
    )


# ---------------------------------------------------------------------------
# the overview and the hub
# ---------------------------------------------------------------------------


def overview(
    connection: sqlite3.Connection, now: datetime | None = None
) -> dict[str, Any]:
    store.ensure(connection)
    tz = store.zone(connection)
    moment = (now or kit.utcnow()).astimezone(UTC)
    today = moment.astimezone(tz).date()
    day_start, day_end = _local_day_bounds(today, tz)
    today_rows = kit.fetch_all(
        connection,
        "SELECT * FROM bk_bookings WHERE start_at >= ? AND start_at < ? AND status != 'cancelled' ORDER BY start_at",
        (kit.iso(day_start), kit.iso(day_end)),
    )
    for row in today_rows:
        row["local_time"] = _parse(row["start_at"]).astimezone(tz).strftime("%H:%M")
    week = kit.count(
        connection,
        "SELECT COUNT(*) FROM bk_bookings WHERE status IN ('booked', 'confirmed') AND start_at >= ? AND start_at < ?",
        (kit.iso(moment), kit.iso(moment + timedelta(days=7))),
    )
    since = kit.iso(moment - timedelta(days=90))
    done = kit.count(
        connection,
        "SELECT COUNT(*) FROM bk_bookings WHERE status = 'completed' AND start_at >= ?",
        (since,),
    )
    missed = kit.count(
        connection,
        "SELECT COUNT(*) FROM bk_bookings WHERE status = 'no_show' AND start_at >= ?",
        (since,),
    )
    to_confirm = kit.count(
        connection,
        "SELECT COUNT(*) FROM bk_bookings WHERE status = 'booked' AND start_at >= ?",
        (kit.iso(moment),),
    )
    # Completed visits that still owe money: nothing paid, or only the deposit.
    unpaid = kit.count(
        connection,
        "SELECT COUNT(*) FROM bk_bookings WHERE status = 'completed' AND paid_cents < price_cents AND payment_kind != 'package' AND price_cents > 0",
    )
    busiest = kit.fetch_all(
        connection,
        "SELECT service_name, COUNT(*) AS bookings FROM bk_bookings WHERE status != 'cancelled' AND start_at >= ? GROUP BY service_id ORDER BY bookings DESC LIMIT 5",
        (kit.iso(moment - timedelta(days=30)),),
    )
    upcoming = list_bookings(connection, when="upcoming", limit=10)
    upcoming = [row for row in upcoming if row["status"] in ACTIVE_STATUSES]
    revenue = kit.revenue(connection, app=APP, moment=moment)
    waiting = kit.count(
        connection,
        "SELECT COUNT(*) FROM bk_waitlist WHERE status = 'waiting' AND day >= ?",
        (today.isoformat(),),
    )
    return {
        "waiting": waiting,
        "today": today.isoformat(),
        "timezone": store.zone_name(connection),
        "today_bookings": today_rows,
        "upcoming": upcoming,
        "bookings_this_week": week,
        "to_confirm": to_confirm,
        "unpaid_completed": unpaid,
        "no_show_rate": round(missed * 100 / (done + missed))
        if done + missed
        else None,
        "busiest": busiest,
        "revenue": revenue,
        "page_link": public.link_for(connection, APP, PAGE_KIND, PAGE_REF),
        "checklist": checklist(connection),
    }


def checklist(connection: sqlite3.Connection) -> list[dict[str, Any]]:
    store.ensure(connection)
    profile = kit.business(connection)
    return [
        kit.step(
            "profile",
            "Add your business details",
            bool(profile["name"]),
            "Business hub > Business profile",
        ),
        kit.step(
            "service",
            "Add a service",
            kit.count(connection, "SELECT COUNT(*) FROM bk_services WHERE active = 1")
            > 0,
            "Name, length, price and deposit",
        ),
        kit.step(
            "hours",
            "Set your hours",
            kit.count(connection, "SELECT COUNT(*) FROM bk_hours") > 0,
            "Staff > hours",
        ),
        kit.step(
            "share",
            "Share your booking page",
            public.link_for(connection, APP, PAGE_KIND, PAGE_REF) is not None,
            "Put it on your website, Instagram and Google profile",
        ),
        kit.step(
            "first",
            "Get your first booking",
            kit.count(connection, "SELECT COUNT(*) FROM bk_bookings") > 0,
            "Book a friend, or add a phone booking",
        ),
    ]


def summary(connection: sqlite3.Connection) -> dict[str, Any]:
    data = overview(connection)
    rate = data["no_show_rate"]
    cards = {
        "cards": [
            {"label": "Bookings this week", "value": data["bookings_this_week"]},
            {
                "label": "Revenue this month",
                "value": kit.fmt_money(
                    data["revenue"]["this_month_cents"], data["revenue"]["currency"]
                ),
            },
            {
                "label": "No-show rate",
                "value": "-" if rate is None else f"{rate}%",
                "tone": "warn" if rate and rate >= 10 else "",
            },
        ]
    }
    if data["waiting"]:
        cards["cards"].append(
            {"label": "On the waiting list", "value": data["waiting"], "tone": "warn"}
        )
    return cards


# ---------------------------------------------------------------------------
# what customers see
# ---------------------------------------------------------------------------


def public_booking(
    connection: sqlite3.Connection,
    booking: Mapping[str, Any],
    now: datetime | None = None,
) -> dict[str, Any]:
    """A booking as its own customer sees it (no internal notes, no staff contact details)."""
    may_change = customer_may_change(connection, booking, now)
    return {
        "reference": booking["reference"],
        "service_name": booking["service_name"],
        "staff_name": booking["staff_name"],
        "customer_name": booking["customer_name"],
        "start_at": booking["start_at"],
        "end_at": booking["end_at"],
        "local_start": local_label(booking["start_at"], store.zone(connection)),
        "status": booking["status"],
        "price_cents": int(booking["price_cents"]),
        "deposit_cents": int(booking["deposit_cents"]),
        "paid_cents": int(booking["paid_cents"]),
        "paid_by_package": booking["payment_kind"] == "package",
        "note": booking["note"],
        "can_change": may_change,
        # the reason the business gave when it cancelled (the cancel form says the customer sees it)
        "cancel_reason": booking["cancel_reason"]
        if booking["status"] == "cancelled"
        else "",
        "change_until": kit.iso(_deadline(connection, booking)),
    }


def public_page(
    connection: sqlite3.Connection, now: datetime | None = None
) -> dict[str, Any]:
    store.ensure(connection)
    rules = store.get_rules(connection)
    tz = store.zone(connection)
    services = []
    for service in store.list_services(connection, active_only=True):
        people = eligible_staff(connection, service)
        if not people:
            continue
        services.append(
            {
                "id": service["id"],
                "name": service["name"],
                "category": service["category"],
                "description": service["description"],
                "minutes": service["minutes"],
                "price_cents": service["price_cents"],
                "deposit_cents": service["deposit_cents"],
                "staff": [
                    {"id": person["id"], "name": person["name"]} for person in people
                ],
            }
        )
    return {
        "services": services,
        "timezone": store.zone_name(connection),
        "today": (now or kit.utcnow()).astimezone(tz).date().isoformat(),
        "max_days_ahead": int(rules["max_days_ahead"]),
        "min_notice_hours": int(rules["min_notice_hours"]),
        "cancel_notice_hours": int(rules["cancel_notice_hours"]),
        "welcome": rules["welcome"],
    }
