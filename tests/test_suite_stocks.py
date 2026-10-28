"""Stocks (aira/suite/stocks): indicators and forecasts, the walk-forward track record, the daily log, practice and
real portfolios (profit and loss with fees and partial sells), watchlists and alerts, ideas that track themselves,
the analyst page with paid subscriptions, the StocksAgent, Today and pulse; on its own, through the single-user
server for a household, and in the hosted build. Every test uses the offline market (no network)."""

from __future__ import annotations

import json
import math
import sqlite3
import urllib.error
import urllib.request
from datetime import date, timedelta

import pytest
from cloud_support import Browser, build_org, sign_in
from life_support import Household

import auto_apply_assistant as local
import test_careers
from aira import suite
from aira.suite import kit, life, public
from aira.suite.stocks import market, predict, service, store, stocks_routes  # noqa: F401 - registers routes

env = test_careers.env
api = test_careers.api
stub = test_careers.stub


class _NoModel:
    def model(self, prompt):
        return None


class _Model:
    """A writing model that answers with what the test tells it, and remembers the prompts."""

    def __init__(self, answer):
        self.answer = answer
        self.prompts = []

    def model(self, prompt):
        self.prompts.append(prompt)
        return json.dumps(self.answer)


@pytest.fixture(autouse=True)
def _offline(monkeypatch):
    monkeypatch.setenv("AIRA_MARKET_PROVIDER", "stub")
    monkeypatch.setattr(
        market, "QUOTE_TTL", 0
    )  # a test that moves a price sees it at once
    market.set_provider(None)
    market.stub_reset()
    previous = suite.gateway()
    suite.set_gateway(_NoModel())
    yield
    suite.set_gateway(previous)
    market.stub_reset()


class Req:
    def __init__(self, header: str = "") -> None:
        self._header = header
        self.user = {"email": "pat@home.example"}

    def header(self, name: str, default: str = "") -> str:
        return self._header if name == "X-Aira-Profile" else default


@pytest.fixture
def db(tmp_path):
    kit.forget_schema()
    public.set_index(public._MemoryIndex())
    connection = sqlite3.connect(tmp_path / "stocks.sqlite3")
    connection.row_factory = sqlite3.Row
    store.ensure(connection)
    yield connection
    connection.close()


@pytest.fixture
def people(db):
    pat = life.current(Req(), db)
    sam = life.add(db, pat, {"name": "Sam", "age_group": "adult"})
    mia = life.add(db, pat, {"name": "Mia", "age_group": "child"})
    return {"pat": pat, "sam": sam, "mia": mia}


def _price(db, symbol):
    return market.quotes(db, [symbol])[symbol]["price_cents"]


# ---------------------------------------------------------------------------
# indicators and forecasts (known values)
# ---------------------------------------------------------------------------


def test_moving_averages_rsi_macd_bollinger_and_volatility_match_known_values():
    values = [float(v) for v in range(1, 11)]
    assert (
        predict.sma(values, 3)[:3] == [None, None, 2.0]
        and predict.sma(values, 3)[-1] == 9.0
    )
    ema = predict.ema(values, 3)
    assert ema[:2] == [None, None] and ema[2] == 2.0 and ema[-1] == pytest.approx(9.0)
    # Wilder's RSI on the StockCharts example series (14 days): 70.53, then 66.32, 66.55, 69.41, 66.36, 57.97
    closes = [
        44.34,
        44.09,
        44.15,
        43.61,
        44.33,
        44.83,
        45.10,
        45.42,
        45.84,
        46.08,
        45.89,
        46.03,
        45.61,
        46.28,
        46.28,
        46.00,
        46.03,
        46.41,
        46.22,
        45.64,
    ]
    rsi = predict.rsi(closes, 14)
    assert rsi[13] is None
    assert [round(v, 1) for v in rsi[14:]] == pytest.approx(
        [70.5, 66.3, 66.6, 69.4, 66.4, 58.0], abs=0.15
    )
    assert predict.rsi([float(v) for v in range(30)])[-1] == 100.0
    flat = [50.0] * 60
    assert predict.latest(predict.macd(flat)["macd"]) == 0.0
    bands = predict.bollinger(flat)
    assert predict.latest(bands["upper"]) == predict.latest(bands["lower"]) == 50.0
    assert predict.volatility(flat) == 0.0
    # sd of [1, 2, 3, 4, 5] around 3 (population) is sqrt(2): the bands are 3 +- 2*sqrt(2)
    five = predict.bollinger([1.0, 2.0, 3.0, 4.0, 5.0], 5, 2)
    assert five["upper"][-1] == pytest.approx(3 + 2 * math.sqrt(2))
    # a steady 1% a day: the trend line carries it on exactly, smoothing of a flat line stays flat
    growth = [100 * 1.01**i for i in range(80)]
    assert predict.trend_forecast(growth, 5) == pytest.approx(100 * 1.01**84, rel=1e-9)
    assert predict.holt_forecast(flat, 10) == pytest.approx(50.0)
    assert predict.daily_volatility(growth) == pytest.approx(0.0, abs=1e-12)


def test_the_signal_reads_a_strong_uptrend_as_bullish_and_explains_why():
    rising = [100 * 1.004**i + (i % 3) * 0.2 for i in range(260)]
    falling = [100 * 0.996**i + (i % 3) * 0.2 for i in range(260)]
    up = predict.signal(rising, predict.forecast(rising, 20))
    down = predict.signal(falling, predict.forecast(falling, 20))
    assert up["label"] == "bullish" and up["score"] >= 2
    # a steady fall is a downtrend on every average, though RSI reads it as oversold (a reason to wait)
    assert down["label"] != "bullish" and down["score"] < 0
    assert any("200-day" in r["text"] for r in up["reasons"])
    assert any("oversold" in r["text"] and r["points"] == 1 for r in down["reasons"])
    with pytest.raises(ValueError):
        predict.forecast(rising[:10], 5)


def test_forecasts_and_the_walk_forward_backtest_are_deterministic_on_the_offline_market(
    db,
):
    first = service.prediction(db, "AAPL")
    again = service.prediction(db, "AAPL")
    assert (
        first["forecasts"] == again["forecasts"]
        and first["backtest"] == again["backtest"]
    )
    f20 = first["forecasts"]["20"]
    assert f20["low"] < f20["models"]["ensemble"]["price"] < f20["high"]
    assert set(f20["models"]) == {"trend", "smoothing", "ensemble"}
    for horizon in ("5", "20"):
        record = first["backtest"][horizon]
        assert record["tests"] >= 40
        for model in ("trend", "smoothing", "ensemble"):
            assert 0 <= record["models"][model]["direction_hit_pct"] <= 100
            assert record["models"][model]["mape_pct"] > 0
        assert (
            record["models"]["naive"]["direction_hit_pct"] is None
            and record["models"]["naive"]["mape_pct"] > 0
        )
    assert len(first["path"]) == 20 and first["signal"]["label"] in (
        "bullish",
        "neutral",
        "bearish",
    )
    assert (
        "not financial advice" in first["explanation"]["text"]
        and first["explanation"]["source"] == "template"
    )
    assert "not financial advice" in first["disclaimer"]
    # the backtest only ever looks at the past: changing tomorrow's data cannot change an old test
    closes = [b["close"] for b in market.series(db, "AAPL")["bars"]]
    early = predict.backtest(closes[:-30], 5)
    assert early["models"]["ensemble"]["tests"] == early["tests"]


def test_the_explanation_comes_from_the_writing_model_with_the_disclaimer_kept(db):
    suite.set_gateway(_Model({"text": "Apple has been rising steadily."}))
    result = service.prediction(db, "AAPL", explain=True)
    assert result["explanation"]["source"] == "model"
    assert (
        result["explanation"]["text"].startswith("Apple has been rising")
        and "not financial advice" in result["explanation"]["text"]
    )


# ---------------------------------------------------------------------------
# the market and each stock's daily log
# ---------------------------------------------------------------------------


def test_the_offline_market_is_the_same_every_time_and_covers_the_us_and_india(db):
    stub_market = market.StubProvider()
    one = stub_market.fetch("RELIANCE.NS", full=True)
    two = stub_market.fetch("RELIANCE.NS", full=True)
    assert one == two and one["currency"] == "INR" and len(one["bars"]) > 400
    assert stub_market.fetch("AAPL", full=False)["currency"] == "USD"
    with pytest.raises(LookupError):
        stub_market.fetch("NOPE", full=True)
    assert len(market.universe("us")) >= 30 and len(market.universe("in")) >= 30
    assert all(s.endswith(".NS") for s in market.universe("in"))
    assert market.provider().name == "stub"
    with pytest.raises(ValueError):
        market.clean_symbol("DROP TABLE")
    assert market.clean_symbol(" tcs.ns ") == "TCS.NS"


