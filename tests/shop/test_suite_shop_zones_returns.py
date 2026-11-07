"""Shop delivery zones (country, state and PIN rules; flat, weight-band and free-over rates; the old flat fee as
the default zone), returns (request, approve or reject, receive, refund by hand or through Razorpay, stock back,
emails) and tracking links per shipment."""

from __future__ import annotations

import json
import sqlite3
import urllib.request

import pytest
from razorpay_payments_stub import KEY_ID, KEY_SECRET, WEBHOOK_SECRET, RazorpayStub
from test_payments_razorpay import Secrets, deliver

import auto_apply_assistant as local
import test_careers
from aira.payments import apps as _apps  # noqa: F401
from aira.payments import razorpay
from aira.payments import service as payments
from aira.suite import kit, public
from aira.suite.shop import delivery, returns, service, store

env = test_careers.env
api = test_careers.api
stub = test_careers.stub


class _NoModel:
    def model(self, prompt):
        return None


@pytest.fixture(autouse=True)
def no_model(monkeypatch):
    from aira import suite

    monkeypatch.setattr(suite, "_gateway", _NoModel())


@pytest.fixture(autouse=True)
def quick_forms(monkeypatch):
    monkeypatch.setattr(service, "MIN_FILL_SECONDS", 0)


@pytest.fixture
def db(tmp_path):
    kit.forget_schema()
    public.set_index(public._MemoryIndex())
    connection = sqlite3.connect(tmp_path / "shop.sqlite3")
    connection.row_factory = sqlite3.Row
    store.ensure(connection)
    kit.save_business(
        connection, {"name": "Chai Co", "currency": "INR", "email": "hi@chai.example"}
    )
    yield connection
    connection.close()
    public.reset_index()


def tea(db, **values):
    return service.create_product(
        db,
        {
            "name": "Tea",
            "price_cents": 20000,
            "status": "active",
            "track_stock": True,
            "stock": 10,
            "weight_grams": 250,
            **values,
        },
    )


def quote(db, product, quantity=1, **address):
    return service.price(
        db,
        [{"product_id": product["id"], "quantity": quantity}],
        delivery_method="delivery",
        address=address,
        strict_delivery=False,
    )


def outbox(db, kind):
    return [m for m in kit.outbox(db, app="shop") if m["kind"] == kind]


# ---------------------------------------------------------------------------
# delivery zones
# ---------------------------------------------------------------------------


def test_the_old_flat_fee_is_the_default_zone(db):
    store.save_settings(
        db, {"delivery_fee_cents": 5000, "free_delivery_over_cents": 100000}
    )
    product = tea(db)
    one = quote(db, product)
    assert (
        one["delivery_cents"] == 5000
        and one["delivery_zone"] == ""
        and not one["delivery_problem"]
    )
    assert quote(db, product, 5)["delivery_cents"] == 0  # free over 1,000.00
    assert (
        service.price(db, [{"product_id": product["id"]}], delivery_method="pickup")[
            "delivery_cents"
        ]
        == 0
    )


def test_the_most_specific_zone_prices_delivery(db):
    store.save_settings(db, {"delivery_fee_cents": 9900})
    product = tea(db)
    delivery.create_zone(
        db,
        {"name": "India", "countries": "India", "rate_type": "flat", "fee_cents": 8000},
    )
    delivery.create_zone(
        db,
        {
            "name": "Telangana",
            "countries": ["IN"],
            "regions": ["Telangana"],
            "rate_type": "flat",
            "fee_cents": 6000,
        },
    )
    delivery.create_zone(
        db,
        {
            "name": "Hyderabad",
            "postcodes": "500, 501",
            "rate_type": "free_over",
            "fee_cents": 3000,
            "free_over_cents": 50000,
        },
    )
    delivery.create_zone(
        db,
        {
            "name": "Vizag range",
            "postcodes": ["530001-530049"],
            "rate_type": "flat",
            "fee_cents": 4500,
        },
    )
    assert (
        quote(db, product, country="IN", region="Kerala", postcode="682001")[
            "delivery_zone"
        ]
        == "India"
    )
    near = quote(
        db, product, country="india", region="36", postcode="506001"
    )  # the GST state code is Telangana
    assert near["delivery_zone"] == "Telangana" and near["delivery_cents"] == 6000
    city = quote(db, product, country="IN", region="Telangana", postcode="500 081")
    assert city["delivery_zone"] == "Hyderabad" and city["delivery_cents"] == 3000
    assert (
        quote(db, product, 3, country="IN", postcode="500081")["delivery_cents"] == 0
    )  # free over 500.00
    assert (
        quote(db, product, country="IN", postcode="530020")["delivery_zone"]
        == "Vizag range"
    )
    assert (
        quote(db, product, country="IN", postcode="530050")["delivery_zone"] == "India"
    )
    abroad = quote(db, product, country="Nepal", postcode="44600")
    assert (
        abroad["delivery_zone"] == "" and abroad["delivery_cents"] == 9900
    )  # everywhere else: the default zone


