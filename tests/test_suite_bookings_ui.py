"""Bookings in a real browser: every tab renders without errors, and a customer books on the public page."""

# ruff: noqa: F811
from __future__ import annotations

import json

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


TABS = (
    "overview",
    "calendar",
    "appointments",
    "services",
    "staff",
    "customers",
    "packages",
    "settings",
)


def post(context, env, path, body):
    response = context.request.post(
        env.base + path,
        data=json.dumps(body),
        headers={
            "Content-Type": "application/json",
            "X-Requested-With": "aira",
            "Origin": env.base,
        },
    )
    assert response.status in (200, 201), response.text()
    return response.json()


def test_bookings_tabs_and_a_customer_booking_in_the_browser(browser, env):
    kit.forget_schema()
    public.set_index(local._public_link_index)
    context, page, problems = signed_in_page(browser, env)
    post(
        context,
        env,
        "/api/suite/business",
        {
            "name": "Glow Studio",
            "email": "owner@glow.example",
            "timezone": "UTC",
            "payment_link": "https://pay.example/glow",
        },
    )
    person = post(
        context,
        env,
        "/api/bookings/staff",
        {
            "name": "Ana",
            "hours": [
                {"weekday": d, "start": "09:00", "end": "17:00"} for d in range(7)
            ],
        },
    )["person"]
    post(
        context,
        env,
        "/api/bookings/services",
        {
            "name": "Cut and style",
            "category": "Hair",
            "minutes": 60,
            "price_cents": 4500,
            "deposit_cents": 1000,
            "staff_ids": [person["id"]],
        },
    )
    post(
        context,
        env,
        "/api/bookings/packages",
        {"name": "5 cuts", "sessions": 5, "price_cents": 20000},
    )

    for tab in TABS:
        go(page, env, f"bookings?tab={tab}")
        page.wait_for_selector(".page-head h1")
        until(
            page,
            lambda: not page.locator(".skeleton-list").count(),
            what=f"the {tab} tab to load",
        )
        assert page.locator(".page-head h1").inner_text() == "Bookings"
        assert not page.locator(".error-box").count(), tab
    go(page, env, "bookings?tab=overview")
    page.get_by_role("button", name="Create link").click()
    page.wait_for_selector(".suite-share-url")
    url = page.locator(".suite-share-url").inner_text()
    assert "/p/" in url

    visitor = context.new_page()
    visitor.on("pageerror", lambda e: problems.append(f"public pageerror: {e}"))
    visitor.goto(url)
    visitor.wait_for_selector(".pub-item")
    assert "Glow Studio" in visitor.locator("#pubName").inner_text()
    visitor.locator(".pub-item").first.click()
    visitor.wait_for_selector('[aria-label="Time"] .pub-slot')
    visitor.locator('[aria-label="Time"] .pub-slot').first.click()
    visitor.wait_for_selector("input[autocomplete='name']")
    visitor.fill("input[autocomplete='name']", "Maya Lopez")
    visitor.fill("input[autocomplete='email']", "maya@example.com")
    visitor.get_by_role("button", name="Book (deposit $10.00)").click()
    visitor.wait_for_selector(".pub-notice.good")
    assert "You're booked" in visitor.locator(".pub-notice.good").inner_text()
    assert visitor.get_by_role("link", name="Pay $10.00").count() == 1
    manage = visitor.get_by_role("link", name="Manage your booking").get_attribute(
        "href"
    )
    visitor.goto(manage)
    visitor.wait_for_selector("text=Need to change it?")
    assert visitor.locator(".pub-badge").first.inner_text() == "confirmed"

    go(page, env, "bookings?tab=appointments")
    page.wait_for_selector(".suite-table tbody tr")
    page.locator(".suite-table tbody tr").first.click()
    page.wait_for_selector(".drawer")
    page.get_by_role("button", name="Deposit paid ($10.00)").click()
    until(
        page,
        lambda: "Paid $10.00" in page.locator(".drawer").inner_text(),
        what="the payment to show",
    )
    assert not problems, problems