def test_the_yahoo_chart_answer_is_parsed_into_daily_bars():
    start = 1792416600  # 2026-10-19 13:30 UTC, a Monday's open
    stamps = [start + 86400 * i for i in range(5)]
    result = {
        "meta": {
            "currency": "USD",
            "regularMarketPrice": 105.0,
            "fullExchangeName": "NasdaqGS",
            "longName": "Demo Inc.",
            "regularMarketTime": stamps[-1] + 3600,
            "gmtoffset": -14400,
        },
        "timestamp": stamps,
        "indicators": {
            "quote": [
                {
                    "open": [100, 101, None, 103, 104],
                    "high": [101, 102, None, 104, 106],
                    "low": [99, 100, None, 102, 103],
                    "close": [100.5, 101.5, None, 103.5, 104.0],
                    "volume": [10, 20, None, 40, 50],
                }
            ]
        },
    }
    parsed = market.parse_chart("DEMO", result)
    assert [b["date"] for b in parsed["bars"]] == [
        "2026-10-19",
        "2026-10-20",
        "2026-10-22",
        "2026-10-23",
    ]
    assert parsed["price"] == 105.0 and parsed["previous_close"] == 103.5
    assert parsed["bars"][-1]["close"] == 105.0 and parsed["bars"][-1]["high"] == 106
    assert parsed["name"] == "Demo Inc." and parsed["exchange"] == "NasdaqGS"
    pence = market.parse_chart(
        "VOD.L", {**result, "meta": {**result["meta"], "currency": "GBp"}}
    )
    assert pence["currency"] == "GBP" and pence["price"] == pytest.approx(1.05)


def test_offline_answers_saved_prices_or_a_clear_message(db, monkeypatch):
    service.stock(db, people_owner(db), "MSFT")

    class Offline:
        name = "offline"

        def fetch(self, symbol, *, full):
            raise market.MarketUnavailable("Market data cannot be reached right now.")

    market.set_provider(Offline())
    try:
        saved = market.series(db, "MSFT")
        assert saved["stale"] is True and saved["bars"]
        with pytest.raises(market.MarketUnavailable):
            market.series(db, "NVDA")
    finally:
        market.set_provider(None)


def people_owner(db):
    return life.current(Req(), db)


def test_the_markets_screen_ranks_gainers_losers_volume_and_52_week_extremes(db):
    market.stub_set("AAPL", price=1000, previous_close=500)  # +100%: the top gainer
    market.stub_set(
        "MSFT", price=10, previous_close=500
    )  # -98%: the top loser, at a 52-week low
    data = service.markets(db, "us")
    assert (
        data["gainers"][0]["symbol"] == "AAPL" and data["losers"][0]["symbol"] == "MSFT"
    )
    assert all(r["change_pct"] > 0 for r in data["gainers"]) and all(
        r["change_pct"] < 0 for r in data["losers"]
    )
    assert data["highs"][0]["symbol"] == "AAPL" and any(
        r["symbol"] == "MSFT" for r in data["lows"]
    )
    volumes = [r["volume"] for r in data["active"]]
    assert volumes == sorted(volumes, reverse=True)
    assert [i["symbol"] for i in data["indices"]] == ["^GSPC", "^IXIC", "^DJI"]
    assert data["breadth"]["up"] + data["breadth"]["down"] + data["breadth"][
        "flat"
    ] == len(data["stocks"])
    india = service.markets(db, "in")
    assert india["currency"] == "INR" and [i["symbol"] for i in india["indices"]][
        :2
    ] == ["^NSEI", "^BSESN"]
    with pytest.raises(ValueError):
        service.markets(db, "mars")
    assert service.search(db, "infosys")["results"][0]["symbol"] == "INFY.NS"
    assert service.search(db, "IBM")["results"][0] == {
        "symbol": "IBM",
        "name": "IBM",
        "market": "us",
        "index": False,
        "found": True,
    }
    assert service.search(db, "zzzz")["results"] == []


def test_each_stock_has_a_daily_log_with_best_and_worst_days_and_a_csv(db, people):
    data = service.stock(db, people["pat"], "AAPL", "3m")
    log = data["log"]
    assert len(log) == 63 and log[0]["date"] > log[-1]["date"]
    for newer, older in zip(log, log[1:]):
        assert newer["change_cents"] == newer["close_cents"] - older["close_cents"]
    s = data["summary"]
    assert s["best"][0]["change_pct"] == max(r["change_pct"] for r in log)
    assert s["worst"][0]["change_pct"] == min(r["change_pct"] for r in log)
    assert s["high"]["price_cents"] == max(r["high_cents"] for r in log)
    assert s["low"]["price_cents"] == min(r["low_cents"] for r in log)
    assert len(data["chart"]["close"]) == 63 and len(data["chart"]["sma50"]) == 63
    assert (
        data["chart"]["sma200"][-1] is not None
    )  # averaged over the whole history, not just the range
    body, name = service.log_csv(db, "M&M.NS", "1m")
    lines = body.decode().splitlines()
    assert name == "M_M.NS-daily-log.csv" and lines[0].startswith(
        "date,open,high,low,close,change"
    )
    assert len(lines) == 22 and lines[1].endswith(",INR")
    with pytest.raises(ValueError):
        service.stock(db, people["pat"], "AAPL", "10y")
    with pytest.raises(LookupError):
        service.stock(db, people["pat"], "NOSUCH")


# ---------------------------------------------------------------------------
# portfolios: the profit and loss maths
# ---------------------------------------------------------------------------


def _txn(kind, qty, price, fees=0, day="2026-01-05", amount=0, n=0):
    return {
        "id": f"t{n:03d}",
        "symbol": "DEMO",
        "kind": kind,
        "trade_date": day,
        "created_at": f"2026-01-01T00:00:{n:02d}Z",
        "quantity": qty,
        "price_cents": price,
        "fees_cents": fees,
        "amount_cents": amount,
    }


def test_average_cost_profit_and_loss_with_fees_partial_sells_and_dividends():
    held, log = service.positions(
        [
            _txn("buy", 10, 100_00, 5_00, "2026-01-05", n=1),
            _txn("buy", 10, 120_00, 5_00, "2026-01-06", n=2),
            _txn("sell", 5, 130_00, 2_00, "2026-01-07", n=3),
            _txn("dividend", 0, 0, 0, "2026-01-08", amount=3_00, n=4),
        ]
    )
    p = held["DEMO"]
    # cost 1,000 + 5 + 1,200 + 5 = 2,210 for 20 shares: 110.50 each; selling 5 removes 552.50
    assert p["quantity"] == 15 and p["cost"] == pytest.approx(1657_50)
    assert p["realised"] == pytest.approx(650_00 - 2_00 - 552_50)  # 95.50
    assert p["dividends"] == 3_00 and p["fees"] == 12_00
    assert [t["realised_cents"] for t in log] == [None, None, 95_50, None]
    # selling everything closes the position; selling more than held is refused with the date
    closed, _ = service.positions(
        [_txn("buy", 2, 50_00, n=1), _txn("sell", 2, 40_00, 1_00, "2026-01-09", n=2)]
    )
    assert closed["DEMO"]["quantity"] == 0 and closed["DEMO"][
        "realised"
    ] == pytest.approx(-21_00)
    with pytest.raises(ValueError, match="only 2 were held"):
        service.positions(
            [_txn("buy", 2, 50_00, n=1), _txn("sell", 3, 60_00, day="2026-01-09", n=2)]
        )
    # order is by trade date, not by when it was typed in
    with pytest.raises(ValueError):
        service.positions(
            [
                _txn("buy", 2, 50_00, day="2026-02-01", n=1),
                _txn("sell", 1, 60_00, day="2026-01-15", n=2),
            ]
        )


