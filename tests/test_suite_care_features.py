"""Health (app id ``care``), added later: the household time zone, refills and supply, the doctor report and its
expiring link, appointment calendar files, care work (clients, visits, statements, payments), and regressions for
bugs found in the browser."""

from __future__ import annotations

import json
import sqlite3
import urllib.error
import urllib.request
from datetime import date, datetime, timedelta, timezone

import pytest
from cloud_support import Browser, build_org, sign_in
from life_support import Household

import auto_apply_assistant as local
import test_careers
from aira.suite import kit, life, public
from aira.suite.care import report, service, store, work

env = test_careers.env
api = test_careers.api
stub = test_careers.stub


class _NoModel:
    def model(self, prompt):
        return None


@pytest.fixture(autouse=True)
def no_model():
    """Never call the real writing model (a live Ollama may run on this machine)."""
    from aira import suite

    previous = suite.gateway()
    suite.set_gateway(_NoModel())
    yield
    suite.set_gateway(previous)


class Req:
    def __init__(self) -> None:
        self.user = {"email": "pat@home.example"}

    def header(self, name: str, default: str = "") -> str:
        return default


@pytest.fixture
def db(tmp_path):
    kit.forget_schema()
    public.set_index(public._MemoryIndex())
    connection = sqlite3.connect(tmp_path / "care.sqlite3")
    connection.row_factory = sqlite3.Row
    store.ensure(connection)
    yield connection
    connection.close()


@pytest.fixture
def people(db):
    pat = life.current(Req(), db)
    rosa = life.add(
        db, pat, {"name": "Rosa", "age_group": "senior", "email": "rosa@home.example"}
    )
    mia = life.add(db, pat, {"name": "Mia", "age_group": "child"})
    return {"pat": pat, "rosa": rosa, "mia": mia}


LONG_AGO = "2025-01-01T00:00:00Z"


def backdate(db, medicine_id: str) -> None:
    db.execute(
        "UPDATE cr_medicines SET created_at = ? WHERE id = ?", (LONG_AGO, medicine_id)
    )
    db.commit()


def utc(day: str, clock: str) -> datetime:
    hour, minute = (int(x) for x in clock.split(":"))
    return datetime.fromisoformat(day).replace(
        hour=hour, minute=minute, tzinfo=timezone.utc
    )


# ---------------------------------------------------------------------------
# the household time zone (bug: a new person's reminders came on UTC time)
# ---------------------------------------------------------------------------


def test_medicine_times_follow_the_households_zone_not_utc(db, people):
    rosa = people["rosa"]
    assert service.timezone_info(db) == {"name": "UTC", "source": "default"}
    pills = service.create_medicine(db, rosa, {"name": "Metformin", "times": "08:00"})
    backdate(db, pills["id"])
    service.set_timezone(db, rosa, "Asia/Kolkata")
    assert service.timezone_info(db) == {"name": "Asia/Kolkata", "source": "health"}
    slot = service.day_slots(
        db, rosa["id"], date(2026, 1, 15), now=utc("2026-01-15", "01:00")
    )[0]
    assert slot["at"] == "2026-01-15T02:30:00Z"  # 08:00 in India, not 08:00 UTC
    # Saving the Business hub without choosing a zone stores UTC: that is not a choice and does not win.
    kit.save_business(db, {"name": "Home"})
    assert service.timezone_info(db)["name"] == "Asia/Kolkata"
    # A zone chosen in the Business hub does win.
    kit.save_business(db, {"timezone": "Europe/London"})
    assert service.timezone_info(db) == {"name": "Europe/London", "source": "business"}
    for wrong in ("Mars/Olympus", "8", "", "IST"):
        with pytest.raises(ValueError):
            service.set_timezone(db, rosa, wrong)


# ---------------------------------------------------------------------------
# medicines: as-needed wording, deleting a stopped one, supply and refills
# ---------------------------------------------------------------------------


