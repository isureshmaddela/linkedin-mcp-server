"""Health routes (``/api/care/*``), the ``emergency_card`` public page, and Health's Today cards and pulse.

Every route works for one person (aira.suite.life.current). ``?person=<profile id>`` reads someone who made
the caller their caregiver (read-only); everything else is the caller's own. Loaded by aira.suite.load_apps.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

from aira.airaapi.http import Raw, Request, aira_route
from aira.suite import background, kit, life, public
from aira.suite.care import service, store
from aira.suite.care.store import APP

BASE = "/api/care"
ID = "([0-9a-f]{32})"


@contextmanager
def _session(request: Request) -> Iterator[tuple[Any, dict[str, Any]]]:
    """The account's database and the person this request is for."""
    with request.connect() as connection:
        me = life.current(request, connection)
        store.ensure(connection)
        yield connection, me


def _viewing(
    request: Request, connection: Any, me: dict[str, Any]
) -> tuple[dict[str, Any], bool]:
    return service.subject(connection, me, request.q("person"))


# ---------------------------------------------------------------------------
# overview
# ---------------------------------------------------------------------------


@aira_route("GET", f"{BASE}/overview")
def overview(request: Request) -> tuple[int, dict[str, Any]]:
    with _session(request) as (connection, me):
        person, can_edit = _viewing(request, connection, me)
        service.notify_missed(connection, origin=request)
        return 200, service.overview(connection, me, person, can_edit)


# ---------------------------------------------------------------------------
# medicines and doses
# ---------------------------------------------------------------------------


@aira_route("GET", f"{BASE}/medicines")
def medicines_list(request: Request) -> tuple[int, dict[str, Any]]:
    with _session(request) as (connection, me):
        person, can_edit = _viewing(request, connection, me)
        return 200, {
            "medicines": store.medicines(connection, person["id"]),
            "forms": list(store.FORMS),
            "can_edit": can_edit,
        }


@aira_route("POST", f"{BASE}/medicines")
def medicines_create(request: Request) -> tuple[int, dict[str, Any]]:
    with _session(request) as (connection, me):
        medicine = service.create_medicine(connection, me, request.payload)
    request.audit("care.medicine_create", "medicine", medicine["id"])
    return 201, {"medicine": medicine}


@aira_route("PATCH", f"{BASE}/medicines/{ID}")
def medicines_update(request: Request) -> tuple[int, dict[str, Any]]:
    with _session(request) as (connection, me):
        medicine = service.update_medicine(
            connection, me, request.match.group(1), request.payload
        )
    return 200, {"medicine": medicine}


@aira_route("DELETE", f"{BASE}/medicines/{ID}")
def medicines_delete(request: Request) -> tuple[int, dict[str, Any]]:
    with _session(request) as (connection, me):
        outcome = service.delete_medicine(connection, me, request.match.group(1))
    request.audit("care.medicine_delete", "medicine", request.match.group(1))
    return 200, {"outcome": outcome}


@aira_route("POST", f"{BASE}/doses/take")
def doses_take(request: Request) -> tuple[int, dict[str, Any]]:
    with _session(request) as (connection, me):
        return 200, service.take(connection, me, request.payload)


@aira_route("POST", f"{BASE}/doses/skip")
def doses_skip(request: Request) -> tuple[int, dict[str, Any]]:
    with _session(request) as (connection, me):
        return 200, service.skip(connection, me, request.payload)


@aira_route("POST", f"{BASE}/doses/snooze")
def doses_snooze(request: Request) -> tuple[int, dict[str, Any]]:
    with _session(request) as (connection, me):
        return 200, service.snooze(connection, me, request.payload)


@aira_route("POST", f"{BASE}/doses/as-needed")
def doses_as_needed(request: Request) -> tuple[int, dict[str, Any]]:
    with _session(request) as (connection, me):
        return 201, service.take_as_needed(connection, me, request.payload)


