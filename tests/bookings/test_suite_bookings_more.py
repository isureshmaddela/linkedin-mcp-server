"""Bookings (Commerce): the waiting list for a full day, team pay (commission), and regressions found while
using it."""

from __future__ import annotations

import json
import sqlite3
import urllib.error
import urllib.request
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import pytest
from cloud_support import Browser, build_org, sign_in

import auto_apply_assistant as local
import test_careers
from aira.suite import kit, public
from aira.suite.bookings import service, store

env = test_careers.env
api = test_careers.api
stub = test_careers.stub

NY = ZoneInfo("America/New_York")
MORNINGS = [{"weekday": d, "start": "09:00", "end": "11:00"} for d in range(7)]


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


@pytest.fixture
def db(tmp_path):
    kit.forget_schema()
    public.set_index(public._MemoryIndex())
    connection = sqlite3.connect(tmp_path / "bookings.sqlite3")
    connection.row_factory = sqlite3.Row
    store.ensure(connection)
    kit.save_business(
        connection,
        {
            "name": "Glow Studio",
            "email": "owner@glow.example",
            "timezone": "America/New_York",
        },
    )
    yield connection
    connection.close()


@pytest.fixture
def local_index():
    public.set_index(local._public_link_index)


def future_day(days=10) -> str:
    return (datetime.now(NY).date() + timedelta(days=days)).isoformat()


def at(day: str, hhmm: str, tz=NY) -> str:
    hours, minutes = (int(x) for x in hhmm.split(":"))
    return kit.iso(
        datetime.combine(
            date.fromisoformat(day),
            datetime.min.time().replace(hour=hours, minute=minutes),
            tzinfo=tz,
        )
    )


def setup(db, *, price=6000, deposit=0, commission=None):
    person = store.create_staff(
        db,
        {
            "name": "Ana",
            "hours": MORNINGS,
            **({"commission_percent": commission} if commission is not None else {}),
        },
    )
    cut = store.create_service(
        db,
        {"name": "Cut", "minutes": 60, "price_cents": price, "deposit_cents": deposit},
    )
    return person, cut


def book(db, cut, day, hhmm, name="Maya Lopez", email="maya@example.com", **extra):
    return service.book(
        db,
        service_id=cut["id"],
        start=at(day, hhmm),
        customer={"name": name, "email": email, **extra.pop("customer", {})},
        **extra,
    )


def outbox(db, kind):
    return [m for m in kit.outbox(db) if m["kind"] == kind]


# ---------------------------------------------------------------------------
# regressions
# ---------------------------------------------------------------------------


def test_times_read_the_same_whatever_the_day_of_the_month():
    utc = ZoneInfo("UTC")
    assert (
        service.local_label("2026-10-27T09:45:00Z", utc)
        == "Tuesday 27 October 2026 at 09:45"
    )
    assert (
        service.local_label("2026-10-07T09:05:00Z", utc)
        == "Wednesday 7 October 2026 at 09:05"
    )


def test_a_customer_who_gives_only_a_phone_number_is_held_to_the_booking_limit(db):
    _, cut = setup(db)
    store.save_rules(db, {"max_active_per_customer": 1, "min_notice_hours": 0})
    day = future_day()
    service.book(
        db,
        service_id=cut["id"],
        start=at(day, "09:00"),
        customer={"name": "Bo", "phone": "+1 555 0101"},
        source="public",
    )
    with pytest.raises(ValueError, match="most upcoming bookings"):
        service.book(
            db,
            service_id=cut["id"],
            start=at(day, "10:00"),
            customer={"name": "Bo", "phone": "+1 555 0101"},
            source="public",
        )


def test_a_completed_visit_with_only_the_deposit_paid_still_shows_money_due(db):
    _, cut = setup(db, price=6000, deposit=1000)
    made = book(db, cut, future_day(), "09:00")
    service.mark_paid(db, made["id"], kind="deposit")
    service.set_status(db, made["id"], "completed")
    assert service.overview(db)["unpaid_completed"] == 1
    service.mark_paid(db, made["id"], kind="full")
    assert service.overview(db)["unpaid_completed"] == 0