def test_a_quick_second_as_needed_dose_says_minutes_not_zero_hours(db, people):
    me = people["pat"]
    relief = service.create_medicine(
        db, me, {"name": "Paracetamol", "as_needed": True, "min_gap_hours": 4}
    )
    service.take_as_needed(
        db, me, {"medicine_id": relief["id"]}, now=utc("2026-01-15", "08:00")
    )
    soon = service.take_as_needed(
        db, me, {"medicine_id": relief["id"]}, now=utc("2026-01-15", "08:03")
    )
    assert "3 minutes ago" in soon["warning"] and "0.0" not in soon["warning"]
    later = service.take_as_needed(
        db, me, {"medicine_id": relief["id"]}, now=utc("2026-01-15", "10:33")
    )
    assert "2 hours and 30 minutes ago" in later["warning"]
    assert service._ago(timedelta(seconds=20)) == "just now"
    assert service._ago(timedelta(hours=1)) == "1 hour ago"


def test_a_stopped_medicine_can_be_deleted_with_its_history(db, people):
    rosa = people["rosa"]
    pills = service.create_medicine(
        db, rosa, {"name": "Statin", "times": "08:00", "stock": 5}
    )
    backdate(db, pills["id"])
    service.take(
        db,
        rosa,
        {"medicine_id": pills["id"], "date": "2026-01-15", "time": "08:00"},
        now=utc("2026-01-15", "08:05"),
    )
    service.refill(db, rosa, pills["id"], {"amount": 10})
    assert service.delete_medicine(db, rosa, pills["id"]) == "paused"
    assert (
        service.delete_medicine(db, rosa, pills["id"]) == "deleted"
    )  # used to stay "paused" for ever
    with pytest.raises(LookupError):
        store.get_medicine(db, pills["id"], rosa["id"])
    for table in ("cr_doses", "cr_refills"):
        assert (
            kit.count(
                db,
                f"SELECT COUNT(*) FROM {table} WHERE medicine_id = ?",
                (pills["id"],),
            )
            == 0
        )  # noqa: S608


def test_supply_days_left_and_refills(db, people):
    rosa = people["rosa"]
    now = utc("2026-01-15", "06:00")
    twice = service.create_medicine(
        db, rosa, {"name": "Metformin", "times": "08:00, 20:00", "stock": 9}
    )
    view = service.medicine_view(
        db, store.get_medicine(db, twice["id"], rosa["id"]), now
    )
    assert (view["per_day"], view["days_left"], view["runs_out"]) == (
        2,
        4,
        "2026-01-19",
    )
    assert (
        view["low"] is True
    )  # no refill number chosen: told when fewer than 5 days are left
    weekly = service.create_medicine(
        db, rosa, {"name": "Vitamin D", "times": "09:00", "days": "mon", "stock": 3}
    )
    assert (
        service.supply(db, store.get_medicine(db, weekly["id"], rosa["id"]), now)[
            "days_left"
        ]
        == 21
    )
    chosen = service.create_medicine(
        db, rosa, {"name": "Aspirin", "times": "09:00", "stock": 12, "refill_at": 10}
    )
    assert [m["name"] for m in service.low_stock(db, rosa["id"], now)] == ["Metformin"]
    service.update_medicine(db, rosa, chosen["id"], {"stock": 10})
    assert {m["name"] for m in service.low_stock(db, rosa["id"], now)} == {
        "Metformin",
        "Aspirin",
    }
    refilled = service.refill(
        db, rosa, twice["id"], {"amount": 60, "note": "City Pharmacy"}
    )
    assert refilled["stock"] == 69 and refilled["low"] is False
    assert (
        refilled["refills"][0]["amount"] == 60
        and refilled["refills"][0]["note"] == "City Pharmacy"
    )
    # A medicine without a count starts one; nonsense is refused; another person cannot refill it.
    relief = service.create_medicine(
        db, rosa, {"name": "Paracetamol", "as_needed": True}
    )
    assert service.refill(db, rosa, relief["id"], {"amount": 16})["stock"] == 16
    for wrong in ({"amount": 0}, {"amount": "lots"}, {}):
        with pytest.raises(ValueError):
            service.refill(db, rosa, twice["id"], wrong)
    with pytest.raises(LookupError):
        service.refill(db, people["pat"], twice["id"], {"amount": 5})


def test_readings_carry_a_short_note_and_a_flag(db, people):
    high = service.add_reading(
        db, people["rosa"], {"kind": "blood_pressure", "value1": 150, "value2": 95}
    )
    assert high["flagged"] is True and high["note_short"].startswith(
        "This is above the usual range"
    )
    assert (
        service.NOT_ADVICE not in high["note_short"]
        and service.NOT_ADVICE in high["hint"]
    )
    normal = service.add_reading(db, people["rosa"], {"kind": "weight", "value1": 64})
    assert normal["flagged"] is False and normal["note_short"] == ""


