"""Health routes (``/api/care/*``), the public pages (``emergency_card``, ``health_report`` for a doctor and
``care_statement`` for a care client), and Health's Today cards and pulse.

Every route works for one person (aira.suite.life.current). ``?person=<profile id>`` reads someone who made
the caller their caregiver (read-only); everything else is the caller's own. Care work (``/api/care/work/*``) is
always the caller's own. Loaded by aira.suite.load_apps.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from datetime import timedelta
from typing import Any

from aira.airaapi.http import Raw, Request, aira_route
from aira.suite import background, kit, life, public
from aira.suite.care import links, report, search, service, store, work
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
        return 200, {
            **service.overview(connection, me, person, can_edit),
            "timezone": _zone(connection, me),
            "me_adult": life.is_adult(me),
        }


@aira_route("GET", f"{BASE}/search")
def search_records(request: Request) -> tuple[int, dict[str, Any]]:
    """The search box across the top: the matching medicines, appointments, readings, diary days, clients and
    people (aira.suite.care.search), ``?q=`` and an optional ``?person=``."""
    with _session(request) as (connection, me):
        person, can_edit = _viewing(request, connection, me)
        return 200, {
            "query": request.q("q"),
            "results": search.search(
                connection,
                me,
                person,
                can_edit,
                request.q("q"),
                request.qint("limit", 6, 1, 25),
            ),
        }


def _zone(connection: Any, me: dict[str, Any]) -> dict[str, Any]:
    """The zone medicine times are read in, and whether this person may change it."""
    return {
        **service.timezone_info(connection),
        "can_change": service.can_set_timezone(me),
    }


# ---------------------------------------------------------------------------
# medicines and doses
# ---------------------------------------------------------------------------


@aira_route("POST", f"{BASE}/settings")
def settings_save(request: Request) -> tuple[int, dict[str, Any]]:
    """The time zone medicine times are read in (used when the Business hub names none). It is shared by everyone
    in the household or organization, so only the people ``service.can_set_timezone`` names may change it once
    it is set."""
    with _session(request) as (connection, me):
        service.set_timezone(connection, me, request.payload.get("timezone"))
        return 200, {"timezone": _zone(connection, me)}


@aira_route("GET", f"{BASE}/medicines")
def medicines_list(request: Request) -> tuple[int, dict[str, Any]]:
    with _session(request) as (connection, me):
        person, can_edit = _viewing(request, connection, me)
        per_medicine = service.medicine_adherence(connection, person["id"], 30)
        return 200, {
            "medicines": [
                {
                    **service.medicine_view(connection, medicine),
                    "adherence_30": per_medicine.get(medicine["id"]),
                }
                for medicine in store.medicines(connection, person["id"])
            ],
            "forms": list(store.FORMS),
            "timezone": _zone(connection, me),
            "can_edit": can_edit,
        }


@aira_route("POST", f"{BASE}/medicines/{ID}/refill")
def medicines_refill(request: Request) -> tuple[int, dict[str, Any]]:
    with _session(request) as (connection, me):
        medicine = service.refill(
            connection, me, request.match.group(1), request.payload
        )
    request.audit("care.medicine_refill", "medicine", medicine["id"])
    return 200, {"medicine": medicine}


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
def doses_history(request: Request) -> tuple[int, dict[str, Any]] | Raw:
    """What was marked (taken, skipped, snoozed), newest first; ``medicine`` and ``status`` filter it and
    ``format=csv`` downloads it."""
    with _session(request) as (connection, me):
        person, _ = _viewing(request, connection, me)
        days = request.qint("days", 30, 1, 365)
        rows = service.history(connection, person["id"], days)
        medicine = request.q("medicine")
        status = request.q("status")
        if medicine:
            rows = [row for row in rows if row["medicine_id"] == medicine]
        if status:
            kit.choice(status, "the status", ("taken", "skipped", "snoozed"))
            rows = [row for row in rows if row["status"] == status]
        if request.q("format") == "csv":
            return Raw(
                200,
                kit.csv_bytes(
                    rows,
                    (
                        "slot_date",
                        "slot_time",
                        "medicine",
                        "strength",
                        "status",
                        "at",
                        "reason",
                    ),
                ),
                "text/csv; charset=utf-8",
                filename="doses.csv",
            )
        return 200, {
            "doses": rows,
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


@aira_route("GET", f"{BASE}/appointments/{ID}/calendar")
def appointments_calendar(request: Request) -> Raw:
    """The appointment as a calendar file, to add it to a phone's or computer's calendar."""
    with _session(request) as (connection, me):
        person, _ = _viewing(request, connection, me)
        appointment = service.get_appointment(
            connection, person["id"], request.match.group(1)
        )
        return Raw(
            200,
            service.calendar_file(appointment),
            "text/calendar; charset=utf-8",
            filename="appointment.ics",
        )


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
# the report for a doctor
# ---------------------------------------------------------------------------


@aira_route("GET", f"{BASE}/report")
def report_get(request: Request) -> tuple[int, dict[str, Any]] | Raw:
    with _session(request) as (connection, me):
        person, can_edit = _viewing(request, connection, me)
        built = report.build(
            connection,
            person,
            request.q("days") or 30,
            include_diary=can_edit and request.q("diary") in ("1", "true"),
        )
        if request.q("format") == "csv":
            return Raw(
                200,
                kit.csv_bytes(
                    report.csv_rows(built),
                    (
                        "section",
                        "name",
                        "detail",
                        "taken",
                        "skipped",
                        "missed",
                        "percent",
                    ),
                ),
                "text/csv; charset=utf-8",
                filename="health-report.csv",
            )
        return 200, {
            "report": built,
            "can_edit": can_edit,
            "can_share": can_edit and life.is_adult(me),
            "link": report.link_state(connection, person["id"]) if can_edit else None,
            "periods": list(report.PERIODS),
            "link_days": report.LINK_DAYS,
        }


@aira_route("POST", f"{BASE}/report/link")
def report_link(request: Request) -> tuple[int, dict[str, Any]]:
    with _session(request) as (connection, me):
        made = report.share(connection, me, request.payload)
    request.audit("care.report_link", "report", me["id"])
    return 200, {"link": made}


@aira_route("DELETE", f"{BASE}/report/link")
def report_unlink(request: Request) -> tuple[int, dict[str, Any]]:
    with _session(request) as (connection, me):
        report.revoke(connection, me["id"])
    return 200, {"link": None}


@public.page(APP, report.KIND)
def health_report_page(connection: Any, link: dict[str, Any]) -> dict[str, Any]:
    """The report as its owner chose it (diary or not), until the link's date."""
    store.ensure(connection)
    return report.public_page(connection, link)


