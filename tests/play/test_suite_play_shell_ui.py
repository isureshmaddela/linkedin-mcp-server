"""Play in the Aira frame, in a real browser against the local server.

"+ New" offers Play's main creates and each opens its form; the search box across the top lists games, quizzes,
the watch list and live games as you type, opens the one chosen, and Enter lists every match; the left menu shows
Play's tabs as folders and a folder opens its tab (the folder you are on goes back to its start); /play opens the
app; a phone in dark mode has no sideways scroll. It fails on any script error. Screenshots go to
``PLAY_SHELL_SHOTS`` (default: none) for a human to look at.
"""

# ruff: noqa: F811
from __future__ import annotations

import json
import os
import re
from pathlib import Path

import pytest

patchright = pytest.importorskip("patchright.sync_api")

import auto_apply_assistant as local  # noqa: E402
from aira.suite import kit, public  # noqa: E402
from test_careers import env, stub  # noqa: E402, F401
from test_leads_ui import browser, go, signed_in_page, until  # noqa: E402, F401

SHOTS = (
    Path(os.environ["PLAY_SHELL_SHOTS"]) if os.environ.get("PLAY_SHELL_SHOTS") else None
)
NEW_ITEMS = [
    "Write a quiz",
    "Host live trivia",
    "Run a paid quiz night",
    "Add to the watch list",
]
FOLDERS = [
    "Games",
    "Daily challenge",
    "Quizzes",
    "Live trivia",
    "Scores",
    "Watch and read",
]
QUESTIONS = [
    {"text": f"Question {n}?", "choices": ["Yes", "No"], "answer": 0} for n in range(3)
]


@pytest.fixture(autouse=True)
def local_index():
    kit.forget_schema()
    public.set_index(local._public_link_index)


def shot(page, name: str) -> None:
    if SHOTS is None:
        return
    SHOTS.mkdir(parents=True, exist_ok=True)
    page.screenshot(path=str(SHOTS / f"{name}.png"))


def post(context, env, path: str, body: dict) -> dict:
    reply = context.request.post(
        env.base + path,
        data=json.dumps(body),
        headers={
            "Content-Type": "application/json",
            "X-Requested-With": "aira",
            "Origin": env.base,
        },
    )
    assert reply.ok, reply.text()
    return reply.json()


def seed(context, env) -> dict:
    quiz = post(
        context,
        env,
        "/api/play/quizzes",
        {"title": "Sunday family quiz", "about": "Birthdays", "questions": QUESTIONS},
    )["quiz"]
    item = post(
        context,
        env,
        "/api/play/watch",
        {"kind": "film", "title": "Sunday in the Park", "creator": "Ana Ruiz"},
    )["item"]
    post(context, env, "/api/play/watch", {"kind": "book", "title": "Paddington"})
    room = post(
        context, env, "/api/play/rooms", {"title": "Sunday trivia", "seconds": 30}
    )["room"]
    return {"quiz": quiz, "item": item, "room": room}


def open_play(page, env, hash_: str = "play") -> None:
    go(page, env, hash_)
    page.wait_for_selector("#main .page-head h1")
    page.wait_for_timeout(300)


def new_menu(page) -> list[str]:
    page.click(".shell-new")
    page.wait_for_selector(".shell-new-menu")
    return [
        text.strip()
        for text in page.locator(".shell-new-menu [role='menuitem']").all_inner_texts()
    ]


def choose_new(page, label: str) -> None:
    new_menu(page)
    page.locator(".shell-new-menu [role='menuitem']", has_text=label).click()


def close_modal(page) -> None:
    page.keyboard.press("Escape")
    page.wait_for_selector(".modal-backdrop", state="detached")


def suggestions(page) -> list[str]:
    return page.locator("#appSearchList [role='option']").all_inner_texts()


def suggest(page, text: str, wanted: str) -> None:
    page.fill("#appSearchInput", text)
    until(
        page,
        lambda: any(wanted in option for option in suggestions(page)),
        what=f"the suggestion {wanted}",
    )


def folders(page) -> list[str]:
    page.wait_for_selector(".shell-subnav[data-section='play'] a.shell-sublink")
    return [
        text.strip()
        for text in page.locator(
            ".shell-subnav[data-section='play'] a.shell-sublink"
        ).all_inner_texts()
    ]