def test_an_appointment_becomes_a_calendar_file(db, people):
    saved = service.save_appointment(
        db,
        people["rosa"],
        {
            "title": "Dr Patel, cardiology",
            "at": "2030-05-04T09:30:00Z",
            "place": "Riverside Clinic; room 4",
            "questions": "Is my dose right?\nCan I stop?",
        },
    )
    text = service.calendar_file(saved).decode()
    assert text.startswith("BEGIN:VCALENDAR\r\n") and text.endswith("END:VCALENDAR\r\n")
    assert "DTSTART:20300504T093000Z" in text and "DTEND:20300504T103000Z" in text
    assert (
        "SUMMARY:Dr Patel\\, cardiology" in text
        and "LOCATION:Riverside Clinic\\; room 4" in text
    )
    assert "Is my dose right?\\nCan I stop?" in text and text.count("BEGIN:VALARM") == 2


# ---------------------------------------------------------------------------
# the doctor report
# ---------------------------------------------------------------------------


def test_the_report_adds_up_medicines_readings_and_the_chosen_diary(db, people):
    rosa = people["rosa"]
    now = utc("2026-01-16", "12:00")
    pills = service.create_medicine(
        db, rosa, {"name": "Metformin", "dose": "500 mg", "times": "08:00"}
    )
    backdate(db, pills["id"])
    service.take(
        db,
        rosa,
        {"medicine_id": pills["id"], "date": "2026-01-15", "time": "08:00"},
        now=now,
    )
    for top, bottom in ((120, 80), (150, 95)):
        service.add_reading(
            db,
            rosa,
            {
                "kind": "blood_pressure",
                "value1": top,
                "value2": bottom,
                "at": "2026-01-15T10:00:00Z",
            },
        )
    service.save_day(
        db, rosa, {"day": "2026-01-15", "mood": 2, "symptoms": "Headache, tired"}
    )
    service.save_day(db, rosa, {"day": "2026-01-16", "mood": 4, "symptoms": "headache"})
    service.save_card(db, rosa, {"allergies": "Penicillin"})
    built = report.build(db, rosa, 30, now=now)
    medicine = built["medicines"][0]
    # 15 January taken; 16 January (08:00) and every earlier morning in the 30 days went unmarked.
    assert (medicine["name"], medicine["taken"], medicine["missed"]) == (
        "Metformin",
        1,
        29,
    )
    assert medicine["percent"] == round(100 / 30)
    bp = built["readings"][0]
    assert (
        bp["count"],
        bp["average_display"],
        bp["highest_display"],
        bp["flagged"],
    ) == (2, "135/88 mmHg", "150/95 mmHg", 1)
    assert built["diary"] is None and built["allergies"] == "Penicillin"
    with_diary = report.build(db, rosa, 30, include_diary=True, now=now)
    assert (
        with_diary["diary"]["days_written"] == 2
        and with_diary["diary"]["mood_average"] == 3.0
    )
    assert with_diary["diary"]["symptoms"][0] == {"name": "headache", "days": 2}
    rows = report.csv_rows(built)
    assert rows[0]["section"] == "medicine" and rows[-1]["section"] == "reading"
    with pytest.raises(ValueError):
        report.build(db, rosa, 45)


def test_a_report_link_works_for_a_while_and_children_cannot_share(db, people):
    rosa, mia = people["rosa"], people["mia"]
    service.save_day(db, rosa, {"mood": 1, "note": "private"})
    made = report.share(db, rosa, {"days": 90, "include_diary": False})
    assert made["days"] == 90 and made["include_diary"] is False
    page = public.view(db, made["token"])["page"]
    assert page["report"]["name"] == "Rosa" and page["report"]["diary"] is None
    assert "private" not in json.dumps(page)
    # Renewing keeps the same link with the new settings.
    again = report.share(db, rosa, {"days": 30, "include_diary": True})
    assert (
        again["token"] == made["token"]
        and public.view(db, made["token"])["page"]["report"]["diary"] is not None
    )
    # Past its date the page is gone, and the app shows no link.
    db.execute("UPDATE cr_report_links SET expires_at = ?", (LONG_AGO,))
    db.commit()
    with pytest.raises(LookupError):
        public.view(db, made["token"])
    assert report.link_state(db, rosa["id"]) is None
    with pytest.raises(PermissionError):
        report.share(db, mia, {"days": 30})
    with pytest.raises(ValueError):
        report.share(db, rosa, {"days": 7})


