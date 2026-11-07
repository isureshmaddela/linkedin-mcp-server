"""Careers in a real browser: the resume editor in a job's Documents tab and the applications summary.

The editor shows the tailored resume and cover letter beside the job description; "Regenerate with my notes"
writes new drafts (the model is a stub); "Approve and save" keeps an approved version that applications send.
The summary on top of Applications > Pipeline counts every application, sent by you or automatically, and
each count opens the list behind it. It fails on any script error. Screenshots go to ``CAREERS_APPLY_SHOTS``.
"""

# ruff: noqa: F811
from __future__ import annotations

import json
import os
import re
from pathlib import Path

import pytest

patchright = pytest.importorskip("patchright.sync_api")

import auto_apply_assistant as app  # noqa: E402
from test_career_copilot import PROFILE, RESUME  # noqa: E402
from test_careers import env, stub  # noqa: E402, F401
from test_leads_ui import browser, go, signed_in_page, until  # noqa: E402, F401

SHOTS = (
    Path(os.environ["CAREERS_APPLY_SHOTS"])
    if os.environ.get("CAREERS_APPLY_SHOTS")
    else None
)
DESCRIPTION = (
    "We are hiring a Cloud Architect. Requirements: AWS, Kubernetes, Terraform, Python. "
    "You will design our platform and lead migrations."
)


def shot(page, name: str) -> None:
    if SHOTS is None:
        return
    SHOTS.mkdir(parents=True, exist_ok=True)
    page.screenshot(path=str(SHOTS / f"{name}.png"))


def post(context, env, path: str, body: dict) -> dict:
    headers = {
        "Content-Type": "application/json",
        "X-Requested-With": "aira",
        "Origin": env.base,
    }
    reply = context.request.post(
        env.base + path, data=json.dumps(body), headers=headers
    )
    assert reply.ok, reply.text()
    return reply.json()


def seed(context, env) -> list[dict]:
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
    return [
        post(
            context,
            env,
            "/api/jobs/import",
            {
                "manual": {
                    "title": title,
                    "company": company,
                    "location": "Remote",
                    "description": DESCRIPTION,
                }
            },
        )["job"]
        for title, company in [
            ("Cloud Architect", "Acme Cloud"),
            ("Platform Lead", "Corner Bakery"),
        ]
    ]


@pytest.fixture
def model(monkeypatch):
    prompts: list[str] = []

    def fake(_model: str, prompt: str) -> dict[str, str]:
        prompts.append(prompt)
        return {
            "tailored_resume": f"Jordan Example\nKubernetes migrations first. DRAFT{len(prompts)}",
            "cover_letter": f"Dear Acme Cloud,\nI would like to lead your migrations. LETTER{len(prompts)}",
        }

    monkeypatch.setattr(app, "_ollama_json", fake)
    return prompts


def no_overflow(page) -> bool:
    return page.evaluate(
        "() => document.documentElement.scrollWidth <= document.documentElement.clientWidth + 1"
    )


