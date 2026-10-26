"""Health in a real browser: the day's medicines with a Taken button, every tab, a reading with its gentle note,
an appointment, the emergency card shared and opened by a visitor, and easy mode on a phone."""

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
    "medicines": "Medicines",
    "readings": "Readings",
    "diary": "Diary",
    "appointments": "Appointments",
    "card": "Emergency card",
    "caregivers": "Caregivers",
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


def test_health_in_the_browser(browser, env):
    kit.forget_schema()
    public.set_index(local._public_link_index)
    context, page, problems = signed_in_page(browser, env)
    post(context, env, "/api/suite/business", {"name": "Home", "timezone": "UTC"})
    now = kit.utcnow().strftime("%H:%M")
    post(
        context,
        env,
        "/api/care/medicines",
        {
            "name": "Metformin",
            "dose": "500 mg",
            "times": now,
            "stock": 30,
            "with_food": "With food",
        },
    )
    post(
        context,
        env,
        "/api/care/medicines",
        {"name": "Paracetamol", "as_needed": True, "min_gap_hours": 4},
    )

    go(page, env, "care")
    page.wait_for_selector(".care-slot")
    assert "Metformin 500 mg" in page.locator(".care-slot").first.inner_text()
    # The reminder comes up by itself (the life pulse) and is answered from the dialog.
    page.wait_for_selector(".modal:has-text('Time for your medicine')")
    assert "With food" in page.locator(".modal").inner_text()
    page.click(".modal button:has-text('Taken')")
    page.wait_for_selector(".modal", state="detached")
    go(page, env, "care?tab=medicines")
    go(page, env, "care")
    until(
        page,
        lambda: page.locator(".care-slot.taken").count() == 1,
        what="the dose to show as taken",
    )
    assert page.locator(".care-slot.taken button:has-text('Taken')").count() == 0
    page.click("button:has-text('I took Paracetamol')")
    until(
        page,
        lambda: (
            "recorded"
            in page.locator("#toasts, .toast-host, body").first.inner_text().lower()
        ),
        what="the as-needed toast",
    )
    page.click(".care-mood:has-text('Good')")
    until(
        page,
        lambda: page.locator(".care-mood[aria-pressed='true']").count() == 1,
        what="the mood to be saved",
    )

    for tab, label in TABS.items():
        go(page, env, f"care?tab={tab}")
        page.wait_for_selector("#main [role='tablist']")
        page.wait_for_timeout(300)
        assert (
            page.locator("#main [role='tab'][aria-selected='true']").inner_text()
            == label
        )

    # A high reading comes back with a gentle, non-diagnostic note.
    go(page, env, "care?tab=readings")
    page.click("button:has-text('Add blood pressure')")
    page.fill(".modal input[name=value1]", "150")
    page.fill(".modal input[name=value2]", "95")
    page.click(".modal button:has-text('Save')")
    page.wait_for_selector(".modal:has-text('above the usual range')")
    page.click(".modal button:has-text('OK')")
    page.wait_for_selector("td:has-text('150/95 mmHg')")

    go(page, env, "care?tab=appointments")
    page.click("button:has-text('Add an appointment')")
    page.fill(".modal input[name=title]", "Dr Patel")
    page.fill(".modal input[name=at]", "2030-05-04T10:30")
    page.click(".modal button:has-text('Save')")
    page.wait_for_selector(".care-appt:has-text('Dr Patel')")

    # The emergency card: saved, shared, and opened by a visitor who is not signed in.
    go(page, env, "care?tab=card")
    page.fill("textarea[name=allergies]", "Penicillin")
    page.fill("input[name=c0_name]", "Sam")
    page.fill("input[name=c0_phone]", "+1 555 0100")
    page.click("button:has-text('Save the card')")
    page.wait_for_selector(".care-emergency:has-text('Penicillin')")
    page.click("button:has-text('Create link')")
    page.wait_for_selector(".suite-share-url")
    url = page.locator(".suite-share-url").inner_text()
    visitor = browser.new_context(viewport={"width": 390, "height": 844})
    card_page = visitor.new_page()
    card_page.goto(url)
    card_page.wait_for_selector("text=Emergency information")
    text = card_page.locator("main").inner_text()
    assert (
        "Penicillin" in text
        and "Metformin" in text
        and "Sam" in text
        and "150" not in text
    )
    visitor.close()

    # Easy mode with extra-large text on a phone: Today in Health fits the screen.
    post(context, env, "/api/life/prefs", {"easy_mode": True, "text_size": "xlarge"})
    page.set_viewport_size({"width": 390, "height": 844})
    page.goto(f"{env.base}/#/care")
    page.reload()
    page.wait_for_selector(".care-slot")
    page.wait_for_timeout(500)
    assert page.evaluate("document.body.classList.contains('easy')")
    assert not page.evaluate(
        "document.documentElement.scrollWidth > window.innerWidth + 2"
    )
    assert [p for p in problems if "pageerror" in p or "Uncaught" in p] == []
    assert [
        p for p in problems if "console.error" in p and "status of 4" not in p
    ] == [], problems
    context.close()
