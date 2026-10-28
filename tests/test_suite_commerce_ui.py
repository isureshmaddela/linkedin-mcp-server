"""Commerce in a real browser: share and earn, back-in-stock requests, the waiting list for a full day, team pay,
and pages that once printed "null"."""

# ruff: noqa: F811
from __future__ import annotations

import json
import re
from datetime import datetime, timedelta, timezone

import pytest

patchright = pytest.importorskip("patchright.sync_api")

from aira.suite import kit, public  # noqa: E402
from test_careers import env, stub  # noqa: E402, F401
from test_leads_ui import browser, go, signed_in_page, until  # noqa: E402, F401

import auto_apply_assistant as local  # noqa: E402


@pytest.fixture(autouse=True)
def no_model():
    """Never call the real writing model (a live Ollama may run on this machine)."""
    from aira import suite

    class NoModel:
        def model(self, prompt):
            return None

    previous = suite.gateway()
    suite.set_gateway(NoModel())
    yield
    suite.set_gateway(previous)


def caller(context, env):
    headers = {
        "Content-Type": "application/json",
        "X-Requested-With": "aira",
        "Origin": env.base,
    }

    def call(method, path, body=None):
        fn = {
            "POST": context.request.post,
            "GET": context.request.get,
            "PATCH": context.request.patch,
        }[method]
        response = fn(
            env.base + path,
            data=json.dumps(body) if body is not None else None,
            headers=headers,
        )
        assert response.status in (200, 201), response.text()
        return response.json()

    return call


def no_null(page) -> None:
    text = page.locator("body").inner_text()
    assert not re.search(r"(^|\s)(null|undefined|NaN)(\s|$)", text), text


