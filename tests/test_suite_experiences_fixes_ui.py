"""Courses & Events in a real browser, for the tester's findings: a mistyped partner code in 'Enrol a student' and
'Sell tickets' is refused in the form, a partner whose only referrals were refunded offers 'Switch off' (and says
so once done), the review form switches to update mode after the first send, the catalog prices an event from the
tickets on sale now, and the saved partner code is kept per business. Screenshots go to ``$XP_SHOTS`` when set."""

# ruff: noqa: F811
from __future__ import annotations

import json
import os
import re
from datetime import timedelta
from pathlib import Path

import pytest

patchright = pytest.importorskip("patchright.sync_api")

import auto_apply_assistant as local  # noqa: E402
from aira import suite  # noqa: E402
from aira.suite import kit, public  # noqa: E402
from test_careers import PASSWORD, env, stub  # noqa: E402, F401
from test_leads_ui import browser, until  # noqa: E402, F401


@pytest.fixture(autouse=True)
def local_index():
    kit.forget_schema()
    public.set_index(local._public_link_index)
    previous = suite.gateway()
    suite.set_gateway(suite._NoGateway())
    yield
    suite.set_gateway(previous)


def _shot(page, name):
    folder = os.environ.get("XP_SHOTS")
    if folder:
        Path(folder).mkdir(parents=True, exist_ok=True)
        page.screenshot(path=str(Path(folder) / f"{name}.png"), full_page=True)


def _watch(page, problems):
    page.set_default_timeout(60_000)
    page.on(
        "console",
        lambda m: (
            problems.append(f"console: {m.text}")
            if m.type == "error" and "status of 400" not in m.text
            else None
        ),
    )
    page.on("pageerror", lambda e: problems.append(f"pageerror: {e}"))


def _later(**delta):
    return kit.iso(kit.utcnow() + timedelta(**delta))


def _overflow(page):
    return page.evaluate(
        "() => document.documentElement.scrollWidth - document.documentElement.clientWidth"
    )


