"""Aira Events (aira/suite/events): availability, orders that never oversell, promo codes, payments, check-in, the
waitlist, sponsors and messages, through the domain code, the single-user server and the hosted gateway."""

from __future__ import annotations

import json
import sqlite3
import threading
import urllib.error
import urllib.request
from datetime import timedelta

import pytest
from cloud_support import Browser, build_org, sign_in

import auto_apply_assistant as local
import test_careers
from aira.suite import kit, public
from aira.suite.events import service, store

env = test_careers.env
api = test_careers.api
stub = test_careers.stub


def later(**delta) -> str:
    return kit.iso(kit.utcnow() + timedelta(**delta))


class _NoModel:
    def model(self, prompt):
        return None


@pytest.fixture(autouse=True)
def no_live_model():
    """A real Ollama may run on this machine: these tests never call it."""
    from aira import suite

    previous = suite.gateway()
    suite.set_gateway(_NoModel())
    yield
    suite.set_gateway(previous)


@pytest.fixture
def db(tmp_path):
    kit.forget_schema()
    public.set_index(public._MemoryIndex())
    path = tmp_path / "events.sqlite3"
    connection = sqlite3.connect(path, timeout=10)
    connection.row_factory = sqlite3.Row
    store.ensure(connection)
    yield connection
    connection.close()


@pytest.fixture
def db_path(db, tmp_path):
    return tmp_path / "events.sqlite3"


def make_event(db, *, capacity=0, types=None, publish=True, starts_in_days=3):
    event = service.create_event(
        db,
        {
            "title": "Jazz Night",
            "venue_name": "The Blue Room",
            "starts_at": later(days=starts_in_days),
            "capacity": capacity,
        },
    )
    created = []
    for spec in types or [{"name": "General", "price_cents": 2000, "quantity": 10}]:
        created.append(service.add_ticket_type(db, event["id"], spec))
    if publish:
        service.set_status(db, event["id"], "published")
    return store.get_event(db, event["id"]), created


def buy(db, event, ticket, quantity=1, **extra):
    payload = {
        "name": "Ana Buyer",
        "email": "ana@example.com",
        "items": [{"ticket_type_id": ticket["id"], "quantity": quantity}],
        **extra,
    }
    return service.place_order(db, event["id"], payload)


# ---------------------------------------------------------------------------
# domain rules
# ---------------------------------------------------------------------------


def test_events_validate_and_publishing_needs_tickets(db):
    with pytest.raises(ValueError, match="title"):
        service.create_event(db, {"starts_at": later(days=1)})
    with pytest.raises(ValueError, match="end after"):
        service.create_event(
            db, {"title": "X", "starts_at": later(days=2), "ends_at": later(days=1)}
        )
    with pytest.raises(ValueError, match="https"):
        service.create_event(
            db,
            {
                "title": "X",
                "starts_at": later(days=2),
                "cover_url": "http://img.example/a.png",
            },
        )
    event = service.create_event(db, {"title": "Comedy", "starts_at": later(days=2)})
    assert event["status"] == "draft" and event["ends_at"] > event["starts_at"]
    with pytest.raises(ValueError, match="ticket type"):
        service.set_status(db, event["id"], "published")
    service.add_ticket_type(
        db, event["id"], {"name": "GA", "price_cents": 0, "quantity": 5}
    )
    assert service.set_status(db, event["id"], "published")["status"] == "published"
    copy = service.duplicate_event(db, event["id"], days_later=7)
    assert copy["status"] == "draft" and len(store.ticket_types(db, copy["id"])) == 1
    assert kit.parse_time(copy["starts_at"]) - kit.parse_time(
        event["starts_at"]
    ) == timedelta(days=7)
    draft = service.draft_description(event, "Laugh Co")
    assert draft["source"] == "template" and "Comedy" in draft["description"]


