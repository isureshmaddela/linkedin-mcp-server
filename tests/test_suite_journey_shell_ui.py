"""Life in the Aira frame, in a real browser against the local server.

"+ New" offers a goal, a habit, today's journal, a timeline event and a document, and each opens its form; the
search box across the top lists this person's goals, habits, journal days, timeline events and documents as you type,
opens the one chosen, and Enter lists every match; the left menu shows Life's tabs as folders and a folder opens its
tab; /life opens the app; a phone in dark mode has no sideways scroll. It fails on any script error. Screenshots go to
``JOURNEY_SHELL_SHOTS`` (default: none) for a human to look at.
"""

# ruff: noqa: F811
from __future__ import annotations

import json
import os
import re
from pathlib import Path

import pytest

patchright = pytest.importorskip("patchright.sync_api")

from aira.suite import kit  # noqa: E402
from test_careers import env, stub  # noqa: E402, F401
from test_leads_ui import browser, go, signed_in_page, until  # noqa: E402, F401
from test_suite_care_ui import dismiss_reminders  # noqa: E402

SHOTS = (
    Path(os.environ["JOURNEY_SHELL_SHOTS"])
    if os.environ.get("JOURNEY_SHELL_SHOTS")
    else None
)
NEW_ITEMS = [
    "New goal",
    "New habit",
    "Write in your journal",
    "Add to your timeline",
    "Add a document",
]
FOLDERS = ["Overview", "Goals", "Habits", "Journal", "Timeline", "Documents"]
SUBNAV = ".shell-subnav[data-section='journey'] a.shell-sublink"


def shot(page, name: str) -> None:
    if SHOTS is None:
        return
    SHOTS.mkdir(parents=True, exist_ok=True)
    page.screenshot(path=str(SHOTS / f"{name}.png"))