def test_weight_bands_and_where_the_shop_does_not_deliver(db):
    store.save_settings(db, {"deliver_elsewhere": False, "default_country": "India"})
    assert store.settings(db)["default_country"] == "IN"
    product = tea(db)  # 250 g each
    delivery.create_zone(
        db,
        {
            "name": "South India",
            "countries": ["IN"],
            "regions": ["Karnataka", "Tamil Nadu"],
            "rate_type": "weight",
            "bands": "500 | 40.00\n2000 | 90.00",
        },
    )
    light = quote(db, product, 2, region="Karnataka")  # 500 g, the default country
    assert light["weight_grams"] == 500 and light["delivery_cents"] == 4000
    assert quote(db, product, 3, region="Tamil Nadu")["delivery_cents"] == 9000
    heavy = quote(db, product, 9, region="Karnataka")
    assert (
        "more than the shop delivers" in heavy["delivery_problem"]
        and "2 kg" in heavy["delivery_problem"]
    )
    assert (
        quote(db, product)["delivery_problem"]
        == "Enter your state to see if the shop delivers there."
    )
    away = quote(db, product, region="Punjab", postcode="141001")
    assert away["delivery_problem"].startswith(
        "Sorry, the shop does not deliver to 141001, Punjab, IN"
    )
    with pytest.raises(ValueError, match="does not deliver"):
        service.place_order(
            db,
            {
                "items": [{"product_id": product["id"]}],
                "name": "Ana",
                "email": "ana@example.com",
                "delivery_method": "delivery",
                "address": "1 MG Road",
                "region": "Punjab",
                "postcode": "141001",
            },
        )
    made = service.place_order(
        db,
        {
            "items": [{"product_id": product["id"], "quantity": 2}],
            "name": "Ana",
            "email": "ana@example.com",
            "delivery_method": "delivery",
            "address": "1 MG Road, Bengaluru",
            "region": "Karnataka",
            "postcode": "560001",
        },
    )
    assert made["delivery_cents"] == 4000 and made["delivery_zone"] == "South India"
    assert (
        made["weight_grams"] == 500
        and made["ship_postcode"] == "560001"
        and made["ship_country"] == "IN"
    )
    assert made["total_cents"] == 44000


def test_zones_are_checked(db):
    with pytest.raises(ValueError, match="Say where"):
        delivery.create_zone(db, {"name": "Nowhere", "rate_type": "flat"})
    with pytest.raises(ValueError, match="weight band"):
        delivery.create_zone(
            db, {"name": "W", "countries": ["IN"], "rate_type": "weight"}
        )
    with pytest.raises(ValueError, match="free from"):
        delivery.create_zone(
            db,
            {
                "name": "F",
                "countries": ["IN"],
                "rate_type": "free_over",
                "fee_cents": 100,
            },
        )
    with pytest.raises(ValueError, match="same length"):
        delivery.create_zone(db, {"name": "R", "postcodes": ["5300-530049"]})
    zone = delivery.create_zone(
        db,
        {
            "name": "Flat",
            "countries": ["IN"],
            "rate_type": "flat",
            "fee_cents": 100,
            "free_over_cents": 999,
        },
    )
    assert zone["free_over_cents"] == 0  # a flat rate keeps only its fee
    changed = delivery.update_zone(db, zone["id"], {"active": False})
    assert not changed["active"] and delivery.list_zones(db, active_only=True) == []
    delivery.delete_zone(db, zone["id"])
    assert delivery.list_zones(db) == []


