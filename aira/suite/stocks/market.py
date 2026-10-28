"""Market data behind one small interface, with a database cache.

A **provider** answers one question: "the daily bars of this symbol" (two years of open, high, low, close and volume,
plus its name, currency, exchange, the latest price and the previous close). Two providers exist:

* :class:`YahooProvider` (the default): Yahoo Finance's public chart endpoint
  (``https://query1.finance.yahoo.com/v8/finance/chart/<SYMBOL>``), no key, a normal browser User-Agent, short
  timeouts. A quick ``range=5d`` call refreshes the price; the full ``range=2y`` history is fetched once a day.
* :class:`StubProvider` (``AIRA_MARKET_PROVIDER=stub``): seeded random-walk prices per symbol, the same on every
  computer for the same day, with no network. Every test uses it; it is also chosen automatically under pytest.

:func:`series` and :func:`quotes` read through the cache (``stk_market``): a price at most every
:data:`QUOTE_TTL` seconds, the history once a day. When the provider cannot be reached the last saved data is
returned marked ``stale``; with nothing saved, :class:`MarketUnavailable` says so in plain words. A market's whole
universe is fetched with a small thread pool (:data:`WORKERS` at a time), so a screen of 30 stocks is quick and
polite. Network calls never run while the database is written: results are saved afterwards on the caller's thread.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import random
import re
import sqlite3
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Iterable
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta, timezone
from typing import Any, Protocol

from aira import suite
from aira.suite import kit

QUOTE_TTL = 300  # seconds between price refreshes of one symbol
WORKERS = 6  # concurrent requests when a market's universe is refreshed
TIMEOUT = 8.0  # seconds per request
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/126.0 Safari/537.36"
)
CHART_URL = "https://query1.finance.yahoo.com/v8/finance/chart/{symbol}?range={range}&interval=1d&includePrePost=false"
SYMBOL = re.compile(r"^[A-Z0-9^][A-Z0-9.\-&=^]{0,19}$")


class MarketUnavailable(RuntimeError):
    """The market data could not be reached and nothing is saved (the server answers 502 with this sentence)."""


# ---------------------------------------------------------------------------
# the universe
# ---------------------------------------------------------------------------

MARKETS: dict[str, dict[str, Any]] = {
    "us": {
        "label": "United States",
        "currency": "USD",
        "indices": (
            ("^GSPC", "S&P 500"),
            ("^IXIC", "Nasdaq Composite"),
            ("^DJI", "Dow Jones"),
        ),
        "stocks": (
            ("AAPL", "Apple"),
            ("MSFT", "Microsoft"),
            ("NVDA", "NVIDIA"),
            ("AMZN", "Amazon"),
            ("GOOGL", "Alphabet (Google)"),
            ("META", "Meta Platforms"),
            ("TSLA", "Tesla"),
            ("BRK-B", "Berkshire Hathaway"),
            ("JPM", "JPMorgan Chase"),
            ("V", "Visa"),
            ("MA", "Mastercard"),
            ("UNH", "UnitedHealth"),
            ("XOM", "Exxon Mobil"),
            ("JNJ", "Johnson & Johnson"),
            ("WMT", "Walmart"),
            ("PG", "Procter & Gamble"),
            ("HD", "Home Depot"),
            ("COST", "Costco"),
            ("AVGO", "Broadcom"),
            ("ORCL", "Oracle"),
            ("CVX", "Chevron"),
            ("KO", "Coca-Cola"),
            ("PEP", "PepsiCo"),
            ("BAC", "Bank of America"),
            ("ADBE", "Adobe"),
            ("CRM", "Salesforce"),
            ("NFLX", "Netflix"),
            ("AMD", "AMD"),
            ("INTC", "Intel"),
            ("DIS", "Walt Disney"),
            ("MCD", "McDonald's"),
            ("NKE", "Nike"),
        ),
    },
    "in": {
        "label": "India (NSE)",
        "currency": "INR",
        "indices": (
            ("^NSEI", "NIFTY 50"),
            ("^BSESN", "Sensex"),
            ("^NSEBANK", "NIFTY Bank"),
        ),
        "stocks": (
            ("RELIANCE.NS", "Reliance Industries"),
            ("TCS.NS", "Tata Consultancy Services"),
            ("HDFCBANK.NS", "HDFC Bank"),
            ("ICICIBANK.NS", "ICICI Bank"),
            ("INFY.NS", "Infosys"),
            ("BHARTIARTL.NS", "Bharti Airtel"),
            ("ITC.NS", "ITC"),
            ("SBIN.NS", "State Bank of India"),
            ("LT.NS", "Larsen & Toubro"),
            ("HINDUNILVR.NS", "Hindustan Unilever"),
            ("KOTAKBANK.NS", "Kotak Mahindra Bank"),
            ("AXISBANK.NS", "Axis Bank"),
            ("BAJFINANCE.NS", "Bajaj Finance"),
            ("MARUTI.NS", "Maruti Suzuki"),
            ("SUNPHARMA.NS", "Sun Pharma"),
            ("HCLTECH.NS", "HCLTech"),
            ("ASIANPAINT.NS", "Asian Paints"),
            ("TITAN.NS", "Titan"),
            ("ULTRACEMCO.NS", "UltraTech Cement"),
            ("WIPRO.NS", "Wipro"),
            ("NTPC.NS", "NTPC"),
            ("POWERGRID.NS", "Power Grid"),
            ("ONGC.NS", "ONGC"),
            ("M&M.NS", "Mahindra & Mahindra"),
            ("TATASTEEL.NS", "Tata Steel"),
            ("NESTLEIND.NS", "Nestle India"),
            ("JSWSTEEL.NS", "JSW Steel"),
            ("ADANIENT.NS", "Adani Enterprises"),
            ("TECHM.NS", "Tech Mahindra"),
            ("COALINDIA.NS", "Coal India"),
            ("DRREDDY.NS", "Dr. Reddy's"),
            ("CIPLA.NS", "Cipla"),
        ),
    },
}
NAMES: dict[str, str] = {}
MARKET_OF: dict[str, str] = {}
INDICES: set[str] = set()
for _market_id, _market in MARKETS.items():
    for _symbol, _name in (*_market["indices"], *_market["stocks"]):
        NAMES[_symbol] = _name
        MARKET_OF[_symbol] = _market_id
    INDICES.update(symbol for symbol, _ in _market["indices"])


def clean_symbol(value: Any) -> str:
    """``" reliance.ns "`` -> ``"RELIANCE.NS"``; a ValueError says what a symbol looks like."""
    text = str(value or "").strip().upper()
    if not text:
        raise ValueError("Enter a stock symbol, for example AAPL or RELIANCE.NS.")
    if not SYMBOL.match(text):
        raise ValueError(
            f"{text[:24]} is not a stock symbol. Use letters like AAPL, or add .NS for India (TCS.NS)."
        )
    return text


def market_of(symbol: str) -> str:
    """``us``, ``in`` or ``other`` (a symbol found through the provider, outside the universe)."""
    if symbol in MARKET_OF:
        return MARKET_OF[symbol]
    if symbol.endswith((".NS", ".BO")):
        return "in"
    if re.fullmatch(r"[A-Z][A-Z\-]{0,5}", symbol):
        return "us"
    return "other"


def is_index(symbol: str) -> bool:
    return symbol.startswith("^")


def universe(market: str) -> list[str]:
    spec = MARKETS.get(market)
    if spec is None:
        raise ValueError(f"Choose a market: {', '.join(MARKETS)}.")
    return [symbol for symbol, _ in spec["stocks"]]


def search_universe(query: str, limit: int = 12) -> list[dict[str, Any]]:
    """Symbols and names of the universe that match ``query`` (symbol first, then names)."""
    wanted = str(query or "").strip().lower()
    if not wanted:
        return []
    scored: list[tuple[int, str]] = []
    for symbol, name in NAMES.items():
        bare = symbol.lower().split(".")[0].lstrip("^")
        if wanted in (symbol.lower(), bare):
            rank = 0
        elif symbol.lower().startswith(wanted) or bare.startswith(wanted):
            rank = 1
        elif name.lower().startswith(wanted):
            rank = 2
        elif wanted in name.lower():
            rank = 3
        else:
            continue
        scored.append((rank, symbol))
    scored.sort()
    return [
        {
            "symbol": symbol,
            "name": NAMES[symbol],
            "market": MARKET_OF[symbol],
            "index": is_index(symbol),
        }
        for _, symbol in scored[:limit]
    ]


# ---------------------------------------------------------------------------
# providers
# ---------------------------------------------------------------------------


class Provider(Protocol):
    name: str

    def fetch(self, symbol: str, *, full: bool) -> dict[str, Any]:
        """The symbol's data: ``{"symbol", "name", "currency", "exchange", "price", "previous_close", "market_time",
        "bars": [{"date", "open", "high", "low", "close", "volume"}]}`` (``full`` = two years of bars, otherwise
        the last few days). Raises LookupError for an unknown symbol and MarketUnavailable when offline."""
        ...


def _num(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


class YahooProvider:
    """Yahoo Finance's public chart endpoint (no key)."""

    name = "yahoo"

    def __init__(self, timeout: float = TIMEOUT) -> None:
        self.timeout = timeout

    def _get(self, url: str) -> dict[str, Any]:
        request = urllib.request.Request(
            url,
            headers={
                "User-Agent": USER_AGENT,
                "Accept": "application/json,text/plain,*/*",
                "Accept-Language": "en-US,en;q=0.9",
            },
        )
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:  # noqa: S310 - fixed https host
                return json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as error:
            if error.code == 404:
                raise LookupError("No stock with that symbol was found.") from None
            if error.code == 429:
                raise MarketUnavailable(
                    "The market data service is busy. Try again in a minute."
                ) from None
            raise MarketUnavailable(
                f"The market data service answered {error.code}. Try again later."
            ) from None
        except (urllib.error.URLError, TimeoutError, OSError, ValueError):
            raise MarketUnavailable(
                "Market data cannot be reached right now. Check the internet connection and try again."
            ) from None

    def fetch(self, symbol: str, *, full: bool) -> dict[str, Any]:
        url = CHART_URL.format(
            symbol=urllib.parse.quote(symbol, safe=""), range="2y" if full else "5d"
        )
        data = self._get(url)
        chart = data.get("chart") or {}
        results = chart.get("result") or []
        if not results:
            error = chart.get("error") or {}
            if str(error.get("code") or "").lower() in {"not found", "bad request"}:
                raise LookupError(f"No stock with the symbol {symbol} was found.")
            raise LookupError(f"No prices were found for {symbol}.")
        return parse_chart(symbol, results[0])


