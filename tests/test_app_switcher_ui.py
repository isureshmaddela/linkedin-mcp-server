"""One account, many apps, in a real browser against the local server.

The app switcher lists Careers, Leads and API; opening an app shows only that app's sections in the sidebar;
deep links land inside the right app; ``g`` then an app's key opens it; the account menu holds the email, the
account link, theme, shortcuts and sign out; the switcher works from the keyboard and, at phone width, from
the More sheet. Screenshots go to ``SWITCHER_SHOTS`` (default: none) for a human to look at.
"""

# ruff: noqa: F811
from __future__ import annotations

import os
import re
from pathlib import Path

import pytest

patchright = pytest.importorskip("patchright.sync_api")

from aira import apps as app_catalog  # noqa: E402
from test_careers import env, stub  # noqa: E402, F401
from test_leads_ui import browser, go, signed_in_page, until  # noqa: E402, F401

SHOTS = (
    Path(os.environ.get("SWITCHER_SHOTS", ""))
    if os.environ.get("SWITCHER_SHOTS")
    else None
)
CAREERS_SECTIONS = [
    "dashboard",
    "jobs",
    "applications",
    "interviews",
    "recruiters",
    "documents",
    "analytics",
    "careers",
]
# The Account group shows under every app: Today and Display and reading (each person's everyday pages), the
# Business hub (industry apps) and Settings.
ACCOUNT_SECTIONS = ["today", "display", "hub", "settings"]


def shot(page, name: str) -> None:
    if SHOTS is None:
        return
    SHOTS.mkdir(parents=True, exist_ok=True)
    page.screenshot(path=str(SHOTS / f"{name}.png"), full_page=False)


def sections(page) -> list[str]:
    return page.locator("#primaryNav a[data-section]").evaluate_all(
        "links => links.map(a => a.dataset.section)"
    )


def labels(page) -> dict[str, str]:
    return dict(
        page.locator("#primaryNav a[data-section]").evaluate_all(
            "links => links.map(a => [a.dataset.section, a.querySelector('span:not([aria-hidden])').textContent.trim()])"
        )
    )


def open_switcher(page):
    page.click("#appSwitcherBtn")
    page.wait_for_selector("#appSwitcher:not([hidden])")
    return page.locator("#appSwitcher")


@pytest.fixture
def account_url(monkeypatch):
    monkeypatch.setenv("AIRA_ACCOUNT_URL", "http://127.0.0.1:8780/")
    yield "http://127.0.0.1:8780"
    monkeypatch.delenv("AIRA_ACCOUNT_URL", raising=False)


def test_the_switcher_lists_the_apps_and_each_app_shows_only_its_sections(browser, env):
    context, page, problems = signed_in_page(browser, env)
    go(page, env, "dashboard")
    page.wait_for_selector("#primaryNav a[data-section='dashboard']")
    # Careers is the default app: its sections plus the Account group, nothing from Marketing or Developers.
    assert sections(page) == [*CAREERS_SECTIONS, *ACCOUNT_SECTIONS]
    assert labels(page)["careers"] == "Research"
    assert page.locator("#brandApp").inner_text() == "Careers"
    assert page.locator("#navAppHead strong").inner_text() == "Careers"
    assert page.locator("#navAppHead .muted").inner_text() == "Find, apply and track"
    assert page.locator(".nav-label").all_text_contents() == ["Careers", "Account"]
    shot(page, "01-careers")

    switcher = open_switcher(page)
    tiles = switcher.locator(".app-tile")
    assert tiles.evaluate_all("tiles => tiles.map(t => t.dataset.app)") == [
        "home",  # Home (Today) first, like a suite's account home
        "careers",
        "api",
        # the everyday apps, then the business apps, each under its own heading: fewer apps, each with sections
        "social",
        "care",
        "learn",
        "play",
        "planner",
        "news",
        "videos",
        "journey",
        "ai",
        "stocks",
        "studio",
        "marketing",
        "service",
        "commerce",
        "experiences",
        "ads",
    ]
    assert [
        t.lower() for t in switcher.locator(".popover-subtitle").all_inner_texts()
    ] == [
        "everyday",
        "business apps",
    ]
    assert (
        switcher.locator(".app-tile[aria-current='true']").get_attribute("data-app")
        == "careers"
    )
    assert switcher.locator(".app-tile.disabled").count() == 0
    assert switcher.locator(".popover-title").text_content() == "Aira apps"
    shot(page, "02-switcher")

    # Marketing holds Leads: two sections, and the account group stays.
    switcher.locator(".app-tile[data-app='marketing']").click()
    page.wait_for_url(re.compile(r"#/marketing"))
    page.wait_for_selector("#primaryNav a[data-section='leads']")
    assert page.locator("#appSwitcher").is_hidden()
    assert sections(page) == ["marketing", "leads", *ACCOUNT_SECTIONS]
    assert page.locator("#brandApp").inner_text() == "Marketing"
    page.click("#primaryNav a[data-section='leads']")
    page.wait_for_url(re.compile(r"#/leads"))
    # The Leads view names its own tab in the breadcrumb: the section, then the place inside it.
    until(page, lambda: "Find" in page.locator(".crumbs").inner_text())
    assert page.locator(".crumbs").inner_text().replace("\n", " ").split() == [
        "Leads",
        "/",
        "Find",
    ]
    assert "Leads" in page.title()
    assert page.locator("#brandApp").inner_text() == "Marketing"
    assert sections(page) == ["marketing", "leads", *ACCOUNT_SECTIONS]
    shot(page, "03-leads")

    # Developers: the API console is its own app.
    open_switcher(page).locator(".app-tile[data-app='api']").click()
    page.wait_for_url(re.compile(r"#/airaapi"))
    page.wait_for_selector("#primaryNav a[data-section='airaapi']")
    assert sections(page)[0] == "airaapi"
    assert sections(page)[-len(ACCOUNT_SECTIONS) :] == ACCOUNT_SECTIONS
    api_sections = sections(page)
    assert page.locator("#brandApp").inner_text() == "Developers"
    shot(page, "04-api")

    # Settings belongs to the account: opening it keeps the app you were in.
    page.click("#primaryNav a[data-section='settings']")
    page.wait_for_url(re.compile(r"#/settings"))
    until(page, lambda: "Settings" in page.locator(".crumbs").inner_text())
    assert sections(page) == api_sections
    assert page.locator("#brandApp").inner_text() == "Developers"
    assert "/" not in page.locator(".crumbs").inner_text()
    assert not problems, problems
    context.close()