@aira_route("GET", f"{BASE}/doses")
def doses_history(request: Request) -> tuple[int, dict[str, Any]]:
    with _session(request) as (connection, me):
        person, _ = _viewing(request, connection, me)
        days = request.qint("days", 30, 1, 365)
        return 200, {
            "doses": service.history(connection, person["id"], days),
            "adherence": service.adherence(connection, person["id"], min(days, 90)),
        }


# ---------------------------------------------------------------------------
# caregivers
# ---------------------------------------------------------------------------


@aira_route("GET", f"{BASE}/caregivers")
def caregivers_list(request: Request) -> tuple[int, dict[str, Any]]:
    with _session(request) as (connection, me):
        mine = service.caregivers(connection, me["id"])
        chosen = {row["caregiver_id"] for row in mine if row["caregiver_id"]}
        return 200, {
            "caregivers": mine,
            "caring_for": service.caring_for(connection, me),
            "candidates": [
                life.public(person)
                for person in life.profiles(connection)
                if person["id"] != me["id"] and person["id"] not in chosen
            ],
        }


@aira_route("POST", f"{BASE}/caregivers")
def caregivers_add(request: Request) -> tuple[int, dict[str, Any]]:
    with _session(request) as (connection, me):
        added = service.add_caregiver(connection, me, request.payload)
    request.audit("care.caregiver_add", "caregiver", added["id"])
    return 201, {"caregiver": added}


@aira_route("DELETE", f"{BASE}/caregivers/{ID}")
def caregivers_remove(request: Request) -> tuple[int, dict[str, Any]]:
    with _session(request) as (connection, me):
        service.remove_caregiver(connection, me, request.match.group(1))
    request.audit("care.caregiver_remove", "caregiver", request.match.group(1))
    return 200, {"removed": True}


# ---------------------------------------------------------------------------
# appointments
# ---------------------------------------------------------------------------


@aira_route("GET", f"{BASE}/appointments")
def appointments_list(request: Request) -> tuple[int, dict[str, Any]]:
    with _session(request) as (connection, me):
        person, can_edit = _viewing(request, connection, me)
        return 200, {
            **service.appointments(connection, person["id"]),
            "can_edit": can_edit,
        }


@aira_route("POST", f"{BASE}/appointments")
def appointments_create(request: Request) -> tuple[int, dict[str, Any]]:
    with _session(request) as (connection, me):
        return 201, {
            "appointment": service.save_appointment(connection, me, request.payload)
        }


@aira_route("PATCH", f"{BASE}/appointments/{ID}")
def appointments_update(request: Request) -> tuple[int, dict[str, Any]]:
    with _session(request) as (connection, me):
        return 200, {
            "appointment": service.save_appointment(
                connection, me, request.payload, request.match.group(1)
            )
        }


@aira_route("DELETE", f"{BASE}/appointments/{ID}")
def appointments_delete(request: Request) -> tuple[int, dict[str, Any]]:
    with _session(request) as (connection, me):
        service.delete_appointment(connection, me, request.match.group(1))
    return 200, {"removed": True}


# ---------------------------------------------------------------------------
# readings and the diary
# ---------------------------------------------------------------------------


@aira_route("GET", f"{BASE}/readings")
def readings_list(request: Request) -> tuple[int, dict[str, Any]] | Raw:
    with _session(request) as (connection, me):
        person, can_edit = _viewing(request, connection, me)
        rows = service.readings(
            connection,
            person["id"],
            kind=request.q("kind"),
            days=request.qint("days", 90, 1, 3650),
        )
        if request.q("format") == "csv":
            return Raw(
                200,
                kit.csv_bytes(
                    rows, ("at", "label", "value1", "value2", "value3", "unit", "note")
                ),
                "text/csv; charset=utf-8",
                filename="readings.csv",
            )
        return 200, {
            "readings": rows,
            "kinds": service.KINDS,
            "can_edit": can_edit,
            "not_advice": service.NOT_ADVICE,
        }


