"""Health (app id ``care``): schedules and dose slots, reminders, caregivers, readings, the diary, the emergency card,
on the single-user server and in the hosted build."""

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
from aira.suite.care import service, store

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
    def __init__(self, header: str = "") -> None:
        self._header = header
        self.user = {"email": "pat@home.example"}

    def header(self, name: str, default: str = "") -> str:
        return self._header if name == "X-Aira-Profile" else default


@pytest.fixture
def db(tmp_path):
    kit.forget_schema()
    public.set_index(public._MemoryIndex())
    connection = sqlite3.connect(tmp_path / "care.sqlite3")
    connection.row_factory = sqlite3.Row
    store.ensure(connection)
    kit.save_business(connection, {"name": "Home", "timezone": "America/New_York"})
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


LONG_AGO = "2025-01-01T00:00:00Z"  # before every date these tests look at


def backdate(db, medicine_id: str) -> None:
    db.execute(
        "UPDATE cr_medicines SET created_at = ? WHERE id = ?", (LONG_AGO, medicine_id)
    )
    db.commit()


def at(day: str, clock: str, zone: str = "America/New_York") -> datetime:
    from zoneinfo import ZoneInfo

    hour, minute = (int(x) for x in clock.split(":"))
    return (
        datetime.fromisoformat(day)
        .replace(hour=hour, minute=minute, tzinfo=ZoneInfo(zone))
        .astimezone(timezone.utc)
    )


# ---------------------------------------------------------------------------
# schedules and doses
# ---------------------------------------------------------------------------


def test_slots_follow_the_business_time_zone_and_the_days(db, people):
    rosa = people["rosa"]
    pills = service.create_medicine(
        db, rosa, {"name": "Metformin", "dose": "500 mg", "times": "20:00, 8:00"}
    )
    weekly = service.create_medicine(
        db, rosa, {"name": "Vitamin D", "times": ["09:00"], "days": "mon, wed"}
    )
    backdate(db, pills["id"])
    backdate(db, weekly["id"])
    assert pills["times"] == ["08:00", "20:00"]
    winter = service.day_slots(
        db, rosa["id"], date(2026, 1, 15), now=at("2026-01-15", "07:00")
    )
    assert [(s["time"], s["at"]) for s in winter if s["name"] == "Metformin"] == [
        ("08:00", "2026-01-15T13:00:00Z"),
        ("20:00", "2026-01-16T01:00:00Z"),
    ]
    # The day clocks change (8 March 2026 in New York), 08:00 is already daylight time.
    spring = service.day_slots(
        db, rosa["id"], date(2026, 3, 8), now=at("2026-03-08", "07:00")
    )
    assert [s["at"] for s in spring if s["name"] == "Metformin"] == [
        "2026-03-08T12:00:00Z",
        "2026-03-09T00:00:00Z",
    ]
    assert any(
        s["name"] == "Vitamin D"
        for s in service.day_slots(
            db, rosa["id"], date(2026, 1, 19), now=at("2026-01-19", "07:00")
        )
    )
    assert not any(
        s["name"] == "Vitamin D"
        for s in service.day_slots(
            db, rosa["id"], date(2026, 1, 20), now=at("2026-01-20", "07:00")
        )
    )
    with pytest.raises(ValueError):
        service.create_medicine(db, rosa, {"name": "X", "times": "25:00"})
    with pytest.raises(ValueError):
        service.create_medicine(db, rosa, {"name": "X"})  # no time and not as needed
    with pytest.raises(ValueError):
        service.create_medicine(
            db, rosa, {"name": "X", "times": "8:00", "days": "someday"}
        )