# ---------------------------------------------------------------------------
# care work: earning by looking after people
# ---------------------------------------------------------------------------


def test_care_work_from_visit_to_money_received(db, people):
    pat, rosa, mia = people["pat"], people["rosa"], people["mia"]
    kit.save_business(
        db, {"currency": "INR", "payment_link": "https://pay.example/pat"}
    )
    client = work.save_client(
        db, pat, {"name": "Mrs Rao", "email": "arun@example.com", "rate_cents": 25000}
    )
    assert client["rate_cents"] == 25000
    first = work.save_visit(
        db,
        pat,
        {
            "client_id": client["id"],
            "day": "2026-01-10",
            "start": "9:00",
            "minutes": 90,
            "tasks": "medicines, lunch",
            "notes": "Key under the mat",
        },
    )
    assert (
        first["start"] == "09:00"
        and first["amount_cents"] == 37500
        and first["tasks"] == ["medicines", "lunch"]
    )
    work.save_visit(
        db,
        pat,
        {
            "client_id": client["id"],
            "day": "2026-01-12",
            "minutes": 20,
            "rate_cents": 30000,
        },
    )
    for wrong in (
        {"client_id": client["id"], "minutes": 2},
        {"client_id": client["id"], "minutes": 60, "day": "2099-01-01"},
        {"client_id": "f" * 32, "minutes": 60},
    ):
        with pytest.raises((ValueError, LookupError)):
            work.save_visit(db, pat, wrong)
    assert work.clients(db, pat["id"])[0]["unbilled_cents"] == 47500
    statement = work.create_statement(
        db, pat, {"client_id": client["id"], "to": "2026-01-31"}
    )
    assert (
        statement["number"],
        statement["total_cents"],
        statement["minutes"],
        statement["currency"],
    ) == ("CARE-0001", 47500, 110, "INR")
    assert (
        statement["period_from"] == "2026-01-10"
        and statement["period_to"] == "2026-01-12"
    )
    with pytest.raises(ValueError):
        work.save_visit(
            db, pat, {"minutes": 30}, first["id"]
        )  # billed visits are locked
    with pytest.raises(ValueError):
        work.create_statement(
            db, pat, {"client_id": client["id"]}
        )  # nothing left to bill
    # The client's page: visits and the total, never the carer's private notes or the rate.
    made = work.link(db, pat, statement["id"])
    page = public.view(db, made["token"])
    shown = page["page"]["statement"]
    assert (
        shown["total_cents"] == 47500
        and shown["carer"] == pat["name"]
        and len(shown["visits"]) == 2
    )
    assert "Key under the mat" not in json.dumps(
        page
    ) and "rate_cents" not in json.dumps(page["page"])
    assert page["business"]["payment_link"] == "https://pay.example/pat"
    # Email it: queued in the outbox (waiting for approval); the statement is not "sent" until the email goes.
    sent = work.send(db, pat, statement["id"])
    assert (
        sent["statement"]["status"] == "open"
        and sent["statement"]["email"]["status"] == "pending"
        and sent["message"]["status"] == "pending"
    )
    assert made["path"] in sent["message"]["body"]
    assert "2 visits, 1 hour 50 minutes" in sent["message"]["body"]
    # Pressing it again while the email waits queues no second copy.
    again = work.send(db, pat, statement["id"])
    assert again["already"] is True and again["message"]["id"] == sent["message"]["id"]
    assert len(kit.outbox(db, app="care")) == 1
    # Approved and sent through the business's mail server: now the statement is sent.
    kit.save_email_settings(
        db, {"from_email": "pat@home.example", "smtp_host": "smtp.example"}
    )
    kit.set_outbox_status(db, [sent["message"]["id"]], "approved")
    assert kit.send_outbox(db, "pw", sender=lambda *args: None)["sent"] == 1
    assert work.get_statement(db, pat["id"], statement["id"])["status"] == "sent"
    # The client says they paid: the carer is told; nothing counts as money yet.
    status, answer = public.act(
        db, made["token"], "paid", {"note": "bank transfer"}, {}
    )
    assert status == 200 and answer == {"status": "told"}
    assert [a["id"] for a in work.alerts(db, pat)] == [f"carepaid:{statement['id']}"]
    assert kit.revenue(db, app="care")["total_cents"] == 0
    # Marked paid: recorded once, however often it is pressed; "not paid after all" takes it back.
    work.set_paid(db, pat, statement["id"], True)
    work.set_paid(db, pat, statement["id"], True)
    assert (
        kit.revenue(db, app="care")["total_cents"] == 47500
        and work.alerts(db, pat) == []
    )
    summary = work.summary(db, pat["id"])
    assert (
        summary["paid_all_time_cents"] == 47500
        and summary["waiting_cents"] == 0
        and summary["payment_ready"] is True
    )
    with pytest.raises(ValueError):
        work.delete_statement(db, pat, statement["id"])
    work.set_paid(db, pat, statement["id"], False)
    assert kit.revenue(db, app="care")["total_cents"] == 0
    assert work.get_statement(db, pat["id"], statement["id"])["status"] == "sent"
    # Deleting a statement frees its visits; its number is never given again.
    work.delete_statement(db, pat, statement["id"])
    with pytest.raises(LookupError):
        public.view(db, made["token"])
    assert len(work.visits(db, pat["id"], unbilled=True)) == 2
    assert (
        work.create_statement(db, pat, {"client_id": client["id"]})["number"]
        == "CARE-0002"
    )
    # Only the carer sees their work; children cannot use it.
    with pytest.raises(LookupError):
        work.get_client(db, rosa["id"], client["id"])
    assert work.clients(db, rosa["id"]) == [] and work.visits(db, rosa["id"]) == []
    with pytest.raises(PermissionError):
        work.save_client(db, mia, {"name": "Someone"})
    assert work.delete_client(db, pat, client["id"]) == "archived"
    fresh = work.save_client(db, pat, {"name": "Never visited"})
    assert work.delete_client(db, pat, fresh["id"]) == "deleted"