def test_real_holdings_show_value_unrealised_realised_day_change_and_who_earned_most(
    db, people
):
    pat = people["pat"]
    market.stub_set("AAPL", price=150, previous_close=140)
    market.stub_set("TSLA", price=80, previous_close=90)
    service.add_real(
        db,
        pat,
        {
            "symbol": "AAPL",
            "kind": "buy",
            "trade_date": "2026-01-05",
            "quantity": "10",
            "price": "100",
            "fees": "5",
        },
    )
    service.add_real(
        db,
        pat,
        {
            "symbol": "AAPL",
            "kind": "sell",
            "trade_date": "2026-02-05",
            "quantity": "4",
            "price": "130",
            "fees": "1",
        },
    )
    service.add_real(
        db,
        pat,
        {
            "symbol": "AAPL",
            "kind": "dividend",
            "trade_date": "2026-03-01",
            "amount": "2.40",
        },
    )
    service.add_real(
        db,
        pat,
        {
            "symbol": "TSLA",
            "kind": "buy",
            "trade_date": "2026-01-10",
            "quantity": "1.5",
            "price": "100",
        },
    )
    with pytest.raises(ValueError):
        service.add_real(
            db,
            pat,
            {
                "symbol": "AAPL",
                "kind": "sell",
                "trade_date": "2026-01-04",
                "quantity": "1",
                "price": "90",
            },
        )
    with pytest.raises(ValueError):
        service.add_real(
            db,
            pat,
            {
                "symbol": "AAPL",
                "kind": "buy",
                "trade_date": (service.today() + timedelta(days=3)).isoformat(),
                "quantity": "1",
                "price": "90",
            },
        )
    data = service.portfolio(db, pat, "real")
    account = data["accounts"][0]
    assert account["currency"] == "USD" and account["cash_cents"] is None
    aapl = next(h for h in account["holdings"] if h["symbol"] == "AAPL")
    # 10 shares cost 1,005 (100.50 each); 4 sold at 130 less 1 fee: realised 520 - 1 - 402 = 117
    assert (
        aapl["quantity"] == 6
        and aapl["cost_cents"] == 603_00
        and aapl["value_cents"] == 900_00
    )
    assert (
        aapl["unrealised_cents"] == 297_00
        and aapl["realised_cents"] == 117_00
        and aapl["dividends_cents"] == 2_40
    )
    assert aapl["day_change_cents"] == 60_00
    tsla = next(h for h in account["holdings"] if h["symbol"] == "TSLA")
    assert (
        tsla["value_cents"] == 120_00
        and tsla["unrealised_cents"] == -30_00
        and tsla["day_change_cents"] == -15_00
    )
    assert account["value_cents"] == 1020_00 and account["invested_cents"] == 753_00
    assert account["day_change_cents"] == 45_00 and account["realised_cents"] == 117_00
    assert account["total_pl_cents"] == 297_00 - 30_00 + 117_00 + 2_40
    assert (
        account["best"][0]["symbol"] == "AAPL"
        and account["worst"][0]["symbol"] == "TSLA"
    )
    assert sum(h["allocation_pct"] for h in account["holdings"]) == pytest.approx(
        100, abs=0.2
    )
    # a change that would leave a sell without shares is refused; deleting the first buy too
    sell = next(t for t in account["transactions"] if t["kind"] == "sell")
    buy = next(
        t
        for t in account["transactions"]
        if t["kind"] == "buy" and t["symbol"] == "AAPL"
    )
    with pytest.raises(ValueError):
        service.update_real(db, pat, buy["id"], {"quantity": "3"})
    with pytest.raises(ValueError):
        service.delete_real(db, pat, buy["id"])
    service.update_real(db, pat, sell["id"], {"price": "140"})
    assert (
        next(
            h
            for h in service.portfolio(db, pat, "real")["accounts"][0]["holdings"]
            if h["symbol"] == "AAPL"
        )["realised_cents"]
        == 157_00
    )
    csv = service.transactions_csv(db, pat, "real").decode()
    assert "dividend,AAPL" in csv and "157.00" in csv
    # the household's other adult sees none of it, the child may not record real money at all
    assert service.portfolio(db, people["sam"], "real")["accounts"] == []
    with pytest.raises(PermissionError):
        service.add_real(
            db,
            people["mia"],
            {
                "symbol": "AAPL",
                "kind": "buy",
                "trade_date": "2026-01-05",
                "quantity": "1",
                "price": "1",
            },
        )
    with pytest.raises(LookupError):
        service.delete_real(db, people["sam"], buy["id"])


def test_practice_trades_fill_at_the_price_with_virtual_cash_in_the_stocks_currency(
    db, people
):
    mia = people["mia"]  # children can practise
    market.stub_set("AAPL", price=200, previous_close=200)
    usd = service.start_practice(db, mia, {"currency": "USD"})
    assert usd["cash_cents"] == 100_000_00
    with pytest.raises(ValueError):
        service.start_practice(db, mia, {"currency": "USD"})
    with pytest.raises(ValueError, match="Not enough practice cash"):
        service.practice_trade(
            db, mia, {"symbol": "AAPL", "side": "buy", "quantity": "501"}
        )
    bought = service.practice_trade(
        db, mia, {"symbol": "AAPL", "side": "buy", "quantity": "100"}
    )
    assert (
        bought["account"]["cash_cents"] == 80_000_00
        and bought["trade"]["price_cents"] == 200_00
    )
    with pytest.raises(ValueError, match="cannot sell"):
        service.practice_trade(
            db, mia, {"symbol": "AAPL", "side": "sell", "quantity": "101"}
        )
    with pytest.raises(ValueError, match="INR"):
        service.practice_trade(
            db, mia, {"symbol": "TCS.NS", "side": "buy", "quantity": "1"}
        )
    with pytest.raises(ValueError):
        service.practice_trade(
            db, mia, {"symbol": "^GSPC", "side": "buy", "quantity": "1"}
        )
    market.stub_set("AAPL", price=250)
    sold = service.practice_trade(
        db, mia, {"symbol": "AAPL", "side": "sell", "quantity": "40"}
    )
    account = sold["account"]
    assert account["cash_cents"] == 90_000_00 and account["realised_cents"] == 2_000_00
    assert (
        account["equity_cents"] == 90_000_00 + 60 * 250_00
        and account["return_pct"] == 5.0
    )
    assert (
        account["day_change_cents"] == 60 * 50_00
    )  # bought today at 200: today's change is from the price paid
    # buying and selling within the same second keeps their order
    for _ in range(5):
        service.practice_trade(
            db, mia, {"symbol": "TSLA", "side": "buy", "quantity": "1"}
        )
        service.practice_trade(
            db, mia, {"symbol": "TSLA", "side": "sell", "quantity": "1"}
        )
    by_amount = service.practice_trade(
        db, mia, {"symbol": "AAPL", "side": "buy", "amount": "1000"}
    )
    assert by_amount["trade"]["quantity"] == 4.0
    inr = service.start_practice(db, mia, {"currency": "INR"})
    assert inr["cash_cents"] == 10_00_000_00
    with pytest.raises(ValueError):
        service.start_practice(db, mia, {"currency": "EUR", "cash": "5"})
    reset = service.reset_practice(db, mia, usd["id"], {"cash": "5000"})
    assert reset["cash_cents"] == 5_000_00
    assert service.portfolio(db, mia, "practice")["accounts"][1]["holdings"] == []
    with pytest.raises(PermissionError):
        service.portfolio(db, mia, "real")


def test_the_practice_league_ranks_returns_and_shows_only_names(db, people):
    market.stub_set("AAPL", price=100, previous_close=100)
    for who in ("pat", "sam"):
        service.start_practice(db, people[who], {"currency": "USD", "cash": "10000"})
        service.practice_trade(
            db,
            people[who],
            {
                "symbol": "AAPL",
                "side": "buy",
                "quantity": "50" if who == "sam" else "10",
            },
        )
    market.stub_set("AAPL", price=120)
    rows = service.league(db, people["mia"])["rows"]
    assert [r["name"] for r in rows] == ["Sam", "Pat"]
    assert rows[0]["return_pct"] == 10.0 and rows[1]["return_pct"] == 2.0
    assert set(rows[0]) == {
        "name",
        "emoji",
        "currency",
        "return_pct",
        "trades",
        "holdings",
        "since",
        "me",
        "rank",
    }
    assert not any(r["me"] for r in rows)


# ---------------------------------------------------------------------------
# watchlists, alerts and the person's own predictions
# ---------------------------------------------------------------------------


def test_watchlists_are_personal(db, people):
    pat, sam = people["pat"], people["sam"]
    added = service.watch(db, pat, {"symbol": "nvda"})
    assert added["item"]["symbol"] == "NVDA" and added["list"]["name"] == "My watchlist"
    service.watch(db, pat, {"symbol": "NVDA"})  # twice is once
    banks = service.create_watchlist(db, pat, {"name": "Banks"})
    service.watch(db, pat, {"symbol": "HDFCBANK.NS", "list_id": banks["id"]})
    lists = service.watchlists(db, pat)["lists"]
    assert [
        (entry["name"], [i["symbol"] for i in entry["items"]]) for entry in lists
    ] == [("My watchlist", ["NVDA"]), ("Banks", ["HDFCBANK.NS"])]
    assert lists[0]["items"][0]["price_cents"] > 0
    assert service.watchlists(db, sam)["lists"] == []
    with pytest.raises(LookupError):
        service.unwatch(db, sam, lists[0]["items"][0]["item_id"])
    with pytest.raises(LookupError):
        service.watch(db, sam, {"symbol": "AAPL", "list_id": banks["id"]})
    with pytest.raises(LookupError):
        service.watch(db, pat, {"symbol": "NOSUCH"})
    service.delete_watchlist(db, pat, banks["id"])
    assert len(service.watchlists(db, pat)["lists"]) == 1


