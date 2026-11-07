"""Service in a real browser: regressions for the tester's findings after paid help was added.

* Enter on a button inside a table row (Mark paid, a tag, Delete) also opened the row's drawer.
* Merging into another customer's case is refused with the reason in the dialog.
* The live chat button kept saying "Make it a case" after the case was made.
* The Business hub card never showed "Paid help this month".
"""

# ruff: noqa: F811
from __future__ import annotations

import pytest

pytest.importorskip("patchright.sync_api")

from aira.suite import kit, public  # noqa: E402
from test_careers import env, stub  # noqa: E402, F401
from test_leads_ui import browser, go, signed_in_page, until  # noqa: E402, F401
from test_suite_service_ui import post, share_url  # noqa: E402

import auto_apply_assistant as local  # noqa: E402


def drawer_open(page) -> bool:
    return (
        page.locator(".drawer").count() > 0
        and page.locator(".drawer").first.is_visible()
    )


def test_keyboard_merge_chat_and_hub_in_the_browser(browser, env):
    kit.forget_schema()
    public.set_index(local._public_link_index)
    context, page, problems = signed_in_page(browser, env)
    post(
        context,
        env,
        "/api/suite/business",
        {
            "name": "Corner Shop",
            "timezone": "UTC",
            "payment_link": "https://pay.example/c",
        },
    )
    post(
        context,
        env,
        "/api/service/offers",
        {"title": "Setup call", "price_cents": 4500},
    )
    ana = post(
        context,
        env,
        "/api/service/cases",
        {
            "subject": "Setup please",
            "customer_email": "ana@example.com",
            "tags": "refund",
        },
    )["case"]
    post(
        context,
        env,
        f"/api/service/cases/{ana['id']}/charges",
        {"label": "Extra hour", "amount_cents": 4500},
    )
    cy = post(
        context,
        env,
        "/api/service/cases",
        {"subject": "Password reset broken", "customer_email": "cy@example.com"},
    )["case"]

    # Enter on Mark paid records the payment and opens nothing else.
    go(page, env, "service?tab=paid")
    button = page.locator("tr.suite-row button:has-text('Mark paid')")
    button.focus()
    page.keyboard.press("Enter")
    page.wait_for_selector("text=Payment recorded. It counts in your revenue.")
    page.wait_for_timeout(500)
    assert not drawer_open(page)
    revenue = context.request.get(env.base + "/api/suite/revenue").json()
    assert revenue["summary"]["by_app"]["service"] == 4500

    # Enter on a tag filters the inbox and opens nothing else; Enter on the row itself still opens it.
    go(page, env, "service")
    page.wait_for_selector("tr.suite-row:has-text('Setup please')")
    page.locator("button.sv-tag:has-text('refund')").focus()
    page.keyboard.press("Enter")
    until(page, lambda: "tag=refund" in page.url, what="the tag filter")
    page.wait_for_timeout(500)
    assert not drawer_open(page)
    page.wait_for_selector("tr.suite-row:has-text('Setup please')")
    page.locator("tr.suite-row:has-text('Setup please')").focus()
    page.keyboard.press("Enter")
    page.wait_for_selector(".drawer:has-text('Merge into another case')")

    # Merging into another customer's case is refused, with the reason, and nothing moves.
    page.click(".drawer button:has-text('Merge into another case')")
    page.fill(".modal [name=number]", str(cy["number"]))
    page.locator(".modal .actions button").last.click()
    page.wait_for_selector(".modal:has-text('is from another customer')")
    page.keyboard.press("Escape")
    detail = context.request.get(env.base + f"/api/service/cases/{ana['id']}").json()
    assert detail["case"]["merged_into"] == "" and len(detail["charges"]) == 1

    # The Business hub shows the money Service earned this month.
    go(page, env, "hub")
    page.wait_for_selector(
        ".suite-kpi:has-text('Paid help this month'):has-text('$45.00')"
    )

    # Live chat: after Make it a case the button says Open its case.
    go(page, env, "service?tab=chat")
    chat_url = share_url(page)
    visitor = browser.new_context(viewport={"width": 390, "height": 844})
    chat_page = visitor.new_page()
    chat_page.goto(chat_url)
    chat_page.get_by_label("Your name").fill("Ben")
    chat_page.get_by_label("Your email").fill("ben@example.com")
    chat_page.get_by_label("Your question").fill("Are you open on Sunday?")
    chat_page.click("button:has-text('Start chat')")
    chat_page.wait_for_selector("text=Waiting for someone to answer")
    page.wait_for_selector(".sv-chat-item:has-text('Ben')", timeout=10000)
    page.click(".sv-chat-item:has-text('Ben')")
    page.click(".sv-chat-pane button:has-text('Make it a case')")
    page.wait_for_selector(".drawer:has-text('Are you open on Sunday?')")
    page.wait_for_selector(".sv-chat-pane button:has-text('Open its case')")
    assert page.locator(".sv-chat-pane button:has-text('Make it a case')").count() == 0
    visitor.close()

    assert [p for p in problems if "pageerror" in p or "Uncaught" in p] == []
    assert [
        p for p in problems if "console.error" in p and "status of 4" not in p
    ] == [], problems
    context.close()
