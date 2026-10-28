"""Planner's fourth round in a real browser: a neighbour on a phone sees why the ask form did not go through right
where they are, saying yes from the pop-up alert updates the open Sell screen, Enter in a pop-up form saves it,
a savings goal never shows less than nothing when a job whose money was taken out is undone, and the chore form
does not show 0 days for a daily chore."""

# ruff: noqa: F811
from __future__ import annotations

import pytest

pytest.importorskip("patchright.sync_api")

from test_careers import env, stub  # noqa: E402, F401
from test_leads_ui import browser, go, signed_in_page, until  # noqa: E402, F401
from test_suite_planner_ui import call, local_index, real_problems, shot  # noqa: E402, F401


def test_sale_page_errors_alert_answers_enter_and_goal_floor(browser, env):
    context, page, problems = signed_in_page(browser, env)
    me = call(context, env, "GET", "/api/life/me")["profile"]
    owner = me["id"]

    # Things for sale above a side job, so the job's ask form is far down the page on a phone.
    for title in ("Pram", "Cot", "High chair", "Bike", "Scooter"):
        call(
            context,
            env,
            "POST",
            "/api/planner/sell/items",
            {
                "title": title,
                "price_cents": 2000,
                "description": "Collect any evening.\nGood condition.",
            },
            profile=owner,
        )
    job = call(
        context,
        env,
        "POST",
        "/api/planner/sell/items",
        {
            "title": "Babysitting",
            "kind": "service",
            "price_cents": 1200,
            "price_unit": "hour",
        },
        profile=owner,
    )["item"]
    link = call(context, env, "POST", "/api/planner/sell/link", {}, profile=owner)[
        "link"
    ]
    # The pram is kept for someone.
    asked = call(
        context,
        env,
        "POST",
        f"/api/public/{link['token']}/ask",
        {
            "item_id": call(
                context, env, "GET", "/api/planner/sell?status=available", profile=owner
            )["items"][-1]["id"],
            "name": "Jo",
            "contact": "jo@example.com",
        },
    )
    call(
        context,
        env,
        "POST",
        f"/api/planner/sell/requests/{asked['request']['id']}/accept",
        {},
        profile=owner,
    )

    # Savings: Enter in a pop-up form presses its main button.
    go(page, env, "planner?tab=savings")
    page.click("#main button:has-text('Save for something')")
    page.fill(".modal input[name=title]", "Summer holiday")
    page.fill(".modal input[name=target_cents]", "1200")
    page.press(".modal input[name=target_cents]", "Enter")
    page.wait_for_selector(".planner-goal:has-text('Summer holiday')")
    assert page.locator(".modal").count() == 0
    page.click(".planner-goal button:has-text('Put money aside')")
    page.fill(".modal input[name=amount_cents]", "100")
    page.press(".modal input[name=amount_cents]", "Enter")
    page.wait_for_selector(".planner-goal:has-text('$100.00 of $1,200.00')")

    # The Sell screen is open when a neighbour asks.
    go(page, env, "planner?tab=sell")
    page.wait_for_selector(".planner-job:has-text('Babysitting')")
    if page.locator(".modal").count():
        page.click(".modal button:has-text('Close')")

    # A neighbour on a phone: a contact that is not one is explained next to the form, on screen.
    visitor = browser.new_context(viewport={"width": 390, "height": 844})
    phone = visitor.new_page()
    phone_problems: list[str] = []
    phone.on("pageerror", lambda e: phone_problems.append(str(e)))
    phone.goto(env.base + link["path"])
    phone.wait_for_selector(":is(h1, h2):has-text('Jobs we can do')")
    # A thing kept for someone says so once; the hint names both buttons.
    assert phone.locator("text=Tap I want this or Ask about this").count() == 1
    pram = phone.locator(".pub-card:has-text('Pram')").inner_text()
    assert pram.count("Kept for someone") == 1, pram
    phone.click(".pub-card:has-text('Babysitting') button:has-text('Ask about this')")
    form = phone.locator(".pub-card:has-text('Babysitting') form")
    form.locator("input >> nth=0").fill("Mrs Patel")
    form.locator("input >> nth=1").fill("not a contact")
    form.locator("button:has-text('Send')").click()
    error = form.locator(".pub-ask-error")
    error.wait_for(state="visible")
    assert "phone number" in error.inner_text()

    def on_screen() -> bool:
        box = error.bounding_box()
        return bool(box) and box["y"] >= 0 and box["y"] + box["height"] <= 844

    until(phone, on_screen, what="the error to be on screen")
    assert (
        phone.evaluate("() => document.activeElement && document.activeElement.value")
        == "not a contact"
    )
    shot(phone, "ask-error-on-phone")
    form.locator("input >> nth=1").fill("07700 900123")
    form.locator("button:has-text('Send')").click()
    phone.wait_for_selector("text=Sent. The family will get in touch")
    assert phone.evaluate(
        "() => document.documentElement.scrollWidth <= window.innerWidth + 1"
    )
    visitor.close()
    assert phone_problems == []

    # The pop-up alert about it: Say yes there, and the open Sell screen follows at once (not after a reload).
    page.evaluate("() => window.Aira.life.tick()", isolated_context=False)
    page.wait_for_selector(".modal button:has-text('Say yes')", timeout=10000)
    page.click(".modal button:has-text('Say yes')")
    page.wait_for_selector(
        ".planner-job button:has-text('Done and paid')", timeout=4000
    )
    assert page.locator(".planner-job button:has-text('Say yes')").count() == 0
    shot(page, "sell-after-alert")

    # Done and paid toward the goal, the money taken out, then Undo the job: refused, the goal stays at $0.00.
    page.click(".planner-job button:has-text('Done and paid')")
    page.fill(".modal input[name=hours]", "2")
    page.select_option(".modal select[name=goal_id]", label="Summer holiday")
    page.click(".modal button:has-text('Record the job')")
    page.wait_for_selector(".planner-job:has-text('1 job done')")
    go(page, env, "planner?tab=savings")
    page.wait_for_selector(".planner-goal:has-text('$124.00 of $1,200.00')")
    page.click(".planner-goal button:has-text('Take some out')")
    page.fill(".modal input[name=amount_cents]", "124")
    page.click(".modal button:has-text('Take it out')")
    page.wait_for_selector(".planner-goal:has-text('$0.00 of $1,200.00')")
    go(page, env, "planner?tab=sell")
    page.click(
        f".planner-job:has-text('{job['title']}') li:has-text('Mrs Patel') button:has-text('Undo')"
    )
    page.click(".modal button:has-text('Undo job')")
    page.wait_for_selector("text=taken out of the savings goal")
    page.wait_for_selector(".planner-job:has-text('1 job done')")
    go(page, env, "planner?tab=savings")
    page.wait_for_selector(".planner-goal:has-text('$0.00 of $1,200.00 (0%)')")
    width = page.evaluate(
        "() => document.querySelector('.planner-progress span').style.width"
    )
    assert width == "0%"

    # A daily chore's Edit form leaves "Every how many days" empty.
    call(
        context,
        env,
        "POST",
        "/api/planner/tasks",
        {"title": "Feed the cat", "repeat": "daily"},
        profile=owner,
    )
    go(page, env, "planner?tab=chores")
    page.click(".life-line:has-text('Feed the cat') button:has-text('Edit')")
    assert page.input_value(".modal input[name=repeat_every]") == ""
    page.keyboard.press("Escape")

    # The 400s are the bad contact and the refused Undo, on purpose; 403 is the page before a person is picked.
    assert [
        p
        for p in real_problems(problems)
        if "status of 400" not in p and "status of 403" not in p
    ] == []