# ---------------------------------------------------------------------------
# the single-user server
# ---------------------------------------------------------------------------


@pytest.fixture
def local_index():
    public.set_index(local._public_link_index)


def anonymous(env, method, path, body=None, raw=False):
    data = json.dumps(body).encode() if body is not None else None
    request = urllib.request.Request(env.base + path, data=data, method=method)
    request.add_header("Content-Type", "application/json")
    request.add_header("X-Requested-With", "aira")
    request.add_header("Origin", env.base)
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            payload = response.read()
            return response.status, payload if raw else json.loads(payload or b"{}")
    except urllib.error.HTTPError as error:
        return error.code, json.loads(error.read() or b"{}")


def test_the_new_features_on_the_local_build(env, api, local_index):
    kit.forget_schema()
    household = Household(env, api)
    pat = household.owner
    rosa = household.add("Rosa", age_group="senior")
    mia = household.add("Mia", age_group="child", pin="2468")

    # The time zone the browser sends is kept; a wrong one is refused.
    assert rosa.get("/api/care/overview")["timezone"]["source"] == "default"
    assert (
        rosa.post("/api/care/settings", {"timezone": "Asia/Kolkata"})["timezone"][
            "name"
        ]
        == "Asia/Kolkata"
    )
    assert rosa.call("POST", "/api/care/settings", {"timezone": "Nowhere"})[0] == 400
    rosa.post("/api/care/settings", {"timezone": "UTC"})

    # Refills, supply, and the history filtered and downloaded.
    pills = rosa.post(
        "/api/care/medicines",
        {"name": "Metformin", "times": "08:00, 20:00", "stock": 4},
        expect=201,
    )["medicine"]
    listed = rosa.get("/api/care/medicines")["medicines"][0]
    assert listed["days_left"] == 2 and listed["low"] is True and listed["per_day"] == 2
    assert any(
        c["title"] == "Time to refill" and "about 2 days" in c["lines"][0]["sub"]
        for c in rosa.get("/api/life/today")["cards"]
    )
    assert (
        rosa.post(f"/api/care/medicines/{pills['id']}/refill", {"amount": 56})[
            "medicine"
        ]["stock"]
        == 60
    )
    assert (
        rosa.call("POST", f"/api/care/medicines/{pills['id']}/refill", {"amount": -3})[
            0
        ]
        == 400
    )
    assert (
        pat.call("POST", f"/api/care/medicines/{pills['id']}/refill", {"amount": 3})[0]
        == 404
    )
    relief = rosa.post(
        "/api/care/medicines", {"name": "Paracetamol", "as_needed": True}, expect=201
    )["medicine"]
    rosa.post("/api/care/doses/as-needed", {"medicine_id": relief["id"]}, expect=201)
    assert [
        d["medicine"]
        for d in rosa.get(f"/api/care/doses?medicine={relief['id']}")["doses"]
    ] == ["Paracetamol"]
    assert rosa.get(f"/api/care/doses?medicine={pills['id']}")["doses"] == []
    assert rosa.get("/api/care/doses?status=skipped")["doses"] == []
    assert rosa.call("GET", "/api/care/doses?status=lost")[0] == 400
    status, csv = rosa.call("GET", "/api/care/doses?format=csv", raw=True)
    assert status == 200 and csv.decode().splitlines()[0].startswith(
        "slot_date,slot_time,medicine"
    )

    # An appointment as a calendar file, for Rosa and for her caregiver.
    appt = rosa.post(
        "/api/care/appointments",
        {"title": "Dr Patel", "at": "2030-05-04T09:30:00Z"},
        expect=201,
    )["appointment"]
    status, ics = rosa.call(
        "GET", f"/api/care/appointments/{appt['id']}/calendar", raw=True
    )
    assert status == 200 and b"SUMMARY:Dr Patel" in ics
    assert (
        pat.call(
            "GET", f"/api/care/appointments/{appt['id']}/calendar?person={rosa.id}"
        )[0]
        == 403
    )

    # The doctor report: Rosa's caregiver may read it (never the diary) but not share it.
    rosa.post("/api/care/diary", {"mood": 2, "note": "private words"})
    rosa.post("/api/care/caregivers", {"profile_id": pat.id}, expect=201)
    mine = rosa.get("/api/care/report?days=30&diary=1")
    assert (
        mine["report"]["diary"]["days_written"] == 1
        and mine["can_share"] is True
        and mine["link"] is None
    )
    seen = pat.get(f"/api/care/report?person={rosa.id}&diary=1")
    assert (
        seen["report"]["diary"] is None
        and seen["can_share"] is False
        and seen["link"] is None
    )
    assert mia.call("GET", f"/api/care/report?person={rosa.id}")[0] == 403
    assert rosa.call("GET", "/api/care/report?days=7")[0] == 400
    status, csv = rosa.call("GET", "/api/care/report?format=csv", raw=True)
    assert status == 200 and b"Metformin" in csv
    link = rosa.post("/api/care/report/link", {"days": 30})["link"]
    status, page = anonymous(env, "GET", f"/api/public/{link['token']}")
    assert (
        status == 200
        and page["page"]["report"]["name"] == "Rosa"
        and "private words" not in json.dumps(page)
    )
    assert mia.call("POST", "/api/care/report/link", {"days": 30})[0] == 403
    rosa.ok("DELETE", "/api/care/report/link")
    assert anonymous(env, "GET", f"/api/public/{link['token']}")[0] == 404

    # Care work: Pat looks after a neighbour for money; Mia (a child) cannot; Rosa sees none of it.
    client = pat.post(
        "/api/care/work/clients",
        {"name": "Mr Lee", "email": "lee.family@example.com", "rate_cents": 2000},
        expect=201,
    )["client"]
    today = kit.utcnow().date().isoformat()
    pat.post(
        "/api/care/work/visits",
        {
            "client_id": client["id"],
            "day": today,
            "minutes": 120,
            "tasks": "lunch, walk",
        },
        expect=201,
    )
    assert (
        pat.call(
            "POST",
            "/api/care/work/visits",
            {"client_id": client["id"], "minutes": "two hours"},
        )[0]
        == 400
    )
    assert mia.call("GET", "/api/care/work")[0] == 403
    assert (
        rosa.call("PATCH", f"/api/care/work/clients/{client['id']}", {"name": "x"})[0]
        == 404
    )
    assert rosa.get("/api/care/work")["clients"] == []
    overview = pat.get("/api/care/work")
    assert (
        overview["summary"]["unbilled_cents"] == 4000
        and overview["visits"][0]["amount_cents"] == 4000
    )
    card = next(
        c for c in pat.get("/api/life/today")["cards"] if c["title"] == "Care work"
    )
    assert card["lines"][0]["sub"] == "$40.00" and card["link"] == "#/care?tab=work"
    statement = pat.post(
        "/api/care/work/statements", {"client_id": client["id"]}, expect=201
    )["statement"]
    sent = pat.post(f"/api/care/work/statements/{statement['id']}/send")
    assert (
        sent["statement"]["status"] == "open"
        and sent["statement"]["email"]["status"] == "pending"
    )
    assert any(
        m["kind"] == "care_statement" and m["recipient"] == "lee.family@example.com"
        for m in api.get("/api/suite/outbox")["messages"]
    )
    token = sent["statement"]["link"]["token"]
    status, page = anonymous(env, "GET", f"/api/public/{token}")
    assert status == 200 and page["page"]["statement"]["total_cents"] == 4000
    assert anonymous(env, "POST", f"/api/public/{token}/paid", {"note": "cash"})[1] == {
        "status": "told"
    }
    alerts = [
        a
        for a in pat.get("/api/life/pulse")["alerts"]
        if a["id"].startswith("carepaid:")
    ]
    assert alerts and alerts[0]["title"] == "A client says they paid"
    pat.post(f"/api/care/work/statements/{statement['id']}/paid", {"paid": True})
    assert api.get("/api/suite/revenue?app=care")["summary"]["total_cents"] == 4000
    status, csv = pat.call("GET", "/api/care/work/visits?format=csv", raw=True)
    assert status == 200 and b"Mr Lee" in csv and b"lunch, walk" in csv


