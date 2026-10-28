"""Stocks in a real browser against the single-user server (offline market): the markets screen, finding a stock and
reading its daily log and chart with the keyboard, the watchlist, a practice trade, a prediction of one's own, setting
up the analyst page and a visitor asking to subscribe on a phone, and every tab fitting a phone screen."""

# ruff: noqa: F811
from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

patchright = pytest.importorskip("patchright.sync_api")

import auto_apply_assistant as local  # noqa: E402
from aira import suite  # noqa: E402
from aira.suite import kit, public  # noqa: E402
from aira.suite.stocks import market  # noqa: E402
from test_careers import env, stub  # noqa: E402, F401
from test_leads_ui import browser, go, signed_in_page, until  # noqa: E402, F401

SHOTS = Path(os.environ["STOCKS_SHOTS"]) if os.environ.get("STOCKS_SHOTS") else None
NO_OVERFLOW = "() => document.documentElement.scrollWidth <= window.innerWidth + 1"


def shot(page, name):
    if SHOTS is not None:
        SHOTS.mkdir(parents=True, exist_ok=True)
        page.screenshot(path=str(SHOTS / f"{name}.png"), full_page=True)


class _NoModel:
    def model(self, prompt):
        return None


@pytest.fixture(autouse=True)
def offline_market(monkeypatch):
    monkeypatch.setenv("AIRA_MARKET_PROVIDER", "stub")
    market.set_provider(None)
    market.stub_reset()
    kit.forget_schema()
    public.set_index(local._public_link_index)
    previous = suite.gateway()
    suite.set_gateway(_NoModel())
    yield
    suite.set_gateway(previous)


def call(context, env, method, path, body=None):
    response = context.request.fetch(
        env.base + path,
        method=method,
        data=json.dumps(body) if body is not None else None,
        headers={
            "Content-Type": "application/json",
            "X-Requested-With": "aira",
            "Origin": env.base,
        },
    )
    assert response.status < 300, (path, response.status, response.text())
    return response.json()


def real_problems(problems):
    # The page asks who is signed in before the session cookie is used; that first 401 is expected.
    return [p for p in problems if "401" not in p]


