"""Sales in the Aira frame, in a real browser: "+ New" offers the app's main creates and opens their forms, the
search box across the top finds clients, deals and invoices and opens them, Enter lists every match, and the
left menu shows the tabs as folders."""

# ruff: noqa: F811
from __future__ import annotations

import json
import re

import pytest

patchright = pytest.importorskip("patchright.sync_api")

import auto_apply_assistant as local  # noqa: E402
from aira import suite  # noqa: E402
from aira.suite import kit, public  # noqa: E402
from test_careers import env, stub  # noqa: E402, F401
from test_leads_ui import browser, go, signed_in_page, until  # noqa: E402, F401

HEADERS = {"Content-Type": "application/json", "X-Requested-With": "aira"}
FOLDERS = [
    "overview",
    "deals",
    "clients",
    "activities",
    "proposals",
    "projects",
    "invoices",
    "retainers",
    "reports",
    "settings",
]


class _NoModel:
    def model(self, prompt):
        return None


@pytest.fixture(autouse=True)
def setup():
    kit.forget_schema()
    public.set_index(local._public_link_index)
    previous = suite.gateway()
    suite.set_gateway(_NoModel())
    yield
    suite.set_gateway(previous)


def _api(context, env, method, path, body=None):
    response = context.request.fetch(
        env.base + path,
        method=method,
        data=json.dumps(body) if body is not None else None,
        headers={**HEADERS, "Origin": env.base},
    )
    assert response.status < 400, (path, response.status, response.text())
    return response.json()


def _seed(context, env):
    bloom = _api(
        context,
        env,
        "POST",
        "/api/studio/clients",
        {
            "name": "Maya Patel",
            "company": "Bloom Bakery",
            "email": "maya@bloom.example",
        },
    )["client"]
    _api(
        context,
        env,
        "POST",
        "/api/studio/deals",
        {"title": "Website refresh", "client_id": bloom["id"], "value_cents": 450000},
    )
    _api(
        context,
        env,
        "POST",
        "/api/studio/invoices",
        {
            "client_id": bloom["id"],
            "items": [{"description": "Logo", "unit_cents": 150000}],
        },
    )
    return bloom


def test_new_search_and_folders_in_the_frame(browser, env):
    context, page, problems = signed_in_page(browser, env, width=1440, height=900)
    _seed(context, env)
    go(page, env, "studio")
    page.wait_for_selector(".page-head h1:has-text('Overview')")
    assert "/sales" in page.url

    # The left menu: every tab is a folder with its own icon; the page's own tab bar steps aside on a wide screen.
    links = page.locator("#primaryNav .shell-sublink")
    until(page, lambda: links.count() == len(FOLDERS), what="the folders")
    assert [
        links.nth(i).get_attribute("data-tab") for i in range(links.count())
    ] == FOLDERS
    assert page.locator("#primaryNav .shell-sublink svg").count() == len(FOLDERS)
    assert page.locator(".studio-page > .tabs").is_hidden()
    page.locator("#primaryNav .shell-sublink[data-tab='invoices']").click()
    page.wait_for_selector(".page-head h1:has-text('Invoices')")
    page.wait_for_selector("text=INV-0001")
    assert re.search(r"tab=invoices", page.url)
    assert (
        page.locator("#primaryNav .shell-sublink[data-tab='invoices']").get_attribute(
            "aria-current"
        )
        == "page"
    )

    # "+ New": the app's creates; each opens the tab's own form and leaves the address clean.
    page.locator(".shell-new").click()
    menu = page.locator(".shell-new-menu")
    assert menu.locator("[role='menuitem']").all_inner_texts() == [
        "New deal",
        "New client",
        "New proposal",
        "New invoice",
        "New activity",
    ]
    menu.get_by_role("menuitem", name="New client").click()
    page.wait_for_selector(".modal input[name='name']")
    assert "new=" not in page.url and "tab=clients" in page.url
    page.locator(".modal input[name='name']").fill("Arjun Rao")
    page.locator(".modal input[name='company']").fill("Rao Logistics")
    page.get_by_role("button", name="Add client").click()
    page.wait_for_selector("text=Rao Logistics")
    page.locator(".shell-new").click()
    page.locator(".shell-new-menu").get_by_role("menuitem", name="New deal").click()
    page.wait_for_selector(".modal input[name='title']")
    assert "new=" not in page.url and "tab=deals" in page.url
    page.keyboard.press("Escape")
    until(page, lambda: page.locator(".modal").count() == 0, what="the form to close")
    page.reload()
    page.wait_for_load_state("networkidle")
    page.wait_for_selector(".page-head h1:has-text('Deals')")
    assert page.locator(".modal").count() == 0  # a refresh does not open the form again
    page.locator(".shell-new").click()
    page.locator(".shell-new-menu").get_by_role("menuitem", name="New invoice").click()
    page.wait_for_selector(".modal h2:has-text('New invoice')")
    page.keyboard.press("Escape")

    # The search box across the top: the app's records as you type; choosing one opens it.
    box = page.locator("#appSearchInput")
    assert box.get_attribute("placeholder") == "Search Sales"
    box.fill("bloom")
    options = page.locator("#appSearchList [role='option']")
    until(page, lambda: options.count() >= 4, what="the suggestions")
    texts = options.all_inner_texts()
    assert any("Maya Patel (Bloom Bakery)" in t for t in texts)
    assert any("Website refresh" in t for t in texts)
    assert any("Invoice INV-0001" in t for t in texts)
    options.filter(has_text="Invoice INV-0001").click()
    page.wait_for_selector(".drawer h2:has-text('Invoice INV-0001')")
    page.keyboard.press("Escape")
    box.fill("rao")
    until(
        page,
        lambda: any("Arjun Rao" in t for t in options.all_inner_texts()),
        what="the client",
    )
    options.filter(has_text="Arjun Rao").click()
    page.wait_for_selector(".drawer h2:has-text('Arjun Rao')")
    page.keyboard.press("Escape")

    # Enter: every kind that matches, each row opening its record.
    box.fill("bloom")
    box.press("Enter")
    page.wait_for_selector(".page-head h1:has-text('Results for')")
    assert "tab=search" in page.url
    sections = page.locator(".suite-section h2")
    until(
        page, lambda: sections.count() == 3, what="the results"
    )  # the heading draws before the matches
    headings = sections.all_inner_texts()
    assert headings == ["Clients (1)", "Deals (1)", "Invoices (1)"]
    page.locator(".studio-results[data-kind='deals'] tr.suite-row").first.click()
    page.wait_for_selector(".drawer h2:has-text('Website refresh')")
    page.keyboard.press("Escape")
    box.fill("nothing like this")
    box.press("Enter")
    page.wait_for_selector("text=Nothing in Sales matches")
    assert page.get_by_role("button", name="Add a client").count() == 1
    assert not problems, problems


@pytest.mark.parametrize("dark", [False, True], ids=["light", "dark"])
def test_phone_keeps_the_tab_bar_and_fits(browser, env, dark):
    context, page, problems = signed_in_page(
        browser, env, width=390, height=844, dark=dark
    )
    _seed(context, env)
    for hash_ in (
        "studio?tab=deals",
        "studio?tab=invoices",
        "studio?tab=search&q=bloom",
        "studio?tab=clients",
    ):
        go(page, env, hash_)
        page.wait_for_selector(
            ".studio-page > .suite-body > .suite-body"
        )  # drawn once loaded
        assert page.locator(".studio-page > .tabs").is_visible()
        wide = page.evaluate(
            "() => document.documentElement.scrollWidth - window.innerWidth"
        )
        assert wide <= 1, (hash_, wide)
    assert not problems, problems
