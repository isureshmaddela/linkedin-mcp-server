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
    # The page is named after the open tab (the app's own name is in the bar above).
    page.wait_for_selector("#main .page-head h1:has-text('Today')")
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
    page.wait_for_selector(".ln-books >> text=Matilda")
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


def test_learn_progress_import_and_selling_in_the_browser(browser, env):
    """Add cards from a pasted list, sell the deck, a buyer asks on the public page, the seller confirms the
    payment in Earn, the buyer opens every card with the code; then Progress, the hardest cards and books."""
    kit.forget_schema()
    public.set_index(local._public_link_index)
    context, page, problems = signed_in_page(browser, env)
    post(
        context, env, "/api/suite/business", {"payment_link": "https://pay.example/me"}
    )
    deck = post(context, env, "/api/learn/decks", {"title": "Kitchen Spanish"})["deck"]

    go(page, env, "learn?tab=decks")
    page.click(".life-card:has-text('Kitchen Spanish') button:has-text('Open')")
    page.click(".drawer button:has-text('Add many cards')")
    page.fill(
        ".modal textarea[name=text]",
        "\n".join(f"Palabra {i} = Word {i}" for i in range(10)),
    )
    page.click(".modal button:has-text('Add the cards')")
    page.wait_for_selector(".drawer >> text=10 cards")
    page.fill(".drawer input[type=search]", "palabra 7")
    until(
        page,
        lambda: page.locator(".drawer .life-lines li:visible").count() == 1,
        what="the card search",
    )
    # A price that cannot be read is refused in the form, not taken as free.
    page.click(".drawer button:has-text('Sell this deck')")
    page.fill(".modal input[name=price_cents]", "abc")
    page.click(".modal button:has-text('Save')")
    page.wait_for_selector(".modal .form-error:has-text('like 4.99')")
    page.fill(".modal input[name=price_cents]", "4.50")
    page.fill(".modal input[name=preview_count]", "2")
    page.click(".modal button:has-text('Save')")
    page.wait_for_selector(".drawer >> text=On sale for")
    link = post(
        context,
        env,
        f"/api/learn/decks/{deck['id']}/price",
        {"price_cents": 450, "preview_count": 2},
    )["deck"]["link"]
    page.keyboard.press("Escape")
    # Search and filters on the deck list.
    go(page, env, "learn?tab=decks")
    page.select_option(".ln-filters select", "sale")
    page.wait_for_selector(".life-card:has-text('Kitchen Spanish')")
    page.fill(".ln-filters input[type=search]", "nothing like this")
    page.wait_for_selector("text=No decks match")

    # The buyer, on a phone, signed out.
    visitor = browser.new_context(viewport={"width": 390, "height": 844})
    buyer = visitor.new_page()
    buyer_problems = []
    buyer.on("pageerror", lambda e: buyer_problems.append(str(e)))
    buyer.goto(env.base + link["path"])
    buyer.wait_for_selector("text=Card 1 of 2 (free preview)")
    buyer.fill("input[autocomplete=name]", "Sam")
    buyer.fill("input[type=email]", "sam@example.com")
    buyer.click("button:has-text('Buy this deck')")
    buyer.wait_for_selector("text=How to pay")
    assert not buyer.evaluate(
        "() => document.documentElement.scrollWidth > window.innerWidth + 2"
    )

    # The seller confirms the payment and reads the code.
    go(page, env, "learn?tab=earn")
    page.wait_for_selector("text=Sam · Kitchen Spanish")
    page.click("button:has-text('Payment received')")
    page.wait_for_selector(".modal:has-text('Payment confirmed')")
    code = page.locator(".modal .ln-code").inner_text()
    page.click(".modal button:has-text('Done')")
    page.wait_for_selector(".suite-kpis:has-text('$4.50')")

    buyer.fill("input[placeholder='ABCDE-FGHJK']", code)
    buyer.click("button:has-text('Open every card')")
    buyer.wait_for_selector("text=Every card is open: 10 cards.")
    buyer.wait_for_selector("text=/Card 1 of 10$/")
    visitor.close()
    assert not buyer_problems

    # Study (missing a card), then Progress and the hardest cards.
    go(page, env, "learn?tab=study")
    page.wait_for_selector("button:has-text('Show answer')")
    page.keyboard.press("Space")
    page.keyboard.press("1")
    page.wait_for_timeout(500)
    if page.locator(".modal button:has-text('Hooray!')").count():
        page.click(".modal button:has-text('Hooray!')")
    go(page, env, "learn?tab=progress")
    page.wait_for_selector("text=The last 12 weeks")
    assert page.locator(".ln-cal-day[role=img]").count() == 84
    page.click("a:has-text('Practise these now')")
    page.wait_for_selector("text=Your hardest cards: the ones you missed before.")
    page.wait_for_selector("button:has-text('Show answer')")

    # Books: edit, finish, read again, delete.
    post(context, env, "/api/learn/books", {"title": "Matlida"})
    go(page, env, "learn?tab=reading")
    page.click(".ln-books button:has-text('Edit')")
    page.fill(".modal input[name=title]", "Matilda")
    page.click(".modal button:has-text('Save')")
    page.wait_for_selector(".ln-books >> text=Matilda")
    page.click("button:has-text('10 minutes')")
    page.wait_for_selector(".ln-books >> text=10 minutes read")
    page.click(".ln-books button:has-text('Finished it!')")
    page.click(".ln-books button:has-text('Reading it again')")
    page.wait_for_selector(".ln-books button:has-text('Finished it!')")
    page.click(".ln-books button:has-text('Delete')")
    page.click(".modal button:has-text('Delete')")
    page.wait_for_selector("text=No books yet")

    # Every tab at phone width: nothing spills sideways.
    page.set_viewport_size({"width": 390, "height": 844})
    for tab in ("today", "decks", "progress", "earn", "reading"):
        go(page, env, f"learn?tab={tab}")
        page.wait_for_selector("#main .page-head h1")
        page.wait_for_selector("#main .ln-page")
        page.wait_for_timeout(300)
        assert not page.evaluate(
            "() => document.documentElement.scrollWidth > window.innerWidth + 2"
        ), tab
    assert not problems, problems


