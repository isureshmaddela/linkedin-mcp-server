"""Learn in the Aira frame, in a real browser: "+ New" offers a deck, a deck on any topic, cards, a book and reading
time and opens their forms; the search across the top suggests decks, cards and books and opens the one chosen;
Enter shows every match on Decks; the tabs are folders in the left menu and the page is named after the open one.
No script errors along the way."""

# ruff: noqa: F811
from __future__ import annotations

import re

import pytest

pytest.importorskip("patchright.sync_api")

from aira.suite import kit, public  # noqa: E402
from test_careers import env, stub  # noqa: E402, F401
from test_leads_ui import browser, signed_in_page, until  # noqa: E402, F401
from test_suite_learn_ui import no_model, post  # noqa: E402, F401

import auto_apply_assistant as local  # noqa: E402

NEW_ITEMS = [
    "New deck",
    "Make a deck on any topic",
    "Add cards to a deck",
    "Add a book",
    "Log reading time",
]
FOLDERS = [
    "Today",
    "Study",
    "Decks",
    "Progress",
    "Quizzes",
    "Maths",
    "Reading",
    "Family",
    "Earn",
]


def open_learn(page, env, hash_="learn"):
    page.goto(f"{env.base}/learn#/{hash_}")
    page.wait_for_load_state("networkidle")
    page.wait_for_selector("#main .ln-page")


def search(page, text):
    page.fill("#appSearchInput", "")
    page.click("#appSearchInput")
    page.keyboard.type(text)
    page.wait_for_selector("#appSearchList:not([hidden]) .shell-search-option")


def choose_new(page, label):
    page.click(".shell-new")
    page.wait_for_selector(".shell-new-menu")
    page.click(f".shell-new-menu [role='menuitem']:has-text('{label}')")


def close_modal(page):
    page.keyboard.press("Escape")
    page.wait_for_selector(".modal", state="detached")


def seed(context, env):
    post(
        context,
        env,
        "/api/learn/decks",
        {
            "title": "Spanish words",
            "emoji": "💬",
            "cards": [
                {"front": "Hola", "back": "Hello"},
                {"front": "Gracias", "back": "Thank you"},
                {"front": "Adiós", "back": "Goodbye"},
            ],
        },
    )
    post(context, env, "/api/learn/starters", {"key": "solar-system"})
    post(context, env, "/api/learn/books", {"title": "Matilda", "author": "Roald Dahl"})


