"""Courses & Events in the Aira frame, in a real browser: the app lives at /courses, "+ New" offers the app's own
creates and opens their forms (the address forgets ?new= at once), the search across the top suggests courses,
events, students, ticket orders and partners and opens the one chosen, Enter shows every match on a results page,
and the tabs of both sections are folders with icons in the left menu. On a phone the section's create button
stays in the page head and the lists fit the screen. No script errors along the way."""

# ruff: noqa: F811
from __future__ import annotations

from datetime import timedelta

import pytest

pytest.importorskip("patchright.sync_api")

import auto_apply_assistant as local  # noqa: E402
from aira import suite  # noqa: E402
from aira.suite import kit, public  # noqa: E402
from test_careers import env, stub  # noqa: E402, F401
from test_leads_ui import browser, signed_in_page, until  # noqa: E402, F401
from test_suite_service_ui import post  # noqa: E402

NEW_ITEMS = [
    "New course",
    "Draft a course with AI",
    "New event",
    "Enrol a student",
    "New partner",
]
COURSE_FOLDERS = [
    "Overview",
    "Courses",
    "Students",
    "Cohorts",
    "Announcements",
    "Reviews",
    "Partners",
    "Earnings",
]
EVENT_FOLDERS = [
    "Overview",
    "Events",
    "Orders",
    "Check-in",
    "Promo codes",
    "Sponsors",
    "Waitlist",
    "Partners",
    "Earnings",
]


@pytest.fixture(autouse=True)
def local_index():
    kit.forget_schema()
    public.set_index(local._public_link_index)
    previous = suite.gateway()
    suite.set_gateway(suite._NoGateway())
    yield
    suite.set_gateway(previous)


def seed(context, env):
    post(context, env, "/api/suite/business", {"name": "Blue Room", "timezone": "UTC"})
    course = post(
        context,
        env,
        "/api/academy/courses",
        {"title": "Guitar for beginners", "price_cents": 4900},
    )["course"]
    post(
        context,
        env,
        "/api/academy/students",
        {"course_id": course["id"], "name": "Ana Lopez", "email": "ana@example.com"},
    )
    post(
        context,
        env,
        "/api/academy/partners",
        {"name": "Priya Shah", "code": "PRIYA"},
    )
    event = post(
        context,
        env,
        "/api/events/events",
        {
            "title": "Friday Jazz Night",
            "starts_at": kit.iso(kit.utcnow() + timedelta(days=9)),
            "venue_name": "The Blue Room",
            "ticket_types": [{"name": "General", "price_cents": 2000, "quantity": 50}],
        },
    )["event"]
    post(
        context,
        env,
        f"/api/events/events/{event['id']}/status",
        {"status": "published"},
    )
    ticket = context.request.get(
        f"{env.base}/api/events/events/{event['id']}",
        headers={"X-Requested-With": "aira"},
    ).json()["event"]["ticket_types"][0]
    order = post(
        context,
        env,
        "/api/events/orders",
        {
            "event_id": event["id"],
            "items": [{"ticket_type_id": ticket["id"], "quantity": 2}],
            "name": "Ben Okafor",
            "email": "ben@example.com",
        },
    )["order"]
    return course, event, order


def open_app(page, env, hash_="academy"):
    page.goto(f"{env.base}/courses#/{hash_}")
    page.wait_for_load_state("networkidle")
    page.wait_for_selector("#main [role='tablist']")


def search(page, text):
    page.fill("#appSearchInput", "")
    page.click("#appSearchInput")
    page.keyboard.type(text)
    # The list for these words, not one still showing for the words before (it is drawn in one go).
    page.locator(
        "#appSearchList:not([hidden]) .shell-search-option", has_text=f"for “{text}”"
    ).wait_for()


def choose(page, text):
    page.locator(
        f"#appSearchList .shell-search-option:has-text('{text}')"
    ).first.click()


def heading(page):
    return page.locator("#main .page-head h1").inner_text()


