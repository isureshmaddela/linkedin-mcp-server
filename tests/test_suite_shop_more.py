"""Shop (Commerce): back-in-stock alerts, share-and-earn referrals, and regressions found while using it."""

from __future__ import annotations

import json
import sqlite3
import urllib.error
import urllib.request

import pytest
from cloud_support import Browser, build_org, sign_in

import auto_apply_assistant as local
import test_careers
from aira.suite import kit, public
from aira.suite.shop import service, store

env = test_careers.env
api = test_careers.api
stub = test_careers.stub


class _NoModel:
    def model(self, prompt):
        return None


@pytest.fixture(autouse=True)
def no_model():
    """Never reach a real writing model (one may be running on this machine)."""
    from aira import suite

    previous = suite.gateway()
    suite.set_gateway(_NoModel())
    yield
    suite.set_gateway(previous)


@pytest.fixture
def db(tmp_path):
    kit.forget_schema()
    public.set_index(public._MemoryIndex())
    connection = sqlite3.connect(tmp_path / "shop.sqlite3")
    connection.row_factory = sqlite3.Row
    store.ensure(connection)
    kit.save_business(connection, {"name": "Clay Co", "email": "owner@clay.example"})
    yield connection
    connection.close()


@pytest.fixture
def local_index():
    public.set_index(local._public_link_index)


def product(db, **values):
    return service.create_product(
        db, {"name": "Mug", "price_cents": 1000, "status": "active", **values}
    )


def place(db, items, **values):
    return service.place_order(
        db, {"items": items, "name": "Ana Silva", "email": "ana@example.com", **values}
    )


def outbox(db, kind):
    return [m for m in kit.outbox(db) if m["kind"] == kind]


def enable_referrals(db, **values):
    return store.save_settings(
        db,
        {
            "referrals_enabled": True,
            "referral_friend_percent": 10,
            "referral_reward_cents": 500,
            **values,
        },
    )


# ---------------------------------------------------------------------------
# regressions
# ---------------------------------------------------------------------------


def test_an_edit_cannot_store_a_was_price_below_the_price(db):
    mug = product(db, price_cents=2000)
    assert (
        service.update_product(db, mug["id"], {"compare_at_cents": 1500})[
            "compare_at_cents"
        ]
        == 0
    )
    assert (
        service.update_product(db, mug["id"], {"compare_at_cents": 2500})[
            "compare_at_cents"
        ]
        == 2500
    )
    # lowering the "was" price under a new price clears it too
    assert (
        service.update_product(db, mug["id"], {"price_cents": 3000})["compare_at_cents"]
        == 0
    )


def test_a_discount_code_cannot_take_a_customers_referral_code(db):
    enable_referrals(db)
    referrer = service.ensure_referrer(db, email="ana@example.com", name="Ana Silva")
    db.commit()
    with pytest.raises(ValueError, match="already exists"):
        service.create_discount(
            db, {"code": referrer["code"], "kind": "percent", "value": 5}
        )


def test_the_orders_csv_lists_what_was_ordered(env, api, local_index):
    kit.forget_schema()
    mug = api.post(
        "/api/shop/products",
        {"name": "Mug", "price_cents": 1200, "status": "active", "options": ["Blue"]},
        expect=201,
    )["product"]
    api.post(
        "/api/shop/orders",
        {
            "items": [{"product_id": mug["id"], "option": "Blue", "quantity": 2}],
            "name": "Ana",
            "email": "ana@example.com",
            "note": "Gift wrap",
        },
        expect=201,
    )
    request = urllib.request.Request(
        env.base + "/api/shop/orders?format=csv", headers={"X-Requested-With": "aira"}
    )
    with api.opener.open(request, timeout=30) as response:
        sheet = response.read().decode()
    assert "items_summary" in sheet.splitlines()[0]
    assert "2 x Mug (Blue)" in sheet and "Gift wrap" in sheet