def test_learn_fixes_in_the_browser(browser, env):
    """No stray "null" under the Study picker; a deck is not sold until the seller says how buyers pay (asked in the
    Sell dialog itself); the buyer sees the seller's own link, and buy-form errors show next to the form and clear
    once the request goes through; a deck someone sells has no copy button."""
    kit.forget_schema()
    public.set_index(local._public_link_index)
    context, page, problems = signed_in_page(browser, env)
    deck = post(
        context,
        env,
        "/api/learn/decks",
        {
            "title": "Capitals",
            "cards": [
                {"front": "France", "back": "Paris"},
                {"front": "Italy", "back": "Rome"},
                {"front": "Spain", "back": "Madrid"},
            ],
        },
    )["deck"]

    for tab in ("study", "study&mode=weak"):
        go(page, env, f"learn?tab={tab}")
        page.wait_for_selector("#main .ln-study-head")
        page.wait_for_timeout(200)
        assert "null" not in page.locator("#main").inner_text(), tab

    # The Sell dialog asks how buyers pay, and refuses a price with no way to pay.
    go(page, env, "learn?tab=decks")
    page.click(".life-card:has-text('Capitals') button:has-text('Open')")
    page.click(".drawer button:has-text('Sell this deck')")
    page.wait_for_selector(".modal >> text=How buyers pay you")
    page.fill(".modal input[name=price_cents]", "4.99")
    page.click(".modal button:has-text('Save')")
    page.wait_for_selector(".modal .form-error:has-text('payment link')")
    page.fill(".modal input[name=payment_link]", "https://pay.example/pat")
    page.click(".modal button:has-text('Save')")
    page.wait_for_selector(".drawer >> text=On sale for")
    # A deck on sale keeps its page: no "Turn off" while it sells.
    assert not page.locator(".drawer button:has-text('Turn off')").count()
    page.keyboard.press("Escape")
    go(page, env, "learn?tab=earn")
    page.wait_for_selector("text=How buyers pay you")
    assert "https://pay.example/pat" in page.locator("#main").inner_text()
    link = context.request.get(env.base + f"/api/learn/decks/{deck['id']}").json()[
        "deck"
    ]["link"]

    visitor = browser.new_context(viewport={"width": 390, "height": 844})
    buyer = visitor.new_page()
    buyer_problems = []
    buyer.on("pageerror", lambda e: buyer_problems.append(str(e)))
    buyer.goto(env.base + link["path"])
    buyer.wait_for_selector("text=Buy this deck")
    buyer.click("button:has-text('Buy this deck')")
    buyer.wait_for_selector(".pub-form .pub-notice.bad:has-text('Enter your name')")
    buyer.fill("input[autocomplete=name]", "Sam")
    buyer.fill("input[type=email]", "not-an-email")
    buyer.click("button:has-text('Buy this deck')")
    buyer.wait_for_selector(".pub-form .pub-notice.bad:has-text('email')")
    assert buyer.locator(".pub-notice.bad").count() == 1  # the old error was replaced
    # The error is next to the button, in view on a phone.
    assert buyer.evaluate(
        """() => { const r = document.querySelector('.pub-form .pub-notice.bad').getBoundingClientRect();
        return r.top >= 0 && r.bottom <= window.innerHeight; }"""
    )
    buyer.fill("input[type=email]", "sam@example.com")
    buyer.click("button:has-text('Buy this deck')")
    buyer.wait_for_selector("text=How to pay")
    assert buyer.locator(".pub-notice.bad").count() == 0
    pay = buyer.locator(".pub-pay a.pub-btn")
    assert pay.get_attribute("href") == "https://pay.example/pat"
    assert "Pay $4.99" in pay.inner_text()
    buyer.click("button:has-text('Open every card')")
    buyer.wait_for_selector(".pub-notice.bad:has-text('Enter your access code')")
    assert not buyer.evaluate(
        "() => document.documentElement.scrollWidth > window.innerWidth + 2"
    )
    visitor.close()
    assert not buyer_problems, buyer_problems

    # Earn: the payment confirmed shows the code and an email ready to send.
    page.reload()  # already on Earn: show the new request
    page.click("button:has-text('Payment received')")
    page.wait_for_selector(".modal:has-text('Payment confirmed')")
    mail = page.locator(".modal a:has-text('Email the buyer')").get_attribute("href")
    assert mail.startswith("mailto:sam%40example.com?subject=")
    page.click(".modal button:has-text('Done')")
    assert not problems, problems
