"""Payments through the real servers: the hub's Payments settings (secrets never come back), Test connection, a
storefront checkout that goes to Razorpay, the signed webhook (raw body, size limit), refunds from the hub, the
customer's return, the public address (only customer pages and the webhook answer there), and the hosted build's
per-organization webhook. Razorpay is the local stand-in (tests/razorpay_payments_stub.py)."""

from __future__ import annotations

import json
import urllib.error
import urllib.request

import pytest
from cloud_support import Browser, build_org, sign_in
from razorpay_payments_stub import KEY_ID, KEY_SECRET, WEBHOOK_SECRET, RazorpayStub

import auto_apply_assistant as local
import test_careers
from aira.payments import razorpay
from aira.suite import kit, public
from aira.suite.shop import service as shop
from test_suite_shop import anonymous, token_of

env = test_careers.env
api = test_careers.api
stub = test_careers.stub


@pytest.fixture(autouse=True)
def quick_forms(monkeypatch):
    monkeypatch.setattr(shop, "MIN_FILL_SECONDS", 0)


@pytest.fixture
def rzp(monkeypatch):
    server = RazorpayStub().start()
    monkeypatch.setenv(razorpay.API_BASE_ENV, server.base)
    yield server
    server.stop()


@pytest.fixture
def local_index():
    public.set_index(local._public_link_index)
    local._public_hosts.forget()
    yield
    local._public_hosts.forget()
    public.reset_index()


def raw_call(env, method, path, body=b"", headers=None):
    request = urllib.request.Request(
        env.base + path, data=body if method != "GET" else None, method=method
    )
    for name, value in (headers or {}).items():
        request.add_header(name, value)
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            data = response.read()
            return response.status, data
    except urllib.error.HTTPError as error:
        return error.code, error.read()


def jsonish(data):
    try:
        return json.loads(data or b"{}")
    except ValueError:
        return {}


def set_up_razorpay(api):
    api.post(
        "/api/suite/business",
        {"name": "Chai Co", "email": "hi@chai.example", "currency": "INR"},
    )
    return api.post(
        "/api/suite/payments",
        {
            "provider": "razorpay",
            "razorpay_key_id": KEY_ID,
            "key_secret": KEY_SECRET,
            "webhook_secret": WEBHOOK_SECRET,
            "upi_id": "chai@okaxis",
        },
    )


def storefront(api, env, stock=5):
    tea = api.post(
        "/api/shop/products",
        {
            "name": "Tea",
            "price_cents": 25000,
            "status": "active",
            "track_stock": True,
            "stock": stock,
        },
        expect=201,
    )["product"]
    token = token_of(api.post("/api/shop/storefront/link")["link"]["path"])
    return tea, token


def test_payment_settings_keep_secrets_server_side_and_test_the_connection(
    env, api, rzp, local_index
):
    view = set_up_razorpay(api)
    text = json.dumps(view)
    assert KEY_SECRET not in text and WEBHOOK_SECRET not in text
    assert (
        view["has_key_secret"]
        and view["has_webhook_secret"]
        and view["ready"]
        and view["mode"] == "test"
    )
    assert view["webhook_url"].startswith(env.base + "/api/payments/razorpay/webhook/")
    assert (
        view["webhook_reachable"] is False
    )  # a loopback address: Razorpay cannot reach it
    assert set(view["webhook_events"]) >= {"payment_link.paid", "refund.processed"}
    again = api.get("/api/suite/payments")
    assert (
        KEY_SECRET not in json.dumps(again)
        and again["webhook_url"] == view["webhook_url"]
    )
    assert api.post("/api/suite/payments/test", {}) == {"ok": True, "mode": "test"}
    assert rzp.calls_to("GET", "/payment_links")[-1]["authorized"]
    api.post("/api/suite/payments", {"key_secret": "wrong-secret"})
    status, body = api.call("POST", "/api/suite/payments/test", {})
    assert status == 502 and "key id or key secret" in body["error"]
    cleared = api.post("/api/suite/payments", {"clear_key_secret": True})
    assert cleared["has_key_secret"] is False and cleared["ready"] is False
    status, body = api.call(
        "POST", "/api/suite/payments", {"razorpay_key_id": "rzp_prod_123"}
    )
    assert status == 400
    # customers' pages never see a key or a secret, only that online payment is on (or not)
    page = anonymous(env, "GET", f"/api/public/{storefront(api, env)[1]}")[1]
    assert (
        page["business"]["online_payments"] is False
        and page["business"]["upi_id"] == "chai@okaxis"
    )
    assert "rzp_" not in json.dumps(page)