def test_alerts_fire_once_or_once_a_day_and_reach_today_and_the_pulse(db, people):
    pat = people["pat"]
    market.stub_set("AAPL", price=100, previous_close=100)
    above = service.create_alert(
        db, pat, {"symbol": "AAPL", "kind": "above", "price": "110"}
    )
    below = service.create_alert(
        db, pat, {"symbol": "AAPL", "kind": "below", "price": "90"}
    )
    move = service.create_alert(
        db, pat, {"symbol": "AAPL", "kind": "move", "move_pct": "5"}
    )
    assert (
        above["text"] == "AAPL rises above $110.00"
        and move["text"] == "AAPL moves 5% or more in a day"
    )
    with pytest.raises(ValueError):
        service.create_alert(
            db, pat, {"symbol": "AAPL", "kind": "move", "move_pct": "80"}
        )
    with pytest.raises(ValueError):
        service.create_alert(db, pat, {"symbol": "AAPL", "kind": "above"})
    assert service.check_alerts(db) == []
    market.stub_set("AAPL", price=112, previous_close=100)
    fired = service.check_alerts(db)
    assert sorted(a["kind"] for a in fired) == ["above", "move"]
    assert (
        service.check_alerts(db) == []
    )  # the price alert is off, the move alert waits for tomorrow
    alerts = {a["id"]: a for a in service.list_alerts(db, pat)}
    assert (
        alerts[above["id"]]["active"] is False and alerts[move["id"]]["active"] is True
    )
    assert alerts[below["id"]]["triggered"] is False
    pulse = life.PULSE["stocks"](db, pat, "")
    assert pulse["badge"] == 2 and pulse["alerts"][0]["title"] == "Price alert: AAPL"
    assert pulse["alerts"][0]["actions"][0]["path"].startswith("/api/stocks/alerts/")
    cards = life.TODAY["stocks"](db, pat)
    assert cards and any(
        "rose above $110.00" in line["text"] for line in cards[0]["lines"]
    )
    service.seen_alert(db, pat, above["id"])
    service.seen_alert(db, pat, above["id"])  # the button on Today may be pressed twice
    assert life.PULSE["stocks"](db, pat, "")["badge"] == 1
    # turning an alert on again re-arms it; another person cannot touch it
    again = service.update_alert(db, pat, above["id"], {"active": True})
    assert again["active"] and not again["triggered"]
    with pytest.raises(LookupError):
        service.delete_alert(db, people["sam"], above["id"])
    assert life.PULSE["stocks"](db, people["sam"], "")["badge"] == 0
    assert life.TODAY["stocks"](db, people["sam"]) == []


def test_a_persons_predictions_are_scored_when_their_date_passes(
    db, people, monkeypatch
):
    pat = people["pat"]
    market.stub_set("AAPL", price=100, previous_close=99)
    with pytest.raises(ValueError):
        service.create_prediction(
            db,
            pat,
            {
                "symbol": "AAPL",
                "direction": "up",
                "target_date": service.today().isoformat(),
            },
        )
    with pytest.raises(ValueError):
        service.create_prediction(
            db, pat, {"symbol": "AAPL", "direction": "up", "days": 5, "target": "90"}
        )
    made = service.create_prediction(
        db, pat, {"symbol": "AAPL", "direction": "up", "days": 3, "target": "110"}
    )
    assert (
        made["status"] == "open"
        and made["base_cents"] == 100_00
        and made["model_cents"] > 0
    )
    bars = [{"date": made["target_date"], "close": 104.0}]
    later = date.fromisoformat(made["target_date"]) + timedelta(days=1)
    scored = service.score_prediction(made, bars, later)
    assert (
        scored["hit"] == 1
        and scored["actual_cents"] == 104_00
        and scored["error_pct"] == pytest.approx(5.77, abs=0.01)
    )
    assert (
        service.score_prediction(made, bars, date.fromisoformat(made["target_date"]))
        is None
    )
    down = {**made, "direction": "down", "target_cents": 0}
    assert service.score_prediction(down, bars, later)["hit"] == 0
    # when the day has passed, the list scores it by itself
    db.execute(
        "UPDATE stk_predictions SET target_date = ?, made_on = ? WHERE id = ?",
        (
            (service.today() - timedelta(days=3)).isoformat(),
            (service.today() - timedelta(days=9)).isoformat(),
            made["id"],
        ),
    )
    db.commit()
    data = service.list_predictions(db, pat)
    assert data["predictions"][0]["status"] == "scored" and data["stats"]["scored"] == 1
    assert data["stats"]["hit_pct"] in (0.0, 100.0)
    assert service.list_predictions(db, people["sam"])["predictions"] == []
    assert "check_on" in service.predictions_csv(db, pat).decode()


# ---------------------------------------------------------------------------
# earning: ideas, their track record and paid subscriptions
# ---------------------------------------------------------------------------


def _bar(day, high, low, close):
    return {
        "date": day,
        "open": close,
        "high": high,
        "low": low,
        "close": close,
        "volume": 1,
    }


def _idea(
    side="buy",
    entry=100_00,
    target=110_00,
    stop=95_00,
    opened="2026-01-05",
    expires="2026-01-20",
):
    return {
        "status": "open",
        "side": side,
        "entry_cents": entry,
        "target_cents": target,
        "stop_cents": stop,
        "opened_on": opened,
        "expires_on": expires,
        "last_cents": entry,
    }


def test_ideas_track_themselves_to_target_stop_or_expiry():
    today = date(2026, 2, 1)
    hit = service.track_idea(
        _idea(),
        [
            _bar("2026-01-05", 200, 1, 100),
            _bar("2026-01-06", 104, 99, 103),
            _bar("2026-01-07", 111, 101, 109),
        ],
        today,
    )
    assert (
        hit["status"] == "target_hit"
        and hit["close_cents"] == 110_00
        and hit["return_bp"] == 1000
        and hit["closed_on"] == "2026-01-07"
    )
    # the day it was published does not count; a day touching both the stop and the target counts as stopped
    both = service.track_idea(_idea(), [_bar("2026-01-06", 112, 94, 100)], today)
    assert both["status"] == "stopped" and both["return_bp"] == -500
    expired = service.track_idea(
        _idea(),
        [
            _bar("2026-01-06", 104, 98, 102),
            _bar("2026-01-20", 105, 98, 104),
            _bar("2026-01-21", 120, 80, 119),
        ],
        today,
    )
    assert (
        expired["status"] == "expired"
        and expired["close_cents"] == 104_00
        and expired["return_bp"] == 400
    )
    still = service.track_idea(
        _idea(), [_bar("2026-01-06", 104, 98, 102)], date(2026, 1, 7)
    )
    assert still == {"last_cents": 102_00, "return_bp": 200}
    sell = service.track_idea(
        _idea("sell", 100_00, 90_00, 105_00), [_bar("2026-01-06", 101, 89, 90)], today
    )
    assert sell["status"] == "target_hit" and sell["return_bp"] == 1000
    record = service.track_record(
        [
            {**_idea(), **hit},
            {**_idea(), **both},
            {**_idea(), "status": "open", "return_bp": 150},
        ]
    )
    assert (
        record["closed"] == 2 and record["wins"] == 1 and record["win_rate_pct"] == 50.0
    )
    assert record["average_return_pct"] == 2.5 and record["open_average_pct"] == 1.5


