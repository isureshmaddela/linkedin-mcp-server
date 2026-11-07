"""The Payments screens in a browser: Business hub > Business profile > Payments (Razorpay keys, Test connection,
the webhook address, the public address warning), and a storefront checkout that goes to Razorpay and comes back
Paid. Razorpay is the local stand-in; its payment page (rzp.io) is answered by the test, never fetched."""

from __future__ import annotations

from urllib.parse import urlencode

import pytest

patchright = pytest.importorskip("patchright.sync_api")

from razorpay_payments_stub import KEY_ID, KEY_SECRET, WEBHOOK_SECRET, RazorpayStub  # noqa: E402

import auto_apply_assistant as local  # noqa: E402
from aira.payments import razorpay  # noqa: E402
from aira.suite import kit, public  # noqa: E402
from test_careers import env, stub  # noqa: E402, F401
from test_leads_ui import browser, go, signed_in_page  # noqa: E402, F401
from test_suite_commerce_ui import caller, no_model, no_null, quick_forms  # noqa: E402, F401


@pytest.fixture
def rzp(monkeypatch):
    server = RazorpayStub().start()
    monkeypatch.setenv(razorpay.API_BASE_ENV, server.base)
    yield server
    server.stop()


def test_payments_settings_and_a_checkout_paid_online(browser, env, rzp):  # noqa: F811
    kit.forget_schema()
    public.set_index(local._public_link_index)
    local._public_hosts.forget()
    context, page, problems = signed_in_page(browser, env, width=1280, height=900)
    call = caller(context, env)
    call(
        "POST",
        "/api/suite/business",
        {"name": "Chai Co", "email": "hi@chai.example", "currency": "INR"},
    )

    go(page, env, "hub?tab=business")
    page.wait_for_selector(
        "text=Customers cannot open your links yet"
    )  # links still point to 127.0.0.1
    payments = page.locator(".suite-section", has_text="Take payments online")
    payments.get_by_label("How customers pay").select_option("razorpay")
    payments.get_by_label("Key id").fill(KEY_ID)
    payments.get_by_label("Key secret").fill(KEY_SECRET)
    payments.get_by_label("Webhook secret").fill(WEBHOOK_SECRET)
    payments.get_by_label("Your UPI ID").fill("chai@okaxis")
    payments.get_by_role("button", name="Save payments").click()
    page.wait_for_selector("text=ready · test mode")
    payments = page.locator(".suite-section", has_text="Take payments online")
    assert KEY_SECRET not in page.content() and WEBHOOK_SECRET not in page.content()
    assert "Key secret (saved; type to replace)" in payments.inner_text()
    assert "/api/payments/razorpay/webhook/" in payments.inner_text()
    assert "Razorpay cannot reach this address" in payments.inner_text()
    payments.get_by_role("button", name="Test connection").click()
    page.wait_for_selector("text=the keys work (test mode)")
    no_null(page)

    call(
        "POST",
        "/api/shop/products",
        {
            "name": "Masala tea",
            "price_cents": 25000,
            "status": "active",
            "track_stock": True,
            "stock": 5,
        },
    )["product"]
    store_url = env.base + call("POST", "/api/shop/storefront/link", {})["link"]["path"]
    shopper = context.new_page()
    shopper.on("pageerror", lambda e: problems.append(f"storefront pageerror: {e}"))
    # Razorpay's hosted page: answered here, so the test never leaves this machine
    shopper.route(
        "https://rzp.io/**",
        lambda route: route.fulfill(
            status=200, content_type="text/html", body="<h1>Razorpay test page</h1>"
        ),
    )
    shopper.goto(store_url)
    item = shopper.locator(".pub-item", has_text="Masala tea")
    item.get_by_role("button", name="Add to cart").click()
    shopper.get_by_label("Your name").fill("Ana Rao")
    shopper.get_by_label("Email").fill("ana@example.com")
    assert (
        shopper.locator("input[name='website']").count() == 1
    )  # the honeypot is there, out of sight
    shopper.get_by_role("button", name="Place order").click()
    shopper.wait_for_url("https://rzp.io/**")
    link = next(iter(rzp.links.values()))
    assert link["amount"] == 25000 and link["callback_url"].startswith(env.base + "/p/")

    # Paid on Razorpay; the customer's browser comes back with the signed parameters
    payment = rzp.pay(link["id"])
    shopper.goto(
        link["callback_url"]
        + "?"
        + urlencode(rzp.return_params(link["id"], payment["id"]))
    )
    shopper.wait_for_selector("text=Payment received. Thank you!")
    shopper.wait_for_selector(".pub-badge.good:has-text('Paid')")
    assert (
        "razorpay_signature" not in shopper.url
    )  # the parameters left the address bar
    no_null(shopper)

    go(page, env, "shop?tab=orders")
    page.locator("tr", has_text="Ana Rao").first.wait_for()
    go(page, env, "hub?tab=revenue")
    page.wait_for_selector("text=₹250.00")
    assert not problems, problems


def test_the_order_page_offers_pay_online_and_upi(browser, env, rzp):  # noqa: F811
    kit.forget_schema()
    public.set_index(local._public_link_index)
    context, page, problems = signed_in_page(browser, env, width=1280, height=900)
    call = caller(context, env)
    call("POST", "/api/suite/business", {"name": "Chai Co", "currency": "INR"})
    call(
        "POST",
        "/api/suite/payments",
        {
            "provider": "razorpay",
            "razorpay_key_id": KEY_ID,
            "key_secret": KEY_SECRET,
            "upi_id": "chai@okaxis",
        },
    )
    tea = call(
        "POST",
        "/api/shop/products",
        {"name": "Tea", "price_cents": 12000, "status": "active"},
    )["product"]
    order = call(
        "POST",
        "/api/shop/orders",
        {"items": [{"product_id": tea["id"]}], "name": "Walk In", "phone": "12345"},
    )["order"]
    order_url = (
        env.base
        + call("POST", f"/api/shop/orders/{order['id']}/link", {})["link"]["path"]
    )
    customer = context.new_page()
    customer.on("pageerror", lambda e: problems.append(f"order pageerror: {e}"))
    customer.route(
        "https://rzp.io/**",
        lambda route: route.fulfill(
            status=200, content_type="text/html", body="<h1>Razorpay test page</h1>"
        ),
    )
    customer.goto(order_url)
    customer.get_by_role("button", name="Pay ₹120.00 online").wait_for()
    upi = customer.get_by_role("link", name="Pay with a UPI app").get_attribute("href")
    assert (
        upi.startswith("upi://pay?pa=chai%40okaxis")
        and "am=120.00" in upi
        and "cu=INR" in upi
    )
    customer.get_by_role("button", name="Pay ₹120.00 online").click()
    customer.wait_for_url("https://rzp.io/**")
    assert len(rzp.links) == 1
    assert not problems, problems