def test_availability_honours_quantity_capacity_window_and_limit(db):
    event, (vip, ga) = make_event(
        db,
        capacity=5,
        types=[
            {"name": "VIP", "price_cents": 5000, "quantity": 2, "max_per_order": 2},
            {"name": "GA", "price_cents": 1500, "quantity": 10, "max_per_order": 4},
        ],
    )
    state = service.availability(db, event)
    by_name = {t["name"]: t for t in state["types"]}
    assert (
        by_name["VIP"]["remaining"] == 2 and by_name["GA"]["remaining"] == 5
    )  # overall capacity caps GA
    with pytest.raises(ValueError, match="at most 4"):
        buy(db, event, ga, 5)
    with pytest.raises(ValueError, match="Only 2"):
        buy(db, event, vip, 3)
    buy(db, event, vip, 2)
    buy(db, event, ga, 3)
    state = service.availability(db, event)
    assert state["sold"] == 5 and state["sold_out"] is True and not state["on_sale"]
    with pytest.raises(ValueError, match="(?i)sold out|only 0|seats left"):
        buy(db, event, ga, 1)
    # A sales window that has not opened, and one that has closed.
    early = service.add_ticket_type(
        db,
        event["id"],
        {
            "name": "Later",
            "price_cents": 100,
            "quantity": 5,
            "sales_start": later(days=1),
        },
    )
    reasons = {t["name"]: t["reason"] for t in service.availability(db, event)["types"]}
    assert reasons["Later"] == "Sales have not started"
    service.update_event(db, event["id"], {"capacity": 0})
    with pytest.raises(ValueError, match="not started"):
        buy(db, event, early, 1)
    closed = service.add_ticket_type(
        db,
        event["id"],
        {
            "name": "Closed",
            "price_cents": 100,
            "quantity": 5,
            "sales_end": later(minutes=-1),
        },
    )
    with pytest.raises(ValueError, match="ended"):
        buy(db, event, closed, 1)
    # The organiser may still sell outside the window (at the door).
    assert service.place_order(
        db,
        event["id"],
        {"name": "Door", "items": [{"ticket_type_id": closed["id"], "quantity": 1}]},
        source="manual",
    )
    with pytest.raises(ValueError, match="lower than"):
        service.update_ticket_type(db, vip["id"], {"quantity": 1})