def test_partner_codes_reviews_and_catalog_prices_in_the_browser(browser, env):
    context = browser.new_context(viewport={"width": 1366, "height": 900})
    headers = {
        "Content-Type": "application/json",
        "X-Requested-With": "aira",
        "Origin": env.base,
    }

    def call(method, path, body=None):
        response = context.request.fetch(
            env.base + path,
            method=method,
            data=json.dumps(body) if body is not None else None,
            headers=headers,
        )
        assert response.status in (200, 201), response.text()
        return response.json()

    call(
        "POST",
        "/api/auth/register",
        {"email": "xp-fix@example.com", "password": PASSWORD},
    )
    call("POST", "/api/suite/business", {"name": "Blue Room School"})
    course = call(
        "POST", "/api/academy/courses", {"title": "Guitar 101", "price_cents": 0}
    )["course"]
    module = call(
        "POST", f"/api/academy/courses/{course['id']}/modules", {"title": "Chords"}
    )["module"]
    call(
        "POST",
        f"/api/academy/modules/{module['id']}/lessons",
        {"title": "G major", "body": "Three fingers."},
    )
    call("POST", f"/api/academy/courses/{course['id']}/status", {"status": "published"})
    course_link = call("GET", f"/api/academy/courses/{course['id']}")["link"]
    priya = call("POST", "/api/academy/partners", {"name": "Priya", "code": "PRIYA"})[
        "partner"
    ]
    soon = call(
        "POST",
        "/api/events/events",
        {
            "title": "Future Sale",
            "starts_at": _later(days=9),
            "ticket_types": [
                {
                    "name": "General",
                    "price_cents": 3000,
                    "quantity": 9,
                    "sales_start": _later(days=5),
                }
            ],
        },
    )["event"]
    mixed = call(
        "POST",
        "/api/events/events",
        {
            "title": "Mixed Night",
            "starts_at": _later(days=8),
            "ticket_types": [
                {"name": "Seat", "price_cents": 100000, "quantity": 9},
                {"name": "Free", "price_cents": 0, "quantity": 9},
            ],
        },
    )["event"]
    for event in (soon, mixed):
        call(
            "POST", f"/api/events/events/{event['id']}/status", {"status": "published"}
        )

    page = context.new_page()
    problems: list[str] = []
    _watch(page, problems)

    # 'Enrol a student' with a mistyped partner code: refused in the form, nothing enrolled
    page.goto(f"{env.base}/#/academy?tab=students")
    page.get_by_role("button", name="Enrol a student").first.click()
    modal = page.locator(".modal")
    modal.get_by_label("Name").fill("Typo Ref")
    modal.get_by_label("Email").fill("typo@example.com")
    modal.get_by_label("Partner code").fill("PRIYAA")
    modal.get_by_role("button", name="Enrol").click()
    modal.get_by_text("No partner has the code PRIYAA").wait_for()
    _shot(page, "enrol-typo")
    assert call("GET", "/api/academy/students")["students"] == []
    modal.get_by_label("Partner code").fill("priya")
    modal.get_by_role("button", name="Enrol").click()
    page.locator(".modal").wait_for(state="detached")
    assert len(call("GET", f"/api/academy/partners/{priya['id']}")["referrals"]) == 1

    # 'Sell tickets' with a mistyped code
    page.goto(f"{env.base}/#/events?tab=events&event={mixed['id']}")
    page.get_by_role("button", name="Sell tickets").first.click()
    modal = page.locator(".modal")
    modal.get_by_label("Buyer name").fill("Door")
    modal.get_by_label("Partner code").fill("PRIYAX")
    modal.get_by_role("button", name="Create order").click()
    modal.get_by_text("No partner has the code PRIYAX").wait_for()
    _shot(page, "sell-typo")
    modal.get_by_role("button", name="Cancel").click()
    assert call("GET", "/api/events/orders")["orders"] == []

    # a partner whose only referral was refunded: the drawer offers Switch off and says so
    voidy = call("POST", "/api/academy/partners", {"name": "Voidy", "code": "VOIDY"})[
        "partner"
    ]
    paid_course = call(
        "POST", "/api/academy/courses", {"title": "Paid Course", "price_cents": 5000}
    )["course"]
    enrolled = call(
        "POST",
        "/api/academy/students",
        {
            "course_id": paid_course["id"],
            "name": "Rex",
            "email": "rex@example.com",
            "ref": "VOIDY",
            "paid": True,
        },
    )
    call("POST", f"/api/academy/students/{enrolled['enrolment']['id']}/refund", {})
    page.goto(f"{env.base}/#/events?tab=partners")
    page.get_by_text("Voidy", exact=True).first.click()
    drawer = page.locator(".drawer")
    drawer.get_by_role("button", name="Switch off").wait_for()
    assert drawer.get_by_role("button", name="Delete", exact=True).count() == 0
    drawer.get_by_role("button", name="Switch off").click()
    page.locator(".modal").get_by_role("button", name="Switch off").click()
    page.get_by_text("Voidy is switched off. Their history stays.").wait_for()
    assert (
        call("GET", f"/api/academy/partners/{voidy['id']}")["partner"]["active"]
        is False
    )

    # the review form switches to update mode after the first send (phone width)
    visitor = browser.new_context(viewport={"width": 390, "height": 844})
    student = visitor.new_page()
    visitor_problems: list[str] = []
    _watch(student, visitor_problems)
    student.goto(f"{env.base}{course_link['path']}?ref=PRIYA")
    student.get_by_label("Your name").fill("Ana Lopez")
    student.get_by_label("Email").fill("ana@example.com")
    student.get_by_role("button", name="Enrol now").click()
    student.get_by_role("link", name="Start learning").click()
    student.get_by_role("button", name="Open lesson").first.click()
    student.get_by_role("button", name="Mark as complete").click()
    student.get_by_role("button", name="Send my review").wait_for()
    student.locator("label:has-text('4')").first.click()
    student.get_by_role("button", name="Send my review").click()
    student.get_by_text("Thank you for your review!").wait_for()
    student.get_by_role("button", name="Update my review").wait_for()
    assert student.locator("h2:has-text('Your review')").count() == 1
    student.locator("label:has-text('5')").first.click()
    student.get_by_role("button", name="Update my review").click()
    student.get_by_text("Thanks, your review is updated.").wait_for()
    _shot(student, "review-updated-390")
    assert _overflow(student) <= 0
    assert call("GET", "/api/academy/reviews")["reviews"][0]["rating"] == 5
    # the partner code is kept under this business's own key
    keys = student.evaluate("() => Object.keys(sessionStorage)")
    assert "aira.ref" not in keys and any(
        re.fullmatch(r"aira\.ref\.[0-9a-f]{16}", k) for k in keys
    )

    # the catalog prices events from the tickets on sale now
    catalog_link = call("POST", "/api/academy/catalog/link", {})["link"]
    student.goto(env.base + catalog_link["path"])
    student.get_by_text("Future Sale").wait_for()
    card = student.locator("a.pub-item", has_text="Future Sale")
    assert "Tickets soon" in card.inner_text() and "$30.00" not in card.inner_text()
    mixed_card = student.locator("a.pub-item", has_text="Mixed Night")
    assert "From $1,000.00 · free tickets too" in mixed_card.inner_text()
    _shot(student, "catalog-390")
    assert _overflow(student) <= 0

    assert visitor_problems == [], visitor_problems
    assert problems == [], problems
    visitor.close()
    context.close()