# ---------------------------------------------------------------------------
# back in stock
# ---------------------------------------------------------------------------


def test_customers_ask_for_a_sold_out_product_and_are_told_once_when_it_is_back(db):
    vase = product(db, name="Vase", track_stock=True, stock=0)
    tee = product(
        db,
        name="Tee",
        track_stock=True,
        options=[{"name": "S", "stock": 0}, {"name": "M", "stock": 4}],
    )
    service.ask_restock_alert(
        db, {"product_id": vase["id"], "email": "Maya@Example.com", "name": "Maya"}
    )
    # asking twice keeps one request
    service.ask_restock_alert(
        db, {"product_id": vase["id"], "email": "maya@example.com"}
    )
    service.ask_restock_alert(
        db, {"product_id": tee["id"], "option": "s", "email": "bo@example.com"}
    )
    with pytest.raises(ValueError, match="in stock now"):
        service.ask_restock_alert(
            db, {"product_id": tee["id"], "option": "M", "email": "bo@example.com"}
        )
    with pytest.raises(ValueError, match="Choose which option"):
        service.ask_restock_alert(
            db, {"product_id": tee["id"], "email": "bo@example.com"}
        )
    with pytest.raises(ValueError):
        service.ask_restock_alert(
            db, {"product_id": vase["id"], "email": "not an email"}
        )
    with pytest.raises(LookupError):
        service.ask_restock_alert(
            db, {"product_id": "0" * 32, "email": "x@example.com"}
        )
    assert service.waiting_counts(db) == {vase["id"]: 1, tee["id"]: 1}

    # more of another option does not tell the person waiting for S
    service.update_product(
        db,
        tee["id"],
        {"options": [{"name": "S", "stock": 0}, {"name": "M", "stock": 9}]},
    )
    assert outbox(db, "back_in_stock") == []
    service.update_product(db, vase["id"], {"stock": 3})
    told = outbox(db, "back_in_stock")
    assert [m["recipient"] for m in told] == ["maya@example.com"]
    assert "Vase is back in stock, but only 3 left" in told[0]["body"]
    assert "Hi Maya" in told[0]["body"]
    service.update_product(db, vase["id"], {"stock": 10})
    assert len(outbox(db, "back_in_stock")) == 1  # told once
    assert service.waiting_counts(db) == {tee["id"]: 1}
    service.update_product(
        db,
        tee["id"],
        {"options": [{"name": "S", "stock": 2}, {"name": "M", "stock": 9}]},
    )
    assert [m["recipient"] for m in outbox(db, "back_in_stock")][-1] == "bo@example.com"
    assert "Tee (S)" in outbox(db, "back_in_stock")[-1]["subject"]


def test_stock_returned_by_a_cancelled_order_tells_the_people_waiting(db):
    mug = product(db, track_stock=True, stock=1)
    order = place(db, [{"product_id": mug["id"]}])
    service.ask_restock_alert(
        db, {"product_id": mug["id"], "email": "wait@example.com"}
    )
    service.cancel(db, order["id"])
    assert [m["recipient"] for m in outbox(db, "back_in_stock")] == ["wait@example.com"]


def test_a_draft_product_does_not_send_back_in_stock_mail(db):
    mug = product(db, track_stock=True, stock=0)
    service.ask_restock_alert(
        db, {"product_id": mug["id"], "email": "wait@example.com"}
    )
    service.update_product(db, mug["id"], {"status": "draft", "stock": 5})
    assert outbox(db, "back_in_stock") == []
    service.update_product(db, mug["id"], {"status": "active"})
    assert len(outbox(db, "back_in_stock")) == 1


# ---------------------------------------------------------------------------
# share and earn
# ---------------------------------------------------------------------------