def test_due_missed_taken_and_stock(db, people):
    rosa = people["rosa"]
    pills = service.create_medicine(
        db, rosa, {"name": "Metformin", "times": "08:00", "stock": 10, "refill_at": 9}
    )
    backdate(db, pills["id"])
    day = "2026-01-15"
    states = {
        clock: service.day_slots(db, rosa["id"], date(2026, 1, 15), now=at(day, clock))[
            0
        ]["status"]
        for clock in ("07:59", "08:30", "09:01")
    }
    assert states == {"07:59": "upcoming", "08:30": "due", "09:01": "missed"}
    slot = {"medicine_id": pills["id"], "date": day, "time": "08:00"}
    service.take(db, rosa, slot, now=at(day, "08:10"))
    service.take(db, rosa, slot, now=at(day, "08:12"))  # pressing Taken twice
    assert store.get_medicine(db, pills["id"], rosa["id"])["stock"] == 9
    assert (
        service.day_slots(db, rosa["id"], date(2026, 1, 15), now=at(day, "10:00"))[0][
            "status"
        ]
        == "taken"
    )
    assert [m["name"] for m in service.low_stock(db, rosa["id"])] == ["Metformin"]
    service.skip(db, rosa, {**slot, "reason": "felt sick"}, now=at(day, "08:20"))
    assert (
        store.get_medicine(db, pills["id"], rosa["id"])["stock"] == 10
    )  # the tablet is back
    service.take(db, rosa, slot, now=at(day, "08:25"))
    assert store.get_medicine(db, pills["id"], rosa["id"])["stock"] == 9
    with pytest.raises(ValueError):
        service.take(
            db, rosa, {**slot, "time": "09:00"}
        )  # not a time this medicine has
    # Somebody else cannot mark Rosa's medicine.
    with pytest.raises(LookupError):
        service.take(db, people["pat"], slot)


def test_snooze_hides_the_reminder_for_a_while(db, people):
    rosa = people["rosa"]
    pills = service.create_medicine(db, rosa, {"name": "Metformin", "times": "08:00"})
    backdate(db, pills["id"])
    day = "2026-01-15"
    service.snooze(
        db,
        rosa,
        {"medicine_id": pills["id"], "date": day, "time": "08:00", "minutes": 15},
        now=at(day, "08:05"),
    )
    assert (
        service.day_slots(db, rosa["id"], date(2026, 1, 15), now=at(day, "08:10"))[0][
            "status"
        ]
        == "snoozed"
    )
    assert (
        service.day_slots(db, rosa["id"], date(2026, 1, 15), now=at(day, "08:25"))[0][
            "status"
        ]
        == "due"
    )


def test_adherence_counts_only_doses_whose_time_has_passed(db, people):
    rosa = people["rosa"]
    pills = service.create_medicine(
        db, rosa, {"name": "Metformin", "times": "08:00, 20:00"}
    )
    backdate(db, pills["id"])
    now = at("2026-01-16", "12:00")
    service.take(
        db,
        rosa,
        {"medicine_id": pills["id"], "date": "2026-01-15", "time": "08:00"},
        now=now,
    )
    service.skip(
        db,
        rosa,
        {"medicine_id": pills["id"], "date": "2026-01-15", "time": "20:00"},
        now=now,
    )
    # 16 January: 08:00 missed, 20:00 still to come.
    result = service.adherence(db, rosa["id"], 2, now)
    assert (
        result["taken"],
        result["skipped"],
        result["missed"],
        result["percent"],
    ) == (1, 1, 1, 33)


def test_as_needed_doses_warn_inside_the_gap(db, people):
    me = people["pat"]
    relief = service.create_medicine(
        db,
        me,
        {"name": "Paracetamol", "as_needed": True, "min_gap_hours": 4, "stock": 16},
    )
    first = service.take_as_needed(
        db, me, {"medicine_id": relief["id"]}, now=at("2026-01-15", "08:00")
    )
    assert first["warning"] == ""
    soon = service.take_as_needed(
        db, me, {"medicine_id": relief["id"]}, now=at("2026-01-15", "09:00")
    )
    assert "usual gap" in soon["warning"]
    later = service.take_as_needed(
        db, me, {"medicine_id": relief["id"]}, now=at("2026-01-15", "14:00")
    )
    assert later["warning"] == ""
    assert store.get_medicine(db, relief["id"], me["id"])["stock"] == 13
    assert (
        service.day_slots(
            db, me["id"], date(2026, 1, 15), now=at("2026-01-15", "15:00")
        )
        == []
    )


