"""Health rules: who may see what, medicine schedules and dose slots, adherence, stock, caregivers, missed-dose
notices, appointments, readings with gentle hints, the mood diary and the emergency card.

Times of day are in the household's time zone (``timezone_info``: the Business hub's zone when one other than UTC
was chosen there, else the zone Health keeps, which the browser fills in the first time); stored instants are UTC. A
scheduled slot is *due* from its time, *missed* 60 minutes after it if nobody marked it, and a slot that was
already missed when the medicine was added does not count. Nothing here is medical advice.
"""

from __future__ import annotations

import sqlite3
from datetime import date, datetime, time, timedelta, timezone
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from aira.suite import kit, life
from aira.suite.care import store
from aira.suite.care.store import APP

MISSED_AFTER = timedelta(minutes=60)
FUTURE_GRACE = timedelta(
    minutes=10
)  # a device clock a little ahead is not "the future"
NOT_ADVICE = "This is general information, not medical advice. Talk to your doctor or nurse if you are worried."


# ---------------------------------------------------------------------------
# time
# ---------------------------------------------------------------------------


def _valid_zone(name: str) -> bool:
    try:
        ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError):
        return False
    return True


def timezone_info(connection: sqlite3.Connection) -> dict[str, str]:
    """The zone medicine times are read in, and where it came from.

    ``business``: the Business hub names a zone other than UTC (UTC there is also what saving the hub without
    touching the zone stores, so it does not count as a choice). ``health``: the zone Health keeps (set by the
    person, or by their browser the first time they open Health). ``default``: nothing set, UTC."""
    stored = kit.get_setting(connection, "core", "business", {}) or {}
    chosen = str(stored.get("timezone") or "") if isinstance(stored, dict) else ""
    if chosen and chosen != "UTC" and _valid_zone(chosen):
        return {"name": chosen, "source": "business"}
    mine = str(kit.get_setting(connection, APP, "timezone", "") or "")
    if mine and _valid_zone(mine):
        return {"name": mine, "source": "health"}
    return {"name": "UTC", "source": "default"}


def can_set_timezone(me: dict[str, Any]) -> bool:
    """Who may change the zone everyone's medicine times are read in: in the hosted build the organization's owner
    or an admin; at home the owner or another adult (not a child)."""
    from aira import suite

    actor = suite.actor()
    if actor:
        return str(actor.get("role") or "") in ("owner", "admin")
    return life.can_manage_household(me)


def set_timezone(
    connection: sqlite3.Connection, me: dict[str, Any], value: Any
) -> dict[str, str]:
    """Save the zone. The first time (nothing chosen yet) anyone's browser may fill it in; changing it later is for
    the people ``can_set_timezone`` names, because it moves everyone's reminders."""
    if timezone_info(connection)["source"] != "default" and not can_set_timezone(me):
        raise PermissionError(
            "Only the owner or an admin can change the time zone: it sets everyone's reminder times."
            if me.get("member_ref")
            else "Ask an adult to change the time zone: it sets the reminder times for everyone at home."
        )
    name = kit.text(value, "the time zone", max_len=60, required=True)
    if ("/" not in name and name != "UTC") or not _valid_zone(name):
        raise ValueError(
            f"{name} is not a time zone. Use a name like Asia/Kolkata or Europe/London."
        )
    kit.set_setting(connection, APP, "timezone", name)
    connection.commit()
    return timezone_info(connection)


def zone(connection: sqlite3.Connection) -> ZoneInfo:
    return ZoneInfo(timezone_info(connection)["name"])


def _now(now: datetime | None) -> datetime:
    moment = now or kit.utcnow()
    return moment if moment.tzinfo else moment.replace(tzinfo=timezone.utc)


def local_today(connection: sqlite3.Connection, now: datetime | None = None) -> date:
    return _now(now).astimezone(zone(connection)).date()


def slot_instant(connection: sqlite3.Connection, day: date, clock: str) -> datetime:
    """The UTC instant of ``clock`` (HH:MM) on ``day`` in the business time zone."""
    hour, minute = (int(part) for part in clock.split(":"))
    return datetime.combine(
        day, time(hour, minute), tzinfo=zone(connection)
    ).astimezone(timezone.utc)


def _parse_day(
    value: Any, connection: sqlite3.Connection, now: datetime | None = None
) -> date:
    text = kit.day(value, "the date")
    return date.fromisoformat(text) if text else local_today(connection, now)


# ---------------------------------------------------------------------------
# who may see whose health
# ---------------------------------------------------------------------------


def is_caregiver(
    connection: sqlite3.Connection, owner_id: str, caregiver_id: str
) -> bool:
    return (
        kit.count(
            connection,
            "SELECT COUNT(*) FROM cr_caregivers WHERE owner_id = ? AND caregiver_id = ?",
            (owner_id, caregiver_id),
        )
        > 0
    )


def subject(
    connection: sqlite3.Connection, me: dict[str, Any], person_id: str = ""
) -> tuple[dict[str, Any], bool]:
    """Whose health this request is about, and whether ``me`` may change it (only their own)."""
    if not person_id or person_id == me["id"]:
        return me, True
    try:
        person = life.get(connection, person_id)
    except LookupError:
        raise PermissionError(
            "You can see someone's health only if they made you their caregiver."
        ) from None
    if is_caregiver(connection, person["id"], me["id"]):
        return person, False
    raise PermissionError(
        "You can see someone's health only if they made you their caregiver."
    )


def require_own(can_edit: bool) -> None:
    if not can_edit:
        raise PermissionError(
            "Only the person themselves can change this. Caregivers can look, not change."
        )


def caring_for(
    connection: sqlite3.Connection, me: dict[str, Any], now: datetime | None = None
) -> list[dict[str, Any]]:
    """The people who made ``me`` their caregiver, with how their day is going."""
    rows = kit.fetch_all(
        connection,
        "SELECT DISTINCT owner_id FROM cr_caregivers WHERE caregiver_id = ?",
        (me["id"],),
    )
    out = []
    for row in rows:
        try:
            person = life.get(connection, row["owner_id"])
        except LookupError:
            continue
        slots = day_slots(connection, person["id"], local_today(connection, now), now)
        out.append(
            {
                **life.public(person),
                "missed_today": sum(1 for slot in slots if slot["status"] == "missed"),
                "taken_today": sum(1 for slot in slots if slot["status"] == "taken"),
                "due_today": len(slots),
                "adherence_7": adherence(connection, person["id"], 7, now)["percent"],
            }
        )
    return out


# ---------------------------------------------------------------------------
# medicines
# ---------------------------------------------------------------------------