def test_a_paid_customer_gets_a_code_and_earns_a_reward_when_a_new_friend_pays(db):
    enable_referrals(db, referral_reward_days=30)
    mug = product(db, price_cents=2000)
    first = place(db, [{"product_id": mug["id"]}])
    assert service.order_referral(db, first) is None  # not paid yet
    service.mark_paid(db, first["id"])
    share = service.order_referral(db, store.get_order(db, first["id"]))
    assert share and share["code"].startswith("ANA-") and share["friend_percent"] == 10
    receipt = outbox(db, "payment_receipt")[-1]
    assert share["code"] in receipt["body"] and "Friends get 10% off" in receipt["body"]

    quote = service.price(db, [{"product_id": mug["id"]}], code=share["code"].lower())
    assert quote["discount_cents"] == 200 and quote["referral"] is True
    assert quote["discount_code"] == share["code"]
    with pytest.raises(ValueError, match="your own code"):
        place(db, [{"product_id": mug["id"]}], discount_code=share["code"])
    with pytest.raises(ValueError, match="email address"):
        place(
            db,
            [{"product_id": mug["id"]}],
            discount_code=share["code"],
            email="",
            phone="555 0100",
        )

    friend = place(
        db,
        [{"product_id": mug["id"]}],
        discount_code=share["code"],
        name="Bo Chen",
        email="bo@example.com",
    )
    assert friend["discount_cents"] == 200 and friend["total_cents"] == 1800
    assert outbox(db, "referral_reward") == []  # earned only when the friend pays
    service.mark_paid(db, friend["id"])
    service.mark_paid(db, friend["id"])  # pressing twice earns once
    rewards = outbox(db, "referral_reward")
    assert len(rewards) == 1 and rewards[0]["recipient"] == "ana@example.com"
    reward_code = kit.fetch_one(db, "SELECT reward_code FROM sh_referral_rewards")[
        "reward_code"
    ]
    assert reward_code in rewards[0]["body"] and "$5.00" in rewards[0]["subject"]
    reward = store.discount_by_code(db, reward_code)
    assert (
        reward["kind"] == "fixed"
        and reward["value"] == 500
        and reward["usage_limit"] == 1
    )
    assert reward["expires_on"] > kit.today()

    # the friend now has a code of their own, and is no longer new
    assert store.referrer_by_email(db, "bo@example.com")
    with pytest.raises(ValueError, match="first order"):
        place(
            db,
            [{"product_id": mug["id"]}],
            discount_code=share["code"],
            name="Bo",
            email="bo@example.com",
        )

    # the reward pays for Ana's next order, once
    again = place(db, [{"product_id": mug["id"]}], discount_code=reward_code)
    assert again["discount_cents"] == 500
    with pytest.raises(ValueError, match="used up"):
        service.price(db, [{"product_id": mug["id"]}], code=reward_code)

    report = service.referrals(db)
    ana = next(r for r in report["referrers"] if r["email"] == "ana@example.com")
    assert ana["friends"] == 1 and ana["sales_cents"] == 1800
    assert ana["rewards_cents"] == 500 and ana["rewards_used"] == 1
    assert report["totals"]["referrers"] == 2


def test_a_refunded_friends_order_takes_back_an_unused_reward(db):
    enable_referrals(db)
    mug = product(db)
    service.mark_paid(db, place(db, [{"product_id": mug["id"]}])["id"])
    code = store.referrer_by_email(db, "ana@example.com")["code"]
    friend = place(
        db,
        [{"product_id": mug["id"]}],
        discount_code=code,
        name="Bo",
        email="bo@example.com",
    )
    service.mark_paid(db, friend["id"])
    reward_code = kit.fetch_one(db, "SELECT reward_code FROM sh_referral_rewards")[
        "reward_code"
    ]
    service.refund(db, friend["id"])
    assert store.discount_by_code(db, reward_code)["active"] is False
    with pytest.raises(ValueError, match="not valid"):
        service.price(db, [{"product_id": mug["id"]}], code=reward_code)
    assert service.referrals(db)["totals"]["rewards_cents"] == 0


