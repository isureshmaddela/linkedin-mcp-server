"""Play's quizzes and quiz nights in a real browser: write a quiz in the editor (a mistake is explained), play it,
host it as a quiz night with an entry fee, a guest joins from the link and sees how to pay, the host marks them as
paid and the takings show; the watch list is edited and searched; everything fits a phone."""

# ruff: noqa: F811
from __future__ import annotations

import sqlite3

import pytest

patchright = pytest.importorskip("patchright.sync_api")

import auto_apply_assistant as local  # noqa: E402
from aira.suite import kit, public  # noqa: E402
from test_careers import env, stub  # noqa: E402, F401
from test_leads_ui import browser, go, signed_in_page  # noqa: E402, F401


@pytest.fixture(autouse=True)
def local_index():
    kit.forget_schema()
    public.set_index(local._public_link_index)


def _answers(env):
    with sqlite3.connect(env.user()["data_path"]) as connection:
        row = connection.execute(
            "SELECT secret FROM pl_sessions ORDER BY started_at DESC, rowid DESC LIMIT 1"
        ).fetchone()
    return kit.loads(row[0])["answers"]


def test_write_a_quiz_and_run_a_paid_quiz_night(browser, env):
    context, page, problems = signed_in_page(browser, env)
    go(page, env, "play?tab=quizzes")
    page.wait_for_selector("text=No quizzes yet")
    page.click("button:has-text('Write a quiz')")
    page.click("button:has-text('Save quiz')")
    page.wait_for_selector(".form-error:not([hidden])")
    page.fill("input[placeholder='Our family quiz']", "Club quiz")
    questions = [
        ("Capital of France?", ["Paris", "Lyon"]),
        ("Two plus two?", ["4", "5", "3"]),
        ("Colour of grass?", ["Green", "Blue"]),
    ]
    for i, (text, answers) in enumerate(questions):
        page.locator(".play-question-edit textarea").nth(i).fill(text)
        for c, answer in enumerate(answers):
            page.fill(
                f"input[aria-label='Answer {'ABCD'[c]} for question {i + 1}']", answer
            )
    page.click("button:has-text('Save quiz')")
    page.wait_for_selector("article:has-text('Club quiz')")

    # Play it: the result says what the right answers were.
    page.click("article:has-text('Club quiz') button:has-text('Play')")
    page.click("button:has-text('Start')")
    page.wait_for_selector("text=Question 1 of 3")
    right = _answers(env)
    for index in range(3):
        page.click(
            f"#main .play-option >> nth={(right[index] + 1) % (len(questions[index][1]))}"
        )
        page.wait_for_timeout(100)
    page.wait_for_selector("text=Good try!")
    assert "the answer is Paris" in page.locator("#main").inner_text()

    # Host it as a quiz night with an entry fee.
    page.click("text=Back to quizzes")
    page.click("article:has-text('Club quiz') button:has-text('Host it live')")
    page.fill(".modal input[name=entry_cents]", "4.50")
    page.fill(".modal input[name=prize]", "A cake")
    page.click(".modal button:has-text('Open the room')")
    page.wait_for_selector("text=Entry $4.50 for guests")
    page.click("button:has-text('Create link')")
    page.wait_for_selector(".suite-share-url")
    url = page.locator(".suite-share-url").inner_text()
    guest_context = browser.new_context(viewport={"width": 390, "height": 844})
    guest = guest_context.new_page()
    guest_errors: list[str] = []
    guest.on("pageerror", lambda e: guest_errors.append(str(e)))
    guest.goto(url)
    guest.wait_for_selector("text=Join the quiz night")
    guest.fill("input[placeholder='Your name']", "Sam")
    guest.click("button:has-text('Join the game')")
    guest.wait_for_selector("text=Please pay the $4.50 entry fee")
    page.click("button:has-text('Mark $4.50 paid')", timeout=15000)
    page.wait_for_selector("text=Taken so far: $4.50 from 1 guest")
    guest.wait_for_selector("text=Waiting for the host to start", timeout=15000)
    until_gone = 0
    while guest.locator("text=Please pay the").count() and until_gone < 60:
        guest.wait_for_timeout(250)
        until_gone += 1
    assert not guest.locator("text=Please pay the").count()
    assert not guest_errors, guest_errors
    guest_context.close()
    go(page, env, "play?tab=live")
    page.wait_for_selector("text=Taken from quiz nights")

    # The watch list: edit a typo, then search.
    go(page, env, "play?tab=watch")
    page.click("button:has-text('Add something')")
    page.fill(".modal input[name=title]", "Paddingtn")
    page.click(".modal button:has-text('Add')")
    page.click("article:has-text('Paddingtn') button:has-text('Edit')")
    page.fill(".modal input[name=title]", "Paddington")
    page.click(".modal button:has-text('Save')")
    page.wait_for_selector("article:has-text('Paddington')")
    page.fill("input[aria-label='Search the list']", "zzz")
    page.keyboard.press("Enter")
    page.wait_for_selector("text=Nothing matches")

    # Pressing the tab you are on in the middle of a game goes back to the list of games.
    go(page, env, "play")
    page.click(".play-tile:has-text('Quick maths')")
    page.click("button:has-text('Start')")
    page.wait_for_selector("text=Check my answers")
    page.click("button[role=tab]:has-text('Games')")
    page.wait_for_selector(".play-tile:has-text('Memory match')")

    # A phone: nothing spills sideways.
    page.set_viewport_size({"width": 390, "height": 844})
    for hash_ in ("play?tab=quizzes", "play?tab=live", "play?tab=watch"):
        go(page, env, hash_)
        page.wait_for_timeout(500)
        assert not page.evaluate(
            "document.documentElement.scrollWidth > window.innerWidth + 2"
        ), hash_
    errors = [p for p in problems if p.startswith("pageerror")]
    assert not errors, errors
    context.close()
