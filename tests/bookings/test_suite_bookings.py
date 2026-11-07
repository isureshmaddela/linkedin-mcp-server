"""Bookings (aira/suite/bookings): slots, the booking re-check, customer rules, payments, packages, messages,
the single-user server and the hosted gateway."""

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
ALL_WEEK = [{"weekday": d, "start": "09:00", "end": "12:00"} for d in range(7)] + [
    {"weekday": d, "start": "13:00", "end": "17:00"} for d in range(7)
]


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


def setup(db, *, people=1, minutes=60, buffer=15, price=5000, deposit=1000):
    staff = [
        store.create_staff(
            db,
            {"name": name, "email": f"{name.lower()}@glow.example", "hours": ALL_WEEK},
        )
        for name in ("Ana", "Ben", "Cy")[:people]
    ]
    cut = store.create_service(
        db,
        {
            "name": "Cut",
            "minutes": minutes,
            "buffer_minutes": buffer,
            "price_cents": price,
            "deposit_cents": deposit,
        },
    )
    return staff, cut


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


def locals_of(slots):
    return [slot["local"] for slot in slots]


CUSTOMER = {"name": "Maya Lopez", "email": "maya@example.com", "phone": "+1 555 0100"}


# ---------------------------------------------------------------------------
# availability
# ---------------------------------------------------------------------------


def test_slots_follow_hours_buffers_and_conflicts(db):
    staff, cut = setup(db)
    day = future_day()
    slots = service.slots_for_day(db, cut["id"], day)
    assert locals_of(slots)[:3] == ["09:00", "09:15", "09:30"]
    assert "11:00" in locals_of(slots) and "11:15" not in locals_of(
        slots
    )  # must end by 12:00
    assert (
        "12:00" not in locals_of(slots)
        and "13:00" in locals_of(slots)
        and "16:00" in locals_of(slots)
    )
    assert all(slot["staff_ids"] == [staff[0]["id"]] for slot in slots)
    service.book(
        db,
        service_id=cut["id"],
        start=at(day, "10:00"),
        customer=CUSTOMER,
        source="public",
    )
    after = locals_of(service.slots_for_day(db, cut["id"], day))
    # 60 minutes plus a 15 minute buffer on both appointments: nothing in the morning fits around 10:00-11:15.
    assert not [t for t in after if t < "12:00"]
    assert "13:00" in after


def test_minimum_notice_horizon_and_past_days(db):
    _, cut = setup(db)
    day = future_day(3)
    now = datetime.fromisoformat(at(day, "09:30").replace("Z", "+00:00"))
    first = locals_of(service.slots_for_day(db, cut["id"], day, now=now))
    assert first[0] == "13:00"  # two hours of notice from 09:30 rules out the morning
    store.save_rules(db, {"max_days_ahead": 30})
    assert service.slots_for_day(db, cut["id"], future_day(31)) == []
    assert service.slots_for_day(db, cut["id"], future_day(-1)) == []
    # A booking the business makes itself ignores notice and horizon (still inside hours).
    assert service.slots_for_day(db, cut["id"], future_day(31), customer_rules=False)


def test_time_off_for_one_person_or_the_whole_business(db):
    staff, cut = setup(db, people=2)
    day = future_day()
    store.add_time_off(
        db, {"staff_id": staff[0]["id"], "start_day": day, "reason": "Holiday"}
    )
    slots = service.slots_for_day(db, cut["id"], day)
    assert slots and all(slot["staff_ids"] == [staff[1]["id"]] for slot in slots)
    store.add_time_off(db, {"start_day": day, "end_day": day, "reason": "Closed"})
    assert service.slots_for_day(db, cut["id"], day) == []
    with pytest.raises(ValueError):
        store.add_time_off(db, {"start_day": day, "end_day": future_day(5)})


def test_slots_are_right_across_a_clock_change(db):
    _, cut = setup(db, minutes=60, buffer=0)
    store.save_rules(db, {"slot_interval": 60})
    now = datetime(2026, 10, 20, 12, 0, tzinfo=timezone.utc)
    before = service.slots_for_day(db, cut["id"], "2026-10-31", now=now)
    after = service.slots_for_day(
        db, cut["id"], "2026-11-01", now=now
    )  # clocks go back at 02:00
    assert (
        before[0]["start"] == "2026-10-31T13:00:00Z" and before[0]["local"] == "09:00"
    )
    assert after[0]["start"] == "2026-11-01T14:00:00Z" and after[0]["local"] == "09:00"
    assert locals_of(after) == locals_of(before)