def test_a_storefront_checkout_pays_online_and_the_webhook_marks_it_paid(
    env, api, rzp, local_index
):
    view = set_up_razorpay(api)
    tea, token = storefront(api, env)
    page = anonymous(env, "GET", f"/api/public/{token}")[1]
    assert (
        page["business"]["online_payments"] is True
        and page["page"]["online_payments"] is True
    )
    status, placed = anonymous(
        env,
        "POST",
        f"/api/public/{token}/place_order",
        {
            "items": [{"product_id": tea["id"], "quantity": 2}],
            "name": "Ana",
            "email": "ana@example.com",
        },
    )
    assert status == 201 and placed["pay_url"].startswith("https://rzp.io/")
    link = next(iter(rzp.links.values()))
    assert link["amount"] == 50000 and link["callback_url"] == env.base + placed["link"]
    assert link["customer"] == {"name": "Ana", "email": "ana@example.com"}
    order_id = public.resolve(env.user_db(), token_of(placed["link"]))["ref_id"]
    payment = rzp.pay(link["id"])
    body, headers = rzp.webhook(
        "payment_link.paid", link_id=link["id"], payment=payment
    )
    path = view["webhook_url"].removeprefix(env.base)
    # a forged delivery is refused before anything changes
    status, answer = raw_call(
        env, "POST", path, body, {**headers, "X-Razorpay-Signature": "0" * 64}
    )
    assert (
        status == 403
        and api.get(f"/api/shop/orders/{order_id}")["order"]["payment_status"]
        == "unpaid"
    )
    status, answer = raw_call(env, "POST", path, body, headers)
    assert status == 200 and jsonish(answer)["outcome"] == "paid", answer
    order = api.get(f"/api/shop/orders/{order_id}")["order"]
    assert (
        order["payment_status"] == "paid" and order["payments"][0]["status"] == "paid"
    )
    status, answer = raw_call(env, "POST", path, body, headers)
    assert status == 200 and jsonish(answer)["duplicate"] is True
    # a webhook token that is not one: 404; a body over the limit: refused unread
    assert (
        raw_call(env, "POST", "/api/payments/razorpay/webhook/" + token, body, headers)[
            0
        ]
        == 404
    )
    status, _ = raw_call(env, "POST", path, b"{" + b" " * 300_000 + b"}", headers)
    assert status == 413
    assert api.get("/api/suite/revenue")["summary"]["total_cents"] == 50000

    # refunds from the order (part) and from the hub (the rest): negative lines, stock back
    api.post(
        f"/api/shop/orders/{order_id}/refund",
        {"amount_cents": 10000, "note": "one broken"},
    )
    assert api.get(f"/api/shop/orders/{order_id}")["order"]["refunded_cents"] == 10000
    request_id = order["payments"][0]["id"]
    refunded = api.post(f"/api/suite/payments/requests/{request_id}/refund", {})[
        "request"
    ]
    assert refunded["status"] == "refunded" and refunded["refunded_cents"] == 50000
    assert api.get(f"/api/shop/products/{tea['id']}")["product"]["stock"] == 5
    summary = api.get("/api/suite/revenue")["summary"]
    assert summary["total_cents"] == 0 and summary["refunded_cents"] == 50000
    assert (
        api.get("/api/suite/payments/requests?status=refunded")["requests"][0]["id"]
        == request_id
    )


def test_pay_online_from_the_order_page_and_the_signed_return(
    env, api, rzp, local_index
):
    tea, token = storefront(api, env)
    status, placed = anonymous(
        env,
        "POST",
        f"/api/public/{token}/place_order",
        {
            "items": [{"product_id": tea["id"]}],
            "name": "Ana",
            "email": "ana@example.com",
        },
    )
    assert placed["pay_url"] == ""  # Razorpay is not set up yet
    order_token = token_of(placed["link"])
    status, body = anonymous(env, "POST", f"/api/public/{order_token}/pay_online", {})
    assert status == 400 and "online payments" in body["error"]
    set_up_razorpay(api)
    view = anonymous(env, "GET", f"/api/public/{order_token}")[1]
    assert {"pay_online", "pay_return"} <= set(view["actions"])
    status, made = anonymous(env, "POST", f"/api/public/{order_token}/pay_online", {})
    assert (
        status == 200
        and made["url"].startswith("https://rzp.io/")
        and made["amount_cents"] == 25000
    )
    link = next(iter(rzp.links.values()))
    payment = rzp.pay(link["id"])
    status, forged = anonymous(
        env,
        "POST",
        f"/api/public/{order_token}/pay_return",
        rzp.return_params(link["id"], payment["id"], key_secret="x"),
    )
    assert status == 200 and forged["verified"] is False
    status, shown = anonymous(
        env,
        "POST",
        f"/api/public/{order_token}/pay_return",
        rzp.return_params(link["id"], payment["id"]),
    )
    assert shown == {**shown, "verified": True, "paid": True, "status": "paid"}
    assert (
        anonymous(env, "GET", f"/api/public/{order_token}")[1]["page"]["payment_status"]
        == "paid"
    )


