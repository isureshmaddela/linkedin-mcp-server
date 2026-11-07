"""Shop for businesses and consumers (B2B and B2C): trade accounts, trade prices, terms, credit and receivables.

A business applies on the storefront (or the shop adds it), the shop approves it with terms, a percent off and a
credit limit, and the business signs in with its email and trade code to buy at trade prices on those terms.
Consumers keep the checkout they had.
"""

from __future__ import annotations

import sqlite3

import pytest
from cloud_support import Browser, build_org, sign_in
from test_suite_shop import anonymous, token_of

import auto_apply_assistant as local
import test_careers
from aira.suite import kit, public
from aira.suite.shop import service, store, trade

env = test_careers.env
api = test_careers.api
stub = test_careers.stub


@pytest.fixture
def db(tmp_path):
    kit.forget_schema()
    public.set_index(public._MemoryIndex())
    connection = sqlite3.connect(tmp_path / "shop.sqlite3")
    connection.row_factory = sqlite3.Row
    store.ensure(connection)
    kit.save_business(connection, {"name": "Bean Co", "sell_to": "both"})
    yield connection
    connection.close()


@pytest.fixture
def local_index():
    public.set_index(local._public_link_index)


def product(db, **values):
    return service.create_product(
        db,
        {"name": "Coffee 1kg", "price_cents": 2000, "status": "active", **values},
    )


def business(db, **values):
    made = trade.create(
        db,
        {
            "company": "Corner Cafe",
            "contact_name": "Ravi Kumar",
            "email": "orders@corner.example",
            "address": "1 High St",
            "send": False,
            **values,
        },
    )
    return made["account"], made["code"]


# ---------------------------------------------------------------------------
# trade prices
# ---------------------------------------------------------------------------


def test_a_business_pays_trade_prices_with_price_breaks_and_its_own_discount(db):
    beans = product(
        db,
        wholesale_cents=1500,
        min_quantity=5,
        price_breaks=[
            {"min_quantity": 50, "unit_cents": 1200},
            {"min_quantity": 20, "unit_cents": 1350},
        ],
    )
    account, _ = business(db, discount_percent=10)
    assert [b["min_quantity"] for b in beans["price_breaks"]] == [20, 50]

    consumer = service.price(db, [{"product_id": beans["id"], "quantity": 2}])
    assert consumer["buyer_type"] == "consumer" and consumer["subtotal_cents"] == 4000

    # 10 at the trade price, 10% off: 13.50 each
    ten = service.price(
        db, [{"product_id": beans["id"], "quantity": 10}], account=account
    )
    assert ten["buyer_type"] == "business" and ten["lines"][0]["unit_cents"] == 1350
    assert ten["savings_cents"] == 20000 - 13500
    # 50 reach the best break: 12.00 less 10% = 10.80
    fifty = service.price(
        db, [{"product_id": beans["id"], "quantity": 50}], account=account
    )
    assert fifty["lines"][0]["unit_cents"] == 1080
    # below the minimum quantity, and the cap on a consumer's quantity does not apply to businesses
    with pytest.raises(ValueError, match="least a business can order"):
        service.price(db, [{"product_id": beans["id"], "quantity": 4}], account=account)
    assert service.price(
        db, [{"product_id": beans["id"], "quantity": 500}], account=account
    )
    with pytest.raises(ValueError, match="at most 99"):
        service.price(db, [{"product_id": beans["id"], "quantity": 500}])


def test_options_keep_their_difference_and_breaks_count_every_option(db):
    tee = product(
        db,
        name="Tee",
        price_cents=2000,
        wholesale_cents=1200,
        options=[{"name": "S"}, {"name": "XL", "price_cents": 2400}],
        price_breaks=[{"min_quantity": 10, "unit_cents": 1000}],
    )
    account, _ = business(db)
    quote = service.price(
        db,
        [
            {"product_id": tee["id"], "option": "S", "quantity": 6},
            {"product_id": tee["id"], "option": "XL", "quantity": 4},
        ],
        account=account,
    )
    units = {line["option"]: line["unit_cents"] for line in quote["lines"]}
    assert units == {
        "S": 1000,
        "XL": 1400,
    }  # 10 tees reach the break; XL stays 4.00 more