def test_hours_must_be_valid_and_not_overlap(db):
    person = store.create_staff(db, {"name": "Dee"})
    with pytest.raises(ValueError, match="must be after"):
        store.set_hours(
            db, person["id"], [{"weekday": 0, "start": "12:00", "end": "09:00"}]
        )
    with pytest.raises(ValueError, match="overlaps"):
        store.set_hours(
            db,
            person["id"],
            [
                {"weekday": 0, "start": "09:00", "end": "12:00"},
                {"weekday": 0, "start": "11:00", "end": "13:00"},
            ],
        )
    assert (
        len(
            store.set_hours(
                db, person["id"], [{"weekday": 1, "start": "9:00", "end": "17:30"}]
            )
        )
        == 1
    )
    with pytest.raises(ValueError, match="deposit"):
        store.create_service(
            db, {"name": "X", "minutes": 30, "price_cents": 1000, "deposit_cents": 2000}
        )


# ---------------------------------------------------------------------------
# booking
# ---------------------------------------------------------------------------


def test_the_same_slot_cannot_be_booked_twice(db):
    staff, cut = setup(db, people=2)
    day = future_day()
    start = at(day, "13:00")
    first = service.book(
        db, service_id=cut["id"], start=start, customer=CUSTOMER, source="public"
    )
    second = service.book(
        db,
        service_id=cut["id"],
        start=start,
        customer={"name": "Ola", "email": "ola@example.com"},
        source="public",
    )
    assert {first["staff_id"], second["staff_id"]} == {staff[0]["id"], staff[1]["id"]}
    with pytest.raises(ValueError, match="no longer available"):
        service.book(
            db,
            service_id=cut["id"],
            start=start,
            customer={"name": "Pat", "phone": "555 0199"},
            source="public",
        )
    with pytest.raises(ValueError, match="email address or a phone"):
        service.book(
            db,
            service_id=cut["id"],
            start=at(day, "15:00"),
            customer={"name": "Nobody"},
            source="public",
        )
    # A walk-in outside hours is allowed for the business, never on top of another booking.
    walk_in = service.book(
        db,
        service_id=cut["id"],
        start=at(day, "18:00"),
        customer={"name": "Walk In"},
        staff_id=staff[0]["id"],
        outside_hours=True,
    )
    assert walk_in["status"] == "confirmed" and walk_in["source"] == "manual"
    with pytest.raises(ValueError, match="already has a booking"):
        service.book(
            db,
            service_id=cut["id"],
            start=at(day, "18:30"),
            customer={"name": "Late"},
            staff_id=staff[0]["id"],
            outside_hours=True,
        )
    assert first["reference"].startswith("BK-") and first["manage_path"].startswith(
        "/p/"
    )
    customers = service.list_customers(db)
    assert {c["name"] for c in customers} >= {"Maya Lopez", "Ola", "Walk In"}


def test_a_customer_may_hold_only_a_few_upcoming_bookings(db):
    _, cut = setup(db)
    store.save_rules(db, {"max_active_per_customer": 2})
    for hhmm in ("09:00", "13:00"):
        service.book(
            db,
            service_id=cut["id"],
            start=at(future_day(), hhmm),
            customer=CUSTOMER,
            source="public",
        )
    with pytest.raises(ValueError, match="most upcoming"):
        service.book(
            db,
            service_id=cut["id"],
            start=at(future_day(), "15:00"),
            customer=CUSTOMER,
            source="public",
        )