@aira_route("POST", f"{BASE}/readings")
def readings_add(request: Request) -> tuple[int, dict[str, Any]]:
    with _session(request) as (connection, me):
        return 201, {"reading": service.add_reading(connection, me, request.payload)}


@aira_route("DELETE", f"{BASE}/readings/{ID}")
def readings_delete(request: Request) -> tuple[int, dict[str, Any]]:
    with _session(request) as (connection, me):
        service.delete_reading(connection, me, request.match.group(1))
    return 200, {"removed": True}


@aira_route("GET", f"{BASE}/diary")
def diary_list(request: Request) -> tuple[int, dict[str, Any]]:
    with _session(request) as (connection, me):
        if request.q("person") and request.q("person") != me["id"]:
            raise PermissionError("A mood diary is private, even from caregivers.")
        person, can_edit = me, True
        return 200, {
            "entries": service.diary(
                connection, person["id"], request.qint("days", 30, 1, 366)
            ),
            "moods": store.MOODS,
            "can_edit": can_edit,
        }


@aira_route("POST", f"{BASE}/diary")
def diary_save(request: Request) -> tuple[int, dict[str, Any]]:
    with _session(request) as (connection, me):
        return 200, {"entry": service.save_day(connection, me, request.payload)}


@aira_route("DELETE", f"{BASE}/diary")
def diary_delete(request: Request) -> tuple[int, dict[str, Any]]:
    with _session(request) as (connection, me):
        service.delete_day(connection, me, request.q("day"))
    return 200, {"removed": True}


# ---------------------------------------------------------------------------
# the emergency card
# ---------------------------------------------------------------------------


@aira_route("GET", f"{BASE}/card")
def card_get(request: Request) -> tuple[int, dict[str, Any]]:
    with _session(request) as (connection, me):
        person, can_edit = service.card_person(connection, me, request.q("person"))
        return 200, {
            "card": service.card(connection, person),
            "person": life.public(person),
            "can_edit": can_edit,
            "can_share": can_edit and me.get("age_group") != "child",
            "link": public.link_for(connection, APP, "emergency_card", person["id"]),
            "blood_types": list(store.BLOOD_TYPES),
            "children": [
                life.public(p)
                for p in life.profiles(connection)
                if p["age_group"] == "child"
                and life.can_manage_household(me)
                and p["id"] != me["id"]
            ],
        }


@aira_route("POST", f"{BASE}/card")
def card_save(request: Request) -> tuple[int, dict[str, Any]]:
    with _session(request) as (connection, me):
        person, can_edit = service.card_person(connection, me, request.q("person"))
        service.require_own(can_edit)
        saved = service.save_card(connection, person, request.payload)
    request.audit("care.card_update", "card", person["id"])
    return 200, {"card": saved}


@aira_route("POST", f"{BASE}/card/link")
def card_link(request: Request) -> tuple[int, dict[str, Any]]:
    with _session(request) as (connection, me):
        person, can_edit = service.card_person(connection, me, request.q("person"))
        service.require_own(can_edit)
        if me.get("age_group") == "child":
            raise PermissionError("Ask an adult to share your emergency card.")
        link = public.create_link(
            connection,
            APP,
            "emergency_card",
            person["id"],
            title=f"Emergency card: {person['name']}",
        )
    request.audit("care.card_link", "card", person["id"])
    return 200, {"link": link}


@aira_route("DELETE", f"{BASE}/card/link")
def card_unlink(request: Request) -> tuple[int, dict[str, Any]]:
    with _session(request) as (connection, me):
        person, can_edit = service.card_person(connection, me, request.q("person"))
        service.require_own(can_edit)
        public.revoke(connection, APP, "emergency_card", person["id"])
    return 200, {"link": None}


