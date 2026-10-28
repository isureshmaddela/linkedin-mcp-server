"""Health's search box (the bar across the top of the Aira frame): one person's medicines, appointments, kinds of
reading, diary entries, care clients and caregivers that match what was typed, in one request.

What it returns follows what the person may open: someone viewing as a caregiver (``person`` is not ``me``) finds
only the medicines, appointments and readings they may already see, never the diary, the clients or the caregivers.
Matching is a plain case- and accent-insensitive "contains" on the words a person would remember.
"""

from __future__ import annotations

import sqlite3
import unicodedata
from typing import Any

from aira.suite import kit, life
from aira.suite.care import service, store, work

MAX_QUERY = 80


def _fold(text: Any) -> str:
    plain = unicodedata.normalize("NFKD", str(text or "").lower())
    return "".join(ch for ch in plain if not unicodedata.combining(ch))


def _hit(needle: str, *values: Any) -> bool:
    return any(needle in _fold(value) for value in values if value)


def search(
    connection: sqlite3.Connection,
    me: dict[str, Any],
    person: dict[str, Any],
    can_edit: bool,
    query: str,
    limit: int = 6,
) -> dict[str, list[dict[str, Any]]]:
    """Up to ``limit`` matches of each kind for ``person`` (``me`` or someone who made ``me`` their caregiver)."""
    needle = _fold(str(query or "").strip()[:MAX_QUERY])
    out: dict[str, list[dict[str, Any]]] = {
        "medicines": [],
        "appointments": [],
        "readings": [],
        "diary": [],
        "clients": [],
        "people": [],
    }
    if not needle:
        return out
    for medicine in store.medicines(connection, person["id"]):
        if _hit(
            needle,
            medicine["name"],
            medicine.get("dose"),
            medicine.get("with_food"),
            medicine.get("notes"),
        ):
            out["medicines"].append(
                {
                    "id": medicine["id"],
                    "name": medicine["name"],
                    "dose": medicine.get("dose") or "",
                    "as_needed": bool(medicine.get("as_needed")),
                    "times": medicine.get("times") or [],
                    "active": bool(medicine.get("active")),
                }
            )
    booked = service.appointments(connection, person["id"])
    for appointment in [*booked["upcoming"], *booked["past"]]:
        if _hit(
            needle,
            appointment["title"],
            appointment.get("place"),
            appointment.get("purpose"),
            appointment.get("questions"),
            appointment.get("notes"),
        ):
            out["appointments"].append(
                {
                    "id": appointment["id"],
                    "title": appointment["title"],
                    "at": appointment["at"],
                    "place": appointment.get("place") or "",
                    "upcoming": appointment in booked["upcoming"],
                }
            )
    counts = {
        row["kind"]: int(row["n"])
        for row in kit.fetch_all(
            connection,
            "SELECT kind, COUNT(*) AS n FROM cr_readings WHERE owner_id = ? GROUP BY kind",
            (person["id"],),
        )
    }
    for kind, spec in service.KINDS.items():
        if _hit(needle, spec["label"], kind.replace("_", " ")):
            out["readings"].append(
                {"kind": kind, "label": spec["label"], "count": counts.get(kind, 0)}
            )
    if can_edit:
        for entry in service.diary(connection, me["id"], 366):
            if _hit(needle, entry.get("note"), *entry["symptoms"]):
                out["diary"].append(
                    {
                        "day": entry["day"],
                        "emoji": entry["emoji"],
                        "note": entry.get("note") or "",
                        "symptoms": entry["symptoms"],
                    }
                )
        if life.is_adult(me):
            for client in work.clients(connection, me["id"]):
                if _hit(
                    needle,
                    client["name"],
                    client.get("email"),
                    client.get("phone"),
                    client.get("notes"),
                ):
                    out["clients"].append(
                        {
                            "id": client["id"],
                            "name": client["name"],
                            "active": client["active"],
                            "unbilled_cents": client["unbilled_cents"],
                        }
                    )
        out["people"] = _people(connection, me, needle)
    return {kind: rows[:limit] for kind, rows in out.items()}


def _people(
    connection: sqlite3.Connection, me: dict[str, Any], needle: str
) -> list[dict[str, Any]]:
    """The people ``me`` looks after (opening their health) and ``me``'s own caregivers, by name or email."""
    found: list[dict[str, Any]] = []
    owners = kit.fetch_all(
        connection,
        "SELECT DISTINCT owner_id FROM cr_caregivers WHERE caregiver_id = ?",
        (me["id"],),
    )
    for row in owners:
        try:
            person = life.get(connection, row["owner_id"])
        except LookupError:
            continue
        if _hit(needle, person["name"]):
            found.append({**life.public(person), "role": "caring_for"})
    for row in service.caregivers(connection, me["id"]):
        if _hit(needle, row.get("name"), row.get("email")):
            found.append(
                {
                    "id": row["id"],
                    "name": row.get("name") or row.get("email") or "",
                    "email": row.get("email") or "",
                    "emoji": row.get("emoji") or "",
                    "role": "caregiver",
                }
            )
    return found