def parse_chart(symbol: str, result: dict[str, Any]) -> dict[str, Any]:
    """One Yahoo chart result -> the provider's shape (prices in the quote currency, GBp turned into GBP)."""
    meta = result.get("meta") or {}
    stamps = result.get("timestamp") or []
    quote = ((result.get("indicators") or {}).get("quote") or [{}])[0] or {}
    currency = str(meta.get("currency") or "USD")
    scale = 1.0
    if currency in {"GBp", "GBX"}:
        currency, scale = "GBP", 0.01
    elif currency == "ZAc":
        currency, scale = "ZAR", 0.01
    offset = int(meta.get("gmtoffset") or 0)
    bars: list[dict[str, Any]] = []
    for index, stamp in enumerate(stamps):

        def pick(key: str) -> float | None:
            values = quote.get(key) or []
            return _num(values[index]) if index < len(values) else None

        close = pick("close")
        if close is None or close <= 0:
            continue
        day = datetime.fromtimestamp(int(stamp) + offset, tz=timezone.utc).date()
        bar = {
            "date": day.isoformat(),
            "open": round((pick("open") or close) * scale, 4),
            "high": round((pick("high") or close) * scale, 4),
            "low": round((pick("low") or close) * scale, 4),
            "close": round(close * scale, 4),
            "volume": int(pick("volume") or 0),
        }
        if bars and bars[-1]["date"] == bar["date"]:
            bars[-1] = bar
        else:
            bars.append(bar)
    price = _num(meta.get("regularMarketPrice"))
    price = round(price * scale, 4) if price else (bars[-1]["close"] if bars else None)
    if price is None:
        raise LookupError(f"No prices were found for {symbol}.")
    previous = None
    if len(bars) >= 2:
        previous = bars[-2]["close"]
    if previous is None:
        raw = _num(meta.get("previousClose")) or _num(meta.get("chartPreviousClose"))
        previous = round(raw * scale, 4) if raw else price
    if bars:
        last = bars[-1]
        last["close"] = price
        last["high"] = max(last["high"], price)
        last["low"] = min(last["low"], price)
    market_time = meta.get("regularMarketTime")
    return {
        "symbol": symbol,
        "name": str(
            meta.get("longName") or meta.get("shortName") or NAMES.get(symbol) or symbol
        )[:120],
        "currency": currency,
        "exchange": str(meta.get("fullExchangeName") or meta.get("exchangeName") or "")[
            :40
        ],
        "kind": str(meta.get("instrumentType") or "").lower()[:20],
        "price": price,
        "previous_close": previous,
        "market_time": kit.iso(
            datetime.fromtimestamp(int(market_time), tz=timezone.utc)
        )
        if market_time
        else kit.now(),
        "bars": bars,
    }