# ---------------------------------------------------------------------------
# tracking links
# ---------------------------------------------------------------------------


def test_a_shipment_with_a_carrier_and_number_gets_its_tracking_link_in_the_email(db):
    product = tea(db)
    order = service.place_order(
        db,
        {
            "items": [{"product_id": product["id"]}],
            "name": "Ana Rao",
            "email": "ana@example.com",
        },
    )
    service.mark_paid(db, order["id"])
    shipped = service.set_fulfilment(
        db, order["id"], "shipped", carrier="delhivery", tracking_number="DL 1234 5678"
    )
    assert (
        shipped["carrier"] == "delhivery"
        and shipped["tracking_number"] == "DL 1234 5678"
    )
    assert (
        shipped["tracking_url"] == "https://www.delhivery.com/track/package/DL12345678"
    )
    mail = outbox(db, "order_shipped")[0]
    assert "Sent with Delhivery, tracking number DL 1234 5678." in mail["body"]
    assert (
        "Track it: https://www.delhivery.com/track/package/DL12345678" in mail["body"]
    )
    assert (
        service.public_order(store.get_order(db, order["id"]))["carrier"] == "Delhivery"
    )
    # the shop's own carrier page
    store.save_settings(
        db, {"custom_tracking_url": "https://track.example.in/?awb={number}"}
    )
    other = service.update_order(
        db, order["id"], {"carrier": "custom", "tracking_number": "AB/12"}
    )
    assert other["tracking_url"] == "https://track.example.in/?awb=AB%2F12"
    for carrier in ("india_post", "bluedart", "dtdc"):
        assert delivery.tracking_link({}, carrier, "X1").startswith("https://")
    with pytest.raises(ValueError, match="carrier"):
        service.set_fulfilment(db, order["id"], "delivered", carrier="pigeon")
    with pytest.raises(ValueError, match="https://"):
        store.save_settings(
            db, {"custom_tracking_url": "http://track.example.in/{number}"}
        )
    with pytest.raises(ValueError, match="number"):
        store.save_settings(db, {"custom_tracking_url": "https://track.example.in/"})


# ---------------------------------------------------------------------------
# returns
# ---------------------------------------------------------------------------


def delivered_order(db, quantity=3, **raw):
    product = tea(db)
    order = service.place_order(
        db,
        {
            "items": [{"product_id": product["id"], "quantity": quantity}],
            "name": "Ana Rao",
            "email": "ana@example.com",
            **raw,
        },
    )
    service.order_link(db, order)
    service.mark_paid(db, order["id"])
    service.set_fulfilment(db, order["id"], "shipped")
    service.set_fulfilment(db, order["id"], "delivered")
    return product, store.get_order(db, order["id"])


