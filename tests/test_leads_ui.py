"""Aira Leads in a real browser: the Find, Leads, Settings and History tabs against the real local server.

The journey registers an account, connects the stand-in SerpApi key, searches Google Maps (dry-run cost first),
saves leads, edits one in the drawer, enriches it from a stand-in company website, finds news, drafts a message,
exports, scores with an ideal customer profile and checks the Sales Navigator source explains why it is off. It
fails on any console error. Screenshots go to ``LEADS_SHOTS`` (default: a scratch folder) for a human to look at.
"""

# ruff: noqa: F811
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest

patchright = pytest.importorskip("patchright.sync_api")

from serpapi_stub import DEFAULT_KEY  # noqa: E402
from test_careers import PASSWORD, env, stub  # noqa: E402, F401
from test_leads import site  # noqa: E402, F401

ROOT = Path(__file__).resolve().parent.parent
NODE = shutil.which("node")
SHOTS = (
    Path(os.environ.get("LEADS_SHOTS", "")) if os.environ.get("LEADS_SHOTS") else None
)


def shot(page, name: str) -> None:
    if SHOTS is None:
        return
    SHOTS.mkdir(parents=True, exist_ok=True)
    page.screenshot(path=str(SHOTS / f"{name}.png"), full_page=True)


@pytest.fixture
def browser():
    with patchright.sync_playwright() as playwright:
        try:
            launched = playwright.chromium.launch(headless=True)
        except Exception as error:  # noqa: BLE001
            pytest.skip(f"Chromium is not available: {error}")
        yield launched
        launched.close()


def signed_in_page(browser, env, *, width=1366, height=900, dark=False):
    context = browser.new_context(
        viewport={"width": width, "height": height},
        color_scheme="dark" if dark else "light",
    )
    headers = {
        "Content-Type": "application/json",
        "X-Requested-With": "aira",
        "Origin": env.base,
    }
    registered = context.request.post(
        env.base + "/api/auth/register",
        data=json.dumps({"email": "leads-ui@example.com", "password": PASSWORD}),
        headers=headers,
    )
    assert registered.status == 201, registered.text()
    keyed = context.request.post(
        env.base + "/api/airaapi/key",
        data=json.dumps({"api_key": DEFAULT_KEY}),
        headers=headers,
    )
    assert keyed.status == 200, keyed.text()
    page = context.new_page()
    page.set_default_timeout(60_000)  # the full suite runs many browsers at once
    problems: list[str] = []
    page.on(
        "console",
        lambda m: (
            problems.append(f"console.{m.type}: {m.text}")
            if m.type == "error"
            else None
        ),
    )
    page.on("pageerror", lambda e: problems.append(f"pageerror: {e}"))
    return context, page, problems


def until(page, check, *, timeout=10.0, what="the page to change"):
    """Poll a Python-side condition. (wait_for_function evaluates a string, which the app's CSP forbids.)"""
    waited = 0.0
    while waited < timeout:
        if check():
            return
        page.wait_for_timeout(100)
        waited += 0.1
    raise AssertionError(f"timed out waiting for {what}")


def go(page, env, hash_):
    page.goto(f"{env.base}/#/{hash_}")
    page.wait_for_load_state("networkidle")


def test_leads_is_in_the_navigation_and_first_run_explains_the_ways_to_get_leads(
    browser, env
):
    context, page, problems = signed_in_page(browser, env)
    go(page, env, "leads")
    page.wait_for_selector("#ldContent .ld-intro")
    assert (
        page.locator("#primaryNav a[data-section='leads']").inner_text().strip()
        == "Leads"
    )
    assert page.locator(".ld-head h1").inner_text() == "Leads"
    assert "Four ways to get leads" in page.locator(".ld-intro").inner_text()
    assert page.locator(".ld-source").count() == 7
    page.keyboard.press("g")
    page.keyboard.press("j")
    page.wait_for_url(re.compile(r"#/jobs"))
    page.keyboard.press("g")
    page.keyboard.press("l")
    page.wait_for_url(re.compile(r"#/leads"))
    shot(page, "01-first-run")
    assert not problems, problems
    context.close()