# The deterministic offline market: the same prices for the same day on every computer.
STUB_START = date(2023, 1, 2)
STUB_EXTRA: dict[str, tuple[str, str]] = {
    "IBM": ("IBM", "USD"),
    "UBER": ("Uber", "USD"),
    "PYPL": ("PayPal", "USD"),
    "SHOP": ("Shopify", "USD"),
    "TATAPOWER.NS": ("Tata Power", "INR"),
    "ZOMATO.NS": ("Zomato", "INR"),
}
_stub_overrides: dict[str, dict[str, float]] = {}
_stub_cache: dict[tuple[str, str], list[dict[str, Any]]] = {}
_stub_lock = threading.Lock()


def stub_set(
    symbol: str, *, price: float | None = None, previous_close: float | None = None
) -> None:
    """Tests: make the offline market show ``price`` today (and ``previous_close`` yesterday)."""
    with _stub_lock:
        entry = _stub_overrides.setdefault(symbol, {})
        if price is not None:
            entry["price"] = float(price)
        if previous_close is not None:
            entry["previous_close"] = float(previous_close)


def stub_reset() -> None:
    with _stub_lock:
        _stub_overrides.clear()


def trading_days(start: date, end: date) -> list[date]:
    days, day = [], start
    while day <= end:
        if day.weekday() < 5:
            days.append(day)
        day += timedelta(days=1)
    return days