def test_who_a_product_is_for_and_the_business_minimum_order(db):
    retail_only = product(db, name="Gift box", audience="consumers")
    trade_only = product(db, name="Sack 20kg", audience="businesses", price_cents=30000)
    account, _ = business(db)
    with pytest.raises(ValueError, match="not sold to businesses"):
        service.price(db, [{"product_id": retail_only["id"]}], account=account)
    with pytest.raises(ValueError, match="business buyers only"):
        service.price(db, [{"product_id": trade_only["id"]}])
    store.save_settings(db, {"trade_min_order_cents": 50000})
    with pytest.raises(ValueError, match="Business orders start at"):
        service.price(db, [{"product_id": trade_only["id"]}], account=account)
    assert service.price(
        db, [{"product_id": trade_only["id"], "quantity": 2}], account=account
    )


def test_a_reseller_pays_no_tax(db):
    kit.save_business(db, {"tax_rate_bp": 1000})
    beans = product(db)
    reseller, _ = business(db, tax_exempt=True)
    assert (
        service.price(db, [{"product_id": beans["id"]}], account=reseller)["tax_cents"]
        == 0
    )
    assert service.price(db, [{"product_id": beans["id"]}])["tax_cents"] == 200


# ---------------------------------------------------------------------------
# terms, credit and what businesses owe
# ---------------------------------------------------------------------------


def test_net_terms_set_a_due_date_and_the_credit_limit_holds(db):
    beans = product(db, wholesale_cents=1000)
    account, _ = business(db, terms="net30", credit_limit_cents=25000)
    first = service.place_order(
        db,
        {"items": [{"product_id": beans["id"], "quantity": 20}], "po_number": "PO-7"},
        account=account,
    )
    assert first["buyer_type"] == "business" and first["company"] == "Corner Cafe"
    assert first["po_number"] == "PO-7" and first["terms"] == "net30"
    assert first["due_on"] == trade.due_on("net30")
    assert first["customer_email"] == "orders@corner.example"
    assert trade.credit(db, account) == {
        "limit_cents": 25000,
        "open_cents": 20000,
        "available_cents": 5000,
    }
    # 10 more would take it to 300.00, over its 250.00 limit
    with pytest.raises(ValueError, match="over its credit limit"):
        service.place_order(
            db,
            {"items": [{"product_id": beans["id"], "quantity": 10}]},
            account=account,
        )
    service.mark_paid(db, first["id"])
    assert service.place_order(
        db, {"items": [{"product_id": beans["id"], "quantity": 10}]}, account=account
    )


def test_receivables_show_what_is_late_and_reminders_go_out(db, monkeypatch):
    beans = product(db, wholesale_cents=1000)
    account, _ = business(db, terms="net7")
    placed = service.place_order(
        db, {"items": [{"product_id": beans["id"], "quantity": 3}]}, account=account
    )
    assert trade.receivables(db)["overdue_cents"] == 0
    later = trade.receivables(db, today=trade.due_on("net30"))
    assert later["open_cents"] == 3000 and later["overdue_cents"] == 3000
    assert later["orders"][0]["days_late"] == 23
    assert later["accounts"][0]["company"] == "Corner Cafe"
    confirmation = next(m for m in kit.outbox(db) if m["kind"] == "order_confirmation")
    assert (
        "Business: Corner Cafe" in confirmation["body"]
        and "Net 7" in confirmation["body"]
    )
    link = service.order_link(db, placed)
    service.queue_message(db, placed, "payment_reminder", link_path=link["path"])
    reminder = next(m for m in kit.outbox(db) if m["kind"] == "payment_reminder")
    assert "is due on" in reminder["body"] and str(placed["number"]) in reminder["body"]