def test_referral_codes_do_nothing_while_share_and_earn_is_off(db):
    enable_referrals(db)
    mug = product(db)
    service.mark_paid(db, place(db, [{"product_id": mug["id"]}])["id"])
    code = store.referrer_by_email(db, "ana@example.com")["code"]
    store.save_settings(db, {"referrals_enabled": False})
    with pytest.raises(ValueError, match="not valid"):
        service.price(db, [{"product_id": mug["id"]}], code=code)
    assert service.storefront(db)["referral_percent"] == 0


def test_share_and_earn_settings_are_validated(db):
    with pytest.raises(ValueError, match="at most 90"):
        store.save_settings(db, {"referral_friend_percent": 95})
    with pytest.raises(ValueError, match="discount, the person who shared a reward"):
        store.save_settings(
            db,
            {
                "referrals_enabled": True,
                "referral_friend_percent": 0,
                "referral_reward_cents": 0,
            },
        )
    saved = enable_referrals(db, referral_reward_cents="7.50")
    assert saved["referral_reward_cents"] == 750


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


def test_back_in_stock_and_share_and_earn_on_the_local_build(env, api, local_index):
    kit.forget_schema()
    api.post(
        "/api/suite/business",
        {"name": "Clay Co", "payment_link": "https://pay.example/clay"},
    )
    api.post(
        "/api/shop/settings",
        {
            "referrals_enabled": True,
            "referral_friend_percent": 15,
            "referral_reward_cents": 400,
        },
    )
    vase = api.post(
        "/api/shop/products",
        {
            "name": "Vase",
            "price_cents": 4000,
            "status": "active",
            "track_stock": True,
            "stock": 0,
        },
        expect=201,
    )["product"]
    mug = api.post(
        "/api/shop/products",
        {"name": "Mug", "price_cents": 2000, "status": "active"},
        expect=201,
    )["product"]
    token = api.post("/api/shop/storefront/link", {})["link"]["token"]

    status, page = anonymous(env, "GET", f"/api/public/{token}")
    assert status == 200 and page["page"]["referral_percent"] == 15
    assert page["page"]["has_discounts"] is True
    status, asked = anonymous(
        env,
        "POST",
        f"/api/public/{token}/notify_me",
        {
            "product_id": vase["id"],
            "email": "maya@example.com",
            "name": "Maya",
            "admin": True,
        },
    )
    assert status == 200 and asked == {"waiting": True}
    listed = api.get("/api/shop/products")["products"]
    assert next(p for p in listed if p["id"] == vase["id"])["waiting"] == 1
    assert (
        api.get(f"/api/shop/products/{vase['id']}")["product"]["alerts"][0]["email"]
        == "maya@example.com"
    )
    assert api.get("/api/shop/overview")["wanted"][0]["name"] == "Vase"
    api.ok("PATCH", f"/api/shop/products/{vase['id']}", {"stock": 2})
    mails = [
        m
        for m in api.get("/api/suite/outbox")["messages"]
        if m["kind"] == "back_in_stock"
    ]
    assert mails and f"/p/{token}" in mails[0]["body"]
    assert api.get("/api/shop/alerts")["alerts"][0]["notified_at"]

    # Ana orders and pays; her order page shows her code
    status, placed = anonymous(
        env,
        "POST",
        f"/api/public/{token}/place_order",
        {
            "items": [{"product_id": mug["id"]}],
            "name": "Ana Silva",
            "email": "ana@example.com",
        },
    )
    assert status == 201, placed
    order_token = placed["link"].rsplit("/", 1)[-1]
    status, before = anonymous(env, "GET", f"/api/public/{order_token}")
    assert before["page"]["referral"] is None
    order_id = api.get("/api/shop/orders")["orders"][0]["id"]
    api.post(f"/api/shop/orders/{order_id}/paid", {})
    status, after = anonymous(env, "GET", f"/api/public/{order_token}")
    share = after["page"]["referral"]
    assert (
        share["code"].startswith("ANA-") and share["storefront_path"] == f"/p/{token}"
    )

    # a friend uses it on the storefront
    status, quote = anonymous(
        env,
        "POST",
        f"/api/public/{token}/quote",
        {"items": [{"product_id": mug["id"]}], "discount_code": share["code"]},
    )
    assert status == 200 and quote["quote"]["discount_cents"] == 300
    assert "referrer_id" not in quote["quote"] and "discount_id" not in quote["quote"]
    status, friend = anonymous(
        env,
        "POST",
        f"/api/public/{token}/place_order",
        {
            "items": [{"product_id": mug["id"]}],
            "discount_code": share["code"],
            "name": "Bo",
            "email": "bo@example.com",
        },
    )
    assert status == 201 and friend["order"]["total_cents"] == 1700
    friend_id = api.get("/api/shop/orders")["orders"][0]["id"]
    api.post(f"/api/shop/orders/{friend_id}/paid", {})
    report = api.get("/api/shop/referrals")
    ana = next(r for r in report["referrers"] if r["email"] == "ana@example.com")
    assert ana["friends"] == 1 and ana["rewards_cents"] == 400
    reward_mail = [
        m
        for m in api.get("/api/suite/outbox")["messages"]
        if m["kind"] == "referral_reward"
    ]
    assert reward_mail and reward_mail[0]["recipient"] == "ana@example.com"