def test_confirmation_and_a_reminder_a_day_before_are_queued(db):
    _, cut = setup(db)
    booking = service.book(
        db,
        service_id=cut["id"],
        start=at(future_day(), "13:00"),
        customer=CUSTOMER,
        source="public",
    )
    messages = {m["kind"]: m for m in kit.outbox(db, app="bookings")}
    assert messages["booking_confirmation"]["recipient"] == "maya@example.com"
    assert (
        "BK-" in messages["booking_confirmation"]["body"]
        and "Deposit" in messages["booking_confirmation"]["body"]
    )
    reminder = messages["booking_reminder"]
    assert reminder["send_after"] == kit.iso(
        datetime.fromisoformat(booking["start_at"].replace("Z", "+00:00"))
        - timedelta(hours=24)
    )
    # Booked for an hour or two from now: the confirmation is enough.
    soon = service.book(
        db,
        service_id=cut["id"],
        start=kit.iso(
            (datetime.now(timezone.utc) + timedelta(hours=2)).replace(
                minute=0, second=0, microsecond=0
            )
        ),
        customer={"name": "Soon", "email": "soon@example.com"},
        source="manual",
        outside_hours=True,
    )
    assert not [
        m
        for m in kit.outbox(db, app="bookings")
        if m["ref_id"] == soon["id"] and m["kind"] == "booking_reminder"
    ]
    # Completing asks for a review when a review link is set.
    store.save_rules(db, {"review_link": "https://g.page/r/glow"})
    service.set_status(db, booking["id"], "completed")
    kinds = [
        m["kind"]
        for m in kit.outbox(db, app="bookings")
        if m["ref_id"] == booking["id"]
    ]
    assert "review_request" in kinds


def test_customers_cancel_and_reschedule_only_before_the_notice(db):
    _, cut = setup(db)
    day = future_day(5)
    booking = service.book(
        db,
        service_id=cut["id"],
        start=at(day, "13:00"),
        customer=CUSTOMER,
        source="public",
    )
    late = datetime.fromisoformat(
        booking["start_at"].replace("Z", "+00:00")
    ) - timedelta(hours=3)
    with pytest.raises(ValueError, match="closes 24 hours"):
        service.cancel(db, booking["id"], by="customer", now=late)
    with pytest.raises(ValueError, match="close 24 hours"):
        service.reschedule(db, booking["id"], at(day, "15:00"), by="customer", now=late)
    moved = service.reschedule(db, booking["id"], at(day, "15:00"), by="customer")
    assert (
        moved["start_at"] == at(day, "15:00")
        and moved["staff_id"] == booking["staff_id"]
    )
    other = service.book(
        db,
        service_id=cut["id"],
        start=at(day, "09:00"),
        customer={"name": "Ola", "email": "ola@example.com"},
        source="public",
    )
    with pytest.raises(ValueError, match="no longer available"):
        service.reschedule(db, moved["id"], other["start_at"], by="customer")
    reminders = [
        m
        for m in kit.outbox(db, app="bookings")
        if m["ref_id"] == booking["id"] and m["kind"] == "booking_reminder"
    ]
    assert sorted(m["status"] for m in reminders) == ["cancelled", "pending"]
    cancelled = service.cancel(db, booking["id"], by="customer", reason="Sick")
    assert cancelled["status"] == "cancelled"
    assert at(day, "15:00") in [
        slot["start"] for slot in service.slots_for_day(db, cut["id"], day)
    ]
    assert all(
        m["status"] == "cancelled"
        for m in kit.outbox(db, app="bookings")
        if m["ref_id"] == booking["id"] and m["kind"] == "booking_reminder"
    )
    assert [
        m
        for m in kit.outbox(db, app="bookings")
        if m["kind"] == "booking_cancelled_notice"
    ][0]["recipient"] == "owner@glow.example"
    with pytest.raises(ValueError):
        service.cancel(db, booking["id"])


def test_payments_count_once_and_unpaying_voids(db):
    _, cut = setup(db)
    booking = service.book(
        db, service_id=cut["id"], start=at(future_day(), "13:00"), customer=CUSTOMER
    )
    service.mark_paid(db, booking["id"], kind="deposit")
    assert kit.revenue(db, app="bookings")["total_cents"] == 1000
    paid = service.mark_paid(db, booking["id"], kind="full")
    assert paid["paid_cents"] == 5000 and paid["due_cents"] == 0
    assert kit.revenue(db, app="bookings")["total_cents"] == 5000
    assert len(kit.revenue_lines(db, app="bookings")) == 1
    service.mark_unpaid(db, booking["id"])
    assert kit.revenue(db, app="bookings")["total_cents"] == 0
    with pytest.raises(ValueError):
        service.mark_paid(db, booking["id"], kind="amount", amount_cents="0")
    assert service.list_customers(db)[0]["spend_cents"] == 0