def test_overview_splits_business_and_consumer_sales(db):
    beans = product(db, wholesale_cents=1000)
    account, _ = business(db)
    service.mark_paid(
        db,
        service.place_order(
            db, {"items": [{"product_id": beans["id"], "quantity": 5}]}, account=account
        )["id"],
    )
    service.mark_paid(
        db,
        service.place_order(
            db,
            {
                "items": [{"product_id": beans["id"]}],
                "name": "Ana",
                "email": "ana@example.com",
            },
        )["id"],
    )
    data = service.overview(db)
    assert data["buyers"]["business"]["this_month_cents"] == 5000
    assert data["buyers"]["consumer"]["this_month_cents"] == 2000
    assert data["sell_to"] == "both" and data["trade"]["approved"] == 1
    rows = {c["email"]: c for c in service.customers(db)}
    assert rows["orders@corner.example"]["buyer_type"] == "business"
    assert rows["orders@corner.example"]["company"] == "Corner Cafe"


# ---------------------------------------------------------------------------
# trade accounts and codes
# ---------------------------------------------------------------------------


def test_applying_approving_and_signing_in_with_the_trade_code(db):
    asked = trade.apply(db, {"company": "Deli Two", "email": "Buy@Deli.example"})
    assert asked == {"status": "received"}
    account = trade.by_email(db, "buy@deli.example")
    assert account["status"] == "pending"
    assert trade.verify(db, "buy@deli.example", "anything") is None
    # the same email again says the same thing, without telling a stranger it exists
    assert trade.apply(db, {"company": "Deli Two", "email": "buy@deli.example"}) == {
        "status": "received"
    }
    trade.update(db, account["id"], {"terms": "net15", "discount_percent": 5})
    approved = trade.approve(db, account["id"])
    code = approved["code"]
    assert len(code) == 14 and "code_hash" not in approved["account"]
    signed = trade.verify(db, "BUY@deli.example", code.lower().replace("-", ""))
    assert signed is not None and signed["terms"] == "net15"
    mail = next(m for m in kit.outbox(db) if m["kind"] == "trade_approved")
    assert code in mail["body"] and "Net 15" in mail["body"]
    # a new code replaces the old one; paused or declined accounts cannot sign in
    fresh = trade.new_code(db, account["id"])["code"]
    assert trade.verify(db, "buy@deli.example", code) is None
    assert trade.verify(db, "buy@deli.example", fresh) is not None
    trade.update(db, account["id"], {"status": "paused"})
    assert trade.verify(db, "buy@deli.example", fresh) is None
    with pytest.raises(ValueError, match="Use Approve"):
        trade.update(db, account["id"], {"status": "approved"})


def test_automatic_approval_gives_the_code_at_once(db):
    store.save_settings(db, {"trade_approval": "auto", "trade_default_terms": "net30"})
    result = trade.apply(db, {"company": "Quick Mart", "email": "qm@example.com"})
    assert result["status"] == "approved"
    account = trade.verify(db, "qm@example.com", result["code"])
    assert account is not None and account["terms"] == "net30"


def test_an_account_with_orders_is_paused_not_deleted(db):
    beans = product(db)
    account, _ = business(db)
    service.place_order(db, {"items": [{"product_id": beans["id"]}]}, account=account)
    assert trade.delete(db, account["id"]) == {"deleted": False, "paused": True}
    other, _ = business(db, company="Never Ordered", email="no@example.com")
    assert trade.delete(db, other["id"]) == {"deleted": True, "paused": False}


def test_a_purchase_order_number_can_be_required(db):
    store.save_settings(db, {"trade_require_po": True})
    beans = product(db)
    account, _ = business(db)
    with pytest.raises(ValueError, match="purchase-order"):
        service.place_order(
            db, {"items": [{"product_id": beans["id"]}]}, account=account
        )
    assert service.place_order(
        db, {"items": [{"product_id": beans["id"]}], "po_number": "A1"}, account=account
    )


