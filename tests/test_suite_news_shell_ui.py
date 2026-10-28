"""News in the Aira frame, in a real browser against the local server.

"+ New" offers News's main creates (follow a word, add a source, the next newsletter issue) and each opens its form;
the search box across the top suggests the headlines already loaded, the words followed and the topics as you type,
opens the one chosen (a headline opens the story in a new tab), and Enter searches all the news; the left menu
shows News's tabs as folders and a folder opens its tab; a phone in dark mode has no sideways scroll. It fails on
any script error. Feeds come from tests/news_stub.py (no network). Screenshots go to ``NEWS_SHELL_SHOTS``
(default: none) for a human to look at.
"""

# ruff: noqa: F811
from __future__ import annotations

import json
import os
import re
from pathlib import Path

import pytest

patchright = pytest.importorskip("patchright.sync_api")

from news_stub import NewsStub  # noqa: E402
from test_careers import env, stub  # noqa: E402, F401
from test_leads_ui import browser, go, signed_in_page, until  # noqa: E402, F401

from aira import suite  # noqa: E402
from aira.suite import kit  # noqa: E402
from aira.suite.news import feeds  # noqa: E402

SHOTS = (
    Path(os.environ["NEWS_SHELL_SHOTS"]) if os.environ.get("NEWS_SHELL_SHOTS") else None
)
NEW_ITEMS = ["Follow a word or topic", "Add a news source", "New newsletter issue"]
FOLDERS = [
    "Top stories",
    "Topics",
    "For you",
    "Search",
    "Saved",
    "My newsletter",
    "Sources & settings",
]
PIXEL = '<svg xmlns="http://www.w3.org/2000/svg" width="30" height="20"><rect width="30" height="20" fill="#6a9"/></svg>'


class _NoModel:
    def model(self, prompt):
        return None


@pytest.fixture(autouse=True)
def news(monkeypatch):
    kit.forget_schema()
    previous = suite.gateway()
    suite.set_gateway(_NoModel())
    server = NewsStub().start()
    monkeypatch.setenv(feeds.GOOGLE_BASE_ENV, server.base_url)
    monkeypatch.setenv(feeds.ALLOW_LOCAL_ENV, "1")
    yield server
    server.stop()
    suite.set_gateway(previous)


def shot(page, name: str) -> None:
    if SHOTS is None:
        return
    SHOTS.mkdir(parents=True, exist_ok=True)
    page.screenshot(path=str(SHOTS / f"{name}.png"))


def opened(browser, env, **size):
    context, page, problems = signed_in_page(browser, env, **size)
    # The stub's pictures and stories live at img.example and news.example: answered here, never fetched.
    context.route(
        "https://img.example/**",
        lambda route: route.fulfill(
            status=200, body=PIXEL, content_type="image/svg+xml"
        ),
    )
    context.route(
        "https://news.example/**",
        lambda route: route.fulfill(
            status=200, body="<title>Story</title>", content_type="text/html"
        ),
    )
    return context, page, problems


def call(context, env, method: str, path: str, body: dict | None = None) -> dict:
    reply = context.request.fetch(
        env.base + path,
        method=method,
        data=json.dumps(body) if body is not None else None,
        headers={
            "Content-Type": "application/json",
            "X-Requested-With": "aira",
            "Origin": env.base,
        },
    )
    assert reply.ok, reply.text()
    return reply.json()


def open_news(page, env, hash_: str = "news") -> None:
    go(page, env, hash_)
    page.wait_for_selector("#main .page-head h1")
    page.wait_for_selector("#main .suite-body .suite-body")
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
    page.wait_for_selector(".shell-subnav[data-section='news'] a.shell-sublink")
    return [
        text.strip()
        for text in page.locator(
            ".shell-subnav[data-section='news'] a.shell-sublink"
        ).all_inner_texts()
    ]