# ---------------------------------------------------------------------------
# care work: earning by looking after people
# ---------------------------------------------------------------------------


@aira_route("GET", f"{BASE}/work")
def work_overview(request: Request) -> tuple[int, dict[str, Any]]:
    with _session(request) as (connection, me):
        work.require_adult(me)
        since = (service.local_today(connection) - timedelta(days=90)).isoformat()
        return 200, {
            "summary": work.summary(connection, me["id"]),
            "clients": work.clients(connection, me["id"]),
            "visits": work.visits(
                connection, me["id"], client_id=request.q("client"), since=since
            ),
            "statements": work.statements(
                connection, me["id"], status=request.q("status")
            ),
        }


@aira_route("GET", f"{BASE}/work/visits")
def work_visits(request: Request) -> tuple[int, dict[str, Any]] | Raw:
    with _session(request) as (connection, me):
        work.require_adult(me)
        rows = work.visits(
            connection,
            me["id"],
            client_id=request.q("client"),
            unbilled=request.q("unbilled") in ("1", "true"),
            since=kit.day(request.q("since"), "the first day"),
            limit=2000,
        )
        if request.q("format") == "csv":
            return Raw(
                200,
                kit.csv_bytes(
                    work.visits_csv_rows(rows),
                    (
                        "day",
                        "start",
                        "client",
                        "minutes",
                        "rate",
                        "amount",
                        "tasks",
                        "billed",
                        "notes",
                    ),
                ),
                "text/csv; charset=utf-8",
                filename="care-visits.csv",
            )
        return 200, {"visits": rows}


@aira_route("POST", f"{BASE}/work/clients")
def work_client_create(request: Request) -> tuple[int, dict[str, Any]]:
    with _session(request) as (connection, me):
        client = work.save_client(connection, me, request.payload)
    request.audit("care.client_create", "client", client["id"])
    return 201, {"client": client}


@aira_route("PATCH", f"{BASE}/work/clients/{ID}")
def work_client_update(request: Request) -> tuple[int, dict[str, Any]]:
    with _session(request) as (connection, me):
        return 200, {
            "client": work.save_client(
                connection, me, request.payload, request.match.group(1)
            )
        }


@aira_route("DELETE", f"{BASE}/work/clients/{ID}")
def work_client_delete(request: Request) -> tuple[int, dict[str, Any]]:
    with _session(request) as (connection, me):
        outcome = work.delete_client(connection, me, request.match.group(1))
    return 200, {"outcome": outcome}


@aira_route("POST", f"{BASE}/work/visits")
def work_visit_create(request: Request) -> tuple[int, dict[str, Any]]:
    with _session(request) as (connection, me):
        return 201, {"visit": work.save_visit(connection, me, request.payload)}


@aira_route("PATCH", f"{BASE}/work/visits/{ID}")
def work_visit_update(request: Request) -> tuple[int, dict[str, Any]]:
    with _session(request) as (connection, me):
        return 200, {
            "visit": work.save_visit(
                connection, me, request.payload, request.match.group(1)
            )
        }