def test_the_resume_editor_regenerates_with_notes_and_approves_a_version(
    browser, env, model
):
    context, page, problems = signed_in_page(browser, env, width=1440, height=900)
    job = seed(context, env)[0]
    go(page, env, "applications?tab=pipeline")
    page.wait_for_selector(".cas")
    page.evaluate(
        "(id) => window.Aira.openJob(id, { tab: 'documents' })",
        job["job_id"],
        isolated_context=False,
    )
    page.wait_for_selector(".drawer .cre-editor")
    # The job description is beside the editor, and nothing is drafted yet.
    assert "Cloud Architect" in page.locator(".cre-jd-text").inner_text()
    assert page.locator("#creResume").input_value() == ""
    page.wait_for_selector(".cre-history p")
    assert "No saved versions yet" in page.locator(".cre-history").inner_text()

    # Write the drafts with my notes: they reach the prompt as the person's guidance.
    page.fill("#creNotes", "Lead with the Kubernetes migrations.")
    page.locator(".cre-notes button", has_text="Write tailored drafts").click()
    until(
        page,
        lambda: (
            "DRAFT1"
            in (
                page.locator("#creResume").input_value()
                if page.locator("#creResume").count()
                else ""
            )
        ),
        what="the regenerated draft in the editor",
    )
    assert "Lead with the Kubernetes migrations." in model[0]
    page.wait_for_selector(".cre-version")
    history = page.locator(".cre-history").inner_text()
    assert (
        "Version 1" in history
        and "Draft" in history
        and "Rewritten with your notes" in history
    )
    assert "not approved yet" in page.locator(".cre-in-use").inner_text()
    shot(page, "01-editor-draft")

    # The cover letter is the other half of the switch.
    page.locator(".cre-switch-btn", has_text="Cover letter").click()
    assert (
        page.locator("#creLetter").is_visible()
        and not page.locator("#creResume").is_visible()
    )
    assert "LETTER1" in page.locator("#creLetter").input_value()
    page.locator(".cre-switch-btn", has_text="Resume").click()

    # Edit in place, then approve: approving needs "I reviewed both".
    page.fill(
        "#creResume",
        page.locator("#creResume").input_value() + "\nLed the EKS migration.",
    )
    assert page.locator(".cre-dirty").inner_text() == "Unsaved changes"
    page.locator(".cre-approve button", has_text="Approve and save").click()
    page.wait_for_selector(".toast.error")
    page.check("#creReviewed")
    page.locator(".cre-approve button", has_text="Approve and save").click()
    # Approving moves the job to Ready to apply and opens the Apply tab.
    page.wait_for_selector(".drawer .step-list")
    page.locator(".drawer .tabs [role='tab']", has_text="Documents").click()
    page.wait_for_selector(".cre-in-use strong")
    until(
        page,
        lambda: "version 2, approved" in page.locator(".cre-in-use").inner_text(),
        what="the approved version in use",
    )
    first = page.locator(".cre-version").first.inner_text()
    assert "Version 2" in first and "Approved" in first
    # No stray "null" from an empty optional part.
    assert "null" not in page.locator(".drawer-body").inner_text()
    assert "Led the EKS migration." in page.locator("#creResume").input_value()
    detail = page.evaluate(
        "(id) => window.Aira.api(`/api/jobs/${encodeURIComponent(id)}`)",
        job["job_id"],
        isolated_context=False,
    )
    assert detail["job"]["stage"] == "ready_to_apply"
    shot(page, "02-editor-approved")

    # An older version opens in the editor and counts as an unsaved change.
    page.locator(".cre-version button", has_text="Open in editor").first.click()
    until(
        page,
        lambda: "EKS" not in page.locator("#creResume").input_value(),
        what="version 1 in the editor",
    )
    assert page.locator(".cre-dirty").inner_text() == "Unsaved changes"

    # On a phone the description comes first and nothing scrolls sideways.
    page.set_viewport_size({"width": 390, "height": 844})
    page.wait_for_timeout(200)
    assert no_overflow(page)
    jd = page.locator(".cre-jd").bounding_box()
    text = page.locator("#creResume").bounding_box()
    assert jd and text and jd["y"] < text["y"]
    shot(page, "03-editor-phone")
    assert not problems, problems


def test_the_summary_counts_every_application_and_opens_each_list(browser, env):
    context, page, problems = signed_in_page(browser, env, width=1366, height=900)
    first, _second = seed(context, env)
    # One sent by you another way; the other job is not an application yet.
    post(
        context,
        env,
        f"/api/jobs/{first['job_id']}/submit",
        {
            "outside": True,
            "channel": "email",
            "idempotency_key": f"job:{first['job_id']}",
        },
    )
    go(page, env, "applications?tab=pipeline")
    page.wait_for_selector(".cas-tile")
    tile = lambda bucket: page.locator(f".cas-tile[data-bucket='{bucket}']")  # noqa: E731
    assert tile("submitted").locator(".cas-value").inner_text() == "1"
    assert (
        tile("submitted").locator(".cas-via[data-via='manual']").inner_text()
        == "1 by you"
    )
    assert (
        tile("submitted").locator(".cas-via[data-via='auto']").inner_text()
        == "0 automatically"
    )
    for bucket in ("in_process", "needs_you", "failed", "interviews"):
        assert tile(bucket).locator(".cas-value").inner_text() == "0"
    assert "1 in all" in page.locator(".cas-total").inner_text()
    # The board is still below the summary.
    assert page.locator(".kanban").count() == 1
    shot(page, "04-summary")

    # A count opens the list behind it.
    tile("submitted").locator(".cas-via[data-via='manual']").click()
    page.wait_for_url(re.compile(r"show=submitted&via=manual"))
    page.wait_for_selector(".cas-list .cas-row")
    rows = page.locator(".cas-list .cas-row")
    assert rows.count() == 1 and "Cloud Architect" in rows.first.inner_text()
    assert "Sent by you" in rows.first.inner_text()
    assert page.locator(".cas-via[aria-current='page']").count() == 1
    assert page.locator(".kanban").count() == 0
    # The row opens the job.
    rows.first.locator("button", has_text="Open").click()
    page.wait_for_selector(".drawer .step-list")
    page.keyboard.press("Escape")
    page.wait_for_selector(".drawer", state="detached")

    # An empty count says so; "Back to the board" returns.
    tile("failed").locator(".cas-main").click()
    page.wait_for_url(re.compile(r"show=failed"))
    page.wait_for_selector(".cas-list .empty")
    page.locator(".cas-back").click()
    page.wait_for_selector(".kanban")

    # Phone, dark: two tiles a row and no sideways scroll.
    page.set_viewport_size({"width": 390, "height": 844})
    page.emulate_media(color_scheme="dark")
    page.wait_for_timeout(200)
    assert no_overflow(page)
    boxes = [tile(b).bounding_box() for b in ("submitted", "in_process")]
    assert boxes[0] and boxes[1] and abs(boxes[0]["y"] - boxes[1]["y"]) < 2
    shot(page, "05-summary-phone-dark")
    assert not problems, problems