def test_the_whole_leads_journey(browser, env, site):
    context, page, problems = signed_in_page(browser, env)
    go(page, env, "leads")
    page.wait_for_selector("#ldSearchForm")

    # ---- Google Maps: the form validates, shows the cost first, asks once, then lists leads ----
    page.click("#ldSearchBtn")
    assert "kind of business" in page.locator("#ldFormError").inner_text()
    page.fill("#ld-query", "coffee roasters")
    page.fill("#ld-location", "New York")
    page.click("#ldSearchBtn")
    dialog = page.locator(".modal")
    dialog.wait_for()
    assert "Uses 1 SerpApi search" in dialog.inner_text()
    assert env.stub.search_calls == 0, "the dry run spends nothing"
    dialog.get_by_role("button", name="Search").click()
    page.wait_for_selector("#ldResults .ld-table tbody tr")
    assert page.locator("#ldResults .ld-table tbody tr").count() == 4
    assert "4 leads found" in page.locator(".ld-summary").inner_text()
    assert "1 SerpApi search" in page.locator("#ldCost").inner_text()
    assert env.stub.search_calls == 1
    shot(page, "02-find-results")
    # the fit score explains itself on focus
    page.locator(".ld-score").first.focus()
    assert (
        page.locator(".ld-score")
        .first.get_attribute("aria-label")
        .startswith("Not scored")
    )
    # the same search again is a cache hit and is not confirmed a second time
    page.click("#ldSearchBtn")
    until(
        page,
        lambda: "cache" in page.locator("#ldCost").inner_text(),
        what="the cache note",
    )
    assert page.locator(".modal").count() == 0 and env.stub.search_calls == 1

    # ---- save two, then the rest ----
    # the cost line is set from the dry run; the table repaints (and the selection resets) when the search itself returns
    page.wait_for_selector("#ldSearchBtn:not([aria-busy])")
    boxes = page.locator("#ldResults tbody input[type=checkbox]")
    boxes.nth(0).check()
    boxes.nth(1).check()
    page.get_by_role("button", name="Save selected (2)").click()
    page.wait_for_selector(".toast.success")
    assert page.locator("#ldResults .ld-dup").count() == 2
    page.get_by_role("button", name="Save all new (2)").click()
    until(
        page,
        lambda: page.locator("#ldResults .ld-dup").count() == 4,
        what="all four leads to show as saved",
    )

    # ---- the Leads tab ----
    page.click(".ld-subnav a:has-text('Leads')")
    page.wait_for_selector(".ld-leads tbody tr")
    assert page.locator(".ld-leads tbody tr").count() == 4
    assert "4" in page.locator(".ld-list.on .count").inner_text()
    page.fill("#ldFilterText", "Fixture")
    until(
        page,
        lambda: 1 <= page.locator(".ld-leads tbody tr").count() < 4,
        what="the filter to narrow the list",
    )
    page.fill("#ldFilterText", "")
    until(
        page,
        lambda: page.locator(".ld-leads tbody tr").count() == 4,
        what="the filter to clear",
    )
    csv_href = page.locator("a:has-text('Export CSV')").get_attribute("href")
    xlsx_href = page.locator("a:has-text('Export Excel')").get_attribute("href")
    assert (
        csv_href.startswith("/api/leads/export?format=csv")
        and "format=xlsx" in xlsx_href
    )
    exported = context.request.get(env.base + csv_href)
    assert exported.status == 200 and exported.headers["content-type"].startswith(
        "text/csv"
    )
    assert "Fixture Coffee Roasters" in exported.text()

    # ---- the drawer: edit, enrich, news, draft ----
    page.locator(".ld-open").first.click()
    page.wait_for_selector(".ld-drawer")
    page.select_option("#ldStatus", "contacted")
    page.fill("#ldNotes", "Called Tuesday about roasting volume.")
    page.fill("#ldf-website", site.url)
    page.get_by_role("button", name="Save changes").click()
    page.wait_for_selector(".toast.success")
    page.get_by_role("button", name="Enrich from website").click()
    page.wait_for_selector(".ld-contacts li")
    contacts = page.locator(".ld-found").inner_text()
    assert (
        "hello@acme.test" in contacts
        and "role address" in contacts
        and "another domain" in contacts
    )
    assert "never guesses" in page.locator(".ld-enrich").inner_text()
    page.locator(
        ".ld-contacts li:has-text('hello@acme.test') >> text=Use this email"
    ).click()
    until(
        page,
        lambda: page.locator("#ldf-email").input_value() == "hello@acme.test",
        what="the email to be saved",
    )
    shot(page, "03-drawer")
    page.get_by_role("button", name="Find recent news").click()
    until(
        page,
        lambda: (
            page.locator("#ldNewsCost").count() == 1
            and len(page.locator("#ldNewsCost").inner_text()) > 0
        ),
        what="the news cost line",
    )
    assert "Headlines only" in page.locator(".ld-news").inner_text()
    page.get_by_role("button", name="Draft outreach").click()
    page.wait_for_selector("#ldDraftGo")
    assert "never sent" in page.locator(".modal").inner_text()
    assert (
        page.locator(".modal button", has_text=re.compile("send", re.I)).count() == 0
    ), "there is no send button"
    page.fill("#ldOffer", "Faster roasting logistics")
    page.click("#ldDraftGo")
    page.wait_for_selector("#ldDraftBody")
    assert "Faster roasting logistics" in page.locator("#ldDraftBody").input_value()
    shot(page, "04-draft")
    page.locator(".modal .actions button", has_text="Close").click()
    page.keyboard.press("Escape")
    page.wait_for_selector(".ld-drawer", state="detached")
    assert page.locator(".ld-status.contacted").count() == 1

    # ---- bulk actions ----
    page.locator(".ld-leads tbody input[type=checkbox]").nth(1).check()
    page.wait_for_selector(".ld-bulk:not([hidden])")
    page.select_option("#ldBulkStatus", "qualified")
    page.wait_for_selector(".toast.success >> text=updated")
    assert page.locator(".ld-status.qualified").count() == 1

    # ---- Settings: the ideal customer profile re-scores the leads ----
    page.click(".ld-subnav a:has-text('Settings')")
    page.wait_for_selector("#ldIcpForm")
    page.fill("#ldicp-titles", "Owner, Roaster")
    page.fill("#ldicp-keywords", "coffee")
    page.locator("#ldIcpForm input[value=website]:not(#ldicp-service-website)").check()
    page.click("#ldIcpSave")
    until(
        page,
        lambda: "4 leads re-scored" in page.locator("#ldIcpResult").inner_text(),
        what="the re-score count",
    )
    shot(page, "05-settings")
    page.click(".ld-subnav a:has-text('Leads')")
    page.wait_for_selector(".ld-leads tbody tr")
    scores = page.locator(".ld-leads .ld-score-n").all_inner_texts()
    assert scores and all(s != "—" for s in scores), scores
    page.locator(".ld-score").first.focus()
    assert page.locator(".ld-score .ld-tip").first.is_visible()
    shot(page, "06-leads-scored")

    # ---- Sales Navigator explains why it is off ----
    go(page, env, "leads?tab=find")
    page.click("#ldSource-navigator")
    page.wait_for_selector("#ldNavNotice")
    notice = page.locator("#ldNavNotice").inner_text()
    assert "read only" in notice.lower() or "not available" in notice.lower()
    assert (
        "Sales Navigator subscription" in notice
        and "restricts automated access" in notice
    )
    assert page.locator("#ldSearchBtn").is_disabled()
    assert page.locator("#ldNavNotice a[href='#/settings/integrations']").count() == 1
    shot(page, "07-sales-navigator")

    # ---- CSV import ----
    page.click("#ldSource-csv")
    page.fill(
        "#ldCsvText",
        "Name,Title,Company,Email\nPat Lee,CEO,Lee Roasting,pat@leeroasting.test\n",
    )
    page.click("#ldCsvImport")
    page.wait_for_selector(".ld-csv-result .ld-notice")
    assert "1 lead imported" in page.locator(".ld-csv-result").inner_text()

    # ---- History ----
    page.click(".ld-subnav a:has-text('History')")
    page.wait_for_selector("#ldHistory tbody tr")
    history = page.locator("#ldHistory").inner_text()
    assert "Google Maps" in history and "4 added" in history or "added" in history
    shot(page, "08-history")
    assert not problems, problems
    context.close()