def test_the_calendar_starts_on_the_businesss_own_day(db):
    tokyo = datetime(2026, 3, 1, 20, 0, tzinfo=timezone.utc)  # already 2 March in Tokyo
    kit.save_business(db, {"timezone": "Asia/Tokyo"})
    assert service.local_today(db, tokyo) == "2026-03-02"


# ---------------------------------------------------------------------------
# the waiting list
# ---------------------------------------------------------------------------


def test_a_full_day_has_a_waiting_list_and_a_cancellation_tells_them(db):
    person, cut = setup(db)
    day = future_day()
    page = public.create_link(
        db, "bookings", service.PAGE_KIND, service.PAGE_REF, title="Book"
    )
    first = book(db, cut, day, "09:00", name="Ola", email="ola@example.com")
    book(db, cut, day, "10:00", name="Pat", email="pat@example.com")
    assert service.slots_for_day(db, cut["id"], day) == []
    assert service.day_open(db, cut, day) is True

    wait = service.join_waitlist(
        db,
        service_id=cut["id"],
        day=day,
        customer={"name": "Maya Lopez", "email": "maya@example.com"},
    )
    again = service.join_waitlist(
        db,
        service_id=cut["id"],
        day=day,
        customer={"name": "Maya", "email": "MAYA@example.com"},
    )
    assert again["id"] == wait["id"]  # once per day
    service.join_waitlist(
        db,
        service_id=cut["id"],
        day=day,
        customer={"name": "Cal", "phone": "+1 555 0199"},
    )
    listed = service.list_waitlist(db)
    assert [row["name"] for row in listed] == ["Maya Lopez", "Cal"] and listed[0][
        "free_now"
    ] is False
    assert service.overview(db)["waiting"] == 2

    service.cancel(db, first["id"], reason="Sick")
    opened = outbox(db, "waitlist_opening")
    assert [m["recipient"] for m in opened] == ["maya@example.com"]
    assert page["path"] in opened[0]["body"] and "09:00" in opened[0]["body"]
    rows = {row["name"]: row for row in service.list_waitlist(db)}
    assert (
        rows["Maya Lopez"]["status"] == "notified"
        and rows["Maya Lopez"]["free_now"] is True
    )
    assert (
        rows["Cal"]["status"] == "waiting" and rows["Cal"]["free_now"] is True
    )  # phone only: call them

    # Maya books the freed time: she is off the list
    book(db, cut, day, "09:00", name="Maya Lopez", email="maya@example.com")
    assert {row["name"]: row["status"] for row in service.list_waitlist(db)}[
        "Maya Lopez"
    ] == "booked"
    # cancelling again tells nobody new by email (Cal has no email)
    second = service.list_bookings(db, when="all", q="Pat")[0]
    service.cancel(db, second["id"])
    assert len(outbox(db, "waitlist_opening")) == 1


def test_moving_a_booking_away_offers_its_old_time(db):
    _, cut = setup(db)
    day, later = future_day(), future_day(12)
    moved = book(db, cut, day, "09:00")
    book(db, cut, day, "10:00", name="Pat", email="pat@example.com")
    service.join_waitlist(
        db,
        service_id=cut["id"],
        day=day,
        customer={"name": "Bo", "email": "bo@example.com"},
    )
    service.reschedule(db, moved["id"], at(later, "09:00"))
    assert [m["recipient"] for m in outbox(db, "waitlist_opening")] == [
        "bo@example.com"
    ]


def test_waiting_list_rules(db):
    person, cut = setup(db)
    with pytest.raises(ValueError, match="passed"):
        service.join_waitlist(
            db,
            service_id=cut["id"],
            day="2020-01-01",
            customer={"name": "A", "email": "a@example.com"},
        )
    with pytest.raises(ValueError, match="too far ahead"):
        service.join_waitlist(
            db,
            service_id=cut["id"],
            day=future_day(400),
            customer={"name": "A", "email": "a@example.com"},
        )
    with pytest.raises(ValueError, match="email address or a phone"):
        service.join_waitlist(
            db, service_id=cut["id"], day=future_day(), customer={"name": "A"}
        )
    store.add_time_off(db, {"start_day": future_day(3)})
    with pytest.raises(ValueError, match="closed"):
        service.join_waitlist(
            db,
            service_id=cut["id"],
            day=future_day(3),
            customer={"name": "A", "email": "a@example.com"},
        )
    for offset in range(5):
        service.join_waitlist(
            db,
            service_id=cut["id"],
            day=future_day(5 + offset),
            customer={"name": "A", "email": "a@example.com"},
        )
    with pytest.raises(ValueError, match="several days"):
        service.join_waitlist(
            db,
            service_id=cut["id"],
            day=future_day(11),
            customer={"name": "A", "email": "a@example.com"},
        )
    entry = service.list_waitlist(db)[0]
    assert (
        service.set_waitlist_status(db, entry["id"], "removed")["status"] == "removed"
    )
    assert len(service.list_waitlist(db)) == 4
    with pytest.raises(ValueError):
        service.set_waitlist_status(db, entry["id"], "bogus")