def test_a_return_by_hand_from_request_to_refund_puts_the_stock_back(db):
    product, order = delivered_order(db)
    assert store.get_product(db, product["id"])["stock"] == 7
    page = returns.public_view(db, order)
    assert page["open"] and page["lines"] == [
        {"line": 0, "name": "Tea", "option": "", "returnable": 3}
    ]
    with pytest.raises(ValueError, match="at most 3"):
        returns.request(
            db,
            order["id"],
            {"items": [{"line": 0, "quantity": 4}], "reason": "damaged"},
        )
    with pytest.raises(ValueError, match="why"):
        returns.request(
            db, order["id"], {"items": [{"line": 0, "quantity": 1}], "reason": "other"}
        )
    made = returns.request(
        db,
        order["id"],
        {
            "items": [{"line": 0, "quantity": 2}],
            "reason": "damaged",
            "details": "Box crushed",
        },
    )
    assert made["status"] == "requested" and made["value_cents"] == 40000
    assert (
        returns.public_view(db, order)["lines"][0]["returnable"] == 1
    )  # one left to return
    customer_mail = outbox(db, "return_update")[-1]
    assert (
        customer_mail["recipient"] == "ana@example.com"
        and "R-0001" in customer_mail["subject"]
    )
    assert [m["recipient"] for m in outbox(db, "return_requested_notice")] == [
        "hi@chai.example"
    ]

    store.save_settings(
        db, {"return_instructions": "Drop it at our Banjara Hills counter."}
    )
    approved = returns.decide(db, made["id"], True, note="Sorry about that!")
    assert approved["status"] == "approved"
    assert "Banjara Hills" in outbox(db, "return_update")[-1]["body"]
    with pytest.raises(ValueError, match="already approved"):
        returns.decide(db, made["id"], False, note="no")

    done = returns.receive(db, made["id"])
    assert (
        done["status"] == "refunded"
        and done["refund_cents"] == 40000
        and done["restocked"]
    )
    assert store.get_product(db, product["id"])["stock"] == 9
    after = store.get_order(db, order["id"])
    assert (
        after["refunded_cents"] == 40000 and after["payment_status"] == "paid"
    )  # partly refunded
    lines = kit.revenue_lines(db)
    assert any(
        line["kind"] == "refund" and line["amount_cents"] == -40000 for line in lines
    )
    assert "refunded ₹400.00" in outbox(db, "return_update")[-1]["body"]

    # the rest of the order refunded later: only the item that was not returned goes back into stock
    service.refund(db, order["id"])
    assert store.get_order(db, order["id"])["payment_status"] == "refunded"
    assert store.get_product(db, product["id"])["stock"] == 10


def test_a_rejected_return_frees_the_items_and_the_window_closes(db):
    _, order = delivered_order(db, quantity=1)
    made = returns.request(
        db,
        order["id"],
        {"items": [{"line": 0, "quantity": 1}], "reason": "changed_mind"},
    )
    with pytest.raises(ValueError, match="already being returned"):
        returns.request(
            db,
            order["id"],
            {"items": [{"line": 0, "quantity": 1}], "reason": "changed_mind"},
        )
    with pytest.raises(ValueError, match="why"):
        returns.decide(db, made["id"], False)
    rejected = returns.decide(
        db, made["id"], False, note="Opened tea cannot be returned."
    )
    assert rejected["status"] == "rejected"
    assert "Opened tea" in outbox(db, "return_update")[-1]["body"]
    assert returns.public_view(db, order)["lines"][0]["returnable"] == 1
    store.save_settings(db, {"return_window_days": 0})
    assert returns.public_view(db, order)["reason"].startswith(
        "This shop does not take returns"
    )
    store.save_settings(db, {"return_window_days": 7})
    db.execute(
        "UPDATE sh_orders SET history = ? WHERE id = ?",
        (
            json.dumps([{"status": "delivered", "at": "2020-01-01T00:00:00Z"}]),
            order["id"],
        ),
    )
    db.commit()
    with pytest.raises(ValueError, match="window has closed"):
        returns.request(
            db,
            store.get_order(db, order["id"])["id"],
            {"items": [{"line": 0, "quantity": 1}], "reason": "damaged"},
        )


def test_returns_need_a_paid_order_that_went_out(db):
    product = tea(db)
    order = service.place_order(
        db,
        {
            "items": [{"product_id": product["id"]}],
            "name": "Ana",
            "email": "ana@example.com",
        },
    )
    with pytest.raises(ValueError, match="once the order is paid"):
        returns.request(
            db,
            order["id"],
            {"items": [{"line": 0, "quantity": 1}], "reason": "damaged"},
        )
    service.mark_paid(db, order["id"])
    with pytest.raises(ValueError, match="sent to you"):
        returns.request(
            db,
            order["id"],
            {"items": [{"line": 0, "quantity": 1}], "reason": "damaged"},
        )