class StubProvider:
    """Seeded random-walk prices: each symbol has its own start price, drift and volatility."""

    name = "stub"

    def _currency(self, symbol: str) -> str:
        if symbol in STUB_EXTRA:
            return STUB_EXTRA[symbol][1]
        market = MARKET_OF.get(symbol)
        return MARKETS[market]["currency"] if market else "USD"

    def knows(self, symbol: str) -> bool:
        return symbol in NAMES or symbol in STUB_EXTRA

    def _walk(self, symbol: str, end: date) -> list[dict[str, Any]]:
        key = (symbol, end.isoformat())
        with _stub_lock:
            cached = _stub_cache.get(key)
        if cached is not None:
            return cached
        seed = int(hashlib.sha256(symbol.encode()).hexdigest()[:12], 16)
        rng = random.Random(seed)
        if is_index(symbol):
            price = {
                "^GSPC": 3900.0,
                "^IXIC": 11000.0,
                "^DJI": 33500.0,
                "^NSEI": 18200.0,
            }.get(symbol, 60000.0 if symbol == "^BSESN" else 42000.0)
            vol, drift = 0.009, 0.0004
        elif self._currency(symbol) == "INR":
            price = 150 + rng.random() * 3500
            vol, drift = 0.012 + rng.random() * 0.012, (rng.random() - 0.35) * 0.0016
        else:
            price = 25 + rng.random() * 500
            vol, drift = 0.012 + rng.random() * 0.016, (rng.random() - 0.35) * 0.0018
        base_volume = (2 + rng.random() * 40) * 1_000_000
        bars: list[dict[str, Any]] = []
        previous = price
        for day in trading_days(STUB_START, end):
            change = drift + vol * rng.gauss(0, 1)
            close = max(0.5, previous * math.exp(change))
            opened = previous * math.exp(vol * 0.35 * rng.gauss(0, 1))
            high = max(opened, close) * (1 + abs(rng.gauss(0, 1)) * vol * 0.45)
            low = min(opened, close) * (1 - abs(rng.gauss(0, 1)) * vol * 0.45)
            volume = int(
                base_volume * math.exp(0.35 * rng.gauss(0, 1) + abs(change) * 12)
            )
            bars.append(
                {
                    "date": day.isoformat(),
                    "open": round(opened, 2),
                    "high": round(high, 2),
                    "low": round(low, 2),
                    "close": round(close, 2),
                    "volume": volume,
                }
            )
            previous = close
        with _stub_lock:
            if len(_stub_cache) > 400:
                _stub_cache.clear()
            _stub_cache[key] = bars
        return bars

    def fetch(self, symbol: str, *, full: bool) -> dict[str, Any]:
        if not self.knows(symbol):
            raise LookupError(f"No stock with the symbol {symbol} was found.")
        end = date.fromisoformat(kit.today())
        bars = [dict(bar) for bar in self._walk(symbol, end)]
        if bars and date.fromisoformat(bars[0]["date"]) < end - timedelta(days=740):
            cut = (end - timedelta(days=740)).isoformat()
            bars = [bar for bar in bars if bar["date"] >= cut]
        with _stub_lock:
            override = dict(_stub_overrides.get(symbol) or {})
        if override and len(bars) >= 2:
            if "previous_close" in override:
                bars[-2]["close"] = round(override["previous_close"], 4)
                bars[-2]["high"] = max(bars[-2]["high"], bars[-2]["close"])
                bars[-2]["low"] = min(bars[-2]["low"], bars[-2]["close"])
            if "price" in override:
                last = bars[-1]
                last["close"] = round(override["price"], 4)
                last["high"] = max(last["open"], last["close"])
                last["low"] = min(last["open"], last["close"])
        if not full:
            bars = bars[-5:]
        name = NAMES.get(symbol) or STUB_EXTRA.get(symbol, (symbol, ""))[0]
        last = bars[-1]
        return {
            "symbol": symbol,
            "name": name,
            "currency": self._currency(symbol),
            "exchange": "Practice data"
            if not is_index(symbol)
            else "Practice data (index)",
            "kind": "index" if is_index(symbol) else "equity",
            "price": last["close"],
            "previous_close": bars[-2]["close"] if len(bars) >= 2 else last["close"],
            "market_time": f"{last['date']}T20:00:00Z",
            "bars": bars,
        }


