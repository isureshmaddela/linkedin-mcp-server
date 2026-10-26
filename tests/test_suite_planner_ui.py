"""Planner in a real browser against the single-user server: every tab renders, a chore is added and done,
the shopping list updates live when someone else adds to it, a relative ticks the shared list on a phone, a bill is
paid, and easy mode with extra-large text still fits a phone screen."""

# ruff: noqa: F811
from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

patchright = pytest.importorskip("patchright.sync_api")

import auto_apply_assistant as local  # noqa: E402
from aira import suite  # noqa: E402
from aira.suite import kit, public  # noqa: E402
from test_careers import env, stub  # noqa: E402, F401
from test_leads_ui import browser, go, signed_in_page, until  # noqa: E402, F401


SHOTS = Path(os.environ["PLANNER_SHOTS"]) if os.environ.get("PLANNER_SHOTS") else None


def shot(page, name):
    if SHOTS is not None:
        SHOTS.mkdir(parents=True, exist_ok=True)
        page.screenshot(path=str(SHOTS / f"{name}.png"), full_page=True)


class _NoModel:
    def model(self, prompt):
        return None


@pytest.fixture(autouse=True)
def local_index():
    kit.forget_schema()
    public.set_index(local._public_link_index)
    previous = suite.gateway()
    suite.set_gateway(_NoModel())
    yield
    suite.set_gateway(previous)


def call(context, env, method, path, body=None):
    response = context.request.fetch(
        env.base + path,
        method=method,
        data=json.dumps(body) if body is not None else None,
        headers={
            "Content-Type": "application/json",
            "X-Requested-With": "aira",
            "Origin": env.base,
        },
    )
    assert response.status < 300, (path, response.status, response.text())
    return response.json()


def real_problems(problems):
    # The page asks who is signed in before the session cookie is used; that first 401 is expected.
    return [p for p in problems if "401" not in p]


