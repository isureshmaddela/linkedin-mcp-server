"""Marketing fixes in a real browser: a lead opens from the keyboard (Enter) and its drawer stays open, the leads
inbox shows whether a lead is paid and what it is worth on a 390 px phone without sideways scrolling, and Enter in
the public lead form and the media kit form sends them."""

# ruff: noqa: F811
from __future__ import annotations

import pytest

pytest.importorskip("patchright.sync_api")

from test_careers import env, stub  # noqa: E402, F401
from test_leads_ui import browser, go, signed_in_page, until  # noqa: E402, F401
from test_suite_marketing_ui import call, local_index, shot  # noqa: E402, F401


def _business_with_a_paid_lead(context, env):
    call(
        context,
        env,
        "POST",
        "/api/suite/business",
        {"name": "Glow Studio", "email": "owner@glow.example"},
    )
    form = call(context, env, "POST", "/api/marketing/forms", {"title": "Consult"})[
        "form"
    ]
    token = call(context, env, "POST", f"/api/marketing/forms/{form['id']}/link", {})[
        "public"
    ]["token"]
    call(
        context,
        env,
        "POST",
        f"/api/public/{token}/submit",
        {"name": "Kim Brightwater", "email": "kim.brightwater@example.com"},
    )
    lead = call(context, env, "GET", "/api/marketing/submissions")["submissions"][0]
    call(
        context,
        env,
        "POST",
        f"/api/marketing/submissions/{lead['id']}/paid",
        {"value_cents": 12000},
    )
    return token


def test_a_lead_opens_from_the_keyboard_and_shows_its_money_on_a_phone(browser, env):
    context, page, problems = signed_in_page(browser, env)
    _business_with_a_paid_lead(context, env)
    go(page, env, "marketing?tab=forms")
    page.wait_for_selector("#main .mk-inbox tbody tr.suite-row")
    row = page.locator("#main .mk-inbox tbody tr.suite-row").first
    row.focus()
    page.keyboard.press("Enter")
    page.wait_for_timeout(400)
    assert page.locator(".drawer").count() == 1, "the drawer closed on the same Enter"
    assert "Kim" in page.locator(".drawer h2").inner_text()
    buttons = [t.strip() for t in page.locator(".drawer button").all_inner_texts()]
    assert "Mark not paid" in buttons or "Reply by email" in buttons, buttons
    page.keyboard.press("Escape")

    page.set_viewport_size({"width": 390, "height": 844})
    go(page, env, "marketing?tab=forms")
    page.wait_for_selector("#main .mk-inbox tbody tr.suite-row")
    inbox = page.locator("#main .mk-inbox")
    wrap = inbox.evaluate("(node) => node.scrollWidth - node.clientWidth")
    assert wrap <= 1, f"the inbox scrolls sideways by {wrap}px"
    text = inbox.inner_text()
    assert "paid" in text.lower() and "120" in text, text
    assert "Consult" in text  # where it came from moved under the name
    assert (
        page.evaluate("() => document.documentElement.scrollWidth - window.innerWidth")
        <= 0
    )
    shot(page, "fixes-inbox-phone")
    assert not problems, problems
    context.close()


def test_enter_sends_the_public_lead_form_and_the_media_kit_form(browser, env):
    context, page, problems = signed_in_page(browser, env)
    token = _business_with_a_paid_lead(context, env)
    call(
        context,
        env,
        "POST",
        "/api/marketing/media-kit",
        {"headline": "Hair ideas", "audience": {"instagram": 900}, "open": True},
    )
    shared = call(context, env, "POST", "/api/marketing/media-kit/share", {})["public"]
    before = call(context, env, "GET", "/api/marketing/submissions")["total"]

    phone = browser.new_context(viewport={"width": 390, "height": 844})
    visitor = phone.new_page()
    visitor_problems: list[str] = []
    visitor.on("pageerror", lambda e: visitor_problems.append(str(e)))
    visitor.goto(f"{env.base}/p/{token}")
    visitor.wait_for_selector("form.pub-form input[name=name]")
    visitor.click("form.pub-form input[name=name]")
    visitor.keyboard.type("Kim")
    visitor.keyboard.press("Tab")
    visitor.keyboard.type("kim@example.com")
    visitor.keyboard.press("Enter")
    visitor.wait_for_selector(".pub-notice.good")
    assert (
        call(context, env, "GET", "/api/marketing/submissions")["total"] == before + 1
    )

    visitor.goto(env.base + shared["path"])
    visitor.wait_for_selector("form.pub-form input[name=budget]")
    visitor.fill("input[name=company]", "Acme Paints")
    visitor.fill("input[name=name]", "Jo")
    visitor.fill("input[name=email]", "jo@acme.example")
    visitor.click("input[name=budget]")
    visitor.keyboard.type("300")
    visitor.keyboard.press("Enter")
    visitor.wait_for_selector(".pub-notice.good")
    shot(visitor, "fixes-media-kit-enter")
    sponsorships = call(
        context, env, "GET", "/api/marketing/submissions?source=media_kit"
    )["submissions"]
    assert len(sponsorships) == 1 and sponsorships[0]["value_cents"] == 30000
    assert not visitor_problems, visitor_problems
    assert not problems, problems
    phone.close()
    context.close()
