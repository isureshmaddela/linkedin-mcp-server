"""Learn in a real browser: every tab, studying with the keyboard, a quiz, maths, reading, the family tab, a shared
deck studied on its public page, and easy mode on a phone."""

# ruff: noqa: F811
from __future__ import annotations

import json

import pytest

patchright = pytest.importorskip("patchright.sync_api")

from aira.suite import kit, public  # noqa: E402
from test_careers import env, stub  # noqa: E402, F401
from test_leads_ui import browser, go, signed_in_page, until  # noqa: E402, F401

import auto_apply_assistant as local  # noqa: E402


@pytest.fixture(autouse=True)
def no_model():
    """Never call the real writing model (a live Ollama may run on this machine)."""
    from aira import suite

    previous = suite.gateway()
    suite.set_gateway(suite._NoGateway())
    yield
    suite.set_gateway(previous)


def post(context, env, path, body, method="POST"):
    response = context.request.fetch(
        env.base + path,
        method=method,
        data=json.dumps(body),
        headers={
            "Content-Type": "application/json",
            "X-Requested-With": "aira",
            "Origin": env.base,
        },
    )
    assert response.status in (200, 201), response.text()
    return response.json()


def test_learn_in_the_browser(browser, env):
    kit.forget_schema()
    public.set_index(local._public_link_index)
    context, page, problems = signed_in_page(browser, env)
    deck = post(context, env, "/api/learn/starters", {"key": "solar-system"})["deck"]

    go(page, env, "learn")
    page.wait_for_selector("#main h1:has-text('Learn')")
    page.wait_for_selector("text=Today's goal")
    assert "Start practising" in page.locator("#main").inner_text()

    # Study with the keyboard: the answer and the grade buttons stay hidden until Space.
    go(page, env, "learn?tab=study")
    page.wait_for_selector("button:has-text('Show answer')")
    assert not page.locator("button:has-text('Easy')").is_visible()
    first = page.locator(".card div").first.inner_text()
    page.keyboard.press("Space")
    page.wait_for_selector("button:has-text('Easy')", state="visible")
    page.keyboard.press("3")
    # The first card studied earns a badge: a celebration to close.
    page.wait_for_selector(".modal:has-text('You earned a badge!')")
    page.keyboard.press("3")  # ignored while the dialog is open
    page.click(".modal button:has-text('Hooray!')")
    until(
        page,
        lambda: page.locator(".card div").first.inner_text() != first,
        what="the next card",
    )
    for _ in range(2):
        page.click("button:has-text('Show answer')")
        page.click("button:has-text('Good')")
        page.wait_for_timeout(300)
    status = context.request.get(env.base + "/api/learn/overview").json()
    assert status["goal"]["cards_today"] == 3 and status["level"]["xp"] > 0

    # Decks: the starter deck, starter list, and the drawer.
    go(page, env, "learn?tab=decks")
    page.wait_for_selector("text=Your decks")
    assert page.locator(".life-card:has-text('The solar system')").count() == 1
    page.click(".life-card:has-text('The solar system') button:has-text('Open')")
    page.wait_for_selector(".drawer >> text=18 cards")
    page.keyboard.press("Escape")

    # A quiz from the deck, answered by clicking.
    go(page, env, f"learn?tab=quizzes&deck={deck['id']}")
    page.wait_for_selector("button:has-text('Start the quiz')")
    page.select_option("select[aria-label='Questions']", "5")
    page.click("button:has-text('Start the quiz')")
    page.wait_for_selector("[role=radiogroup]")
    for group in page.locator("[role=radiogroup]").all():
        group.locator("button").first.click()
    page.click("button:has-text('Hand in')")
    page.wait_for_selector("text=/\\d of 5/")

    # Maths, relaxed: answer everything with 1 and see the results.
    go(page, env, "learn?tab=maths")
    page.wait_for_selector("text=Maths practice")
    page.click(".life-tile:has-text('Times tables')")
    page.wait_for_selector("button:has-text('Check my answers')")
    for box in page.locator("#main input[type=text]").all():
        box.fill("1")
    page.click("button:has-text('Check my answers')")
    page.wait_for_selector("text=/of 10/")

    # Reading: a book and ten minutes.
    post(context, env, "/api/learn/books", {"title": "Matilda", "author": "Roald Dahl"})
    go(page, env, "learn?tab=reading")
    page.wait_for_selector("td:has-text('Matilda')")
    page.click("button:has-text('10 minutes')")
    until(
        page,
        lambda: "10 min" in page.locator(".suite-kpis").inner_text(),
        what="the reading minutes",
    )

    go(page, env, "learn?tab=leaderboard")
    page.wait_for_selector("text=This week in the family")

    # A shared deck studied on its public page, signed out.
    link = post(context, env, f"/api/learn/decks/{deck['id']}/link", {})["link"]
    visitor = browser.new_context(viewport={"width": 390, "height": 844})
    public_page = visitor.new_page()
    public_problems = []
    public_page.on("pageerror", lambda e: public_problems.append(str(e)))
    public_page.goto(env.base + link["path"])
    public_page.wait_for_selector("text=Card 1 of 18")
    assert "null" not in public_page.locator("main").inner_text()
    assert not public_page.locator("button:has-text('I knew it')").is_visible()
    for _ in range(18):
        public_page.click("button:has-text('Show answer')")
        public_page.click("button:has-text('I knew it')")
    public_page.wait_for_selector("text=You knew 18 of 18.")
    visitor.close()
    assert not public_problems

    # Easy mode and extra large text on a phone: nothing spills sideways.
    post(context, env, "/api/life/prefs", {"easy_mode": True, "text_size": "xlarge"})
    page.set_viewport_size({"width": 390, "height": 844})
    page.reload()
    go(page, env, "learn?tab=study")
    page.wait_for_selector("button:has-text('Show answer')")
    assert page.evaluate("document.body.classList.contains('easy')")
    assert not page.evaluate(
        "document.documentElement.scrollWidth > window.innerWidth + 2"
    )
    assert not problems, problems
