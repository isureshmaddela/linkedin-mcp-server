"""Shop (aira/suite/shop): pricing, stock, orders, discounts, the storefront and order pages, both builds."""

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
def no_model(monkeypatch):
    """Never reach a real writing model (one may be running on this machine)."""
    from aira import suite

    monkeypatch.setattr(suite, "_gateway", _NoModel())


@pytest.fixture
def db(tmp_path):
    kit.forget_schema()
    public.set_index(public._MemoryIndex())
    connection = sqlite3.connect(tmp_path / "shop.sqlite3")
    connection.row_factory = sqlite3.Row
    store.ensure(connection)
    yield connection
    connection.close()


@pytest.fixture
def local_index():
    public.set_index(local._public_link_index)


def product(db, **values):
    return service.create_product(
        db, {"name": "Mug", "price_cents": 1200, "status": "active", **values}
    )


def order(db, items, **values):
    return service.place_order(
        db, {"items": items, "name": "Ana Silva", "email": "ana@example.com", **values}
    )


# ---------------------------------------------------------------------------
# pricing
# ---------------------------------------------------------------------------


def test_options_set_their_own_price_and_need_choosing(db):
    tee = product(
        db,
        name="Tee",
        price_cents=2000,
        options=[{"name": "S"}, {"name": "XL", "price_cents": 2400}],
    )
    quote = service.price(
        db,
        [
            {"product_id": tee["id"], "option": "xl", "quantity": 2},
            {"product_id": tee["id"], "option": "S"},
        ],
    )
    assert [line["unit_cents"] for line in quote["lines"]] == [2400, 2000]
    assert quote["lines"][0]["option"] == "XL"
    assert quote["subtotal_cents"] == 6800
    with pytest.raises(ValueError, match="Choose an option"):
        service.price(db, [{"product_id": tee["id"]}])
    with pytest.raises(ValueError, match="no options"):
        service.price(db, [{"product_id": product(db)["id"], "option": "M"}])
    with pytest.raises(ValueError):
        service.price(db, [])
    with pytest.raises(ValueError):
        service.price(db, [{"product_id": tee["id"], "option": "S", "quantity": 0}])


def test_drafts_and_archived_products_cannot_be_bought_online(db):
    hidden = product(db, status="draft")
    with pytest.raises(ValueError, match="no longer available"):
        service.price(db, [{"product_id": hidden["id"]}])
    assert (
        service.price(db, [{"product_id": hidden["id"]}], include_drafts=True)[
            "subtotal_cents"
        ]
        == 1200
    )


def test_discounts_percent_fixed_minimum_expiry_and_limit(db):
    mug = product(db, price_cents=3333)
    service.create_discount(db, {"code": "save10", "kind": "percent", "value": 10})
    service.create_discount(
        db, {"code": "FIVE", "kind": "fixed", "value": 500, "min_order_cents": 5000}
    )
    service.create_discount(
        db, {"code": "OLD", "kind": "percent", "value": 50, "expires_on": "2020-01-01"}
    )
    service.create_discount(
        db, {"code": "ONCE", "kind": "fixed", "value": 100000, "usage_limit": 1}
    )
    items = [{"product_id": mug["id"], "quantity": 1}]
    assert (
        service.price(db, items, code="SAVE10")["discount_cents"] == 333
    )  # 10% of 33.33, rounded half up
    with pytest.raises(ValueError, match="at least"):
        service.price(db, items, code="five")
    assert (
        service.price(db, [{"product_id": mug["id"], "quantity": 2}], code="FIVE")[
            "discount_cents"
        ]
        == 500
    )
    with pytest.raises(ValueError, match="expired"):
        service.price(db, items, code="OLD")
    with pytest.raises(ValueError, match="not valid"):
        service.price(db, items, code="NOPE")
    capped = service.price(db, items, code="ONCE")
    assert (
        capped["discount_cents"] == 3333 and capped["total_cents"] == 0
    )  # never below zero
    order(db, items, discount_code="ONCE")
    with pytest.raises(ValueError, match="used up"):
        service.price(db, items, code="ONCE")
    with pytest.raises(ValueError, match="already exists"):
        service.create_discount(db, {"code": "save10", "kind": "percent", "value": 5})
    with pytest.raises(ValueError):
        service.create_discount(db, {"code": "BAD", "kind": "percent", "value": 150})