def test_publishing_ideas_is_for_adults_with_an_honest_entry_and_paid_needs_a_price(
    db, people
):
    pat = people["pat"]
    market.stub_set("AAPL", price=100, previous_close=100)
    with pytest.raises(PermissionError):
        service.create_idea(
            db,
            people["mia"],
            {"symbol": "AAPL", "side": "buy", "target": "110", "stop": "95"},
        )
    with pytest.raises(ValueError, match="stop must be below"):
        service.create_idea(
            db, pat, {"symbol": "AAPL", "side": "buy", "target": "99", "stop": "95"}
        )
    with pytest.raises(ValueError, match="within 10%"):
        service.create_idea(
            db,
            pat,
            {
                "symbol": "AAPL",
                "side": "buy",
                "entry": "50",
                "target": "60",
                "stop": "45",
            },
        )
    with pytest.raises(ValueError, match="monthly price"):
        service.create_idea(
            db,
            pat,
            {
                "symbol": "AAPL",
                "side": "buy",
                "target": "110",
                "stop": "95",
                "access": "paid",
            },
        )
    idea = service.create_idea(
        db,
        pat,
        {
            "symbol": "AAPL",
            "side": "buy",
            "target": "110",
            "stop": "95",
            "horizon_days": 10,
            "reasoning": "Strong iPhone cycle",
        },
    )
    assert (
        idea["entry_cents"] == 100_00
        and idea["status"] == "open"
        and idea["upside_pct"] == 10.0
        and idea["risk_pct"] == 5.0
    )
    closed = service.update_idea(db, pat, idea["id"], {"close": True})
    assert closed["status"] == "closed" and closed["return_pct"] == 0.0
    with pytest.raises(ValueError):
        service.update_idea(db, pat, idea["id"], {"close": True})
    with pytest.raises(ValueError, match="track record"):
        service.delete_idea(db, pat, idea["id"])
    fresh = service.create_idea(
        db,
        pat,
        {
            "symbol": "MSFT",
            "side": "sell",
            "target": str(_price(db, "MSFT") * 0.9 / 100),
            "stop": str(_price(db, "MSFT") * 1.05 / 100),
        },
    )
    with pytest.raises(LookupError):
        service.delete_idea(db, people["sam"], fresh["id"])
    service.delete_idea(db, pat, fresh["id"])  # within the first hour a mistake can go
    assert [i["symbol"] for i in service.my_ideas(db, pat)] == ["AAPL"]


def test_paid_subscriptions_from_request_to_access_code_with_revenue_counted_once(
    db, people
):
    pat = people["pat"]
    market.stub_set("AAPL", price=100, previous_close=100)
    with pytest.raises(ValueError, match="payment link"):
        service.save_analyst(db, pat, {"display_name": "Pat Picks", "price": "9.99"})
    service.save_analyst(
        db,
        pat,
        {
            "display_name": "Pat Picks",
            "price": "9.99",
            "currency": "USD",
            "payment_link": "https://pay.example/pat",
        },
    )
    free = service.create_idea(
        db, pat, {"symbol": "AAPL", "side": "buy", "target": "110", "stop": "95"}
    )
    paid = service.create_idea(
        db,
        pat,
        {
            "symbol": "AAPL",
            "side": "buy",
            "target": "120",
            "stop": "90",
            "access": "paid",
            "reasoning": "secret sauce",
        },
    )
    link = service.create_link(db, pat)
    assert service.create_link(db, pat)["token"] == link["token"]
    page = public.view(db, link["token"])["page"]
    assert [i["id"] for i in page["free"]] == [free["id"]] and page["paid_open"] == 1
    assert (
        "secret sauce" not in json.dumps(page)
        and "subscribers" in page
        and isinstance(page["subscribers"], int)
    )
    status, asked = public.act(
        db,
        link["token"],
        "subscribe",
        {"name": "Ravi", "email": "Ravi@Example.com"},
        {"client": "x"},
    )
    assert (
        status == 201
        and asked["reference"].startswith("ST-")
        and asked["payment_link"] == "https://pay.example/pat"
    )
    status, again = public.act(
        db,
        link["token"],
        "subscribe",
        {"name": "Ravi K", "email": "ravi@example.com"},
        {"client": "x"},
    )
    assert again["reference"] == asked["reference"]
    assert (
        public.act(
            db, link["token"], "subscribe", {"name": "", "email": "x"}, {"client": "x"}
        )[0]
        == 400
    )
    subs = service.subscribers(db, pat)
    sub = subs["subscribers"][0]
    assert (
        sub["status"] == "pending"
        and sub["name"]
        == "Ravi"  # a second visitor with the same email cannot rename the request
        and subs["earnings"]["pending"] == 1
    )
    assert (
        public.ask(
            db,
            link["token"],
            "paid",
            {"email": "ravi@example.com", "secret": "AAAA-BBBB-CCCC"},
        )[0]
        == 403
    )
    with pytest.raises(PermissionError):
        service.subscribers(db, people["mia"])
    with pytest.raises(LookupError):
        service.mark_paid(db, people["sam"], sub["id"], {})
    result = service.mark_paid(db, pat, sub["id"], {"months": 2})
    code = result["code"]
    assert len(code) == 14 and result["subscriber"]["status"] == "active"
    assert (
        result["subscriber"]["paid_until"]
        == (service.today() + timedelta(days=60)).isoformat()
    )
    payment = service.subscribers(db, pat)["subscribers"][0]["payments"][0]
    assert payment["amount_cents"] == 19_98
    revenue = kit.revenue(db)
    assert revenue["total_cents"] == 19_98
    outbox = kit.outbox(db, app="stocks")
    assert (
        len(outbox) == 1
        and outbox[0]["recipient"] == "ravi@example.com"
        and code in outbox[0]["body"]
    )
    status, opened = public.ask(
        db, link["token"], "paid", {"email": "ravi@example.com", "secret": code.lower()}
    )
    assert (
        status == 200
        and [i["id"] for i in opened["ideas"]] == [paid["id"]]
        and opened["ideas"][0]["reasoning"] == "secret sauce"
    )
    assert (
        public.ask(
            db, link["token"], "paid", {"email": "other@example.com", "secret": code}
        )[0]
        == 403
    )
    # a second payment extends the period; undoing it shortens it again and stops counting
    second = service.mark_paid(db, pat, sub["id"], {"months": 1, "amount": "9.99"})
    assert (
        second["code"] == ""
        and second["subscriber"]["paid_until"]
        == (service.today() + timedelta(days=90)).isoformat()
    )
    assert kit.revenue(db)["total_cents"] == 29_97
    latest = service.subscribers(db, pat)["subscribers"][0]["payments"][0]
    after = service.void_payment(db, pat, latest["id"])
    assert (
        after["paid_until"] == (service.today() + timedelta(days=60)).isoformat()
        and kit.revenue(db)["total_cents"] == 19_98
    )
    service.void_payment(db, pat, latest["id"])  # twice is once
    # a new code replaces the old one
    fresh = service.new_code(db, pat, sub["id"])["code"]
    assert (
        public.ask(
            db, link["token"], "paid", {"email": "ravi@example.com", "secret": code}
        )[0]
        == 403
    )
    assert (
        public.ask(
            db, link["token"], "paid", {"email": "ravi@example.com", "secret": fresh}
        )[0]
        == 200
    )
    # once the period ends the code stops working, and the background marks it expired
    db.execute(
        "UPDATE stk_subscribers SET paid_until = ? WHERE id = ?",
        ((service.today() - timedelta(days=1)).isoformat(), sub["id"]),
    )
    db.commit()
    assert (
        public.ask(
            db, link["token"], "paid", {"email": "ravi@example.com", "secret": fresh}
        )[0]
        == 403
    )
    assert service.run_background(db)["subscriptions_expired"] == 1
    # cancelling ends access; turning the page off makes the link 404
    service.cancel_subscriber(db, pat, sub["id"])
    assert service.subscribers(db, pat)["subscribers"][0]["status"] == "cancelled"
    service.revoke_link(db, pat)
    with pytest.raises(LookupError):
        public.view(db, link["token"])


def test_an_analyst_page_shows_closed_paid_ideas_in_the_track_record(db, people):
    pat = people["pat"]
    market.stub_set("AAPL", price=100, previous_close=100)
    service.save_analyst(
        db, pat, {"price": "5", "payment_instructions": "UPI pat@bank"}
    )
    paid = service.create_idea(
        db,
        pat,
        {
            "symbol": "AAPL",
            "side": "buy",
            "target": "110",
            "stop": "95",
            "access": "paid",
        },
    )
    db.execute(
        "UPDATE stk_ideas SET opened_on = ?, expires_on = ? WHERE id = ?",
        (
            (service.today() - timedelta(days=40)).isoformat(),
            (service.today() - timedelta(days=20)).isoformat(),
            paid["id"],
        ),
    )
    db.commit()
    page = service.analyst_page(db, pat["id"])
    assert page["paid_open"] == 0 and [i["id"] for i in page["closed"]] == [paid["id"]]
    assert (
        page["closed"][0]["status"] in ("target_hit", "stopped", "expired")
        and page["record"]["closed"] == 1
    )
    asked = service.public_subscribe(db, pat["id"], {"name": "A", "email": "a@x.test"})
    assert (
        asked["payment_instructions"] == "UPI pat@bank" and asked["status"] == "pending"
    )
    service.save_analyst(db, pat, {"accepting": False})
    with pytest.raises(ValueError):
        service.public_subscribe(db, pat["id"], {"name": "B", "email": "b@x.test"})


