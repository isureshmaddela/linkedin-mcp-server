"""Business hub > Email in a real browser: labelled boxes choose what goes out without approval (no internal ids to
type), the incoming mailbox is set up and checked next to the mail server, and Service's settings choose which
team notices go out by email."""

# ruff: noqa: F811
from __future__ import annotations

import pytest

pytest.importorskip("patchright.sync_api")

from imap_stub import ImapStub  # noqa: E402
from test_careers import env, stub  # noqa: E402, F401
from test_leads_ui import browser, go, signed_in_page, until  # noqa: E402, F401
from test_suite_marketing_ui import call, local_index, shot  # noqa: E402, F401


def test_the_email_tab_offers_labelled_boxes_and_the_incoming_mailbox(browser, env):
    context, page, problems = signed_in_page(browser, env)
    imap = ImapStub(user="hello@glow.example", password="pw").start()
    try:
        call(
            context,
            env,
            "POST",
            "/api/suite/email",
            {"from_email": "hello@glow.example", "smtp_host": "smtp.glow.example"},
        )
        go(page, env, "hub?tab=email")
        page.wait_for_selector(".hub-auto-send input[type=checkbox]")
        boxes = page.locator(".hub-auto-send label.suite-check")
        labels = [text.strip() for text in boxes.all_inner_texts()]
        assert "Shop: order confirmations" in labels and "Events: tickets" in labels
        assert (
            "auto_send_text" not in page.content()
        )  # no free-text list of internal ids any more
        order = page.get_by_label("Shop: order confirmations")
        reminder = page.get_by_label("Bookings: reminders the day before")
        assert (
            order.is_checked() and not reminder.is_checked()
        )  # transactional kinds are on by default
        order.uncheck()
        reminder.check()
        page.locator("#main").get_by_role("button", name="Save").first.click()
        until(
            page,
            lambda: call(context, env, "GET", "/api/suite/email")["settings"][
                "auto_send_chosen"
            ],
            what="the choice to be saved",
        )
        saved = call(context, env, "GET", "/api/suite/email")["settings"]["auto_send"]
        assert (
            "booking_reminder" in saved
            and "order_confirmation" not in saved
            and "ticket" in saved
        )

        page.wait_for_selector(".hub-inbound input[name=enabled]")
        section = page.locator(".hub-inbound")
        assert "Incoming mail" in section.inner_text()
        section.locator("input[name=enabled]").check()
        section.locator("input[name=imap_host]").fill("127.0.0.1")
        section.locator("input[name=imap_port]").fill(str(imap.port))
        section.locator("select[name=imap_security]").select_option("none")
        section.locator("input[name=imap_username]").fill("hello@glow.example")
        section.locator("input[name=imap_password]").fill("pw")
        section.get_by_role("button", name="Save").click()
        page.wait_for_selector("button:has-text('Check now')")
        view = call(context, env, "GET", "/api/suite/inbound")
        assert view["settings"]["enabled"] and view["has_password"]
        page.get_by_role("button", name="Check now").click()
        page.wait_for_selector("text=Last checked")
        shot(page, "hub-email-incoming")
        assert not problems, problems
    finally:
        imap.stop()
        context.close()


def test_service_settings_choose_the_team_notices(browser, env):
    context, page, problems = signed_in_page(browser, env)
    go(page, env, "service?tab=settings")
    page.wait_for_selector("text=Tell the team by email")
    page.get_by_label("A visitor starts a live chat").uncheck()
    page.locator("input[name=notify_email]").fill("support@glow.example")
    page.get_by_role("button", name="Save settings").click()
    until(
        page,
        lambda: (
            call(context, env, "GET", "/api/service/settings")["settings"][
                "notify_email"
            ]
            == "support@glow.example"
        ),
        what="the settings to be saved",
    )
    settings = call(context, env, "GET", "/api/service/settings")["settings"]
    assert settings["notify_new_chat"] is False and settings["notify_new_case"] is True
    assert settings["email_to_case"] is True
    assert not problems, problems
    context.close()