def call(context, env, method: str, path: str, body: dict) -> dict:
    reply = context.request.fetch(
        env.base + path,
        method=method,
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
    kit.forget_schema()
    goal = call(
        context,
        env,
        "POST",
        "/api/journey/goals",
        {"title": "Run a 5K race", "category": "health", "steps": ["Buy shoes"]},
    )["goal"]
    swim = call(context, env, "POST", "/api/journey/goals", {"title": "Learn to swim"})[
        "goal"
    ]
    call(
        context,
        env,
        "PATCH",
        f"/api/journey/goals/{swim['id']}",
        {"status": "completed"},
    )
    habit = call(
        context,
        env,
        "POST",
        "/api/journey/habits",
        {"title": "Run 20 minutes", "per_week": 3},
    )["habit"]
    call(context, env, "POST", "/api/journey/habits", {"title": "Read 20 pages"})
    entry = call(
        context,
        env,
        "POST",
        "/api/journey/journal",
        {"mood": 4, "body": "A good run by the river this morning.", "grateful": ""},
    )["entry"]
    event = call(
        context,
        env,
        "POST",
        "/api/journey/events",
        {
            "title": "First marathon run",
            "event_date": "2019-05-04",
            "kind": "achievement",
            "location": "Hyderabad",
        },
    )["event"]
    document = call(
        context,
        env,
        "POST",
        "/api/journey/documents",
        {"title": "My passport", "kind": "passport", "issuer": "Passport Office"},
    )["document"]
    return {
        "goal": goal,
        "habit": habit,
        "entry": entry,
        "event": event,
        "document": document,
    }


def open_life(page, env, hash_: str = "journey") -> None:
    go(page, env, hash_)
    page.wait_for_selector("#main .page-head h1")
    page.wait_for_timeout(400)
    dismiss_reminders(page)


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


def folders(page) -> list[str]:
    page.wait_for_selector(SUBNAV)
    return [text.strip() for text in page.locator(SUBNAV).all_inner_texts()]


def focused_open_id(page) -> str:
    return page.evaluate(
        "() => (document.activeElement && document.activeElement.dataset.jrOpen) || ''"
    )


def test_new_offers_the_life_creates_and_each_opens_its_form(browser, env):
    context, page, problems = signed_in_page(browser, env, width=1440, height=900)
    seed(context, env)
    open_life(page, env)
    assert new_menu(page) == NEW_ITEMS
    assert page.locator(".shell-new-menu [role='menuitem'] svg").count() == len(
        NEW_ITEMS
    )
    shot(page, "01-new-menu")
    page.keyboard.press("Escape")
    page.wait_for_selector(".shell-new-menu", state="detached")

    # New goal: Goals opens with its form; the address forgets ?new= so Back or a reload does not reopen it.
    choose_new(page, "New goal")
    page.wait_for_selector(".modal h2:has-text('New goal')")
    assert re.search(r"#/journey\?tab=goals$", page.url), page.url
    assert page.locator("#main h1").inner_text() == "Goals"
    page.fill(".modal input[name=title]", "Visit Goa")
    page.click(".modal button:has-text('Add goal')")
    page.wait_for_selector(".jr-card:has-text('Visit Goa')")
    page.reload()
    page.wait_for_load_state("networkidle")
    page.wait_for_selector(".jr-card:has-text('Visit Goa')")
    dismiss_reminders(page)
    assert page.locator(".modal:has-text('New goal')").count() == 0

    choose_new(page, "New habit")
    page.wait_for_selector(".modal h2:has-text('New habit')")
    assert re.search(r"#/journey\?tab=habits$", page.url), page.url
    close_modal(page)

    # The journal's form is on its page: today's mood takes focus (the mood already picked today).
    choose_new(page, "Write in your journal")
    page.wait_for_url(re.compile(r"#/journey\?tab=journal$"))
    until(
        page,
        lambda: page.evaluate(
            "() => !!document.activeElement && document.activeElement.classList.contains('jr-mood') && document.activeElement.getAttribute('aria-pressed') === 'true'"
        ),
        what="the journal's mood to take focus",
    )

    choose_new(page, "Add to your timeline")
    page.wait_for_selector(".modal h2:has-text('Add to your timeline')")
    assert re.search(r"#/journey\?tab=timeline$", page.url), page.url
    shot(page, "02-new-event")
    close_modal(page)

    choose_new(page, "Add a document")
    page.wait_for_selector(".modal h2:has-text('Add a document')")
    assert re.search(r"#/journey\?tab=documents$", page.url), page.url
    close_modal(page)
    assert not problems, problems
    context.close()


def test_the_top_search_finds_life_records_and_opens_them(browser, env):
    context, page, problems = signed_in_page(browser, env, width=1440, height=900)
    seeded = seed(context, env)
    open_life(page, env)
    search = page.locator("#appSearchInput")
    assert (
        search.get_attribute("placeholder")
        == "Search goals, habits, journal, timeline, documents"
    )
    search.fill("run")
    until(page, lambda: len(suggestions(page)) >= 5, what="suggestions")
    found = " | ".join(suggestions(page))
    assert "Search Life for “run”" in found
    assert "Run a 5K race" in found and "Run 20 minutes" in found, found
    assert "A good run by the river" in found and "First marathon run" in found, found
    assert "Read 20 pages" not in found and "Learn to swim" not in found
    shot(page, "03-suggestions")

    # A habit, a journal day or an event opens its tab with the record outlined and focused.
    page.locator("#appSearchList [role='option']", has_text="Run 20 minutes").click()
    page.wait_for_url(re.compile(r"#/journey\?tab=habits$"))
    page.wait_for_selector(".jr-habit.jr-found:has-text('Run 20 minutes')")
    assert focused_open_id(page) == f"habit:{seeded['habit']['id']}"

    search.fill("good run")
    until(
        page,
        lambda: any("A good run" in text for text in suggestions(page)),
        what="the journal suggestion",
    )
    page.locator("#appSearchList [role='option']", has_text="A good run").click()
    page.wait_for_url(re.compile(r"#/journey\?tab=journal&month=\d{4}-\d{2}$"))
    page.wait_for_selector(".jr-entry.jr-found")
    assert focused_open_id(page) == f"entry:{seeded['entry']['id']}"

    # A goal and a document open in their dialog.
    search.fill("5k")
    until(
        page,
        lambda: any("Run a 5K race" in text for text in suggestions(page)),
        what="the goal suggestion",
    )
    page.locator("#appSearchList [role='option']", has_text="Run a 5K race").click()
    page.wait_for_selector(".modal h2:has-text('Run a 5K race')")
    assert "tab=goals" in page.url and seeded["goal"]["id"] in page.url
    close_modal(page)

    search.fill("passport office")
    until(
        page,
        lambda: any("My passport" in text for text in suggestions(page)),
        what="the document suggestion",
    )
    page.locator("#appSearchList [role='option']", has_text="My passport").click()
    page.wait_for_selector(".modal h2:has-text('My passport')")
    close_modal(page)

    # Enter lists every match, by kind; a result opens its record.
    search.fill("run")
    search.press("Enter")
    page.wait_for_url(re.compile(r"#/journey\?tab=search&q=run$"))
    page.wait_for_selector("#main .jr-results")
    assert page.locator("#main h1").inner_text() == "Results for “run”"
    headings = page.locator("#main .suite-h2").all_inner_texts()
    assert headings == ["Goals (1)", "Habits (1)", "Journal (1)", "Timeline (1)"], (
        headings
    )
    shot(page, "04-results")
    page.locator("#main .jr-result", has_text="First marathon run").click()
    page.wait_for_url(re.compile(r"#/journey\?tab=timeline$"))
    page.wait_for_selector(".jr-event.jr-found:has-text('First marathon run')")
    assert focused_open_id(page) == f"event:{seeded['event']['id']}"

    # Nothing found: the page says so and offers the main creates.
    search.fill("zzzz nothing")
    search.press("Enter")
    page.wait_for_selector("#main .empty:has-text('Nothing matches')")
    assert page.locator("#main .empty button", has_text="New goal").count() == 1
    assert not problems, problems
    context.close()


def test_the_left_menu_shows_the_life_folders_and_each_opens_its_tab(browser, env):
    context, page, problems = signed_in_page(browser, env, width=1440, height=900)
    seed(context, env)
    # The app has its own address: /life opens it on Overview.
    page.goto(f"{env.base}/life")
    page.wait_for_load_state("networkidle")
    page.wait_for_selector("#main .page-head h1")
    dismiss_reminders(page)
    assert page.locator("#main h1").inner_text() == "Overview"
    assert re.search(r"/life#/journey", page.url), page.url
    assert folders(page) == FOLDERS
    assert page.locator(f"{SUBNAV} svg").count() == len(FOLDERS)
    # On a wide screen the folders stand in for the tab strip under the heading.
    assert page.locator("#main .jr-tabstrip").is_hidden()
    shot(page, "05-folders-overview")
    for label, tab in [
        ("Goals", "goals"),
        ("Journal", "journal"),
        ("Habits", "habits"),
    ]:
        page.locator(SUBNAV, has_text=label).click()
        page.wait_for_url(re.compile(rf"#/journey\?tab={tab}$"))
        until(
            page,
            lambda label=label: page.locator("#main h1").inner_text() == label,
            what=f"the {label} tab",
        )
        assert (
            page.locator(f"{SUBNAV}[aria-current='page']").inner_text().strip() == label
        )
    assert page.title() == "Habits · Life · Monosage"

    # "All goals" shows the finished ones too (an empty filter value used to fall back to the active ones).
    page.locator(SUBNAV, has_text="Goals").click()
    page.wait_for_selector(".jr-card:has-text('Run a 5K race')")
    assert page.locator(".jr-card:has-text('Learn to swim')").count() == 0
    page.select_option("#main select[aria-label='Status']", "all")
    page.wait_for_url(re.compile(r"status=all"))
    page.wait_for_selector(".jr-card:has-text('Learn to swim')")
    shot(page, "06-all-goals")
    assert not problems, problems
    context.close()


def test_a_phone_in_dark_mode_has_no_sideways_scroll(browser, env):
    context, page, problems = signed_in_page(
        browser, env, width=390, height=844, dark=True
    )
    seed(context, env)
    for tab in [
        "overview",
        "goals",
        "habits",
        "journal",
        "timeline",
        "documents",
        "search&q=run",
    ]:
        open_life(page, env, f"journey?tab={tab}")
        page.wait_for_selector("#main .suite-body .card, #main .suite-kpis")
        assert page.evaluate(
            "() => document.documentElement.scrollWidth <= window.innerWidth"
        ), tab
        # The menu is folded away on a phone, so the tabs stay under the heading.
        assert page.locator("#main .jr-tabstrip").is_visible(), tab
        shot(page, f"07-phone-{tab.split('&')[0]}")
    assert not problems, problems
    context.close()
