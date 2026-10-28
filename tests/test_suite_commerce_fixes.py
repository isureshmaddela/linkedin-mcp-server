"""Commerce (Shop and Bookings): regressions an independent tester found after the waiting list, team pay and share
and earn were added. Each test names the problem it guards against."""

from __future__ import annotations

import json
import sqlite3
import urllib.error
import urllib.request
from datetime import date, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

import pytest

import auto_apply_assistant as local
import test_careers
from aira.suite import kit, public
from aira.suite.bookings import service as bookings
from aira.suite.bookings import store as bstore
from aira.suite.shop import service as shop
from aira.suite.shop import store as sstore

env = test_careers.env
api = test_careers.api
stub = test_careers.stub

UTC = ZoneInfo("UTC")
NY = ZoneInfo("America/New_York")


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
    connection = sqlite3.connect(tmp_path / "commerce.sqlite3")
    connection.row_factory = sqlite3.Row
    bstore.ensure(connection)
    sstore.ensure(connection)
    kit.save_business(
        connection,
        {"name": "Glow Studio", "email": "owner@glow.example", "timezone": "UTC"},
    )
    yield connection
    connection.close()


@pytest.fixture
def local_index():
    public.set_index(local._public_link_index)


def at(day: str, hhmm: str, tz=UTC) -> str:
    hours, minutes = (int(x) for x in hhmm.split(":"))
    return kit.iso(
        datetime.combine(date.fromisoformat(day), time(hours, minutes), tzinfo=tz)
    )


def shift(day: str, days: int) -> str:
    return (date.fromisoformat(day) + timedelta(days=days)).isoformat()


def outbox(db, kind):
    return [m for m in kit.outbox(db) if m["kind"] == kind]


def one_hour_a_day(db, *, commission=None):
    """Ana works 09:00-10:00 every day; Cut takes the whole hour."""
    ana = bstore.create_staff(
        db,
        {
            "name": "Ana",
            "hours": [
                {"weekday": d, "start": "09:00", "end": "10:00"} for d in range(7)
            ],
            **({"commission_percent": commission} if commission is not None else {}),
        },
    )
    cut = bstore.create_service(db, {"name": "Cut", "minutes": 60, "price_cents": 4500})
    return ana, cut


def book(db, service_id, start, name="Maya Lopez", email="maya@example.com", **extra):
    return bookings.book(
        db,
        service_id=service_id,
        start=start,
        customer={"name": name, "email": email, **extra.pop("customer", {})},
        **extra,
    )


# ---------------------------------------------------------------------------
# bookings: a day is "full" only when a cancellation could free a time
# ---------------------------------------------------------------------------


def test_a_day_whose_times_are_over_too_soon_or_too_far_is_not_full(db):
    bstore.save_rules(db, {"max_days_ahead": 4, "min_notice_hours": 2})
    _, cut = one_hour_a_day(db)
    today = datetime.now(UTC).date().isoformat()
    # 08:00: the only time today (09:00) is within the two hours' notice
    now = datetime.combine(date.fromisoformat(today), time(8, 0), tzinfo=UTC)
    book(db, cut["id"], at(shift(today, 2), "09:00"), name="Ola", email="ola@x.com")
    states = {
        d["day"]: d["state"]
        for d in bookings.day_states(db, cut["id"], shift(today, -1), 7, now=now)
    }
    assert states[shift(today, -1)] == "past"
    assert states[today] == "closed"  # nobody is booked: nothing to wait for
    assert states[shift(today, 1)] == "open"
    assert states[shift(today, 2)] == "full"  # Ola has the only time
    assert states[shift(today, 5)] == "not_yet"  # beyond "book up to 4 days ahead"
    with pytest.raises(ValueError, match="No more times"):
        bookings.join_waitlist(
            db,
            service_id=cut["id"],
            day=today,
            customer={"name": "Maya", "email": "maya@example.com"},
            now=now,
        )
    assert bookings.join_waitlist(
        db,
        service_id=cut["id"],
        day=shift(today, 2),
        customer={"name": "Maya", "email": "maya@example.com"},
        now=now,
    )["day"] == shift(today, 2)
    # before 07:00 the same day is still bookable, so it reads open
    early = datetime.combine(date.fromisoformat(today), time(6, 0), tzinfo=UTC)
    assert bookings.day_states(db, cut["id"], today, 1, now=early)[0]["state"] == "open"


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