def test_planner_tabs_chores_and_live_shopping(browser, env):
    context, page, problems = signed_in_page(browser, env)
    go(page, env, "planner")
    page.wait_for_selector("#main h1:has-text('Planner')")
    page.wait_for_selector("text=Nothing for today")
    # a quick task for today, then done with the big button
    page.fill("input[aria-label='Add something to do today']", "Water the plants")
    page.click("#main button:has-text('Add')")
    page.wait_for_selector(".life-line:has-text('Water the plants')")
    page.click("button[aria-label='Done: Water the plants']")
    page.wait_for_selector("text=Nothing for today")
    # a repeating chore through the form
    go(page, env, "planner?tab=chores")
    page.click("#main button:has-text('New task')")
    page.fill(".modal input[name=title]", "Take out the bins")
    page.select_option(".modal select[name=repeat]", "weekly")
    page.check(".modal input[name=d0]")
    page.check(".modal input[name=d3]")
    page.fill(".modal input[name=points]", "5")
    page.click(".modal button:has-text('Add task')")
    page.wait_for_selector(".life-line:has-text('Take out the bins')")
    assert (
        "Every Mon, Thu"
        in page.locator(".life-line:has-text('Take out the bins')").inner_text()
    )

    # shopping: add an item, tick it, and see someone else's addition arrive by itself
    go(page, env, "planner?tab=shopping")
    page.wait_for_selector("text=Add to Groceries")
    page.fill("input[aria-label='Item']", "Milk")
    page.fill("input[aria-label='How many']", "2")
    page.keyboard.press("Enter")
    page.wait_for_selector(".life-line:has-text('Milk (2)')")
    assert page.locator("section:has-text('Milk, cheese and eggs')").count() >= 1
    lists = call(context, env, "GET", "/api/planner/lists")["lists"]
    call(
        context,
        env,
        "POST",
        f"/api/planner/lists/{lists[0]['id']}/items",
        {"name": "Bread"},
    )
    page.wait_for_selector(".life-line:has-text('Bread')", timeout=15000)
    page.click("button[aria-label='Tick Milk']")
    page.wait_for_selector("text=In the basket (1)")
    shot(page, "shopping")

    # a relative opens the shared list on a phone, ticks Bread and adds Biscuits
    page.click("#main button:has-text('Create link')")
    page.wait_for_selector(".suite-share-url")
    url = page.locator(".suite-share-url").inner_text()
    visitor = browser.new_context(viewport={"width": 390, "height": 844})
    phone = visitor.new_page()
    phone_problems: list[str] = []
    phone.on("pageerror", lambda e: phone_problems.append(str(e)))
    phone.goto(url)
    phone.wait_for_selector("h1:has-text('Groceries')")
    phone.fill("input[placeholder='Your name']", "Grandma")
    phone.click("li:has-text('Bread') button")
    phone.wait_for_selector("h2:has-text('In the basket')")
    phone.fill("input[placeholder='Something to add']", "Biscuits")
    phone.click("button:has-text('Add')")
    phone.wait_for_selector("li:has-text('Biscuits')")
    shot(phone, "public-list")
    assert phone.evaluate(
        "document.documentElement.scrollWidth <= window.innerWidth + 1"
    )
    page.wait_for_selector(".life-line:has-text('Biscuits')", timeout=15000)
    page.wait_for_selector(".life-line:has-text('Ticked by Grandma')", timeout=15000)
    visitor.close()
    assert phone_problems == []

    # bills, budget, notes and rewards render; a bill is paid and lands in the budget
    go(page, env, "planner?tab=bills")
    page.click("#main button:has-text('Add a bill')")
    page.fill(".modal input[name=name]", "Electricity")
    page.fill(".modal input[name=amount_cents]", "85.50")
    page.click(".modal button:has-text('Add bill')")
    page.wait_for_selector(".life-card:has-text('Electricity')")
    page.click(".life-card:has-text('Electricity') button:has-text('Paid')")
    page.wait_for_selector(
        ".life-card:has-text('Electricity') button:has-text('Undo last payment')"
    )
    go(page, env, "planner?tab=budget")
    page.wait_for_selector("td:has-text('Bill: Electricity')")
    go(page, env, "planner?tab=notes")
    page.click("#main button:has-text('New note')")
    page.fill(".modal input[name=title]", "Wifi")
    page.fill(".modal textarea[name=body]", "Password on the router")
    page.click(".modal button:has-text('Save note')")
    page.wait_for_selector(".life-card:has-text('Wifi')")
    go(page, env, "planner?tab=rewards")
    page.click("#main button:has-text('Add a reward')")
    page.fill(".modal input[name=title]", "Pick the film")
    page.fill(".modal input[name=points]", "20")
    page.click(".modal button:has-text('Add')")
    page.wait_for_selector(".life-line:has-text('Pick the film')")
    go(page, env, "planner")
    page.wait_for_selector("text=Coming up this week")
    shot(page, "my-day")
    go(page, env, "planner?tab=budget")
    page.wait_for_selector("text=By category")
    shot(page, "budget")
    assert real_problems(problems) == []


def test_planner_fits_a_phone_in_easy_mode(browser, env):
    context, page, problems = signed_in_page(browser, env, width=390, height=844)
    call(
        context,
        env,
        "POST",
        "/api/life/prefs",
        {"easy_mode": True, "text_size": "xlarge"},
    )
    call(
        context,
        env,
        "POST",
        "/api/planner/tasks",
        {
            "title": "Call the doctor about the appointment next week",
            "due_date": kit.now()[:10],
        },
    )
    for tab in ("day", "chores", "shopping", "budget", "bills", "notes", "rewards"):
        go(page, env, f"planner?tab={tab}")
        page.reload()
        page.wait_for_selector("#main h1:has-text('Planner')")
        page.wait_for_timeout(400)
        assert page.evaluate("document.body.classList.contains('easy')")
        shot(page, f"phone-{tab}")
        overflow = page.evaluate(
            "document.documentElement.scrollWidth - window.innerWidth"
        )
        assert overflow <= 1, (tab, overflow)
    assert real_problems(problems) == []