def test_deep_links_prefill_but_never_run(browser, env):
    context, page, problems = signed_in_page(browser, env)
    go(page, env, "leads?tab=find&source=maps&query=dentists&location=Austin")
    page.wait_for_selector("#ldSearchForm")
    assert page.locator("#ld-query").input_value() == "dentists"
    assert page.locator("#ld-location").input_value() == "Austin"
    assert (
        page.locator("#ldResults .ld-table").count() == 0 and env.stub.search_calls == 0
    )
    go(page, env, "leads?tab=find&source=people&titles=VP%20Sales&keywords=SaaS")
    page.wait_for_selector("#ld-titles")
    assert page.locator("#ld-titles").input_value() == "VP Sales"
    assert env.stub.search_calls == 0
    assert not problems, problems
    context.close()


def test_people_search_and_load_more_and_saved_searches(browser, env):
    context, page, problems = signed_in_page(browser, env)
    go(page, env, "leads?tab=find&source=people")
    page.wait_for_selector("#ld-titles")
    page.fill("#ld-titles", "VP Sales, Head of Sales")
    page.fill("#ld-location", "Texas")
    page.click("#ldSearchBtn")
    page.locator(".modal").get_by_role("button", name="Search").click()
    page.wait_for_selector("#ldResults .ld-table tbody tr")
    names = page.locator("#ldResults .ld-name strong").all_inner_texts()
    assert {"Jane Doe", "Raj Patel", "Ana Silva"} <= set(names)
    assert "skipped" in page.locator(".ld-summary").inner_text()
    page.get_by_role("button", name="Save this search").click()
    page.locator(".modal #ldSearchName").fill("Texas sales leaders")
    page.locator(".modal").get_by_role("button", name="Save search").click()
    page.wait_for_selector(".ld-saved-list li")
    assert "Texas sales leaders" in page.locator(".ld-saved-list").inner_text()
    page.get_by_role("button", name="Run Texas sales leaders").click()
    page.wait_for_selector(".toast.success >> text=added")
    page.get_by_role("button", name="Delete Texas sales leaders").click()
    page.locator(".modal").get_by_role("button", name="Delete").click()
    page.wait_for_selector(".ld-saved-list", state="detached")
    assert not problems, problems
    context.close()