def test_packages_are_sold_redeemed_and_restored(db):
    _, cut = setup(db)
    package = store.create_package(
        db,
        {
            "name": "3 cuts",
            "service_id": cut["id"],
            "sessions": 2,
            "price_cents": 12000,
        },
    )
    day = future_day()
    first = service.book(
        db, service_id=cut["id"], start=at(day, "09:00"), customer=CUSTOMER
    )
    second = service.book(
        db, service_id=cut["id"], start=at(day, "13:00"), customer=CUSTOMER
    )
    sold = service.sell_package(
        db, customer_id=first["customer_id"], package_id=package["id"], paid=True
    )
    assert (
        sold["remaining"] == 2
        and kit.revenue(db, app="bookings")["total_cents"] == 12000
    )
    service.redeem(db, first["id"], sold["id"])
    redeemed = service.redeem(db, second["id"], sold["id"])
    assert redeemed["payment_kind"] == "package" and redeemed["due_cents"] == 0
    assert service._sold(db, sold["id"])["status"] == "used_up"
    third = service.book(
        db, service_id=cut["id"], start=at(day, "15:00"), customer=CUSTOMER
    )
    with pytest.raises(ValueError, match="no sessions left"):
        service.redeem(db, third["id"], sold["id"])
    with pytest.raises(ValueError, match="package"):
        service.mark_paid(db, second["id"])
    service.cancel(db, second["id"])
    assert service._sold(db, sold["id"])["remaining"] == 1
    other = service.book(
        db,
        service_id=cut["id"],
        start=at(future_day(11), "09:00"),
        customer={"name": "Ola", "email": "ola@example.com"},
    )
    with pytest.raises(ValueError, match="another customer"):
        service.redeem(db, other["id"], sold["id"])
    assert (
        kit.revenue(db, app="bookings")["total_cents"] == 12000
    )  # sessions are paid by the package, once


def test_overview_checklist_and_customer_stats(db):
    _, cut = setup(db)
    booking = service.book(
        db, service_id=cut["id"], start=at(future_day(2), "13:00"), customer=CUSTOMER
    )
    data = service.overview(db)
    assert (
        data["bookings_this_week"] == 1 and data["upcoming"][0]["id"] == booking["id"]
    )
    steps = {step["id"]: step["done"] for step in data["checklist"]}
    assert steps == {
        "profile": True,
        "service": True,
        "hours": True,
        "share": False,
        "first": True,
    }
    public.create_link(db, "bookings", service.PAGE_KIND, service.PAGE_REF)
    assert all(step["done"] for step in service.checklist(db))
    service.set_status(db, booking["id"], "no_show")
    assert service.overview(db)["no_show_rate"] == 100
    customer = service.get_customer(db, booking["customer_id"])
    assert (
        customer["no_shows"] == 1
        and customer["history"][0]["reference"] == booking["reference"]
    )
    assert b"Maya Lopez" in service.customers_csv(db)
    cards = service.summary(db)["cards"]
    assert cards[2]["value"] == "100%" and cards[2]["tone"] == "warn"


def test_what_a_customer_sees_leaves_out_internal_details(db):
    staff, cut = setup(db)
    page = service.public_page(db)
    assert page["services"][0]["staff"] == [{"id": staff[0]["id"], "name": "Ana"}]
    assert "email" not in json.dumps(page["services"])
    booking = service.book(
        db,
        service_id=cut["id"],
        start=at(future_day(), "13:00"),
        customer=CUSTOMER,
        internal_note="Pays late",
        source="public",
    )
    seen = service.public_booking(db, booking)
    assert (
        "internal_note" not in seen
        and "customer_email" not in seen
        and seen["can_change"] is True
    )
    store.update_service(db, cut["id"], {"active": False})
    assert service.public_page(db)["services"] == []


# ---------------------------------------------------------------------------
# the single-user build
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