# ---------------------------------------------------------------------------
# the StocksAgent
# ---------------------------------------------------------------------------


def test_the_stocks_agent_reads_portfolios_movers_and_alerts(db, people):
    from aira.suite.ai import agents

    pat = people["pat"]
    agent = agents.BY_ID["stocks"]
    assert (
        agent.name == "StocksAgent"
        and agent.app == "stocks"
        and "nifty" in agent.keywords
    )
    assert {a.name: a.risk for a in agent.actions} == {
        "add_to_watchlist": "minor",
        "create_price_alert": "minor",
        "practice_buy": "major",
        "practice_sell": "major",
    }
    assert agent.read(db, pat)[0] == "No portfolio, watchlist or alerts yet in Stocks."
    market.stub_set("AAPL", price=100, previous_close=95)
    service.start_practice(db, pat, {"currency": "USD", "cash": "10000"})
    assert (
        agent.action("practice_buy").run(db, pat, {"symbol": "apple", "quantity": 5})
        == "Bought 5 AAPL at $100.00 (practice money)."
    )
    market.stub_set(
        "AAPL", price=105, previous_close=95
    )  # shares bought today count from the price paid
    assert (
        agent.action("add_to_watchlist").run(db, pat, {"symbol": "Infosys"})
        == "Added INFY.NS to My watchlist."
    )
    assert (
        agent.action("create_price_alert")
        .run(db, pat, {"symbol": "TCS", "condition": "below", "price": "1"})
        .startswith("I will tell you when TCS.NS falls below")
    )
    facts = agent.read(db, pat)
    assert facts[0].startswith(
        "Practice portfolio (USD): worth $10,025.00, today +$25.00"
    )
    assert any(f.startswith("Watchlist: Infosys (INFY.NS)") for f in facts)
    assert any("price alerts are waiting" in f for f in facts)
    with pytest.raises(ValueError):
        agent.action("practice_sell").run(db, pat, {"symbol": "AAPL"})


# ---------------------------------------------------------------------------
# the single-user build: a household
# ---------------------------------------------------------------------------


def _anonymous(env, method, path, body=None, headers=None):
    data = json.dumps(body).encode() if body is not None else None
    request = urllib.request.Request(env.base + path, data=data, method=method)
    for name, value in {
        "Content-Type": "application/json",
        "X-Requested-With": "aira",
        "Origin": env.base,
        **(headers or {}),
    }.items():
        request.add_header(name, value)
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            return response.status, json.loads(response.read() or b"{}")
    except urllib.error.HTTPError as error:
        return error.code, json.loads(error.read() or b"{}")


@pytest.fixture
def local_index():
    public.set_index(local._public_link_index)


def test_a_household_follows_markets_trades_in_practice_and_records_real_holdings(
    env, api, local_index
):
    kit.forget_schema()
    home = Household(env, api)
    pat = home.owner
    mia = home.add("Mia", age_group="child", pin="2468")
    us = pat.get("/api/stocks/markets?market=us")
    assert len(us["stocks"]) >= 30 and us["gainers"] and us["provider"] == "stub"
    assert pat.get("/api/stocks/markets?market=in")["indices"][0]["symbol"] == "^NSEI"
    assert (
        pat.get("/api/stocks/search?q=reliance")["results"][0]["symbol"]
        == "RELIANCE.NS"
    )
    page = pat.get("/api/stocks/stock?symbol=%5ENSEI&range=6m")
    assert page["quote"]["index"] is True and len(page["log"]) == 126
    status, csv = pat.call(
        "GET", "/api/stocks/export/log?symbol=M%26M.NS&range=1m", raw=True
    )
    assert status == 200 and csv.startswith(b"date,open,high,low,close")
    assert pat.call("GET", "/api/stocks/stock?symbol=NOSUCH")[0] == 404
    assert pat.call("GET", "/api/stocks/stock?symbol=bad%20one")[0] == 400
    predicted = pat.get("/api/stocks/predict?symbol=TCS.NS")
    assert (
        predicted["currency"] == "INR"
        and "not financial advice" in predicted["disclaimer"]
    )

    # practice (a child too), real holdings (adults only)
    market.stub_set("AAPL", price=200, previous_close=190)
    pat.post("/api/stocks/practice/start", {"currency": "USD"}, expect=201)
    trade = pat.post(
        "/api/stocks/practice/trade",
        {"symbol": "AAPL", "side": "buy", "quantity": 10},
        expect=201,
    )
    assert trade["account"]["cash_cents"] == 98_000_00
    mia.post(
        "/api/stocks/practice/start",
        {"currency": "USD", "cash_cents": 1_000_00},
        expect=201,
    )
    mia.post(
        "/api/stocks/practice/trade",
        {"symbol": "AAPL", "side": "buy", "quantity": 1},
        expect=201,
    )
    assert mia.call("GET", "/api/stocks/portfolio?kind=real")[0] == 403
    assert (
        mia.call(
            "POST",
            "/api/stocks/real/transactions",
            {
                "symbol": "AAPL",
                "kind": "buy",
                "trade_date": "2026-01-02",
                "quantity": 1,
                "price_cents": 100,
            },
        )[0]
        == 403
    )
    assert mia.call("GET", "/api/stocks/ideas")[0] == 403
    real = pat.post(
        "/api/stocks/real/transactions",
        {
            "symbol": "AAPL",
            "kind": "buy",
            "trade_date": "2026-01-02",
            "quantity": 2,
            "price_cents": 150_00,
            "fees_cents": 1_00,
        },
        expect=201,
    )
    holdings = pat.get("/api/stocks/portfolio?kind=real")["accounts"][0]
    assert holdings["value_cents"] == 400_00 and holdings["unrealised_cents"] == 99_00
    pat.ok(
        "PATCH",
        f"/api/stocks/real/transactions/{real['transaction']['id']}",
        {"quantity": 3},
    )
    assert (
        pat.get("/api/stocks/portfolio?kind=real")["accounts"][0]["value_cents"]
        == 600_00
    )
    status, csv = pat.call("GET", "/api/stocks/export/transactions?kind=real", raw=True)
    assert status == 200 and b"AAPL" in csv
    assert (
        mia.call(
            "DELETE", f"/api/stocks/real/transactions/{real['transaction']['id']}"
        )[0]
        == 403
    )
    league = mia.get("/api/stocks/league")["rows"]
    assert {r["name"] for r in league} == {pat.name, "Mia"} and next(
        r for r in league if r["me"]
    )["name"] == "Mia"

    # watchlist and alerts are each person's own
    pat.post("/api/stocks/watchlist", {"symbol": "MSFT"}, expect=201)
    assert mia.get("/api/stocks/watchlist")["lists"] == []
    alert = pat.post(
        "/api/stocks/alerts",
        {"symbol": "AAPL", "kind": "above", "price_cents": 210_00},
        expect=201,
    )["alert"]
    assert mia.get("/api/stocks/alerts")["alerts"] == []
    assert mia.call("DELETE", f"/api/stocks/alerts/{alert['id']}")[0] == 404
    market.stub_set("AAPL", price=215)
    checked = pat.post("/api/stocks/alerts/check")
    assert [a["id"] for a in checked["fired"]] == [alert["id"]]
    pulse = pat.get("/api/life/pulse")
    assert pulse["apps"]["stocks"]["badge"] == 1 and any(
        a["app"] == "stocks" for a in pulse["alerts"]
    )
    cards = [c for c in pat.get("/api/life/today")["cards"] if c["app"] == "stocks"]
    assert cards and cards[0]["lines"][0]["text"].startswith("Practice portfolio (USD)")
    seen = next(line for line in cards[0]["lines"] if line.get("action"))
    pat.post(seen["action"]["path"], seen["action"]["body"])
    assert pat.get("/api/life/pulse")["apps"]["stocks"]["badge"] == 0
    overview = pat.get("/api/stocks/overview")
    assert (
        overview["accounts"][0]["kind"] == "practice"
        and overview["movers"][0]["symbol"] == "MSFT"
    )

    # a prediction of one's own
    made = pat.post(
        "/api/stocks/predictions",
        {"symbol": "AAPL", "direction": "down", "days": 7},
        expect=201,
    )
    assert pat.get("/api/stocks/predictions")["stats"]["open"] == 1
    assert (
        mia.call("DELETE", f"/api/stocks/predictions/{made['prediction']['id']}")[0]
        == 404
    )