_provider_override: Provider | None = None


def set_provider(provider: Provider | None) -> None:
    """Tests may install their own provider (None goes back to the environment's choice)."""
    global _provider_override
    _provider_override = provider


def provider() -> Provider:
    if _provider_override is not None:
        return _provider_override
    chosen = os.getenv("AIRA_MARKET_PROVIDER", "").strip().lower()
    if chosen == "stub" or (not chosen and os.getenv("PYTEST_CURRENT_TEST")):
        return _STUB
    return _YAHOO


_STUB = StubProvider()
_YAHOO = YahooProvider()


# ---------------------------------------------------------------------------
# the cache
# ---------------------------------------------------------------------------


def _row(connection: sqlite3.Connection, symbol: str) -> dict[str, Any] | None:
    return kit.fetch_one(
        connection, "SELECT * FROM stk_market WHERE symbol = ?", (symbol,)
    )


def _fresh_quote(row: dict[str, Any] | None, now: float) -> bool:
    return bool(
        row and row.get("quote_ts") and now - float(row["quote_ts"]) < QUOTE_TTL
    )


def _fresh_history(row: dict[str, Any] | None) -> bool:
    return bool(row and row.get("history_on") == kit.today() and row.get("history"))


def _merge_quote(
    history: list[dict[str, Any]], quick: dict[str, Any]
) -> list[dict[str, Any]]:
    """Put the latest days of a quick fetch over the saved history."""
    if not history:
        return list(quick["bars"])
    by_date = {bar["date"]: bar for bar in history}
    for bar in quick["bars"]:
        by_date[bar["date"]] = bar
    return [by_date[key] for key in sorted(by_date)]