def test_tax_and_delivery_fee_with_a_free_delivery_threshold(db):
    kit.save_business(db, {"name": "Clay Co", "tax_rate_bp": 825})
    store.save_settings(
        db, {"delivery_fee_cents": 499, "free_delivery_over_cents": 5000}
    )
    mug = product(db, price_cents=1999)
    pickup = service.price(db, [{"product_id": mug["id"]}])
    assert (
        pickup["delivery_cents"] == 0 and pickup["tax_cents"] == 165
    )  # 8.25% of 19.99 = 1.649 -> 1.65
    delivered = service.price(
        db, [{"product_id": mug["id"]}], delivery_method="delivery"
    )
    assert delivered["delivery_cents"] == 499
    assert delivered["total_cents"] == 1999 + 499 + 165
    free = service.price(
        db, [{"product_id": mug["id"], "quantity": 3}], delivery_method="delivery"
    )
    assert free["delivery_cents"] == 0
    store.save_settings(db, {"delivery_enabled": False})
    with pytest.raises(ValueError, match="pickup only"):
        service.price(db, [{"product_id": mug["id"]}], delivery_method="delivery")
    with pytest.raises(ValueError, match="pickup, delivery or both"):
        store.save_settings(db, {"pickup_enabled": False})


# ---------------------------------------------------------------------------
# stock and orders
# ---------------------------------------------------------------------------


def test_stock_is_taken_on_order_and_refused_when_short(db):
    mug = product(db, track_stock=True, stock=3)
    tee = product(
        db,
        name="Tee",
        track_stock=True,
        options=[{"name": "S", "stock": 1}, {"name": "M", "stock": 0}],
    )
    first = order(
        db,
        [
            {"product_id": mug["id"], "quantity": 2},
            {"product_id": tee["id"], "option": "S"},
        ],
    )
    assert store.get_product(db, mug["id"])["stock"] == 1
    assert service.find_option(store.get_product(db, tee["id"]), "S")["stock"] == 0
    with pytest.raises(ValueError, match="Only 1 left"):
        order(db, [{"product_id": mug["id"], "quantity": 2}])
    with pytest.raises(ValueError, match="sold out"):
        order(db, [{"product_id": tee["id"], "option": "M"}])
    # A refused order changes nothing.
    assert store.get_product(db, mug["id"])["stock"] == 1
    assert kit.count(db, "SELECT COUNT(*) FROM sh_orders") == 1
    # Cancelling gives the stock back, once.
    service.cancel(db, first["id"])
    service.cancel(db, first["id"])
    assert store.get_product(db, mug["id"])["stock"] == 3
    assert service.find_option(store.get_product(db, tee["id"]), "S")["stock"] == 1
    assert service.is_low(store.get_product(db, mug["id"])) is True


def test_orders_are_numbered_and_keep_a_snapshot(db):
    mug = product(db, track_stock=False)
    numbers = [order(db, [{"product_id": mug["id"]}])["number"] for _ in range(3)]
    assert numbers == [1001, 1002, 1003]
    service.update_product(db, mug["id"], {"price_cents": 9999, "name": "Big mug"})
    kept = store.list_orders(db)[-1]
    assert kept["items"][0]["unit_cents"] == 1200 and kept["items"][0]["name"] == "Mug"
    with pytest.raises(ValueError, match="email address or a phone"):
        service.place_order(
            db, {"items": [{"product_id": mug["id"]}], "name": "No Contact"}
        )
    with pytest.raises(ValueError, match="delivery address"):
        order(db, [{"product_id": mug["id"]}], delivery_method="delivery")
    store.save_settings(db, {"open": False, "closed_message": "Back Monday."})
    with pytest.raises(PermissionError, match="Back Monday"):
        order(db, [{"product_id": mug["id"]}])
    assert (
        service.place_order(
            db,
            {
                "items": [{"product_id": mug["id"]}],
                "name": "Walk In",
                "phone": "555 0100",
            },
            allow_closed=True,
        )["number"]
        == 1004
    )