@pytest.fixture
def rzp(db, monkeypatch):
    server = RazorpayStub().start()
    monkeypatch.setenv(razorpay.API_BASE_ENV, server.base)
    secrets = Secrets()
    secrets.put(db, kit.RAZORPAY_KEY_SECRET, KEY_SECRET)
    secrets.put(db, kit.RAZORPAY_WEBHOOK_SECRET, WEBHOOK_SECRET)
    kit.save_payment_settings(
        db,
        {
            "provider": "razorpay",
            "razorpay_key_id": KEY_ID,
            "key_secret_saved": True,
            "webhook_secret_saved": True,
        },
    )
    with payments.secrets(secrets):
        yield server, secrets
    server.stop()


def test_a_return_of_an_order_paid_online_is_refunded_through_razorpay(db, rzp):
    server, secrets = rzp
    product = tea(db)
    order = service.place_order(
        db,
        {
            "items": [{"product_id": product["id"], "quantity": 2}],
            "name": "Ana Rao",
            "email": "ana@example.com",
        },
    )
    made = payments.create_request(db, "shop", "order", order["id"])
    payment = server.pay(made["provider_ref"])
    deliver(
        db,
        secrets,
        *server.webhook(
            "payment_link.paid", link_id=made["provider_ref"], payment=payment
        ),
    )
    service.set_fulfilment(
        db, order["id"], "shipped", carrier="bluedart", tracking_number="BD123"
    )
    entry = returns.request(
        db, order["id"], {"items": [{"line": 0, "quantity": 1}], "reason": "wrong_item"}
    )
    returns.decide(db, entry["id"], True)
    done = returns.receive(db, entry["id"], secret_store=secrets)
    assert done["status"] == "refunded" and done["refund_cents"] == 20000
    sent = server.calls_to("POST", f"/payments/{payment['id']}/refund")[0]["body"]
    assert sent["amount"] == 20000 and "Return R-0001" in sent["notes"]["note"]
    assert store.get_product(db, product["id"])["stock"] == 9
    assert store.get_order(db, order["id"])["refunded_cents"] == 20000
    assert kit.refunded_cents(db, "shop", "order", order["id"]) == 20000


def test_a_refund_that_fails_leaves_the_return_received_to_try_again(db, monkeypatch):
    _, order = delivered_order(db, quantity=1)
    entry = returns.request(
        db, order["id"], {"items": [{"line": 0, "quantity": 1}], "reason": "damaged"}
    )

    def refuse(*args, **kwargs):
        raise ValueError("Razorpay is not answering.")

    monkeypatch.setattr(service, "refund", refuse)
    with pytest.raises(ValueError, match="refund did not go through"):
        returns.receive(db, entry["id"])
    kept = returns.get(db, entry["id"])
    assert (
        kept["status"] == "received"
        and "not answering" in kept["refund_error"]
        and kept["restocked"]
    )
    monkeypatch.undo()
    monkeypatch.setattr(service, "MIN_FILL_SECONDS", 0)
    assert returns.refund(db, entry["id"])["status"] == "refunded"


def test_the_new_routes_have_hosted_rules():
    from monosage_cloud.policy import POLICY

    reads = ("shop_routes__zones_list", "shop_routes__returns_list")
    manages = (
        "shop_routes__zones_create",
        "shop_routes__zones_update",
        "shop_routes__zones_delete",
        "shop_routes__returns_approve",
        "shop_routes__returns_reject",
        "shop_routes__returns_receive",
        "shop_routes__returns_refund",
        "bookings_routes__staff_calendar_set",
        "bookings_routes__staff_calendar_sync",
        "bookings_routes__staff_calendar_remove",
    )
    for name in reads:
        assert POLICY[f"airaapi__{name}"].permission == "suite.read", name
    for name in manages:
        assert POLICY[f"airaapi__{name}"].permission == "suite.manage", name
    assert (
        POLICY["airaapi__bookings_routes__booking_calendar_file"].scope == "public_link"
    )


# ---------------------------------------------------------------------------
# the single-user server: zones, the storefront, the order page and the routes
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