def test_share_and_earn_and_back_in_stock_in_the_browser(browser, env):
    kit.forget_schema()
    public.set_index(local._public_link_index)
    context, page, problems = signed_in_page(browser, env, width=390, height=860)
    call = caller(context, env)
    call(
        "POST", "/api/suite/business", {"name": "Clay Co"}
    )  # no payment link: nothing to print as "null"
    call(
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
    call(
        "POST",
        "/api/shop/products",
        {"name": "Mug", "price_cents": 2000, "status": "active"},
    )

    go(page, env, "shop?tab=referrals")
    page.get_by_label("Turn on share and earn").check()
    page.get_by_role("button", name="Save").click()
    page.wait_for_selector("text=Nobody has shared yet")
    store_url = env.base + call("POST", "/api/shop/storefront/link", {})["link"]["path"]

    shopper = context.new_page()
    shopper.on("pageerror", lambda e: problems.append(f"storefront pageerror: {e}"))
    shopper.goto(store_url)
    shopper.wait_for_selector(".pub-item")
    vase = shopper.locator(".pub-item", has_text="Vase")
    assert (
        vase.locator("input[type='number']").count() == 0
    )  # no quantity for something sold out
    vase.get_by_role("button", name="Email me when it's back").click()
    vase.get_by_label("Your email").fill("maya@example.com")
    vase.get_by_role("button", name="Tell me").click()
    shopper.wait_for_selector("text=We will email you when it is back")
    shopper.locator(".pub-item", has_text="Mug").get_by_role(
        "button", name="Add to cart"
    ).click()
    shopper.wait_for_selector(".pub-total")
    no_null(
        shopper
    )  # the cart once printed "null" for the missing pickup note and code row
    shopper.get_by_label("Your name", exact=True).fill("Ana Silva")
    shopper.get_by_label("Email", exact=True).fill("ana@example.com")
    shopper.get_by_role("button", name="Place order").click()
    shopper.wait_for_selector("text=Order placed")
    no_null(
        shopper
    )  # and the confirmation printed "null" when the shop has no payment details
    order_path = shopper.get_by_role("link", name="See your order").get_attribute(
        "href"
    )

    go(page, env, "shop?tab=products")
    page.wait_for_selector("text=1 waiting")
    order_id = call("GET", "/api/shop/orders")["orders"][0]["id"]
    call("POST", f"/api/shop/orders/{order_id}/paid", {})

    shopper.goto(env.base + order_path)
    card = shopper.locator(".pub-card", has_text="Share and earn")
    card.wait_for()
    code = card.locator(".pub-badge").inner_text()
    assert code.startswith("ANA-")

    friend = context.new_page()
    friend.goto(f"{store_url}?ref={code}")
    friend.wait_for_selector("text=A friend shared this shop with you")
    friend.locator(".pub-item", has_text="Mug").get_by_role(
        "button", name="Add to cart"
    ).click()
    friend.wait_for_selector("text=Friend's code")
    assert "-$2.00" in friend.locator(".pub-table").last.inner_text()
    friend.get_by_label("Your name", exact=True).fill("Bo Chen")
    friend.get_by_label("Email", exact=True).fill("bo@example.com")
    friend.get_by_role("button", name="Place order").click()
    friend.wait_for_selector("text=Order placed")
    friend_order = call("GET", "/api/shop/orders")["orders"][0]["id"]
    call("POST", f"/api/shop/orders/{friend_order}/paid", {})

    go(page, env, "shop?tab=referrals")
    page.wait_for_selector(".suite-table >> text=Ana Silva")
    assert "$5.00" in page.locator(".suite-kpis").inner_text()
    assert page.evaluate("() => document.documentElement.scrollWidth") <= 391
    assert [p for p in problems if "favicon" not in p] == []
    context.close()


def test_waiting_list_and_team_pay_in_the_browser(browser, env):
    kit.forget_schema()
    public.set_index(local._public_link_index)
    context, page, problems = signed_in_page(browser, env)
    call = caller(context, env)
    call("POST", "/api/suite/business", {"name": "Glow Studio", "timezone": "UTC"})
    ana = call(
        "POST",
        "/api/bookings/staff",
        {
            "name": "Ana",
            "commission_percent": 40,
            "hours": [
                {"weekday": d, "start": "09:00", "end": "10:00"} for d in range(7)
            ],
        },
    )["person"]
    cut = call(
        "POST",
        "/api/bookings/services",
        {"name": "Cut", "minutes": 60, "price_cents": 4500, "staff_ids": [ana["id"]]},
    )["service"]
    day = (datetime.now(timezone.utc).date() + timedelta(days=2)).isoformat()
    taken = call(
        "POST",
        "/api/bookings/bookings",
        {
            "service_id": cut["id"],
            "day": day,
            "time": "09:00",
            "name": "Ola",
            "email": "ola@example.com",
        },
    )["booking"]
    free = call(
        "POST",
        "/api/bookings/services",
        {
            "name": "Free chat",
            "minutes": 30,
            "price_cents": 0,
            "staff_ids": [ana["id"]],
        },
    )["service"]
    link = call("POST", "/api/bookings/page-link", {})["link"]

    visitor = context.new_page()
    visitor.on("pageerror", lambda e: problems.append(f"booking page pageerror: {e}"))
    visitor.goto(env.base + link["path"])
    visitor.locator(".pub-item", has_text="Cut").click()
    strip = visitor.locator('[aria-label="Day"] .pub-slot')
    strip.first.wait_for()
    # The strip starts on the business's today: day + 2 is the third button. Today reads "closed" once its only
    # hour is too soon to book (it once read "full" and the customer joined a list for a day nothing could free).
    full = strip.nth(2)
    assert full.inner_text().endswith(" · full"), full.inner_text()
    assert strip.filter(has_text=" · full").count() == 1
    full.click()
    form = visitor.locator(".pub-form", has_text="Join the waiting list")
    form.get_by_label("Name").fill("Maya Lopez")
    form.get_by_label("Email").fill("maya@example.com")
    visitor.get_by_role("button", name="Join the waiting list").click()
    visitor.wait_for_selector("text=You're on the waiting list")

    # a free service books without a payment box (it printed "null" before)
    visitor.goto(env.base + link["path"])
    visitor.locator(".pub-item", has_text="Free chat").click()
    visitor.wait_for_selector('[aria-label="Time"] .pub-slot')
    visitor.locator('[aria-label="Time"] .pub-slot').first.click()
    visitor.fill("input[autocomplete='name']", "Pat")
    visitor.fill("input[autocomplete='email']", "pat@example.com")
    visitor.get_by_role("button", name="Book", exact=True).click()
    visitor.wait_for_selector(".pub-notice.good")
    no_null(visitor)

    go(page, env, "bookings?tab=waitlist")
    page.wait_for_selector(".suite-table >> text=Maya Lopez")
    call(
        "POST", f"/api/bookings/bookings/{taken['id']}/cancel", {"reason": "Ana is ill"}
    )
    go(page, env, "bookings?tab=overview")
    go(page, env, "bookings?tab=waitlist")
    page.wait_for_selector("text=time free")
    page.get_by_role("button", name="Book", exact=True).first.click()
    page.wait_for_selector(".modal")
    assert page.locator(".modal input[name='name']").input_value() == "Maya Lopez"
    page.locator(".modal .btn", has_text="09:00").click()
    page.locator(".modal").get_by_role("button", name="Book", exact=True).click()
    until(
        page, lambda: not page.locator(".modal").count(), what="the booking to be made"
    )
    rows = call("GET", "/api/bookings/waitlist")["waitlist"]
    assert rows[0]["status"] == "booked"

    # the customer whose booking was cancelled sees the reason
    manage = call("POST", f"/api/bookings/bookings/{taken['id']}/link", {})["link"][
        "path"
    ]
    visitor.goto(env.base + manage)
    visitor.wait_for_selector("text=Reason: Ana is ill")
    no_null(visitor)

    made = call("GET", "/api/bookings/bookings")["bookings"]
    maya = next(b for b in made if b["customer_name"] == "Maya Lopez")
    call("POST", f"/api/bookings/bookings/{maya['id']}/pay", {"kind": "full"})
    call("PATCH", f"/api/bookings/bookings/{maya['id']}", {"status": "completed"})
    go(page, env, f"bookings?tab=pay&from={day}&to={day}")
    page.wait_for_selector("text=Commission to pay")
    assert "$18.00" in page.locator(".suite-kpis").inner_text()
    go(page, env, "bookings?tab=calendar")
    page.wait_for_selector(".card >> text=Book")
    assert not page.locator(".error-box").count()
    assert free["id"]
    assert [p for p in problems if "favicon" not in p] == []
    context.close()