# ---------------------------------------------------------------------------
# team pay
# ---------------------------------------------------------------------------


def test_team_pay_counts_completed_takings_packages_and_commission(db):
    ana, cut = setup(db, price=6000, commission="40")
    ben = store.create_staff(
        db, {"name": "Ben", "hours": MORNINGS, "commission_percent": 12.5}
    )
    assert ana["commission_percent"] == 40 and ben["commission_percent"] == 12.5
    day = future_day(2)
    paid = book(db, cut, day, "09:00", staff_id=ana["id"])
    service.mark_paid(db, paid["id"], kind="full")
    service.set_status(db, paid["id"], "completed")
    by_package = book(
        db, cut, day, "09:00", name="Bo", email="bo@example.com", staff_id=ben["id"]
    )
    package = store.create_package(
        db, {"name": "4 cuts", "sessions": 4, "price_cents": 20000}
    )
    sold = service.sell_package(
        db, customer_id=by_package["customer_id"], package_id=package["id"], paid=True
    )
    service.redeem(db, by_package["id"], sold["id"])
    service.set_status(db, by_package["id"], "completed")
    missed = book(
        db, cut, day, "10:00", name="Cy", email="cy@example.com", staff_id=ana["id"]
    )
    service.set_status(db, missed["id"], "no_show")
    book(
        db, cut, day, "10:00", name="Di", email="di@example.com", staff_id=ben["id"]
    )  # not done yet

    report = service.earnings(db, day, day)
    team = {row["name"]: row for row in report["team"]}
    assert team["Ana"]["appointments"] == 1 and team["Ana"]["no_shows"] == 1
    assert (
        team["Ana"]["takings_cents"] == 6000 and team["Ana"]["commission_cents"] == 2400
    )
    assert team["Ben"]["takings_cents"] == 5000  # a quarter of the package
    assert team["Ben"]["commission_cents"] == 625
    assert report["totals"] == {
        "appointments": 2,
        "no_shows": 1,
        "takings_cents": 11000,
        "commission_cents": 3025,
    }
    sheet = service.earnings_csv(db, day, day).decode()
    assert sheet.splitlines()[0].startswith("day,staff_name,customer_name")
    assert "Maya Lopez" in sheet and "package" in sheet
    assert service.earnings(db, future_day(30), future_day(31))["team"] == []


def test_team_pay_input_is_checked(db):
    with pytest.raises(ValueError, match="between 0 and 100"):
        store.create_staff(db, {"name": "Ana", "commission_percent": 120})
    assert store.list_staff(db) == []  # nothing half-saved
    with pytest.raises(ValueError, match="a number"):
        store.create_staff(db, {"name": "Ana", "commission_percent": "lots"})
    with pytest.raises(ValueError, match="on or after"):
        service.earnings(db, "2026-05-02", "2026-05-01")
    with pytest.raises(ValueError, match="a year"):
        service.earnings(db, "2024-01-01", "2026-01-01")
    person = store.create_staff(db, {"name": "Ana", "commission_percent": "35%"})
    assert (
        store.update_staff(db, person["id"], {"commission_percent": ""})[
            "commission_percent"
        ]
        == 0
    )


# ---------------------------------------------------------------------------
# the local server and the hosted gateway
# ---------------------------------------------------------------------------


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


