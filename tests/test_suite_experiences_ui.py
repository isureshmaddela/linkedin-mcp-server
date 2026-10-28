"""Courses & Events in a real browser: event times are typed and shown in the business's time zone whatever the
computer's zone is, #/experiences opens the app, a public form's error clears once fixed and nothing scrolls
sideways on a phone, and the Partners, Earnings and Reviews screens render without errors."""

# ruff: noqa: F811
from __future__ import annotations

import json
import re
from datetime import timedelta

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


def _watch(page, problems):
    page.set_default_timeout(60_000)
    page.on(
        "console",
        lambda m: problems.append(f"console: {m.text}") if m.type == "error" else None,
    )
    page.on("pageerror", lambda e: problems.append(f"pageerror: {e}"))


def _owner(browser, env, *, timezone_id="America/New_York"):
    context = browser.new_context(
        viewport={"width": 1366, "height": 900}, timezone_id=timezone_id
    )
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
        {"email": "xp-ui@example.com", "password": PASSWORD},
    )
    page = context.new_page()
    problems: list[str] = []
    _watch(page, problems)
    return context, page, call, problems


def _overflow(page):
    return page.evaluate(
        "() => document.documentElement.scrollWidth - document.documentElement.clientWidth"
    )


def test_event_times_follow_the_business_time_zone_and_the_app_screens_render(
    browser, env
):
    context, page, call, problems = _owner(browser, env)
    call(
        "POST",
        "/api/suite/business",
        {"name": "Blue Room", "timezone": "Asia/Kolkata", "currency": "INR"},
    )

    page.goto(f"{env.base}/#/experiences")
    page.wait_for_selector(".page-head h1:has-text('Courses overview')")
    assert page.url.endswith("#/academy")

    page.goto(f"{env.base}/#/events?tab=events")
    page.locator("#main .xp-toolbar").get_by_role("button", name="New event").click()
    page.get_by_label("Title *").fill("Friday Jazz Night")
    page.get_by_label("Starts *").fill("2026-12-05T19:30")
    page.locator(".modal").get_by_role("button", name="Create event").click()
    page.wait_for_selector(".page-head h1:has-text('Friday Jazz Night')")
    event = call("GET", "/api/events/events?scope=")["events"][0]
    # 19:30 in Kolkata, typed on a computer set to New York
    assert event["starts_at"] == "2026-12-05T14:00:00Z"
    page.get_by_role("button", name="Edit", exact=True).first.click()
    assert page.get_by_label("Starts *").input_value() == "2026-12-05T19:30"
    page.locator(".modal").get_by_role("button", name="Cancel").click()

    for tab, marker in (
        ("academy?tab=partners", "text=No partners yet"),
        ("academy?tab=earnings", "text=Nothing earned yet"),
        ("academy?tab=reviews", "text=No reviews yet"),
        ("academy?tab=overview", "text=Your catalog page"),
        ("events?tab=partners", "text=No partners yet"),
        ("events?tab=earnings", "text=Payment log"),
    ):
        page.goto(f"{env.base}/#/{tab}")
        page.wait_for_selector(marker)
    assert problems == [], problems
    context.close()


def test_a_public_form_error_clears_and_a_phone_never_scrolls_sideways(browser, env):
    context, page, call, problems = _owner(browser, env)
    call("POST", "/api/suite/business", {"name": "Code School"})
    course = call(
        "POST", "/api/academy/courses", {"title": "Python Basics", "price_cents": 4900}
    )["course"]
    module = call(
        "POST", f"/api/academy/courses/{course['id']}/modules", {"title": "Start"}
    )["module"]
    call(
        "POST",
        f"/api/academy/modules/{module['id']}/lessons",
        {"title": "Hello", "body": "Print."},
    )
    call("POST", f"/api/academy/courses/{course['id']}/status", {"status": "published"})
    link = call("GET", f"/api/academy/courses/{course['id']}")["link"]
    assert link, "publishing makes the course page"
    partner = call(
        "POST", "/api/academy/partners", {"name": "Priya", "commission_percent": 20}
    )["partner"]
    event = call(
        "POST",
        "/api/events/events",
        {
            "title": "Open Mic",
            "starts_at": kit.iso(kit.utcnow() + timedelta(days=5)),
            "ticket_types": [{"name": "Seat", "price_cents": 0, "quantity": 9}],
        },
    )["event"]
    call("POST", f"/api/events/events/{event['id']}/status", {"status": "published"})

    visitor = browser.new_context(viewport={"width": 390, "height": 844})
    student = visitor.new_page()
    visitor_problems: list[str] = []
    _watch(student, visitor_problems)
    student.goto(f"{env.base}{link['path']}?ref={partner['code']}")
    student.get_by_label("Your name").fill("Ravi Kumar")
    student.get_by_label("Email").fill("not-an-email")
    student.get_by_role("button", name=re.compile("Enrol for")).click()
    student.wait_for_selector(".pub-notice.bad:has-text('Enter your email address')")
    student.get_by_label("Email").fill("ravi@example.com")
    student.get_by_role("button", name=re.compile("Enrol for")).click()
    student.wait_for_selector("text=Your course page:")
    assert student.locator(".pub-notice.bad:visible").count() == 0
    assert _overflow(student) <= 0
    referral = call("GET", f"/api/academy/partners/{partner['id']}")["referrals"]
    assert len(referral) == 1 and referral[0]["status"] == "pending"

    partner_link = call("POST", f"/api/academy/partners/{partner['id']}/link", {})[
        "link"
    ]
    student.goto(env.base + partner_link["path"])
    student.wait_for_selector("h1:has-text('Hi Priya')")
    assert student.get_by_text("Open Mic").count() >= 1
    assert _overflow(student) <= 0
    assert visitor_problems == [], visitor_problems
    assert problems == [], problems
    visitor.close()
    context.close()
