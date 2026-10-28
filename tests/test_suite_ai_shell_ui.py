"""AI in the Aira frame, in a real browser: "+ New" offers a conversation, a routine, something to remember and an
earning goal and opens their forms; the search across the top suggests conversations, memories, routines and agents
and opens the one chosen; Enter shows every match (activity too) on a results page; the tabs are folders in the left
menu and the page is named after the open one. No script errors along the way, and a phone fits."""

# ruff: noqa: F811
from __future__ import annotations

import re

import pytest

pytest.importorskip("patchright.sync_api")

from aira.suite import kit  # noqa: E402
from test_careers import env, stub  # noqa: E402, F401
from test_leads_ui import browser, signed_in_page, until  # noqa: E402, F401
from test_suite_learn_ui import no_model, post  # noqa: E402, F401

NEW_ITEMS = ["New conversation", "New routine", "Something to remember", "Earning goal"]
FOLDERS = ["Chat", "Agents", "Approvals", "Routines", "Memory", "Earn", "Activity"]
EVERY_DAY = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]


def open_ai(page, env, hash_="ai"):
    page.goto(f"{env.base}/ai#/{hash_}")
    page.wait_for_load_state("networkidle")
    page.wait_for_selector("#main .ai-page")


def search(page, text):
    page.fill("#appSearchInput", "")
    page.click("#appSearchInput")
    page.keyboard.type(text)
    page.wait_for_selector("#appSearchList:not([hidden]) .shell-search-option")
    page.wait_for_timeout(250)  # the newest keystroke's list


def choose_new(page, label):
    page.click(".shell-new")
    page.wait_for_selector(".shell-new-menu")
    page.click(f".shell-new-menu [role='menuitem']:has-text('{label}')")


def active_class(page):
    return page.evaluate(
        "() => document.activeElement ? document.activeElement.className : ''"
    )


def seed(context, env):
    post(context, env, "/api/ai/chat", {"message": "Remember that I am vegetarian"})
    post(
        context,
        env,
        "/api/ai/chat",
        {"message": "Add milk and bread to the shopping list"},
    )
    post(
        context,
        env,
        "/api/ai/routines",
        {
            "title": "Money check",
            "prompt": "How much did I earn this month?",
            "at": "19:00",
            "days": EVERY_DAY,
        },
    )