def test_waiting_list_and_team_pay_on_the_local_build(env, api, local_index):
    kit.forget_schema()
    api.post(
        "/api/suite/business",
        {"name": "Glow Studio", "email": "owner@glow.example", "timezone": "UTC"},
    )
    person = api.post(
        "/api/bookings/staff",
        {
            "name": "Ana",
            "commission_percent": 50,
            "hours": [
                {"weekday": d, "start": "09:00", "end": "10:00"} for d in range(7)
            ],
        },
        expect=201,
    )["person"]
    assert person["commission_percent"] == 50
    cut = api.post(
        "/api/bookings/services",
        {"name": "Cut", "minutes": 60, "price_cents": 4000},
        expect=201,
    )["service"]
    token = api.post("/api/bookings/page-link", {})["link"]["token"]
    day = (datetime.now(timezone.utc).date() + timedelta(days=5)).isoformat()
    status, found = anonymous(
        env,
        "POST",
        f"/api/public/{token}/slots",
        {"service_id": cut["id"], "day": day, "days": 1},
    )
    assert status == 200 and found["days"][0]["open"] is True
    start = found["days"][0]["slots"][0]["start"]
    status, made = anonymous(
        env,
        "POST",
        f"/api/public/{token}/book",
        {
            "service_id": cut["id"],
            "start": start,
            "name": "Ola",
            "email": "ola@example.com",
        },
    )
    assert status == 201, made
    status, full = anonymous(
        env,
        "POST",
        f"/api/public/{token}/slots",
        {"service_id": cut["id"], "day": day, "days": 1},
    )
    assert full["days"][0]["slots"] == [] and full["days"][0]["open"] is True
    status, joined = anonymous(
        env,
        "POST",
        f"/api/public/{token}/join_waitlist",
        {
            "service_id": cut["id"],
            "day": day,
            "name": "Maya",
            "email": "maya@example.com",
            "status": "booked",
        },
    )
    assert status == 201 and joined == {"waiting": True, "day": day}
    status, refused = anonymous(
        env,
        "POST",
        f"/api/public/{token}/join_waitlist",
        {"service_id": "0" * 32, "day": day, "name": "M", "email": "m@example.com"},
    )
    assert status == 404
    waiting = api.get("/api/bookings/waitlist")["waitlist"]
    assert (
        waiting[0]["name"] == "Maya"
        and waiting[0]["status"] == "waiting"
        and waiting[0]["service_name"] == "Cut"
    )
    assert api.get("/api/bookings/overview")["waiting"] == 1

    manage = made["manage_path"].rsplit("/", 1)[-1]
    status, cancelled = anonymous(
        env, "POST", f"/api/public/{manage}/cancel", {"reason": "Moving"}
    )
    assert status == 200, cancelled
    mails = [
        m
        for m in api.get("/api/suite/outbox")["messages"]
        if m["kind"] == "waitlist_opening"
    ]
    assert (
        mails
        and mails[0]["recipient"] == "maya@example.com"
        and f"/p/{token}" in mails[0]["body"]
    )
    assert api.get("/api/bookings/waitlist")["waitlist"][0]["status"] == "notified"
    entry = api.get("/api/bookings/waitlist")["waitlist"][0]
    assert api.call("DELETE", f"/api/bookings/waitlist/{entry['id']}")[0] == 200
    assert api.get("/api/bookings/waitlist")["waitlist"] == []

    booked = api.post(
        "/api/bookings/bookings",
        {"service_id": cut["id"], "start": start, "name": "Pat"},
        expect=201,
    )["booking"]
    api.post(f"/api/bookings/bookings/{booked['id']}/pay", {"kind": "full"})
    api.ok("PATCH", f"/api/bookings/bookings/{booked['id']}", {"status": "completed"})
    report = api.get(f"/api/bookings/earnings?from={day}&to={day}")
    assert (
        report["team"][0]["takings_cents"] == 4000
        and report["team"][0]["commission_cents"] == 2000
    )
    month = api.get("/api/bookings/earnings")
    assert month["from"].endswith("-01")
    request = urllib.request.Request(
        env.base + f"/api/bookings/earnings?format=csv&from={day}&to={day}",
        headers={"X-Requested-With": "aira"},
    )
    with api.opener.open(request, timeout=30) as response:
        assert "Pat" in response.read().decode()
    assert (
        api.call("GET", "/api/bookings/earnings?from=2026-05-02&to=2026-05-01")[0]
        == 400
    )