def _save(
    connection: sqlite3.Connection,
    symbol: str,
    fetched: dict[str, Any],
    *,
    full: bool,
    previous: dict[str, Any] | None,
) -> dict[str, Any]:
    old_bars = kit.loads(previous.get("history"), []) if previous else []
    bars = fetched["bars"] if full else _merge_quote(old_bars, fetched)
    meta = {
        key: fetched[key]
        for key in (
            "name",
            "currency",
            "exchange",
            "kind",
            "price",
            "previous_close",
            "market_time",
        )
    }
    history_on = kit.today() if full else (previous or {}).get("history_on") or ""
    connection.execute(
        """INSERT INTO stk_market (symbol, meta, history, history_on, quote_ts, error, updated_at)
        VALUES (?, ?, ?, ?, ?, '', ?)
        ON CONFLICT(symbol) DO UPDATE SET meta = excluded.meta, history = excluded.history,
        history_on = excluded.history_on, quote_ts = excluded.quote_ts, error = '', updated_at = excluded.updated_at""",
        (symbol, kit.dumps(meta), kit.dumps(bars), history_on, time.time(), kit.now()),
    )
    return {
        "symbol": symbol,
        **meta,
        "bars": bars,
        "stale": False,
        "full": bool(history_on),
    }


def _from_row(row: dict[str, Any], *, stale: bool) -> dict[str, Any]:
    meta = kit.loads(row.get("meta"), {}) or {}
    return {
        "symbol": row["symbol"],
        **meta,
        "bars": kit.loads(row.get("history"), []) or [],
        "stale": stale,
        "full": bool(row.get("history_on")),
    }


def _fetch_many(
    wanted: list[tuple[str, bool]],
) -> dict[str, dict[str, Any] | BaseException]:
    chosen = provider()
    if len(wanted) == 1 or isinstance(chosen, StubProvider):
        out: dict[str, dict[str, Any] | BaseException] = {}
        for symbol, full in wanted:
            try:
                out[symbol] = chosen.fetch(symbol, full=full)
            except Exception as error:  # noqa: BLE001 - reported per symbol
                out[symbol] = error
        return out

    def one(item: tuple[str, bool]) -> tuple[str, dict[str, Any] | BaseException]:
        symbol, full = item
        try:
            return symbol, chosen.fetch(symbol, full=full)
        except Exception as error:  # noqa: BLE001
            return symbol, error

    with ThreadPoolExecutor(max_workers=min(WORKERS, len(wanted))) as pool:
        return dict(pool.map(one, wanted))