def test_markets_a_stocks_log_practice_trading_and_a_prediction(browser, env):
    context, page, problems = signed_in_page(browser, env)
    go(page, env, "stocks")
    page.wait_for_selector("#main h1:has-text('Markets')")
    page.wait_for_selector("section:has-text('Top gainers today') .st-mover")
    page.wait_for_selector("text=Practice data (offline market)")
    assert page.locator("section:has-text('Every stock') tbody tr").count() >= 30
    page.click(".st-ranges button:has-text('India (NSE)')")
    page.wait_for_selector(".st-card:has-text('NIFTY 50')")
    shot(page, "markets")

    # find a stock by its name with the search across the top and read its daily log
    page.fill("#appSearchInput", "infosys")
    page.wait_for_selector("#appSearchList [role='option']:has-text('INFY.NS')")
    page.click("#appSearchList [role='option']:has-text('INFY.NS')")
    page.wait_for_selector("#main h1:has-text('Infosys')")
    page.wait_for_selector("section:has-text('Daily log') tbody tr")
    assert page.locator("section:has-text('Daily log') tbody tr").count() == 30
    page.click("button:has-text('Show 30 more days')")
    until(
        page,
        lambda: page.locator("section:has-text('Daily log') tbody tr").count() == 60,
        what="more days",
    )
    page.click("button[aria-label='Show 3M']")
    page.wait_for_selector("text=The 3M in numbers")
    assert page.locator("h3:has-text('Best days (top gains)')").count() == 1
    assert (
        "▲" in page.locator("section:has-text('Best days') tbody tr").first.inner_text()
    )
    # the chart reads out a day with the keyboard
    page.focus(".st-chart")
    before = page.locator(".st-readout").inner_text()
    page.keyboard.press("ArrowLeft")
    until(
        page,
        lambda: page.locator(".st-readout").inner_text() != before,
        what="the chart readout",
    )
    assert "₹" in page.locator(".st-readout").inner_text()
    page.check("label:has-text('200-day average') input")
    page.wait_for_selector(".st-legend:has-text('200-day average')")
    page.click("button:has-text('Add to watchlist')")
    page.wait_for_selector(".st-hero .suite-pill:has-text('On My watchlist')")
    shot(page, "stock")

    go(page, env, "stocks?tab=watchlist")
    page.wait_for_selector("section:has-text('My watchlist') td:has-text('INFY')")

    # practice: start, buy through the form, see the holding and the league
    go(page, env, "stocks?tab=portfolio")
    page.click("button:has-text('Start with 100,000 US dollars')")
    page.click(".modal button:has-text('Start')")
    page.wait_for_selector(
        "section:has-text('Practice (USD)') .suite-kpi:has-text('Worth now')"
    )
    page.click("section:has-text('Practice (USD)') button:has-text('Buy')")
    page.fill(".modal input[name=symbol]", "AAPL")
    page.fill(".modal input[name=quantity]", "5")
    page.click(".modal button:has-text('Buy')")
    page.wait_for_selector("section:has-text('Practice (USD)') td:has-text('AAPL')")
    page.wait_for_selector("section:has-text('Practice league') td:has-text('(you)')")
    held = call(context, env, "GET", "/api/stocks/portfolio")["accounts"][0]["holdings"]
    assert held[0]["symbol"] == "AAPL" and held[0]["quantity"] == 5
    # buy by an amount of money instead of a number of shares
    page.click("section:has-text('Practice (USD)') button:has-text('Buy')")
    page.fill(".modal input[name=symbol]", "MSFT")
    page.fill(".modal input[name=amount]", "1000")
    page.click(".modal button:has-text('Buy')")
    page.wait_for_selector("section:has-text('Practice (USD)') td:has-text('MSFT')")
    msft = next(
        h
        for h in call(context, env, "GET", "/api/stocks/portfolio")["accounts"][0][
            "holdings"
        ]
        if h["symbol"] == "MSFT"
    )
    assert 0 < msft["value_cents"] <= 1000_00

    # a real holding bought long ago: the form asks for the date instead of assuming today
    go(page, env, "stocks?tab=portfolio&kind=real")
    page.click("#main button:has-text('Record a real trade')")
    assert page.locator(".modal input[name=trade_date]").input_value() == ""
    page.fill(".modal input[name=symbol]", "MSFT")
    page.fill(".modal input[name=quantity]", "3")
    page.fill(".modal input[name=price]", "3OO")  # a typo: letters O for zeros
    page.click(".modal button:has-text('Record')")
    page.wait_for_selector(".modal .form-error:has-text('Enter date of the trade')")
    page.fill(".modal input[name=trade_date]", kit.today())
    page.click(".modal button:has-text('Record')")
    page.wait_for_selector(".modal .form-error:has-text('must be a number')")
    page.fill(".modal input[name=price]", "300")
    page.click(".modal button:has-text('Record')")
    page.wait_for_selector("#main td:has-text('MSFT')")
    real = call(context, env, "GET", "/api/stocks/portfolio?kind=real")["accounts"][0]
    assert abs(real["day_change_pct"]) < 15, real[
        "day_change_pct"
    ]  # not the +112% gain
    shot(page, "real")
    shot(page, "practice")

    # a prediction with its track record, and one of my own
    go(page, env, "stocks?tab=predictions&symbol=AAPL")
    page.wait_for_selector("text=Signal:")
    page.wait_for_selector(
        "section:has-text('Track record') td:has-text('No change (baseline)')"
    )
    assert (
        "not financial advice"
        in page.locator(".st-disclaimer").first.inner_text().lower()
    )
    page.click("button:has-text('I think AAPL goes up')")
    page.click(".modal button:has-text('Save my prediction')")
    page.wait_for_selector("section:has-text('My predictions') td:has-text('AAPL')")
    shot(page, "predictions")
    # the one 400 is the price typed with letters, refused on purpose
    assert [p for p in real_problems(problems) if "status of 400" not in p] == []
    assert sum("status of 400" in p for p in problems) == 1