def test_hosted_waiting_list_and_team_pay_need_commerce_and_the_right_role(cloud):
    kit.forget_schema()
    org = build_org(cloud)
    owner = sign_in(cloud, org.owner)
    assert owner.call("GET", "/api/bookings/earnings")[0] == 402
    cloud.billing.subscribe_app(org.owner, "commerce")
    owner.call("POST", "/api/suite/business", {"name": "Acme Spa", "timezone": "UTC"})
    status, person = owner.call(
        "POST",
        "/api/bookings/staff",
        {
            "name": "Ana",
            "commission_percent": 30,
            "hours": [
                {"weekday": d, "start": "09:00", "end": "10:00"} for d in range(7)
            ],
        },
    )
    assert status == 201, person
    status, cut = owner.call(
        "POST",
        "/api/bookings/services",
        {"name": "Massage", "minutes": 60, "price_cents": 8000},
    )
    token = owner.call("POST", "/api/bookings/page-link", {})[1]["link"]["token"]
    day = (datetime.now(timezone.utc).date() + timedelta(days=4)).isoformat()
    visitor = Browser(cloud)
    slots = visitor.call(
        "POST",
        f"/api/public/{token}/slots",
        {"service_id": cut["service"]["id"], "day": day, "days": 1},
    )[1]
    visitor.call(
        "POST",
        f"/api/public/{token}/book",
        {
            "service_id": cut["service"]["id"],
            "start": slots["days"][0]["slots"][0]["start"],
            "name": "Ola",
            "email": "ola@example.com",
        },
    )
    status, joined = Browser(cloud).call(
        "POST",
        f"/api/public/{token}/join_waitlist",
        {
            "service_id": cut["service"]["id"],
            "day": day,
            "name": "Maya",
            "email": "maya@example.com",
        },
    )
    assert status == 201, joined
    auditor = sign_in(cloud, org.auditor)
    status, listed = auditor.call("GET", "/api/bookings/waitlist")
    assert status == 200 and listed["waitlist"][0]["name"] == "Maya"
    assert (
        auditor.call("DELETE", f"/api/bookings/waitlist/{listed['waitlist'][0]['id']}")[
            0
        ]
        == 403
    )
    assert auditor.call("GET", "/api/bookings/earnings")[0] == 200
    assert sign_in(cloud, org.candidate).call("GET", "/api/bookings/waitlist")[0] == 403
    coach = sign_in(cloud, org.coach)
    assert (
        coach.call(
            "PATCH",
            f"/api/bookings/waitlist/{listed['waitlist'][0]['id']}",
            {"status": "removed"},
        )[0]
        == 200
    )

    other = build_org(cloud, "Other Spa", slug_email="otherspa.test")
    cloud.billing.subscribe_app(other.owner, "commerce")
    status, theirs = sign_in(cloud, other.owner).call("GET", "/api/bookings/waitlist")
    assert status == 200 and theirs["waitlist"] == []


def test_the_reason_the_business_gives_for_cancelling_reaches_the_customer(db):
    _, cut = setup(db)
    made = book(db, cut, future_day(), "09:00")
    cancelled = service.cancel(db, made["id"], reason="Ana is ill")
    assert service.public_booking(db, cancelled)["cancel_reason"] == "Ana is ill"
    mail = outbox(db, "booking_cancelled")[-1]
    assert "Reason: Ana is ill" in mail["body"]
    live = book(db, cut, future_day(), "10:00", name="Bo", email="bo@example.com")
    assert service.public_booking(db, live)["cancel_reason"] == ""


def test_the_hub_card_shows_the_waiting_list(db):
    _, cut = setup(db)
    assert "On the waiting list" not in [
        c["label"] for c in service.summary(db)["cards"]
    ]
    service.join_waitlist(
        db,
        service_id=cut["id"],
        day=future_day(),
        customer={"name": "A", "email": "a@example.com"},
    )
    card = next(
        c for c in service.summary(db)["cards"] if c["label"] == "On the waiting list"
    )
    assert card["value"] == 1
