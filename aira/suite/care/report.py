"""The report for a doctor's visit: one page with how the medicines went, what the readings were, how the person
felt and the questions they want to ask, for the last 30, 90 or 180 days.

The person reads and prints it in Health; their caregivers may read it too (never with the diary). The person may
share it with a doctor as a link (``health_report`` public page) that stops working after ``LINK_DAYS`` days;
children cannot share. Nothing here is medical advice: the numbers are what was recorded.
"""

from __future__ import annotations

import sqlite3
from collections import Counter
from datetime import datetime, timedelta
from typing import Any

from aira.suite import kit, life
from aira.suite.care import links, service, store
from aira.suite.care.store import APP

PERIODS = (30, 90, 180)
LINK_DAYS = 14
KIND = "health_report"


def _period(value: Any) -> int:
    days = kit.integer(value, "the number of days", default=30, minimum=1, maximum=366)
    if days not in PERIODS:
        raise ValueError("Choose 30, 90 or 180 days.")
    return days


def _stats(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """Count, average, lowest and highest of one kind of reading, and how many were outside the usual range."""
    first = rows[0]
    values = [float(row["value1"]) for row in rows]
    out: dict[str, Any] = {
        "kind": first["kind"],
        "label": first["label"],
        "unit": first["unit"],
        "count": len(rows),
        "average": round(sum(values) / len(values), 1),
        "lowest": min(values),
        "highest": max(values),
        "flagged": sum(1 for row in rows if row["flagged"]),
        "latest": first["display"],
        "latest_at": first["at"],
    }
    if first["kind"] == "blood_pressure":
        bottoms = [float(row["value2"]) for row in rows if row["value2"] is not None]
        out["average_display"] = (
            f"{round(sum(values) / len(values))}/{round(sum(bottoms) / len(bottoms))} {first['unit']}"
            if bottoms
            else ""
        )
        out["highest_display"] = next(
            row["display"] for row in rows if float(row["value1"]) == out["highest"]
        )
    else:
        out["average_display"] = f"{out['average']:g} {first['unit']}"
        out["highest_display"] = f"{out['highest']:g} {first['unit']}"
    out["lowest_display"] = (
        next(row["display"] for row in rows if float(row["value1"]) == out["lowest"])
        if first["kind"] == "blood_pressure"
        else f"{out['lowest']:g} {first['unit']}"
    )
    return out


def build(
    connection: sqlite3.Connection,
    person: dict[str, Any],
    days: Any = 30,
    *,
    include_diary: bool = False,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Everything a doctor would ask about, for ``person`` over the last ``days`` days."""
    period = _period(days)
    today = service.local_today(connection, now)
    since = today - timedelta(days=period - 1)
    owner = person["id"]
    per_medicine = service.medicine_adherence(connection, owner, period, now)
    medicines = []
    for medicine in store.medicines(connection, owner):
        stats = per_medicine.get(medicine["id"])
        if not medicine["active"] and not stats:
            continue
        as_needed = 0
        if medicine["as_needed"]:
            as_needed = kit.count(
                connection,
                "SELECT COUNT(*) FROM cr_doses WHERE medicine_id = ? AND slot_time = '' AND status = 'taken' AND slot_date >= ?",
                (medicine["id"], since.isoformat()),
            )
        medicines.append(
            {
                "name": medicine["name"],
                "dose": medicine["dose"],
                "active": medicine["active"],
                "schedule": "when needed"
                if medicine["as_needed"]
                else ", ".join(medicine["times"])
                + (
                    " on "
                    + ", ".join(store.WEEKDAYS[d].title() for d in medicine["days"])
                    if medicine["days"]
                    else " every day"
                ),
                "with_food": medicine["with_food"],
                "taken": stats["taken"] if stats else 0,
                "skipped": stats["skipped"] if stats else 0,
                "missed": stats["missed"] if stats else 0,
                "percent": stats["percent"] if stats else None,
                "as_needed_taken": as_needed,
            }
        )
    by_kind: dict[str, list[dict[str, Any]]] = {}
    for row in service.readings(connection, owner, days=period, now=now):
        by_kind.setdefault(row["kind"], []).append(row)
    readings = [_stats(by_kind[kind]) for kind in service.KINDS if by_kind.get(kind)]
    diary: dict[str, Any] | None = None
    if include_diary:
        entries = [
            entry
            for entry in service.diary(connection, owner, days=period + 1, now=now)
            if entry["day"] >= since.isoformat()
        ]
        symptoms = Counter(
            symptom.lower() for entry in entries for symptom in entry["symptoms"]
        )
        diary = {
            "days_written": len(entries),
            "mood_average": round(
                sum(int(entry["mood"]) for entry in entries) / len(entries), 1
            )
            if entries
            else None,
            "low_days": sum(1 for entry in entries if int(entry["mood"]) <= 2),
            "symptoms": [
                {"name": name, "days": count} for name, count in symptoms.most_common(8)
            ],
        }
    overall = service.adherence(connection, owner, period, now)
    upcoming = service.next_appointment(connection, owner, now)
    card = service.card(connection, person)
    return {
        "name": person["name"],
        "days": period,
        "from": since.isoformat(),
        "to": today.isoformat(),
        "made_at": kit.now(),
        "adherence": overall,
        "medicines": medicines,
        "readings": readings,
        "diary": diary,
        "allergies": card["allergies"],
        "conditions": card["conditions"],
        "next_appointment": {
            "title": upcoming["title"],
            "at": upcoming["at"],
            "place": upcoming["place"],
            "questions": upcoming["questions"],
        }
        if upcoming
        else None,
        "not_advice": service.NOT_ADVICE,
    }


def csv_rows(report: dict[str, Any]) -> list[dict[str, Any]]:
    """The report as rows for a spreadsheet: one per medicine and one per kind of reading."""
    rows: list[dict[str, Any]] = []
    for medicine in report["medicines"]:
        rows.append(
            {
                "section": "medicine",
                "name": f"{medicine['name']} {medicine['dose']}".strip(),
                "detail": medicine["schedule"],
                "taken": medicine["taken"] or medicine["as_needed_taken"],
                "skipped": medicine["skipped"],
                "missed": medicine["missed"],
                "percent": "" if medicine["percent"] is None else medicine["percent"],
            }
        )
    for reading in report["readings"]:
        rows.append(
            {
                "section": "reading",
                "name": reading["label"],
                "detail": f"average {reading['average_display']}, lowest {reading['lowest_display']}, "
                f"highest {reading['highest_display']}",
                "taken": reading["count"],
                "skipped": "",
                "missed": reading["flagged"],
                "percent": "",
            }
        )
    return rows


# ---------------------------------------------------------------------------
# the link for a doctor
# ---------------------------------------------------------------------------


def link_state(connection: sqlite3.Connection, owner_id: str) -> dict[str, Any] | None:
    """The live link with its settings, or None (a link past its date is turned off here)."""
    row = kit.fetch_one(
        connection, "SELECT * FROM cr_report_links WHERE owner_id = ?", (owner_id,)
    )
    link = links.current(connection, KIND, owner_id)
    if not row or not link:
        return None
    if kit.parse_time(row["expires_at"]) <= kit.utcnow():
        revoke(connection, owner_id)
        return None
    return {
        **link,
        "days": row["days"],
        "include_diary": bool(row["include_diary"]),
        "expires_at": row["expires_at"],
    }


def share(
    connection: sqlite3.Connection, me: dict[str, Any], raw: dict[str, Any]
) -> dict[str, Any]:
    """Make (or renew for another ``LINK_DAYS`` days) the link a doctor opens."""
    if not life.is_adult(me):
        raise PermissionError("Ask an adult to share your health report.")
    days = _period(raw.get("days"))
    include_diary = kit.flag(raw.get("include_diary"))
    expires = kit.iso(kit.utcnow() + timedelta(days=LINK_DAYS))
    connection.execute(
        """INSERT INTO cr_report_links (owner_id, days, include_diary, expires_at, created_at) VALUES (?, ?, ?, ?, ?)
        ON CONFLICT(owner_id) DO UPDATE SET days = excluded.days, include_diary = excluded.include_diary,
        expires_at = excluded.expires_at""",
        (me["id"], days, 1 if include_diary else 0, expires, kit.now()),
    )
    connection.commit()
    links.create(connection, KIND, me["id"], title=f"Health report: {me['name']}")
    kit.log(
        connection,
        APP,
        "report",
        f"{me['name']} shared a health report for {LINK_DAYS} days",
        actor=me["name"],
    )
    connection.commit()
    return link_state(connection, me["id"]) or {}


def revoke(connection: sqlite3.Connection, owner_id: str) -> None:
    links.revoke(connection, KIND, owner_id)
    connection.execute("DELETE FROM cr_report_links WHERE owner_id = ?", (owner_id,))
    connection.commit()


def public_page(connection: sqlite3.Connection, link: dict[str, Any]) -> dict[str, Any]:
    """What the doctor sees: the report as the person chose it, until the link's date."""
    row = kit.fetch_one(
        connection,
        "SELECT * FROM cr_report_links WHERE owner_id = ?",
        (link["ref_id"],),
    )
    if not row or kit.parse_time(row["expires_at"]) <= kit.utcnow():
        raise LookupError("This link is no longer available.")
    try:
        person = life.get(connection, link["ref_id"])
    except LookupError:
        raise LookupError("This link is no longer available.") from None
    report = build(
        connection, person, row["days"], include_diary=bool(row["include_diary"])
    )
    return {"report": report, "expires_at": row["expires_at"]}