def test_hosted_shop_extras_need_commerce_and_the_right_role(cloud):
    kit.forget_schema()
    org = build_org(cloud)
    owner = sign_in(cloud, org.owner)
    assert owner.call("GET", "/api/shop/referrals")[0] == 402
    assert owner.call("GET", "/api/shop/alerts")[0] == 402
    cloud.billing.subscribe_app(org.owner, "commerce")
    status, report = owner.call("GET", "/api/shop/referrals")
    assert status == 200 and report["referrers"] == []
    status, made = owner.call(
        "POST",
        "/api/shop/products",
        {
            "name": "Vase",
            "price_cents": 4000,
            "status": "active",
            "track_stock": True,
            "stock": 0,
        },
    )
    assert status == 201, made
    status, link = owner.call("POST", "/api/shop/storefront/link", {})
    visitor = Browser(cloud)
    status, asked = visitor.call(
        "POST",
        f"/api/public/{link['link']['token']}/notify_me",
        {"product_id": made["product"]["id"], "email": "maya@example.com"},
    )
    assert status == 200, asked
    auditor = sign_in(cloud, org.auditor)
    status, alerts = auditor.call("GET", "/api/shop/alerts")
    assert status == 200 and alerts["alerts"][0]["email"] == "maya@example.com"
    assert (
        auditor.call("DELETE", f"/api/shop/alerts/{alerts['alerts'][0]['id']}")[0]
        == 403
    )
    assert sign_in(cloud, org.candidate).call("GET", "/api/shop/referrals")[0] == 403
    coach = sign_in(cloud, org.coach)
    assert (
        coach.call("DELETE", f"/api/shop/alerts/{alerts['alerts'][0]['id']}")[0] == 200
    )

    other = build_org(cloud, "Other Shop", slug_email="othershop.test")
    cloud.billing.subscribe_app(other.owner, "commerce")
    status, theirs = sign_in(cloud, other.owner).call("GET", "/api/shop/alerts")
    assert status == 200 and theirs["alerts"] == []


def test_the_hub_card_shows_customers_waiting_for_stock(db):
    from aira.suite.shop import shop_routes

    vase = product(db, name="Vase", track_stock=True, stock=0)
    labels = [card["label"] for card in shop_routes._summary(db)["cards"]]
    assert "Waiting for stock" not in labels
    service.ask_restock_alert(db, {"product_id": vase["id"], "email": "a@example.com"})
    card = next(
        c
        for c in shop_routes._summary(db)["cards"]
        if c["label"] == "Waiting for stock"
    )
    assert card["value"] == 1
