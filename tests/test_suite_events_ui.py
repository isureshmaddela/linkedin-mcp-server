"""Aira Events in a real browser against the local server: every tab of the organiser's screen renders without
errors, a customer buys a free ticket on the public event page and lands on their tickets page, and the organiser
checks the code in at the door."""

# ruff: noqa: F811
from __future__ import annotations

import json
from datetime import timedelta

import pytest

patchright = pytest.importorskip("patchright.sync_api")

import auto_apply_assistant as local  # noqa: E402
from aira.suite import kit, public  # noqa: E402
from test_careers import env, stub  # noqa: E402, F401
from test_leads_ui import browser, go, signed_in_page, until  # noqa: E402, F401


@pytest.fixture(autouse=True)
def local_index():
    kit.forget_schema()
    public.set_index(local._public_link_index)


def _post(context, env, path, body):
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


def test_the_organiser_screens_render_and_a_customer_buys_and_checks_in(browser, env):
    context, page, problems = signed_in_page(browser, env)
    starts = kit.iso(kit.utcnow() + timedelta(days=6))
    event = _post(
        context,
        env,
        "/api/events/events",
        {
            "title": "Rooftop Cinema",
            "venue_name": "Skyline Roof",
            "starts_at": starts,
            "ticket_types": [
                {"name": "Deckchair", "price_cents": 0, "quantity": 40},
                {"name": "Sofa for two", "price_cents": 3000, "quantity": 5},
            ],
        },
    )["event"]
    published = _post(
        context,
        env,
        f"/api/events/events/{event['id']}/status",
        {"status": "published"},
    )["event"]
    _post(
        context,
        env,
        "/api/events/promos",
        {"code": "FRIENDS", "kind": "percent", "value": 10},
    )
    _post(
        context,
        env,
        "/api/events/sponsors",
        {"name": "Popcorn Co", "amount_cents": 25000, "status": "agreed"},
    )

    go(page, env, "events")
    page.wait_for_selector("text=Upcoming events")
    page.wait_for_selector("text=Rooftop Cinema")
    for tab, marker in (
        (f"events?tab=events&event={event['id']}", "text=Ticket types"),
        ("events?tab=events", "text=Rooftop Cinema"),
        ("events?tab=orders", "text=Confirmed revenue"),
        (f"events?tab=checkin&event={event['id']}", "text=At the door"),
        ("events?tab=promos", "text=FRIENDS"),
        ("events?tab=sponsors", "text=Popcorn Co"),
        ("events?tab=waitlist", "text=Nobody is waiting"),
    ):
        go(page, env, tab)
        page.wait_for_selector(marker)
    go(page, env, f"events?tab=events&event={event['id']}")
    page.wait_for_selector(".suite-share-url")
    assert "/p/" in page.locator(".suite-share-url").inner_text()

    visitor = browser.new_context()
    shop = visitor.new_page()
    errors: list[str] = []
    shop.on("pageerror", lambda e: errors.append(str(e)))
    shop.on("console", lambda m: errors.append(m.text) if m.type == "error" else None)
    shop.goto(env.base + published["link"]["path"])
    shop.wait_for_selector("h1:has-text('Rooftop Cinema')")
    shop.locator("select[aria-label='Number of Deckchair tickets']").select_option("2")
    until(
        shop,
        lambda: shop.locator("button:has-text('Get 2 free tickets')").count() == 1,
        what="the free order button",
    )
    shop.locator("input[autocomplete='name']").first.fill("Ana Viewer")
    shop.locator("input[autocomplete='email']").first.fill("ana@example.com")
    shop.locator("button:has-text('Get 2 free tickets')").click()
    shop.wait_for_selector(".pub-code")
    assert shop.locator(".pub-code").count() == 2
    code = shop.locator(".pub-code").first.inner_text().strip()
    assert "You're going!" in shop.inner_text("main")
    assert not errors, errors

    go(page, env, f"events?tab=checkin&event={event['id']}")
    page.wait_for_selector("text=At the door")
    page.locator("input[placeholder='ABCD-2345']").fill(code)
    page.locator("button[type='submit']:has-text('Check in')").click()
    page.wait_for_selector("text=Welcome in!")
    page.locator("input[placeholder='ABCD-2345']").fill(code)
    page.locator("button[type='submit']:has-text('Check in')").click()
    page.wait_for_selector("text=Already checked in.")
    assert not problems, problems
    visitor.close()
    context.close()