# ---------------------------------------------------------------------------
# the storefront, over HTTP (the local build)
# ---------------------------------------------------------------------------


def test_a_business_signs_in_on_the_storefront_and_orders_on_terms(
    env, api, local_index
):
    kit.forget_schema()
    api.post("/api/suite/business", {"name": "Bean Co", "sell_to": "both"})
    beans = api.post(
        "/api/shop/products",
        {
            "name": "Coffee 1kg",
            "price_cents": 2000,
            "status": "active",
            "wholesale_cents": 1500,
            "min_quantity": 5,
        },
        expect=201,
    )["product"]
    sack = api.post(
        "/api/shop/products",
        {
            "name": "Sack 20kg",
            "price_cents": 30000,
            "status": "active",
            "audience": "businesses",
        },
        expect=201,
    )["product"]
    token = token_of(api.post("/api/shop/storefront/link")["link"]["path"])

    status, page = anonymous(env, "GET", f"/api/public/{token}")
    assert status == 200 and page["page"]["business_buyers"]["enabled"] is True
    assert [p["name"] for p in page["page"]["products"]] == [
        "Coffee 1kg"
    ]  # the sack is for businesses

    status, asked = anonymous(
        env,
        "POST",
        f"/api/public/{token}/trade_apply",
        {"company": "Corner Cafe", "email": "orders@corner.example", "tax_id": "GB123"},
    )
    assert status == 200 and asked == {"status": "received"}
    listed = api.get("/api/shop/trade-accounts")["accounts"]
    assert [(a["company"], a["status"]) for a in listed] == [("Corner Cafe", "pending")]
    approved = api.post(
        f"/api/shop/trade-accounts/{listed[0]['id']}/approve",
        {"terms": "net30", "credit_limit_cents": 100000},
    )
    code = approved["code"]
    assert approved["account"]["terms"] == "net30"

    status, wrong = anonymous(
        env,
        "POST",
        f"/api/public/{token}/trade_sign_in",
        {"email": "orders@corner.example", "code": "AAAA-BBBB-CCCC"},
    )
    assert status == 403, wrong
    status, signed = anonymous(
        env,
        "POST",
        f"/api/public/{token}/trade_sign_in",
        {"email": "orders@corner.example", "code": code},
    )
    assert status == 200 and signed["page"]["account"]["company"] == "Corner Cafe"
    shown = {p["name"]: p for p in signed["page"]["products"]}
    assert (
        shown["Coffee 1kg"]["price_cents"] == 1500
        and shown["Coffee 1kg"]["min_quantity"] == 5
    )
    assert "Sack 20kg" in shown

    signed_in = {"trade_email": "orders@corner.example", "trade_code": code}
    status, quote = anonymous(
        env,
        "POST",
        f"/api/public/{token}/quote",
        {"items": [{"product_id": beans["id"], "quantity": 10}], **signed_in},
    )
    assert status == 200 and quote["quote"]["subtotal_cents"] == 15000
    status, placed = anonymous(
        env,
        "POST",
        f"/api/public/{token}/place_order",
        {
            "items": [{"product_id": sack["id"], "quantity": 2}],
            "po_number": "PO-9",
            **signed_in,
        },
    )
    assert status == 201, placed
    assert (
        placed["order"]["company"] == "Corner Cafe"
        and placed["order"]["po_number"] == "PO-9"
    )
    assert placed["order"]["terms_label"].startswith("Net 30")
    # a consumer cannot buy the sack, nor pretend to be the business
    status, refused = anonymous(
        env,
        "POST",
        f"/api/public/{token}/place_order",
        {
            "items": [{"product_id": sack["id"]}],
            "name": "Ana",
            "email": "a@example.com",
        },
    )
    assert status == 400 and "business buyers only" in refused["error"]
    status, _ = anonymous(
        env,
        "POST",
        f"/api/public/{token}/quote",
        {
            "items": [{"product_id": beans["id"], "quantity": 10}],
            "trade_email": "orders@corner.example",
            "trade_code": "nope",
        },
    )
    assert status == 403

    owed = api.get("/api/shop/receivables")
    assert (
        owed["open_cents"] == 60000 and owed["accounts"][0]["company"] == "Corner Cafe"
    )
    business_orders = api.get("/api/shop/orders?buyer=business")["orders"]
    assert [o["po_number"] for o in business_orders] == ["PO-9"]
    assert api.get("/api/shop/orders?buyer=consumer")["orders"] == []
    assert (
        api.post(f"/api/shop/orders/{business_orders[0]['id']}/remind")["queued"]
        is True
    )