def test_learn_new_search_and_folders_in_the_frame(browser, env):
    kit.forget_schema()
    public.set_index(local._public_link_index)
    context, page, problems = signed_in_page(browser, env)
    seed(context, env)

    # The address names the app and the page head names the open folder, not the app again.
    open_learn(page, env)
    assert re.search(r"/learn#/learn", page.url)
    assert page.locator("#main .page-head h1").inner_text() == "Today"

    # The left menu shows the tabs as folders with icons; a folder opens its tab, and the bar under the title (the
    # same list again) is folded away on a wide screen.
    folders = page.locator("#primaryNav .shell-sublink")
    assert [t.strip() for t in folders.all_inner_texts()] == FOLDERS
    assert page.locator("#primaryNav .shell-sublink .shell-subdot").count() == 0
    assert not page.locator("#main .ln-page > .tabs").is_visible()
    page.click("#primaryNav .shell-sublink[data-tab='reading']")
    until(page, lambda: "tab=reading" in page.url, what="the Reading folder")
    page.wait_for_selector("#main .page-head h1:has-text('Reading')")
    page.wait_for_selector(".ln-books >> text=Matilda")
    assert (
        page.locator("#primaryNav .shell-sublink[aria-current='page']")
        .inner_text()
        .strip()
        == "Reading"
    )

    # "+ New" lists the app's creates; each opens that form, and the address forgets ?new= at once.
    page.click(".shell-new")
    page.wait_for_selector(".shell-new-menu")
    assert [
        t.strip()
        for t in page.locator(".shell-new-menu [role='menuitem']").all_inner_texts()
    ] == NEW_ITEMS
    page.keyboard.press("Escape")

    choose_new(page, "New deck")
    page.wait_for_selector(".modal:has-text('New deck') [name=title]")
    assert "tab=decks" in page.url and "new=" not in page.url
    page.fill(".modal [name=title]", "Birds")
    page.locator(".modal .actions button").last.click()
    page.wait_for_selector(".drawer:has-text('Birds')")
    page.keyboard.press("Escape")

    choose_new(page, "Make a deck on any topic")
    page.wait_for_selector(".modal:has-text('Make a deck on any topic') [name=topic]")
    assert "tab=decks" in page.url and "new=" not in page.url
    close_modal(page)

    # Cards: pick one of your decks, then the card form; the deck opens at the new card.
    choose_new(page, "Add cards to a deck")
    page.wait_for_selector(".modal:has-text('Add cards to which deck?') select")
    page.select_option(".modal select", label="💬 Spanish words")
    page.locator(".modal .actions button").last.click()
    page.wait_for_selector(
        ".modal:has-text('Add a card to Spanish words') [name=front]"
    )
    page.fill(".modal [name=front]", "Buenos días")
    page.fill(".modal [name=back]", "Good morning")
    page.locator(".modal .actions button").last.click()
    page.wait_for_selector(".drawer .ln-picked:has-text('Buenos días')")
    page.keyboard.press("Escape")

    choose_new(page, "Add a book")
    page.wait_for_selector(".modal:has-text('Add a book') [name=title]")
    assert "tab=reading" in page.url and "new=" not in page.url
    close_modal(page)

    choose_new(page, "Log reading time")
    page.wait_for_selector(".modal:has-text('Minutes read') [name=minutes]")
    assert (
        page.locator(".modal select[name=book_id] option:has-text('Matilda')").count()
        == 1
    )
    page.fill(".modal [name=minutes]", "15")
    page.locator(".modal .actions button").last.click()
    page.wait_for_selector(".modal", state="detached")
    until(
        page,
        lambda: "15 min" in page.locator(".suite-kpis").inner_text(),
        what="the reading minutes",
    )
    # Back does not open a form again.
    page.go_back()
    page.wait_for_load_state("networkidle")
    page.wait_for_timeout(400)
    assert page.locator(".modal").count() == 0

    # The search across the top: decks, cards and books, each opening itself.
    open_learn(page, env, "learn?tab=progress")
    assert (
        page.get_attribute("#appSearchInput", "placeholder")
        == "Search decks, cards and books"
    )
    search(page, "gracias")
    texts = page.locator("#appSearchList .shell-search-option").all_inner_texts()
    assert any(
        "Gracias" in t and "Thank you" in t and "Spanish words" in t for t in texts
    ), texts
    page.locator("#appSearchList .shell-search-option:has-text('Thank you')").click()
    page.wait_for_selector(".drawer .ln-picked:has-text('Gracias')")
    assert (
        "tab=decks" in page.url and "open=" not in page.url and "card=" not in page.url
    )
    page.keyboard.press("Escape")

    search(page, "solar")
    page.locator("#appSearchList .shell-search-option:has-text('18 cards')").click()
    page.wait_for_selector(".drawer:has-text('The solar system') >> text=18 cards")
    page.keyboard.press("Escape")

    search(page, "dahl")
    page.locator("#appSearchList .shell-search-option:has-text('Matilda')").click()
    page.wait_for_selector(".ln-books .ln-picked:has-text('Matilda')")
    assert "tab=reading" in page.url and "book=" not in page.url

    # Enter shows every match on Decks, in the page's own search box.
    search(page, "hello")
    page.keyboard.press("Enter")
    until(page, lambda: "q=hello" in page.url, what="the search results")
    page.wait_for_selector("#main .page-head h1:has-text('Decks')")
    page.wait_for_selector(".ln-results-for:has-text('“hello”')")
    page.wait_for_selector(".ln-hits:has-text('Hola')")
    assert (
        page.locator("#main input[aria-label='Search decks']").input_value() == "hello"
    )
    page.locator(".ln-hits li:has-text('Hola') button:has-text('Open')").click()
    page.wait_for_selector(".drawer .ln-picked:has-text('Hola')")
    page.keyboard.press("Escape")

    assert not problems, problems


def test_learn_in_the_frame_on_a_phone_fits_and_keeps_its_creates(browser, env):
    kit.forget_schema()
    context, page, problems = signed_in_page(
        browser, env, width=390, height=844, dark=True
    )
    seed(context, env)
    # The menu (and its "+ New") is a sheet on a phone, so the tab bar stays and the page keeps its own create buttons.
    for hash_ in (
        "learn?tab=decks&q=hello",
        "learn?tab=reading",
        "learn?tab=leaderboard",
        "learn?tab=earn",
    ):
        open_learn(page, env, hash_)
        page.wait_for_timeout(300)
        assert page.locator("#main .ln-page > .tabs").is_visible(), hash_
        assert not page.evaluate(
            "() => document.documentElement.scrollWidth > window.innerWidth + 2"
        ), hash_
    open_learn(page, env, "learn?tab=decks")
    assert page.locator("#main button:has-text('New deck')").is_visible()
    # A starter deck already added opens your copy instead of adding it again.
    starter = page.locator(".life-tile:has-text('The solar system')")
    assert starter.locator("button:has-text('Open')").count() == 1
    assert starter.locator("button:has-text('Add')").count() == 0
    open_learn(page, env, "learn?tab=leaderboard")
    page.wait_for_selector(".ln-alone a:has-text('Add people')")
    assert not problems, problems