def test_zones_returns_and_tracking_through_the_routes(env, api, local_index):
    kit.forget_schema()
    api.post(
        "/api/suite/business",
        {"name": "Chai Co", "currency": "INR", "email": "hi@chai.example"},
    )
    api.post(
        "/api/shop/settings", {"deliver_elsewhere": False, "default_country": "IN"}
    )
    zone = api.post(
        "/api/shop/zones",
        {
            "name": "Hyderabad",
            "postcodes": "500",
            "rate_type": "flat",
            "fee_cents": 3000,
        },
        expect=201,
    )["zone"]
    listed = api.get("/api/shop/zones")
    assert (
        listed["zones"][0]["rate_text"] == "₹30.00"
        and listed["default"]["active"] is False
    )
    product = api.post(
        "/api/shop/products",
        {"name": "Tea", "price_cents": 20000, "status": "active", "weight_grams": 250},
        expect=201,
    )["product"]
    token = api.post("/api/shop/storefront/link", {})["link"]["token"]
    status, page = anonymous(env, "GET", f"/api/public/{token}")
    assert page["page"]["delivery"]["asks"] == {
        "country": False,
        "region": False,
        "postcode": True,
    }
    assert page["page"]["delivery"]["elsewhere"] is False
    items = [{"product_id": product["id"], "quantity": 2}]
    status, quoted = anonymous(
        env,
        "POST",
        f"/api/public/{token}/quote",
        {"items": items, "delivery_method": "delivery", "postcode": "110001"},
    )
    assert (
        status == 200
        and "does not deliver to 110001" in quoted["quote"]["delivery_problem"]
    )
    status, quoted = anonymous(
        env,
        "POST",
        f"/api/public/{token}/quote",
        {"items": items, "delivery_method": "delivery", "postcode": "500081"},
    )
    assert (
        quoted["quote"]["delivery_cents"] == 3000
        and quoted["quote"]["delivery_zone"] == "Hyderabad"
    )
    status, placed = anonymous(
        env,
        "POST",
        f"/api/public/{token}/place_order",
        {
            "items": items,
            "delivery_method": "delivery",
            "name": "Ana Rao",
            "email": "ana@example.com",
            "address": "Road 12, Banjara Hills",
            "postcode": "500034",
            "form_token": page["page"]["form_token"],
        },
    )
    assert status == 201, placed
    assert (
        placed["order"]["delivery_zone"] == "Hyderabad"
        and placed["order"]["total_cents"] == 43000
    )
    order_id = api.get("/api/shop/orders")["orders"][0]["id"]
    api.post(f"/api/shop/orders/{order_id}/paid", {})
    shipped = api.post(
        f"/api/shop/orders/{order_id}/fulfil",
        {
            "status": "shipped",
            "carrier": "india_post",
            "tracking_number": "EE123456789IN",
        },
    )["order"]
    assert (
        shipped["carrier_name"] == "India Post"
        and "EE123456789IN" in shipped["tracking_url"]
    )
    order_token = placed["link"].removeprefix("/p/")
    status, mine = anonymous(env, "GET", f"/api/public/{order_token}")
    assert mine["page"]["carrier"] == "India Post" and mine["page"]["returns"]["open"]
    status, asked = anonymous(
        env,
        "POST",
        f"/api/public/{order_token}/request_return",
        {
            "items": [{"line": 0, "quantity": 1}],
            "reason": "damaged",
            "details": "Leaking",
        },
    )
    assert (
        status == 201
        and asked["order"]["returns"]["returns"][0]["status"] == "requested"
    )
    listing = api.get("/api/shop/returns?status=open")
    assert listing["counts"]["requested"] == 1
    return_id = listing["returns"][0]["id"]
    assert listing["returns"][0]["order_number"] == mine["page"]["number"]
    api.post(f"/api/shop/returns/{return_id}/approve", {"note": "Send it back"})
    out = api.post(f"/api/shop/returns/{return_id}/receive", {})
    assert (
        out["return"]["status"] == "refunded"
        and out["order"]["refunded_cents"] == 20000
    )
    assert out["order"]["returns"][0]["status"] == "refunded"
    settings = api.get("/api/shop/settings")
    assert {c["id"] for c in settings["carriers"]} >= {
        "india_post",
        "delhivery",
        "bluedart",
        "dtdc",
        "custom",
    }
    api.call("DELETE", f"/api/shop/zones/{zone['id']}")
    assert api.get("/api/shop/zones")["zones"] == []