def test_concurrent_buyers_never_get_the_same_last_seat(db, db_path):
    event, (ticket,) = make_event(
        db, types=[{"name": "Last", "price_cents": 0, "quantity": 1}]
    )
    path = db_path
    results: list[str] = []
    barrier = threading.Barrier(4)

    def attempt(index):
        connection = sqlite3.connect(path, timeout=10)
        connection.row_factory = sqlite3.Row
        try:
            barrier.wait()
            service.place_order(
                connection,
                event["id"],
                {
                    "name": f"Buyer {index}",
                    "email": f"b{index}@example.com",
                    "items": [{"ticket_type_id": ticket["id"], "quantity": 1}],
                },
            )
            results.append("ok")
        except ValueError:
            results.append("refused")
        finally:
            connection.close()

    threads = [threading.Thread(target=attempt, args=(i,)) for i in range(4)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert sorted(results) == ["ok", "refused", "refused", "refused"]
    assert store.event_counts(db, event["id"])["sold"] == 1


def test_promo_codes_scope_limit_and_expiry(db):
    event, (vip, ga) = make_event(
        db,
        types=[
            {"name": "VIP", "price_cents": 10000, "quantity": 10},
            {"name": "GA", "price_cents": 2000, "quantity": 10},
        ],
    )
    service.create_promo(db, {"code": "early20", "kind": "percent", "value": 20})
    service.create_promo(
        db,
        {
            "code": "VIPTEN",
            "kind": "amount",
            "amount": "10",
            "ticket_type_id": vip["id"],
            "max_uses": 1,
        },
    )
    service.create_promo(
        db,
        {
            "code": "OLD",
            "kind": "percent",
            "value": 50,
            "expires_at": later(minutes=-5),
        },
    )
    with pytest.raises(ValueError, match="already exists"):
        service.create_promo(db, {"code": "EARLY20", "kind": "percent", "value": 10})
    priced = service.quote(
        db, event, [{"ticket_type_id": ga["id"], "quantity": 2}], "early20"
    )
    assert (
        priced["subtotal_cents"] == 4000
        and priced["discount_cents"] == 800
        and priced["total_cents"] == 3200
    )
    with pytest.raises(ValueError, match="does not apply"):
        service.quote(
            db, event, [{"ticket_type_id": ga["id"], "quantity": 1}], "VIPTEN"
        )
    order = buy(db, event, vip, 1, promo_code="VIPTEN")["order"]
    assert order["total_cents"] == 9000 and order["promo_code"] == "VIPTEN"
    with pytest.raises(ValueError, match="used up"):
        service.quote(
            db, event, [{"ticket_type_id": vip["id"], "quantity": 1}], "VIPTEN"
        )
    with pytest.raises(ValueError, match="expired"):
        service.quote(db, event, [{"ticket_type_id": ga["id"], "quantity": 1}], "OLD")
    with pytest.raises(ValueError, match="not valid"):
        service.quote(db, event, [{"ticket_type_id": ga["id"], "quantity": 1}], "NOPE")
    service.cancel_order(db, order["id"])  # a cancelled order gives the use back
    assert (
        service.quote(
            db, event, [{"ticket_type_id": vip["id"], "quantity": 1}], "VIPTEN"
        )["discount_cents"]
        == 1000
    )


def test_paid_orders_record_revenue_once_and_refunds_free_seats(db):
    kit.save_business(
        db, {"name": "Blue Room", "payment_link": "https://pay.example/blue"}
    )
    event, (ticket,) = make_event(
        db, types=[{"name": "GA", "price_cents": 2500, "quantity": 2}]
    )
    placed = buy(db, event, ticket, 2, holders=["Ana", "Ben"])
    order = placed["order"]
    assert order["status"] == "pending_payment" and order["total_cents"] == 5000
    assert placed["tickets_link"]["path"].startswith("/p/")
    tickets = store.tickets_of_order(db, order["id"])
    assert [t["holder_name"] for t in tickets] == ["Ana", "Ben"] and len(
        {t["code"] for t in tickets}
    ) == 2
    kinds = [m["kind"] for m in kit.outbox(db, app="events")]
    assert kinds == ["payment_request"]
    assert kit.revenue(db)["total_cents"] == 0
    service.mark_paid(db, order["id"])
    service.mark_paid(db, order["id"])
    assert kit.revenue(db)["total_cents"] == 5000 and kit.revenue(db)["payments"] == 1
    kinds = sorted(m["kind"] for m in kit.outbox(db, app="events"))
    assert kinds == ["event_reminder", "payment_request", "ticket"]
    reminder = next(
        m for m in kit.outbox(db, app="events") if m["kind"] == "event_reminder"
    )
    assert reminder["send_after"] == kit.iso(
        kit.parse_time(event["starts_at"]) - timedelta(hours=24)
    )
    assert service.availability(db, event)["sold_out"] is True
    refunded = service.cancel_order(db, order["id"], refund=True)
    assert refunded["status"] == "refunded"
    assert kit.revenue(db)["total_cents"] == 0
    assert service.availability(db, event)["types"][0]["remaining"] == 2
    assert (
        next(m for m in kit.outbox(db, app="events") if m["kind"] == "event_reminder")[
            "status"
        ]
        == "cancelled"
    )
    with pytest.raises(ValueError, match="cancelled"):
        service.mark_paid(db, order["id"])
    unpaid = buy(db, event, ticket, 1)["order"]
    with pytest.raises(ValueError, match="not paid"):
        service.cancel_order(db, unpaid["id"], refund=True)


def test_free_and_complimentary_orders_are_confirmed_and_reminders_wait(db):
    event, (free,) = make_event(
        db, types=[{"name": "Free", "price_cents": 0, "quantity": 5}]
    )
    order = buy(db, event, free, 1)["order"]
    assert order["status"] == "confirmed"
    reminder = next(
        m for m in kit.outbox(db, app="events") if m["kind"] == "event_reminder"
    )
    assert reminder["send_after"] > kit.now()
    # Nothing goes out before its time even when approved.
    kit.save_email_settings(
        db, {"from_email": "me@blue.example", "smtp_host": "smtp.example.com"}
    )
    kit.set_outbox_status(db, [m["id"] for m in kit.outbox(db)], "approved")
    sent = []
    kit.send_outbox(
        db,
        "pw",
        sender=lambda settings, password, message: sent.append(message["Subject"]),
    )
    assert sent == ["Your tickets for Jazz Night"]
    paid_event, (vip,) = make_event(
        db, types=[{"name": "VIP", "price_cents": 9000, "quantity": 3}]
    )
    comp = service.place_order(
        db,
        paid_event["id"],
        {"name": "Press", "items": [{"ticket_type_id": vip["id"], "quantity": 2}]},
        source="manual",
        comp=True,
    )["order"]
    assert (
        comp["status"] == "confirmed"
        and comp["total_cents"] == 0
        and comp["source"] == "comp"
    )
    assert kit.revenue(db)["total_cents"] == 0
    # An event within a day gets no reminder.
    soon, (soon_ticket,) = make_event(
        db, types=[{"name": "Now", "price_cents": 0, "quantity": 5}], starts_in_days=0.5
    )
    soon_order = buy(db, soon, soon_ticket, 1)["order"]
    assert not [
        m
        for m in kit.outbox(db, app="events")
        if m["kind"] == "event_reminder" and m["ref_id"] == soon_order["id"]
    ]


def test_check_in_never_twice_and_refuses_unpaid_or_cancelled(db):
    event, (free, paid) = make_event(
        db,
        types=[
            {"name": "Free", "price_cents": 0, "quantity": 5},
            {"name": "Paid", "price_cents": 1000, "quantity": 5},
        ],
    )
    order = buy(db, event, free, 1)["order"]
    code = store.tickets_of_order(db, order["id"])[0]["code"]
    assert service.check_in(db, event["id"], "nope")["result"] == "invalid"
    first = service.check_in(db, event["id"], code.lower().replace("-", ""))
    assert first["result"] == "checked_in"
    again = service.check_in(db, event["id"], code)
    assert (
        again["result"] == "already"
        and again["checked_in_at"] == first["checked_in_at"]
    )
    unpaid = buy(db, event, paid, 1)["order"]
    assert (
        service.check_in(
            db, event["id"], store.tickets_of_order(db, unpaid["id"])[0]["code"]
        )["result"]
        == "unpaid"
    )
    service.cancel_order(db, unpaid["id"])
    assert (
        service.check_in(
            db, event["id"], store.tickets_of_order(db, unpaid["id"])[0]["code"]
        )["result"]
        == "cancelled"
    )
    counts = store.event_counts(db, event["id"])
    assert counts["checked_in"] == 1 and counts["sold"] == 1
    assert [a["code"] for a in service.find_attendees(db, event["id"], "Ana")] == [code]
    ticket_id = store.tickets_of_order(db, order["id"])[0]["id"]
    assert service.undo_check_in(db, ticket_id)["status"] == "valid"


def test_waitlist_announcements_and_sponsors(db):
    event, (ticket,) = make_event(
        db, types=[{"name": "GA", "price_cents": 0, "quantity": 1}]
    )
    buy(db, event, ticket, 1)
    assert service.public_event(db, event)["waitlist_open"] is True
    entry = service.join_waitlist(
        db, event, {"name": "Cam", "email": "cam@example.com", "quantity": 2}
    )
    assert (
        service.join_waitlist(db, event, {"name": "Cam", "email": "CAM@example.com"})[
            "id"
        ]
        == entry["id"]
    )
    offered = service.offer_waitlist(
        db, entry["id"], via={"base_url": "http://shop.example"}
    )
    assert offered["status"] == "offered"
    offer = next(
        m for m in kit.outbox(db, app="events") if m["kind"] == "waitlist_offer"
    )
    assert "http://shop.example/p/" in offer["body"]
    assert (
        service.announce(
            db, event["id"], subject="Doors at 7", body="Doors open at 7pm."
        )
        == 1
    )
    result = service.cancel_event(
        db, event["id"], via={"base_url": "http://shop.example"}
    )
    assert result["notified"] == 1 and result["event"]["status"] == "cancelled"
    with pytest.raises(ValueError):
        service.set_status(db, event["id"], "published")
    sponsor = service.save_sponsor(
        db, {"name": "Brewery", "tier": "Gold", "amount": "500", "status": "agreed"}
    )
    assert kit.revenue(db)["total_cents"] == 0
    service.save_sponsor(db, {"status": "paid"}, sponsor["id"])
    assert kit.revenue(db)["by_app"] == {"events": 50000}
    service.save_sponsor(db, {"status": "agreed"}, sponsor["id"])
    assert kit.revenue(db)["total_cents"] == 0


def test_public_views_never_show_other_attendees(db):
    event, (ticket,) = make_event(
        db, types=[{"name": "GA", "price_cents": 0, "quantity": 500}]
    )
    order = buy(db, event, ticket, 1)["order"]
    page = service.public_event(db, event)
    text = json.dumps(page)
    assert (
        "ana@example.com" not in text
        and "Ana Buyer" not in text
        and "notes" not in page["event"]
    )
    assert page["types"][0]["remaining"] is None  # plenty left: no exact count
    mine = service.public_order(db, order)
    assert mine["order"]["buyer_name"] == "Ana Buyer" and len(mine["tickets"]) == 1
    assert "buyer_email" not in mine["order"]


# ---------------------------------------------------------------------------
# the single-user build
# ---------------------------------------------------------------------------


@pytest.fixture
def local_index():
    public.set_index(local._public_link_index)


def _anonymous(env, method, path, body=None):
    data = json.dumps(body).encode() if body is not None else None
    request = urllib.request.Request(env.base + path, data=data, method=method)
    request.add_header("Content-Type", "application/json")
    request.add_header("X-Requested-With", "aira")
    request.add_header("Origin", env.base)
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            raw = response.read()
            return response.status, (
                json.loads(raw) if raw[:1] in (b"{", b"[") else raw
            )
    except urllib.error.HTTPError as error:
        raw = error.read()
        return error.code, (json.loads(raw) if raw[:1] in (b"{", b"[") else raw)


def test_local_journey_from_event_to_door(env, api, local_index):
    kit.forget_schema()
    api.post(
        "/api/suite/business",
        {"name": "Blue Room", "payment_link": "https://pay.example/blue"},
    )
    created = api.post(
        "/api/events/events",
        {
            "title": "Jazz Night",
            "venue_name": "The Blue Room",
            "starts_at": later(days=5),
            "ticket_types": [
                {"name": "Free entry", "price_cents": 0, "quantity": 3},
                {"name": "Table", "price_cents": 4000, "quantity": 2},
            ],
        },
        expect=201,
    )["event"]
    assert created["status"] == "draft" and len(created["ticket_types"]) == 2
    published = api.post(
        f"/api/events/events/{created['id']}/status", {"status": "published"}
    )["event"]
    link = published["link"]
    assert link and published["phase"] == "published"
    token = link["token"]

    status, page = _anonymous(env, "GET", f"/api/public/{token}")
    assert (
        status == 200
        and page["page"]["event"]["title"] == "Jazz Night"
        and page["actions"] == ["join_waitlist", "order", "quote"]
    )
    free_type, table_type = (t["id"] for t in page["page"]["types"])
    status, data = _anonymous(
        env,
        "POST",
        f"/api/public/{token}/order",
        {
            "name": "Ana",
            "email": "ana@example.com",
            "items": [{"ticket_type_id": free_type, "quantity": 1}],
        },
    )
    assert status == 201 and data["order"]["status"] == "confirmed"
    code = data["tickets"][0]["code"]
    tickets_token = data["tickets_path"].rsplit("/", 1)[1]
    status, mine = _anonymous(env, "GET", f"/api/public/{tickets_token}")
    assert status == 200 and mine["page"]["tickets"][0]["code"] == code

    status, data = _anonymous(
        env,
        "POST",
        f"/api/public/{token}/order",
        {
            "name": "Ben",
            "email": "ben@example.com",
            "items": [{"ticket_type_id": table_type, "quantity": 2}],
        },
    )
    assert (
        status == 201
        and data["order"]["status"] == "pending_payment"
        and data["order"]["total_cents"] == 8000
    )
    paid_token = data["tickets_path"].rsplit("/", 1)[1]
    assert (
        _anonymous(env, "POST", f"/api/public/{paid_token}/notify_paid", {})[1][
            "order"
        ]["payment_claimed"]
        is True
    )
    status, error = _anonymous(
        env,
        "POST",
        f"/api/public/{token}/order",
        {
            "name": "Cy",
            "email": "cy@example.com",
            "items": [{"ticket_type_id": table_type, "quantity": 1}],
        },
    )
    assert status == 400 and "sold out" in error["error"].lower()
    assert (
        _anonymous(
            env,
            "POST",
            f"/api/public/{token}/order",
            {"items": [{"ticket_type_id": free_type, "quantity": 1}]},
        )[0]
        == 400
    )

    orders = api.get("/api/events/orders")["orders"]
    pending = next(o for o in orders if o["status"] == "pending_payment")
    api.post(f"/api/events/orders/{pending['id']}/paid", {})
    assert api.get("/api/suite/home")["revenue"]["total_cents"] == 8000

    result = api.post(f"/api/events/events/{created['id']}/checkin", {"code": code})
    assert result["result"] == "checked_in" and result["counts"]["checked_in"] == 1
    assert (
        api.post(f"/api/events/events/{created['id']}/checkin", {"code": code})[
            "result"
        ]
        == "already"
    )
    roster = api.get(f"/api/events/events/{created['id']}/checkin?q=Ben")
    assert len(roster["attendees"]) == 2

    outbox = api.get("/api/suite/outbox?app=events")["messages"]
    ticket_mail = next(
        m
        for m in outbox
        if m["kind"] == "ticket" and m["recipient"] == "ana@example.com"
    )
    assert f"{env.base}/p/" in ticket_mail["body"] and code in ticket_mail["body"]
    overview = api.get("/api/events/overview")
    assert overview["cards"][0]["value"] == 3
    steps = {s["id"]: s["done"] for s in overview["checklist"]}
    assert steps == {
        "profile": True,
        "event": True,
        "tickets": True,
        "publish": True,
        "order": True,
        "checkin": True,
    }
    home = api.get("/api/suite/home")
    events = next(
        s for app in home["apps"] for s in app["sections"] if s["id"] == "events"
    )  # a section of the Courses & Events app
    assert events["progress"] == {
        "done": 6,
        "total": 6,
    }


def test_local_csv_export(env, api, local_index):
    kit.forget_schema()
    event = api.post(
        "/api/events/events",
        {
            "title": "=cmd Show",
            "starts_at": later(days=2),
            "ticket_types": [{"name": "GA", "price_cents": 0, "quantity": 5}],
        },
        expect=201,
    )["event"]
    api.post(
        "/api/events/orders",
        {
            "event_id": event["id"],
            "name": "=Mallory",
            "items": [
                {"ticket_type_id": event["ticket_types"][0]["id"], "quantity": 1}
            ],
        },
        expect=201,
    )
    request = urllib.request.Request(
        f"{env.base}/api/events/events/{event['id']}/attendees.csv"
    )
    opener = api.opener
    request.add_header("X-Requested-With", "aira")
    with opener.open(request, timeout=30) as response:
        body = response.read().decode()
    assert "holder_name" in body and "'=Mallory" in body


# ---------------------------------------------------------------------------
# the hosted build
# ---------------------------------------------------------------------------


def test_hosted_events_need_the_app_and_respect_roles(cloud):
    kit.forget_schema()
    org = build_org(cloud)
    owner = sign_in(cloud, org.owner)
    status, data = owner.call("GET", "/api/events/overview")
    assert status == 402 and data["code"] == "app_required"
    cloud.billing.subscribe_app(org.owner, "events")
    status, data = owner.call(
        "POST",
        "/api/events/events",
        {
            "title": "Launch Party",
            "starts_at": later(days=4),
            "ticket_types": [{"name": "GA", "price_cents": 1500, "quantity": 20}],
        },
    )
    assert status == 201, data
    event = data["event"]
    coach = sign_in(cloud, org.coach)
    assert (
        coach.call(
            "POST",
            f"/api/events/events/{event['id']}/ticket-types",
            {"name": "VIP", "price_cents": 5000, "quantity": 5},
        )[0]
        == 201
    )
    auditor = sign_in(cloud, org.auditor)
    assert auditor.call("GET", "/api/events/events")[0] == 200
    assert (
        auditor.call(
            "POST", "/api/events/events", {"title": "No", "starts_at": later(days=1)}
        )[0]
        == 403
    )
    assert sign_in(cloud, org.candidate).call("GET", "/api/events/events")[0] == 403
    status, data = owner.call(
        "POST", f"/api/events/events/{event['id']}/status", {"status": "published"}
    )
    assert status == 200
    token = data["event"]["link"]["token"]

    visitor = Browser(cloud)
    status, page = visitor.call("GET", f"/api/public/{token}")
    assert status == 200 and page["page"]["event"]["title"] == "Launch Party"
    ga = next(t for t in page["page"]["types"] if t["name"] == "GA")
    status, placed = visitor.call(
        "POST",
        f"/api/public/{token}/order",
        {
            "name": "Dee",
            "email": "dee@example.com",
            "items": [{"ticket_type_id": ga["id"], "quantity": 2}],
        },
    )
    assert status == 201 and placed["order"]["total_cents"] == 3000
    orders = owner.call("GET", "/api/events/orders")[1]["orders"]
    assert (
        owner.call("POST", f"/api/events/orders/{orders[0]['id']}/paid", {})[0] == 200
    )
    assert owner.call("GET", "/api/suite/home")[1]["revenue"]["total_cents"] == 3000

    other = build_org(cloud, "Other Org", slug_email="other.test", apps=["events"])
    status, listing = sign_in(cloud, other.owner).call("GET", "/api/events/events")
    assert status == 200 and listing["events"] == []