def test_a_customer_books_reschedules_and_cancels_on_the_local_build(
    env, api, local_index
):
    kit.forget_schema()
    api.post(
        "/api/suite/business",
        {"name": "Glow Studio", "email": "owner@glow.example", "timezone": "UTC"},
    )
    person = api.post(
        "/api/bookings/staff",
        {
            "name": "Ana",
            "email": "ana@glow.example",
            "hours": [
                {"weekday": d, "start": "09:00", "end": "17:00"} for d in range(7)
            ],
        },
        expect=201,
    )["person"]
    cut = api.post(
        "/api/bookings/services",
        {
            "name": "Cut",
            "minutes": 60,
            "price_cents": 4500,
            "deposit_cents": 1000,
            "staff_ids": [person["id"]],
        },
        expect=201,
    )["service"]
    link = api.post("/api/bookings/page-link", {})["link"]
    token = link["token"]

    status, page = anonymous(env, "GET", f"/api/public/{token}")
    assert (
        status == 200
        and page["kind"] == "booking_page"
        and page["page"]["services"][0]["name"] == "Cut"
    )
    assert "ana@glow.example" not in json.dumps(page)
    day = (datetime.now(timezone.utc).date() + timedelta(days=7)).isoformat()
    status, found = anonymous(
        env,
        "POST",
        f"/api/public/{token}/slots",
        {"service_id": cut["id"], "day": day, "days": 1},
    )
    assert status == 200 and found["days"][0]["slots"][0]["local"] == "09:00"
    first, second = found["days"][0]["slots"][0], found["days"][0]["slots"][4]
    status, made = anonymous(
        env,
        "POST",
        f"/api/public/{token}/book",
        {"service_id": cut["id"], "start": first["start"], **CUSTOMER},
    )
    assert status == 201, made
    assert made["booking"]["reference"].startswith("BK-")
    status, again = anonymous(
        env,
        "POST",
        f"/api/public/{token}/book",
        {
            "service_id": cut["id"],
            "start": first["start"],
            "name": "Ola",
            "email": "ola@example.com",
        },
    )
    assert status == 400 and "no longer available" in again["error"]

    manage = made["manage_path"].removeprefix("/p/")
    status, mine = anonymous(env, "GET", f"/api/public/{manage}")
    assert (
        status == 200
        and mine["kind"] == "booking"
        and mine["page"]["booking"]["can_change"] is True
    )
    status, moved = anonymous(
        env, "POST", f"/api/public/{manage}/reschedule", {"start": second["start"]}
    )
    assert status == 200 and moved["booking"]["start_at"] == second["start"]

    listing = api.get("/api/bookings/bookings")["bookings"]
    assert len(listing) == 1 and listing[0]["source"] == "public"
    booking_id = listing[0]["id"]
    paid = api.post(f"/api/bookings/bookings/{booking_id}/pay", {"kind": "deposit"})[
        "booking"
    ]
    assert paid["paid_cents"] == 1000
    home = api.get("/api/suite/home")
    assert home["revenue"]["by_app"]["bookings"] == 1000
    assert any(
        item["public"] and item["app"] == "bookings" for item in home["activity"]
    )
    # Bookings is a section of the Commerce app: the hub lists it inside that app
    bookings_app = next(
        s for app in home["apps"] for s in app["sections"] if s["id"] == "bookings"
    )
    assert (
        bookings_app["installed"]
        and bookings_app["cards"][0]["label"] == "Bookings this week"
    )
    outbox = api.get("/api/suite/outbox?app=bookings")["messages"]
    confirmation = next(m for m in outbox if m["kind"] == "booking_confirmation")
    assert f"{env.base}/p/" in confirmation["body"]

    status, cancelled = anonymous(
        env, "POST", f"/api/public/{manage}/cancel", {"reason": "Travel"}
    )
    assert status == 200 and cancelled["booking"]["status"] == "cancelled"
    assert (
        api.get(f"/api/bookings/bookings/{booking_id}")["booking"]["cancel_reason"]
        == "Travel"
    )
    customers = api.get("/api/bookings/customers")["customers"]
    assert (
        customers[0]["email"] == "maya@example.com"
        and customers[0]["spend_cents"] == 1000
    )
    agenda = api.get(f"/api/bookings/agenda?start={day}&days=1")
    assert agenda["days"][0]["bookings"][0]["status"] == "cancelled"
    assert api.get("/api/bookings/overview")["page_link"]["token"] == token
    api.call("DELETE", "/api/bookings/page-link")
    assert anonymous(env, "GET", f"/api/public/{token}")[0] == 404