def test_the_booking_pages_say_why_a_day_has_no_times(env, api, local_index):
    kit.forget_schema()
    api.post("/api/suite/business", {"name": "Glow Studio", "timezone": "UTC"})
    api.post("/api/bookings/settings", {"max_days_ahead": 4})
    api.post(
        "/api/bookings/staff",
        {
            "name": "Ana",
            "hours": [
                {"weekday": d, "start": "09:00", "end": "10:00"} for d in range(7)
            ],
        },
        expect=201,
    )
    cut = api.post(
        "/api/bookings/services",
        {"name": "Cut", "minutes": 60, "price_cents": 4000},
        expect=201,
    )["service"]
    token = api.post("/api/bookings/page-link", {})["link"]["token"]
    today = datetime.now(timezone.utc).date().isoformat()
    status, past = anonymous(
        env,
        "POST",
        f"/api/public/{token}/slots",
        {"service_id": cut["id"], "day": shift(today, -3), "days": 3},
    )
    assert status == 200
    assert [(d["state"], d["open"]) for d in past["days"]] == [("past", False)] * 3
    status, ahead = anonymous(
        env,
        "POST",
        f"/api/public/{token}/slots",
        {"service_id": cut["id"], "day": shift(today, 5), "days": 3},
    )
    assert [d["state"] for d in ahead["days"]] == ["not_yet"] * 3
    assert not any(d["open"] for d in ahead["days"])
    status, refused = anonymous(
        env,
        "POST",
        f"/api/public/{token}/join_waitlist",
        {
            "service_id": cut["id"],
            "day": shift(today, 5),
            "name": "M",
            "email": "m@x.com",
        },
    )
    assert status == 400 and "too far ahead" in refused["error"]

    # the customer's own page (moving a booking) says closed or full, never "full" for a day nobody works
    api.post(
        "/api/bookings/settings", {"max_days_ahead": 60}
    )  # back to the default horizon
    made = api.post(
        "/api/bookings/bookings",
        {
            "service_id": cut["id"],
            "day": shift(today, 3),
            "time": "09:00",
            "name": "Ola",
            "email": "ola@x.com",
        },
        expect=201,
    )["booking"]
    manage = api.post(f"/api/bookings/bookings/{made['id']}/link", {})["link"]["token"]
    status, page = anonymous(env, "GET", f"/api/public/{manage}")
    assert status == 200 and page["page"]["today"] == today
    status, moving = anonymous(
        env, "POST", f"/api/public/{manage}/slots", {"day": shift(today, 1), "days": 3}
    )
    assert status == 200
    assert [d["state"] for d in moving["days"]] == [
        "open",
        "open",
        "open",
    ]  # its own time counts as free


def test_a_cancellation_tells_people_waiting_for_another_service_that_now_fits(db):
    ana = bstore.create_staff(
        db,
        {
            "name": "Ana",
            "hours": [
                {"weekday": d, "start": "09:00", "end": "11:00"} for d in range(7)
            ],
        },
    )
    cut = bstore.create_service(db, {"name": "Cut", "minutes": 60, "price_cents": 4000})
    chat = bstore.create_service(db, {"name": "Chat", "minutes": 30})
    long = bstore.create_service(db, {"name": "Colour", "minutes": 120})
    day = (datetime.now(UTC).date() + timedelta(days=6)).isoformat()
    first = book(db, cut["id"], at(day, "09:00"), name="Ola", email="ola@x.com")
    book(db, cut["id"], at(day, "10:00"), name="Pat", email="pat@x.com")
    for name, wanted in (("Chad", chat), ("Wendy", cut), ("Lou", long)):
        bookings.join_waitlist(
            db,
            service_id=wanted["id"],
            day=day,
            customer={"name": name, "email": f"{name.lower()}@x.com"},
        )
    bookings.cancel(db, first["id"])
    told = {m["recipient"]: m for m in outbox(db, "waitlist_opening")}
    assert set(told) == {"chad@x.com", "wendy@x.com"}  # 120 minutes still does not fit
    assert (
        "Chat" in told["chad@x.com"]["subject"]
        and "09:00" in told["chad@x.com"]["body"]
    )
    rows = {row["name"]: row["status"] for row in bookings.list_waitlist(db)}
    assert rows == {"Chad": "notified", "Wendy": "notified", "Lou": "waiting"}
    assert ana["id"]


