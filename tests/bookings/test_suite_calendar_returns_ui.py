"""Calendars, delivery zones and returns in a real browser: a staff member connects their calendar (a stand-in
feed on this computer) and its busy time disappears; a customer's booking offers "Add to calendar"; the shop adds
a delivery zone, the storefront says where it does not deliver, the shop ships with a carrier, the customer asks
for a return on the order page and the shop approves, receives and refunds it."""

# ruff: noqa: F811
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

patchright = pytest.importorskip("patchright.sync_api")

from aira.suite import kit, public, webfetch  # noqa: E402
from test_careers import env, stub  # noqa: E402, F401
from test_leads_ui import browser, go, signed_in_page, until  # noqa: E402, F401
from test_suite_bookings_calendar import Feeds, calendar, event, stamp  # noqa: E402
from test_suite_commerce_ui import caller, no_model, no_null, quick_forms  # noqa: E402, F401

import auto_apply_assistant as local  # noqa: E402


@pytest.fixture
def feeds(monkeypatch):
    monkeypatch.setenv(webfetch.ALLOW_LOCAL_ENV, "1")
    server = Feeds()
    yield server
    server.stop()


def test_a_staff_calendar_and_add_to_calendar_in_the_browser(browser, env, feeds):
    kit.forget_schema()
    public.set_index(local._public_link_index)
    context, page, problems = signed_in_page(browser, env)
    call = caller(context, env)
    call(
        "POST",
        "/api/suite/business",
        {"name": "Glow Studio", "email": "owner@glow.example", "timezone": "UTC"},
    )
    person = call(
        "POST",
        "/api/bookings/staff",
        {
            "name": "Ana",
            "hours": [
                {"weekday": d, "start": "09:00", "end": "17:00"} for d in range(7)
            ],
        },
    )["person"]
    cut = call(
        "POST",
        "/api/bookings/services",
        {"name": "Cut", "minutes": 60, "staff_ids": [person["id"]]},
    )["service"]
    day = datetime.now(timezone.utc).date() + timedelta(days=4)
    url = feeds.put(
        "/ana/basic.ics",
        calendar(
            event(
                "busy@x",
                f"DTSTART:{stamp(day, '09:00')}Z",
                f"DTEND:{stamp(day, '12:00')}Z",
            )
        ),
    )

    go(page, env, "bookings?tab=staff")
    page.wait_for_selector("text=Not connected")
    page.get_by_role("button", name="Connect calendar").click()
    page.wait_for_selector(".drawer >> text=Private iCal address")
    page.locator(".drawer input[type='url']").fill(url)
    page.locator(".drawer").get_by_role("button", name="Connect", exact=True).click()
    until(
        page,
        lambda: (
            "Not yet" not in page.locator(".drawer").inner_text()
            and "127.0.0.1/…/basic.ics" in page.locator(".drawer").inner_text()
        ),
        what="the calendar to be read",
    )
    assert url not in page.locator(".drawer").inner_text()
    page.keyboard.press("Escape")
    go(page, env, "bookings?tab=staff")
    page.wait_for_selector(".suite-table >> text=synced")
    assert "1 busy time" in page.locator(".suite-table").inner_text()
    slots = call(
        "GET",
        f"/api/bookings/slots?service_id={cut['id']}&day={day.isoformat()}&days=1",
    )["days"][0]["slots"]
    assert slots[0]["local"] == "12:00"

    link = call("POST", "/api/bookings/page-link", {})["link"]
    visitor = context.new_page()
    visitor.on("pageerror", lambda e: problems.append(f"public pageerror: {e}"))
    visitor.goto(env.base + link["path"])
    visitor.wait_for_selector(".pub-item")
    visitor.locator(".pub-item").first.click()
    visitor.wait_for_selector('[aria-label="Time"] .pub-slot')
    visitor.locator('[aria-label="Time"] .pub-slot').first.click()
    visitor.fill("input[autocomplete='name']", "Maya Lopez")
    visitor.fill("input[autocomplete='email']", "maya@example.com")
    visitor.get_by_role("button", name="Book").click()
    visitor.wait_for_selector("text=You're booked.")
    add = visitor.get_by_role("link", name="Add to calendar", exact=True)
    href = add.get_attribute("href")
    assert href.startswith("/api/public/") and href.endswith("/calendar.ics")
    google = visitor.get_by_role("link", name="Add to Google Calendar").get_attribute(
        "href"
    )
    assert google.startswith(
        "https://calendar.google.com/calendar/render?action=TEMPLATE"
    )
    file = context.request.get(env.base + href)
    assert file.status == 200 and "METHOD:PUBLISH" in file.text()
    visitor.get_by_role("link", name="Manage your booking").click()
    visitor.wait_for_selector("text=Need to change it?")
    assert visitor.get_by_role("link", name="Add to calendar", exact=True).count() == 1
    no_null(visitor)
    assert [p for p in problems if "favicon" not in p] == []
    context.close()