def test_alerts_are_stable_and_go_when_taken(db, people):
    rosa = people["rosa"]
    pills = service.create_medicine(
        db,
        rosa,
        {
            "name": "Metformin",
            "dose": "500 mg",
            "times": "08:00",
            "with_food": "With breakfast",
        },
    )
    backdate(db, pills["id"])
    now = at("2026-01-15", "08:05")
    first = [a for a in service.alerts(db, rosa, now) if a["id"].startswith("dose:")]
    again = [
        a
        for a in service.alerts(db, rosa, now + timedelta(minutes=3))
        if a["id"].startswith("dose:")
    ]
    assert (
        len(first) == 1
        and first[0]["id"] == again[0]["id"] == f"dose:{pills['id']}:2026-01-15:08:00"
    )
    assert first[0]["speak"] is True and "With breakfast" in first[0]["body"]
    taken, skip = first[0]["actions"]
    assert taken["label"] == "Taken" and taken["path"] == "/api/care/doses/take"
    service.take(db, rosa, taken["body"], now=now)
    assert not [a for a in service.alerts(db, rosa, now) if a["id"].startswith("dose:")]


def test_only_the_person_and_their_caregivers_see_their_health(db, people):
    pat, rosa, mia = people["pat"], people["rosa"], people["mia"]
    service.create_medicine(db, rosa, {"name": "Metformin", "times": "08:00"})
    with pytest.raises(PermissionError):
        service.subject(db, pat, rosa["id"])
    with pytest.raises(PermissionError):
        service.subject(db, pat, "f" * 32)  # nobody: the same answer
    service.add_caregiver(db, rosa, {"profile_id": pat["id"]})
    person, can_edit = service.subject(db, pat, rosa["id"])
    assert person["id"] == rosa["id"] and can_edit is False
    with pytest.raises(PermissionError):
        service.require_own(can_edit)
    with pytest.raises(PermissionError):
        service.subject(db, mia, rosa["id"])
    with pytest.raises(ValueError):
        service.add_caregiver(db, rosa, {"profile_id": pat["id"]})  # already
    with pytest.raises(ValueError):
        service.add_caregiver(db, rosa, {"profile_id": rosa["id"]})
    assert [p["name"] for p in service.caring_for(db, pat)] == ["Rosa"]


def test_caregivers_hear_about_a_missed_dose_once(db, people):
    pat, rosa = people["pat"], people["rosa"]
    pills = service.create_medicine(
        db, rosa, {"name": "Metformin", "dose": "500 mg", "times": "08:00"}
    )
    backdate(db, pills["id"])
    service.add_caregiver(db, rosa, {"profile_id": pat["id"]})
    service.add_caregiver(
        db, rosa, {"email": "nurse@clinic.example", "name": "Nurse Ann"}
    )
    db.execute("UPDATE cr_caregivers SET created_at = ?", (LONG_AGO,))
    db.execute(
        "UPDATE life_profiles SET email = 'pat@home.example' WHERE id = ?", (pat["id"],)
    )
    db.commit()
    now = at("2026-01-15", "09:30")
    assert service.notify_missed(db, now) == 2
    assert service.notify_missed(db, now + timedelta(minutes=5)) == 0
    mails = [m for m in kit.outbox(db) if m["kind"] == "missed_dose"]
    assert sorted(m["recipient"] for m in mails) == [
        "nurse@clinic.example",
        "pat@home.example",
    ]
    assert "Rosa has not taken Metformin" in mails[0]["subject"]
    alert = next(
        a for a in service.alerts(db, pat, now) if a["id"].startswith("missed:")
    )
    assert alert["title"] == "Rosa missed a medicine"
    # Rosa sees her own due/missed state but no caregiver alert about herself.
    assert not [
        a for a in service.alerts(db, rosa, now) if a["id"].startswith("missed:")
    ]