def test_courses_and_events_new_search_and_folders_in_the_frame(browser, env):
    context, page, problems = signed_in_page(browser, env)
    course, event, order = seed(context, env)

    # The app's own address, its home, and a page head named after the open folder.
    open_app(page, env)
    assert "/courses#/academy" in page.url
    assert heading(page) == "Courses overview"
    assert page.title().startswith("Courses overview · Courses & Events")

    # The left menu shows the tabs as folders with icons; a folder opens its tab.
    folders = page.locator(
        "#primaryNav .shell-subnav[data-section='academy'] .shell-sublink"
    )
    assert [t.strip() for t in folders.all_inner_texts()] == COURSE_FOLDERS
    assert page.locator("#primaryNav .shell-sublink .shell-subdot").count() == 0
    page.click("#primaryNav .shell-sublink[data-tab='students']")
    until(page, lambda: "tab=students" in page.url, what="the Students folder")
    page.wait_for_selector(
        "#main [role='tab'][aria-selected='true']:has-text('Students')"
    )
    assert heading(page) == "Students"
    assert (
        page.locator("#primaryNav .shell-sublink[aria-current='page']")
        .inner_text()
        .strip()
        == "Students"
    )
    page.click("#primaryNav a[data-section='events']")
    page.wait_for_selector("#primaryNav .shell-subnav[data-section='events']")
    events_folders = page.locator(
        "#primaryNav .shell-subnav[data-section='events'] .shell-sublink"
    )
    assert [t.strip() for t in events_folders.all_inner_texts()] == EVENT_FOLDERS
    page.click(
        "#primaryNav .shell-subnav[data-section='events'] .shell-sublink[data-tab='promos']"
    )
    until(
        page, lambda: "#/events?tab=promos" in page.url, what="the Promo codes folder"
    )
    page.wait_for_selector(
        "#main [role='tab'][aria-selected='true']:has-text('Promo codes')"
    )
    assert heading(page) == "Promo codes"

    # "+ New" lists the app's creates; each opens its form and the address forgets ?new= at once.
    open_app(page, env, "academy?tab=overview")
    page.click(".shell-new")
    page.wait_for_selector(".shell-new-menu")
    assert [
        t.strip()
        for t in page.locator(".shell-new-menu [role='menuitem']").all_inner_texts()
    ] == NEW_ITEMS
    page.click(".shell-new-menu [role='menuitem']:has-text('New course')")
    page.wait_for_selector(".modal:has-text('New course') [name=title]")
    assert "tab=courses" in page.url and "new=" not in page.url
    page.fill(".modal [name=title]", "Ukulele in a weekend")
    page.locator(".modal").get_by_role("button", name="Create course").click()
    until(page, lambda: heading(page) == "Ukulele in a weekend", what="the new course")
    assert "course=" in page.url

    for label, tab, title, field in [
        ("Draft a course with AI", "courses", "Draft a course with AI", "topic"),
        ("New event", "events", "New event", "title"),
        ("Enrol a student", "students", "Enrol a student", "email"),
        ("New partner", "partners", "New partner", "name"),
    ]:
        page.click(".shell-new")
        page.click(f".shell-new-menu [role='menuitem']:has-text('{label}')")
        page.wait_for_selector(f".modal:has-text('{title}') [name={field}]")
        assert f"tab={tab}" in page.url and "new=" not in page.url, page.url
        page.keyboard.press("Escape")
        page.wait_for_selector(".modal", state="detached")
    # Back does not open a form again.
    page.go_back()
    page.wait_for_load_state("networkidle")
    page.wait_for_timeout(400)
    assert page.locator(".modal").count() == 0

    # A new partner made from "+ New" opens in its panel.
    page.click(".shell-new")
    page.click(".shell-new-menu [role='menuitem']:has-text('New partner')")
    page.fill(".modal [name=name]", "Campus Club")
    page.locator(".modal").get_by_role("button", name="Add partner").click()
    page.wait_for_selector(".drawer:has-text('Campus Club')")
    assert "tab=partners" in page.url and "open=" not in page.url
    page.keyboard.press("Escape")

    # The search across the top: the app's own records, each opening itself.
    open_app(page, env, "academy?tab=cohorts")
    assert (
        page.get_attribute("#appSearchInput", "placeholder")
        == "Search courses, events, students and orders"
    )
    search(page, "guitar")
    texts = page.locator("#appSearchList .shell-search-option").all_inner_texts()
    assert any("Guitar for beginners" in t and "Course" in t for t in texts), texts
    choose(page, "Guitar for beginners")
    until(page, lambda: heading(page) == "Guitar for beginners", what="the course")
    assert f"course={course['id']}" in page.url

    search(page, "ana@")
    choose(page, "Ana Lopez")
    page.wait_for_selector(".drawer:has-text('ana@example.com')")
    assert "tab=students" in page.url and "open=" not in page.url
    page.keyboard.press("Escape")

    search(page, order["reference"])
    texts = page.locator("#appSearchList .shell-search-option").all_inner_texts()
    assert any("Ben Okafor" in t and "Ticket order" in t for t in texts), texts
    choose(page, "Ben Okafor")
    page.wait_for_selector(f".drawer:has-text('Order {order['reference']}')")
    assert "#/events?tab=orders" in page.url and "open=" not in page.url
    page.keyboard.press("Escape")

    search(page, "priya")
    choose(page, "Priya Shah")
    page.wait_for_selector(".drawer:has-text('PRIYA')")
    assert "tab=partners" in page.url and "open=" not in page.url
    page.keyboard.press("Escape")

    search(page, "blue room")
    choose(page, "Friday Jazz Night")
    until(page, lambda: heading(page) == "Friday Jazz Night", what="the event")
    assert f"event={event['id']}" in page.url

    # Enter: every match on a results page, each row opening its record.
    search(page, "jazz")
    page.keyboard.press("Enter")
    until(
        page,
        lambda: "tab=search" in page.url and "q=jazz" in page.url,
        what="the results",
    )
    page.wait_for_selector("#main .page-head h1:has-text('Results for “jazz”')")
    page.wait_for_selector("#main .suite-section:has-text('Events (1)')")
    page.click("#main tr.suite-row:has-text('Friday Jazz Night')")
    until(
        page,
        lambda: heading(page) == "Friday Jazz Night",
        what="the event from the results",
    )
    search(page, "nothing-like-this")
    page.keyboard.press("Enter")
    page.wait_for_selector("#main .empty:has-text('Nothing matches')")

    assert not problems, problems
    context.close()