def test_the_analyst_page_works_for_a_visitor_without_an_account(env, api, local_index):
    kit.forget_schema()
    pat = Household(env, api).owner
    market.stub_set("AAPL", price=100, previous_close=100)
    pat.post(
        "/api/stocks/analyst",
        {
            "display_name": "Pat Picks",
            "price_cents": 4_99,
            "payment_link": "https://pay.example/p",
        },
    )
    pat.post(
        "/api/stocks/ideas",
        {"symbol": "AAPL", "side": "buy", "target_cents": 110_00, "stop_cents": 95_00},
        expect=201,
    )
    paid = pat.post(
        "/api/stocks/ideas",
        {
            "symbol": "AAPL",
            "side": "buy",
            "target_cents": 108_00,
            "stop_cents": 96_00,
            "access": "paid",
            "reasoning": "members only",
        },
        expect=201,
    )["idea"]
    link = pat.post("/api/stocks/analyst/link")["link"]
    assert link["url"].endswith(link["path"])
    status, page = _anonymous(env, "GET", f"/api/public/{link['token']}")
    assert status == 200 and page["app"] == "stocks" and page["kind"] == "analyst"
    assert (
        page["page"]["analyst"]["display_name"] == "Pat Picks"
        and len(page["page"]["free"]) == 1
        and page["page"]["paid_open"] == 1
    )
    assert (
        "members only" not in json.dumps(page)
        and page["actions"] == ["subscribe"]
        and page["queries"] == ["paid"]
    )
    status, asked = _anonymous(
        env,
        "POST",
        f"/api/public/{link['token']}/subscribe",
        {"name": "Ravi", "email": "ravi@example.com"},
    )
    assert status == 201 and asked["reference"].startswith("ST-")
    ideas = pat.get("/api/stocks/ideas")
    sub = ideas["subscribers"][0]
    assert sub["status"] == "pending" and ideas["link"]["token"] == link["token"]
    cards = [c for c in pat.get("/api/life/today")["cards"] if c["app"] == "stocks"]
    assert (
        cards[0]["lines"][-1]["text"]
        == "1 person wants to subscribe to your stock ideas"
    )
    code = pat.post(f"/api/stocks/subscribers/{sub['id']}/paid", {"months": 1})["code"]
    assert (
        _anonymous(
            env,
            "GET",
            f"/api/public/{link['token']}/paid?email=ravi%40example.com",
            headers={"Aira-Secret": "WRONG-CODE"},
        )[0]
        == 403
    )
    status, opened = _anonymous(
        env,
        "GET",
        f"/api/public/{link['token']}/paid?email=ravi%40example.com",
        headers={"Aira-Secret": code},
    )
    assert status == 200 and [i["id"] for i in opened["ideas"]] == [paid["id"]]
    assert pat.get("/api/suite/home")["revenue"]["total_cents"] == 4_99
    pat.ok("DELETE", "/api/stocks/analyst/link")
    assert _anonymous(env, "GET", f"/api/public/{link['token']}")[0] == 404


def test_the_stocks_agent_works_through_the_ai_chat(env, api, local_index):
    kit.forget_schema()
    pat = Household(env, api).owner
    market.stub_set("AAPL", price=100, previous_close=100)
    pat.post("/api/stocks/practice/start", {"currency": "USD"}, expect=201)
    agents = {a["id"]: a for a in pat.get("/api/ai/agents")["agents"]}
    assert agents["stocks"]["name"] == "StocksAgent"
    suite.set_gateway(
        _Model(
            {
                "reply": "Done.",
                "actions": [
                    {
                        "agent": "stocks",
                        "action": "add_to_watchlist",
                        "args": {"symbol": "NVDA"},
                    },
                    {
                        "agent": "stocks",
                        "action": "practice_buy",
                        "args": {"symbol": "AAPL", "quantity": 3},
                    },
                ],
            }
        )
    )
    answer = pat.post(
        "/api/ai/chat",
        {"message": "Watch NVIDIA and buy 3 Apple shares in my practice portfolio"},
    )["answer"]
    statuses = {a["summary"]: a["status"] for a in answer["actions"]}
    assert statuses["Add NVDA to the watchlist"] == "done"
    assert statuses["Buy 3 AAPL with practice money"] == "pending"
    assert [
        i["symbol"] for i in pat.get("/api/stocks/watchlist")["lists"][0]["items"]
    ] == ["NVDA"]
    waiting = next(a for a in answer["actions"] if a["status"] == "pending")
    pat.post(f"/api/ai/ops/{waiting['id']}/approve")
    holdings = pat.get("/api/stocks/portfolio")["accounts"][0]["holdings"]
    assert holdings[0]["symbol"] == "AAPL" and holdings[0]["quantity"] == 3
    # a question about the portfolio is answered from the StocksAgent's facts
    suite.set_gateway(_NoModel())
    reply = pat.post(
        "/api/ai/chat", {"message": "How is my portfolio doing in the stock market?"}
    )["answer"]
    assert (
        "StocksAgent" in reply["agents"]
        and "Practice portfolio (USD)" in reply["content"]
    )


# ---------------------------------------------------------------------------
# the hosted build
# ---------------------------------------------------------------------------


def test_hosted_stocks_needs_the_app_and_keeps_each_member_private(cloud):
    kit.forget_schema()
    org = build_org(cloud)
    owner = sign_in(cloud, org.owner)
    status, body = owner.call("GET", "/api/stocks/markets")
    assert status == 402 and body["code"] == "app_required"
    cloud.billing.subscribe_app(org.owner, "stocks")
    for who in ("owner", "admin", "coach", "candidate", "billing", "auditor"):
        browser = sign_in(cloud, getattr(org, who))
        assert browser.call("GET", "/api/stocks/markets?market=in")[0] == 200, who
        status, added = browser.call(
            "POST", "/api/stocks/watchlist", {"symbol": "AAPL"}
        )
        assert status == 201, (who, added)
        assert (
            browser.call("POST", "/api/stocks/practice/start", {"currency": "USD"})[0]
            == 201
        ), who
    candidate = sign_in(cloud, org.candidate)
    lists = candidate.call("GET", "/api/stocks/watchlist")[1]["lists"]
    assert len(lists) == 1 and [i["symbol"] for i in lists[0]["items"]] == ["AAPL"]
    status, alert = owner.call(
        "POST",
        "/api/stocks/alerts",
        {"symbol": "AAPL", "kind": "below", "price_cents": 1},
    )
    assert status == 201
    assert candidate.call("GET", "/api/stocks/alerts")[1]["alerts"] == []
    assert (
        candidate.call("DELETE", f"/api/stocks/alerts/{alert['alert']['id']}")[0] == 404
    )
    league = candidate.call("GET", "/api/stocks/league")[1]
    assert league["hosted"] is True and len(league["rows"]) == 6
    # an analyst page in the organization, opened by a visitor with no account
    owner.call(
        "POST",
        "/api/stocks/analyst",
        {
            "display_name": "Olive",
            "price_cents": 10_00,
            "payment_link": "https://pay.example/o",
        },
    )
    status, idea = owner.call(
        "POST",
        "/api/stocks/ideas",
        {
            "symbol": "AAPL",
            "side": "buy",
            "target_cents": _hosted_price(owner) * 2,
            "stop_cents": 1,
        },
    )
    assert status == 201, idea
    status, made = owner.call("POST", "/api/stocks/analyst/link", {})
    assert status == 200
    visitor = Browser(cloud)
    status, page = visitor.call("GET", f"/api/public/{made['link']['token']}")
    assert (
        status == 200
        and page["page"]["analyst"]["display_name"] == "Olive"
        and len(page["page"]["free"]) == 1
    )
    status, asked = visitor.call(
        "POST",
        f"/api/public/{made['link']['token']}/subscribe",
        {"name": "Vi", "email": "vi@example.com"},
    )
    assert status == 201, asked
    sub = owner.call("GET", "/api/stocks/ideas")[1]["subscribers"][0]
    status, paid = owner.call("POST", f"/api/stocks/subscribers/{sub['id']}/paid", {})
    assert status == 200 and paid["code"]
    status, _ = visitor.call(
        "GET",
        f"/api/public/{made['link']['token']}/paid?email=vi%40example.com",
        headers={"Aira-Secret": paid["code"]},
    )
    assert status == 200
    # another organization sees none of it
    other = build_org(cloud, "Other Org", slug_email="other.test")
    cloud.billing.subscribe_app(other.owner, "stocks")
    stranger = sign_in(cloud, other.owner)
    assert stranger.call("GET", "/api/stocks/watchlist")[1]["lists"] == []
    assert stranger.call("GET", "/api/stocks/league")[1]["rows"] == []