@pytest.mark.parametrize("dark", [False, True], ids=["light", "dark"])
@pytest.mark.parametrize("width", [1366, 390], ids=["desktop", "phone"])
def test_the_screens_fit_every_viewport_and_theme(browser, env, dark, width):
    context, page, problems = signed_in_page(
        browser, env, width=width, height=900 if width > 500 else 800, dark=dark
    )
    headers = {
        "Content-Type": "application/json",
        "X-Requested-With": "aira",
        "Origin": env.base,
    }
    csv = "Name,Title,Company,Email,Website\n" + "\n".join(
        f"Person {n},Head of Ops,Company {n},p{n}@c{n}.test,https://c{n}.test"
        for n in range(6)
    )
    assert (
        context.request.post(
            env.base + "/api/leads/items/import-csv",
            data=json.dumps({"csv": csv}),
            headers=headers,
        ).status
        == 201
    )
    tag = f"{'dark' if dark else 'light'}-{width}"
    for tab in ("find", "leads", "settings", "history"):
        go(page, env, f"leads?tab={tab}")
        page.wait_for_selector("#ldContent > *")
        page.wait_for_timeout(250)
        overflow = page.evaluate(
            "document.documentElement.scrollWidth - document.documentElement.clientWidth"
        )
        assert overflow <= 1, f"{tab} overflows the page by {overflow}px at {width}px"
        shot(page, f"v-{tag}-{tab}")
    go(page, env, "leads?tab=leads")
    page.wait_for_selector(".ld-leads tbody tr")
    page.locator(".ld-open").first.click()
    page.wait_for_selector(".ld-drawer")
    shot(page, f"v-{tag}-drawer")
    assert (
        page.evaluate(
            "document.documentElement.scrollWidth - document.documentElement.clientWidth"
        )
        <= 1
    )
    assert not problems, problems
    context.close()


def test_a_missing_key_explains_setup_and_the_other_sources_still_work(browser, env):
    context = browser.new_context(viewport={"width": 1366, "height": 900})
    headers = {
        "Content-Type": "application/json",
        "X-Requested-With": "aira",
        "Origin": env.base,
    }
    assert (
        context.request.post(
            env.base + "/api/auth/register",
            data=json.dumps({"email": "nokey@example.com", "password": PASSWORD}),
            headers=headers,
        ).status
        == 201
    )
    page = context.new_page()
    problems: list[str] = []
    page.on("console", lambda m: problems.append(m.text) if m.type == "error" else None)
    go(page, env, "leads?tab=find")
    page.wait_for_selector(".ld-setup")
    assert "Connect SerpApi" in page.locator(".ld-setup").inner_text()
    assert page.locator(".ld-setup a[href='#/airaapi?tab=account']").count() == 1
    assert page.locator("#ldSearchBtn").is_disabled()
    page.click("#ldSource-csv")
    assert page.locator("#ldCsvImport").is_enabled()
    shot(page, "09-no-key")
    assert not [p for p in problems if "401" not in p], problems
    context.close()