def test_deep_links_land_inside_the_right_app_and_chords_open_apps(browser, env):
    context, page, problems = signed_in_page(browser, env)
    # #/careers is the Research section of the Careers app.
    go(page, env, "careers")
    page.wait_for_selector("#primaryNav a[data-section='careers'][aria-current='page']")
    assert sections(page) == [*CAREERS_SECTIONS, *ACCOUNT_SECTIONS]
    assert labels(page)["careers"] == "Research"
    assert page.locator("#brandApp").inner_text() == "Careers"
    # A section link inside the app does not redraw the sidebar as a whole, only the current mark.
    page.click("#primaryNav a[data-section='jobs']")
    page.wait_for_url(re.compile(r"#/jobs"))
    page.wait_for_selector("#primaryNav a[data-section='jobs'][aria-current='page']")
    assert sections(page) == [*CAREERS_SECTIONS, *ACCOUNT_SECTIONS]
    until(page, lambda: "Jobs" in page.locator(".crumbs").inner_text())
    assert page.locator(".crumbs").inner_text().replace("\n", " ").split() == [
        "Careers",
        "/",
        "Jobs",
    ]
    assert page.title().startswith("Jobs · Careers ·")
    # `g` then an app's key opens that app's home; a section's chord still works from any app.
    page.keyboard.press("g")
    page.keyboard.press("l")
    page.wait_for_url(re.compile(r"#/leads"))
    page.wait_for_selector("#primaryNav a[data-section='leads']")
    assert sections(page) == ["marketing", "leads", *ACCOUNT_SECTIONS]
    page.keyboard.press("g")
    page.keyboard.press("2")
    page.wait_for_url(re.compile(r"#/marketing"))
    page.keyboard.press("g")
    page.keyboard.press("p")
    page.wait_for_url(re.compile(r"#/airaapi"))
    page.keyboard.press("g")
    page.keyboard.press("c")
    page.wait_for_url(re.compile(r"#/dashboard"))
    page.wait_for_selector("#primaryNav a[data-section='dashboard']")
    assert sections(page)[0] == "dashboard"
    page.keyboard.press("g")
    page.keyboard.press("f")
    page.wait_for_url(re.compile(r"#/careers"))
    page.keyboard.press("g")
    page.keyboard.press("j")
    page.wait_for_url(re.compile(r"#/jobs"))
    # The shortcuts help lists the app keys and the current app's sections.
    page.keyboard.press("?")
    page.wait_for_selector(".modal")
    help_text = page.locator(".modal").inner_text()
    assert (
        "Careers app" in help_text
        and "Marketing app" in help_text
        and "Developers app" in help_text
    )
    assert "Research" in help_text and "Dashboard" in help_text
    page.keyboard.press("Escape")
    assert not problems, problems
    context.close()