def load(
    connection: sqlite3.Connection,
    symbols: Iterable[str],
    *,
    history: bool = False,
    fetch: bool = True,
) -> dict[str, dict[str, Any]]:
    """The data of each symbol (``history`` = the full two years are needed), from the cache when fresh.

    Symbols that cannot be had at all are left out; ask :func:`series` for one symbol to get the reason."""
    from aira.suite.stocks import store

    store.ensure(connection)
    names = list(dict.fromkeys(symbols))
    now = time.time()
    rows = {name: _row(connection, name) for name in names}
    out: dict[str, dict[str, Any]] = {}
    wanted: list[tuple[str, bool]] = []
    for name in names:
        row = rows[name]
        need_full = history and not _fresh_history(row)
        if (
            row
            and not need_full
            and _fresh_quote(row, now)
            and (row.get("history") or not history)
        ):
            out[name] = _from_row(row, stale=False)
            continue
        if not fetch:
            if row:
                out[name] = _from_row(row, stale=True)
            continue
        # The first look at a symbol brings its whole history (the 52-week range and the log need it).
        wanted.append((name, need_full or not (row and row.get("history_on"))))
    if wanted:
        fetched = _fetch_many(wanted)
        for name, full in wanted:
            result = fetched.get(name)
            row = rows[name]
            if isinstance(result, dict):
                out[name] = _save(connection, name, result, full=full, previous=row)
            elif row:
                out[name] = _from_row(row, stale=True)
            if isinstance(result, BaseException) and not isinstance(
                result, LookupError
            ):
                suite.logger.info("Market data for %s: %s", name, result)
        connection.commit()
    return out


def series(
    connection: sqlite3.Connection, symbol: Any, *, fetch: bool = True
) -> dict[str, Any]:
    """One symbol with its full daily history; LookupError for an unknown symbol, MarketUnavailable offline."""
    symbol = clean_symbol(symbol)
    found = load(connection, [symbol], history=True, fetch=fetch).get(symbol)
    if found:
        return found
    if not fetch:
        raise MarketUnavailable("No saved prices for this stock yet.")
    try:
        result = provider().fetch(symbol, full=True)
    except LookupError:
        raise LookupError(
            f"No stock with the symbol {symbol} was found. Check the spelling; Indian shares end in .NS (TCS.NS)."
        ) from None
    from aira.suite.stocks import store

    store.ensure(connection)
    saved = _save(
        connection, symbol, result, full=True, previous=_row(connection, symbol)
    )
    connection.commit()
    return saved


def quote_of(data: dict[str, Any]) -> dict[str, Any]:
    """The headline numbers of a symbol's data (prices in cents of its currency)."""
    bars = data.get("bars") or []
    price = float(data.get("price") or (bars[-1]["close"] if bars else 0))
    previous = float(data.get("previous_close") or price)
    change = price - previous
    year = bars[-252:]
    high52 = max((bar["high"] for bar in year), default=price)
    low52 = min((bar["low"] for bar in year), default=price)
    last = bars[-1] if bars else {}
    return {
        "symbol": data["symbol"],
        "name": data.get("name") or NAMES.get(data["symbol"]) or data["symbol"],
        "currency": data.get("currency") or "USD",
        "exchange": data.get("exchange") or "",
        "market": market_of(data["symbol"]),
        "index": is_index(data["symbol"]),
        "price_cents": to_cents(price),
        "previous_close_cents": to_cents(previous),
        "change_cents": to_cents(price) - to_cents(previous),
        "change_pct": round(change / previous * 100, 2) if previous else 0.0,
        "open_cents": to_cents(last.get("open", price)),
        "day_high_cents": to_cents(last.get("high", price)),
        "day_low_cents": to_cents(last.get("low", price)),
        "volume": int(last.get("volume") or 0),
        "high52_cents": to_cents(max(high52, price)),
        "low52_cents": to_cents(min(low52, price)),
        "market_time": data.get("market_time") or "",
        "stale": bool(data.get("stale")),
    }


def quotes(
    connection: sqlite3.Connection, symbols: Iterable[str], *, fetch: bool = True
) -> dict[str, dict[str, Any]]:
    return {
        symbol: quote_of(data)
        for symbol, data in load(connection, symbols, fetch=fetch).items()
    }


def to_cents(price: float) -> int:
    return int(round(float(price) * 100))


def provider_name() -> str:
    return provider().name