def test_new_offers_the_news_creates_and_each_opens_its_form(browser, env):
    context, page, problems = opened(browser, env, width=1440, height=900)
    open_news(page, env)
    assert new_menu(page) == NEW_ITEMS
    assert page.locator(".shell-new-menu [role='menuitem'] svg").count() == len(
        NEW_ITEMS
    )
    shot(page, "01-new-menu")
    page.keyboard.press("Escape")
    page.wait_for_selector(".shell-new-menu", state="detached")

    # Follow a word: Sources & settings opens on the form; the address forgets ?new= so a reload does not ask again.
    choose_new(page, "Follow a word or topic")
    page.wait_for_selector(".modal h2:has-text('Follow a word or topic')")
    assert re.search(r"#/news\?tab=settings$", page.url), page.url
    assert page.locator("#main h1").inner_text() == "Sources & settings"
    shot(page, "02-new-follow")
    page.fill(".modal input[name=query]", "metro")
    page.click(".modal button:has-text('Follow')")
    page.wait_for_selector("#main li:has-text('metro')")
    page.reload()
    page.wait_for_load_state("networkidle")
    page.wait_for_selector("#main li:has-text('metro')")
    assert page.locator(".modal").count() == 0

    choose_new(page, "Add a news source")
    page.wait_for_selector(".modal h2:has-text('Add a news source')")
    assert page.locator(".modal input[name=url]").count() == 1
    close_modal(page)

    # The next issue, before there is a newsletter: the form that starts one.
    open_news(page, env)
    choose_new(page, "New newsletter issue")
    page.wait_for_selector(".modal h2:has-text('Start my newsletter')")
    assert re.search(r"#/news\?tab=newsletter$", page.url), page.url
    page.fill(".modal input[name=name]", "Hyderabad This Week")
    page.click(".modal button:has-text('Start')")
    page.wait_for_selector("#main .nw-next-issue:has-text('No stories yet')")

    # With a newsletter and a story picked: the issue's title is ready to type in.
    article = call(context, env, "GET", "/api/news/feed?topic=top")["articles"][0]
    call(
        context,
        env,
        "POST",
        "/api/news/newsletter/picks",
        {"article_id": article["id"]},
    )
    open_news(page, env)
    choose_new(page, "New newsletter issue")
    page.wait_for_url(re.compile(r"#/news\?tab=newsletter$"))
    until(
        page,
        lambda: page.evaluate(
            "() => !!document.activeElement && !!document.activeElement.closest('.nw-next-issue') && document.activeElement.tagName === 'INPUT'"
        ),
        what="the issue title to take focus",
    )
    shot(page, "03-new-issue")
    assert not problems, problems
    context.close()


def test_the_top_search_finds_news_records_and_opens_them(browser, env):
    context, page, problems = opened(browser, env, width=1440, height=900)
    call(context, env, "POST", "/api/news/keywords", {"query": "metro"})
    articles = call(context, env, "GET", "/api/news/feed?topic=top")["articles"]
    farmers = next(a for a in articles if a["title"].startswith("Monsoon rains"))
    call(context, env, "POST", f"/api/news/articles/{farmers['id']}/save", {})
    open_news(page, env)
    search = page.locator("#appSearchInput")
    assert search.get_attribute("placeholder") == "Search news"

    suggest(page, "metro", "New metro line opens in Hyderabad")
    found = " | ".join(suggestions(page))
    assert "Search News for “metro”" in found
    assert "City News" in found, found
    assert "You follow this" in found, found
    assert "Monsoon" not in found
    shot(page, "04-suggestions")

    # A headline opens the story in a new tab.
    with context.expect_page() as story:
        page.locator(
            "#appSearchList [role='option']", has_text="New metro line opens"
        ).click()
    assert story.value.url.startswith("https://news.example/"), story.value.url
    story.value.close()

    # A saved headline is found too, and says so.
    suggest(page, "monsoon", "Monsoon rains bring relief to farmers")
    assert "Saved" in " | ".join(suggestions(page))

    # A word you follow opens its news.
    suggest(page, "metro", "You follow this")
    page.locator("#appSearchList [role='option']", has_text="You follow this").click()
    page.wait_for_url(re.compile(r"#/news\?tab=search&q=metro$"))
    page.wait_for_selector("#main h2:has-text('News about metro')")

    # A topic opens that section.
    suggest(page, "techn", "Technology")
    page.locator("#appSearchList [role='option']", has_text="Technology").click()
    page.wait_for_url(re.compile(r"#/news\?tab=topics&topic=TECHNOLOGY$"))
    page.wait_for_selector("article:has-text('New phone has a week-long battery')")

    # Enter searches all the news.
    search.fill("solar power")
    search.press("Enter")
    page.wait_for_url(re.compile(r"#/news\?tab=search&q=solar\+power$"))
    page.wait_for_selector("article:has-text('solar power makes the news today')")
    assert page.locator("#main h1").inner_text() == "Search"
    shot(page, "05-results")
    assert not problems, problems
    context.close()