def test_a_shop_for_businesses_only_hides_prices_until_sign_in(env, api, local_index):
    kit.forget_schema()
    api.post("/api/suite/business", {"name": "Wholesale Ltd", "sell_to": "businesses"})
    beans = api.post(
        "/api/shop/products",
        {"name": "Coffee 1kg", "price_cents": 2000, "status": "active"},
        expect=201,
    )["product"]
    token = token_of(api.post("/api/shop/storefront/link")["link"]["path"])
    page = anonymous(env, "GET", f"/api/public/{token}")[1]["page"]
    assert page["business_buyers"]["only"] is True
    assert page["products"][0]["price_cents"] is None
    status, refused = anonymous(
        env,
        "POST",
        f"/api/public/{token}/place_order",
        {
            "items": [{"product_id": beans["id"]}],
            "name": "Ana",
            "email": "a@example.com",
        },
    )
    assert status == 403 and "businesses only" in refused["error"]


def test_a_consumer_shop_offers_no_trade_accounts(env, api, local_index):
    kit.forget_schema()
    api.post(
        "/api/suite/business", {"name": "Corner Gifts"}
    )  # sells to consumers, as before
    token = token_of(api.post("/api/shop/storefront/link")["link"]["path"])
    page = anonymous(env, "GET", f"/api/public/{token}")[1]["page"]
    assert (
        page["business_buyers"]["enabled"] is False and page["sell_to"] == "consumers"
    )
    status, _ = anonymous(
        env,
        "POST",
        f"/api/public/{token}/trade_apply",
        {"company": "X", "email": "x@example.com"},
    )
    assert status == 403


# ---------------------------------------------------------------------------
# hosted: a member's own shop, and staff who may only read
# ---------------------------------------------------------------------------


def test_hosted_trade_accounts_follow_the_shop_roles(cloud):
    kit.forget_schema()
    org = build_org(cloud)
    cloud.billing.subscribe_app(org.owner, "shop")
    owner = sign_in(cloud, org.owner)
    assert (
        owner.call(
            "POST", "/api/suite/business", {"name": "Org Shop", "sell_to": "both"}
        )[0]
        == 200
    )
    status, made = owner.call(
        "POST",
        "/api/shop/trade-accounts",
        {"company": "Corner Cafe", "email": "orders@corner.example", "terms": "net15"},
    )
    assert status == 201 and made["code"] and made["account"]["status"] == "approved"
    auditor = sign_in(cloud, org.auditor)
    assert auditor.call("GET", "/api/shop/trade-accounts")[0] == 200
    assert (
        auditor.call(
            "POST", f"/api/shop/trade-accounts/{made['account']['id']}/code", {}
        )[0]
        == 403
    )
    other = build_org(cloud, "Other Org", slug_email="other.test")
    cloud.billing.subscribe_app(other.owner, "shop")
    stranger = sign_in(cloud, other.owner)
    assert (
        stranger.call("GET", f"/api/shop/trade-accounts/{made['account']['id']}")[0]
        == 404
    )
    assert isinstance(Browser(cloud), Browser)