def test_zones_tracking_and_a_return_in_the_browser(browser, env):
    kit.forget_schema()
    public.set_index(local._public_link_index)
    context, page, problems = signed_in_page(browser, env)
    call = caller(context, env)
    call(
        "POST",
        "/api/suite/business",
        {"name": "Chai Co", "currency": "INR", "email": "hi@chai.example"},
    )
    call(
        "POST",
        "/api/shop/settings",
        {"deliver_elsewhere": False, "pickup_enabled": True},
    )
    call(
        "POST",
        "/api/shop/products",
        {
            "name": "Masala tea",
            "price_cents": 20000,
            "status": "active",
            "weight_grams": 250,
        },
    )["product"]

    # The shop adds a zone in Settings.
    go(page, env, "shop?tab=settings")
    page.wait_for_selector("text=Delivery zones")
    page.get_by_role("button", name="Add a zone").click()
    page.get_by_label("Name *").fill("Hyderabad")
    page.get_by_label("Postcode or PIN prefixes").fill("500, 501")
    page.get_by_label("Fee", exact=True).fill("30")
    page.get_by_role("button", name="Save").last.click()
    page.wait_for_selector(".suite-table >> text=PIN 500, 501")
    assert "₹30.00" in page.locator(".suite-table").inner_text()
    assert "Everywhere else: not delivered" in page.locator("body").inner_text()

    store_url = env.base + call("POST", "/api/shop/storefront/link", {})["link"]["path"]
    shopper = context.new_page()
    shopper.on("pageerror", lambda e: problems.append(f"storefront pageerror: {e}"))
    shopper.goto(store_url)
    shopper.wait_for_selector(".pub-item")
    shopper.get_by_role("button", name="Add to cart").first.click()
    shopper.get_by_role("radio", name="Delivery").click()
    shopper.get_by_label("PIN or postcode").fill("110001")
    shopper.get_by_label("PIN or postcode").dispatch_event("change")
    shopper.wait_for_selector("text=Sorry, the shop does not deliver to 110001")
    shopper.get_by_label("PIN or postcode").fill("500034")
    shopper.get_by_label("PIN or postcode").dispatch_event("change")
    shopper.wait_for_selector("text=Delivery (Hyderabad)")
    shopper.get_by_label("Your name").fill("Ana Rao")
    shopper.get_by_label("Email").fill("ana@example.com")
    shopper.get_by_label("Delivery address").fill("Road 12, Banjara Hills")
    shopper.get_by_role("button", name="Place order").click()
    shopper.wait_for_selector("text=Order placed")
    order_path = shopper.get_by_role("link", name="See your order").get_attribute(
        "href"
    )
    order = call("GET", "/api/shop/orders")["orders"][0]
    assert order["delivery_cents"] == 3000 and order["delivery_zone"] == "Hyderabad"
    call("POST", f"/api/shop/orders/{order['id']}/paid", {})

    # The shop ships it with a carrier and number.
    go(page, env, f"shop?tab=orders&order={order['id']}")
    page.wait_for_selector(".drawer")
    page.locator(".drawer").get_by_role("button", name="Shipped").click()
    page.get_by_label("Carrier").select_option("delhivery")
    page.get_by_label("Tracking number").fill("DL998877")
    page.get_by_role("button", name="Mark shipped").click()
    until(
        page,
        lambda: "Delhivery · DL998877" in page.locator(".drawer").inner_text(),
        what="the carrier to show",
    )

    shopper.goto(env.base + order_path)
    shopper.wait_for_selector("text=Sent with Delhivery")
    assert "delhivery.com/track/package/DL998877" in shopper.get_by_role(
        "link", name="Track your parcel"
    ).get_attribute("href")
    shopper.get_by_role("button", name="Return items").click()
    shopper.get_by_label("Masala tea", exact=False).fill("1")
    shopper.get_by_label("Why are you returning it?").select_option("damaged")
    shopper.get_by_role("button", name="Send the return request").click()
    shopper.wait_for_selector("text=Waiting for the shop")

    go(page, env, "shop?tab=returns")
    page.wait_for_selector("text=R-0001")
    page.get_by_role("button", name="Approve").click()
    page.get_by_role("button", name="Approve").last.click()
    page.wait_for_selector("text=Return R-0001: approved.")
    page.wait_for_selector(".suite-pill >> text=approved")
    page.get_by_role("button", name="Received").click()
    page.get_by_role("button", name="Received").last.click()
    page.wait_for_selector("text=Return R-0001: received and refunded.")
    go(page, env, "shop?tab=returns&status=refunded")
    page.wait_for_selector(".suite-pill >> text=refunded")
    shopper.reload()
    shopper.wait_for_selector("text=Refunded ₹200.00.")
    kinds = [m["kind"] for m in call("GET", "/api/suite/outbox?app=shop")["messages"]]
    assert kinds.count("return_update") == 3 and "order_shipped" in kinds
    no_null(page)
    no_null(shopper)
    assert [p for p in problems if "favicon" not in p] == []
    context.close()