@public.page(APP, "emergency_card")
def emergency_card_page(connection: Any, link: dict[str, Any]) -> dict[str, Any]:
    """Only the card: no readings, no diary, no dose history."""
    store.ensure(connection)
    try:
        person = life.get(connection, link["ref_id"])
    except LookupError:
        raise LookupError("This link is no longer available.") from None
    return {"card": service.card(connection, person)}


# ---------------------------------------------------------------------------
# Today and pulse
# ---------------------------------------------------------------------------


@life.today(APP)
def _today(connection: Any, me: dict[str, Any]) -> list[dict[str, Any]]:
    store.ensure(connection)
    slots = service.day_slots(connection, me["id"], service.local_today(connection))
    cards: list[dict[str, Any]] = []
    if slots:
        lines = []
        for slot in slots:
            body = {
                "medicine_id": slot["medicine_id"],
                "date": slot["date"],
                "time": slot["time"],
            }
            words = {
                "taken": "Taken",
                "skipped": "Skipped",
                "snoozed": "Later",
                "upcoming": "Later today",
                "due": "Now",
                "missed": "Missed",
            }
            lines.append(
                {
                    "text": f"{slot['name']} {slot['dose']}".strip(),
                    "sub": f"{slot['time']} · {words[slot['status']]}"
                    + (f" · {slot['with_food']}" if slot["with_food"] else ""),
                    "done": slot["status"] == "taken",
                    "action": None
                    if slot["status"] in ("taken", "skipped")
                    else life.action("Taken", "/api/care/doses/take", body=body),
                }
            )
        waiting = any(slot["status"] in ("due", "missed") for slot in slots)
        cards.append(
            life.card(
                APP,
                "Medicines today",
                lines=lines,
                tone="warn" if waiting else "good",
                link="#/care",
            )
        )
    upcoming = service.next_appointment(connection, me["id"])
    if upcoming:
        when = (
            kit.parse_time(upcoming["at"])
            .astimezone(service.zone(connection))
            .strftime("%A %d %B, %H:%M")
        )
        cards.append(
            life.card(
                APP,
                "Next appointment",
                lines=[
                    {
                        "text": upcoming["title"],
                        "sub": f"{when}{' · ' + upcoming['place'] if upcoming['place'] else ''}",
                    }
                ],
                link="#/care?tab=appointments",
            )
        )
    low = service.low_stock(connection, me["id"])
    if low:
        cards.append(
            life.card(
                APP,
                "Time to refill",
                lines=[{"text": m["name"], "sub": f"{m['stock']} left"} for m in low],
                tone="warn",
                link="#/care?tab=medicines",
            )
        )
    people = service.caring_for(connection, me)
    if people:
        cards.append(
            life.card(
                APP,
                "People you care for",
                lines=[
                    {
                        "text": person["name"],
                        "sub": (
                            f"{person['missed_today']} missed today"
                            if person["missed_today"]
                            else f"{person['taken_today']} of {person['due_today']} taken so far"
                        ),
                        "done": person["missed_today"] == 0
                        and person["due_today"] > 0
                        and person["taken_today"] == person["due_today"],
                    }
                    for person in people
                ],
                tone="bad" if any(person["missed_today"] for person in people) else "",
                link="#/care?tab=caregivers",
            )
        )
    return cards


@life.pulse(APP)
def _pulse(connection: Any, me: dict[str, Any], since: str) -> dict[str, Any]:
    store.ensure(connection)
    service.notify_missed(connection)
    alerts = service.alerts(connection, me)
    slots = service.day_slots(connection, me["id"], service.local_today(connection))
    badge = sum(1 for slot in slots if slot["status"] in ("due", "missed"))
    return {"badge": badge, "alerts": alerts}


@background.task(APP)
def _background(connection: Any) -> int:
    """Caregivers hear about a missed dose even when nobody has Aira open."""
    store.ensure(connection)
    return service.notify_missed(connection)