def test_team_pay_counts_every_appointment_in_a_busy_year(db):
    ana, cut = one_hour_a_day(db, commission=10)
    first = date(2025, 1, 1)
    stamp = kit.now()
    rows = []
    for index in range(2100):
        start = datetime.combine(
            first + timedelta(days=index % 365), time(9, 0), tzinfo=UTC
        ) + timedelta(minutes=index // 365)
        status = "cancelled" if index % 10 == 0 else "completed"
        rows.append(
            (
                kit.new_id(),
                f"R{index:05d}",
                cut["id"],
                ana["id"],
                "c",
                "Cut",
                "Ana",
                f"Customer {index}",
                kit.iso(start),
                kit.iso(start + timedelta(hours=1)),
                kit.iso(start + timedelta(hours=1)),
                status,
                1000,
                1000 if status == "completed" else 0,
                stamp,
                stamp,
            )
        )
    db.executemany(
        """INSERT INTO bk_bookings (id, reference, service_id, staff_id, customer_id, service_name, staff_name,
        customer_name, start_at, end_at, busy_until, status, price_cents, paid_cents, created_at, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        rows,
    )
    db.commit()
    report = bookings.earnings(db, "2025-01-01", "2025-12-31")
    assert report["totals"]["appointments"] == 1890  # every completed one, none cut off
    assert report["totals"]["takings_cents"] == 1_890_000
    assert report["totals"]["commission_cents"] == 189_000
    assert (
        len(bookings.earnings_csv(db, "2025-01-01", "2025-12-31").decode().splitlines())
        == 1891
    )


def test_a_new_commission_rate_does_not_rewrite_pay_for_earlier_appointments(db):
    ana, cut = one_hour_a_day(db, commission=40)
    past = (datetime.now(UTC).date() - timedelta(days=3)).isoformat()
    for offset, name in ((0, "Ola"), (1, "Pat")):
        done = book(
            db,
            cut["id"],
            at(shift(past, offset), "09:00"),
            staff_id=ana["id"],
            outside_hours=True,  # a past day: entered afterwards
            name=name,
            email=f"{name}@x.com",
        )
        bookings.mark_paid(db, done["id"], kind="full")
        bookings.set_status(db, done["id"], "completed")
    before = bookings.earnings(db, past, shift(past, 1))
    assert before["totals"]["commission_cents"] == 3600  # 40% of 2 x 45.00
    assert (
        bstore.update_staff(db, ana["id"], {"commission_percent": 10})[
            "commission_percent"
        ]
        == 10
    )
    after = bookings.earnings(db, past, shift(past, 1))
    assert after["totals"]["commission_cents"] == 3600
    assert after["team"][0]["rates"] == [40.0]
    # an appointment after the change is paid at the new rate
    later = (datetime.now(UTC).date() + timedelta(days=2)).isoformat()
    new = book(db, cut["id"], at(later, "09:00"), name="Cy", email="cy@x.com")
    bookings.mark_paid(db, new["id"], kind="full")
    bookings.set_status(db, new["id"], "completed")
    report = bookings.earnings(db, past, later)
    assert report["totals"]["commission_cents"] == 3600 + 450
    assert report["team"][0]["rates"] == [40.0, 10.0]
    assert [line["commission_percent"] for line in report["lines"]] == [
        40.0,
        40.0,
        10.0,
    ]
    # setting the same rate again changes nothing
    bstore.update_staff(db, ana["id"], {"commission_percent": "10"})
    assert bookings.earnings(db, past, later)["totals"]["commission_cents"] == 4050


def test_a_rate_first_given_later_covers_earlier_appointments(db):
    ana, cut = one_hour_a_day(
        db, commission=0
    )  # the staff form sends 0 when it is left empty
    past = (datetime.now(UTC).date() - timedelta(days=2)).isoformat()
    done = book(
        db, cut["id"], at(past, "09:00"), staff_id=ana["id"], outside_hours=True
    )
    bookings.mark_paid(db, done["id"], kind="full")
    bookings.set_status(db, done["id"], "completed")
    bstore.update_staff(db, ana["id"], {"commission_percent": 20})
    assert bookings.earnings(db, past, past)["totals"]["commission_cents"] == 900


@pytest.mark.parametrize("value", ["nan", "NaN", "inf", "-inf"])
def test_a_commission_that_is_not_a_number_is_refused_in_plain_words(db, value):
    with pytest.raises(ValueError, match="must be a number"):
        bstore.create_staff(db, {"name": "X", "commission_percent": value})
    assert bstore.list_staff(db) == []


def test_the_booking_limit_counts_a_phone_number_however_it_is_written(db):
    bstore.save_rules(db, {"max_active_per_customer": 2, "min_notice_hours": 0})
    _, cut = one_hour_a_day(db)
    start = datetime.now(UTC).date() + timedelta(days=3)
    for offset in range(2):
        bookings.book(
            db,
            service_id=cut["id"],
            start=at(shift(start.isoformat(), offset), "09:00"),
            customer={"name": "Cal", "phone": "+1 555 0199"},
            source="public",
        )
    with pytest.raises(ValueError, match="most upcoming bookings"):
        bookings.book(
            db,
            service_id=cut["id"],
            start=at(shift(start.isoformat(), 2), "09:00"),
            customer={"name": "Cal", "phone": "+15550199"},
            source="public",
        )
    # the waiting list knows them too
    first = bookings.join_waitlist(
        db,
        service_id=cut["id"],
        day=start.isoformat(),
        customer={"name": "Cal", "phone": "+1 555 0199"},
    )
    again = bookings.join_waitlist(
        db,
        service_id=cut["id"],
        day=start.isoformat(),
        customer={"name": "Cal", "phone": "+1-555-0199"},
    )
    assert again["id"] == first["id"]


def test_the_business_day_reaches_the_admin_forms(env, api):
    kit.forget_schema()
    api.post("/api/suite/business", {"name": "Glow", "timezone": "Pacific/Kiritimati"})
    expected = datetime.now(ZoneInfo("Pacific/Kiritimati")).date().isoformat()
    assert api.get("/api/bookings/staff")["today"] == expected
    assert api.get("/api/bookings/services")["today"] == expected


# ---------------------------------------------------------------------------
# bookings and shop: a child's or a teen's household profile cannot open Commerce
# ---------------------------------------------------------------------------


def as_profile(api, method, path, profile_id, body=None):
    data = json.dumps(body).encode() if body is not None else None
    request = urllib.request.Request(api.base + path, data=data, method=method)
    for name, value in (
        ("Content-Type", "application/json"),
        ("X-Requested-With", "aira"),
        ("Origin", api.base),
        ("X-Aira-Profile", profile_id),
    ):
        request.add_header(name, value)
    try:
        with api.opener.open(request, timeout=60) as response:
            return response.status, json.loads(response.read() or b"{}")
    except urllib.error.HTTPError as error:
        return error.code, json.loads(error.read() or b"{}")


def test_a_child_or_teen_profile_cannot_open_commerce(env, api):
    kit.forget_schema()
    api.post("/api/suite/business", {"name": "Clay Co"})
    me = api.get("/api/life/me")["profile"]
    kid = api.post(
        "/api/life/profiles", {"name": "Kid", "age_group": "child"}, expect=201
    )["profile"]
    status, made = as_profile(  # with two people the browser names who is asking
        api,
        "POST",
        "/api/life/profiles",
        me["id"],
        {"name": "Teen", "age_group": "teen"},
    )
    assert status == 201, made
    teen = made["profile"]
    mug = api.post(
        "/api/shop/products",
        {"name": "Mug", "price_cents": 2000, "status": "active"},
        expect=201,
    )["product"]
    order = api.post(
        "/api/shop/orders",
        {
            "items": [{"product_id": mug["id"]}],
            "name": "Ana",
            "email": "ana@x.com",
            "mark_paid": True,
        },
        expect=201,
    )["order"]
    for young in (kid, teen):
        for method, path, body in (
            ("GET", "/api/shop/customers", None),
            ("GET", "/api/shop/orders", None),
            ("POST", f"/api/shop/orders/{order['id']}/refund", {}),
            ("GET", "/api/bookings/customers", None),
            ("POST", "/api/bookings/staff", {"name": "X", "commission_percent": 90}),
            ("GET", "/api/bookings/earnings", None),
        ):
            status, payload = as_profile(api, method, path, young["id"], body)
            assert status == 403 and payload.get("code") == "adult_only", (
                path,
                payload,
            )
    assert (
        api.get(f"/api/shop/orders/{order['id']}")["order"]["payment_status"] == "paid"
    )
    assert api.get("/api/bookings/staff")["staff"] == []
    status, customers = as_profile(api, "GET", "/api/shop/customers", me["id"])
    assert status == 200 and customers["customers"][0]["email"] == "ana@x.com"


# ---------------------------------------------------------------------------
# shop
# ---------------------------------------------------------------------------


def product(db, **values):
    return shop.create_product(
        db, {"name": "Mug", "price_cents": 1000, "status": "active", **values}
    )


def place(db, items, **values):
    return shop.place_order(
        db, {"items": items, "name": "Ana Silva", "email": "ana@example.com", **values}
    )


def enable_referrals(db, **values):
    return sstore.save_settings(
        db,
        {
            "referrals_enabled": True,
            "referral_friend_percent": 10,
            "referral_reward_cents": 500,
            **values,
        },
    )


def friend_paid(db, mug):
    shop.mark_paid(db, place(db, [{"product_id": mug["id"]}])["id"])
    code = sstore.referrer_by_email(db, "ana@example.com")["code"]
    friend = place(
        db,
        [{"product_id": mug["id"]}],
        discount_code=code,
        name="Bo",
        email="bo@example.com",
    )
    shop.mark_paid(db, friend["id"])
    reward = kit.fetch_one(db, "SELECT reward_code FROM sh_referral_rewards")[
        "reward_code"
    ]
    return friend, reward


def test_mark_unpaid_withdraws_the_reward_and_paying_again_restores_it(db):
    enable_referrals(db)
    mug = product(db)
    friend, reward = friend_paid(db, mug)
    shop.mark_unpaid(db, friend["id"])
    assert sstore.discount_by_code(db, reward)["active"] is False
    with pytest.raises(ValueError, match="not valid"):
        shop.price(db, [{"product_id": mug["id"]}], code=reward)
    assert shop.referrals(db)["totals"]["rewards_cents"] == 0
    shop.mark_paid(db, friend["id"])
    assert (
        sstore.discount_by_code(db, reward)["active"] is True
    )  # the same code, not a second one
    assert kit.count(db, "SELECT COUNT(*) FROM sh_referral_rewards") == 1
    assert len([m for m in kit.outbox(db) if m["kind"] == "referral_reward"]) == 1
    assert shop.referrals(db)["totals"]["rewards_cents"] == 500


def test_a_cancelled_or_refunded_order_cannot_be_marked_paid(db):
    enable_referrals(db)
    mug = product(db, track_stock=True, stock=5)
    cancelled = place(
        db, [{"product_id": mug["id"]}], name="Cy", email="cy@example.com"
    )
    shop.cancel(db, cancelled["id"])
    with pytest.raises(ValueError, match="cancelled"):
        shop.mark_paid(db, cancelled["id"])
    assert sstore.get_order(db, cancelled["id"])["payment_status"] == "unpaid"
    assert kit.revenue(db)["total_cents"] == 0
    assert (
        sstore.referrer_by_email(db, "cy@example.com") is None
    )  # no share code from it
    refunded = place(
        db, [{"product_id": mug["id"]}], name="Rae", email="rae@example.com"
    )
    shop.mark_paid(db, refunded["id"])
    shop.refund(db, refunded["id"])
    with pytest.raises(ValueError, match="refunded"):
        shop.mark_paid(db, refunded["id"])
    assert kit.revenue(db)["total_cents"] == 0


def test_a_shared_link_credits_the_sharer_when_friends_get_no_percent_off(db):
    enable_referrals(db, referral_friend_percent=0, referral_reward_cents=500)
    mug = product(db, price_cents=2000)
    shop.mark_paid(db, place(db, [{"product_id": mug["id"]}])["id"])
    code = sstore.referrer_by_email(db, "ana@example.com")["code"]
    page = shop.storefront(db)
    assert page["referrals"] is True and page["referral_percent"] == 0
    quote = shop.price(db, [{"product_id": mug["id"]}], code=code)
    assert (
        quote["referral"] is True
        and quote["discount_cents"] == 0
        and quote["discount_code"] == code
    )
    friend = place(
        db,
        [{"product_id": mug["id"]}],
        discount_code=code,
        name="Bo",
        email="bo@example.com",
    )
    assert friend["discount_code"] == code and friend["total_cents"] == 2000
    shop.mark_paid(db, friend["id"])
    assert shop.referrals(db)["totals"]["rewards_cents"] == 500


def test_customers_who_paid_before_share_and_earn_get_a_code(db):
    mug = product(db)
    cleo = place(db, [{"product_id": mug["id"]}], name="Cleo", email="cleo@example.com")
    shop.mark_paid(db, cleo["id"])
    enable_referrals(db)
    share = shop.order_referral(db, sstore.get_order(db, cleo["id"]))
    assert share and share["code"].startswith("CLEO-")


def test_turning_on_share_and_earn_gives_everyone_who_paid_a_code(env, api):
    kit.forget_schema()
    api.post("/api/suite/business", {"name": "Clay Co"})
    lamp = api.post(
        "/api/shop/products",
        {"name": "Lamp", "price_cents": 900, "status": "active"},
        expect=201,
    )["product"]
    api.post(
        "/api/shop/orders",
        {
            "items": [{"product_id": lamp["id"]}],
            "name": "Dee",
            "email": "dee@example.com",
            "mark_paid": True,
        },
        expect=201,
    )
    api.post("/api/shop/settings", {"referrals_enabled": True})
    sharers = api.get("/api/shop/referrals")["referrers"]
    assert [row["email"] for row in sharers] == ["dee@example.com"]


def test_deleting_a_product_removes_its_back_in_stock_requests(db):
    lamp = product(db, name="Lamp", track_stock=True, stock=0)
    keep = product(db, name="Vase", track_stock=True, stock=0)
    shop.ask_restock_alert(db, {"product_id": lamp["id"], "email": "lamp@example.com"})
    shop.ask_restock_alert(db, {"product_id": keep["id"], "email": "vase@example.com"})
    assert shop.delete_product(db, lamp["id"])["deleted"] is True
    alerts = shop.restock_alerts(db)
    assert [(a["product_name"], a["email"]) for a in alerts] == [
        ("Vase", "vase@example.com")
    ]
    assert shop.waiting_counts(db) == {keep["id"]: 1}
