"""Play in a real browser against the local server: the games are playable with clicks and typing, the tabs render,
live trivia runs between the host's page and a relative who joins from the public link, and everything fits a phone
in easy mode with extra-large text."""

# ruff: noqa: F811
from __future__ import annotations

import os
import sqlite3

import pytest

patchright = pytest.importorskip("patchright.sync_api")

import auto_apply_assistant as local  # noqa: E402
from aira.suite import kit, public  # noqa: E402
from test_careers import env, stub  # noqa: E402, F401
from test_leads_ui import browser, go, signed_in_page, until  # noqa: E402, F401


@pytest.fixture(autouse=True)
def local_index():
    kit.forget_schema()
    public.set_index(local._public_link_index)


def _secret(env, session_id=None):
    with sqlite3.connect(env.user()["data_path"]) as connection:
        if session_id is None:
            row = connection.execute(
                "SELECT id, secret FROM pl_sessions ORDER BY started_at DESC, rowid DESC LIMIT 1"
            ).fetchone()
        else:
            row = connection.execute(
                "SELECT id, secret FROM pl_sessions WHERE id = ?", (session_id,)
            ).fetchone()
    return row[0], kit.loads(row[1])


SHOTS = os.environ.get(
    "PLAY_SHOTS", ""
)  # a folder: save screenshots of the main screens there


def _shot(page, name):
    if SHOTS:
        page.screenshot(path=os.path.join(SHOTS, f"{name}.png"), full_page=True)


def _start(page, game_name, level, mode=None):
    page.click(f".play-tile:has-text('{game_name}')")
    page.click(f"button[role=radio]:has-text('{level}')")
    if mode:
        page.click(f"button[role=radio]:has-text('{mode}')")
    page.click("button:has-text('Start')")