def test_revenue_is_recorded_once_and_voided_on_refund(db):
    mug = product(db)
    placed = order(db, [{"product_id": mug["id"], "quantity": 2}])
    assert kit.revenue(db)["total_cents"] == 0
    service.mark_paid(db, placed["id"])
    service.mark_paid(db, placed["id"])
    assert kit.revenue(db, app="shop")["total_cents"] == 2400
    assert kit.revenue(db)["payments"] == 1
    service.refund(db, placed["id"])
    assert kit.revenue(db)["total_cents"] == 0
    with pytest.raises(ValueError, match="Only a paid order"):
        service.refund(db, placed["id"])
    # the money went back: the refunded order is never paid (and counted) again
    with pytest.raises(ValueError, match="refunded"):
        service.mark_paid(db, placed["id"])
    assert kit.revenue(db)["total_cents"] == 0
    again = order(db, [{"product_id": mug["id"], "quantity": 2}])
    service.mark_paid(db, again["id"])
    assert kit.revenue(db)["total_cents"] == 2400
    service.mark_unpaid(db, again["id"])
    assert kit.revenue(db)["total_cents"] == 0


def test_messages_go_to_the_outbox_and_fulfilment_moves_forward(db, monkeypatch):
    from aira import suite

    # The hosted build's configured address is what links in emails use.
    monkeypatch.setattr(suite, "_public_origin", lambda: "https://shop.example")
    kit.save_business(
        db, {"name": "Clay Co", "payment_link": "https://pay.example/clay"}
    )
    mug = product(db)
    placed = order(db, [{"product_id": mug["id"]}])
    service.mark_paid(db, placed["id"])
    shipped = service.set_fulfilment(
        db, placed["id"], "shipped", tracking_url="https://track.example/1"
    )
    assert (
        shipped["fulfilment_status"] == "shipped"
        and shipped["tracking_url"] == "https://track.example/1"
    )
    kinds = [m["kind"] for m in kit.outbox(db)]
    assert sorted(kinds) == ["order_confirmation", "order_shipped", "payment_receipt"]
    confirmation = next(m for m in kit.outbox(db) if m["kind"] == "order_confirmation")
    assert (
        "https://pay.example/clay" in confirmation["body"]
        and "https://shop.example/p/" in confirmation["body"]
    )
    assert "1001" in confirmation["body"]
    service.set_fulfilment(db, placed["id"], "delivered")
    with pytest.raises(ValueError, match="delivered order"):
        service.cancel(db, placed["id"])
    with pytest.raises(ValueError):
        service.set_fulfilment(db, placed["id"], "cancelled")


def test_customers_overview_and_public_views(db):
    mug = product(db, track_stock=True, stock=10, low_stock=5)
    product(db, name="Secret", status="draft")
    a = order(db, [{"product_id": mug["id"], "quantity": 3}])
    order(db, [{"product_id": mug["id"]}], email="ANA@example.com", name="Ana S")
    service.place_order(
        db, {"items": [{"product_id": mug["id"]}], "name": "Ben", "phone": "555 0101"}
    )
    service.mark_paid(db, a["id"])
    people = service.customers(db)
    assert (
        people[0]["orders"] == 2
        and people[0]["spend_cents"] == 3600
        and people[0]["repeat"]
    )
    assert len(service.customers(db, q="ben")) == 1
    data = service.overview(db)
    assert (
        data["to_fulfil"] == 3
        and data["unpaid"] == 2
        and data["average_order_cents"] == 3600
    )
    assert data["best_sellers"][0]["quantity"] == 5
    assert [p["name"] for p in data["low_stock"]] == ["Mug"]
    front = service.storefront(db)
    assert [p["name"] for p in front["products"]] == ["Mug"]
    assert front["products"][0]["left"] == 5
    seen = service.public_order(store.get_order(db, a["id"]))
    assert (
        "internal_notes" not in seen
        and "customer_email" not in seen
        and seen["number"] == 1001
    )
    assert (
        service.draft_description(
            {"name": "Mug", "notes": "hand thrown, dishwasher safe"}
        )["source"]
        == "template"
    )


def test_product_input_is_validated(db):
    with pytest.raises(ValueError, match="https"):
        product(db, images=["http://example.com/a.png"])
    with pytest.raises(ValueError):
        product(db, price_cents=-5)
    with pytest.raises(ValueError, match="twice"):
        product(db, options=["S", "s"])
    made = product(db, compare_at_cents=1000, options="S, M")
    assert made["compare_at_cents"] == 0 and [o["name"] for o in made["options"]] == [
        "S",
        "M",
    ]
    assert product(db, price_cents="12.50")["price_cents"] == 1250


# ---------------------------------------------------------------------------
# the single-user build
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


def token_of(path):
    return path.rsplit("/", 1)[-1]


