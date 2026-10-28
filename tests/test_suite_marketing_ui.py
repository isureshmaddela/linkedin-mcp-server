"""Marketing in a real browser against the local server: every tab renders without errors, the calendar shows a
scheduled post, a visitor fills in a shared lead form, and a tracked link sends the visitor on."""

# ruff: noqa: F811
from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

patchright = pytest.importorskip("patchright.sync_api")

import auto_apply_assistant as local  # noqa: E402
from aira import suite  # noqa: E402
from aira.suite import kit, public  # noqa: E402
from test_careers import env, stub  # noqa: E402, F401
from test_leads_ui import browser, go, signed_in_page, until  # noqa: E402, F401

SHOTS = (
    Path(os.environ["MARKETING_SHOTS"]) if os.environ.get("MARKETING_SHOTS") else None
)


def shot(page, name):
    if SHOTS is not None:
        SHOTS.mkdir(parents=True, exist_ok=True)
        page.screenshot(path=str(SHOTS / f"{name}.png"), full_page=True)


# Each tab and the page title it shows (the app's own name is in the bar above the page).
TABS = {
    "overview": "Overview",
    "calendar": "Calendar",
    "campaigns": "Campaigns",
    "links": "Links",
    "forms": "Forms and leads",
    "bio": "Bio page",
    "subscribers": "Subscribers",
    "newsletters": "Newsletters",
}


class _NoModel:
    def model(self, prompt):
        return None


@pytest.fixture(autouse=True)
def local_index():
    kit.forget_schema()
    public.set_index(local._public_link_index)
    previous = suite.gateway()
    suite.set_gateway(
        _NoModel()
    )  # a real model may run on this machine: tests never call it
    yield
    suite.set_gateway(previous)


def call(context, env, method, path, body=None):
    response = context.request.fetch(
        env.base + path,
        method=method,
        data=json.dumps(body) if body is not None else None,
        headers={
            "Content-Type": "application/json",
            "X-Requested-With": "aira",
            "Origin": env.base,
        },
    )
    assert response.status < 300, (path, response.status, response.text())
    return response.json()


def test_every_tab_renders_and_the_calendar_shows_a_post(browser, env):
    context, page, problems = signed_in_page(browser, env)
    when = kit.now()[:8] + "15T10:00:00Z"
    call(
        context,
        env,
        "POST",
        "/api/marketing/posts",
        {
            "channel": "instagram",
            "title": "Autumn looks",
            "body": "Three looks",
            "status": "scheduled",
            "scheduled_at": when,
        },
    )
    for tab, title in TABS.items():
        go(page, env, f"marketing?tab={tab}")
        page.wait_for_selector(".page-head h1")
        until(
            page,
            lambda: page.locator("#main .skeleton-list").count() == 0,
            what=f"the {tab} tab to load",
        )
        assert page.locator(".page-head h1").inner_text() == title
        assert page.locator("#main .error-box").count() == 0, tab
        shot(page, f"tab-{tab}")
    go(page, env, "marketing?tab=forms")
    page.get_by_role("button", name="New form").first.click()
    page.wait_for_selector(".modal input[name=title]")
    page.fill(".modal input[name=title]", "Free consultation")
    page.locator(".modal .actions button", has_text="Save").click()
    page.wait_for_selector("#main td strong:text('Free consultation')")
    go(page, env, "marketing?tab=calendar")
    page.wait_for_selector("button[title='Autumn looks']")
    assert "Autumn looks" in page.locator("button[title='Autumn looks']").inner_text()
    page.click("button[title='Autumn looks']")
    page.wait_for_selector(".drawer h2")
    assert page.locator(".drawer h2").inner_text() == "Autumn looks"
    assert not problems, problems
    context.close()


def test_a_visitor_fills_in_a_shared_form_and_a_tracked_link_sends_them_on(
    browser, env
):
    context, page, problems = signed_in_page(browser, env)
    call(
        context,
        env,
        "POST",
        "/api/suite/business",
        {"name": "Glow Studio", "accent": "#aa3366"},
    )
    form = call(
        context,
        env,
        "POST",
        "/api/marketing/forms",
        {"title": "Book a consult", "tag": "consult"},
    )["form"]
    shared = call(context, env, "POST", f"/api/marketing/forms/{form['id']}/link", {})[
        "public"
    ]
    link = call(
        context,
        env,
        "POST",
        "/api/marketing/links",
        {"destination": "https://glow.example/book", "title": "Bio booking"},
    )["link"]

    visitor_context = browser.new_context(viewport={"width": 390, "height": 844})
    visitor = visitor_context.new_page()
    visitor_problems: list[str] = []
    visitor.on("pageerror", lambda e: visitor_problems.append(str(e)))
    visitor.goto(env.base + shared["path"])
    visitor.wait_for_selector("form.pub-form")
    assert visitor.locator("h1").inner_text() == "Book a consult"
    assert visitor.locator("#pubName").inner_text() == "Glow Studio"
    assert "null" not in visitor.locator("main").inner_text()
    visitor.fill("input[name=name]", "Ana")
    visitor.fill("input[name=email]", "ana@example.com")
    visitor.check("#pubConsent")
    shot(visitor, "public-form")
    visitor.click("form.pub-form button.pub-btn")
    visitor.wait_for_selector(".pub-notice.good")
    subscribers = call(context, env, "GET", "/api/marketing/subscribers")["subscribers"]
    assert [s["email"] for s in subscribers] == ["ana@example.com"]

    visitor.route(
        "https://glow.example/**",
        lambda route: route.fulfill(
            status=200, body="<h1>Landed</h1>", content_type="text/html"
        ),
    )
    visitor.goto(env.base + link["public"]["path"])
    visitor.wait_for_url("https://glow.example/**")
    assert "utm_source=aira" in visitor.url
    assert (
        call(context, env, "GET", f"/api/marketing/links/{link['id']}/clicks")["link"][
            "clicks"
        ]
        == 1
    )
    assert not visitor_problems, visitor_problems
    assert not problems, problems
    visitor_context.close()
    context.close()
