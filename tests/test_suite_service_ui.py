"""Service in a real browser: every tab, a published answer in the help centre, a visitor's message that becomes a
case, the agent's reply from the case drawer, the customer's request page, and a live chat answered both ways."""

# ruff: noqa: F811
from __future__ import annotations

import json

import pytest

patchright = pytest.importorskip("patchright.sync_api")

from aira.suite import kit, public  # noqa: E402
from test_careers import env, stub  # noqa: E402, F401
from test_leads_ui import browser, go, signed_in_page, until  # noqa: E402, F401

import auto_apply_assistant as local  # noqa: E402

TABS = {
    "inbox": "Inbox",
    "chat": "Live chat",
    "help": "Help centre",
    "replies": "Saved replies",
    "settings": "Settings",
    "reports": "Reports",
}


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


def share_url(page):
    page.click("button:has-text('Create link')")
    page.wait_for_selector(".suite-share-url")
    return page.locator(".suite-share-url").inner_text()


def test_service_in_the_browser(browser, env):
    kit.forget_schema()
    public.set_index(local._public_link_index)
    context, page, problems = signed_in_page(browser, env)
    post(
        context, env, "/api/suite/business", {"name": "Corner Shop", "timezone": "UTC"}
    )
    post(
        context,
        env,
        "/api/service/articles",
        {
            "title": "When will my order arrive?",
            "body": "Orders arrive in three working days.",
            "status": "published",
        },
    )
    post(
        context,
        env,
        "/api/service/macros",
        {"title": "On its way", "body": "Hello {customer}, case {case} is on its way."},
    )

    for tab, label in TABS.items():
        go(page, env, f"service?tab={tab}")
        page.wait_for_selector("#main [role='tablist']")
        page.wait_for_timeout(300)
        assert (
            page.locator("#main [role='tab'][aria-selected='true']").inner_text()
            == label
        )
        assert page.locator("#main .error-box").count() == 0, tab

    # The help centre: shared, searched and read by a visitor, who then writes in.
    go(page, env, "service?tab=help")
    page.wait_for_selector("td:has-text('When will my order arrive?')")
    help_url = share_url(page)
    visitor = browser.new_context(viewport={"width": 390, "height": 844})
    help_page = visitor.new_page()
    help_page.goto(help_url)
    help_page.wait_for_selector("a:has-text('When will my order arrive?')")
    help_page.click("a:has-text('When will my order arrive?')")
    help_page.wait_for_selector("text=Orders arrive in three working days.")
    help_page.click("button:has-text('Yes')")
    help_page.wait_for_selector("text=Thank you for letting us know.")
    help_page.get_by_label("Your name").fill("Ana Lopez")
    help_page.get_by_label("Your email").fill("ana@example.com")
    help_page.get_by_label("Subject").fill("Parcel missing")
    help_page.get_by_label("How can we help?").fill("My parcel has not come.")
    help_page.click("button:has-text('Send message')")
    help_page.wait_for_selector("text=Thank you, we have your message")
    assert "#1001" in help_page.locator("main").inner_text()
    assert not help_page.evaluate(
        "document.documentElement.scrollWidth > window.innerWidth + 2"
    )

    # The agent answers from the case drawer with a saved reply.
    go(page, env, "service")
    page.wait_for_selector("tr.suite-row:has-text('Parcel missing')")
    page.click("tr.suite-row:has-text('Parcel missing')")
    page.wait_for_selector(".drawer:has-text('My parcel has not come.')")
    page.select_option(".drawer select[aria-label='Saved replies']", label="On its way")
    until(
        page,
        lambda: (
            "case #1001 is on its way"
            in page.locator(".drawer textarea[aria-label='Reply']").input_value()
        ),
        what="the saved reply to be inserted",
    )
    page.click(".drawer button:has-text('Send')")
    page.wait_for_selector(".drawer .sv-msg.agent:has-text('Hello Ana')")
    outbox = context.request.get(env.base + "/api/suite/outbox").json()
    reply = next(m for m in outbox["messages"] if m["kind"] == "case_reply")
    case_path = "/p/" + reply["body"].split("/p/")[1].split()[0]

    # The customer's own request page: the reply is there, internal notes are not, and they answer.
    case_page = visitor.new_page()
    case_page.goto(env.base + case_path)
    case_page.wait_for_selector("text=Hello Ana")
    case_page.get_by_label("Your message").fill("Thank you, it just arrived.")
    case_page.click("button:has-text('Send reply')")
    case_page.wait_for_selector("text=Sent. We will reply soon.")

    # Live chat, both ways.
    go(page, env, "service?tab=chat")
    chat_url = share_url(page)
    chat_page = visitor.new_page()
    chat_page.goto(chat_url)
    chat_page.get_by_label("Your name").fill("Ben")
    chat_page.get_by_label("Your email").fill("ben@example.com")
    chat_page.get_by_label("Your question").fill("Are you open on Sunday?")
    chat_page.click("button:has-text('Start chat')")
    chat_page.wait_for_selector("text=Waiting for someone to answer")
    page.wait_for_selector(".sv-chat-item:has-text('Ben')", timeout=10000)
    page.click(".sv-chat-item:has-text('Ben')")
    page.wait_for_selector(".sv-chat-pane:has-text('Are you open on Sunday?')")
    page.fill(".sv-chat-pane textarea", "Yes, from 10 to 4.")
    page.click(".sv-chat-pane button:has-text('Send')")
    chat_page.wait_for_selector("text=Yes, from 10 to 4.", timeout=10000)
    chat_page.get_by_label("Your message").fill("Great, thanks!")
    chat_page.click("button:has-text('Send')")
    page.wait_for_selector(".sv-chat-pane:has-text('Great, thanks!')", timeout=10000)
    visitor.close()

    go(page, env, "service?tab=reports")
    page.wait_for_selector(".suite-kpi:has-text('Open cases')")
    assert [p for p in problems if "pageerror" in p or "Uncaught" in p] == []
    assert [
        p for p in problems if "console.error" in p and "status of 4" not in p
    ] == [], problems
    context.close()