def test_readings_validate_and_give_gentle_hints(db, people):
    me = people["rosa"]
    high = service.add_reading(
        db, me, {"kind": "blood_pressure", "value1": 150, "value2": 95, "value3": 70}
    )
    assert (
        high["display"] == "150/95 mmHg, pulse 70"
        and "above the usual range" in high["hint"]
        and "not medical advice" in high["hint"]
    )
    normal = service.add_reading(
        db, me, {"kind": "blood_pressure", "value1": 118, "value2": 76}
    )
    assert normal["hint"] == ""
    low = service.add_reading(
        db, me, {"kind": "glucose", "value1": "2.5", "unit": "mmol/L"}
    )
    assert "below the usual range" in low["hint"]
    with pytest.raises(ValueError):
        service.add_reading(
            db, me, {"kind": "blood_pressure", "value1": 80, "value2": 120}
        )
    with pytest.raises(ValueError):
        service.add_reading(db, me, {"kind": "glucose", "value1": 5, "unit": "stones"})
    with pytest.raises(ValueError):
        service.add_reading(db, me, {"kind": "oxygen", "value1": 140})
    assert [
        r["kind"] for r in service.readings(db, me["id"], kind="blood_pressure")
    ] == ["blood_pressure", "blood_pressure"]
    assert set(service.latest_readings(db, me["id"])) == {"blood_pressure", "glucose"}
    with pytest.raises(LookupError):
        service.delete_reading(db, people["pat"], high["id"])


def test_the_diary_keeps_one_entry_a_day(db, people):
    me = people["rosa"]
    service.save_day(
        db,
        me,
        {
            "day": "2026-01-15",
            "mood": 2,
            "symptoms": "Headache, tired",
            "note": "Slept badly",
        },
    )
    entry = service.save_day(
        db, me, {"day": "2026-01-15", "mood": 4, "symptoms": ["better"]}
    )
    assert (
        entry["mood"] == 4
        and entry["symptoms"] == ["better"]
        and entry["emoji"] == "🙂"
    )
    with pytest.raises(ValueError):
        service.save_day(db, me, {"mood": 9})


def test_the_emergency_card_shows_only_what_it_should(db, people):
    pat, rosa, mia = people["pat"], people["rosa"], people["mia"]
    service.create_medicine(
        db, rosa, {"name": "Warfarin", "dose": "3 mg", "times": "18:00"}
    )
    service.add_reading(db, rosa, {"kind": "weight", "value1": 64})
    service.save_card(
        db,
        rosa,
        {
            "blood_type": "O-",
            "allergies": "Penicillin",
            "contacts": [{"name": "Pat", "relation": "Son", "phone": "+1 555 0100"}],
        },
    )
    with pytest.raises(ValueError):
        service.save_card(db, rosa, {"contacts": [{"name": "No phone"}]})
    link = public.create_link(
        db, "care", "emergency_card", rosa["id"], title="Emergency card"
    )
    page = public.view(db, link["token"])["page"]
    assert set(page) == {"card"}
    card = page["card"]
    assert (
        card["name"] == "Rosa"
        and card["blood_type"] == "O-"
        and card["medicines"] == [{"name": "Warfarin", "dose": "3 mg"}]
    )
    assert "readings" not in json.dumps(card) and "pin" not in json.dumps(card)
    # An adult in the household keeps a child's card; nobody else's.
    assert service.can_edit_card(db, pat, mia) is True
    assert service.can_edit_card(db, pat, rosa) is False
    assert service.can_edit_card(db, mia, pat) is False


# ---------------------------------------------------------------------------
# the single-user server
# ---------------------------------------------------------------------------


@pytest.fixture
def local_index():
    public.set_index(local._public_link_index)


def anonymous(env, method, path, body=None):
    data = json.dumps(body).encode() if body is not None else None
    request = urllib.request.Request(env.base + path, data=data, method=method)
    request.add_header("Content-Type", "application/json")
    request.add_header("X-Requested-With", "aira")
    request.add_header("Origin", env.base)
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            return response.status, json.loads(response.read() or b"{}")
    except urllib.error.HTTPError as error:
        return error.code, json.loads(error.read() or b"{}")


def _backdate_all(env) -> None:
    with sqlite3.connect(env.user()["data_path"]) as connection:
        connection.execute(
            "UPDATE cr_medicines SET created_at = ?",
            (kit.iso(kit.utcnow() - timedelta(days=5)),),
        )
        connection.execute(
            "UPDATE cr_caregivers SET created_at = ?",
            (kit.iso(kit.utcnow() - timedelta(days=5)),),
        )