def test_the_hub_makes_payment_links_for_any_payable(env, api, rzp, local_index):
    set_up_razorpay(api)
    tea, _ = storefront(api, env)
    order = api.post(
        "/api/shop/orders",
        {"items": [{"product_id": tea["id"]}], "name": "Walk In", "phone": "12345"},
        expect=201,
    )["order"]
    made = api.post(
        "/api/suite/payments/requests",
        {"app": "shop", "object_type": "order", "object_id": order["id"]},
        expect=201,
    )["request"]
    assert (
        made["short_url"].startswith("https://rzp.io/")
        and made["amount_cents"] == 25000
    )
    assert (
        api.post(f"/api/suite/payments/requests/{made['id']}/cancel", {})["request"][
            "status"
        ]
        == "cancelled"
    )
    status, body = api.call(
        "POST",
        "/api/suite/payments/requests",
        {"app": "shop", "object_type": "nothing", "object_id": order["id"]},
    )
    assert status == 404
    status, body = api.call(
        "POST", f"/api/suite/payments/requests/{'0' * 32}/refund", {}
    )
    assert status == 404
    home = api.get("/api/suite/home")
    assert home["payments_attention"] == 0 and home["public_links"]["loopback"] is True


# ---------------------------------------------------------------------------
# the public address
# ---------------------------------------------------------------------------


def as_host(env, method, path, host, body=None, extra=None):
    headers = {"Host": host, "Content-Type": "application/json", **(extra or {})}
    data = json.dumps(body).encode() if body is not None else b""
    return raw_call(env, method, path, data, headers)


def test_a_public_address_reaches_only_customer_pages_and_the_webhook(
    env, api, rzp, local_index
):
    host = "chai-shop.example.com"
    for bad in (
        "http://chai-shop.example.com",
        "https://127.0.0.1",
        "https://localhost",
        "https://chai.example.com/shop",
        "https://203.0.113.5",
    ):
        status, body = api.call("POST", "/api/suite/business", {"public_address": bad})
        assert status == 400, bad
    tea, token = storefront(api, env)
    assert as_host(env, "GET", f"/p/{token}", host)[0] == 403  # not set yet
    saved = api.post(
        "/api/suite/business", {"name": "Chai Co", "public_address": f"https://{host}/"}
    )
    assert (
        saved["public_address"] == f"https://{host}"
        and saved["public_links"]["loopback"] is False
    )
    assert api.get("/api/suite/home")["public_links"]["origin"] == f"https://{host}"
    # customers' pages, the files they load and their actions answer at the public address
    assert as_host(env, "GET", f"/p/{token}", host)[0] == 200
    status, page = as_host(env, "GET", f"/api/public/{token}", host)
    assert status == 200 and jsonish(page)["kind"] == "storefront"
    assert as_host(env, "GET", "/static/public.js", host)[0] == 200
    assert as_host(env, "GET", "/static/public-shop.js", host)[0] == 200
    assert as_host(env, "GET", "/static/i18n/de.json", host)[0] in (200, 404)
    status, placed = as_host(
        env,
        "POST",
        f"/api/public/{token}/place_order",
        host,
        {
            "items": [{"product_id": tea["id"]}],
            "name": "Ana",
            "email": "ana@example.com",
            "form_token": jsonish(page)["page"]["form_token"],
        },
        {"Origin": f"https://{host}"},
    )
    assert status == 201, placed
    # everything else stays loopback-only, signed in or not
    for method, path in (
        ("GET", "/"),
        ("GET", "/hub"),
        ("GET", "/static/app.js"),
        ("GET", "/static/views-hub.js"),
        ("GET", "/api/suite/home"),
        ("GET", "/api/health"),
        ("GET", "/api/shop/orders"),
        ("POST", "/api/auth/login"),
    ):
        assert (
            as_host(env, method, path, host, {} if method == "POST" else None)[0] == 403
        ), path
    # another name is refused even for the customer pages
    assert as_host(env, "GET", f"/p/{token}", "evil.example.com")[0] == 403
    assert as_host(env, "GET", f"/api/public/{token}", "evil.example.com")[0] == 403
    # links for customers now use the public address: payment links come back to it, the webhook is there
    view = set_up_razorpay(api)
    assert (
        view["webhook_url"].startswith(f"https://{host}/api/payments/razorpay/webhook/")
        and view["webhook_reachable"]
    )
    status, placed = as_host(
        env,
        "POST",
        f"/api/public/{token}/place_order",
        host,
        {
            "items": [{"product_id": tea["id"]}],
            "name": "Bo",
            "email": "bo@example.com",
            "form_token": jsonish(page)["page"]["form_token"],
        },
        {"Origin": f"https://{host}"},
    )
    link = next(iter(rzp.links.values()))
    assert link["callback_url"].startswith(f"https://{host}/p/")
    payment = rzp.pay(link["id"])
    body, headers = rzp.webhook(
        "payment_link.paid", link_id=link["id"], payment=payment
    )
    status, answer = raw_call(
        env,
        "POST",
        view["webhook_url"].removeprefix(f"https://{host}"),
        body,
        {**headers, "Host": host},
    )
    assert status == 200 and jsonish(answer)["outcome"] == "paid"
    # cleared: the address stops answering
    api.post("/api/suite/business", {"public_address": ""})
    assert as_host(env, "GET", f"/p/{token}", host)[0] == 403
    assert api.get("/api/suite/business")["public_links"]["loopback"] is True


