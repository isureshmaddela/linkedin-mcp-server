"""Recruiter contacts in a real browser against the local server: Find recruiters (review, then Save selected),
the contact story in the drawer (no AI model: it says so), Lists (a preset, CSV), emailing a list through the local
SMTP stub, and LinkedIn drafts that are never sent. It fails on any script error. Screenshots go to
``CAREERS_RECRUITERS_SHOTS`` (default: none) for a human to look at.
"""

# ruff: noqa: F811
from __future__ import annotations

import os
import re
from pathlib import Path

import pytest

patchright = pytest.importorskip("patchright.sync_api")

from smtp_stub import SmtpStub  # noqa: E402
from test_careers import env, stub  # noqa: E402, F401
from test_leads_ui import browser, go, signed_in_page, until  # noqa: E402, F401
from test_recruiter_contacts import MARCO_PAGE, add_thread  # noqa: E402
from test_suite_careers_shell_ui import folders, post  # noqa: E402

SHOTS = (
    Path(os.environ["CAREERS_RECRUITERS_SHOTS"])
    if os.environ.get("CAREERS_RECRUITERS_SHOTS")
    else None
)


def shot(page, name: str) -> None:
    if SHOTS is None:
        return
    SHOTS.mkdir(parents=True, exist_ok=True)
    page.screenshot(path=str(SHOTS / f"{name}.png"), full_page=True)


def seed(context, env) -> None:
    post(
        context,
        env,
        "/api/profile",
        {
            "resume": "Jordan Example\nSolution Architect",
            "resume_name": "resume.txt",
            "application_profile": {
                "first_name": "Jordan",
                "last_name": "Example",
                "full_name": "Jordan Example",
                "email": "jordan@example.com",
            },
        },
    )
    post(
        context,
        env,
        "/api/email/import",
        {
            "subject": "Solution Architect at Contoso Labs",
            "body": "Hi Jordan,\n\nThanks for the chat.\n\nBest regards,\nMarco Diaz\nSenior Technical Recruiter\n",
            "sender": "marco.diaz@contosolabs.com",
            "sender_name": "Marco Diaz",
            "received_at": "2026-10-22T16:00:00+00:00",
        },
    )
    post(
        context,
        env,
        "/api/interviews",
        {
            "company": "Northwind",
            "role": "Architect",
            "interview_type": "recruiter_screen",
            "start_at": "2026-10-25T10:00",
            "timezone": "UTC",
            "participants": [
                {
                    "name": "Lena Park",
                    "role": "Recruiter",
                    "email": "lena@northwind.example",
                }
            ],
        },
    )
    with env.user_db() as connection:
        add_thread(
            connection,
            "t-marco",
            "Marco Diaz",
            raw_text=MARCO_PAGE,
            messages=[
                (
                    "Marco Diaz",
                    False,
                    "2026-10-20",
                    "9:15 AM",
                    "Hello Jordan, are you available?",
                )
            ],
        )
        add_thread(
            connection,
            "t-priya",
            "Priya Shah",
            messages=[
                (
                    "Priya Shah",
                    False,
                    "2026-10-12",
                    "2:00 PM",
                    "Hi! I'm a recruiter at Fabrikam. Would you like to chat?",
                )
            ],
        )
        connection.commit()


def proposal(page, name: str):
    return page.locator(
        "#main .rcx-proposal", has=page.locator(".title", has_text=name)
    )