def test_the_analyst_page_and_a_visitor_on_a_phone(browser, env):
    context, page, problems = signed_in_page(browser, env)
    go(page, env, "stocks?tab=ideas")
    page.wait_for_selector("text=Earn from your stock ideas")
    page.click("section:has-text('Your analyst page') button:has-text('Set up')")
    page.fill(".modal input[name=display_name]", "Asha Picks")
    page.fill(".modal input[name=price]", "abc")
    page.fill(".modal input[name=payment_link]", "https://pay.example/asha")
    page.click(".modal button:has-text('Save')")
    page.wait_for_selector(".modal .form-error:has-text('must be a number')")
    page.fill(".modal input[name=price]", "9.99")
    page.click(".modal button:has-text('Save')")
    page.wait_for_selector("text=$9.99 a month")
    price = call(context, env, "GET", "/api/stocks/stock?symbol=MSFT&range=1m")[
        "quote"
    ]["price_cents"]
    page.click("#main button:has-text('Publish an idea')")
    page.fill(".modal input[name=symbol]", "MSFT")
    page.fill(".modal input[name=target]", f"{price * 1.1 / 100:.2f}")
    page.fill(".modal input[name=stop]", f"{price * 0.95 / 100:.2f}")
    page.fill(".modal textarea[name=reasoning]", "Cloud growth")
    page.click(".modal button:has-text('Publish')")
    page.wait_for_selector("section:has-text('Your ideas') td:has-text('MSFT')")
    # the reasoning can be edited, and an idea deleted in its first hour
    page.click("section:has-text('Your ideas') button:has-text('Edit why')")
    page.fill(".modal textarea[name=reasoning]", "Cloud growth and margins")
    page.click(".modal button:has-text('Save')")
    until(
        page,
        lambda: (
            call(context, env, "GET", "/api/stocks/ideas")["ideas"][0]["reasoning"]
            == "Cloud growth and margins"
        ),
        what="the edited reasoning",
    )
    page.click("#main button:has-text('Publish an idea')")
    page.fill(".modal input[name=symbol]", "AAPL")
    aapl = call(context, env, "GET", "/api/stocks/stock?symbol=AAPL&range=1m")["quote"][
        "price_cents"
    ]
    page.fill(".modal input[name=target]", f"{aapl * 1.1 / 100:.2f}")
    page.fill(".modal input[name=stop]", f"{aapl * 0.95 / 100:.2f}")
    page.click(".modal button:has-text('Publish')")
    page.wait_for_selector("section:has-text('Your ideas') td:has-text('AAPL')")
    page.click(
        "section:has-text('Your ideas') tr:has-text('AAPL') button:has-text('Delete')"
    )
    page.click(".modal button:has-text('Delete idea')")
    until(
        page,
        lambda: (
            page.locator("section:has-text('Your ideas') td:has-text('AAPL')").count()
            == 0
        ),
        what="the deleted idea",
    )
    page.click("section:has-text('Your analyst page') button:has-text('Create link')")
    page.wait_for_selector(".suite-share-url")
    url = page.locator(".suite-share-url").inner_text()
    shot(page, "ideas")

    visitor = browser.new_context(viewport={"width": 390, "height": 844})
    phone = visitor.new_page()
    phone_problems: list[str] = []
    phone.on("pageerror", lambda e: phone_problems.append(str(e)))
    phone.goto(url)
    phone.wait_for_selector("h1:has-text('Asha Picks: stock ideas')")
    phone.wait_for_selector("td:has-text('Buy MSFT')")
    # a mistake shows next to the form, and goes away once it works
    phone.click("button:has-text('Ask to subscribe')")
    form = "form:has-text('Ask to subscribe')"
    phone.wait_for_selector(f"{form} .pub-notice.bad:has-text('Enter your name')")
    assert phone.locator(f"{form} .pub-notice.bad").is_visible()
    phone.fill("input[autocomplete=name]", "Ravi")
    phone.fill("input[autocomplete=email]", "ravi@example.com")
    phone.click("button:has-text('Ask to subscribe')")
    phone.wait_for_selector("text=Your payment reference")
    assert phone.locator(".pub-notice.bad").count() == 0
    reference = phone.locator("p.pub-code")
    assert reference.evaluate("(node) => node.scrollWidth <= node.clientWidth + 1")
    assert reference.bounding_box()["height"] < 70  # one line
    shot(phone, "public")
    phone.fill(
        "form:has-text('I am a subscriber') input[type=email]", "ravi@example.com"
    )
    phone.fill("input[autocomplete=one-time-code]", "AAAA-BBBB-CCCC")
    phone.click("button:has-text('Show my paid ideas')")
    wrong = "form:has-text('I am a subscriber') .pub-notice.bad"
    phone.wait_for_selector(f"{wrong}:has-text('do not match')")
    box = phone.locator(wrong).bounding_box()
    assert box and 0 <= box["y"] <= 844 - box["height"]  # in view, next to the form
    shot(phone, "public-wrong-code")
    assert (
        phone.locator("a.pub-btn:has-text('Pay $9.99')").get_attribute("href")
        == "https://pay.example/asha"
    )
    assert phone.evaluate(NO_OVERFLOW)
    visitor.close()
    assert phone_problems == []

    page.reload()  # the same address: load the page again to see the new subscriber
    page.wait_for_selector("section:has-text('Subscribers') td:has-text('Ravi')")
    page.click("section:has-text('Subscribers') button:has-text('Payment received')")
    page.click(".modal button:has-text('Confirm payment')")
    page.wait_for_selector(".st-code")
    assert len(page.locator(".st-code").inner_text()) == 14
    # the one 400 is the monthly price "abc", refused on purpose
    assert [p for p in real_problems(problems) if "status of 400" not in p] == []
    assert sum("status of 400" in p for p in problems) == 1


