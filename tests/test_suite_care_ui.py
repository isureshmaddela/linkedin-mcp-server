"""Health in a real browser: the day's medicines with a Taken button, every tab, a reading with its gentle note,
an appointment, the emergency card shared and opened by a visitor, and easy mode on a phone. Then the later
features and the bugs found in the browser: the time zone taken from the browser, "Nothing due", choosing what to
measure, a refill from Today, deleting a reading only after a question, the doctor report and its link, and care
work from a visit to a client's "I have paid" and the money received."""

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
    "report": "Doctor report",
    "card": "Emergency card",
    "caregivers": "Caregivers",
    "work": "Care work",
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
    # Saving UTC in the hub is not a choice; Health would follow the browser's zone, so choose UTC in Health.
    post(context, env, "/api/care/settings", {"timezone": "UTC"})
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
        # On a wide screen the strip is hidden: the left menu's folders stand in for it.
        page.wait_for_selector("#main [role='tablist']", state="attached")
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
    page.wait_for_selector(".care-readings li:has-text('150/95 mmHg')")

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


def dismiss_reminders(page):
    """Close the life pulse's reminder dialogs (a refill, a dose due) that may cover the page."""
    for _ in range(6):
        reminder = page.locator(".modal:has(.life-alert)")
        if not reminder.count():
            return
        reminder.first.locator("button:has-text('Close')").click()
        page.wait_for_timeout(200)


def open_tab(page, env, hash_):
    go(page, env, hash_)
    page.wait_for_timeout(600)
    dismiss_reminders(page)


def test_health_new_features_in_the_browser(browser, env):
    kit.forget_schema()
    public.set_index(local._public_link_index)
    context, page, problems = signed_in_page(browser, env)
    open_tab(page, env, "care")
    page.wait_for_selector(".suite-kpi")
    assert (
        "Nothing due" in page.locator(".suite-kpi").first.inner_text()
    )  # was a green "0 of 0"
    zone = context.request.get(
        env.base + "/api/care/overview",
        headers={"X-Requested-With": "aira", "Origin": env.base},
    ).json()["timezone"]
    browser_zone = page.evaluate(
        "() => Intl.DateTimeFormat().resolvedOptions().timeZone"
    )
    # The first visit keeps the browser's zone (medicine times used to be read as UTC).
    assert zone["name"] == browser_zone or (
        browser_zone == "UTC" and zone["source"] == "default"
    )
    post(context, env, "/api/care/settings", {"timezone": "UTC"})
    post(
        context,
        env,
        "/api/care/medicines",
        {"name": "Metformin", "times": "23:59", "stock": 3},
    )

    # Refill straight from the warning on Today.
    open_tab(page, env, "care")
    page.wait_for_selector(".care-refill")
    dismiss_reminders(page)
    page.click(".care-refill button:has-text('I got a refill')")
    page.fill(".modal input[name=amount]", "60")
    page.click(".modal button:has-text('Add to the count')")
    until(
        page,
        lambda: page.locator(".care-refill").count() == 0,
        what="the refill warning to go",
    )

    # "Add a reading" asks what was measured first.
    page.click("button:has-text('Add a reading')")
    page.click(".modal .care-choose button:has-text('Weight')")
    page.wait_for_selector(".modal:has-text('Add weight')")
    page.fill(".modal input[name=value1]", "64")
    page.click(".modal button:has-text('Save')")
    page.wait_for_selector(".modal", state="detached")

    # A reading is deleted only after a question.
    open_tab(page, env, "care?tab=readings")
    page.click(".care-chips button:has-text('Weight')")
    page.wait_for_selector(".care-readings li")
    page.click(".care-readings li button:has-text('Delete')")
    page.wait_for_selector(".modal:has-text('Delete this reading')")
    page.click(".modal button:has-text('Cancel')")
    assert page.locator(".care-readings li").count() == 1

    # The doctor report and its link, opened by the doctor on a phone.
    open_tab(page, env, "care?tab=report")
    page.wait_for_selector(".care-report:has-text('Metformin')")
    page.click("button:has-text('Make a link')")
    page.wait_for_selector(".suite-share-url")
    doctor = browser.new_context(viewport={"width": 390, "height": 844})
    doctor_page = doctor.new_page()
    doctor_page.goto(page.locator(".suite-share-url").inner_text())
    doctor_page.wait_for_selector("text=Health report")
    assert "Metformin" in doctor_page.locator("main").inner_text()
    doctor.close()

    # Care work: a client, a visit, a statement, the client says they paid, the carer marks it paid.
    open_tab(page, env, "care?tab=work")
    page.click("button:has-text('Add a client')")
    page.fill(".modal input[name=name]", "Mr Lee")
    page.fill(".modal input[name=rate_cents]", "20")
    page.click(".modal button:has-text('Save')")
    page.wait_for_selector(".care-client:has-text('Mr Lee')")
    page.click(".care-client button:has-text('Record a visit')")
    page.fill(".modal input[name=hours]", "1.5")
    page.fill(".modal input[name=tasks]", "lunch, a walk")
    page.fill(".modal textarea[name=notes]", "Key under the mat")
    page.click(".modal button:has-text('Save')")
    page.wait_for_selector(".care-visits li:has-text('$30.00')")
    page.click(".care-client button:has-text('Bill now')")
    page.click(".modal button:has-text('Make the statement')")
    page.wait_for_selector(".care-statement:has-text('CARE-0001')")
    page.click(".care-statement button:has-text('Copy link')")
    opener = ".care-statement a:has-text('Open the client')"
    page.wait_for_selector(opener)
    link = page.locator(opener).get_attribute("href")
    client = browser.new_context(viewport={"width": 390, "height": 844})
    client_page = client.new_page()
    client_page.goto(link)
    client_page.wait_for_selector("text=Care visits for Mr Lee")
    assert "Key under the mat" not in client_page.locator("main").inner_text()
    client_page.click("button:has-text('I have paid')")
    client_page.wait_for_selector("text=has been told you paid")
    assert not client_page.evaluate(
        "document.documentElement.scrollWidth > window.innerWidth + 2"
    )
    client.close()
    page.reload()  # the same address: reload instead of navigating
    page.wait_for_timeout(600)
    dismiss_reminders(page)
    page.wait_for_selector(".care-statement:has-text('says they paid')")
    page.click(".care-statement button:has-text('Mark paid')")
    page.wait_for_selector(".care-statement.paid")
    assert (
        "$30.00" in page.locator(".suite-kpi:has-text('Paid this month')").inner_text()
    )

    # Every tab fits a phone.
    page.set_viewport_size({"width": 390, "height": 844})
    for tab in TABS:
        open_tab(page, env, f"care?tab={tab}")
        page.wait_for_selector("#main [role='tablist']")
        page.wait_for_timeout(300)
        assert not page.evaluate(
            "document.documentElement.scrollWidth > window.innerWidth + 2"
        ), tab
    assert [p for p in problems if "pageerror" in p or "Uncaught" in p] == []
    assert [
        p for p in problems if "console.error" in p and "status of 4" not in p
    ] == [], problems
    context.close()