def test_find_recruiters_story_lists_email_and_linkedin_drafts(browser, env):
    context, page, problems = signed_in_page(browser, env, width=1440, height=900)
    seed(context, env)
    smtp = SmtpStub().start()
    try:
        # Recruiters > Contacts lists every saved contact with where it came from, and says people are waiting.
        post(
            context,
            env,
            "/api/recruiters",
            {"name": "Sam Rivera", "company": "Acme Cloud"},
        )
        go(page, env, "recruiters?tab=contacts")
        page.wait_for_selector("#main .rc-waiting-note")
        assert folders(page, "recruiters") == [
            "Contacts",
            "Messages",
            "Find recruiters",
            "Lists",
        ]
        assert (
            "Waiting for review: 3 new contacts and 0 updates"
            in page.locator(".rc-waiting-note").inner_text()
        )
        sam = page.locator("#main .list .row", has_text="Sam Rivera")
        sam.wait_for()
        assert sam.locator(".rc-source").all_inner_texts() == ["Added by you"]
        shot(page, "00-contacts-before")
        page.click("#rcFindButton")
        page.wait_for_url(re.compile(r"#/recruiters\?tab=find"))
        page.wait_for_selector("#main .rcx-proposal")
        assert sorted(page.locator("#main .rcx-proposal .title").all_inner_texts()) == [
            "Lena Park",
            "Marco Diaz",
            "Priya Shah",
        ]
        marco = proposal(page, "Marco Diaz")
        assert (
            "Technical Recruiter · Contoso Labs · marco.diaz@contosolabs.com"
            in marco.inner_text()
        )
        assert "LinkedIn inbox" in marco.inner_text() and "Email" in marco.inner_text()
        assert (
            "No email address in the text" in proposal(page, "Priya Shah").inner_text()
        )
        assert (
            "Checked 2 conversations, 1 email and 1 interview participant."
            in page.locator(".rcx-scanned").inner_text()
        )
        assert page.locator("#rcxSave").inner_text() == "Save selected (3)"
        assert "null" not in page.locator("#main").inner_text()
        shot(page, "01-find")
        # Lena stays out; the others are saved.
        proposal(page, "Lena Park").locator("input[type=checkbox]").uncheck()
        assert page.locator("#rcxSave").inner_text() == "Save selected (2)"
        page.click("#rcxSave")
        page.wait_for_selector(".toast:has-text('Saved 2 new contacts')")
        until(
            page,
            lambda: (
                page.locator("#main .rcx-proposal .title").all_inner_texts()
                == ["Lena Park"]
            ),
            what="only Lena left",
        )

        # Contacts: where each one was found; the drawer tells their story.
        page.locator(
            ".shell-subnav[data-section='recruiters'] a", has_text="Contacts"
        ).click()
        page.wait_for_url(re.compile(r"#/recruiters\?tab=contacts"))
        row = page.locator("#main .list .row", has_text="Marco Diaz")
        row.wait_for()
        # The people from the LinkedIn inbox are contacts now, with their sources as badges.
        assert row.locator(".rc-source").all_inner_texts() == [
            "LinkedIn inbox",
            "Email",
        ]
        priya = page.locator("#main .list .row", has_text="Priya Shah")
        assert priya.locator(".rc-source").all_inner_texts() == ["LinkedIn inbox"]
        assert page.locator("#main .list .row .title").count() == 3
        page.wait_for_selector(
            ".rc-waiting-note:has-text('Waiting for review: 1 new contact and 0 updates')"
        )
        assert "null" not in page.locator("#main").inner_text()
        shot(page, "01b-contacts-after")
        row.click()
        page.wait_for_selector(".drawer .rcx-story .rcx-timeline")
        story = page.locator(".drawer .rcx-story")
        assert story.locator(".rcx-item").count() == 2
        assert (
            "LinkedIn · from them" in story.inner_text()
            and "Solution Architect at Contoso Labs" in story.inner_text()
        )
        assert "No AI model is set up" in story.inner_text()
        assert story.locator("button", has_text="Summarize with AI").is_disabled()
        assert "null" not in page.locator(".drawer").inner_text()
        shot(page, "02-story")
        page.keyboard.press("Escape")

        # Lists: start from a preset, see who is on it, export it.
        go(page, env, "recruiters?tab=lists")
        page.wait_for_selector("#main .rcx-presets")
        page.locator("#main .rcx-presets button", has_text="Has email").click()
        page.wait_for_selector(
            ".modal p[role='status']:has-text('1 contact match right now.')"
        )
        page.locator(".modal .actions button", has_text="Create list").click()
        page.wait_for_url(re.compile(r"#/recruiters\?tab=lists&list=[0-9a-f]{32}"))
        page.wait_for_selector("#main .rcx-members")
        assert page.locator("#main .rcx-members tbody tr").count() == 1
        assert (
            "marco.diaz@contosolabs.com"
            in page.locator("#main .rcx-members").inner_text()
        )
        with page.expect_download() as download:
            page.locator("#main button", has_text="Export CSV").click()
        assert download.value.suggested_filename == "recruiters-has-email.csv"
        assert "null" not in page.locator("#main").inner_text()
        shot(page, "03-list")

        # Email: no mail server yet, so the preview works but Send waits for one.
        page.locator("#main button", has_text="Email this list").click()
        page.wait_for_selector(".modal .rcx-flow")
        assert "Set up your email first" in page.locator(".modal").inner_text()
        page.locator(".modal button", has_text="Preview emails").click()
        page.wait_for_selector(".modal .rcx-mail")
        assert "1 ready · 0 skipped" in page.locator(".modal .rcx-preview").inner_text()
        assert page.locator("#rcxSendEmails").is_disabled()
        page.keyboard.press("Escape")
        post(
            context,
            env,
            "/api/suite/email",
            {
                "from_email": "jordan@example.com",
                "from_name": "Jordan Example",
                "smtp_host": "127.0.0.1",
                "smtp_port": smtp.port,
                "smtp_security": "none",
            },
        )
        page.locator("#main button", has_text="Email this list").click()
        page.wait_for_selector(".modal .rcx-flow")
        assert (
            "Emails go out from jordan@example.com"
            in page.locator(".modal").inner_text()
        )
        page.locator(".modal button", has_text="Preview emails").click()
        page.wait_for_selector(".modal .rcx-letter")
        page.locator(".modal .rcx-letter summary").click()
        assert (
            'reply "unsubscribe"' in page.locator(".modal .rcx-letter pre").inner_text()
        )
        shot(page, "04-email-preview")
        assert page.locator("#rcxSendEmails").inner_text() == "Send 1 email"
        assert "null" not in page.locator(".modal").inner_text()
        page.click("#rcxSendEmails")
        page.locator(".modal .actions button", has_text="Send now").click()
        page.wait_for_selector(".toast:has-text('Sent 1')")
        until(
            page,
            lambda: len(smtp.messages) == 1,
            what="the email to reach the SMTP stub",
        )
        page.wait_for_selector(".modal .rcx-mail:has-text('You emailed them on')")
        page.keyboard.press("Escape")

        # LinkedIn: drafts only, for a list from the inbox; Priya has no email and is LinkedIn only.
        go(page, env, "recruiters?tab=lists")
        page.locator(
            "#main .rcx-presets button", has_text="From my LinkedIn inbox"
        ).click()
        page.wait_for_selector(
            ".modal p[role='status']:has-text('2 contacts match right now.')"
        )
        page.locator(".modal .actions button", has_text="Create list").click()
        page.wait_for_selector("#main .rcx-members")
        assert "LinkedIn only" in page.locator("#main .rcx-members").inner_text()
        page.locator("#main button", has_text="LinkedIn drafts").click()
        page.wait_for_selector(".modal .rcx-flow")
        assert "Nothing is sent" in page.locator(".modal").inner_text()
        page.locator(".modal button", has_text="Preview drafts").click()
        page.wait_for_selector(".modal .rcx-draft")
        assert page.locator("#rcxSaveDrafts").inner_text() == "Save 2 drafts"
        page.click("#rcxSaveDrafts")
        page.wait_for_selector(".toast:has-text('Saved 2 drafts. Nothing was sent.')")
        assert page.locator(".modal .rcx-drafted li").count() == 2
        shot(page, "05-linkedin-drafts")
        assert len(smtp.messages) == 1
    finally:
        smtp.stop()
    assert not problems, problems


def test_find_and_lists_fit_a_phone_in_the_dark(browser, env):
    context, page, problems = signed_in_page(
        browser, env, width=390, height=844, dark=True
    )
    seed(context, env)
    go(page, env, "recruiters?tab=find")
    page.wait_for_selector("#main .rcx-proposal")
    assert page.evaluate("() => document.documentElement.scrollWidth - innerWidth") <= 1
    shot(page, "06-phone-find")
    page.click("#rcxSave")
    page.wait_for_selector(".toast:has-text('Saved 3 new contacts')")
    go(page, env, "recruiters?tab=lists")
    page.locator("#main .rcx-presets button", has_text="Has email").click()
    page.locator(".modal .actions button", has_text="Create list").click()
    page.wait_for_selector("#main .rcx-members")
    assert page.evaluate("() => document.documentElement.scrollWidth - innerWidth") <= 1
    shot(page, "07-phone-list")
    assert not problems, problems
