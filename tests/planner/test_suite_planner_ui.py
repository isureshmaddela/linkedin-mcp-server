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


def call(context, env, method, path, body=None, profile=""):
    response = context.request.fetch(
        env.base + path,
        method=method,
        data=json.dumps(body) if body is not None else None,
        headers={
            "Content-Type": "application/json",
            "X-Requested-With": "aira",
            "Origin": env.base,
            **({"X-Aira-Profile": profile} if profile else {}),
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
    # The page is named after the open tab; the app's name is in the bar above.
    page.wait_for_selector("#main .page-head h1:has-text('My day')")
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
    for tab in (
        "day",
        "chores",
        "shopping",
        "meals",
        "budget",
        "savings",
        "bills",
        "sell",
        "notes",
        "rewards",
    ):
        go(page, env, f"planner?tab={tab}")
        page.reload()
        page.wait_for_selector("#main .page-head h1")
        page.wait_for_timeout(400)
        assert page.evaluate("document.body.classList.contains('easy')")
        shot(page, f"phone-{tab}")
        overflow = page.evaluate(
            "document.documentElement.scrollWidth - window.innerWidth"
        )
        assert overflow <= 1, (tab, overflow)
    assert real_problems(problems) == []


def test_meals_selling_pocket_money_and_switching_person(browser, env):
    """The newer journeys in a real browser: a recipe planned and shopped for, a thing sold through the public sale
    page on a phone, pocket money paid out; an empty shopping list prints no stray "null"; and a person added or
    switched while Planner is open shows up without reloading the page."""
    context, page, problems = signed_in_page(browser, env)
    go(page, env, "planner?tab=shopping")
    page.wait_for_selector("text=Nothing to buy")
    assert "null" not in page.locator("#main").inner_text().split(chr(10))

    # meals: a recipe, planned for tonight, its ingredients on the shopping list
    go(page, env, "planner?tab=meals")
    page.click("#main button:has-text('New recipe')")
    page.fill(".modal input[name=title]", "Fish pie")
    page.fill(
        ".modal textarea[name=ingredients_text]",
        "4 potatoes" + chr(10) + "Fish" + chr(10) + "Peas",
    )
    page.click(".modal button:has-text('Save recipe')")
    page.wait_for_selector(".life-line:has-text('Fish pie')")
    page.click(".life-line:has-text('Fish pie') button:has-text('Plan it')")
    page.click(".modal button:has-text('Plan it')")
    page.wait_for_selector(".planner-today .life-line-text:has-text('Fish pie')")
    page.click('#main button:has-text("Shop for this week\'s meals")')
    page.wait_for_selector("text=Added 3 items")
    shot(page, "meals")
    go(page, env, "planner?tab=shopping")
    page.wait_for_selector(".life-line:has-text('potatoes (4)')")

    # selling: a neighbour asks on the public page (phone), the adult keeps it for them and marks it sold
    go(page, env, "planner?tab=sell")
    page.click("#main button:has-text('Sell something')")
    page.fill(".modal input[name=title]", "Pram")
    page.fill(".modal input[name=price_cents]", "60")
    page.click(".modal button:has-text('Put it up for sale')")
    page.wait_for_selector(".life-card:has-text('Pram')")
    page.click("#main button:has-text('Create link')")
    page.wait_for_selector(".suite-share-url")
    url = page.locator(".suite-share-url").inner_text()
    visitor = browser.new_context(viewport={"width": 390, "height": 844})
    phone = visitor.new_page()
    phone_problems: list[str] = []
    phone.on("pageerror", lambda e: phone_problems.append(str(e)))
    phone.goto(url)
    phone.click("button:has-text('I want this')")
    phone.click("button:has-text('Send')")
    phone.wait_for_selector("text=Write your name.")
    phone.fill("form input >> nth=0", "Sam")
    phone.fill("form input >> nth=1", "not a contact")
    phone.click("button:has-text('Send')")
    phone.wait_for_selector("text=Write a phone number")
    phone.fill("form input >> nth=1", "sam@example.com")
    phone.fill("form input >> nth=2", "55")
    phone.click("button:has-text('Send')")
    phone.wait_for_selector("text=Sent. The family will get in touch")
    assert phone.locator("text=Write your name.").count() == 0
    shot(phone, "public-sale")
    assert phone.evaluate(
        "document.documentElement.scrollWidth <= window.innerWidth + 1"
    )
    visitor.close()
    assert phone_problems == []
    page.reload()
    page.wait_for_selector("text=Sam offers $55.00")
    if page.locator(".modal button:has-text('Keep it for them')").count():
        page.click(
            ".modal button:has-text('Close')"
        )  # the pulse alert about the same request
    page.click("#main button:has-text('Keep it for them')")
    page.wait_for_selector("text=Kept for Sam")
    page.click(".life-card:has-text('Pram') button:has-text('Sold')")
    assert page.locator(".modal input[name=sold_cents]").input_value() == "55.00"
    page.click(".modal button:has-text('Mark sold')")
    page.wait_for_selector(".life-card:has-text('Sold for $55.00')")
    shot(page, "sell")

    # a child is added while Planner is open: the chore form lists her without a reload
    owner = call(context, env, "GET", "/api/life/me")["profile"]["id"]
    mia = call(
        context,
        env,
        "POST",
        "/api/life/profiles",
        {"name": "Mia", "age_group": "child"},
    )["profile"]
    call(
        context,
        env,
        "POST",
        "/api/planner/tasks",
        {"title": "Hoover", "assignee": mia["id"], "points": 120},
        profile=owner,
    )
    go(page, env, "planner?tab=chores")
    page.wait_for_selector(".life-line:has-text('Hoover')")
    page.click("#main .btn-row button:has-text('New task')")
    assert any(
        "Mia" in o
        for o in page.locator(".modal select[name=assignee] option").all_inner_texts()
    )
    page.keyboard.press("Escape")
    hoover = call(context, env, "GET", "/api/planner/tasks?scope=family", profile=owner)
    call(
        context,
        env,
        "POST",
        f"/api/planner/tasks/{hoover['tasks'][0]['id']}/done",
        {},
        profile=owner,
    )

    # pocket money for her points
    go(page, env, "planner?tab=rewards")
    page.click("#main button:has-text('Set up pocket money')")
    page.fill(".modal input[name=per_100_cents]", "1.00")
    page.click(".modal button:has-text('Save')")
    page.wait_for_selector("text=100 points are worth $1.00")
    page.click("#main button:has-text('Pay out')")
    page.click(".modal button:has-text('Pay out')")
    page.wait_for_selector("text=Mia: $1.20")
    go(page, env, "planner?tab=budget")
    page.wait_for_selector("td:has-text('Pocket money: Mia')")
    page.wait_for_selector("td:has-text('Sold: Pram')")
    assert real_problems(problems) == []


def test_turns_side_jobs_savings_and_a_list_that_stops_polling(browser, env):
    """The third round in a real browser: a chore the children take turns on, a side job asked for on the public
    page and recorded as done and paid toward a savings goal, the budget's last six months, usual items that follow
    the list, and a removed shopping list whose screen stops asking for changes (it used to keep polling, and
    every visit to the Shopping tab added another loop)."""
    context, page, problems = signed_in_page(browser, env)
    me = call(context, env, "GET", "/api/life/me")["profile"]
    owner = me["id"]
    for name, age in (("Mia", "child"), ("Leo", "teen")):
        call(
            context,
            env,
            "POST",
            "/api/life/profiles",
            {"name": name, "age_group": age},
            profile=owner,
        )
    go(page, env, "planner?tab=chores")
    page.click(
        f".life-picker button:has-text('{me['name']}')"
    )  # a household now: who is using it
    page.click("#main .btn-row button:has-text('New task')")
    page.fill(".modal input[name=title]", "Wash up")
    page.select_option(".modal select[name=repeat]", "daily")
    page.check(".modal label:has-text('Mia') input")
    page.check(".modal label:has-text('Leo') input")
    page.click(".modal button:has-text('Add task')")
    page.wait_for_selector(".life-line:has-text('Takes turns: Mia, Leo (next: Leo)')")

    # savings: a goal and money put aside
    go(page, env, "planner?tab=savings")
    page.wait_for_selector("text=Nothing saved for yet")
    page.click("#main button:has-text('Save for something')")
    page.fill(".modal input[name=title]", "Summer holiday")
    page.fill(".modal input[name=target_cents]", "1200")
    page.click(".modal button:has-text('Start saving')")
    page.click(".planner-goal button:has-text('Put money aside')")
    page.fill(".modal input[name=amount_cents]", "100")
    page.click(".modal button:has-text('Put it aside')")
    page.wait_for_selector(".planner-goal:has-text('$100.00 of $1,200.00')")
    assert (
        page.locator(".planner-goal [role=progressbar]").get_attribute("aria-valuenow")
        == "8"
    )

    # a side job: offered, asked for on a phone, said yes to, done and paid toward the goal
    go(page, env, "planner?tab=sell")
    page.click("#main button:has-text('Offer a side job')")
    page.fill(".modal input[name=title]", "Dog walking")
    page.fill(".modal input[name=price_cents]", "12")
    page.click(".modal button:has-text('Offer it')")
    page.wait_for_selector(".planner-job:has-text('$12.00 an hour')")
    page.click("#main button:has-text('Create link')")
    page.wait_for_selector(".suite-share-url")
    url = page.locator(".suite-share-url").inner_text()
    visitor = browser.new_context(viewport={"width": 390, "height": 844})
    phone = visitor.new_page()
    phone_problems: list[str] = []
    phone.on("pageerror", lambda e: phone_problems.append(str(e)))
    phone.goto(url)
    phone.wait_for_selector(":is(h1, h2):has-text('Jobs we can do')")
    phone.click("button:has-text('Ask about this')")
    assert phone.locator("text=Your offer").count() == 0
    phone.fill("form input >> nth=0", "Mrs Patel")
    phone.fill("form input >> nth=1", "07700 900123")
    phone.click("button:has-text('Send')")
    phone.wait_for_selector("text=Sent. The family will get in touch")
    assert phone.evaluate(
        "document.documentElement.scrollWidth <= window.innerWidth + 1"
    )
    visitor.close()
    assert phone_problems == []
    page.reload()
    page.wait_for_selector(".planner-job:has-text('Mrs Patel')")
    if page.locator(".modal button:has-text('Say yes')").count():
        page.click(".modal button:has-text('Close')")  # the pulse alert about it
    page.click(".planner-job button:has-text('Say yes')")
    page.click(".planner-job button:has-text('Done and paid')")
    page.fill(".modal input[name=hours]", "2")
    page.select_option(".modal select[name=goal_id]", label="Summer holiday")
    page.click(".modal button:has-text('Record the job')")
    page.wait_for_selector(".planner-job:has-text('1 job done')")
    page.wait_for_selector(
        ".suite-kpi:has-text('Earned this month'):has-text('$24.00')"
    )
    shot(page, "side-job")
    go(page, env, "planner?tab=savings")
    page.wait_for_selector(".planner-goal:has-text('$124.00 of $1,200.00')")
    go(page, env, "planner?tab=budget")
    page.wait_for_selector("td:has-text('Side job: Dog walking for Mrs Patel')")
    page.wait_for_selector("section:has-text('Last 6 months')")

    # shopping: usual items follow the list; a removed list stops polling
    go(page, env, "planner?tab=shopping")
    page.fill("input[aria-label='Item']", "Bananas")
    page.keyboard.press("Enter")
    page.click("button[aria-label='Tick Bananas']")
    page.click("#main button:has-text('Clear ticked items')")
    page.wait_for_selector("button:has-text('+ Bananas')")
    page.click("#main button:has-text('New list')")
    page.fill(".modal input[name=name]", "Pharmacy")
    page.click(".modal button:has-text('Create')")
    page.wait_for_selector("text=Add to Pharmacy")
    gone: list[str] = []
    page.on(
        "response",
        lambda r: (
            gone.append(r.url)
            if r.status == 404 and "/api/planner/lists/" in r.url
            else None
        ),
    )
    page.click("#main button:has-text('Remove this list')")
    page.click(".modal button:has-text('Delete')")
    page.wait_for_selector("text=Add to Groceries")
    go(page, env, "planner?tab=notes")
    page.wait_for_timeout(6000)
    # At most one request can race the removal; the old loop asked every 2.5 seconds for ever.
    assert len(gone) <= 1, gone
    # 403: the page asked before a person was picked (expected); 404s are counted above.
    assert [
        p
        for p in real_problems(problems)
        if "status of 404" not in p and "status of 403" not in p
    ] == []