# ---------------------------------------------------------------------------
# the hosted build
# ---------------------------------------------------------------------------


def test_hosted_bookings_need_the_app_and_respect_roles(cloud):
    kit.forget_schema()
    org = build_org(cloud)
    owner = sign_in(cloud, org.owner)
    status, data = owner.call("GET", "/api/bookings/overview")
    assert status == 402 and data["code"] == "app_required"
    cloud.billing.subscribe_app(org.owner, "bookings")
    assert owner.call("GET", "/api/bookings/overview")[0] == 200
    owner.call("POST", "/api/suite/business", {"name": "Acme Spa", "timezone": "UTC"})
    status, made = owner.call(
        "POST",
        "/api/bookings/staff",
        {
            "name": "Ana",
            "hours": [
                {"weekday": d, "start": "09:00", "end": "17:00"} for d in range(7)
            ],
        },
    )
    assert status == 201, made
    coach = sign_in(cloud, org.coach)
    status, cut = coach.call(
        "POST",
        "/api/bookings/services",
        {"name": "Massage", "minutes": 60, "price_cents": 8000},
    )
    assert status == 201, cut
    auditor = sign_in(cloud, org.auditor)
    assert auditor.call("GET", "/api/bookings/services")[0] == 200
    assert (
        auditor.call("POST", "/api/bookings/services", {"name": "X", "minutes": 30})[0]
        == 403
    )
    assert sign_in(cloud, org.candidate).call("GET", "/api/bookings/services")[0] == 403

    status, link = owner.call("POST", "/api/bookings/page-link", {})
    assert status == 200
    token = link["link"]["token"]
    visitor = Browser(cloud)
    status, page = visitor.call("GET", f"/api/public/{token}")
    assert status == 200 and page["page"]["services"][0]["name"] == "Massage"
    day = (datetime.now(timezone.utc).date() + timedelta(days=6)).isoformat()
    status, found = visitor.call(
        "POST",
        f"/api/public/{token}/slots",
        {"service_id": cut["service"]["id"], "day": day, "days": 1},
    )
    assert status == 200 and found["days"][0]["slots"]
    start = found["days"][0]["slots"][0]["start"]
    status, made = visitor.call(
        "POST",
        f"/api/public/{token}/book",
        {
            "service_id": cut["service"]["id"],
            "start": start,
            "name": "Ola",
            "email": "ola@example.com",
        },
    )
    assert status == 201, made
    status, listing = owner.call("GET", "/api/bookings/bookings")
    assert (
        status == 200 and listing["bookings"][0]["customer_email"] == "ola@example.com"
    )

    other = build_org(cloud, "Other Spa", slug_email="otherspa.test")
    cloud.billing.subscribe_app(other.owner, "bookings")
    status, theirs = sign_in(cloud, other.owner).call("GET", "/api/bookings/bookings")
    assert status == 200 and theirs["bookings"] == []


def test_two_customers_racing_for_one_slot_get_one_booking(db, tmp_path):
    import threading

    _, cut = setup(db)
    start = at(future_day(), "13:00")
    path = tmp_path / "bookings.sqlite3"
    barrier = threading.Barrier(2)
    outcomes: list[str] = []

    def attempt(name: str) -> None:
        connection = sqlite3.connect(path, timeout=10)
        connection.row_factory = sqlite3.Row
        try:
            barrier.wait()
            service.book(
                connection,
                service_id=cut["id"],
                start=start,
                customer={"name": name, "email": f"{name}@example.com"},
                source="public",
            )
            outcomes.append("booked")
        except ValueError:
            outcomes.append("refused")
        finally:
            connection.close()

    threads = [threading.Thread(target=attempt, args=(name,)) for name in ("ana", "bo")]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert sorted(outcomes) == ["booked", "refused"]
    assert (
        kit.count(db, "SELECT COUNT(*) FROM bk_bookings WHERE start_at = ?", (start,))
        == 1
    )