def test_courses_and_events_on_a_phone_keep_new_and_fit(browser, env):
    context, page, problems = signed_in_page(browser, env, width=390, height=844)
    seed(context, env)
    for target in (
        "academy?tab=overview",
        "academy?tab=courses",
        "academy?tab=students",
        "academy?tab=partners",
        "events?tab=events",
        "events?tab=orders",
        "academy?tab=earnings",
    ):
        open_app(page, env, target)
        page.wait_for_timeout(300)
        assert not page.evaluate(
            "() => document.documentElement.scrollWidth > window.innerWidth + 2"
        ), target
        wide = page.evaluate(
            "() => [...document.querySelectorAll('#main .table-wrap')].some((w) => w.scrollWidth > w.clientWidth + 2)"
        )
        assert not wide, f"a list scrolls sideways on {target}"
    # The facts of the folded columns ride under the name.
    open_app(page, env, "events?tab=orders")
    assert (
        "Friday Jazz Night" in page.locator("#main .xp-phone-only").first.inner_text()
    )
    # The menu (and its "+ New") is folded away on a phone: the page head keeps the section's create.
    open_app(page, env, "academy?tab=students")
    new = page.locator("#main .page-head").get_by_role("button", name="New course")
    assert new.is_visible()
    new.click()
    page.wait_for_selector(".modal:has-text('New course') [name=title]")
    assert not problems, problems
    context.close()