# ---- the pure logic, under node -------------------------------------------------------------------------------------


@pytest.mark.skipif(NODE is None, reason="node is not installed")
def test_leads_logic_and_syntax():
    assert NODE is not None
    for script in ("views-leads.js", "views-leads-outreach.js"):
        check = subprocess.run(
            [NODE, "--check", f"auto_apply_assistant/{script}"],
            cwd=ROOT,
            capture_output=True,
            text=True,
            timeout=60,
            check=False,
        )
        assert check.returncode == 0, check.stderr
    result = subprocess.run(
        [NODE, "tests/js/leads.test.mjs"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "0 failed" in result.stdout


class _ConnectedLinkedIn:
    """The person's LinkedIn tools, connected, answering with two Sales Navigator leads (the reader itself is tested apart)."""

    def __init__(self):
        self.calls = []

    def linkedin_state(self):
        return "connected"

    def linkedin_call(self, name, arguments):
        self.calls.append((name, arguments))
        return {
            "url": arguments.get("url", ""),
            "pages_read": 1,
            "warnings": [],
            "leads": [
                {
                    "name": "Maria Lopez",
                    "title": "Director of Sales",
                    "company": "Northwind Cloud",
                    "location": "Austin, Texas",
                    "profile_url": "https://www.linkedin.com/sales/lead/ACwAAA111,NAME_SEARCH",
                    "linkedin_url": "https://www.linkedin.com/in/maria-lopez-77",
                    "degree": "2nd",
                    "tenure_in_role": "2 years",
                },
                {
                    "name": "Chen Wei",
                    "title": "VP Sales",
                    "company": "Contoso Data",
                    "location": "Dallas, Texas",
                    "profile_url": "https://www.linkedin.com/sales/lead/ACwAAA222,NAME_SEARCH",
                    "degree": "3rd",
                },
            ],
        }

    def sender_name(self):
        return ""

    def model(self, prompt):
        return None


def test_sales_navigator_results_are_read_previewed_and_saved(browser, env):
    from aira.leads import service

    bridge = _ConnectedLinkedIn()
    previous = service.gateway()
    service.set_gateway(bridge)
    try:
        context, page, problems = signed_in_page(browser, env)
        go(page, env, "leads?tab=find&source=sales_navigator")
        page.wait_for_selector("#ldSearchForm")
        assert page.locator("#ldSearchBtn").is_enabled()
        assert "Read only" in page.locator("#ldNavNotice").inner_text()
        page.click("#ldSearchBtn")
        assert "Sales Navigator" in page.locator("#ldFormError").inner_text()
        page.fill("#ld-url", "https://example.com/not-sales-navigator")
        page.click("#ldSearchBtn")
        assert "Sales Navigator" in page.locator("#ldFormError").inner_text()
        assert bridge.calls == []
        url = "https://www.linkedin.com/sales/search/people?query=(keywords:sales)"
        page.fill("#ld-url", url)
        page.click("#ldSearchBtn")
        page.wait_for_selector("#ldResults .ld-table tbody tr")
        assert page.locator("#ldResults .ld-table tbody tr").count() == 2
        text = page.locator("#ldResults").inner_text()
        assert (
            "Maria Lopez" in text
            and "Director of Sales" in text
            and "Northwind Cloud" in text
        )
        assert (
            bridge.calls[0][0] == "search_sales_navigator"
            and bridge.calls[0][1]["url"] == url
        )
        assert env.stub.search_calls == 0, "Sales Navigator spends no SerpApi search"
        shot(page, "09-sales-navigator-results")
        page.get_by_role("button", name="Save all new (2)").click()
        until(
            page,
            lambda: page.locator("#ldResults .ld-dup").count() == 2,
            what="both leads to be marked saved",
        )
        go(page, env, "leads?tab=leads")
        page.wait_for_selector(".ld-table tbody tr")
        table = page.locator(".ld-table").inner_text()
        assert "Maria Lopez" in table and "Chen Wei" in table
        assert not problems, problems
        context.close()
    finally:
        service.set_gateway(previous)