# ---------------------------------------------------------------------------
# the hosted build: one webhook address per organization
# ---------------------------------------------------------------------------


def test_hosted_webhooks_find_their_organization_and_the_policy_lists_every_route(
    cloud, rzp
):
    from monosage_cloud.policy import POLICY

    for name in (
        "airaapi__suite_routes__razorpay_webhook",
        "airaapi__suite_routes__payments_get",
        "airaapi__suite_routes__payments_save",
        "airaapi__suite_routes__payments_test",
        "airaapi__suite_routes__payment_requests",
        "airaapi__suite_routes__payment_request_create",
        "airaapi__suite_routes__payment_request_refund",
        "airaapi__suite_routes__payment_request_cancel",
    ):
        assert name in POLICY, name
    assert POLICY["airaapi__suite_routes__razorpay_webhook"].scope == "public_link"
    assert POLICY["airaapi__suite_routes__payments_get"].permission == "suite.read"
    assert POLICY["airaapi__suite_routes__payments_save"].permission == "suite.manage"

    kit.forget_schema()
    org = build_org(cloud)
    cloud.billing.subscribe_app(org.owner, "shop")
    owner = sign_in(cloud, org.owner)
    owner.call(
        "POST", "/api/suite/business", {"name": "Hosted Chai", "currency": "INR"}
    )
    status, view = owner.call(
        "POST",
        "/api/suite/payments",
        {
            "provider": "razorpay",
            "razorpay_key_id": KEY_ID,
            "key_secret": KEY_SECRET,
            "webhook_secret": WEBHOOK_SECRET,
        },
    )
    assert status == 200 and view["ready"], view
    assert KEY_SECRET not in json.dumps(view)
    assert "/api/payments/razorpay/webhook/" in view["webhook_url"]
    status, made = owner.call(
        "POST",
        "/api/shop/products",
        {"name": "Tea", "price_cents": 25000, "status": "active"},
    )
    status, order = owner.call(
        "POST",
        "/api/shop/orders",
        {
            "items": [{"product_id": made["product"]["id"]}],
            "name": "Dee",
            "phone": "12345",
        },
    )
    order_id = order["order"]["id"]
    status, request = owner.call(
        "POST",
        "/api/suite/payments/requests",
        {"app": "shop", "object_type": "order", "object_id": order_id},
    )
    assert status == 201, request
    link_id = request["request"]["provider_ref"]
    payment = rzp.pay(link_id)
    body, headers = rzp.webhook("payment_link.paid", link_id=link_id, payment=payment)
    path = "/api/payments/razorpay/webhook/" + view["webhook_url"].rsplit("/", 1)[-1]
    machine = Browser(cloud)  # no session: Razorpay's server
    status, answer = machine.call("POST", path, b"{}", headers=headers)
    assert status == 403, answer  # the signature is over the exact bytes sent
    status, answer = machine.call("POST", path, body, headers=headers)
    assert status == 200 and answer["outcome"] == "paid", answer
    status, seen = owner.call("GET", f"/api/shop/orders/{order_id}")
    assert seen["order"]["payment_status"] == "paid"
    status, answer = machine.call(
        "POST", path, body, headers={**headers, "X-Razorpay-Signature": "f" * 64}
    )
    assert status == 403
    status, answer = machine.call("POST", path, body, headers=headers)
    assert status == 200 and answer["duplicate"] is True
    # another organization's token does not reach this one's payments
    status, answer = machine.call(
        "POST", "/api/payments/razorpay/webhook/" + "A" * 43, body, headers=headers
    )
    assert status == 404