def create_medicine(
    connection: sqlite3.Connection, me: dict[str, Any], raw: dict[str, Any]
) -> dict[str, Any]:
    values = store.clean_medicine(raw)
    if (
        kit.count(
            connection,
            "SELECT COUNT(*) FROM cr_medicines WHERE owner_id = ?",
            (me["id"],),
        )
        >= 60
    ):
        raise ValueError("You can keep at most 60 medicines.")
    medicine_id = kit.new_id()
    stamp = kit.now()
    connection.execute(
        """INSERT INTO cr_medicines (id, owner_id, name, dose, form, times, days, start_date, end_date, as_needed,
        min_gap_hours, with_food, stock, refill_at, active, notes, created_at, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            medicine_id,
            me["id"],
            values["name"],
            values["dose"],
            values["form"],
            kit.dumps(values["times"]),
            kit.dumps(values["days"]),
            values["start_date"],
            values["end_date"],
            1 if values["as_needed"] else 0,
            values["min_gap_hours"],
            values["with_food"],
            values["stock"],
            values["refill_at"],
            1 if values["active"] else 0,
            values["notes"],
            stamp,
            stamp,
        ),
    )
    if _schedule_of(values) is not None:
        _open_schedule(connection, me["id"], medicine_id, values, None)
    kit.log(
        connection,
        APP,
        "medicine",
        f"Added a medicine: {values['name']}",
        actor=me["name"],
        ref_type="medicine",
        ref_id=medicine_id,
    )
    connection.commit()
    return store.get_medicine(connection, medicine_id, me["id"])


def update_medicine(
    connection: sqlite3.Connection,
    me: dict[str, Any],
    medicine_id: str,
    raw: dict[str, Any],
    now: datetime | None = None,
) -> dict[str, Any]:
    current = store.get_medicine(connection, medicine_id, me["id"])
    values = store.clean_medicine(raw, current)
    _keep_schedule(connection, current)
    if _schedule_of(current) != _schedule_of(values):
        # The old times stay true for the days they held; the new ones start now.
        stamp = kit.iso(_now(now))
        _close_schedule(connection, current, stamp)
        if _schedule_of(values) is not None:
            _open_schedule(connection, me["id"], medicine_id, values, stamp)
    kit.update_row(
        connection,
        "cr_medicines",
        medicine_id,
        {
            **{
                k: v
                for k, v in values.items()
                if k not in ("times", "days", "as_needed", "active")
            },
            "times": kit.dumps(values["times"]),
            "days": kit.dumps(values["days"]),
            "as_needed": 1 if values["as_needed"] else 0,
            "active": 1 if values["active"] else 0,
            "updated_at": kit.now(),
        },
    )
    connection.commit()
    return store.get_medicine(connection, medicine_id, me["id"])


def delete_medicine(
    connection: sqlite3.Connection,
    me: dict[str, Any],
    medicine_id: str,
    now: datetime | None = None,
) -> str:
    """A medicine with a history is paused (the history stays); one never taken is deleted. Deleting a medicine
    that is already stopped removes it with its history and refills."""
    medicine = store.get_medicine(connection, medicine_id, me["id"])
    if not medicine["active"]:
        for table in ("cr_doses", "cr_refills", "cr_missed_notices", "cr_schedules"):
            connection.execute(
                f"DELETE FROM {table} WHERE medicine_id = ?",  # noqa: S608
                (medicine_id,),
            )
        connection.execute("DELETE FROM cr_medicines WHERE id = ?", (medicine_id,))
        outcome = "deleted"
    elif kit.count(
        connection,
        "SELECT COUNT(*) FROM cr_doses WHERE medicine_id = ?",
        (medicine_id,),
    ):
        _close_schedule(connection, medicine, kit.iso(_now(now)))
        kit.update_row(
            connection,
            "cr_medicines",
            medicine_id,
            {"active": 0, "updated_at": kit.now()},
        )
        outcome = "paused"
    else:
        connection.execute("DELETE FROM cr_medicines WHERE id = ?", (medicine_id,))
        connection.execute(
            "DELETE FROM cr_schedules WHERE medicine_id = ?", (medicine_id,)
        )
        outcome = "deleted"
    kit.log(
        connection,
        APP,
        "medicine",
        f"{outcome.title()} {medicine['name']}",
        actor=me["name"],
        ref_type="medicine",
        ref_id=medicine_id,
    )
    connection.commit()
    return outcome


def scheduled_on(medicine: dict[str, Any], day: date) -> bool:
    """Whether the medicine's current schedule has doses on ``day``."""
    if not medicine["active"] or medicine["as_needed"] or not medicine["times"]:
        return False
    return _on_day(medicine, day)


def _on_day(schedule: dict[str, Any], day: date) -> bool:
    if schedule["start_date"] and day.isoformat() < schedule["start_date"]:
        return False
    if schedule["end_date"] and day.isoformat() > schedule["end_date"]:
        return False
    return not schedule["days"] or day.weekday() in schedule["days"]


# ---------------------------------------------------------------------------
# a medicine's schedule over time (cr_schedules)
# ---------------------------------------------------------------------------


def _schedule_of(values: dict[str, Any]) -> tuple[Any, ...] | None:
    """What decides when doses are due; None while the medicine is stopped or only taken when needed."""
    if not values["active"] or values["as_needed"] or not values["times"]:
        return None
    return (
        tuple(values["times"]),
        tuple(values["days"]),
        values["start_date"],
        values["end_date"],
    )


def _open_schedule(
    connection: sqlite3.Connection,
    owner_id: str,
    medicine_id: str,
    values: dict[str, Any],
    from_at: str | None,
) -> None:
    connection.execute(
        """INSERT INTO cr_schedules (id, owner_id, medicine_id, times, days, start_date, end_date, from_at, to_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, NULL)""",
        (
            kit.new_id(),
            owner_id,
            medicine_id,
            kit.dumps(list(values["times"])),
            kit.dumps(list(values["days"])),
            values["start_date"],
            values["end_date"],
            from_at,
        ),
    )


def _keep_schedule(connection: sqlite3.Connection, medicine: dict[str, Any]) -> None:
    """A medicine added before schedules were kept gets its one schedule stored (from the day it was added, until
    it was stopped), before anything about it changes."""
    if medicine["as_needed"] or not medicine["times"]:
        return
    if kit.count(
        connection,
        "SELECT COUNT(*) FROM cr_schedules WHERE medicine_id = ?",
        (medicine["id"],),
    ):
        return
    _open_schedule(connection, medicine["owner_id"], medicine["id"], medicine, None)
    if not medicine["active"]:
        connection.execute(
            "UPDATE cr_schedules SET to_at = ? WHERE medicine_id = ?",
            (medicine["updated_at"], medicine["id"]),
        )


def _close_schedule(
    connection: sqlite3.Connection, medicine: dict[str, Any], stamp: str
) -> None:
    """End the schedule that holds now."""
    _keep_schedule(connection, medicine)
    connection.execute(
        "UPDATE cr_schedules SET to_at = ? WHERE medicine_id = ? AND to_at IS NULL",
        (stamp, medicine["id"]),
    )


def _plan(
    row: dict[str, Any], added: datetime, times: Any, days: Any
) -> dict[str, Any]:
    return {
        "times": times,
        "days": days,
        "start_date": row["start_date"],
        "end_date": row["end_date"],
        "from": kit.parse_time(row["from_at"]) if row["from_at"] else added,
        "to": kit.parse_time(row["to_at"]) if row["to_at"] else None,
    }


def schedules(
    connection: sqlite3.Connection, owner_id: str, medicines: list[dict[str, Any]]
) -> dict[str, list[dict[str, Any]]]:
    """Each medicine's schedules over time, with ``from``/``to`` as instants (``to`` None while it holds).

    A medicine added before schedules were kept has none stored: its current times count from the day it was
    added (until it was stopped, for a stopped one)."""
    stored: dict[str, list[dict[str, Any]]] = {}
    for row in kit.fetch_all(
        connection,
        "SELECT * FROM cr_schedules WHERE owner_id = ? ORDER BY from_at",
        (owner_id,),
    ):
        stored.setdefault(row["medicine_id"], []).append(row)
    out: dict[str, list[dict[str, Any]]] = {}
    for medicine in medicines:
        added = kit.parse_time(medicine["created_at"])
        rows = stored.get(medicine["id"])
        if rows is not None:
            out[medicine["id"]] = [
                _plan(
                    row,
                    added,
                    kit.loads(row["times"], []) or [],
                    kit.loads(row["days"], []) or [],
                )
                for row in rows
            ]
        elif not medicine["as_needed"] and medicine["times"]:
            legacy = {
                "start_date": medicine["start_date"],
                "end_date": medicine["end_date"],
                "from_at": None,
                "to_at": None if medicine["active"] else medicine["updated_at"],
            }
            out[medicine["id"]] = [
                _plan(legacy, added, medicine["times"], medicine["days"])
            ]
    return out


def _status(at: datetime, dose: dict[str, Any] | None, now: datetime) -> str:
    if dose:
        if dose["status"] in ("taken", "skipped"):
            return dose["status"]
        if (
            dose["status"] == "snoozed"
            and dose.get("snooze_until")
            and kit.parse_time(dose["snooze_until"]) > now
        ):
            return "snoozed"
    if now < at:
        return "upcoming"
    if now < at + MISSED_AFTER:
        return "due"
    return "missed"


def _slot_times(
    connection: sqlite3.Connection, plans: list[dict[str, Any]], day: date
) -> dict[str, datetime]:
    """The times of day a medicine was due on ``day`` under the schedules that held then.

    A slot counts under a schedule only if the schedule began before the slot would have been missed (a time
    already missed when it was set does not count) and still held when it would have been missed (a dose that was
    due but not yet missed when the time changed or the medicine stopped does not count either)."""
    out: dict[str, datetime] = {}
    for plan in plans:
        if not _on_day(plan, day):
            continue
        for clock in plan["times"]:
            at = slot_instant(connection, day, clock)
            if at + MISSED_AFTER < plan["from"]:
                continue
            if plan["to"] is not None and at + MISSED_AFTER > plan["to"]:
                continue
            out.setdefault(clock, at)
    return out


def day_slots(
    connection: sqlite3.Connection,
    owner_id: str,
    day: date,
    now: datetime | None = None,
    *,
    stopped: bool = False,
) -> list[dict[str, Any]]:
    """Every scheduled dose of ``day`` for one person, with its state (taken, skipped, snoozed, upcoming, due,
    missed), under the schedule each medicine had on that day. A dose marked taken or skipped always counts, even
    if its time was changed later. ``stopped`` also includes the medicines that are stopped now (for the history
    and the numbers; Today shows only the medicines still taken)."""
    moment = _now(now)
    doses = {
        (row["medicine_id"], row["slot_time"]): row
        for row in kit.fetch_all(
            connection,
            "SELECT * FROM cr_doses WHERE owner_id = ? AND slot_date = ? AND slot_time != ''",
            (owner_id, day.isoformat()),
        )
    }
    medicines = [
        medicine
        for medicine in store.medicines(connection, owner_id)
        if medicine["active"] or stopped
    ]
    plans = schedules(connection, owner_id, medicines)
    slots = []
    for medicine in medicines:
        due = _slot_times(connection, plans.get(medicine["id"], []), day)
        for (medicine_id, clock), dose in doses.items():
            if (
                medicine_id == medicine["id"]
                and clock not in due
                and dose["status"] in ("taken", "skipped")
            ):
                due[clock] = slot_instant(connection, day, clock)
        for clock, at in due.items():
            dose = doses.get((medicine["id"], clock))
            slots.append(
                {
                    "medicine_id": medicine["id"],
                    "name": medicine["name"],
                    "dose": medicine["dose"],
                    "form": medicine["form"],
                    "with_food": medicine["with_food"],
                    "active": medicine["active"],
                    "date": day.isoformat(),
                    "time": clock,
                    "at": kit.iso(at),
                    "status": _status(at, dose, moment),
                    "taken_at": dose["at"]
                    if dose and dose["status"] == "taken"
                    else None,
                    "reason": dose["reason"] if dose else "",
                }
            )
    return sorted(slots, key=lambda slot: (slot["time"], slot["name"].lower()))


def _slot_for(
    connection: sqlite3.Connection, me: dict[str, Any], raw: dict[str, Any]
) -> tuple[dict[str, Any], date, str]:
    """The dose a button marks: one the medicine has on that day, under its schedule now or the one it had then."""
    medicine = store.get_medicine(
        connection, str(raw.get("medicine_id") or ""), me["id"]
    )
    day = _parse_day(raw.get("date"), connection)
    clock = store.clock(raw.get("time"))
    if not (scheduled_on(medicine, day) and clock in medicine["times"]):
        plans = schedules(connection, me["id"], [medicine]).get(medicine["id"], [])
        if clock not in _slot_times(connection, plans, day):
            raise ValueError(
                f"{medicine['name']} is not due at {clock} on {day.isoformat()}."
            )
    return medicine, day, clock


def _upsert_dose(
    connection: sqlite3.Connection,
    me: dict[str, Any],
    medicine: dict[str, Any],
    day: date,
    clock: str,
    status: str,
    *,
    reason: str = "",
    snooze_until: str | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    stamp = kit.iso(_now(now))
    existing = kit.fetch_one(
        connection,
        "SELECT * FROM cr_doses WHERE medicine_id = ? AND slot_date = ? AND slot_time = ?",
        (medicine["id"], day.isoformat(), clock),
    )
    takes_stock = (
        status == "taken"
        and medicine["stock"] is not None
        and not (existing and existing["stock_taken"])
    )
    gives_back = (
        existing
        and existing["stock_taken"]
        and status != "taken"
        and medicine["stock"] is not None
    )
    if existing:
        if existing["status"] == status and status in ("taken", "skipped"):
            return existing  # pressing Taken twice changes nothing
        connection.execute(
            "UPDATE cr_doses SET status = ?, at = ?, reason = ?, snooze_until = ?, stock_taken = ?, updated_at = ? WHERE id = ?",
            (
                status,
                stamp,
                reason,
                snooze_until,
                1
                if (takes_stock or (existing["stock_taken"] and status == "taken"))
                else 0,
                stamp,
                existing["id"],
            ),
        )
        dose_id = existing["id"]
    else:
        dose_id = kit.new_id()
        connection.execute(
            """INSERT INTO cr_doses (id, owner_id, medicine_id, slot_date, slot_time, status, at, reason, snooze_until,
            stock_taken, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                dose_id,
                me["id"],
                medicine["id"],
                day.isoformat(),
                clock,
                status,
                stamp,
                reason,
                snooze_until,
                1 if takes_stock else 0,
                stamp,
                stamp,
            ),
        )
    if takes_stock:
        connection.execute(
            "UPDATE cr_medicines SET stock = MAX(0, stock - 1) WHERE id = ?",
            (medicine["id"],),
        )
    elif gives_back:
        connection.execute(
            "UPDATE cr_medicines SET stock = stock + 1 WHERE id = ?", (medicine["id"],)
        )
    connection.commit()
    return (
        kit.fetch_one(connection, "SELECT * FROM cr_doses WHERE id = ?", (dose_id,))
        or {}
    )


def take(
    connection: sqlite3.Connection,
    me: dict[str, Any],
    raw: dict[str, Any],
    now: datetime | None = None,
) -> dict[str, Any]:
    """Mark a scheduled dose taken. Idempotent: the stock goes down once."""
    medicine, day, clock = _slot_for(connection, me, raw)
    dose = _upsert_dose(connection, me, medicine, day, clock, "taken", now=now)
    kit.log(
        connection,
        APP,
        "dose",
        f"Took {medicine['name']} ({clock})",
        actor=me["name"],
        ref_type="medicine",
        ref_id=medicine["id"],
    )
    connection.commit()
    return {
        "dose": dose,
        "medicine": store.get_medicine(connection, medicine["id"], me["id"]),
    }


def skip(
    connection: sqlite3.Connection,
    me: dict[str, Any],
    raw: dict[str, Any],
    now: datetime | None = None,
) -> dict[str, Any]:
    medicine, day, clock = _slot_for(connection, me, raw)
    reason = kit.text(raw.get("reason"), "a reason", max_len=200)
    dose = _upsert_dose(
        connection, me, medicine, day, clock, "skipped", reason=reason, now=now
    )
    return {"dose": dose}


def snooze(
    connection: sqlite3.Connection,
    me: dict[str, Any],
    raw: dict[str, Any],
    now: datetime | None = None,
) -> dict[str, Any]:
    medicine, day, clock = _slot_for(connection, me, raw)
    minutes = kit.integer(
        raw.get("minutes"), "minutes", default=10, minimum=5, maximum=120
    )
    until = kit.iso(_now(now) + timedelta(minutes=minutes))
    existing = kit.fetch_one(
        connection,
        "SELECT status FROM cr_doses WHERE medicine_id = ? AND slot_date = ? AND slot_time = ?",
        (medicine["id"], day.isoformat(), clock),
    )
    if existing and existing["status"] in ("taken", "skipped"):
        raise ValueError("That dose is already marked.")
    dose = _upsert_dose(
        connection, me, medicine, day, clock, "snoozed", snooze_until=until, now=now
    )
    return {"dose": dose, "until": until}


def take_as_needed(
    connection: sqlite3.Connection,
    me: dict[str, Any],
    raw: dict[str, Any],
    now: datetime | None = None,
) -> dict[str, Any]:
    """Log an as-needed dose. It is always recorded; a dose sooner than the medicine's minimum gap comes back
    with a warning to show."""
    medicine = store.get_medicine(
        connection, str(raw.get("medicine_id") or ""), me["id"]
    )
    if not medicine["as_needed"]:
        raise ValueError(
            f"{medicine['name']} has set times; mark the dose on its time instead."
        )
    moment = _now(now)
    last = kit.fetch_one(
        connection,
        "SELECT at FROM cr_doses WHERE medicine_id = ? AND slot_time = '' AND status = 'taken' ORDER BY at DESC LIMIT 1",
        (medicine["id"],),
    )
    warning = ""
    if last and medicine["min_gap_hours"]:
        since = moment - kit.parse_time(last["at"])
        if since < timedelta(hours=medicine["min_gap_hours"]):
            warning = (
                f"Your last dose was {_ago(since)}; the usual gap for {medicine['name']} is "
                f"{medicine['min_gap_hours']} hours. Check the leaflet or ask your pharmacist."
            )
    dose_id = kit.new_id()
    stamp = kit.iso(moment)
    connection.execute(
        """INSERT INTO cr_doses (id, owner_id, medicine_id, slot_date, slot_time, status, at, reason, stock_taken, created_at, updated_at)
        VALUES (?, ?, ?, ?, '', 'taken', ?, ?, ?, ?, ?)""",
        (
            dose_id,
            me["id"],
            medicine["id"],
            local_today(connection, moment).isoformat(),
            stamp,
            kit.text(raw.get("note"), "a note", max_len=200),
            1 if medicine["stock"] is not None else 0,
            stamp,
            stamp,
        ),
    )
    if medicine["stock"] is not None:
        connection.execute(
            "UPDATE cr_medicines SET stock = MAX(0, stock - 1) WHERE id = ?",
            (medicine["id"],),
        )
    kit.log(
        connection,
        APP,
        "dose",
        f"Took {medicine['name']} (as needed)",
        actor=me["name"],
        ref_type="medicine",
        ref_id=medicine["id"],
    )
    connection.commit()
    return {
        "dose": kit.fetch_one(
            connection, "SELECT * FROM cr_doses WHERE id = ?", (dose_id,)
        ),
        "warning": warning,
    }


def _ago(span: timedelta) -> str:
    """3 minutes -> "3 minutes ago", 150 minutes -> "2 hours and 30 minutes ago" (never "0.0 hours")."""
    minutes = max(0, int(span.total_seconds() // 60))
    if minutes < 1:
        return "just now"
    hours, rest = divmod(minutes, 60)
    parts = []
    if hours:
        parts.append(f"{hours} hour{'s' if hours != 1 else ''}")
    if rest:
        parts.append(f"{rest} minute{'s' if rest != 1 else ''}")
    return " and ".join(parts) + " ago"


def history(
    connection: sqlite3.Connection, owner_id: str, days: int = 30
) -> list[dict[str, Any]]:
    since = (kit.utcnow() - timedelta(days=days)).date().isoformat()
    return kit.fetch_all(
        connection,
        """SELECT d.*, m.name AS medicine, m.dose AS strength FROM cr_doses d JOIN cr_medicines m ON m.id = d.medicine_id
        WHERE d.owner_id = ? AND d.slot_date >= ? ORDER BY d.slot_date DESC, d.slot_time DESC, d.at DESC LIMIT 500""",
        (owner_id, since),
    )


def adherence(
    connection: sqlite3.Connection,
    owner_id: str,
    days: int = 7,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Of the scheduled doses whose time has passed (missed or marked) in the last ``days`` days, how many were
    taken. Doses still to come today do not count."""
    today = local_today(connection, now)
    counts = {"taken": 0, "skipped": 0, "missed": 0}
    for offset in range(days):
        for slot in day_slots(
            connection, owner_id, today - timedelta(days=offset), now, stopped=True
        ):
            if slot["status"] in counts:
                counts[slot["status"]] += 1
    total = sum(counts.values())
    return {
        "days": days,
        **counts,
        "counted": total,
        "percent": round(counts["taken"] * 100 / total) if total else None,
    }


def per_day(medicine: dict[str, Any]) -> float:
    """How many a scheduled medicine uses on an average day (0 for 'as needed')."""
    if medicine["as_needed"] or not medicine["times"]:
        return 0.0
    return len(medicine["times"]) * (len(medicine["days"]) or 7) / 7


def supply(
    connection: sqlite3.Connection,
    medicine: dict[str, Any],
    now: datetime | None = None,
) -> dict[str, Any]:
    """How long the count left lasts: ``days_left`` and the date it runs out, when both are known."""
    use = per_day(medicine)
    if medicine["stock"] is None or not use:
        return {"days_left": None, "runs_out": None}
    days_left = int(medicine["stock"] // use)
    return {
        "days_left": days_left,
        "runs_out": (
            local_today(connection, now) + timedelta(days=days_left)
        ).isoformat(),
    }


REFILL_SOON_DAYS = 5


def is_low(medicine: dict[str, Any], days_left: int | None) -> bool:
    """Time to refill: the count reached the number the person chose; with none chosen, when fewer than
    ``REFILL_SOON_DAYS`` days are left (or none at all)."""
    if medicine["stock"] is None:
        return False
    if medicine["refill_at"]:
        return medicine["stock"] <= medicine["refill_at"]
    return medicine["stock"] <= 0 or (
        days_left is not None and days_left < REFILL_SOON_DAYS
    )


def low_stock(
    connection: sqlite3.Connection, owner_id: str, now: datetime | None = None
) -> list[dict[str, Any]]:
    out = []
    for medicine in store.medicines(connection, owner_id, active_only=True):
        left = supply(connection, medicine, now)
        if is_low(medicine, left["days_left"]):
            out.append({**medicine, **left})
    return out


def refills(
    connection: sqlite3.Connection, medicine_id: str, limit: int = 5
) -> list[dict[str, Any]]:
    return kit.fetch_all(
        connection,
        "SELECT id, amount, note, at FROM cr_refills WHERE medicine_id = ? ORDER BY at DESC LIMIT ?",
        (medicine_id, limit),
    )


def refill(
    connection: sqlite3.Connection,
    me: dict[str, Any],
    medicine_id: str,
    raw: dict[str, Any],
) -> dict[str, Any]:
    """A new box or bottle: add it to the count left (a count is started if none was kept)."""
    medicine = store.get_medicine(connection, medicine_id, me["id"])
    amount = kit.integer(
        raw.get("amount"), "how many you got", minimum=1, maximum=10000
    )
    note = kit.text(raw.get("note"), "a note", max_len=120)
    stock = min((medicine["stock"] or 0) + amount, 100000)
    stamp = kit.now()
    connection.execute(
        "INSERT INTO cr_refills (id, owner_id, medicine_id, amount, note, at) VALUES (?, ?, ?, ?, ?, ?)",
        (kit.new_id(), me["id"], medicine_id, amount, note, stamp),
    )
    connection.execute(
        "UPDATE cr_medicines SET stock = ?, updated_at = ? WHERE id = ?",
        (stock, stamp, medicine_id),
    )
    kit.log(
        connection,
        APP,
        "refill",
        f"Refilled {medicine['name']} (+{amount})",
        actor=me["name"],
        ref_type="medicine",
        ref_id=medicine_id,
    )
    connection.commit()
    return medicine_view(
        connection, store.get_medicine(connection, medicine_id, me["id"])
    )


def medicine_view(
    connection: sqlite3.Connection,
    medicine: dict[str, Any],
    now: datetime | None = None,
) -> dict[str, Any]:
    """A medicine with how long it lasts, whether to refill, and its last refills."""
    left = supply(connection, medicine, now)
    return {
        **medicine,
        **left,
        "per_day": round(per_day(medicine), 2),
        "low": medicine["active"] and is_low(medicine, left["days_left"]),
        "refills": refills(connection, medicine["id"]),
    }


def medicine_adherence(
    connection: sqlite3.Connection,
    owner_id: str,
    days: int = 30,
    now: datetime | None = None,
) -> dict[str, dict[str, Any]]:
    """Taken, skipped and missed per medicine over the last ``days`` days (doses whose time has passed)."""
    today = local_today(connection, now)
    out: dict[str, dict[str, Any]] = {}
    for offset in range(days):
        for slot in day_slots(
            connection, owner_id, today - timedelta(days=offset), now, stopped=True
        ):
            if slot["status"] not in ("taken", "skipped", "missed"):
                continue
            row = out.setdefault(
                slot["medicine_id"],
                {
                    "name": slot["name"],
                    "dose": slot["dose"],
                    "taken": 0,
                    "skipped": 0,
                    "missed": 0,
                },
            )
            row[slot["status"]] += 1
    for row in out.values():
        counted = row["taken"] + row["skipped"] + row["missed"]
        row["counted"] = counted
        row["percent"] = round(row["taken"] * 100 / counted) if counted else None
    return out


# ---------------------------------------------------------------------------
# caregivers and missed doses
# ---------------------------------------------------------------------------


def caregivers(connection: sqlite3.Connection, owner_id: str) -> list[dict[str, Any]]:
    rows = kit.fetch_all(
        connection,
        "SELECT * FROM cr_caregivers WHERE owner_id = ? ORDER BY created_at",
        (owner_id,),
    )
    for row in rows:
        row["notify_missed"] = bool(row["notify_missed"])
        if row["caregiver_id"]:
            try:
                person = life.get(connection, row["caregiver_id"])
                row["name"] = person["name"]
                row["emoji"] = person["emoji"]
            except LookupError:
                row["name"] = row["name"] or "Someone who left"
    return rows


def add_caregiver(
    connection: sqlite3.Connection, me: dict[str, Any], raw: dict[str, Any]
) -> dict[str, Any]:
    profile_id = str(raw.get("profile_id") or "")
    email = kit.email(raw.get("email"))
    name = kit.text(raw.get("name"), "a name", max_len=60)
    if profile_id:
        if profile_id == me["id"]:
            raise ValueError("You cannot be your own caregiver.")
        person = life.get(connection, profile_id)
        name = person["name"]
        if is_caregiver(connection, me["id"], profile_id):
            raise ValueError(f"{name} is already your caregiver.")
    elif not email:
        raise ValueError("Choose someone in the household, or enter an email address.")
    if (
        kit.count(
            connection,
            "SELECT COUNT(*) FROM cr_caregivers WHERE owner_id = ?",
            (me["id"],),
        )
        >= 10
    ):
        raise ValueError("You can have at most 10 caregivers.")
    caregiver_id = kit.new_id()
    connection.execute(
        "INSERT INTO cr_caregivers (id, owner_id, caregiver_id, email, name, notify_missed, created_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
        (
            caregiver_id,
            me["id"],
            profile_id or None,
            email,
            name or email,
            1 if kit.flag(raw.get("notify_missed", True)) else 0,
            kit.now(),
        ),
    )
    kit.log(
        connection,
        APP,
        "caregiver",
        f"{me['name']} made {name or email} a caregiver",
        actor=me["name"],
    )
    connection.commit()
    return next(
        row for row in caregivers(connection, me["id"]) if row["id"] == caregiver_id
    )


def remove_caregiver(
    connection: sqlite3.Connection, me: dict[str, Any], caregiver_row_id: str
) -> None:
    removed = connection.execute(
        "DELETE FROM cr_caregivers WHERE id = ? AND owner_id = ?",
        (caregiver_row_id, me["id"]),
    ).rowcount
    if not removed:
        raise LookupError("That caregiver was not found.")
    connection.commit()


def _missed_recent(
    connection: sqlite3.Connection, owner_id: str, now: datetime | None
) -> list[dict[str, Any]]:
    """Missed doses of today and yesterday, at most 18 hours old."""
    moment = _now(now)
    today = local_today(connection, moment)
    out = []
    for day in (today - timedelta(days=1), today):
        for slot in day_slots(connection, owner_id, day, moment):
            if slot["status"] == "missed" and moment - kit.parse_time(
                slot["at"]
            ) <= timedelta(hours=18):
                out.append(slot)
    return out


def notify_missed(
    connection: sqlite3.Connection, now: datetime | None = None, origin: Any = None
) -> int:
    """Tell caregivers (by email, through the outbox) about each missed dose, once. Returns how many."""
    owners = kit.fetch_all(
        connection,
        "SELECT owner_id, MIN(created_at) AS since FROM cr_caregivers WHERE notify_missed = 1 GROUP BY owner_id",
    )
    sent = 0
    for row in owners:
        try:
            person = life.get(connection, row["owner_id"])
        except LookupError:
            continue
        since = kit.parse_time(row["since"])
        for slot in _missed_recent(connection, person["id"], now):
            if kit.parse_time(slot["at"]) < since:
                continue
            claimed = connection.execute(
                "INSERT OR IGNORE INTO cr_missed_notices (medicine_id, slot_date, slot_time, owner_id, notified_at) VALUES (?, ?, ?, ?, ?)",
                (
                    slot["medicine_id"],
                    slot["date"],
                    slot["time"],
                    person["id"],
                    kit.now(),
                ),
            ).rowcount
            if not claimed:
                continue
            for caregiver in caregivers(connection, person["id"]):
                if not caregiver["notify_missed"]:
                    continue
                address = caregiver["email"]
                if caregiver["caregiver_id"] and not address:
                    try:
                        address = life.get(connection, caregiver["caregiver_id"])[
                            "email"
                        ]
                    except LookupError:
                        address = ""
                if not address:
                    continue
                kit.queue_email(
                    connection,
                    APP,
                    "missed_dose",
                    to=address,
                    subject=f"{person['name']} has not taken {slot['name']}",
                    body=(
                        f"Hello {caregiver['name'] or ''},\n\n"
                        f"{person['name']} has not marked {slot['name']} {slot['dose']} as taken. It was due at "
                        f"{slot['time']} on {slot['date']}.\n\nYou may want to check in with them.\n\n"
                        f"Open Aira: {kit.public_url('/#/care', origin)}\n\n"
                        f"You receive this because {person['name']} made you their caregiver in Aira Health."
                    ),
                    ref_type="medicine",
                    ref_id=slot["medicine_id"],
                )
                sent += 1
            kit.log(
                connection,
                APP,
                "missed",
                f"{person['name']} missed {slot['name']} ({slot['time']})",
                ref_type="medicine",
                ref_id=slot["medicine_id"],
            )
    connection.commit()
    return sent


# ---------------------------------------------------------------------------
# appointments
# ---------------------------------------------------------------------------


def _clean_appointment(
    raw: dict[str, Any], current: dict[str, Any] | None = None
) -> dict[str, Any]:
    merged = {**(current or {}), **raw}
    return {
        "title": kit.text(
            merged.get("title"),
            "who the appointment is with",
            max_len=100,
            required=True,
        ),
        "purpose": kit.text(merged.get("purpose"), "the purpose", max_len=200),
        "place": kit.text(merged.get("place"), "the place", max_len=200),
        "at": kit.moment(merged.get("at"), "the date and time", required=True),
        "notes": kit.text(merged.get("notes"), "notes", max_len=1000, multiline=True),
        "questions": kit.text(
            merged.get("questions"), "questions to ask", max_len=1000, multiline=True
        ),
        "done": 1 if kit.flag(merged.get("done")) else 0,
    }


def save_appointment(
    connection: sqlite3.Connection,
    me: dict[str, Any],
    raw: dict[str, Any],
    appointment_id: str = "",
) -> dict[str, Any]:
    if appointment_id:
        current = get_appointment(connection, me["id"], appointment_id)
        values = _clean_appointment(raw, current)
        kit.update_row(
            connection,
            "cr_appointments",
            appointment_id,
            {**values, "updated_at": kit.now()},
        )
    else:
        values = _clean_appointment(raw)
        appointment_id = kit.new_id()
        connection.execute(
            """INSERT INTO cr_appointments (id, owner_id, title, purpose, place, at, notes, questions, done, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                appointment_id,
                me["id"],
                values["title"],
                values["purpose"],
                values["place"],
                values["at"],
                values["notes"],
                values["questions"],
                values["done"],
                kit.now(),
                kit.now(),
            ),
        )
        kit.log(
            connection,
            APP,
            "appointment",
            f"Appointment with {values['title']}",
            actor=me["name"],
            ref_type="appointment",
            ref_id=appointment_id,
        )
    connection.commit()
    return get_appointment(connection, me["id"], appointment_id)


def get_appointment(
    connection: sqlite3.Connection, owner_id: str, appointment_id: str
) -> dict[str, Any]:
    row = kit.fetch_one(
        connection,
        "SELECT * FROM cr_appointments WHERE id = ? AND owner_id = ?",
        (appointment_id, owner_id),
    )
    if not row:
        raise LookupError("That appointment was not found.")
    row["done"] = bool(row["done"])
    return row


def _ics_text(value: str) -> str:
    """Escape text for a calendar file (RFC 5545)."""
    return (
        value.replace("\\", "\\\\")
        .replace(";", "\\;")
        .replace(",", "\\,")
        .replace("\r", "")
        .replace("\n", "\\n")
    )


def calendar_file(appointment: dict[str, Any]) -> bytes:
    """One appointment as an .ics file (an hour long, a reminder the day before and two hours before)."""
    start = kit.parse_time(appointment["at"]).astimezone(timezone.utc)
    end = start + timedelta(hours=1)
    stamp = kit.utcnow().strftime("%Y%m%dT%H%M%SZ")
    description = "\n".join(
        part
        for part in (
            appointment.get("purpose") or "",
            f"Questions to ask:\n{appointment['questions']}"
            if appointment.get("questions")
            else "",
            appointment.get("notes") or "",
        )
        if part
    )
    lines = [
        "BEGIN:VCALENDAR",
        "VERSION:2.0",
        "PRODID:-//Aira//Health//EN",
        "CALSCALE:GREGORIAN",
        "BEGIN:VEVENT",
        f"UID:{appointment['id']}@aira-health",
        f"DTSTAMP:{stamp}",
        f"DTSTART:{start.strftime('%Y%m%dT%H%M%SZ')}",
        f"DTEND:{end.strftime('%Y%m%dT%H%M%SZ')}",
        f"SUMMARY:{_ics_text(appointment['title'])}",
    ]
    if appointment.get("place"):
        lines.append(f"LOCATION:{_ics_text(appointment['place'])}")
    if description:
        lines.append(f"DESCRIPTION:{_ics_text(description)}")
    for trigger in ("-P1D", "-PT2H"):
        lines += [
            "BEGIN:VALARM",
            "ACTION:DISPLAY",
            f"DESCRIPTION:{_ics_text(appointment['title'])}",
            f"TRIGGER:{trigger}",
            "END:VALARM",
        ]
    lines += ["END:VEVENT", "END:VCALENDAR"]
    return ("\r\n".join(lines) + "\r\n").encode("utf-8")


def delete_appointment(
    connection: sqlite3.Connection, me: dict[str, Any], appointment_id: str
) -> None:
    get_appointment(connection, me["id"], appointment_id)
    connection.execute("DELETE FROM cr_appointments WHERE id = ?", (appointment_id,))
    connection.commit()


def appointments(
    connection: sqlite3.Connection, owner_id: str, now: datetime | None = None
) -> dict[str, list[dict[str, Any]]]:
    stamp = kit.iso(_now(now) - timedelta(hours=2))
    rows = kit.fetch_all(
        connection,
        "SELECT * FROM cr_appointments WHERE owner_id = ? ORDER BY at",
        (owner_id,),
    )
    for row in rows:
        row["done"] = bool(row["done"])
    upcoming = [row for row in rows if row["at"] >= stamp and not row["done"]]
    past = [row for row in rows if row not in upcoming][::-1][:50]
    return {"upcoming": upcoming, "past": past}


# ---------------------------------------------------------------------------
# readings
# ---------------------------------------------------------------------------

KINDS: dict[str, dict[str, Any]] = {
    "blood_pressure": {
        "label": "Blood pressure",
        "units": ["mmHg"],
        "fields": ["Top number", "Bottom number", "Pulse"],
    },
    "glucose": {
        "label": "Blood sugar",
        "units": ["mg/dL", "mmol/L"],
        "fields": ["Level"],
    },
    "weight": {"label": "Weight", "units": ["kg", "lb"], "fields": ["Weight"]},
    "temperature": {
        "label": "Temperature",
        "units": ["°C", "°F"],
        "fields": ["Temperature"],
    },
    "oxygen": {"label": "Oxygen", "units": ["%"], "fields": ["Oxygen"]},
    "steps": {"label": "Steps", "units": ["steps"], "fields": ["Steps"]},
    "water": {"label": "Water", "units": ["glasses"], "fields": ["Glasses"]},
    "sleep": {"label": "Sleep", "units": ["hours"], "fields": ["Hours"]},
}
_LIMITS: dict[tuple[str, str], tuple[float, float]] = {
    ("blood_pressure", "mmHg"): (40, 300),
    ("glucose", "mg/dL"): (10, 1000),
    ("glucose", "mmol/L"): (0.5, 55),
    ("weight", "kg"): (1, 400),
    ("weight", "lb"): (2, 900),
    ("temperature", "°C"): (30, 45),
    ("temperature", "°F"): (86, 113),
    ("oxygen", "%"): (50, 100),
    ("steps", "steps"): (0, 200000),
    ("water", "glasses"): (0, 40),
    ("sleep", "hours"): (0, 24),
}


def _number(
    value: Any, name: str, low: float, high: float, *, required: bool = True
) -> float | None:
    if value in (None, ""):
        if required:
            raise ValueError(f"Enter the {name.lower()}.")
        return None
    try:
        number = float(str(value).replace(",", "."))
    except ValueError:
        raise ValueError(f"The {name.lower()} must be a number.") from None
    if not low <= number <= high:
        raise ValueError(f"The {name.lower()} must be between {low:g} and {high:g}.")
    return number


def hint(kind: str, value1: float, value2: float | None, unit: str) -> str:
    """A gentle, general note for readings outside common ranges (never a diagnosis)."""
    note = ""
    if kind == "blood_pressure" and value2 is not None:
        if value1 >= 180 or value2 >= 120:
            note = "This is much higher than the usual range. If you feel unwell, get medical help now."
        elif value1 >= 140 or value2 >= 90:
            note = "This is above the usual range. Talk to your doctor if this keeps happening."
        elif value1 < 90 or value2 < 60:
            note = "This is below the usual range. Talk to your doctor if you feel dizzy or this keeps happening."
    elif kind == "glucose":
        high, low = (180, 70) if unit == "mg/dL" else (10, 3.9)
        if value1 > high:
            note = "This is above the usual range. Talk to your doctor if this keeps happening."
        elif value1 < low:
            note = "This is below the usual range. If you feel shaky or unwell, follow your plan for low blood sugar."
    elif kind == "temperature":
        fever, cold = (38, 35) if unit == "°C" else (100.4, 95)
        if value1 >= fever:
            note = "This is a raised temperature. Rest, drink water, and talk to your doctor if it lasts."
        elif value1 < cold:
            note = (
                "This is lower than usual. Keep warm, and get help if you feel unwell."
            )
    elif kind == "oxygen" and value1 < 94:
        note = "This is below the usual range. Talk to your doctor, and get help now if you are short of breath."
    elif kind == "sleep" and value1 < 6:
        note = "Most adults feel best with 7 to 9 hours of sleep."
    return f"{note} {NOT_ADVICE}" if note else ""


def add_reading(
    connection: sqlite3.Connection, me: dict[str, Any], raw: dict[str, Any]
) -> dict[str, Any]:
    kind = kit.choice(raw.get("kind"), "the kind of reading", tuple(KINDS))
    spec = KINDS[kind]
    unit = str(raw.get("unit") or spec["units"][0])
    if unit not in spec["units"]:
        raise ValueError(f"Choose a unit: {', '.join(spec['units'])}.")
    low, high = _LIMITS[(kind, unit)]
    value1 = _number(raw.get("value1"), spec["fields"][0], low, high)
    value2 = value3 = None
    if kind == "blood_pressure":
        value2 = _number(raw.get("value2"), "Bottom number", 20, 200)
        value3 = _number(raw.get("value3"), "Pulse", 20, 250, required=False)
        if value2 is not None and value1 is not None and value2 >= value1:
            raise ValueError(
                "The top number is usually bigger than the bottom number. Check the reading."
            )
    at = kit.moment(raw.get("at"), "the time") or kit.now()
    if kit.parse_time(at) > kit.utcnow() + FUTURE_GRACE:
        raise ValueError(
            "A reading cannot be in the future. Leave the time empty for now."
        )
    reading_id = kit.new_id()
    connection.execute(
        "INSERT INTO cr_readings (id, owner_id, kind, value1, value2, value3, unit, at, note, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            reading_id,
            me["id"],
            kind,
            value1,
            value2,
            value3,
            unit,
            at,
            kit.text(raw.get("note"), "a note", max_len=300),
            kit.now(),
        ),
    )
    connection.commit()
    return reading_view(
        kit.fetch_one(
            connection, "SELECT * FROM cr_readings WHERE id = ?", (reading_id,)
        )
        or {}
    )


def reading_view(row: dict[str, Any]) -> dict[str, Any]:
    if not row:
        return row
    row["label"] = KINDS.get(row["kind"], {}).get("label", row["kind"])
    row["hint"] = hint(row["kind"], float(row["value1"]), row["value2"], row["unit"])
    # The note alone (the page shows "not medical advice" once) and whether it is outside the usual range.
    row["note_short"] = row["hint"].replace(f" {NOT_ADVICE}", "")
    row["flagged"] = bool(row["hint"])
    if row["kind"] == "blood_pressure":
        row["display"] = f"{row['value1']:g}/{row['value2']:g} {row['unit']}" + (
            f", pulse {row['value3']:g}" if row.get("value3") is not None else ""
        )
    else:
        row["display"] = f"{row['value1']:g} {row['unit']}"
    return row


def readings(
    connection: sqlite3.Connection,
    owner_id: str,
    *,
    kind: str = "",
    days: int = 90,
    now: datetime | None = None,
) -> list[dict[str, Any]]:
    since = kit.iso(_now(now) - timedelta(days=days))
    until = kit.iso(
        _now(now) + FUTURE_GRACE
    )  # a reading dated ahead (an older Health allowed it) is left out
    params: list[Any] = [owner_id, since, until]
    where = ""
    if kind:
        kit.choice(kind, "the kind of reading", tuple(KINDS))
        where = "AND kind = ?"
        params.append(kind)
    rows = kit.fetch_all(
        connection,
        f"SELECT * FROM cr_readings WHERE owner_id = ? AND at >= ? AND at <= ? {where} ORDER BY at DESC LIMIT 500",  # noqa: S608
        params,
    )
    return [reading_view(row) for row in rows]


def latest_readings(
    connection: sqlite3.Connection, owner_id: str
) -> dict[str, dict[str, Any]]:
    out = {}
    for kind in KINDS:
        row = kit.fetch_one(
            connection,
            "SELECT * FROM cr_readings WHERE owner_id = ? AND kind = ? AND at <= ? ORDER BY at DESC LIMIT 1",
            (owner_id, kind, kit.iso(kit.utcnow() + FUTURE_GRACE)),
        )
        if row:
            out[kind] = reading_view(row)
    return out


def delete_reading(
    connection: sqlite3.Connection, me: dict[str, Any], reading_id: str
) -> None:
    removed = connection.execute(
        "DELETE FROM cr_readings WHERE id = ? AND owner_id = ?", (reading_id, me["id"])
    ).rowcount
    if not removed:
        raise LookupError("That reading was not found.")
    connection.commit()


# ---------------------------------------------------------------------------
# the diary
# ---------------------------------------------------------------------------


def save_day(
    connection: sqlite3.Connection, me: dict[str, Any], raw: dict[str, Any]
) -> dict[str, Any]:
    today = local_today(connection).isoformat()
    day = kit.day(raw.get("day"), "the day") or today
    if day > today:
        raise ValueError(
            "The diary is for days that have come. Choose today or a day before."
        )
    mood = kit.integer(raw.get("mood"), "your mood", minimum=1, maximum=5)
    symptoms = kit.tags(raw.get("symptoms"), limit=15)
    note = kit.text(raw.get("note"), "a note", max_len=1000, multiline=True)
    stamp = kit.now()
    connection.execute(
        """INSERT INTO cr_diary (id, owner_id, day, mood, symptoms, note, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(owner_id, day) DO UPDATE SET mood = excluded.mood, symptoms = excluded.symptoms, note = excluded.note,
        updated_at = excluded.updated_at""",
        (kit.new_id(), me["id"], day, mood, kit.dumps(symptoms), note, stamp, stamp),
    )
    connection.commit()
    return next(
        entry for entry in diary(connection, me["id"], days=4000) if entry["day"] == day
    )


def diary(
    connection: sqlite3.Connection,
    owner_id: str,
    days: int = 30,
    now: datetime | None = None,
) -> list[dict[str, Any]]:
    since = (_now(now) - timedelta(days=days)).date().isoformat()
    rows = kit.fetch_all(
        connection,
        "SELECT * FROM cr_diary WHERE owner_id = ? AND day >= ? AND day <= ? ORDER BY day DESC",
        (owner_id, since, local_today(connection, now).isoformat()),
    )
    for row in rows:
        row["symptoms"] = kit.loads(row["symptoms"], []) or []
        row["emoji"] = store.MOODS.get(int(row["mood"]), "")
    return rows


def delete_day(connection: sqlite3.Connection, me: dict[str, Any], day: str) -> None:
    removed = connection.execute(
        "DELETE FROM cr_diary WHERE owner_id = ? AND day = ?",
        (me["id"], kit.day(day, "the day", required=True)),
    ).rowcount
    if not removed:
        raise LookupError("There is no diary entry for that day.")
    connection.commit()


# ---------------------------------------------------------------------------
# the emergency card
# ---------------------------------------------------------------------------


def can_edit_card(
    connection: sqlite3.Connection, me: dict[str, Any], person: dict[str, Any]
) -> bool:
    """Your own card; or, in a household, an adult may keep a child's card."""
    if person["id"] == me["id"]:
        return True
    return person.get("age_group") == "child" and life.can_manage_household(me)


def card_person(
    connection: sqlite3.Connection, me: dict[str, Any], person_id: str
) -> tuple[dict[str, Any], bool]:
    """Whose card this is and whether ``me`` may change it (own, a child's for a household adult, or read-only
    for a caregiver)."""
    if person_id and person_id != me["id"]:
        try:
            person = life.get(connection, person_id)
        except LookupError:
            raise PermissionError("You cannot see that card.") from None
        if can_edit_card(connection, me, person):
            return person, True
    return subject(connection, me, person_id)


def card(connection: sqlite3.Connection, person: dict[str, Any]) -> dict[str, Any]:
    row = (
        kit.fetch_one(
            connection, "SELECT * FROM cr_cards WHERE owner_id = ?", (person["id"],)
        )
        or {}
    )
    return {
        "name": person["name"],
        "birthday": person.get("birthday", ""),
        "blood_type": row.get("blood_type", ""),
        "allergies": row.get("allergies", ""),
        "conditions": row.get("conditions", ""),
        "contacts": kit.loads(row.get("contacts"), []) or [],
        "doctor": row.get("doctor", ""),
        "doctor_phone": row.get("doctor_phone", ""),
        "notes": row.get("notes", ""),
        "medicines": [
            {"name": medicine["name"], "dose": medicine["dose"]}
            for medicine in store.medicines(connection, person["id"], active_only=True)
        ],
        "updated_at": row.get("updated_at", ""),
    }


def _blood_type(value: Any) -> str:
    text = str(value or "").strip()
    match = next(
        (option for option in store.BLOOD_TYPES if option.lower() == text.lower()), None
    )
    if match is None:
        raise ValueError(
            f"Choose a blood type: {', '.join(o for o in store.BLOOD_TYPES if o)}."
        )
    return match


def save_card(
    connection: sqlite3.Connection, person: dict[str, Any], raw: dict[str, Any]
) -> dict[str, Any]:
    contacts = []
    for item in (raw.get("contacts") or [])[:5]:
        if not isinstance(item, dict):
            continue
        name = kit.text(item.get("name"), "a contact's name", max_len=60)
        phone = kit.phone(item.get("phone"), "a contact's phone")
        if name or phone:
            if not (name and phone):
                raise ValueError(
                    "Each emergency contact needs a name and a phone number."
                )
            contacts.append(
                {
                    "name": name,
                    "relation": kit.text(
                        item.get("relation"), "the relation", max_len=40
                    ),
                    "phone": phone,
                }
            )
    values = {
        "blood_type": _blood_type(raw.get("blood_type")),
        "allergies": kit.text(
            raw.get("allergies"), "allergies", max_len=500, multiline=True
        ),
        "conditions": kit.text(
            raw.get("conditions"), "conditions", max_len=500, multiline=True
        ),
        "contacts": kit.dumps(contacts),
        "doctor": kit.text(raw.get("doctor"), "the doctor", max_len=100),
        "doctor_phone": kit.phone(raw.get("doctor_phone"), "the doctor's phone"),
        "notes": kit.text(raw.get("notes"), "notes", max_len=500, multiline=True),
    }
    connection.execute(
        """INSERT INTO cr_cards (owner_id, blood_type, allergies, conditions, contacts, doctor, doctor_phone, notes, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?) ON CONFLICT(owner_id) DO UPDATE SET blood_type = excluded.blood_type,
        allergies = excluded.allergies, conditions = excluded.conditions, contacts = excluded.contacts, doctor = excluded.doctor,
        doctor_phone = excluded.doctor_phone, notes = excluded.notes, updated_at = excluded.updated_at""",
        (
            person["id"],
            values["blood_type"],
            values["allergies"],
            values["conditions"],
            values["contacts"],
            values["doctor"],
            values["doctor_phone"],
            values["notes"],
            kit.now(),
        ),
    )
    connection.commit()
    return card(connection, person)


# ---------------------------------------------------------------------------
# Today, pulse and the overview
# ---------------------------------------------------------------------------


def next_appointment(
    connection: sqlite3.Connection, owner_id: str, now: datetime | None = None
) -> dict[str, Any] | None:
    upcoming = appointments(connection, owner_id, now)["upcoming"]
    return upcoming[0] if upcoming else None


def alerts(
    connection: sqlite3.Connection, me: dict[str, Any], now: datetime | None = None
) -> list[dict[str, Any]]:
    """What to show now: my doses due, my appointments soon, refills, and missed doses of people I care for."""
    moment = _now(now)
    today = local_today(connection, moment)
    out: list[dict[str, Any]] = []
    for slot in day_slots(connection, me["id"], today, moment):
        if slot["status"] != "due":
            continue
        body = {
            "medicine_id": slot["medicine_id"],
            "date": slot["date"],
            "time": slot["time"],
        }
        out.append(
            {
                "id": f"dose:{slot['medicine_id']}:{slot['date']}:{slot['time']}",
                "title": "Time for your medicine",
                "body": f"{slot['name']} {slot['dose']} ({slot['time']})".strip()
                + (f". {slot['with_food']}" if slot["with_food"] else ""),
                "tone": "warn",
                "speak": True,
                "actions": [
                    life.action("Taken", "/api/care/doses/take", body=body),
                    life.action("Skip", "/api/care/doses/skip", body=body),
                ],
            }
        )
    for appointment in appointments(connection, me["id"], moment)["upcoming"]:
        at = kit.parse_time(appointment["at"])
        when = at.astimezone(zone(connection)).strftime("%A %H:%M")
        if at - timedelta(hours=2) <= moment < at:
            out.append(
                {
                    "id": f"appt:{appointment['id']}:soon",
                    "title": "Appointment soon",
                    "body": f"{appointment['title']} at {when}. {appointment['place']}".strip(),
                    "tone": "",
                    "speak": True,
                }
            )
        elif at - timedelta(hours=24) <= moment < at - timedelta(hours=2):
            out.append(
                {
                    "id": f"appt:{appointment['id']}:day",
                    "title": "Appointment tomorrow",
                    "body": f"{appointment['title']} on {when}. {appointment['place']}".strip(),
                    "tone": "",
                    "speak": True,
                }
            )
    for medicine in low_stock(connection, me["id"]):
        out.append(
            {
                "id": f"refill:{medicine['id']}:{today.isoformat()}",
                "title": "Time to refill",
                "body": f"{medicine['name']}: {medicine['stock']} left.",
                "tone": "",
                "speak": False,
            }
        )
    for person in caring_for(connection, me, moment):
        for slot in _missed_recent(connection, person["id"], moment):
            out.append(
                {
                    "id": f"missed:{slot['medicine_id']}:{slot['date']}:{slot['time']}:{me['id']}",
                    "title": f"{person['name']} missed a medicine",
                    "body": f"{slot['name']} {slot['dose']} was due at {slot['time']}.".replace(
                        "  ", " "
                    ),
                    "tone": "bad",
                    "speak": True,
                }
            )
    return out


def overview(
    connection: sqlite3.Connection,
    me: dict[str, Any],
    person: dict[str, Any],
    can_edit: bool,
    now: datetime | None = None,
) -> dict[str, Any]:
    today = local_today(connection, now)
    return {
        "person": life.public(person),
        "can_edit": can_edit,
        "today": today.isoformat(),
        "slots": day_slots(connection, person["id"], today, now),
        "adherence_7": adherence(connection, person["id"], 7, now),
        "adherence_30": adherence(connection, person["id"], 30, now),
        "low_stock": [
            {
                "id": m["id"],
                "name": m["name"],
                "stock": m["stock"],
                "days_left": m["days_left"],
            }
            for m in low_stock(connection, person["id"], now)
        ],
        "as_needed": [
            {"id": m["id"], "name": m["name"], "dose": m["dose"]}
            for m in store.medicines(connection, person["id"], active_only=True)
            if m["as_needed"]
        ],
        "next_appointment": next_appointment(connection, person["id"], now),
        "latest": latest_readings(connection, person["id"]),
        "mood": diary(connection, person["id"], days=7)
        if person["id"] == me["id"]
        else [],  # the diary is private
        "caring_for": caring_for(connection, me, now),
        "kinds": KINDS,
        "timezone": timezone_info(connection),
        "not_advice": NOT_ADVICE,
    }