def test_new_offers_the_play_creates_and_each_opens_its_form(browser, env):
    context, page, problems = signed_in_page(browser, env, width=1440, height=900)
    seed(context, env)
    open_play(page, env)
    assert new_menu(page) == NEW_ITEMS
    assert page.locator(".shell-new-menu [role='menuitem'] svg").count() == len(
        NEW_ITEMS
    )
    shot(page, "01-new-menu")
    page.keyboard.press("Escape")
    page.wait_for_selector(".shell-new-menu", state="detached")

    # Write a quiz: Quizzes opens on the editor; the address forgets ?new= so a reload shows the quizzes.
    choose_new(page, "Write a quiz")
    page.wait_for_selector("#main .suite-h2:has-text('Write a quiz')")
    assert re.search(r"#/play\?tab=quizzes$", page.url), page.url
    assert page.locator("#main h1").inner_text() == "Quizzes"
    shot(page, "02-new-quiz")
    page.reload()
    page.wait_for_load_state("networkidle")
    page.wait_for_selector(".play-quiz:has-text('Sunday family quiz')")
    assert page.locator("#main .suite-h2:has-text('Write a quiz')").count() == 0

    choose_new(page, "Host live trivia")
    page.wait_for_selector(".modal h2:has-text('Host live trivia')")
    assert re.search(r"#/play\?tab=live$", page.url), page.url
    close_modal(page)

    # A paid quiz night is the same form, named for it, with the entry fee to fill in first.
    choose_new(page, "Run a paid quiz night")
    page.wait_for_selector(".modal h2:has-text('Run a paid quiz night')")
    until(
        page,
        lambda: page.evaluate(
            "() => !!document.activeElement && document.activeElement.name === 'entry_cents'"
        ),
        what="the entry fee to take focus",
    )
    shot(page, "03-new-night")
    close_modal(page)

    choose_new(page, "Add to the watch list")
    page.wait_for_selector(".modal h2:has-text('Add to the list')")
    assert re.search(r"#/play\?tab=watch$", page.url), page.url
    page.fill(".modal input[name=title]", "Bluey")
    page.click(".modal button:has-text('Add')")
    page.wait_for_selector("article.play-watch:has-text('Bluey')")
    assert not problems, problems
    context.close()


def test_the_top_search_finds_play_records_and_opens_them(browser, env):
    context, page, problems = signed_in_page(browser, env, width=1440, height=900)
    seeded = seed(context, env)
    open_play(page, env)
    search = page.locator("#appSearchInput")
    assert (
        search.get_attribute("placeholder") == "Search games, quizzes, films and books"
    )
    suggest(page, "sunday", "Sunday trivia")
    found = " | ".join(suggestions(page))
    assert "Search Play for “sunday”" in found
    assert "Sunday family quiz" in found and "3 questions" in found, found
    assert "Sunday in the Park" in found and "Ana Ruiz" in found, found
    assert f"code {seeded['room']['code']}" in found, found
    assert "Paddington" not in found
    shot(page, "04-suggestions")

    # A watch-list entry: Watch and read opens with it outlined and focused.
    page.locator(
        "#appSearchList [role='option']", has_text="Sunday in the Park"
    ).click()
    page.wait_for_url(re.compile(r"#/play\?tab=watch$"))
    page.wait_for_selector(
        "article.play-watch.play-found:has-text('Sunday in the Park')"
    )
    assert (
        page.evaluate(
            "() => document.activeElement && document.activeElement.dataset.playItem"
        )
        == seeded["item"]["id"]
    )

    # A quiz opens ready to play; a game opens on its levels; a live game opens its room.
    suggest(page, "sunday fam", "Sunday family quiz")
    page.locator(
        "#appSearchList [role='option']", has_text="Sunday family quiz"
    ).click()
    page.wait_for_url(re.compile(r"#/play\?tab=quizzes$"))
    page.wait_for_selector("#main .suite-h2:has-text('Sunday family quiz')")
    assert page.locator("#main button:has-text('Start')").count() == 1

    suggest(page, "memory", "Memory match")
    page.locator("#appSearchList [role='option']", has_text="Memory match").click()
    page.wait_for_url(re.compile(r"#/play\?tab=games$"))
    page.wait_for_selector("#main .play-field:has-text('Level')")
    assert "Memory match" in page.locator("#main .suite-h2").first.inner_text()

    suggest(page, seeded["room"]["code"].lower(), "Sunday trivia")
    page.locator("#appSearchList [role='option']", has_text="Sunday trivia").click()
    page.wait_for_url(re.compile(rf"#/play\?tab=live&room={seeded['room']['id']}$"))
    page.wait_for_selector("#main .play-head:has-text('Sunday trivia')")

    # Enter lists every match, by kind; a result opens its record.
    search.fill("sunday")
    search.press("Enter")
    page.wait_for_url(re.compile(r"#/play\?tab=search&q=sunday$"))
    page.wait_for_selector("#main .play-results")
    assert page.locator("#main h1").inner_text() == "Results for “sunday”"
    headings = page.locator("#main .suite-h2").all_inner_texts()
    assert headings == ["Quizzes (1)", "Watch and read (1)", "Live games (1)"], headings
    assert page.locator("#main [role='tab'][aria-selected='true']").count() == 0
    shot(page, "05-results")
    page.locator("#main .play-result", has_text="Sunday in the Park").click()
    page.wait_for_selector(
        "article.play-watch.play-found:has-text('Sunday in the Park')"
    )

    # Nothing found: the page says so and offers the main creates.
    search.fill("zzzz nothing")
    search.press("Enter")
    page.wait_for_selector("#main .empty:has-text('Nothing matches')")
    assert page.locator("#main .empty a", has_text="Write a quiz").count() == 1
    assert not problems, problems
    context.close()