def test_the_left_menu_shows_the_news_folders_and_each_opens_its_tab(browser, env):
    context, page, problems = opened(browser, env, width=1440, height=900)
    page.goto(f"{env.base}/news")
    page.wait_for_load_state("networkidle")
    page.wait_for_selector("article:has-text('Monsoon rains bring relief to farmers')")
    assert page.locator("#main h1").inner_text() == "Top stories"
    assert re.search(r"/news#/news", page.url), page.url
    assert folders(page) == FOLDERS
    assert page.locator(
        ".shell-subnav[data-section='news'] a.shell-sublink svg"
    ).count() == len(FOLDERS)
    # On a wide screen the folders are the tabs: the bar under the title is not shown twice.
    assert page.locator("#main .nw-page > .tabs").is_hidden()
    shot(page, "06-folders")
    for label, tab, marker in [
        ("Saved", "saved", "Nothing saved yet"),
        ("For you", "foryou", "Follow a word or topic"),
        ("Topics", "topics", "Your country"),
        ("My newsletter", "newsletter", "Earn from the news you follow"),
        ("Sources & settings", "settings", "Language and country"),
        ("Top stories", "top", "Monsoon rains bring relief to farmers"),
    ]:
        page.locator(".shell-subnav[data-section='news'] a", has_text=label).click()
        page.wait_for_url(re.compile(rf"#/news\?tab={tab}$"))
        page.wait_for_selector(f"#main :text('{marker}')")
        assert page.locator("#main h1").inner_text() == label
        assert (
            page.locator(".shell-subnav[data-section='news'] a[aria-current='page']")
            .inner_text()
            .strip()
            == label
        )
    assert not problems, problems
    context.close()


def test_headlines_show_as_a_grid_or_a_list_and_the_choice_stays(browser, env):
    context, page, problems = opened(browser, env, width=1440, height=900)
    open_news(page, env, "news?tab=top")
    page.wait_for_selector("#main .nw-list article")
    columns = "() => getComputedStyle(document.querySelector('.nw-list')).gridTemplateColumns.split(' ').length"
    switch = page.locator("#main .page-head .nw-layout[role='radiogroup']")
    assert switch.locator("[role='radio'][aria-checked='true']").get_attribute("aria-label") == "Grid"
    assert page.evaluate(columns) >= 2
    switch.locator("[aria-label='List']").click()
    assert page.evaluate(columns) == 1
    assert switch.locator("[aria-label='List']").get_attribute("aria-checked") == "true"
    shot(page, "07-list")
    # The choice stays on this device: another tab and a reload keep the list; arrow keys switch back.
    open_news(page, env, "news?tab=saved")
    page.reload()
    open_news(page, env, "news?tab=top")
    page.wait_for_selector("#main .nw-list article")
    assert page.evaluate(columns) == 1
    page.locator("#main .nw-layout [aria-checked='true']").focus()
    page.keyboard.press("ArrowLeft")
    assert page.evaluate(columns) >= 2
    assert "nw-layout-btn" in page.evaluate("() => document.activeElement.className")
    assert not problems, problems
    context.close()


def test_a_phone_in_dark_mode_has_no_sideways_scroll(browser, env):
    context, page, problems = opened(browser, env, width=390, height=844, dark=True)
    for tab in [
        "top",
        "topics",
        "foryou",
        "search&q=metro",
        "saved",
        "newsletter",
        "settings",
    ]:
        open_news(page, env, f"news?tab={tab}")
        page.wait_for_timeout(300)
        assert page.evaluate(
            "() => document.documentElement.scrollWidth <= window.innerWidth"
        ), tab
        # The phone keeps the tab bar (the menu is a sheet there).
        assert page.locator("#main .nw-page > .tabs").is_visible(), tab
        shot(page, f"07-phone-{tab.split('&')[0]}")
    assert not problems, problems
    context.close()