def _hosted_price(browser):
    return browser.call("GET", "/api/stocks/stock?symbol=AAPL&range=1m")[1]["quote"][
        "price_cents"
    ]


# ---------------------------------------------------------------------------
# regressions from the tester's round
# ---------------------------------------------------------------------------


def test_an_old_holding_recorded_with_todays_date_does_not_show_its_whole_gain_as_today(
    db, people
):
    pat = people["pat"]
    market.stub_set("MSFT", price=636, previous_close=630)
    service.add_real(
        db,
        pat,
        {
            "symbol": "MSFT",
            "kind": "buy",
            "trade_date": service.today().isoformat(),  # the date the form used to fill in
            "quantity": "3",
            "price": "300",  # what it cost years ago: far outside today's trading
        },
    )
    account = service.portfolio(db, pat, "real")["accounts"][0]
    assert account["unrealised_cents"] == 3 * (636_00 - 300_00)
    assert account["day_change_cents"] == 3 * 6_00  # the day's move, not +112%
    assert account["day_change_pct"] == pytest.approx(0.95, abs=0.01)
    brief = service.overview(db, pat)
    assert "112" not in json.dumps(brief)
    # a real buy made today at today's price still counts from the price paid
    service.add_real(
        db,
        pat,
        {
            "symbol": "MSFT",
            "kind": "buy",
            "trade_date": service.today().isoformat(),
            "quantity": "1",
            "price": "634",
        },
    )
    account = service.portfolio(db, pat, "real")["accounts"][0]
    assert account["day_change_cents"] == 3 * 6_00 + (636_00 - 634_00)


def test_trades_of_no_shares_or_worth_nothing_are_refused(db, people):
    mia = people["mia"]
    service.start_practice(db, mia, {"currency": "USD"})
    for tiny in ("0.0000001", "0.0000004"):
        with pytest.raises(ValueError, match="too small"):
            service.practice_trade(
                db, mia, {"symbol": "AAPL", "side": "buy", "quantity": tiny}
            )
    with pytest.raises(ValueError, match="worth less than"):
        service.practice_trade(
            db, mia, {"symbol": "AAPL", "side": "buy", "quantity": "0.000001"}
        )
    assert service.quantity("0.000001") == 0.000001
    account = service.portfolio(db, mia, "practice")["accounts"][0]
    assert account["trades"] == 0 and account["transactions"] == []


def test_a_typo_in_a_price_is_an_error_not_a_free_page(db, people):
    pat = people["pat"]
    with pytest.raises(ValueError, match="monthly price must be a number"):
        service.save_analyst(
            db,
            pat,
            {
                "display_name": "Qa Picks",
                "price": "abc",
                "payment_link": "https://p.example",
            },
        )
    assert service.analyst(db, pat["id"]) is None
    saved = service.save_analyst(
        db,
        pat,
        {
            "display_name": "Qa Picks",
            "price": "4.50",
            "payment_link": "https://p.example",
        },
    )
    assert saved["price_cents"] == 4_50
    with pytest.raises(ValueError, match="must be a number"):
        service.add_real(
            db,
            pat,
            {
                "symbol": "AAPL",
                "kind": "buy",
                "trade_date": "2026-01-05",
                "quantity": "1",
                "price": "abc",
            },
        )


def test_confirming_the_same_payment_twice_records_it_once(db, people):
    import threading

    pat = people["pat"]
    service.save_analyst(
        db,
        pat,
        {"display_name": "Pat", "price": "9.99", "payment_link": "https://p.example"},
    )
    owner = pat["id"]
    service.public_subscribe(db, owner, {"name": "Vi", "email": "vi@example.com"})
    sub = service.subscribers(db, pat)["subscribers"][0]
    first = service.mark_paid(db, pat, sub["id"], {})
    again = service.mark_paid(db, pat, sub["id"], {})  # a retry after a slow network
    assert first["code"] and again["duplicate"] is True and again["code"] == ""
    # two requests at the same moment, each on its own connection
    service.public_subscribe(db, owner, {"name": "Bo", "email": "bo@example.com"})
    bo = next(
        s for s in service.subscribers(db, pat)["subscribers"] if s["name"] == "Bo"
    )
    path = db.execute("PRAGMA database_list").fetchone()[2]
    answers, errors = [], []
    start = threading.Barrier(2)

    def pay():
        connection = sqlite3.connect(path, timeout=15)
        connection.row_factory = sqlite3.Row
        try:
            start.wait()
            answers.append(service.mark_paid(connection, pat, bo["id"], {}))
        except Exception as error:  # noqa: BLE001 - reported below
            errors.append(error)
        finally:
            connection.close()

    threads = [threading.Thread(target=pay) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert not errors, errors
    assert sorted(bool(a.get("duplicate")) for a in answers) == [False, True]
    earnings = service.subscribers(db, pat)["earnings"]
    assert earnings["payments"] == 2 and earnings["total_cents"] == 2 * 9_99
    assert kit.revenue(db)["total_cents"] == 2 * 9_99
    bo = next(
        s for s in service.subscribers(db, pat)["subscribers"] if s["name"] == "Bo"
    )
    assert bo["paid_until"] == (service.today() + timedelta(days=30)).isoformat()
    # a different payment (another amount) is a new one
    more = service.mark_paid(db, pat, bo["id"], {"months": 1, "amount": "9.98"})
    assert not more.get("duplicate")


def test_the_public_subscribe_answer_does_not_tell_who_already_pays(db, people):
    pat = people["pat"]
    service.save_analyst(
        db,
        pat,
        {"display_name": "Pat", "price": "9.99", "payment_link": "https://p.example"},
    )
    owner = pat["id"]
    asked = service.public_subscribe(
        db, owner, {"name": "Vi", "email": "vi@example.com", "note": "hello"}
    )
    renamed = service.public_subscribe(
        db, owner, {"name": "Vi2", "email": "VI@example.com", "note": "changed"}
    )
    sub = service.subscribers(db, pat)["subscribers"][0]
    assert sub["name"] == "Vi" and sub["note"] == "hello"
    assert renamed["reference"] == asked["reference"]
    service.mark_paid(db, pat, sub["id"], {})
    active = service.public_subscribe(
        db, owner, {"name": "Someone", "email": "vi@example.com"}
    )
    stranger = service.public_subscribe(
        db, owner, {"name": "Someone", "email": "new@example.com"}
    )
    assert set(active) == set(stranger) and active["status"] == stranger["status"]
    assert "already subscribed" not in active["message"]
    assert service.subscribers(db, pat)["subscribers"][-1]["status"] in (
        "active",
        "pending",
    )
    vi = next(
        s for s in service.subscribers(db, pat)["subscribers"] if s["id"] == sub["id"]
    )
    assert vi["status"] == "active" and vi["name"] == "Vi"


def test_an_idea_can_be_deleted_in_its_first_hour_and_its_reasoning_edited(db, people):
    pat = people["pat"]
    market.stub_set("AAPL", price=100, previous_close=100)
    idea = service.create_idea(
        db, pat, {"symbol": "AAPL", "side": "buy", "target": "110", "stop": "95"}
    )
    view = next(i for i in service.my_ideas(db, pat) if i["id"] == idea["id"])
    assert view["deletable"] is True
    edited = service.update_idea(db, pat, idea["id"], {"reasoning": "Cheap now."})
    assert edited["reasoning"] == "Cheap now." and edited["entry_cents"] == 100_00
    db.execute(
        "UPDATE stk_ideas SET created_at = ? WHERE id = ?",
        ("2020-01-01T00:00:00Z", idea["id"]),
    )
    db.commit()
    view = next(i for i in service.my_ideas(db, pat) if i["id"] == idea["id"])
    assert view["deletable"] is False
    with pytest.raises(ValueError, match="first hour"):
        service.delete_idea(db, pat, idea["id"])


def test_the_agent_finds_a_stock_from_its_full_company_name(tmp_path):
    """The writing model often names the company in full ("Apple Inc.", "Infosys Ltd"): the agent still finds it."""
    import sqlite3 as _sqlite3

    from aira.suite.stocks import assistant

    connection = _sqlite3.connect(tmp_path / "names.sqlite3")
    connection.row_factory = _sqlite3.Row
    assert assistant.resolve(connection, "Apple Inc.") == "AAPL"
    assert assistant.resolve(connection, "apple shares") == "AAPL"
    assert assistant.resolve(connection, "Infosys Ltd") == "INFY.NS"
    assert assistant.resolve(connection, "MSFT") == "MSFT"