def test_a_customer_orders_from_the_storefront_and_the_owner_gets_paid(
    env, api, local_index
):
    kit.forget_schema()
    api.post(
        "/api/suite/business",
        {
            "name": "Clay Co",
            "email": "hi@clay.example",
            "payment_link": "https://pay.example/clay",
        },
    )
    mug = api.post(
        "/api/shop/products",
        {
            "name": "Mug",
            "price_cents": 1500,
            "status": "active",
            "track_stock": True,
            "stock": 5,
        },
        expect=201,
    )["product"]
    api.post(
        "/api/shop/products",
        {"name": "Prototype", "price_cents": 100, "status": "draft"},
        expect=201,
    )
    api.post(
        "/api/shop/discounts",
        {"code": "HELLO", "kind": "percent", "value": 20},
        expect=201,
    )
    link = api.post("/api/shop/storefront/link")["link"]
    token = token_of(link["path"])

    status, page = anonymous(env, "GET", f"/api/public/{token}")
    assert (
        status == 200
        and page["kind"] == "storefront"
        and page["business"]["name"] == "Clay Co"
    )
    assert [p["name"] for p in page["page"]["products"]] == ["Mug"]
    status, quote = anonymous(
        env,
        "POST",
        f"/api/public/{token}/quote",
        {"items": [{"product_id": mug["id"], "quantity": 2}], "discount_code": "hello"},
    )
    assert (
        status == 200
        and quote["quote"]["total_cents"] == 2400
        and "discount_id" not in quote["quote"]
    )
    # The browser's prices are ignored: the shop prices the order.
    status, placed = anonymous(
        env,
        "POST",
        f"/api/public/{token}/place_order",
        {
            "items": [
                {
                    "product_id": mug["id"],
                    "quantity": 2,
                    "unit_cents": 1,
                    "price_cents": 1,
                }
            ],
            "discount_code": "HELLO",
            "total_cents": 1,
            "name": "Ana",
            "email": "ana@example.com",
        },
    )
    assert status == 201, placed
    assert placed["order"]["total_cents"] == 2400 and placed["order"]["number"] == 1001
    status, error = anonymous(
        env,
        "POST",
        f"/api/public/{token}/place_order",
        {
            "items": [{"product_id": mug["id"], "quantity": 9}],
            "name": "Bo",
            "email": "bo@example.com",
        },
    )
    assert status == 400 and "Only 3 left" in error["error"]

    order_token = token_of(placed["link"])
    status, view = anonymous(env, "GET", f"/api/public/{order_token}")
    assert (
        status == 200
        and view["kind"] == "order"
        and view["page"]["payment_status"] == "unpaid"
    )
    assert (
        anonymous(
            env,
            "POST",
            f"/api/public/{order_token}/notify_paid",
            {"reference": "UPI 123"},
        )[1]["order"]["customer_says_paid"]
        is True
    )

    orders = api.get("/api/shop/orders")["orders"]
    assert len(orders) == 1 and orders[0]["source"] == "storefront"
    order_id = orders[0]["id"]
    api.post(f"/api/shop/orders/{order_id}/paid")
    shipped = api.post(
        f"/api/shop/orders/{order_id}/fulfil",
        {"status": "shipped", "tracking_url": "https://track.example/9"},
    )["order"]
    assert (
        shipped["fulfilment_status"] == "shipped"
        and shipped["link"]["path"] == placed["link"]
    )
    assert (
        anonymous(env, "GET", f"/api/public/{order_token}")[1]["page"][
            "fulfilment_status"
        ]
        == "shipped"
    )
    home = api.get("/api/suite/home")
    assert home["revenue"]["by_app"] == {"shop": 2400}
    # Shop is a section of the Commerce app: the hub lists it inside that app
    shop_card = next(
        s for app in home["apps"] for s in app["sections"] if s["id"] == "shop"
    )
    assert shop_card["installed"] and shop_card["progress"]["done"] == 5
    assert {m["kind"] for m in api.get("/api/suite/outbox")["messages"]} == {
        "order_confirmation",
        "payment_receipt",
        "order_shipped",
    }
    overview = api.get("/api/shop/overview")
    assert overview["to_fulfil"] == 0 and overview["storefront"]["path"] == link["path"]
    assert api.get("/api/shop/products/" + mug["id"])["product"]["stock"] == 3
    request = urllib.request.Request(
        env.base + "/api/shop/orders?format=csv", headers={"X-Requested-With": "aira"}
    )
    with api.opener.open(request, timeout=30) as response:
        sheet = response.read().decode()
    assert sheet.startswith("number,created_at") and "1001" in sheet and "Ana" in sheet
    assert (
        api.call("PATCH", f"/api/shop/products/{mug['id']}", {"price_cents": -1})[0]
        == 400
    )

    # Closing the shop refuses new orders politely; turning the link off makes it a 404.
    api.post(
        "/api/shop/settings",
        {"open": False, "closed_message": "On holiday until Monday."},
    )
    status, error = anonymous(
        env,
        "POST",
        f"/api/public/{token}/place_order",
        {"items": [{"product_id": mug["id"]}], "name": "Cy", "email": "cy@example.com"},
    )
    assert status == 403 and "holiday" in error["error"]
    api.call("DELETE", "/api/shop/storefront/link")
    assert anonymous(env, "GET", f"/api/public/{token}")[0] == 404


