"""Marketing's money features and fixes in a real browser: a draft with no date is listed under the calendar, a
lead is marked paid from its drawer, the media kit tab works, and a brand sends a sponsorship request from the
public media kit on a phone (with one error message at a time, never a pile of old ones)."""

# ruff: noqa: F811
from __future__ import annotations

import pytest

pytest.importorskip("patchright.sync_api")

from test_careers import env, stub  # noqa: E402, F401
from test_leads_ui import browser, go, signed_in_page, until  # noqa: E402, F401
from test_suite_marketing_ui import call, local_index, shot  # noqa: E402, F401


def _click(page, selector, label):
    """Click the element under ``selector`` whose text is ``label`` (plain text match, no selector engines)."""
    loc = page.locator(selector)
    texts = [text.strip() for text in loc.all_inner_texts()]
    assert label in texts, (label, texts)
    loc.nth(texts.index(label)).click()


def test_drafts_without_a_date_and_a_paid_lead(browser, env):
    context, page, problems = signed_in_page(browser, env)
    call(context, env, "POST", "/api/suite/business", {"name": "Glow Studio"})
    call(
        context,
        env,
        "POST",
        "/api/marketing/posts",
        {
            "channel": "linkedin",
            "title": "Written with AI",
            "body": "Draft",
            "status": "draft",
        },
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
        {"name": "Ana", "email": "ana@example.com"},
    )

    go(page, env, "marketing?tab=calendar")
    page.wait_for_selector("#main .suite-table")
    until(
        page,
        lambda: "Not scheduled yet" in page.locator("#main").inner_text(),
        what="the drafts list",
    )
    assert "Written with AI" in page.locator("#main").inner_text()
    shot(page, "earn-calendar")

    go(page, env, "marketing?tab=forms")
    page.wait_for_selector("#main .mk-filters")
    page.locator("#main .suite-table").nth(1).locator("tbody tr").first.click()
    page.wait_for_selector(".drawer h2")
    _click(page, ".drawer button", "Mark paid")
    page.wait_for_selector(".modal input[name=value_cents]")
    page.fill(".modal input[name=value_cents]", "120")
    _click(page, ".modal .actions button", "Mark paid")
    until(
        page,
        lambda: (
            page.locator("#main .suite-table").count() > 1
            and "Paid" in page.locator("#main .suite-table").nth(1).inner_text()
        ),
        what="the lead to show Paid",
    )
    revenue = call(context, env, "GET", "/api/suite/revenue?app=marketing")
    assert revenue["summary"]["this_month_cents"] == 12000
    shot(page, "earn-paid")
    assert not problems, problems
    context.close()


def test_a_brand_sends_a_sponsorship_request_from_the_media_kit_on_a_phone(
    browser, env
):
    context, page, problems = signed_in_page(browser, env)
    call(
        context,
        env,
        "POST",
        "/api/suite/business",
        {"name": "Glow Studio", "email": "owner@glow.example"},
    )
    go(page, env, "marketing?tab=kit")
    page.wait_for_selector("#main .mk-audience")
    page.fill("input[name=headline]", "Hair ideas for busy people")
    page.fill("input[name=aud_instagram]", "12500")
    page.check("input[name=open]")
    _click(page, "#main button", "Save")
    until(
        page,
        lambda: call(context, env, "GET", "/api/marketing/media-kit")["reach"] == 12500,
        what="the save",
    )
    call(
        context,
        env,
        "POST",
        "/api/marketing/media-kit/packages",
        {"name": "One reel", "price_cents": 45000},
    )
    shared = call(context, env, "POST", "/api/marketing/media-kit/share", {})["public"]

    phone = browser.new_context(viewport={"width": 390, "height": 844})
    visitor = phone.new_page()
    visitor_problems: list[str] = []
    visitor.on("pageerror", lambda e: visitor_problems.append(str(e)))
    visitor.goto(env.base + shared["path"])
    visitor.wait_for_selector("form.pub-form")
    assert "12,500" in visitor.locator("main").inner_text()
    assert "One reel" in visitor.locator("main").inner_text()
    send = visitor.locator("form.pub-form button.pub-btn")
    send.click()
    visitor.fill("input[name=company]", "Acme Paints")
    visitor.fill("input[name=name]", "Jo")
    visitor.fill("input[name=email]", "jo@acme")
    send.click()
    assert (
        visitor.locator(".pub-notice.bad:not([hidden])").count() == 1
    )  # replaced, not stacked
    assert (
        "email address" in visitor.locator(".pub-notice.bad:not([hidden])").inner_text()
    )
    visitor.fill("input[name=email]", "jo@acme.example")
    visitor.select_option("select[name=package_id]", index=1)
    send.click()
    visitor.wait_for_selector(".pub-notice.good")
    assert visitor.locator(".pub-notice.bad:not([hidden])").count() == 0
    assert (
        visitor.evaluate(
            "() => document.documentElement.scrollWidth - window.innerWidth"
        )
        <= 0
    )
    shot(visitor, "earn-media-kit-phone")
    leads = call(context, env, "GET", "/api/marketing/submissions?source=media_kit")[
        "submissions"
    ]
    assert leads[0]["company"] == "Acme Paints" and leads[0]["value_cents"] == 45000
    assert not visitor_problems, visitor_problems
    assert not problems, problems
    phone.close()
    context.close()