@aira_route("DELETE", f"{BASE}/work/visits/{ID}")
def work_visit_delete(request: Request) -> tuple[int, dict[str, Any]]:
    with _session(request) as (connection, me):
        work.delete_visit(connection, me, request.match.group(1))
    return 200, {"removed": True}


@aira_route("POST", f"{BASE}/work/statements")
def work_statement_create(request: Request) -> tuple[int, dict[str, Any]]:
    with _session(request) as (connection, me):
        statement = work.create_statement(connection, me, request.payload)
    request.audit("care.statement_create", "statement", statement["id"])
    return 201, {"statement": statement}


@aira_route("GET", f"{BASE}/work/statements/{ID}")
def work_statement_get(request: Request) -> tuple[int, dict[str, Any]]:
    with _session(request) as (connection, me):
        work.require_adult(me)
        return 200, {
            "statement": work.get_statement(
                connection, me["id"], request.match.group(1)
            )
        }


@aira_route("DELETE", f"{BASE}/work/statements/{ID}")
def work_statement_delete(request: Request) -> tuple[int, dict[str, Any]]:
    with _session(request) as (connection, me):
        work.delete_statement(connection, me, request.match.group(1))
    request.audit("care.statement_delete", "statement", request.match.group(1))
    return 200, {"removed": True}


@aira_route("POST", f"{BASE}/work/statements/{ID}/link")
def work_statement_link(request: Request) -> tuple[int, dict[str, Any]]:
    with _session(request) as (connection, me):
        return 200, {"link": work.link(connection, me, request.match.group(1))}


@aira_route("POST", f"{BASE}/work/statements/{ID}/send")
def work_statement_send(request: Request) -> tuple[int, dict[str, Any]]:
    with _session(request) as (connection, me):
        sent = work.send(connection, me, request.match.group(1), origin=request)
    request.audit("care.statement_send", "statement", request.match.group(1))
    return 200, sent


@aira_route("POST", f"{BASE}/work/statements/{ID}/paid")
def work_statement_paid(request: Request) -> tuple[int, dict[str, Any]]:
    with _session(request) as (connection, me):
        statement = work.set_paid(
            connection,
            me,
            request.match.group(1),
            kit.flag(request.payload.get("paid", True)),
        )
    request.audit("care.statement_paid", "statement", statement["id"])
    return 200, {"statement": statement}


@public.page(APP, work.KIND)
def care_statement_page(connection: Any, link: dict[str, Any]) -> dict[str, Any]:
    store.ensure(connection)
    return work.public_page(connection, link)


@public.action(APP, work.KIND, "paid")
def care_statement_paid(
    connection: Any, link: dict[str, Any], payload: dict[str, Any], visitor: Any
) -> tuple[int, dict[str, Any]]:
    """The client says they paid; the carer checks and marks it."""
    store.ensure(connection)
    return 200, work.client_says_paid(connection, link, payload)


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
            "link": links.current(connection, "emergency_card", person["id"]),
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
        link = links.create(
            connection,
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
        links.revoke(connection, "emergency_card", person["id"])
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
                lines=[
                    {
                        "text": m["name"],
                        "sub": f"{m['stock']} left"
                        + (
                            f" · about {m['days_left']} days"
                            if m["days_left"] is not None
                            else ""
                        ),
                    }
                    for m in low
                ],
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
    if life.is_adult(me) and kit.count(
        connection,
        "SELECT COUNT(*) FROM cr_clients WHERE owner_id = ? AND active = 1",
        (me["id"],),
    ):
        money = work.summary(connection, me["id"])
        currency = money["currency"]
        lines = [
            {
                "text": "Visits not billed yet",
                "sub": kit.fmt_money(money["unbilled_cents"], currency),
            },
            {
                "text": "Waiting to be paid",
                "sub": kit.fmt_money(money["waiting_cents"], currency),
            },
            {
                "text": "Paid this month",
                "sub": kit.fmt_money(money["paid_this_month_cents"], currency),
            },
        ]
        lines += [
            {
                "text": f"{row['client']} says they paid",
                "sub": row["number"]
                + (f": {row['client_paid_note']}" if row["client_paid_note"] else ""),
            }
            for row in money["to_check"]
        ]
        cards.append(
            life.card(
                APP,
                "Care work",
                lines=lines,
                tone="good" if money["to_check"] else "",
                link="#/care?tab=work",
            )
        )
    return cards


@life.pulse(APP)
def _pulse(connection: Any, me: dict[str, Any], since: str) -> dict[str, Any]:
    store.ensure(connection)
    service.notify_missed(connection)
    alerts = service.alerts(connection, me) + work.alerts(connection, me)
    slots = service.day_slots(connection, me["id"], service.local_today(connection))
    badge = sum(1 for slot in slots if slot["status"] in ("due", "missed"))
    return {"badge": badge, "alerts": alerts}


@background.task(APP)
def _background(connection: Any) -> int:
    """Caregivers hear about a missed dose even when nobody has Aira open."""
    store.ensure(connection)
    return service.notify_missed(connection)