def test_ai_new_search_and_folders_in_the_frame(browser, env):
    kit.forget_schema()
    context, page, problems = signed_in_page(browser, env, width=1440, height=900)
    seed(context, env)

    # The address names the app and the page head names the open folder, not the app again.
    open_ai(page, env)
    assert re.search(r"/ai#/ai", page.url)
    assert page.locator("#main .page-head h1").inner_text() == "Chat"

    # The left menu shows the tabs as folders with icons; a folder opens its tab, and the bar under the title (the same
    # list again) is folded away on a wide screen.
    folders = page.locator("#primaryNav .shell-sublink")
    assert [t.strip() for t in folders.all_inner_texts()] == FOLDERS
    assert page.locator("#primaryNav .shell-sublink .shell-subdot").count() == 0
    assert not page.locator("#main .ai-page > .tabs").is_visible()
    page.click("#primaryNav .shell-sublink[data-tab='routines']")
    until(page, lambda: "tab=routines" in page.url, what="the Routines folder")
    page.wait_for_selector("#main .page-head h1:has-text('Routines')")
    page.wait_for_selector(".ai-routine:has-text('Money check')")
    assert (
        page.locator("#primaryNav .shell-sublink[aria-current='page']")
        .inner_text()
        .strip()
        == "Routines"
    )

    # "+ New" lists the app's creates; each opens its form, and the address forgets ?new= at once.
    page.click(".shell-new")
    page.wait_for_selector(".shell-new-menu")
    assert [
        t.strip()
        for t in page.locator(".shell-new-menu [role='menuitem']").all_inner_texts()
    ] == NEW_ITEMS
    page.keyboard.press("Escape")

    choose_new(page, "New routine")
    page.wait_for_selector(".modal:has-text('New routine') [name=prompt]")
    assert "tab=routines" in page.url and "new=" not in page.url
    page.fill(".modal [name=title]", "Evening plan")
    page.fill(".modal [name=prompt]", "What do I need to do tomorrow?")
    page.locator(".modal .actions button").last.click()
    page.wait_for_selector(".modal", state="detached")
    page.wait_for_selector(".ai-routine:has-text('Evening plan')")

    choose_new(page, "Something to remember")
    until(page, lambda: "tab=memory" in page.url, what="the Memory tab")
    page.wait_for_selector(".ai-memory:has-text('I am vegetarian')")
    until(
        page,
        lambda: "ai-memory-input" in active_class(page),
        what="the memory box to take focus",
    )
    assert "new=" not in page.url

    choose_new(page, "Earning goal")
    page.wait_for_selector(".modal:has-text('Set an earning goal') [name=target]")
    assert "tab=earn" in page.url and "new=" not in page.url
    page.keyboard.press("Escape")
    page.wait_for_selector(".modal", state="detached")

    choose_new(page, "New conversation")
    until(page, lambda: "tab=chat" in page.url, what="the chat")
    page.wait_for_selector(".ai-welcome")
    until(
        page,
        lambda: "ai-input" in active_class(page),
        what="the message box to take focus",
    )
    assert "new=" not in page.url and "session=" not in page.url

    # Back does not open a form again.
    page.go_back()
    page.wait_for_load_state("networkidle")
    page.wait_for_timeout(400)
    assert page.locator(".modal").count() == 0

    # The search across the top: conversations, memories, routines and agents, each opening itself.
    open_ai(page, env, "ai?tab=agents")
    assert (
        page.get_attribute("#appSearchInput", "placeholder")
        == "Search chats, memories and routines"
    )
    search(page, "vegetarian")
    texts = page.locator("#appSearchList .shell-search-option").all_inner_texts()
    assert any("I am vegetarian" in t and "Memory" in t for t in texts), texts
    assert any(
        "Remember that I am vegetarian" in t and "Conversation" in t for t in texts
    ), texts
    page.locator("#appSearchList .shell-search-option:has-text('Memory')").first.click()
    page.wait_for_selector(".ai-memory.ai-picked:has-text('I am vegetarian')")
    assert "tab=memory" in page.url and "open=" not in page.url

    search(page, "money check")
    page.locator(
        "#appSearchList .shell-search-option:has-text('Routine')"
    ).first.click()
    page.wait_for_selector(".ai-routine.ai-picked:has-text('Money check')")
    assert "tab=routines" in page.url and "open=" not in page.url

    search(page, "planneragent")
    page.locator(
        "#appSearchList .shell-search-option:has-text('Agent · Planner')"
    ).first.click()
    page.wait_for_selector(".modal:has-text('Configure PlannerAgent')")
    assert "tab=agents" in page.url and "open=" not in page.url
    page.keyboard.press("Escape")
    page.wait_for_selector(".modal", state="detached")

    search(page, "milk")
    page.locator(
        "#appSearchList .shell-search-option:has-text('Conversation')"
    ).first.click()
    until(page, lambda: "session=" in page.url, what="the conversation")
    page.wait_for_selector(".ai-msg.mine:has-text('Add milk and bread')")
    # The conversation's numbers sit in one slim line under its name.
    page.wait_for_selector(".ai-main .ai-stats:has-text('Answers from')")
    assert page.locator("#main .suite-kpis").count() == 0

    # Enter shows every match on a results page, activity included, with a way to ask Aira the same words.
    search(page, "milk")
    page.keyboard.press("Enter")
    until(
        page,
        lambda: "tab=results" in page.url and "q=milk" in page.url,
        what="the search results",
    )
    page.wait_for_selector("#main .page-head h1:has-text('Search results')")
    page.wait_for_selector(
        ".ai-hits .ai-hit:has-text('Add milk and bread to the shopping list')"
    )
    page.wait_for_selector(
        ".suite-section:has-text('Activity') >> text=Add milk, bread to the shopping list"
    )
    assert (
        page.locator("a.btn:has-text('Ask Aira: “milk”')").get_attribute("href")
        == "#/ai?tab=chat&ask=milk"
    )
    page.locator(".ai-hits .ai-hit").first.click()
    until(
        page, lambda: "session=" in page.url, what="the conversation from the results"
    )

    # A search that finds nothing says so and offers the chat.
    search_text = "zebra crossing"
    page.fill("#appSearchInput", search_text)
    page.press("#appSearchInput", "Enter")
    page.wait_for_selector(".ai-results-for:has-text('Nothing in AI matches')")
    page.wait_for_selector(".empty a:has-text('Open the chat')")

    assert not problems, problems


def test_ai_in_the_frame_on_a_phone_fits(browser, env):
    kit.forget_schema()
    context, page, problems = signed_in_page(
        browser, env, width=390, height=844, dark=True
    )
    seed(context, env)
    # The menu (and its "+ New") is a sheet on a phone, so the tab bar and the head's New conversation stay.
    for hash_ in (
        "ai?tab=chat",
        "ai?tab=agents",
        "ai?tab=approvals",
        "ai?tab=routines",
        "ai?tab=memory",
        "ai?tab=earn",
        "ai?tab=activity",
        "ai?tab=results&q=milk",
    ):
        open_ai(page, env, hash_)
        page.wait_for_timeout(300)
        assert page.locator("#main .ai-page > .tabs").is_visible(), hash_
        assert not page.evaluate(
            "() => document.documentElement.scrollWidth > window.innerWidth + 2"
        ), hash_
    open_ai(page, env, "ai?tab=chat")
    assert page.locator("#main .ai-new-chat").is_visible()
    # An agent's consent pills sit on their own line, above the switch, not under it.
    open_ai(page, env, "ai?tab=agents")
    row = page.locator(".ai-agent").first
    pills = row.locator(".ai-agent-pills").bounding_box()
    switch = row.locator(".ai-switch").bounding_box()
    assert pills["y"] + pills["height"] <= switch["y"] + 1, (pills, switch)
    # An empty activity filter shows its empty state on a phone too.
    open_ai(page, env, "ai?tab=activity&q=zebra")
    page.wait_for_selector(".empty:has-text('Nothing matches')")
    assert not problems, problems
