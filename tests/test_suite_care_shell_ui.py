"""Health in the Aira frame, in a real browser against the local server.

"+ New" offers the app's main creates and each opens its form; the search box across the top lists this person's
medicines, appointments, diary days and care clients as you type, opens the one chosen, and Enter lists every
match; the left menu shows Health's tabs as folders and a folder opens its tab; /health opens the app; a phone in
dark mode has no sideways scroll. It fails on any script error. Screenshots go to ``CARE_SHELL_SHOTS`` (default:
none) for a human to look at.
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
    Path(os.environ["CARE_SHELL_SHOTS"]) if os.environ.get("CARE_SHELL_SHOTS") else None
)
NEW_ITEMS = [
    "Add a medicine",
    "Add a reading",
    "Add an appointment",
    "Write in the diary",
    "Record a care visit",
]
FOLDERS = [
    "Today",
    "Medicines",
    "Readings",
    "Diary",
    "Appointments",
    "Doctor report",
    "Emergency card",
    "Caregivers",
    "Care work",
]


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
    kit.forget_schema()
    post(context, env, "/api/care/settings", {"timezone": "UTC"})
    metformin = post(
        context,
        env,
        "/api/care/medicines",
        {"name": "Metformin", "dose": "500 mg", "times": "23:59"},
    )["medicine"]
    post(
        context,
        env,
        "/api/care/medicines",
        {"name": "Paracetamol", "as_needed": True, "min_gap_hours": 4},
    )
    appointment = post(
        context,
        env,
        "/api/care/appointments",
        {
            "title": "Dr Patel",
            "at": "2030-05-04T10:30:00Z",
            "place": "Riverside Clinic",
        },
    )["appointment"]
    post(
        context, env, "/api/care/diary", {"mood": 3, "note": "Called Dr Patel's office"}
    )
    client = post(
        context,
        env,
        "/api/care/work/clients",
        {"name": "Mrs Patel", "rate_cents": 2500},
    )["client"]
    return {"medicine": metformin, "appointment": appointment, "client": client}


def open_care(page, env, hash_: str = "care") -> None:
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
    page.wait_for_selector(".shell-subnav[data-section='care'] a.shell-sublink")
    return [
        text.strip()
        for text in page.locator(
            ".shell-subnav[data-section='care'] a.shell-sublink"
        ).all_inner_texts()
    ]


def focused_open_id(page) -> str:
    return page.evaluate(
        "() => (document.activeElement && document.activeElement.dataset.careOpen) || ''"
    )


def test_new_offers_the_health_creates_and_each_opens_its_form(browser, env):
    context, page, problems = signed_in_page(browser, env, width=1440, height=900)
    seed(context, env)
    open_care(page, env)
    assert new_menu(page) == NEW_ITEMS
    assert page.locator(".shell-new-menu [role='menuitem'] svg").count() == len(
        NEW_ITEMS
    )
    shot(page, "01-new-menu")
    page.keyboard.press("Escape")
    page.wait_for_selector(".shell-new-menu", state="detached")

    # Add a medicine: Medicines opens with its form; the address forgets ?new= so Back or a reload does not reopen it.
    choose_new(page, "Add a medicine")
    page.wait_for_selector(".modal h2:has-text('Add a medicine')")
    assert re.search(r"#/care\?tab=medicines$", page.url), page.url
    assert page.locator("#main h1").inner_text() == "Medicines"
    page.fill(".modal input[name=name]", "Aspirin")
    page.fill(".modal input[name=times]", "23:58")
    page.click(".modal button:has-text('Save')")
    page.wait_for_selector(".care-med:has-text('Aspirin')")
    page.reload()
    page.wait_for_load_state("networkidle")
    page.wait_for_selector(".care-med:has-text('Aspirin')")
    dismiss_reminders(page)
    assert page.locator(".modal:has-text('Add a medicine')").count() == 0

    choose_new(page, "Add a reading")
    page.wait_for_selector(".modal h2:has-text('What did you measure?')")
    assert re.search(r"#/care\?tab=readings$", page.url), page.url
    page.click(".modal button:has-text('Blood pressure')")
    page.wait_for_selector(".modal h2:has-text('Add blood pressure')")
    close_modal(page)

    choose_new(page, "Add an appointment")
    page.wait_for_selector(".modal h2:has-text('Add an appointment')")
    assert re.search(r"#/care\?tab=appointments$", page.url), page.url
    shot(page, "02-new-appointment")
    close_modal(page)

    # The diary's form is on its page: the mood box takes focus.
    choose_new(page, "Write in the diary")
    page.wait_for_url(re.compile(r"#/care\?tab=diary$"))
    until(
        page,
        lambda: page.evaluate(
            "() => document.activeElement && document.activeElement.name === 'mood'"
        ),
        what="the diary form to take focus",
    )

    choose_new(page, "Record a care visit")
    page.wait_for_selector(".modal h2:has-text('Record a visit')")
    assert re.search(r"#/care\?tab=work$", page.url), page.url
    assert "Mrs Patel" in page.locator(".modal select[name=client_id]").inner_text()
    close_modal(page)
    assert not problems, problems
    context.close()


def test_the_top_search_finds_health_records_and_opens_them(browser, env):
    context, page, problems = signed_in_page(browser, env, width=1440, height=900)
    seeded = seed(context, env)
    open_care(page, env)
    search = page.locator("#appSearchInput")
    assert (
        search.get_attribute("placeholder")
        == "Search medicines, appointments, readings"
    )
    search.fill("patel")
    until(page, lambda: len(suggestions(page)) >= 4, what="suggestions")
    found = " | ".join(suggestions(page))
    assert "Search Health for “patel”" in found
    assert "Dr Patel" in found and "Riverside Clinic" in found, found
    assert "Mrs Patel" in found and "Care client" in found, found
    assert "Called Dr Patel's office" in found, found
    assert "Metformin" not in found
    shot(page, "03-suggestions")

    # Choosing a record opens its tab and shows it, outlined and focused.
    page.locator("#appSearchList [role='option']", has_text="Riverside Clinic").click()
    page.wait_for_url(re.compile(r"#/care\?tab=appointments$"))
    page.wait_for_selector(".care-appt.care-found:has-text('Dr Patel')")
    assert focused_open_id(page) == f"appointment:{seeded['appointment']['id']}"

    search.fill("metf")
    until(
        page,
        lambda: any("Metformin" in text for text in suggestions(page)),
        what="the medicine suggestion",
    )
    page.locator("#appSearchList [role='option']", has_text="Metformin").click()
    page.wait_for_url(re.compile(r"#/care\?tab=medicines$"))
    page.wait_for_selector(".care-med.care-found:has-text('Metformin')")

    # A kind of reading opens the Readings tab on that kind.
    search.fill("blood pre")
    until(
        page,
        lambda: any("Blood pressure" in text for text in suggestions(page)),
        what="the reading suggestion",
    )
    page.locator("#appSearchList [role='option']", has_text="Blood pressure").click()
    page.wait_for_url(re.compile(r"#/care\?tab=readings$"))
    page.wait_for_selector(
        ".care-chips [aria-pressed='true']:has-text('Blood pressure')"
    )

    # Enter lists every match, by kind; a result opens its record.
    search.fill("patel")
    search.press("Enter")
    page.wait_for_url(re.compile(r"#/care\?tab=search&q=patel$"))
    page.wait_for_selector("#main .care-results")
    assert page.locator("#main h1").inner_text() == "Results for “patel”"
    headings = page.locator("#main .suite-h2").all_inner_texts()
    assert headings == ["Appointments (1)", "Diary (1)", "Care clients (1)"], headings
    shot(page, "04-results")
    page.locator("#main .care-result", has_text="Mrs Patel").click()
    page.wait_for_url(re.compile(r"#/care\?tab=work$"))
    page.wait_for_selector(".care-client.care-found:has-text('Mrs Patel')")

    # Nothing found: the page says so and offers the main creates.
    search.fill("zzzz nothing")
    search.press("Enter")
    page.wait_for_selector("#main .empty:has-text('Nothing matches')")
    assert page.locator("#main .empty button", has_text="Add a medicine").count() == 1
    assert not problems, problems
    context.close()


def test_the_left_menu_shows_the_health_folders_and_each_opens_its_tab(browser, env):
    context, page, problems = signed_in_page(browser, env, width=1440, height=900)
    seed(context, env)
    # The app has its own address: /health opens it on Today.
    page.goto(f"{env.base}/health")
    page.wait_for_load_state("networkidle")
    page.wait_for_selector("#main .page-head h1")
    dismiss_reminders(page)
    assert page.locator("#main h1").inner_text() == "Today"
    assert re.search(r"/health#/care", page.url), page.url
    assert folders(page) == FOLDERS
    assert page.locator(
        ".shell-subnav[data-section='care'] a.shell-sublink svg"
    ).count() == len(FOLDERS)
    # On a wide screen the folders stand in for the tab strip under the heading.
    assert page.locator("#main .care-tabstrip").is_hidden()
    shot(page, "05-folders-today")
    for label, tab in [
        ("Readings", "readings"),
        ("Doctor report", "report"),
        ("Care work", "work"),
    ]:
        page.locator(".shell-subnav[data-section='care'] a", has_text=label).click()
        page.wait_for_url(re.compile(rf"#/care\?tab={tab}$"))
        until(
            page,
            lambda label=label: (
                page.locator("#main [role='tab'][aria-selected='true']").inner_text()
                == label
            ),
            what=f"the {label} tab",
        )
        assert page.locator("#main h1").inner_text() == label
        assert (
            page.locator(".shell-subnav[data-section='care'] a[aria-current='page']")
            .inner_text()
            .strip()
            == label
        )
    assert page.title() == "Care work · Health · Monosage"
    shot(page, "06-folders-work")
    assert not problems, problems
    context.close()


def test_a_phone_in_dark_mode_has_no_sideways_scroll(browser, env):
    context, page, problems = signed_in_page(
        browser, env, width=390, height=844, dark=True
    )
    seed(context, env)
    for tab in [
        "today",
        "medicines",
        "readings",
        "diary",
        "appointments",
        "report",
        "card",
        "caregivers",
        "work",
        "search&q=patel",
    ]:
        open_care(page, env, f"care?tab={tab}")
        page.wait_for_selector("#main .suite-section")
        assert page.evaluate(
            "() => document.documentElement.scrollWidth <= window.innerWidth"
        ), tab
        # The menu is folded away on a phone, so the tabs stay under the heading.
        assert page.locator("#main .care-tabstrip").is_visible(), tab
        shot(page, f"07-phone-{tab.split('&')[0]}")
    assert not problems, problems
    context.close()
