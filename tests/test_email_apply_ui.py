"""Applying by email in a real browser: the Apply tab card, its preview and the send, and the plan option.

The mail server is the loopback stand-in; the page fails on any script error. Screenshots go to
``EMAIL_APPLY_SHOTS`` when set.
"""

# ruff: noqa: F811
from __future__ import annotations

import os
from pathlib import Path

import pytest

patchright = pytest.importorskip("patchright.sync_api")

from smtp_stub import SmtpStub  # noqa: E402
from test_career_copilot import PROFILE, RESUME  # noqa: E402
from test_careers import env, stub  # noqa: E402, F401
from test_leads_ui import browser, go, signed_in_page, until  # noqa: E402, F401
from test_suite_careers_apply_ui import post  # noqa: E402

SHOTS = (
    Path(os.environ["EMAIL_APPLY_SHOTS"])
    if os.environ.get("EMAIL_APPLY_SHOTS")
    else None
)
APPLY_TO = "talent@acme.test"
DESCRIPTION = (
    "We are hiring a Cloud Architect. Requirements: AWS, Kubernetes, Terraform, Python. "
    f"To apply, please send your resume to {APPLY_TO}. Reference: ENG-2041."
)


def shot(page, name: str) -> None:
    if SHOTS is not None:
        SHOTS.mkdir(parents=True, exist_ok=True)
        page.screenshot(path=str(SHOTS / f"{name}.png"))


@pytest.fixture
def smtp():
    server = SmtpStub().start()
    yield server
    server.stop()


@pytest.mark.parametrize("dark", [False, True])
def test_the_apply_tab_previews_and_sends_an_email_application(
    browser, env, smtp, dark
):
    context, page, problems = signed_in_page(
        browser, env, width=1366, height=900, dark=dark
    )
    post(
        context,
        env,
        "/api/profile",
        {
            "resume": RESUME,
            "profile_url": PROFILE["linkedin_url"],
            "resume_name": "resume.txt",
            "application_profile": PROFILE,
        },
    )
    post(
        context,
        env,
        "/api/suite/email",
        {
            "from_email": "jordan@example.com",
            "smtp_host": "127.0.0.1",
            "smtp_port": smtp.port,
            "smtp_security": "none",
        },
    )
    job = post(
        context,
        env,
        "/api/jobs/import",
        {
            "manual": {
                "title": "Cloud Architect",
                "company": "Acme",
                "location": "Remote",
                "description": DESCRIPTION,
            }
        },
    )["job"]
    go(page, env, "applications?tab=pipeline")
    page.evaluate(
        "(id) => window.Aira.openJob(id, { tab: 'apply' })",
        job["job_id"],
        isolated_context=False,
    )
    card = page.locator(
        ".drawer .card", has=page.locator("h3", has_text="Apply by email")
    )
    card.wait_for()
    assert APPLY_TO in card.inner_text()
    assert "send your resume to" in card.inner_text()
    card.locator("button", has_text="Preview and send").click()
    modal = page.locator(".modal")
    modal.wait_for()
    text = modal.inner_text()
    assert APPLY_TO in text and "Jordan Example <jordan@example.com>" in text
    assert "(ENG-2041)" in modal.locator("input[type=text]").input_value()
    assert ".pdf" in text, "the resume is listed as attached"
    shot(page, f"preview-{'dark' if dark else 'light'}")
    modal.locator("button", has_text="Send application").click()
    page.locator(
        ".modal button", has_text="Send application"
    ).last.click()  # the confirmation
    page.wait_for_selector(".toast.success")
    until(page, lambda: len(smtp.messages) == 1, what="the email at the mail server")
    assert smtp.messages[0]["to"] == [APPLY_TO]
    page.wait_for_selector(".drawer .card h3:has-text('Submitted')")
    assert "Apply by email" not in page.locator(".drawer-body").inner_text()
    shot(page, f"sent-{'dark' if dark else 'light'}")
    assert problems == [], problems


def test_the_plan_editor_offers_apply_by_email_off_by_default(browser, env):
    context, page, problems = signed_in_page(browser, env)
    go(page, env, "autopilot")
    page.locator("button", has_text="New search plan").first.click()
    page.locator(".modal summary", has_text="More options").click()
    option = page.locator(
        ".modal label.check", has_text="Apply by email when the posting asks for it"
    )
    option.wait_for()
    assert not option.locator("input").is_checked()
    assert problems == [], problems
