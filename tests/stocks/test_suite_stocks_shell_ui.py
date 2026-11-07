"""Stocks in the Aira frame, in a real browser against the local server (offline market).

"+ New" offers a practice trade, a price alert, a prediction, a real trade and a stock idea, and each opens its form
(on a stock's page, for that stock); the search box across the top suggests stocks as you type, marks the ones on a
watchlist, opens the one chosen, and Enter lists every match with today's price; the left menu shows Stocks' tabs
as folders with icons and a folder opens its tab; a phone in dark mode has no sideways scroll. It fails on any
script error. Screenshots go to ``STOCKS_SHELL_SHOTS`` (default: none) for a human to look at.
"""

# ruff: noqa: F811
from __future__ import annotations

import os
import re
from pathlib import Path

import pytest

patchright = pytest.importorskip("patchright.sync_api")

from test_careers import env, stub  # noqa: E402, F401
from test_leads_ui import browser, go, signed_in_page, until  # noqa: E402, F401
from test_suite_stocks_ui import call, offline_market, real_problems  # noqa: E402, F401

SHOTS = (
    Path(os.environ["STOCKS_SHELL_SHOTS"])
    if os.environ.get("STOCKS_SHELL_SHOTS")
    else None
)
NEW_ITEMS = [
    "Buy with practice money",
    "Price alert",
    "My prediction",
    "Record a real trade",
    "Publish a stock idea",
]
FOLDERS = ["Markets", "Watchlist", "Portfolio", "Predictions", "Ideas (earn)", "Alerts"]
NO_OVERFLOW = "() => document.documentElement.scrollWidth <= window.innerWidth + 1"


def shot(page, name: str) -> None:
    if SHOTS is None:
        return
    SHOTS.mkdir(parents=True, exist_ok=True)
    page.screenshot(path=str(SHOTS / f"{name}.png"))


def open_stocks(page, env, hash_: str = "stocks") -> None:
    go(page, env, hash_)
    page.wait_for_selector("#main .page-head h1")
    page.wait_for_load_state("networkidle")


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


def heading(page) -> str:
    return page.locator("#main .page-head h1").inner_text().strip()


def suggestions(page) -> list[str]:
    return page.locator("#appSearchList [role='option']").all_inner_texts()


def type_search(page, text: str) -> None:
    page.fill("#appSearchInput", "")
    page.click("#appSearchInput")
    page.keyboard.type(text)


def test_new_offers_the_stocks_creates_and_each_opens_its_form(browser, env):
    context, page, problems = signed_in_page(browser, env, width=1440, height=900)
    open_stocks(page, env)
    assert heading(page) == "Markets"
    assert new_menu(page) == NEW_ITEMS
    assert page.locator(".shell-new-menu [role='menuitem'] svg").count() == len(
        NEW_ITEMS
    )
    shot(page, "01-new-menu")
    page.keyboard.press("Escape")
    page.wait_for_selector(".shell-new-menu", state="detached")

    # No practice portfolio yet: the form that starts one comes first, then the trade form. The address forgets
    # ?new= so Back or a reload does not ask again.
    choose_new(page, "Buy with practice money")
    page.wait_for_selector(".modal h2:has-text('Start a practice portfolio')")
    assert re.search(r"#/stocks\?tab=portfolio&kind=practice$", page.url), page.url
    assert heading(page) == "Portfolio"
    page.click(".modal button:has-text('Start')")
    page.wait_for_selector("section:has-text('Practice (USD)') .suite-kpi")
    choose_new(page, "Buy with practice money")
    page.wait_for_selector(".modal h2:has-text('Buy with practice money')")
    page.fill(".modal input[name=symbol]", "AAPL")
    page.fill(".modal input[name=quantity]", "2")
    page.click(".modal button:has-text('Buy')")
    page.wait_for_selector("section:has-text('Practice (USD)') td:has-text('AAPL')")
    page.reload()
    page.wait_for_load_state("networkidle")
    page.wait_for_selector("section:has-text('Practice (USD)') td:has-text('AAPL')")
    assert page.locator(".modal").count() == 0

    choose_new(page, "Price alert")
    page.wait_for_selector(".modal h2:has-text('New price alert')")
    assert re.search(r"#/stocks\?tab=alerts$", page.url), page.url
    assert heading(page) == "Alerts"
    close_modal(page)

    # On a stock's page "+ New" is about that stock.
    open_stocks(page, env, "stocks?symbol=MSFT")
    page.wait_for_selector("#main h1:has-text('Microsoft')")
    choose_new(page, "Price alert")
    page.wait_for_selector(".modal h2:has-text('Price alert for MSFT')")
    assert page.input_value(".modal input[name=symbol]") == "MSFT"
    close_modal(page)

    open_stocks(page, env, "stocks?symbol=MSFT")
    page.wait_for_selector("#main h1:has-text('Microsoft')")
    choose_new(page, "My prediction")
    page.wait_for_selector(".modal h2:has-text('My prediction for MSFT')")
    assert re.search(r"#/stocks\?tab=predictions&symbol=MSFT$", page.url), page.url
    shot(page, "02-new-prediction")
    page.select_option(".modal select[name=direction]", "down")
    page.click(".modal button:has-text('Save my prediction')")
    page.wait_for_selector("section:has-text('My predictions') td:has-text('MSFT')")
    assert (
        "Down"
        in page.locator(
            "section:has-text('My predictions') tbody tr"
        ).first.inner_text()
    )

    choose_new(page, "Record a real trade")
    page.wait_for_selector(".modal h2:has-text('Record a real trade')")
    assert re.search(r"#/stocks\?tab=portfolio&kind=real$", page.url), page.url
    close_modal(page)

    choose_new(page, "Publish a stock idea")
    page.wait_for_selector(".modal h2:has-text('Publish a stock idea')")
    assert re.search(r"#/stocks\?tab=ideas$", page.url), page.url
    assert heading(page) == "Ideas (earn)"
    close_modal(page)
    assert real_problems(problems) == []
    context.close()