def test_the_left_menu_shows_the_play_folders_and_each_opens_its_tab(browser, env):
    context, page, problems = signed_in_page(browser, env, width=1440, height=900)
    seed(context, env)
    # The app has its own address: /play opens it on Games.
    page.goto(f"{env.base}/play")
    page.wait_for_load_state("networkidle")
    page.wait_for_selector(".play-tile:has-text('Memory match')")
    assert page.locator("#main h1").inner_text() == "Games"
    assert re.search(r"/play#/play", page.url), page.url
    assert folders(page) == FOLDERS
    assert page.locator(
        ".shell-subnav[data-section='play'] a.shell-sublink svg"
    ).count() == len(FOLDERS)
    shot(page, "06-folders-games")
    for label, tab, marker in [
        ("Scores", "scores", "My best scores"),
        ("Watch and read", "watch", "Sunday in the Park"),
        ("Live trivia", "live", "Games happening now"),
        ("Games", "games", "Memory match"),
    ]:
        page.locator(".shell-subnav[data-section='play'] a", has_text=label).click()
        page.wait_for_url(re.compile(rf"#/play\?tab={tab}$"))
        page.wait_for_selector(f"#main :text('{marker}')")
        assert page.locator("#main h1").inner_text() == label
        assert (
            page.locator("#main [role='tab'][aria-selected='true']").inner_text()
            == label
        )
        assert (
            page.locator(".shell-subnav[data-section='play'] a[aria-current='page']")
            .inner_text()
            .strip()
            == label
        )
    assert page.title() == "Games · Play · Monosage"

    # The folder you are on, in the middle of a game, goes back to the games.
    page.click(".play-tile:has-text('Quick maths')")
    page.click("#main button:has-text('Start')")
    page.wait_for_selector("text=Check my answers")
    page.locator(".shell-subnav[data-section='play'] a", has_text="Games").click()
    page.wait_for_selector(".play-tile:has-text('Memory match')")
    assert not problems, problems
    context.close()


def test_a_phone_in_dark_mode_has_no_sideways_scroll(browser, env):
    context, page, problems = signed_in_page(
        browser, env, width=390, height=844, dark=True
    )
    seed(context, env)
    for tab in [
        "games",
        "daily",
        "quizzes",
        "live",
        "scores",
        "watch",
        "search&q=sunday",
    ]:
        open_play(page, env, f"play?tab={tab}")
        page.wait_for_selector("#main .suite-body > *:not(.skeleton)")
        page.wait_for_timeout(300)
        assert page.evaluate(
            "() => document.documentElement.scrollWidth <= window.innerWidth"
        ), tab
        shot(page, f"07-phone-{tab.split('&')[0]}")
    assert not problems, problems
    context.close()