def test_the_switcher_works_from_the_keyboard(browser, env):
    context, page, problems = signed_in_page(browser, env)
    go(page, env, "dashboard")
    page.wait_for_selector("#primaryNav a[data-section='dashboard']")
    button = page.locator("#appSwitcherBtn")
    assert button.get_attribute("aria-haspopup") == "menu"
    assert button.get_attribute("aria-expanded") == "false"
    button.focus()
    page.keyboard.press("Enter")
    page.wait_for_selector("#appSwitcher:not([hidden])")
    assert button.get_attribute("aria-expanded") == "true"
    assert page.locator("#appSwitcher").get_attribute("role") == "menu"
    # Focus starts on Home; the arrows move through the items; Escape closes and returns focus.
    assert page.evaluate("document.activeElement.dataset.app") == "home"
    page.keyboard.press("ArrowRight")
    assert page.evaluate("document.activeElement.dataset.app") == "careers"
    page.keyboard.press("ArrowRight")
    assert page.evaluate("document.activeElement.dataset.app") == "api"
    page.keyboard.press("ArrowDown")
    assert (
        page.evaluate("document.activeElement.dataset.app") == "social"
    )  # the everyday apps follow
    page.keyboard.press("ArrowDown")
    assert page.evaluate("document.activeElement.dataset.app") == "care"
    page.keyboard.press("End")
    assert (
        page.evaluate("document.activeElement.dataset.app") == "ads"
    )  # the last business app
    page.keyboard.press("Home")
    assert page.evaluate("document.activeElement.dataset.app") == "home"
    page.keyboard.press("Escape")
    page.wait_for_selector("#appSwitcher", state="hidden")
    assert button.get_attribute("aria-expanded") == "false"
    assert page.evaluate("document.activeElement.id") == "appSwitcherBtn"
    # Enter on a tile opens the app.
    page.keyboard.press("Enter")
    page.wait_for_selector("#appSwitcher:not([hidden])")
    page.keyboard.press("ArrowRight")
    page.keyboard.press("ArrowRight")
    page.keyboard.press("Enter")
    page.wait_for_url(re.compile(r"/developers#/airaapi"))
    page.wait_for_selector("#primaryNav a[data-section='airaapi']")
    # An outside click closes an open menu.
    open_switcher(page)
    page.mouse.click(400, 500)
    page.wait_for_selector("#appSwitcher", state="hidden")
    assert not problems, problems
    context.close()


def test_the_account_menu_names_the_person_and_signs_out(browser, env, account_url):
    context, page, problems = signed_in_page(browser, env)
    go(page, env, "dashboard")
    page.wait_for_selector("#primaryNav a[data-section='dashboard']")
    assert page.locator("#accountBtnEmail").inner_text() == "leads-ui@example.com"
    assert page.locator("#accountAvatar").inner_text() == "L"
    page.click("#accountBtn")
    page.wait_for_selector("#accountMenu:not([hidden])")
    menu = page.locator("#accountMenu")
    assert menu.get_attribute("role") == "menu"
    assert menu.locator("#accountEmail").inner_text() == "leads-ui@example.com"
    manage = menu.locator("a.menu-item", has_text="Manage your account")
    assert manage.get_attribute("href") == f"{account_url}/#/org"
    assert manage.get_attribute("rel") == "noopener noreferrer"
    assert menu.locator("#themeToggle").is_visible()
    assert menu.locator("#shortcutsBtn").is_visible()
    assert menu.locator("#logoutBtn").is_visible()
    assert menu.locator("a.menu-item", has_text="Switch organization").count() == 0
    shot(page, "05-account-menu")
    # The switcher also offers the account for a local installation that knows where it lives.
    page.keyboard.press("Escape")
    switcher = open_switcher(page)
    link = switcher.locator("a.menu-item", has_text="Manage your account")
    assert link.get_attribute("href") == f"{account_url}/#/org"
    page.keyboard.press("Escape")
    # Theme toggles from the menu; sign out ends the session.
    page.click("#accountBtn")
    page.wait_for_selector("#accountMenu:not([hidden])")
    page.click("#themeToggle")
    theme = page.evaluate("document.documentElement.getAttribute('data-theme')")
    assert theme in ("dark", "light")
    assert not problems, problems
    page.click("#logoutBtn")
    page.wait_for_selector("#authScreen:not([hidden])")
    assert page.locator("#app").is_hidden()
    context.close()


