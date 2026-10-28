"""Careers in the Aira frame, in a real browser against the local server.

"+ New" offers the app's main creates and each opens its form; the search box across the top lists jobs, recruiters
and interviews as you type, opens the one chosen, and Enter filters the jobs list; the left menu shows each page's
tabs as folders and a folder opens its tab. It fails on any script error. Screenshots go to ``CAREERS_SHELL_SHOTS``
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

from test_careers import env, stub  # noqa: E402, F401
from test_leads_ui import browser, go, signed_in_page, until  # noqa: E402, F401

SHOTS = (
    Path(os.environ["CAREERS_SHELL_SHOTS"])
    if os.environ.get("CAREERS_SHELL_SHOTS")
    else None
)
NEW_ITEMS = [
    "Add a job",
    "Schedule an interview",
    "Add a recruiter",
    "Upload a document",
    "Create a job alert",
]


def shot(page, name: str) -> None:
    if SHOTS is None:
        return
    SHOTS.mkdir(parents=True, exist_ok=True)
    page.screenshot(path=str(SHOTS / f"{name}.png"))


def post(context, env, path: str, body: dict) -> dict:
    headers = {
        "Content-Type": "application/json",
        "X-Requested-With": "aira",
        "Origin": env.base,
    }
    reply = context.request.post(
        env.base + path, data=json.dumps(body), headers=headers
    )
    assert reply.ok, reply.text()
    return reply.json()


def seed(context, env) -> dict:
    jobs = [
        post(
            context,
            env,
            "/api/jobs/import",
            {"manual": {"title": title, "company": company, "location": "Austin, TX"}},
        )["job"]
        for title, company in [
            ("Store manager", "Corner Bakery"),
            ("Solution Architect", "Acme Cloud"),
        ]
    ]
    person = post(
        context,
        env,
        "/api/recruiters",
        {"name": "Sam Rivera", "company": "Acme Cloud", "email": "sam@acme.example"},
    )
    post(
        context,
        env,
        "/api/interviews",
        {
            "company": "Acme Cloud",
            "role": "Solution Architect",
            "interview_type": "recruiter_screen",
            "start_at": "2030-11-20T10:00",
            "timezone": "America/Chicago",
        },
    )
    return {"jobs": jobs, "recruiter": person["recruiter"]}


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


def folders(page, section: str) -> list[str]:
    page.wait_for_selector(f".shell-subnav[data-section='{section}'] a.shell-sublink")
    return [
        text.strip()
        for text in page.locator(
            f".shell-subnav[data-section='{section}'] a.shell-sublink"
        ).all_inner_texts()
    ]


def suggestions(page) -> list[str]:
    return page.locator("#appSearchList [role='option']").all_inner_texts()


def test_new_offers_the_careers_creates_and_each_opens_its_form(browser, env):
    context, page, problems = signed_in_page(browser, env, width=1440, height=900)
    seed(context, env)
    go(page, env, "dashboard")
    page.wait_for_selector(".shell-new")
    assert new_menu(page) == NEW_ITEMS
    # Every item has an icon and the menu works from the keyboard.
    assert page.locator(".shell-new-menu [role='menuitem'] svg").count() == len(
        NEW_ITEMS
    )
    shot(page, "01-new-menu")
    page.keyboard.press("Escape")
    page.wait_for_selector(".shell-new-menu", state="detached")

    # Add a job: Jobs opens with its "Add a job" form; the address forgets ?new= so Back or a reload does not reopen it.
    choose_new(page, "Add a job")
    page.wait_for_selector(".modal h2:has-text('Add a job')")
    assert "new=" not in page.url and re.search(r"/careers#/jobs/all$", page.url), (
        page.url
    )
    modal = page.locator(".modal")
    modal.get_by_label("Job title").fill("Night baker")
    modal.get_by_label("Company or employer").fill("Corner Bakery")
    modal.locator("button", has_text="Add job").click()
    page.wait_for_selector(".drawer h2:has-text('Night baker')")
    page.keyboard.press("Escape")
    until(
        page,
        lambda: "Night baker" in page.locator("#main .list").inner_text(),
        what="the new job in the list",
    )
    page.reload()
    page.wait_for_load_state("networkidle")
    page.wait_for_selector("#main .list .row")
    assert page.locator(".modal").count() == 0

    choose_new(page, "Schedule an interview")
    page.wait_for_selector(".modal h2:has-text('Add interview')")
    assert re.search(r"#/interviews$", page.url), page.url
    shot(page, "02-new-interview")
    close_modal(page)

    choose_new(page, "Add a recruiter")
    page.wait_for_selector(".modal h2:has-text('Add recruiter')")
    assert re.search(r"#/recruiters\?tab=contacts$", page.url), page.url
    close_modal(page)

    with page.expect_file_chooser() as chooser:
        choose_new(page, "Upload a document")
    assert chooser.value is not None
    page.wait_for_selector("#careersUpload .dropzone")
    until(
        page,
        lambda: page.evaluate(
            "() => document.activeElement && document.activeElement.classList.contains('dropzone')"
        ),
        what="the drop zone to take focus",
    )
    assert re.search(r"#/documents$", page.url), page.url

    choose_new(page, "Create a job alert")
    page.wait_for_selector(".modal h2:has-text('Create job alert')")
    assert "new=" not in page.url and "tab=alerts" in page.url
    close_modal(page)
    assert not problems, problems
    context.close()


def test_the_top_search_finds_jobs_recruiters_and_interviews(browser, env):
    context, page, problems = signed_in_page(browser, env, width=1440, height=900)
    seeded = seed(context, env)
    go(page, env, "dashboard")
    search = page.locator("#appSearchInput")
    assert search.get_attribute("placeholder") == "Search jobs, people, interviews"
    search.fill("acme")
    until(page, lambda: len(suggestions(page)) >= 4, what="suggestions")
    found = " | ".join(suggestions(page))
    assert "Search Careers for “acme”" in found
    assert (
        "Solution Architect" in found
        and "Sam Rivera" in found
        and "Recruiter Screen · Acme Cloud" in found
    ), found
    assert "Store manager" not in found
    shot(page, "03-suggestions")

    # Choosing a record opens it.
    page.locator("#appSearchList [role='option']", has_text="Sam Rivera").click()
    page.wait_for_url(
        re.compile(rf"#/recruiters/{seeded['recruiter']['recruiter_id']}$")
    )
    page.wait_for_selector(".drawer h2:has-text('Sam Rivera')")
    page.keyboard.press("Escape")

    search.fill("solution")
    until(
        page,
        lambda: any("Solution Architect" in text for text in suggestions(page)),
        what="the job suggestion",
    )
    page.locator(
        "#appSearchList [role='option']", has_text="Solution Architect"
    ).click()
    page.wait_for_selector(".drawer h2:has-text('Solution Architect')")
    page.keyboard.press("Escape")

    # Enter shows the results: the jobs list filtered by the words, with a heading that says so.
    go(page, env, "dashboard")
    search.fill("bakery")
    search.press("Enter")
    page.wait_for_url(re.compile(r"#/jobs/all\?q=bakery"))
    until(
        page,
        lambda: (
            "Jobs matching “bakery”"
            in page.locator("#main .panel-head h3").inner_text()
        ),
        what="the results heading",
    )
    rows = page.locator("#main .list .row .title").all_inner_texts()
    assert rows == ["Store manager"], rows
    assert page.locator("#jobFilterQ").input_value() == "bakery"
    shot(page, "04-results")

    # Nothing found: the empty list says so and offers to clear the filter.
    search.fill("zzzz nothing")
    search.press("Enter")
    page.wait_for_selector("#main .empty:has-text('No jobs match')")
    assert page.locator("#main .empty button", has_text="Clear filters").count() == 1

    # On Recruiters, Enter searches the contacts.
    go(page, env, "recruiters")
    page.wait_for_selector("#recruiterSearch")
    search.fill("rivera")
    search.press("Enter")
    page.wait_for_url(re.compile(r"#/recruiters\?tab=contacts&q=rivera"))
    until(
        page,
        lambda: page.locator("#recruiterSearch").input_value() == "rivera",
        what="the contact search",
    )
    until(
        page,
        lambda: "Sam Rivera" in page.locator("#main .list").inner_text(),
        what="the matching contact",
    )
    assert not problems, problems
    context.close()


def test_the_left_menu_follows_a_job_search_and_old_links_still_work(browser, env):
    context, page, problems = signed_in_page(browser, env, width=1440, height=900)
    seed(context, env)
    go(page, env, "dashboard")
    page.wait_for_selector("#primaryNav a[data-section='dashboard']")
    # The menu is grouped like a job search: find a job, apply, follow up.
    assert page.locator("#primaryNav .nav-label").all_text_contents() == [
        "Home",
        "Find",
        "Apply",
        "Follow up",
        "Your materials",
        "Insights",
    ]
    groups = page.evaluate(
        r"""() => [...document.querySelectorAll('#primaryNav .nav-group')].map(g =>
            [g.querySelector('.nav-label').textContent, [...g.querySelectorAll(':scope > a')].map(a => a.innerText.replace(/\s*\d+$/, '').trim())])"""
    )
    assert groups == [
        ["Home", ["Dashboard"]],
        ["Find", ["Jobs", "Research"]],
        ["Apply", ["Auto-apply", "Needs you", "Applications"]],
        [
            "Follow up",
            ["Interviews", "Messages & recruiters", "Email updates", "Offers"],
        ],
        ["Your materials", ["Profile & resume", "Documents"]],
        ["Insights", ["Analytics"]],
    ]
    shot(page, "05-menu-groups")

    # Applications is the pipeline; Offers and Email updates are pages of their own under Follow up.
    page.click("#primaryNav a[data-section='applications']")
    page.wait_for_selector("#main h1:has-text('Applications')")
    assert page.locator(".shell-subnav[data-section='applications']").count() == 0
    page.click("#primaryNav a[data-section='offers']")
    page.wait_for_url(re.compile(r"#/offers$"))
    page.wait_for_selector("#main h1:has-text('Offers')")
    assert (
        page.locator("#primaryNav a[data-section='offers']").get_attribute(
            "aria-current"
        )
        == "page"
    )
    # Old addresses land on the new pages (bookmarks, notifications, links in older emails).
    for old, new, heading in [
        ("applications?tab=offers", r"#/offers$", "Offers"),
        ("applications?tab=email", r"#/emails$", "Email updates"),
        ("applications?tab=queue", r"#/needs$", "Needs you"),
        ("jobs/autopilot", r"#/autopilot$", "Auto-apply"),
        ("jobs?tab=autopilot", r"#/autopilot$", "Auto-apply"),
    ]:
        go(page, env, old)
        page.wait_for_url(re.compile(new))
        page.wait_for_selector(f"#main h1:has-text('{heading}')")

    # Jobs has three folders; where a job comes from is a chip on All jobs.
    page.click("#primaryNav a[data-section='jobs']")
    assert folders(page, "jobs") == ["All jobs", "Recommended", "Saved"]
    chips = page.locator("#main .careers-source-chip")
    assert [t.strip() for t in chips.all_inner_texts()] == [
        "Every source",
        "LinkedIn",
        "Free job boards",
        "Google Jobs",
        "Company portals",
    ]
    assert chips.first.get_attribute("aria-current") == "true"
    chips.filter(has_text="Free job boards").click()
    page.wait_for_url(re.compile(r"#/jobs/all\?from=aira"))
    until(
        page,
        lambda: (
            "Free job boards"
            in page.locator("#main .search-panel select").first.inner_text()
        ),
        what="the free job boards as the search source",
    )
    assert (
        "Free job boards: Monosage searches"
        in page.locator("#main .search-panel").inner_text()
    )
    assert (
        page.locator("#main .careers-source-chip[aria-current='true']")
        .inner_text()
        .strip()
        == "Free job boards"
    )
    shot(page, "06-source-chips")
    # The old source folders are chips now: #/jobs/linkedin opens All jobs with LinkedIn chosen.
    go(page, env, "jobs/linkedin")
    page.wait_for_url(re.compile(r"#/jobs/all\?from=linkedin"))
    until(
        page,
        lambda: (
            page.locator("#main .careers-source-chip[aria-current='true']")
            .inner_text()
            .strip()
            == "LinkedIn"
        ),
        what="the LinkedIn chip",
    )
    page.locator(".shell-subnav[data-section='jobs'] a", has_text="Saved").click()
    page.wait_for_url(re.compile(r"#/jobs/saved"))
    until(
        page,
        lambda: (
            page.locator("#main .subnav a[aria-current='page']").inner_text() == "Saved"
        ),
        what="the Saved folder",
    )
    assert page.locator("#main .panel-head h3").first.inner_text() == "Saved"
    assert page.locator("#main .careers-source-chips").count() == 0
    # Only the open page's folders show, so the menu stays short.
    assert page.locator(".shell-subnav[data-section='jobs']").is_visible()

    # Profile & resume: the parts of Settings an application is filled from, as Careers folders.
    page.click("#primaryNav a[data-section='profile']")
    assert folders(page, "profile") == ["Profile", "Resume", "Standard answers"]
    page.wait_for_selector("#main h1:has-text('Profile & resume')")
    page.locator(".shell-subnav[data-section='profile'] a", has_text="Resume").click()
    page.wait_for_url(re.compile(r"#/profile\?tab=resume"))

    page.click("#primaryNav a[data-section='interviews']")
    assert folders(page, "interviews") == ["Upcoming", "Past", "All"]
    page.locator(".shell-subnav[data-section='interviews'] a", has_text="Past").click()
    page.wait_for_url(re.compile(r"#/interviews\?tab=past"))
    page.wait_for_selector("#main .empty:has-text('No past interviews')")

    page.click("#primaryNav a[data-section='recruiters']")
    assert folders(page, "recruiters") == [
        "Contacts",
        "Messages",
        "Find recruiters",
        "Lists",
    ]

    page.click("#primaryNav a[data-section='documents']")
    assert folders(page, "documents")[:2] == ["All files", "Resumes"]
    page.locator(
        ".shell-subnav[data-section='documents'] a", has_text="Cover letters"
    ).click()
    page.wait_for_url(re.compile(r"#/documents\?tab=cover_letter"))
    page.wait_for_selector("#main .empty:has-text('No cover letter files yet')")

    # Research: the workspaces, with their own heading (not the app's name again).
    page.click("#primaryNav a[data-section='careers']")
    assert folders(page, "careers") == [
        "Advanced search",
        "Company",
        "Salary",
        "Market",
        "Interview prep",
        "Travel",
        "Learn",
        "Alerts",
    ]
    assert page.locator("#main h1").inner_text() == "Research"
    page.locator(".shell-subnav[data-section='careers'] a", has_text="Alerts").click()
    page.wait_for_url(re.compile(r"#/careers\?tab=alerts"))
    page.wait_for_selector("#main h2:has-text('Job alerts')")
    shot(page, "06-folders-research")
    assert not problems, problems
    context.close()


def test_needs_you_gathers_what_waits_and_counts_it_in_the_menu(browser, env):
    context, page, problems = signed_in_page(browser, env, width=1440, height=900)
    made = seed(context, env)
    job = made["jobs"][1]
    reply = context.request.patch(
        f"{env.base}/api/jobs/{job['job_id']}/stage",
        data=json.dumps({"stage": "needs_review", "reason": "Check the packet"}),
        headers={
            "Content-Type": "application/json",
            "X-Requested-With": "aira",
            "Origin": env.base,
        },
    )
    assert reply.ok, reply.text()
    go(page, env, "dashboard")
    # The menu counts what waits for you.
    badge = page.locator("#primaryNav a[data-section='needs'] .count")
    until(
        page,
        lambda: badge.count() == 1 and badge.inner_text() == "1",
        what="the Needs you count",
    )
    page.click("#primaryNav a[data-section='needs']")
    page.wait_for_url(re.compile(r"#/needs$"))
    page.wait_for_selector("#main h1:has-text('Needs you')")
    page.wait_for_selector(
        "#main .panel:has-text('Needs your review') .row:has-text('Solution Architect')"
    )
    shot(page, "07-needs-you")
    # A phone shows Needs you in the bottom bar, with a dot for the count.
    page.set_viewport_size({"width": 390, "height": 844})
    go(page, env, "dashboard")
    page.wait_for_selector("#bottomNav a[data-section='needs']")
    until(
        page,
        lambda: page.locator("#bottomNav a[data-section='needs'] .dot").count() == 1,
        what="the dot on Needs you",
    )
    assert (
        page.locator("#bottomNav a[data-section='needs'] .dot").get_attribute(
            "aria-label"
        )
        == "1 waiting"
    )
    assert page.evaluate("() => document.documentElement.scrollWidth - innerWidth") <= 1
    assert not problems, problems
    context.close()


def test_a_phone_folds_the_job_search_form_and_keeps_the_list_in_view(browser, env):
    context, page, problems = signed_in_page(
        browser, env, width=390, height=844, dark=True
    )
    seed(context, env)
    go(page, env, "jobs/all")
    page.wait_for_selector("details.careers-finder")
    assert page.locator("details.careers-finder").get_attribute("open") is None
    assert page.locator("#main .list .row").first.is_visible()
    # The menu is folded away on a phone, so the tabs stay under the heading.
    assert page.locator("#main .subnav.careers-tabstrip").is_visible()
    page.click("details.careers-finder > summary")
    page.wait_for_selector("details.careers-finder[open] .search-panel")
    # Nothing is wider than the screen.
    assert page.evaluate(
        "() => document.documentElement.scrollWidth <= window.innerWidth"
    )
    shot(page, "07-phone-jobs")
    assert not problems, problems
    context.close()