def test_manual_orders_can_be_entered_paid(env, api, local_index):
    kit.forget_schema()
    mug = api.post(
        "/api/shop/products",
        {"name": "Mug", "price_cents": 1500, "status": "draft"},
        expect=201,
    )["product"]
    made = api.post(
        "/api/shop/orders",
        {
            "items": [{"product_id": mug["id"]}],
            "name": "Walk In",
            "phone": "555 0100",
            "mark_paid": True,
        },
        expect=201,
    )["order"]
    assert made["source"] == "manual" and made["payment_status"] == "paid"
    assert api.get("/api/suite/revenue")["summary"]["total_cents"] == 1500
    assert api.get("/api/shop/customers")["customers"][0]["name"] == "Walk In"


# ---------------------------------------------------------------------------
# the hosted build
# ---------------------------------------------------------------------------


def test_hosted_shop_needs_the_subscription_and_the_right_role(cloud):
    kit.forget_schema()
    org = build_org(cloud)
    owner = sign_in(cloud, org.owner)
    status, data = owner.call("GET", "/api/shop/overview")
    assert status == 402 and data["code"] == "app_required"
    cloud.billing.subscribe_app(org.owner, "shop")
    assert owner.call("GET", "/api/shop/overview")[0] == 200
    for who in ("admin", "coach"):
        browser = sign_in(cloud, getattr(org, who))
        status, made = browser.call(
            "POST",
            "/api/shop/products",
            {"name": f"{who} mug", "price_cents": 900, "status": "active"},
        )
        assert status == 201, (who, made)
    auditor = sign_in(cloud, org.auditor)
    assert auditor.call("GET", "/api/shop/products")[0] == 200
    assert (
        auditor.call("POST", "/api/shop/products", {"name": "x", "price_cents": 1})[0]
        == 403
    )
    assert sign_in(cloud, org.candidate).call("GET", "/api/shop/products")[0] == 403

    products = owner.call("GET", "/api/shop/products")[1]["products"]
    status, linked = owner.call("POST", "/api/shop/storefront/link", {})
    assert status == 200
    visitor = Browser(cloud)
    token = token_of(linked["link"]["path"])
    status, page = visitor.call("GET", f"/api/public/{token}")
    assert status == 200 and len(page["page"]["products"]) == 2
    status, placed = visitor.call(
        "POST",
        f"/api/public/{token}/place_order",
        {
            "items": [{"product_id": products[0]["id"], "quantity": 2}],
            "name": "Dee",
            "email": "dee@example.com",
        },
    )
    assert status == 201, placed
    assert placed["order"]["total_cents"] == 1800
    status, view = visitor.call("GET", f"/api/public/{token_of(placed['link'])}")
    assert status == 200 and view["page"]["number"] == 1001
    order_id = owner.call("GET", "/api/shop/orders")[1]["orders"][0]["id"]
    assert owner.call("POST", f"/api/shop/orders/{order_id}/paid", {})[0] == 200
    assert owner.call("GET", "/api/suite/home")[1]["revenue"]["by_app"] == {
        "shop": 1800
    }

    other = build_org(cloud, "Other Org", slug_email="other.test")
    cloud.billing.subscribe_app(other.owner, "shop")
    other_owner = sign_in(cloud, other.owner)
    assert other_owner.call("GET", "/api/shop/orders")[1]["orders"] == []
    assert other_owner.call("GET", f"/api/shop/orders/{order_id}")[0] == 404