def test_every_tab_fits_a_phone_in_easy_mode(browser, env):
    context, page, problems = signed_in_page(browser, env, width=390, height=844)
    call(context, env, "POST", "/api/stocks/practice/start", {"currency": "INR"})
    call(
        context,
        env,
        "POST",
        "/api/stocks/practice/trade",
        {"symbol": "TCS.NS", "side": "buy", "quantity": 3},
    )
    call(context, env, "POST", "/api/stocks/watchlist", {"symbol": "AAPL"})
    call(
        context,
        env,
        "POST",
        "/api/stocks/alerts",
        {"symbol": "AAPL", "kind": "move", "move_pct": 40},
    )
    call(
        context,
        env,
        "POST",
        "/api/stocks/real/transactions",
        {
            "symbol": "AAPL",
            "kind": "buy",
            "trade_date": "2026-01-05",
            "quantity": 2,
            "price_cents": 150_00,
        },
    )
    for target, ready in (
        ("stocks", "section:has-text('Top losers today')"),
        ("stocks?symbol=AAPL", "section:has-text('Daily log') tbody tr"),
        ("stocks?tab=watchlist", "td:has-text('AAPL')"),
        ("stocks?tab=portfolio", "td:has-text('TCS')"),
        ("stocks?tab=portfolio&kind=real", "td:has-text('AAPL')"),
        ("stocks?tab=predictions&symbol=TCS.NS", "text=Signal:"),
        ("stocks?tab=ideas", "text=Earn from your stock ideas"),
        ("stocks?tab=alerts", "td:has-text('moves 40%')"),
    ):
        go(page, env, target)
        page.evaluate("() => document.body.classList.add('easy', 'text-xlarge')")
        page.wait_for_selector(ready)
        page.wait_for_timeout(150)
        assert page.evaluate(NO_OVERFLOW), target
    shot(page, "phone-alerts")
    assert real_problems(problems) == []