def test_the_games_are_playable_and_live_trivia_reaches_a_relative(browser, env):
    context, page, problems = signed_in_page(browser, env)
    go(page, env, "play")
    page.wait_for_selector(".play-tile:has-text('Memory match')")
    _shot(page, "games")

    # Quick maths: type the answers, the server checks them.
    _start(page, "Quick maths", "Adding and taking away to 10", "Relaxed")
    page.wait_for_selector("text=Check my answers")
    _, secret = _secret(env)
    for index, value in enumerate(secret["answers"]):
        page.fill(f"input[aria-label='Answer {index + 1}']", str(value))
    page.click("text=Check my answers")
    page.wait_for_selector("text=1000 points")

    # Memory: turn the cards over pair by pair.
    page.click("text=Back to games")
    _start(page, "Memory match", "6 pairs")
    page.wait_for_selector("button[aria-label='Card 1, face down']")
    _shot(page, "memory")
    _, secret = _secret(env)
    pairs: dict[str, list[int]] = {}
    for index, card in enumerate(secret["layout"]):
        pairs.setdefault(card, []).append(index)
    for a, b in pairs.values():
        page.click(f"button[aria-label^='Card {a + 1}']")
        page.wait_for_selector(
            f"button[aria-label='Card {a + 1}: {secret['layout'][a]}']"
        )
        page.click(f"button[aria-label^='Card {b + 1}']")
        page.wait_for_timeout(150)
    page.wait_for_selector("text=points")
    assert "perfect" in page.locator("#main").inner_text()

    # Sudoku for children: a wrong check keeps the game going, the right grid finishes it.
    page.click("text=Back to games")
    _start(page, "Sudoku", "4 by 4")
    page.wait_for_selector("text=Hint")
    session_id, secret = _secret(env)
    page.click("button:has-text('Check')")
    page.wait_for_selector(".form-error:not([hidden])")
    for index, value in enumerate(secret["solution"]):
        cell = page.locator(
            f"input[aria-label^='Row {index // 4 + 1}, column {index % 4 + 1}']"
        )
        if cell.is_editable():
            cell.fill(str(value))
    page.click("button:has-text('Check')")
    page.wait_for_selector("text=points")

    # Word of the day: a guess, then the word.
    page.click("text=Back to games")
    _start(page, "Word of the day", "A practice word")
    page.wait_for_selector("input[aria-label='Your five-letter guess']")
    _, secret = _secret(env)
    page.fill("input[aria-label='Your five-letter guess']", secret["answer"])
    page.click("button:has-text('Guess')")
    page.wait_for_selector("text=600 points")
    _shot(page, "word-result")

    # The other tabs render.
    for tab, marker in (
        ("daily", "Today's challenge"),
        ("scores", "My best scores"),
        ("watch", "Add something"),
    ):
        go(page, env, f"play?tab={tab}")
        page.wait_for_selector(f"text={marker}")
    page.click("text=Add something")
    page.fill(".modal input[name=title]", "Paddington")
    page.click(".modal button:has-text('Add')")
    page.wait_for_selector("article:has-text('Paddington')")
    page.click("article:has-text('Paddington') button:has-text('Finished')")
    page.wait_for_selector(
        "article:has-text('Paddington') button[aria-pressed=true]:has-text('Finished')"
    )

    # Live trivia: host from the page, invite a relative with the public link.
    go(page, env, "play?tab=live")
    page.click("button:has-text('Host live trivia')")
    page.fill(".modal input[name=title]", "Sunday quiz")
    page.fill(".modal input[name=seconds]", "60")
    page.click(".modal button:has-text('Open the room')")
    page.wait_for_selector("text=Waiting for players")
    page.click("button:has-text('Create link')")
    page.wait_for_selector(".suite-share-url")
    url = page.locator(".suite-share-url").inner_text()
    visitor_context = browser.new_context(viewport={"width": 390, "height": 844})
    visitor = visitor_context.new_page()
    visitor_errors: list[str] = []
    visitor.on("pageerror", lambda e: visitor_errors.append(str(e)))
    visitor.goto(url)
    visitor.wait_for_selector("text=Join the family quiz")
    visitor.fill("input[placeholder='Your name']", "Auntie Jo")
    visitor.click("button:has-text('Join the game')")
    visitor.wait_for_selector("text=You're in!")
    page.wait_for_selector("text=Auntie Jo", timeout=15000)
    page.click("button:has-text('Start the game')")
    page.wait_for_selector("text=Question 1 of 8")
    visitor.wait_for_selector("text=Question 1 of 8", timeout=15000)
    _shot(page, "live-host")
    _shot(visitor, "live-visitor")
    visitor.click(".pub-card button.pub-btn >> nth=0")
    visitor.wait_for_selector("text=Answer locked in.")
    page.click("#main button:has-text('A. ')")
    page.wait_for_selector("text=Answer locked in. Waiting for the others")
    page.click("button:has-text('Next question')")
    page.wait_for_selector("text=Question 2 of 8")
    page.click("button:has-text('End the game')")
    # A winner is named only when someone scored (a game where everyone missed is just over).
    page.wait_for_selector("text=/wins with|The game is over/")
    visitor.wait_for_selector("text=/wins!|The game is over/", timeout=15000)
    assert not visitor_errors, visitor_errors
    visitor_context.close()

    # A phone in easy mode with extra-large text: nothing spills sideways.
    page.set_viewport_size({"width": 390, "height": 844})
    page.evaluate("document.body.classList.add('easy', 'text-xlarge')")
    for hash_ in ("play", "play?tab=live", "play?tab=watch", "play?tab=scores"):
        go(page, env, hash_)
        page.wait_for_timeout(600)
        assert not page.evaluate(
            "document.documentElement.scrollWidth > window.innerWidth + 2"
        ), hash_
    go(page, env, "play")
    _start(page, "Memory match", "12 pairs")
    page.wait_for_selector("button[aria-label='Card 24, face down']")
    _shot(page, "memory-phone-easy")
    assert not page.evaluate(
        "document.documentElement.scrollWidth > window.innerWidth + 2"
    )
    errors = [p for p in problems if p.startswith("pageerror")]
    assert not errors, errors
    context.close()