def test_search_suggests_stocks_opens_one_and_enter_lists_every_match(browser, env):
    context, page, problems = signed_in_page(browser, env, width=1440, height=900)
    call(context, env, "POST", "/api/stocks/watchlist", {"symbol": "AAPL"})
    open_stocks(page, env)
    assert (
        page.get_attribute("#appSearchInput", "placeholder")
        == "Search stocks: Apple, AAPL, TCS.NS"
    )

    type_search(page, "apple")
    until(
        page,
        lambda: any("On a watchlist" in s for s in suggestions(page)),
        what="Apple suggested",
    )
    found = suggestions(page)
    assert "Search Stocks for “apple”" in found[0]
    assert any(
        "Apple" in s and "AAPL · United States · On a watchlist" in s for s in found
    )
    shot(page, "03-suggestions")
    page.locator("#appSearchList [role='option']", has_text="AAPL").click()
    page.wait_for_url(re.compile(r"#/stocks\?symbol=AAPL$"))
    page.wait_for_selector("#main h1:has-text('Apple')")
    assert "AAPL" in page.locator("#main .page-head p").inner_text()

    # Enter: every match, across both markets, with today's price; a row opens its stock.
    type_search(page, "bank")
    until(page, lambda: len(suggestions(page)) > 1, what="bank suggestions")
    page.keyboard.press("Enter")
    page.wait_for_url(re.compile(r"#/stocks\?q=bank$"))
    page.wait_for_selector("#main h1:has-text('Results for “bank”')")
    rows = page.locator("#main tbody tr")
    until(page, lambda: rows.count() >= 5, what="bank results")
    text = page.locator("#main tbody").inner_text()
    assert "Bank of America" in text and "HDFC Bank" in text
    assert "India (NSE)" in text and "United States" in text
    shot(page, "04-results")
    page.locator("#main tbody tr", has_text="HDFC Bank").click()
    page.wait_for_url(re.compile(r"#/stocks\?symbol=HDFCBANK\.NS$"))
    page.wait_for_selector("#main h1:has-text('HDFC Bank')")

    # Nothing matches: a clear way on.
    type_search(page, "zz qq")
    page.keyboard.press("Enter")
    page.wait_for_selector("text=No stock matches “zz qq”")
    page.click("#main button:has-text('See every stock')")
    page.wait_for_url(re.compile(r"#/stocks\?tab=markets$"))
    assert real_problems(problems) == []
    context.close()


def test_the_folders_open_their_tabs(browser, env):
    context, page, problems = signed_in_page(browser, env, width=1440, height=900)
    open_stocks(page, env)
    links = page.locator(".shell-subnav[data-section='stocks'] a.shell-sublink")
    assert [t.strip() for t in links.all_inner_texts()] == FOLDERS
    assert page.locator(
        ".shell-subnav[data-section='stocks'] a.shell-sublink svg"
    ).count() == len(FOLDERS)
    # The bar under the title would repeat the folders on a wide screen.
    assert not page.locator("#main .st-app > .tabs").is_visible()
    for label, tab in (
        ("Alerts", "alerts"),
        ("Watchlist", "watchlist"),
        ("Portfolio", "portfolio"),
    ):
        links.filter(has_text=label).click()
        page.wait_for_url(re.compile(rf"#/stocks\?tab={tab}$"))
        page.wait_for_selector(f"#main .page-head h1:has-text('{label}')")
        assert links.filter(has_text=label).get_attribute("aria-current") == "page"
    shot(page, "05-folders")
    assert real_problems(problems) == []
    context.close()


def test_a_dark_phone_fits_and_keeps_the_tab_bar(browser, env):
    phone, page2, problems2 = signed_in_page(
        browser, env, width=390, height=844, dark=True
    )
    for target, ready in (
        ("stocks", "section:has-text('Top gainers today') .st-mover"),
        ("stocks?q=tata", "#main tbody tr"),
        ("stocks?symbol=TCS.NS", "section:has-text('Daily log') tbody tr"),
        ("stocks?tab=watchlist", "text=Your watchlist is empty"),
    ):
        open_stocks(page2, env, target)
        page2.wait_for_selector(ready)
        assert page2.evaluate(NO_OVERFLOW), target
    # On a phone the menu is folded away, so the tab bar stays.
    assert page2.locator("#main .st-app > .tabs").is_visible()
    shot(page2, "06-phone-dark")
    assert real_problems(problems2) == []
    phone.close()