# ---------------------------------------------------------------------------
# the hosted build
# ---------------------------------------------------------------------------


def test_hosted_care_work_and_reports_are_private_to_each_member(cloud):
    kit.forget_schema()
    org = build_org(cloud)
    owner = sign_in(cloud, org.owner)
    assert owner.call("GET", "/api/care/work")[0] == 402
    assert owner.call("GET", "/api/care/report")[0] == 402
    cloud.billing.subscribe_app(org.owner, "care")
    coach = sign_in(cloud, org.coach)
    status, made = coach.call(
        "POST", "/api/care/work/clients", {"name": "Mrs Rao", "rate_cents": 1500}
    )
    assert status == 201, made
    status, visit = coach.call(
        "POST",
        "/api/care/work/visits",
        {"client_id": made["client"]["id"], "minutes": 60},
    )
    assert status == 201, visit
    # Another member of the same organization sees none of it and cannot touch it.
    assert owner.call("GET", "/api/care/work")[1]["clients"] == []
    assert (
        owner.call(
            "PATCH", f"/api/care/work/clients/{made['client']['id']}", {"name": "x"}
        )[0]
        == 404
    )
    status, statement = coach.call(
        "POST", "/api/care/work/statements", {"client_id": made["client"]["id"]}
    )
    assert status == 201, statement
    status, link = coach.call(
        "POST", f"/api/care/work/statements/{statement['statement']['id']}/link", {}
    )
    assert status == 200
    status, page = Browser(cloud).call("GET", f"/api/public/{link['link']['token']}")
    assert status == 200 and page["page"]["statement"]["total_cents"] == 1500
    # The auditor and the candidate keep their own health, including a report link.
    for who in ("auditor", "candidate"):
        member = sign_in(cloud, getattr(org, who))
        status, shared = member.call("POST", "/api/care/report/link", {"days": 30})
        assert status == 200, (who, shared)
        status, page = Browser(cloud).call(
            "GET", f"/api/public/{shared['link']['token']}"
        )
        assert status == 200 and page["page"]["report"]["medicines"] == []
    other = build_org(cloud, "Other Org", slug_email="other.test")
    cloud.billing.subscribe_app(other.owner, "care")
    stranger = sign_in(cloud, other.owner)
    assert stranger.call("GET", "/api/care/work")[1]["clients"] == []
    assert (
        stranger.call(
            "GET", f"/api/care/work/statements/{statement['statement']['id']}"
        )[0]
        == 404
    )