def test_without_an_account_url_the_menu_has_no_account_link(browser, env):
    context, page, problems = signed_in_page(browser, env)
    go(page, env, "leads")
    page.wait_for_selector("#primaryNav a[data-section='leads']")
    page.click("#accountBtn")
    page.wait_for_selector("#accountMenu:not([hidden])")
    # No link to an account; the person's own Display and reading is the only entry.
    labels = page.locator("#accountMenu a.menu-item").all_inner_texts()
    assert labels == ["Display and reading"], labels
    assert not problems, problems
    context.close()


@pytest.mark.parametrize("dark", [False, True], ids=["light", "dark"])
def test_at_phone_width_the_switcher_lives_in_the_more_sheet(browser, env, dark):
    context, page, problems = signed_in_page(
        browser, env, width=390, height=800, dark=dark
    )
    go(page, env, "dashboard")
    page.wait_for_selector("#bottomNav a[data-section='dashboard']")
    # The bottom bar carries the current app's quick items; the top-bar switcher is hidden.
    assert page.locator("#bottomNav a[data-section]").evaluate_all(
        "links => links.map(a => a.dataset.section)"
    ) == ["dashboard", "jobs", "applications", "interviews", "more"]
    assert page.locator("#appSwitcherBtn").is_hidden()
    assert page.locator("#accountBtn").is_visible()
    assert page.locator("#accountBtnEmail").bounding_box()["width"] <= 1
    page.click("#bottomNav a[data-section='more']")
    page.wait_for_selector("#sidebar.open")
    side = page.locator("#appSwitcherSideBtn")
    assert side.is_visible()
    shot(page, f"06-phone-more-{'dark' if dark else 'light'}")
    side.click()
    page.wait_for_selector("#appSwitcher:not([hidden])")
    box = page.locator("#appSwitcher").bounding_box()
    assert box["x"] >= 0 and box["x"] + box["width"] <= 390
    shot(page, f"07-phone-switcher-{'dark' if dark else 'light'}")
    page.locator("#appSwitcher .app-tile[data-app='marketing']").click()
    page.wait_for_url(re.compile(r"#/marketing"))
    page.wait_for_selector("#bottomNav a[data-section='leads']")
    assert page.locator("#sidebar.open").count() == 0
    assert page.locator("#bottomNav a[data-section]").evaluate_all(
        "links => links.map(a => a.dataset.section)"
    ) == ["marketing", "leads", "more"]
    # Nothing overflows the viewport.
    assert page.evaluate("document.documentElement.scrollWidth") <= 390
    page.click("#accountBtn")
    page.wait_for_selector("#accountMenu:not([hidden])")
    box = page.locator("#accountMenu").bounding_box()
    assert box["x"] >= 0 and box["x"] + box["width"] <= 390
    shot(page, f"08-phone-account-{'dark' if dark else 'light'}")
    assert not problems, problems
    context.close()


def test_the_app_catalog_in_the_browser_matches_the_server_catalog() -> None:
    app_js = (
        Path(__file__).resolve().parent.parent / "auto_apply_assistant" / "app.js"
    ).read_text(encoding="utf-8")
    block = re.search(r"const APPS = \[(.*?)\n\];", app_js, re.S)
    assert block is not None
    shown = [
        dict(re.findall(r'(\w+): "([^"]*)"', line))
        for line in block.group(1).splitlines()
        if line.strip().startswith("{")
    ]
    expected = app_catalog.catalog()
    assert [item["id"] for item in shown] == [item["id"] for item in expected]
    for item, catalog in zip(shown, expected, strict=True):
        for field in ("name", "tagline", "icon", "home", "key"):
            assert item.get(field, "") == catalog[field], (item["id"], field)


def test_desktop_dark_menus_fit_and_show_no_stray_text(browser, env):
    context, page, problems = signed_in_page(browser, env, dark=True)
    go(page, env, "dashboard")
    page.wait_for_selector("#primaryNav a[data-section='dashboard']")
    assert page.locator("#appSwitcherSideBtn").is_hidden(), (
        "the sidebar switcher is for phones"
    )
    switcher = open_switcher(page)
    assert "null" not in switcher.text_content()
    box = switcher.bounding_box()
    assert box["x"] >= 0 and box["x"] + box["width"] <= 1366
    shot(page, "09-desktop-dark-switcher")
    page.keyboard.press("Escape")
    page.click("#accountBtn")
    page.wait_for_selector("#accountMenu:not([hidden])")
    assert "null" not in page.locator("#accountMenu").text_content()
    shot(page, "10-desktop-dark-account")
    assert page.evaluate("document.documentElement.scrollWidth") <= 1366
    assert not problems, problems
    context.close()