def test_a_household_on_the_local_build(env, api, local_index):
    kit.forget_schema()
    household = Household(env, api)
    pat = household.owner
    rosa = household.add("Rosa", age_group="senior")
    mia = household.add("Mia", age_group="child", pin="2468")
    now = kit.utcnow()
    current = now.strftime("%H:%M")
    earlier = (now - timedelta(hours=3)).strftime("%H:%M")
    due = rosa.post(
        "/api/care/medicines",
        {"name": "Metformin", "dose": "500 mg", "times": current, "stock": 20},
        expect=201,
    )["medicine"]

    # Today: a "Taken" button on the medicine due now; pressing it twice changes nothing.
    day = rosa.get("/api/life/today")
    card = next(c for c in day["cards"] if c["title"] == "Medicines today")
    line = card["lines"][0]
    assert line["text"] == "Metformin 500 mg" and line["action"]["label"] == "Taken"
    rosa.post(line["action"]["path"], line["action"]["body"])
    rosa.post(line["action"]["path"], line["action"]["body"])
    assert rosa.get("/api/care/medicines")["medicines"][0]["stock"] == 19
    day = rosa.get("/api/life/today")
    assert (
        next(c for c in day["cards"] if c["title"] == "Medicines today")["lines"][0][
            "done"
        ]
        is True
    )

    # The pulse: a reminder with a stable id for a dose due now.
    rosa.post(
        "/api/care/medicines",
        {"name": "Aspirin", "dose": "75 mg", "times": current},
        expect=201,
    )
    beats = [rosa.get("/api/life/pulse") for _ in range(2)]
    ids = [[a["id"] for a in beat["alerts"] if a["app"] == "care"] for beat in beats]
    assert ids[0] == ids[1] and len(ids[0]) == 1 and ids[0][0].startswith("dose:")
    assert beats[0]["apps"]["care"]["badge"] >= 1

    # A caregiver: Pat sees Rosa's medicines read-only and hears about a missed dose. Mia sees nothing.
    rosa.post("/api/care/medicines", {"name": "Statin", "times": earlier}, expect=201)
    candidates = rosa.get("/api/care/caregivers")["candidates"]
    assert {p["name"] for p in candidates} == {"Careers", "Mia"}
    pat_id = next(p["id"] for p in candidates if p["name"] == "Careers")
    rosa.post("/api/care/caregivers", {"profile_id": pat_id}, expect=201)
    _backdate_all(env)
    seen = pat.get(f"/api/care/overview?person={rosa.id}")
    assert seen["can_edit"] is False and {s["name"] for s in seen["slots"]} == {
        "Metformin",
        "Aspirin",
        "Statin",
    }
    assert seen["mood"] == []
    status, _ = mia.call("GET", f"/api/care/overview?person={rosa.id}")
    assert status == 403
    status, _ = pat.call(
        "PATCH", f"/api/care/medicines/{due['id']}", {"dose": "1000 mg"}
    )
    assert status == 404
    status, _ = pat.call("GET", f"/api/care/diary?person={rosa.id}")
    assert status == 403
    outbox = api.get("/api/suite/outbox")["messages"]
    missed = [m for m in outbox if m["kind"] == "missed_dose"]
    assert (
        len(missed) == 1
        and missed[0]["recipient"] == "careers@example.com"
        and "Statin" in missed[0]["subject"]
    )
    alerts = [
        a for a in pat.get("/api/life/pulse")["alerts"] if a["id"].startswith("missed:")
    ]
    assert alerts and alerts[0]["title"] == "Rosa missed a medicine"

    # Readings and the diary.
    reading = rosa.post(
        "/api/care/readings",
        {"kind": "blood_pressure", "value1": 150, "value2": 95},
        expect=201,
    )["reading"]
    assert "above the usual range" in reading["hint"]
    assert (
        pat.get(f"/api/care/readings?person={rosa.id}")["readings"][0]["id"]
        == reading["id"]
    )
    status, data = rosa.call(
        "POST", "/api/care/readings", {"kind": "blood_pressure", "value1": "high"}
    )
    assert status == 400
    rosa.post("/api/care/diary", {"mood": 5, "symptoms": "none", "note": "A good day"})
    entry = rosa.get("/api/care/diary")["entries"][0]
    assert entry["emoji"] == "😄"
    rosa.ok("DELETE", f"/api/care/diary?day={entry['day']}")
    assert rosa.get("/api/care/diary")["entries"] == []

    # The emergency card: shared by Rosa; Mia (a child) cannot share; Pat keeps Mia's card.
    rosa.post(
        "/api/care/card",
        {
            "blood_type": "A+",
            "allergies": "Penicillin",
            "contacts": [{"name": "Pat", "relation": "Son", "phone": "555 0100"}],
        },
    )
    link = rosa.post("/api/care/card/link")["link"]
    status, page = anonymous(env, "GET", f"/api/public/{link['token']}")
    assert status == 200 and page["page"]["card"]["allergies"] == "Penicillin"
    assert {m["name"] for m in page["page"]["card"]["medicines"]} == {
        "Metformin",
        "Aspirin",
        "Statin",
    }
    status, _ = mia.call("POST", "/api/care/card/link", {})
    assert status == 403
    pat.post(f"/api/care/card?person={mia.id}", {"allergies": "Peanuts"})
    assert pat.post(f"/api/care/card/link?person={mia.id}")["link"]["path"].startswith(
        "/p/"
    )
    status, _ = pat.call(
        "POST", f"/api/care/card?person={rosa.id}", {"allergies": "None"}
    )
    assert status == 403
    rosa.ok("DELETE", "/api/care/card/link")
    assert anonymous(env, "GET", f"/api/public/{link['token']}")[0] == 404


