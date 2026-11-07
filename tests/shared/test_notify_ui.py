"""The Notifications page and the live stream in a real browser against the local server.

The service worker registers at the root, the page lists the notification centre and the choices per category,
a notification made on the server appears without a reload (the live stream), and Mark all read clears the badge
on the account button. "Turn on notifications" is not clicked: that would reach a real push service.
"""

# ruff: noqa: F811
from __future__ import annotations

import re

import pytest

patchright = pytest.importorskip("patchright.sync_api")

from aira import notify  # noqa: E402
from aira.notify import realtime  # noqa: E402
from test_careers import env, stub  # noqa: E402, F401
from test_leads_ui import browser, go, signed_in_page, until  # noqa: E402, F401


@pytest.fixture(autouse=True)
def live(monkeypatch):
    realtime.HUB.reset()
    monkeypatch.setattr(realtime, "HEARTBEAT_SECONDS", 1.0)
    monkeypatch.setattr(notify, "_live", True)
    yield
    realtime.HUB.shutdown()
    realtime.HUB.reset()  # the next test in this process gets a working hub again


def test_the_notifications_page_updates_live_and_the_app_installs(browser, env):
    context, page, problems = signed_in_page(browser, env)
    go(page, env, "notifications")
    page.wait_for_selector("h1:has-text('Notifications')")
    page.wait_for_selector("text=What to tell you")
    for label in (
        "Careers follow-ups",
        "Medicine reminders",
        "Bookings",
        "Helpdesk",
        "Stock alerts",
        "Messages",
    ):
        assert page.locator("li strong", has_text=label).count() >= 1, label
    assert page.get_by_role("button", name="Turn on notifications").count() >= 1
    # the service worker runs from the root, and the page holds one live stream (opened once it settled)
    until(
        page,
        lambda: any(w.url == f"{env.base}/sw.js" for w in context.service_workers),
        timeout=30,
        what="the service worker",
    )
    until(
        page,
        lambda: realtime.HUB.stats()["streams"] == 1,
        timeout=30,
        what="the live stream",
    )
    # a reminder made on the server shows at once, with its unread badge
    user = env.user()
    with env.user_db() as connection:
        notify.notify(
            connection,
            {"user_id": user["user_id"]},
            "careers",
            "Follow-up due",
            "Something you planned in your job search is due.",
            "/#/dashboard",
            detail="Email the recruiter at Initech",
            home=f"local:{user['user_id']}",
        )
    page.wait_for_selector("li.notify-item.unread:has-text('Follow-up due')")
    assert (
        "Email the recruiter at Initech"
        in page.locator("li.notify-item").first.inner_text()
    )
    page.wait_for_selector("#notifyBadge")
    assert page.locator("#notifyBadge").inner_text() == "1"
    page.get_by_role("button", name="Mark all read").click()
    page.wait_for_selector("#notifyBadge", state="detached")
    assert page.locator("li.notify-item.unread").count() == 0
    # the account menu leads here; Settings offers the same controls
    page.click("#accountBtn")
    page.wait_for_selector("#accountMenu:not([hidden])")
    assert page.locator("#accountMenu a.menu-item[href='#/notifications']").count() == 1
    page.keyboard.press("Escape")
    go(page, env, "settings/profile")
    page.wait_for_selector(".page-head .notify-controls")
    assert page.locator(".page-head a[href='#/notifications']").count() == 1
    page.goto(f"{env.base}/manifest.webmanifest")
    assert re.search(r'"display":\s*"standalone"', page.content())
    assert not problems, problems
    context.close()