# ---------------------------------------------------------------------------
# the screens, in a real browser
# ---------------------------------------------------------------------------


@pytest.fixture
def chromium():
    sync_api = pytest.importorskip("patchright.sync_api")
    with sync_api.sync_playwright() as playwright:
        try:
            launched = playwright.chromium.launch(headless=True)
        except Exception as error:  # noqa: BLE001
            pytest.skip(f"Chromium is not available: {error}")
        yield launched
        launched.close()


def test_the_shop_screens_and_the_storefront_work_in_a_browser(
    chromium, env, local_index
):
    from test_leads_ui import signed_in_page

    kit.forget_schema()
    context, page, problems = signed_in_page(chromium, env)
    headers = {
        "Content-Type": "application/json",
        "X-Requested-With": "aira",
        "Origin": env.base,
    }

    def post(path, body):
        response = context.request.post(
            env.base + path, data=json.dumps(body), headers=headers
        )
        assert response.status in (200, 201), response.text()
        return response.json()

    post(
        "/api/suite/business",
        {"name": "Clay Co", "payment_link": "https://pay.example/clay"},
    )
    post(
        "/api/shop/products",
        {
            "name": "Mug",
            "price_cents": 1500,
            "status": "active",
            "category": "Cups",
            "track_stock": True,
            "stock": 4,
        },
    )
    post(
        "/api/shop/products",
        {"name": "Tee", "price_cents": 2000, "status": "active", "options": ["S", "M"]},
    )

    page.goto(f"{env.base}/#/shop")
    page.wait_for_selector(".suite-kpis")
    page.get_by_role("button", name="Create link").click()
    page.wait_for_selector(".suite-share-url")
    storefront_url = page.locator(".suite-share-url").inner_text()
    assert "/p/" in storefront_url
    page.goto(f"{env.base}/#/shop?tab=products")
    page.wait_for_selector(".suite-table")
    assert "Mug" in page.locator(".suite-table").inner_text()
    page.locator(".suite-table tr", has_text="Mug").click()
    page.wait_for_selector(".drawer")
    assert page.locator(".drawer input[name='name']").input_value() == "Mug"
    page.keyboard.press("Escape")

    shopper = context.new_page()
    shopper.on("pageerror", lambda e: problems.append(f"pageerror: {e}"))
    shopper.goto(storefront_url)
    shopper.wait_for_selector(".pub-item")
    assert shopper.locator("#pubName").inner_text() == "Clay Co"
    shopper.locator(".pub-item", has_text="Mug").get_by_role(
        "button", name="Add to cart"
    ).click()
    shopper.wait_for_selector(".pub-total")
    assert "15.00" in shopper.locator(".pub-total").inner_text()
    shopper.get_by_label("Your name").fill("Ana Silva")
    shopper.get_by_label("Email").fill("ana@example.com")
    shopper.get_by_role("button", name="Place order").click()
    shopper.wait_for_selector("text=Order placed")
    assert "#1001" in shopper.locator("main").inner_text()
    assert shopper.get_by_role("link", name="Pay").count() == 1

    page.goto(f"{env.base}/#/shop?tab=orders")
    page.wait_for_selector(".suite-table")
    page.locator(".suite-table tr", has_text="#1001").click()
    page.wait_for_selector(".drawer")
    page.locator(".drawer").get_by_role("button", name="Mark paid").click()
    page.wait_for_selector(".drawer .suite-pill.good")
    page.goto(f"{env.base}/#/hub")
    page.wait_for_selector(".suite-kpis")
    assert "15.00" in page.locator(".suite-kpis").first.inner_text()
    for tab, marker in (
        ("discounts", "New discount code"),
        ("customers", "Ana Silva"),
        ("settings", "Taking orders"),
        ("overview", "Best sellers"),
    ):
        page.goto(f"{env.base}/#/shop?tab={tab}")
        page.wait_for_selector(f"text={marker}")
    page.goto(f"{env.base}/#/shop?tab=discounts")
    page.get_by_role("button", name="New discount code").click()
    page.locator(".modal input[name='code']").fill("WELCOME10")
    page.locator(".modal input[name='value']").fill("10")
    page.locator(".modal").get_by_role("button", name="Save").click()
    page.wait_for_selector(".suite-table >> text=WELCOME10")
    assert [p for p in problems if "favicon" not in p] == []
    context.close()