# ---------------------------------------------------------------------------
# the hosted build
# ---------------------------------------------------------------------------


def test_hosted_health_is_private_to_each_member(cloud):
    kit.forget_schema()
    org = build_org(cloud)
    owner = sign_in(cloud, org.owner)
    status, data = owner.call("GET", "/api/care/overview")
    assert status == 402 and data["code"] == "app_required"
    cloud.billing.subscribe_app(org.owner, "care")
    for who in ("owner", "admin", "coach", "candidate", "billing", "auditor"):
        assert (
            sign_in(cloud, getattr(org, who)).call("GET", "/api/care/overview")[0]
            == 200
        ), who
    candidate = sign_in(cloud, org.candidate)
    status, made = candidate.call(
        "POST", "/api/care/medicines", {"name": "Inhaler", "as_needed": True}
    )
    assert status == 201, made
    cand_id = candidate.call("GET", "/api/life/me")[1]["profile"]["id"]
    owner_id = owner.call("GET", "/api/life/me")[1]["profile"]["id"]
    assert owner.call("GET", f"/api/care/medicines?person={cand_id}")[0] == 403
    status, listed = owner.call("GET", "/api/care/medicines")
    assert (
        status == 200 and listed["medicines"] == []
    )  # one shared database, but only your own rows
    status, _ = candidate.call("POST", "/api/care/caregivers", {"profile_id": owner_id})
    assert status == 201
    status, seen = owner.call("GET", f"/api/care/medicines?person={cand_id}")
    assert (
        status == 200
        and seen["can_edit"] is False
        and seen["medicines"][0]["name"] == "Inhaler"
    )
    # The public emergency card works for a visitor; another organization sees nothing.
    candidate.call("POST", "/api/care/card", {"allergies": "Dust"})
    status, link = candidate.call("POST", "/api/care/card/link", {})
    assert status == 200
    status, page = Browser(cloud).call("GET", f"/api/public/{link['link']['token']}")
    assert status == 200 and page["page"]["card"]["allergies"] == "Dust"
    other = build_org(cloud, "Other Org", slug_email="other.test")
    cloud.billing.subscribe_app(other.owner, "care")
    stranger = sign_in(cloud, other.owner)
    assert stranger.call("GET", f"/api/care/medicines?person={cand_id}")[0] == 403
    assert stranger.call("GET", "/api/care/medicines")[1]["medicines"] == []
