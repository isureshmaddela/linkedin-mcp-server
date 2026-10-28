"""Stocks: the rules. Markets and each stock's daily log, predictions with their track record, practice and real
portfolios, watchlists and alerts, and earning from published stock ideas.

Who may do what:

* Everyone (children too) may look at the markets, keep watchlists and alerts, make predictions and trade in a
  **practice** portfolio with virtual cash.
* **Real holdings**, publishing **ideas**, the analyst page and paid subscriptions are for adults
  (:func:`aira.suite.life.is_adult`): they are about real money.
* Everything belongs to the person who made it (``owner_id``); the practice league shows other people's name and
  return only.

Aira never places real trades: real holdings are a record the person keeps, and payments from subscribers go
through the analyst's own payment link and count only when the analyst confirms them.
"""

from __future__ import annotations

import hashlib
import hmac
import math
import secrets
import sqlite3
from collections.abc import Iterable
from datetime import date, timedelta
from decimal import Decimal, InvalidOperation
from typing import Any

from aira import suite
from aira.suite import kit, life, public
from aira.suite.stocks import market, predict, store

APP = store.APP
DISCLAIMER = (
    "This is not financial advice. Forecasts are estimates from past prices and are often wrong; "
    "the track record shows how often. Aira never buys or sells real shares."
)
RANGES = {"1m": 21, "3m": 63, "6m": 126, "1y": 252, "2y": 520}
PRACTICE_DEFAULTS = {"USD": 100_000_00, "INR": 10_00_000_00}
PRACTICE_MIN, PRACTICE_MAX = 1_000_00, 10_000_000_00
QTY_EPS = 1e-9
IDEA_SIDES = ("buy", "sell")
IDEA_STATUSES = ("open", "target_hit", "stopped", "expired", "closed")
ALERT_KINDS = ("above", "below", "move")
TXN_KINDS = ("buy", "sell", "dividend")
CODE_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
PAGE_KIND = "analyst"


# ---------------------------------------------------------------------------
# small helpers
# ---------------------------------------------------------------------------


def today() -> date:
    return date.fromisoformat(kit.today())


def require_adult(me: dict[str, Any], what: str = "This") -> None:
    if not life.is_adult(me):
        raise PermissionError(
            f"{what} is for adults: it is about real money. You can use the practice portfolio."
        )


def quantity(value: Any, name: str = "the number of shares") -> float:
    text = str(value if value is not None else "").strip().replace(",", "")
    if not text:
        raise ValueError(f"Enter {name}.")
    try:
        number = Decimal(text)
    except InvalidOperation:
        raise ValueError(
            f"{name[:1].upper()}{name[1:]} must be a number like 10 or 2.5."
        ) from None
    if not number.is_finite() or number <= 0:
        raise ValueError(f"{name[:1].upper()}{name[1:]} must be more than 0.")
    if number > 1_000_000_000:
        raise ValueError(f"{name[:1].upper()}{name[1:]} is too large.")
    rounded = round(float(number), 6)
    if rounded <= 0:  # 0.0000001 would be recorded as 0 shares
        raise ValueError(
            f"{name[:1].upper()}{name[1:]} is too small: the smallest is 0.000001."
        )
    return rounded


def price_input(
    raw: dict[str, Any], key: str, name: str, *, required: bool = True
) -> int:
    """A price as ``<key>`` (text a person typed, "182.50") or ``<key>_cents`` (an integer)."""
    if raw.get(f"{key}_cents") not in (None, ""):
        value = kit.cents_value(raw.get(f"{key}_cents"), name)
    elif raw.get(key) not in (None, ""):
        value = kit.cents(raw.get(key), name)
    elif required:
        raise ValueError(f"Enter {name}.")
    else:
        return 0
    if required and value <= 0:
        raise ValueError(f"{name[:1].upper()}{name[1:]} must be more than 0.")
    return value


def fmt_price(cents: int, currency: str, *, index: bool = False) -> str:
    if index:
        return f"{cents / 100:,.2f}"
    return kit.fmt_money(int(cents), currency)


def signed_money(cents: int, currency: str) -> str:
    sign = "+" if cents > 0 else "-" if cents < 0 else ""
    return f"{sign}{kit.fmt_money(abs(int(cents)), currency)}"


def arrow(value: float) -> str:
    return "▲" if value > 0 else "▼" if value < 0 else "■"


def pct(value: float | None) -> str:
    return "n/a" if value is None else f"{value:+.2f}%"


def _quote(
    connection: sqlite3.Connection, symbol: Any, *, fetch: bool = True
) -> dict[str, Any]:
    return market.quote_of(market.series(connection, symbol, fetch=fetch))


def _qty_text(value: float) -> str:
    return f"{value:,.6f}".rstrip("0").rstrip(".")


# ---------------------------------------------------------------------------
# markets
# ---------------------------------------------------------------------------


def markets(connection: sqlite3.Connection, market_id: str) -> dict[str, Any]:
    spec = market.MARKETS.get(market_id)
    if spec is None:
        raise ValueError(f"Choose a market: {', '.join(market.MARKETS)}.")
    stocks = market.universe(market_id)
    indices = [symbol for symbol, _ in spec["indices"]]
    found = market.quotes(connection, [*indices, *stocks])
    if not found:
        raise market.MarketUnavailable(
            "Market data cannot be reached right now and nothing is saved yet. Check the internet connection."
        )
    rows = [found[s] for s in stocks if s in found]
    for row in rows:
        high, low, price = row["high52_cents"], row["low52_cents"], row["price_cents"]
        row["from_high_pct"] = round((price / high - 1) * 100, 2) if high else 0.0
        row["from_low_pct"] = round((price / low - 1) * 100, 2) if low else 0.0
    gainers = sorted(
        [r for r in rows if r["change_pct"] > 0], key=lambda r: -r["change_pct"]
    )
    losers = sorted(
        [r for r in rows if r["change_pct"] < 0], key=lambda r: r["change_pct"]
    )
    highs = sorted(
        [r for r in rows if r["from_high_pct"] >= -2], key=lambda r: -r["from_high_pct"]
    )
    lows = sorted(
        [r for r in rows if r["from_low_pct"] <= 2], key=lambda r: r["from_low_pct"]
    )
    times = [r["market_time"] for r in found.values() if r.get("market_time")]
    return {
        "market": market_id,
        "label": spec["label"],
        "currency": spec["currency"],
        "markets": [
            {"id": key, "label": value["label"]}
            for key, value in market.MARKETS.items()
        ],
        "indices": [found[s] for s in indices if s in found],
        "gainers": gainers[:6],
        "losers": losers[:6],
        "active": sorted(rows, key=lambda r: -r["volume"])[:6],
        "highs": highs[:6],
        "lows": lows[:6],
        "stocks": sorted(rows, key=lambda r: r["name"].lower()),
        "breadth": {
            "up": sum(1 for r in rows if r["change_pct"] > 0),
            "down": sum(1 for r in rows if r["change_pct"] < 0),
            "flat": sum(1 for r in rows if r["change_pct"] == 0),
        },
        "as_of": max(times) if times else "",
        "stale": any(r["stale"] for r in found.values()),
        "missing": [s for s in [*indices, *stocks] if s not in found],
        "provider": market.provider_name(),
        "disclaimer": DISCLAIMER,
    }


def search(connection: sqlite3.Connection, query: str) -> dict[str, Any]:
    text = kit.text(query, "a search", max_len=40)
    results = market.search_universe(text)
    if text and not any(r["symbol"] == text.upper() for r in results):
        try:
            symbol = market.clean_symbol(text)
        except ValueError:
            symbol = ""
        if symbol and " " not in text:
            try:
                data = market.series(connection, symbol)
                results.insert(
                    0,
                    {
                        "symbol": symbol,
                        "name": data.get("name") or symbol,
                        "market": market.market_of(symbol),
                        "index": market.is_index(symbol),
                        "found": True,
                    },
                )
            except (LookupError, market.MarketUnavailable):
                pass
    return {"query": text, "results": results[:12]}


# ---------------------------------------------------------------------------
# one stock: quote, chart, daily log
# ---------------------------------------------------------------------------


def _bars_in_range(bars: list[dict[str, Any]], range_id: str) -> list[dict[str, Any]]:
    count = RANGES.get(range_id)
    if count is None:
        raise ValueError(f"Choose a range: {', '.join(RANGES)}.")
    return bars[-count:]


def daily_log(bars: list[dict[str, Any]], range_id: str = "1y") -> list[dict[str, Any]]:
    """Every day of the range with its change from the day before (newest first)."""
    count = RANGES.get(range_id, 252)
    start = max(0, len(bars) - count)
    out = []
    for index in range(start, len(bars)):
        bar = bars[index]
        previous = bars[index - 1]["close"] if index > 0 else bar["open"]
        change = bar["close"] - previous
        out.append(
            {
                "date": bar["date"],
                "open_cents": market.to_cents(bar["open"]),
                "high_cents": market.to_cents(bar["high"]),
                "low_cents": market.to_cents(bar["low"]),
                "close_cents": market.to_cents(bar["close"]),
                "change_cents": market.to_cents(bar["close"])
                - market.to_cents(previous),
                "change_pct": round(change / previous * 100, 2) if previous else 0.0,
                "range_pct": round((bar["high"] - bar["low"]) / bar["low"] * 100, 2)
                if bar["low"]
                else 0.0,
                "volume": int(bar["volume"]),
            }
        )
    return out[::-1]


def log_summary(log: list[dict[str, Any]]) -> dict[str, Any]:
    if not log:
        return {
            "best": [],
            "worst": [],
            "high": None,
            "low": None,
            "return_pct": None,
            "days": 0,
            "up_days": 0,
        }
    by_change = sorted(log, key=lambda r: r["change_pct"])
    high = max(log, key=lambda r: r["high_cents"])
    low = min(log, key=lambda r: r["low_cents"])
    oldest, newest = log[-1], log[0]
    first = oldest["close_cents"] - oldest["change_cents"]
    return {
        "best": [r for r in by_change[::-1][:5] if r["change_pct"] > 0],
        "worst": [r for r in by_change[:5] if r["change_pct"] < 0],
        "high": {"date": high["date"], "price_cents": high["high_cents"]},
        "low": {"date": low["date"], "price_cents": low["low_cents"]},
        "return_pct": round((newest["close_cents"] / first - 1) * 100, 2)
        if first
        else None,
        "days": len(log),
        "up_days": sum(1 for r in log if r["change_pct"] > 0),
        "down_days": sum(1 for r in log if r["change_pct"] < 0),
        "biggest_range": max(log, key=lambda r: r["range_pct"]),
    }


def chart(bars: list[dict[str, Any]], range_id: str) -> dict[str, Any]:
    closes = [bar["close"] for bar in bars]
    count = len(_bars_in_range(bars, range_id))
    cut = len(bars) - count
    averages = {f"sma{n}": predict.sma(closes, n)[cut:] for n in (20, 50, 200)}
    return {
        "range": range_id,
        "dates": [bar["date"] for bar in bars[cut:]],
        "close": closes[cut:],
        "volume": [bar["volume"] for bar in bars[cut:]],
        **{
            key: [round(v, 4) if v is not None else None for v in values]
            for key, values in averages.items()
        },
    }


def stock(
    connection: sqlite3.Connection,
    me: dict[str, Any],
    symbol: str,
    range_id: str = "1y",
) -> dict[str, Any]:
    data = market.series(connection, symbol)
    symbol = data["symbol"]
    bars = data["bars"]
    if range_id not in RANGES:
        raise ValueError(f"Choose a range: {', '.join(RANGES)}.")
    log = daily_log(bars, range_id)
    quote = market.quote_of(data)
    watch = kit.fetch_all(
        connection,
        "SELECT i.id, i.list_id, l.name FROM stk_watch_items i JOIN stk_watchlists l ON l.id = i.list_id "
        "WHERE i.owner_id = ? AND i.symbol = ?",
        (me["id"], symbol),
    )
    positions = []
    for account in _accounts(connection, me):
        summary = account_summary(
            connection, account, fetch=False, quotes={symbol: quote}
        )
        for holding in summary["holdings"]:
            if holding["symbol"] == symbol:
                positions.append(
                    {"account_id": account["id"], "kind": account["kind"], **holding}
                )
    return {
        "quote": quote,
        "chart": chart(bars, range_id),
        "log": log,
        "summary": log_summary(log),
        "watching": watch,
        "positions": positions,
        "alerts": [a for a in list_alerts(connection, me) if a["symbol"] == symbol],
        "practice_currency_ready": bool(
            kit.fetch_one(
                connection,
                "SELECT 1 FROM stk_accounts WHERE owner_id = ? AND kind = 'practice' AND currency = ?",
                (me["id"], quote["currency"]),
            )
        ),
        "adult": life.is_adult(me),
        "disclaimer": DISCLAIMER,
    }


def log_csv(
    connection: sqlite3.Connection, symbol: str, range_id: str = "1y"
) -> tuple[bytes, str]:
    data = market.series(connection, symbol)
    rows = []
    for row in daily_log(data["bars"], range_id if range_id in RANGES else "1y"):
        rows.append(
            {
                "date": row["date"],
                "open": f"{row['open_cents'] / 100:.2f}",
                "high": f"{row['high_cents'] / 100:.2f}",
                "low": f"{row['low_cents'] / 100:.2f}",
                "close": f"{row['close_cents'] / 100:.2f}",
                "change": f"{row['change_cents'] / 100:.2f}",
                "change_pct": f"{row['change_pct']:.2f}",
                "volume": row["volume"],
                "currency": data.get("currency") or "",
            }
        )
    name = f"{data['symbol'].replace('^', '').replace('&', '_')}-daily-log.csv"
    return kit.csv_bytes(
        rows,
        (
            "date",
            "open",
            "high",
            "low",
            "close",
            "change",
            "change_pct",
            "volume",
            "currency",
        ),
    ), name


# ---------------------------------------------------------------------------
# predictions (the models) and the person's own predictions
# ---------------------------------------------------------------------------


def prediction(
    connection: sqlite3.Connection, symbol: str, *, explain: bool = False
) -> dict[str, Any]:
    data = market.series(connection, symbol)
    closes = [bar["close"] for bar in data["bars"]]
    if len(closes) < 60:
        raise ValueError(
            "Not enough price history yet for a prediction (at least 60 trading days)."
        )
    forecasts = {h: predict.forecast(closes, h) for h in (5, 20)}
    tests = {h: predict.backtest(closes, h) for h in (5, 20)}
    sig = predict.signal(closes, forecasts[20])
    quote = market.quote_of(data)
    result = {
        "symbol": data["symbol"],
        "name": quote["name"],
        "currency": quote["currency"],
        "index": quote["index"],
        "quote": quote,
        "indicators": predict.indicators(closes),
        "forecasts": {str(h): f for h, f in forecasts.items()},
        "path": predict.path(closes, 20),
        "history": {
            "dates": [b["date"] for b in data["bars"][-60:]],
            "close": closes[-60:],
        },
        "backtest": {str(h): t for h, t in tests.items()},
        "signal": sig,
        "disclaimer": DISCLAIMER,
    }
    result["explanation"] = explanation(result, use_model=explain)
    return result


def explanation(result: dict[str, Any], *, use_model: bool) -> dict[str, Any]:
    f20 = result["forecasts"]["20"]
    t20 = result["backtest"]["20"]["models"]["ensemble"]
    naive = result["backtest"]["20"]["models"]["naive"]
    money = (
        (lambda v: f"{v:,.2f}")
        if result["index"]
        else (lambda v: kit.fmt_money(market.to_cents(v), result["currency"]))
    )
    reasons = " ".join(r["text"] for r in result["signal"]["reasons"] if r["points"])
    hit = t20["direction_hit_pct"]
    template = (
        f"{result['name']} looks {result['signal']['label']} on its price chart. {reasons} "
        f"The models expect about {f20['change_pct']:+.1f}% over the next 20 trading days; if the past is a guide, "
        f"8 times in 10 the price would end between {money(f20['low'])} and {money(f20['high'])}. "
        + (
            f"In {t20['tests']} past tests the ensemble called the direction right {hit:.0f}% of the time, "
            f"with an average miss of {t20['mape_pct']:.1f}% (doing nothing missed by {naive['mape_pct']:.1f}%). "
            if hit is not None
            and t20["mape_pct"] is not None
            and naive["mape_pct"] is not None
            else ""
        )
        + "This is not financial advice."
    ).replace("  ", " ")
    if not use_model:
        return {"text": template, "source": "template"}
    prompt = (
        "You explain a stock's technical picture to a beginner in one short paragraph (at most 90 words), in plain "
        "words, without hype and without telling them to buy or sell. End with: This is not financial advice.\n"
        f"Facts: {template}\nIndicators: {kit.dumps(result['indicators'])}\n"
        'Answer as JSON: {"text": "the paragraph"}'
    )
    answer = kit.draft(prompt, {"text": template})
    text = str(answer.get("text") or template).strip() or template
    if "not financial advice" not in text.lower():
        text = f"{text} This is not financial advice."
    return {"text": text[:1200], "source": answer.get("source", "template")}


def _business_days(start: date, end: date) -> int:
    days, day = 0, start
    while day < end:
        day += timedelta(days=1)
        if day.weekday() < 5:
            days += 1
    return days


def create_prediction(
    connection: sqlite3.Connection, me: dict[str, Any], raw: dict[str, Any]
) -> dict[str, Any]:
    data = market.series(connection, raw.get("symbol"))
    quote = market.quote_of(data)
    direction = kit.choice(raw.get("direction"), "the direction", ("up", "down"))
    target_date = kit.day(raw.get("target_date"), "the date to check")
    if not target_date and raw.get("days") not in (None, ""):
        target_date = (
            today()
            + timedelta(
                days=kit.integer(
                    raw.get("days"), "the number of days", minimum=1, maximum=366
                )
            )
        ).isoformat()
    if not target_date:
        raise ValueError("Choose the date when Aira should check your prediction.")
    when = date.fromisoformat(target_date)
    if when <= today():
        raise ValueError("Choose a date after today.")
    if when > today() + timedelta(days=366):
        raise ValueError("Choose a date within a year.")
    target = price_input(raw, "target", "the target price", required=False)
    if target and direction == "up" and target <= quote["price_cents"]:
        raise ValueError("For a rise, the target must be above today's price.")
    if target and direction == "down" and target >= quote["price_cents"]:
        raise ValueError("For a fall, the target must be below today's price.")
    closes = [bar["close"] for bar in data["bars"]]
    horizon = max(1, _business_days(today(), when))
    model = (
        market.to_cents(predict.model_forecast(closes, horizon, "ensemble"))
        if len(closes) >= 30
        else 0
    )
    open_count = kit.count(
        connection,
        "SELECT COUNT(*) FROM stk_predictions WHERE owner_id = ? AND status = 'open'",
        (me["id"],),
    )
    if open_count >= 200:
        raise ValueError(
            "You have 200 open predictions. Wait for some to be checked first."
        )
    prediction_id = kit.new_id()
    connection.execute(
        """INSERT INTO stk_predictions (id, owner_id, symbol, currency, direction, made_on, target_date, base_cents,
        target_cents, model_cents, note, status, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'open', ?)""",
        (
            prediction_id,
            me["id"],
            quote["symbol"],
            quote["currency"],
            direction,
            kit.today(),
            target_date,
            quote["price_cents"],
            target,
            model,
            kit.text(raw.get("note"), "a note", max_len=300),
            kit.now(),
        ),
    )
    connection.commit()
    return _prediction_view(
        kit.require_row(connection, "stk_predictions", prediction_id, "prediction")
    )


def _prediction_view(row: dict[str, Any]) -> dict[str, Any]:
    row = dict(row)
    for key in ("hit", "model_hit"):
        row[key] = None if row[key] is None else bool(row[key])
    row["name"] = market.NAMES.get(row["symbol"], row["symbol"])
    row["days_left"] = (date.fromisoformat(row["target_date"]) - today()).days
    if row["model_cents"] and row["base_cents"]:
        row["model_direction"] = (
            "up" if row["model_cents"] > row["base_cents"] else "down"
        )
    else:
        row["model_direction"] = ""
    return row


def score_prediction(
    row: dict[str, Any], bars: list[dict[str, Any]], on: date
) -> dict[str, Any] | None:
    """The scored values once the target date has passed (None while it is still open)."""
    if date.fromisoformat(row["target_date"]) >= on:
        return None
    eligible = [bar for bar in bars if bar["date"] <= row["target_date"]]
    if not eligible:
        return None
    actual = market.to_cents(eligible[-1]["close"])
    rose = actual > row["base_cents"]
    hit = rose if row["direction"] == "up" else actual < row["base_cents"]
    model_hit = None
    if row["model_cents"]:
        model_hit = (row["model_cents"] > row["base_cents"]) == rose
    error = (
        abs(row["target_cents"] - actual) / actual * 100
        if row["target_cents"] and actual
        else None
    )
    return {
        "actual_cents": actual,
        "hit": 1 if hit else 0,
        "model_hit": None if model_hit is None else (1 if model_hit else 0),
        "error_pct": round(error, 2) if error is not None else None,
    }


def score_predictions(
    connection: sqlite3.Connection, owner_id: str = "", *, fetch: bool = True
) -> int:
    clauses = ["status = 'open'", "target_date < ?"]
    params: list[Any] = [kit.today()]
    if owner_id:
        clauses.append("owner_id = ?")
        params.append(owner_id)
    due = kit.fetch_all(
        connection,
        f"SELECT * FROM stk_predictions WHERE {' AND '.join(clauses)}",
        params,
    )  # noqa: S608
    if not due:
        return 0
    data = market.load(
        connection, {row["symbol"] for row in due}, history=True, fetch=fetch
    )
    scored = 0
    for row in due:
        found = data.get(row["symbol"])
        if not found:
            continue
        result = score_prediction(row, found["bars"], today())
        if result is None:
            continue
        kit.update_row(
            connection,
            "stk_predictions",
            row["id"],
            {**result, "status": "scored", "scored_at": kit.now()},
        )
        scored += 1
    connection.commit()
    return scored


def list_predictions(
    connection: sqlite3.Connection, me: dict[str, Any]
) -> dict[str, Any]:
    score_predictions(connection, me["id"])
    rows = [
        _prediction_view(row)
        for row in kit.fetch_all(
            connection,
            "SELECT * FROM stk_predictions WHERE owner_id = ? ORDER BY status = 'open' DESC, target_date, created_at DESC",
            (me["id"],),
        )
    ]
    scored = [r for r in rows if r["status"] == "scored"]
    model_scored = [r for r in scored if r["model_hit"] is not None]
    open_rows = [r for r in rows if r["status"] == "open"]
    if open_rows:
        quotes = market.quotes(connection, {r["symbol"] for r in open_rows}, fetch=True)
        for row in open_rows:
            q = quotes.get(row["symbol"])
            if q:
                row["now_cents"] = q["price_cents"]
                now, base = q["price_cents"], row["base_cents"]
                row["so_far"] = (
                    "even"
                    if now == base
                    else "right"
                    if (now > base) == (row["direction"] == "up")
                    else "wrong"
                )
    return {
        "predictions": rows,
        "stats": {
            "open": len(open_rows),
            "scored": len(scored),
            "hits": sum(1 for r in scored if r["hit"]),
            "hit_pct": round(sum(1 for r in scored if r["hit"]) / len(scored) * 100, 1)
            if scored
            else None,
            "model_hit_pct": round(
                sum(1 for r in model_scored if r["model_hit"])
                / len(model_scored)
                * 100,
                1,
            )
            if model_scored
            else None,
        },
    }


def delete_prediction(
    connection: sqlite3.Connection, me: dict[str, Any], prediction_id: str
) -> None:
    row = kit.fetch_one(
        connection,
        "SELECT * FROM stk_predictions WHERE id = ? AND owner_id = ?",
        (prediction_id, me["id"]),
    )
    if not row:
        raise LookupError("That prediction was not found.")
    connection.execute("DELETE FROM stk_predictions WHERE id = ?", (prediction_id,))
    connection.commit()


def predictions_csv(connection: sqlite3.Connection, me: dict[str, Any]) -> bytes:
    rows = list_predictions(connection, me)["predictions"]
    out = [
        {
            "symbol": r["symbol"],
            "direction": r["direction"],
            "made_on": r["made_on"],
            "check_on": r["target_date"],
            "price_then": f"{r['base_cents'] / 100:.2f}",
            "target": f"{r['target_cents'] / 100:.2f}" if r["target_cents"] else "",
            "model_said": r["model_direction"],
            "status": r["status"],
            "price_on_date": f"{r['actual_cents'] / 100:.2f}"
            if r["actual_cents"]
            else "",
            "you_were": "" if r["hit"] is None else ("right" if r["hit"] else "wrong"),
            "currency": r["currency"],
            "note": r["note"],
        }
        for r in rows
    ]
    return kit.csv_bytes(
        out,
        (
            "symbol",
            "direction",
            "made_on",
            "check_on",
            "price_then",
            "target",
            "model_said",
            "status",
            "price_on_date",
            "you_were",
            "currency",
            "note",
        ),
    )


# ---------------------------------------------------------------------------
# watchlists
# ---------------------------------------------------------------------------


def _default_list(connection: sqlite3.Connection, me: dict[str, Any]) -> dict[str, Any]:
    found = kit.fetch_one(
        connection,
        "SELECT * FROM stk_watchlists WHERE owner_id = ? ORDER BY position, created_at LIMIT 1",
        (me["id"],),
    )
    if found:
        return found
    list_id = kit.new_id()
    connection.execute(
        "INSERT INTO stk_watchlists (id, owner_id, name, position, created_at) VALUES (?, ?, ?, 0, ?)",
        (list_id, me["id"], "My watchlist", kit.now()),
    )
    connection.commit()
    return kit.require_row(connection, "stk_watchlists", list_id, "watchlist")


def _own_list(
    connection: sqlite3.Connection, me: dict[str, Any], list_id: str
) -> dict[str, Any]:
    found = kit.fetch_one(
        connection,
        "SELECT * FROM stk_watchlists WHERE id = ? AND owner_id = ?",
        (list_id, me["id"]),
    )
    if not found:
        raise LookupError("That watchlist was not found.")
    return found


def create_watchlist(
    connection: sqlite3.Connection, me: dict[str, Any], raw: dict[str, Any]
) -> dict[str, Any]:
    name = kit.text(raw.get("name"), "a name for the list", max_len=60, required=True)
    if (
        kit.count(
            connection,
            "SELECT COUNT(*) FROM stk_watchlists WHERE owner_id = ?",
            (me["id"],),
        )
        >= 20
    ):
        raise ValueError("You can keep up to 20 watchlists.")
    list_id = kit.new_id()
    position = kit.count(
        connection,
        "SELECT COUNT(*) FROM stk_watchlists WHERE owner_id = ?",
        (me["id"],),
    )
    connection.execute(
        "INSERT INTO stk_watchlists (id, owner_id, name, position, created_at) VALUES (?, ?, ?, ?, ?)",
        (list_id, me["id"], name, position, kit.now()),
    )
    connection.commit()
    return kit.require_row(connection, "stk_watchlists", list_id, "watchlist")


def rename_watchlist(
    connection: sqlite3.Connection,
    me: dict[str, Any],
    list_id: str,
    raw: dict[str, Any],
) -> dict[str, Any]:
    _own_list(connection, me, list_id)
    kit.update_row(
        connection,
        "stk_watchlists",
        list_id,
        {
            "name": kit.text(
                raw.get("name"), "a name for the list", max_len=60, required=True
            )
        },
    )
    connection.commit()
    return kit.require_row(connection, "stk_watchlists", list_id, "watchlist")


def delete_watchlist(
    connection: sqlite3.Connection, me: dict[str, Any], list_id: str
) -> None:
    _own_list(connection, me, list_id)
    connection.execute("DELETE FROM stk_watch_items WHERE list_id = ?", (list_id,))
    connection.execute("DELETE FROM stk_watchlists WHERE id = ?", (list_id,))
    connection.commit()


def watch(
    connection: sqlite3.Connection, me: dict[str, Any], raw: dict[str, Any]
) -> dict[str, Any]:
    data = market.series(connection, raw.get("symbol"))
    target = (
        _own_list(connection, me, str(raw["list_id"]))
        if raw.get("list_id")
        else _default_list(connection, me)
    )
    if (
        kit.count(
            connection,
            "SELECT COUNT(*) FROM stk_watch_items WHERE list_id = ?",
            (target["id"],),
        )
        >= 100
    ):
        raise ValueError("A watchlist can hold up to 100 stocks.")
    connection.execute(
        "INSERT OR IGNORE INTO stk_watch_items (id, list_id, owner_id, symbol, note, added_at) VALUES (?, ?, ?, ?, ?, ?)",
        (
            kit.new_id(),
            target["id"],
            me["id"],
            data["symbol"],
            kit.text(raw.get("note"), "a note", max_len=200),
            kit.now(),
        ),
    )
    connection.commit()
    item = kit.fetch_one(
        connection,
        "SELECT * FROM stk_watch_items WHERE list_id = ? AND symbol = ?",
        (target["id"], data["symbol"]),
    )
    return {"item": item, "list": target}


def unwatch(connection: sqlite3.Connection, me: dict[str, Any], item_id: str) -> None:
    found = kit.fetch_one(
        connection,
        "SELECT * FROM stk_watch_items WHERE id = ? AND owner_id = ?",
        (item_id, me["id"]),
    )
    if not found:
        raise LookupError("That stock is not on your watchlist.")
    connection.execute("DELETE FROM stk_watch_items WHERE id = ?", (item_id,))
    connection.commit()


def watchlists(
    connection: sqlite3.Connection, me: dict[str, Any], *, fetch: bool = True
) -> dict[str, Any]:
    lists = kit.fetch_all(
        connection,
        "SELECT * FROM stk_watchlists WHERE owner_id = ? ORDER BY position, created_at",
        (me["id"],),
    )
    items = kit.fetch_all(
        connection,
        "SELECT * FROM stk_watch_items WHERE owner_id = ? ORDER BY added_at",
        (me["id"],),
    )
    quotes = (
        market.quotes(connection, {i["symbol"] for i in items}, fetch=fetch)
        if items
        else {}
    )
    out = []
    for entry in lists:
        rows = []
        for item in items:
            if item["list_id"] != entry["id"]:
                continue
            q = quotes.get(item["symbol"]) or {
                "symbol": item["symbol"],
                "name": market.NAMES.get(item["symbol"], item["symbol"]),
                "missing": True,
            }
            rows.append(
                {
                    **q,
                    "item_id": item["id"],
                    "note": item["note"],
                    "added_at": item["added_at"],
                }
            )
        out.append({**entry, "items": rows})
    return {"lists": out, "stale": any(q.get("stale") for q in quotes.values())}


# ---------------------------------------------------------------------------
# alerts
# ---------------------------------------------------------------------------


def _alert_text(alert: dict[str, Any], currency: str = "USD") -> str:
    index = market.is_index(alert["symbol"])
    if alert["kind"] == "above":
        return f"{alert['symbol']} rises above {fmt_price(alert['price_cents'], currency, index=index)}"
    if alert["kind"] == "below":
        return f"{alert['symbol']} falls below {fmt_price(alert['price_cents'], currency, index=index)}"
    return f"{alert['symbol']} moves {alert['move_pct']:g}% or more in a day"


def _alert_view(row: dict[str, Any], currency: str = "") -> dict[str, Any]:
    out = dict(row)
    out["active"] = bool(row["active"])
    code = currency or ("INR" if market.market_of(row["symbol"]) == "in" else "USD")
    out["currency"] = code
    out["text"] = _alert_text(row, code)
    out["triggered"] = bool(row.get("triggered_at"))
    return out


def create_alert(
    connection: sqlite3.Connection, me: dict[str, Any], raw: dict[str, Any]
) -> dict[str, Any]:
    quote = _quote(connection, raw.get("symbol"))
    kind = kit.choice(raw.get("kind"), "the kind of alert", ALERT_KINDS)
    price_cents, move = 0, 0.0
    if kind in ("above", "below"):
        price_cents = price_input(raw, "price", "the alert price")
    else:
        try:
            move = float(str(raw.get("move_pct") or "").strip().rstrip("%"))
        except ValueError:
            raise ValueError(
                "Enter the daily move in percent, for example 3."
            ) from None
        if not 0.1 <= move <= 50:
            raise ValueError("The daily move must be between 0.1% and 50%.")
    if (
        kit.count(
            connection,
            "SELECT COUNT(*) FROM stk_alerts WHERE owner_id = ? AND active = 1",
            (me["id"],),
        )
        >= 100
    ):
        raise ValueError("You can have up to 100 active alerts.")
    alert_id = kit.new_id()
    connection.execute(
        """INSERT INTO stk_alerts (id, owner_id, symbol, kind, price_cents, move_pct, note, active, created_at, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, 1, ?, ?)""",
        (
            alert_id,
            me["id"],
            quote["symbol"],
            kind,
            price_cents,
            round(move, 2),
            kit.text(raw.get("note"), "a note", max_len=200),
            kit.now(),
            kit.now(),
        ),
    )
    connection.commit()
    return _alert_view(
        kit.require_row(connection, "stk_alerts", alert_id, "alert"), quote["currency"]
    )


def _own_alert(
    connection: sqlite3.Connection, me: dict[str, Any], alert_id: str
) -> dict[str, Any]:
    found = kit.fetch_one(
        connection,
        "SELECT * FROM stk_alerts WHERE id = ? AND owner_id = ?",
        (alert_id, me["id"]),
    )
    if not found:
        raise LookupError("That alert was not found.")
    return found


def update_alert(
    connection: sqlite3.Connection,
    me: dict[str, Any],
    alert_id: str,
    raw: dict[str, Any],
) -> dict[str, Any]:
    _own_alert(connection, me, alert_id)
    changes: dict[str, Any] = {"updated_at": kit.now()}
    if "active" in raw:
        changes["active"] = 1 if kit.flag(raw["active"]) else 0
        if changes["active"]:
            changes.update({"triggered_at": None, "triggered_on": "", "seen_at": None})
    if "note" in raw:
        changes["note"] = kit.text(raw.get("note"), "a note", max_len=200)
    kit.update_row(connection, "stk_alerts", alert_id, changes)
    connection.commit()
    return _alert_view(_own_alert(connection, me, alert_id))


def delete_alert(
    connection: sqlite3.Connection, me: dict[str, Any], alert_id: str
) -> None:
    _own_alert(connection, me, alert_id)
    connection.execute("DELETE FROM stk_alerts WHERE id = ?", (alert_id,))
    connection.commit()


def seen_alert(
    connection: sqlite3.Connection, me: dict[str, Any], alert_id: str
) -> dict[str, Any]:
    alert = _own_alert(connection, me, alert_id)
    if not alert["seen_at"]:
        kit.update_row(connection, "stk_alerts", alert_id, {"seen_at": kit.now()})
        connection.commit()
    return _alert_view(_own_alert(connection, me, alert_id))


def alert_fires(alert: dict[str, Any], quote: dict[str, Any], on: str) -> bool:
    if alert["kind"] == "above":
        return quote["price_cents"] >= alert["price_cents"]
    if alert["kind"] == "below":
        return quote["price_cents"] <= alert["price_cents"]
    return alert["triggered_on"] != on and abs(quote["change_pct"]) >= alert["move_pct"]


def check_alerts(
    connection: sqlite3.Connection, owner_id: str = "", *, fetch: bool = True
) -> list[dict[str, Any]]:
    """Fire the alerts whose condition is met; returns the ones that fired now. Price alerts fire once (and switch
    off); a daily-move alert fires at most once a day and stays on."""
    clauses, params = ["active = 1"], []
    if owner_id:
        clauses.append("owner_id = ?")
        params.append(owner_id)
    alerts = kit.fetch_all(
        connection, f"SELECT * FROM stk_alerts WHERE {' AND '.join(clauses)}", params
    )  # noqa: S608
    if not alerts:
        return []
    quotes = market.quotes(connection, {a["symbol"] for a in alerts}, fetch=fetch)
    on = kit.today()
    fired = []
    for alert in alerts:
        quote = quotes.get(alert["symbol"])
        if not quote or not alert_fires(alert, quote, on):
            continue
        changes = {
            "triggered_at": kit.now(),
            "triggered_on": on,
            "triggered_price_cents": quote["price_cents"],
            "triggered_change_pct": quote["change_pct"],
            "seen_at": None,
            "updated_at": kit.now(),
        }
        if alert["kind"] != "move":
            changes["active"] = 0
        kit.update_row(connection, "stk_alerts", alert["id"], changes)
        fired.append(_alert_view({**alert, **changes}, quote["currency"]))
    connection.commit()
    return fired


def _meta_currency(
    connection: sqlite3.Connection, symbols: Iterable[str]
) -> dict[str, str]:
    names = list(set(symbols))
    if not names:
        return {}
    marks = ",".join("?" for _ in names)
    rows = kit.fetch_all(
        connection,
        f"SELECT symbol, meta FROM stk_market WHERE symbol IN ({marks})",
        names,
    )  # noqa: S608
    return {
        row["symbol"]: (kit.loads(row["meta"], {}) or {}).get("currency", "")
        for row in rows
    }


def list_alerts(
    connection: sqlite3.Connection, me: dict[str, Any]
) -> list[dict[str, Any]]:
    rows = kit.fetch_all(
        connection,
        "SELECT * FROM stk_alerts WHERE owner_id = ? ORDER BY active DESC, triggered_at DESC, created_at DESC",
        (me["id"],),
    )
    currencies = _meta_currency(connection, (r["symbol"] for r in rows))
    return [_alert_view(row, currencies.get(row["symbol"], "")) for row in rows]


def recent_alerts(
    connection: sqlite3.Connection, me: dict[str, Any], *, unseen_only: bool = True
) -> list[dict[str, Any]]:
    since = kit.iso(kit.utcnow() - timedelta(hours=24))
    rows = kit.fetch_all(
        connection,
        "SELECT * FROM stk_alerts WHERE owner_id = ? AND triggered_at IS NOT NULL AND triggered_at >= ?"
        + (" AND seen_at IS NULL" if unseen_only else "")
        + " ORDER BY triggered_at DESC",
        (me["id"], since),
    )
    currencies = _meta_currency(connection, (r["symbol"] for r in rows))
    return [_alert_view(row, currencies.get(row["symbol"], "")) for row in rows]


def fired_text(alert: dict[str, Any]) -> str:
    index = market.is_index(alert["symbol"])
    now = fmt_price(alert["triggered_price_cents"], alert["currency"], index=index)
    name = market.NAMES.get(alert["symbol"], alert["symbol"])
    if alert["kind"] == "above":
        return f"{name} ({alert['symbol']}) rose above {fmt_price(alert['price_cents'], alert['currency'], index=index)}: now {now}."
    if alert["kind"] == "below":
        return f"{name} ({alert['symbol']}) fell below {fmt_price(alert['price_cents'], alert['currency'], index=index)}: now {now}."
    change = alert["triggered_change_pct"]
    return f"{name} ({alert['symbol']}) {'rose' if change > 0 else 'fell'} {abs(change):.1f}% today: now {now}."


# ---------------------------------------------------------------------------
# portfolios: accounts, transactions and the profit and loss maths
# ---------------------------------------------------------------------------


def _accounts(
    connection: sqlite3.Connection, me: dict[str, Any], kind: str = ""
) -> list[dict[str, Any]]:
    sql = "SELECT * FROM stk_accounts WHERE owner_id = ?"
    params: list[Any] = [me["id"]]
    if kind:
        sql += " AND kind = ?"
        params.append(kind)
    return kit.fetch_all(connection, sql + " ORDER BY kind, currency", params)


def _own_account(
    connection: sqlite3.Connection, me: dict[str, Any], account_id: str
) -> dict[str, Any]:
    found = kit.fetch_one(
        connection,
        "SELECT * FROM stk_accounts WHERE id = ? AND owner_id = ?",
        (account_id, me["id"]),
    )
    if not found:
        raise LookupError("That portfolio was not found.")
    return found


def start_practice(
    connection: sqlite3.Connection, me: dict[str, Any], raw: dict[str, Any]
) -> dict[str, Any]:
    currency = kit.currency(raw.get("currency"), default="USD")
    cash = price_input(
        raw, "cash", "the starting cash", required=False
    ) or PRACTICE_DEFAULTS.get(currency, 100_000_00)
    if not PRACTICE_MIN <= cash <= PRACTICE_MAX:
        raise ValueError(
            f"Start with between {kit.fmt_money(PRACTICE_MIN, currency)} and {kit.fmt_money(PRACTICE_MAX, currency)}."
        )
    existing = kit.fetch_one(
        connection,
        "SELECT * FROM stk_accounts WHERE owner_id = ? AND kind = 'practice' AND currency = ?",
        (me["id"], currency),
    )
    if existing:
        raise ValueError(
            f"You already have a practice portfolio in {currency}. Reset it to start again."
        )
    account_id = kit.new_id()
    connection.execute(
        """INSERT INTO stk_accounts (id, owner_id, kind, currency, name, start_cash_cents, cash_cents, started_at, created_at)
        VALUES (?, ?, 'practice', ?, ?, ?, ?, ?, ?)""",
        (
            account_id,
            me["id"],
            currency,
            f"Practice ({currency})",
            cash,
            cash,
            kit.now(),
            kit.now(),
        ),
    )
    connection.commit()
    return kit.require_row(connection, "stk_accounts", account_id, "portfolio")


def reset_practice(
    connection: sqlite3.Connection,
    me: dict[str, Any],
    account_id: str,
    raw: dict[str, Any],
) -> dict[str, Any]:
    account = _own_account(connection, me, account_id)
    if account["kind"] != "practice":
        raise ValueError("Only a practice portfolio can be reset.")
    cash = (
        price_input(raw, "cash", "the starting cash", required=False)
        or account["start_cash_cents"]
    )
    if not PRACTICE_MIN <= cash <= PRACTICE_MAX:
        raise ValueError("Choose a starting amount in the allowed range.")
    connection.execute("DELETE FROM stk_txns WHERE account_id = ?", (account_id,))
    kit.update_row(
        connection,
        "stk_accounts",
        account_id,
        {"start_cash_cents": cash, "cash_cents": cash, "started_at": kit.now()},
    )
    connection.commit()
    return _own_account(connection, me, account_id)


def _real_account(
    connection: sqlite3.Connection, me: dict[str, Any], currency: str
) -> dict[str, Any]:
    found = kit.fetch_one(
        connection,
        "SELECT * FROM stk_accounts WHERE owner_id = ? AND kind = 'real' AND currency = ?",
        (me["id"], currency),
    )
    if found:
        return found
    account_id = kit.new_id()
    connection.execute(
        """INSERT INTO stk_accounts (id, owner_id, kind, currency, name, start_cash_cents, cash_cents, started_at, created_at)
        VALUES (?, ?, 'real', ?, ?, 0, 0, ?, ?)""",
        (
            account_id,
            me["id"],
            currency,
            f"My holdings ({currency})",
            kit.now(),
            kit.now(),
        ),
    )
    return kit.require_row(connection, "stk_accounts", account_id, "portfolio")


def positions(
    txns: list[dict[str, Any]],
) -> tuple[dict[str, dict[str, Any]], list[dict[str, Any]]]:
    """Replay transactions in date order with the average-cost method.

    A buy adds ``quantity * price + fees`` to the cost; a sell removes the average cost of the shares sold and
    realises ``proceeds - fees - that cost``; a dividend adds to dividends. Raises ValueError when a sell is larger
    than what was held on its date. Returns the positions by symbol and the transactions with ``realised_cents``."""
    ordered = sorted(  # same-day trades in the order they were made
        txns,
        key=lambda t: (t["trade_date"], t.get("seq") or 0, t["created_at"], t["id"]),
    )
    held: dict[str, dict[str, Any]] = {}
    log = []
    for txn in ordered:
        p = held.setdefault(
            txn["symbol"],
            {
                "symbol": txn["symbol"],
                "quantity": 0.0,
                "cost": 0.0,
                "realised": 0.0,
                "dividends": 0,
                "fees": 0,
                "bought": 0.0,
                "today_buys": [],  # (shares, price) bought today: their day change counts from the price paid
                "sold": 0.0,
                "first_date": txn["trade_date"],
            },
        )
        realised = None
        qty = float(txn["quantity"] or 0)
        if txn["kind"] == "buy":
            p["quantity"] += qty
            p["cost"] += qty * txn["price_cents"] + txn["fees_cents"]
            p["fees"] += txn["fees_cents"]
            p["bought"] += qty
            if txn["trade_date"] == kit.today():
                p["today_buys"].append((qty, txn["price_cents"]))
        elif txn["kind"] == "sell":
            if qty > p["quantity"] + QTY_EPS:
                raise ValueError(
                    f"That would sell {_qty_text(qty)} {txn['symbol']} on {txn['trade_date']}, but only "
                    f"{_qty_text(p['quantity'])} were held then."
                )
            average = p["cost"] / p["quantity"] if p["quantity"] > QTY_EPS else 0.0
            removed = average * qty
            realised = qty * txn["price_cents"] - txn["fees_cents"] - removed
            p["realised"] += realised
            p["quantity"] -= qty
            p["cost"] -= removed
            p["fees"] += txn["fees_cents"]
            p["sold"] += qty
            if p["quantity"] < QTY_EPS:
                p["quantity"], p["cost"] = 0.0, 0.0
        else:
            p["dividends"] += int(txn["amount_cents"])
        log.append(
            {
                **txn,
                "realised_cents": None if realised is None else int(round(realised)),
            }
        )
    return held, log


def _bought_today(
    buys: list[tuple[float, int]], quote: dict[str, Any] | None, *, real: bool
) -> list[tuple[float, int]]:
    """The buys dated today whose day change counts from the price paid.

    A practice trade always filled at today's price. A real one counts only when its price is inside today's
    trading range (with a little room): a holding bought long ago and recorded with today's date (the form's
    default) is outside it, and its day change then follows the previous close like any older share."""
    if not real or not quote:
        return list(buys)
    prices = [
        quote.get(key) or 0
        for key in (
            "day_low_cents",
            "day_high_cents",
            "previous_close_cents",
            "price_cents",
        )
    ]
    prices = [price for price in prices if price > 0]
    if not prices:
        return list(buys)
    low, high = min(prices) * 0.98, max(prices) * 1.02
    return [(qty, paid) for qty, paid in buys if low <= paid <= high]


def account_summary(
    connection: sqlite3.Connection,
    account: dict[str, Any],
    *,
    fetch: bool = True,
    quotes: dict[str, dict[str, Any]] | None = None,
) -> dict[str, Any]:
    txns = kit.fetch_all(
        connection,
        "SELECT rowid AS seq, * FROM stk_txns WHERE account_id = ?",
        (account["id"],),
    )
    held, log = positions(txns)
    symbols = [s for s, p in held.items() if p["quantity"] > QTY_EPS]
    found = dict(quotes or {})
    missing = [s for s in symbols if s not in found]
    if missing:
        found.update(market.quotes(connection, missing, fetch=fetch))
    holdings, closed = [], []
    totals = {
        "value": 0,
        "cost": 0,
        "unrealised": 0,
        "realised": 0,
        "dividends": 0,
        "day": 0,
        "fees": 0,
    }
    for symbol, p in held.items():
        q = found.get(symbol)
        realised = int(round(p["realised"]))
        totals["realised"] += realised
        totals["dividends"] += p["dividends"]
        totals["fees"] += p["fees"]
        if p["quantity"] <= QTY_EPS:
            closed.append(
                {
                    "symbol": symbol,
                    "name": market.NAMES.get(symbol, symbol),
                    "realised_cents": realised,
                    "dividends_cents": p["dividends"],
                    "total_cents": realised + p["dividends"],
                    "open": False,
                }
            )
            continue
        price = q["price_cents"] if q else int(round(p["cost"] / p["quantity"]))
        previous = q["previous_close_cents"] if q else price
        value = int(round(p["quantity"] * price))
        cost = int(round(p["cost"]))
        today_buys = _bought_today(p["today_buys"], q, real=account["kind"] == "real")
        today_qty = sum(qty for qty, _ in today_buys)
        fresh = min(p["quantity"], today_qty)
        paid_today = (
            sum(qty * paid for qty, paid in today_buys) / today_qty * fresh
            if today_qty
            else 0.0
        )
        day = int(
            round(
                (p["quantity"] - fresh) * (price - previous)
                + fresh * price
                - paid_today
            )
        )
        unrealised = value - cost
        totals["value"] += value
        totals["cost"] += cost
        totals["unrealised"] += unrealised
        totals["day"] += day
        holdings.append(
            {
                "symbol": symbol,
                "name": (q or {}).get("name") or market.NAMES.get(symbol, symbol),
                "quantity": round(p["quantity"], 6),
                "average_cents": int(round(p["cost"] / p["quantity"])),
                "price_cents": price,
                "change_pct": (q or {}).get("change_pct", 0.0),
                "value_cents": value,
                "cost_cents": cost,
                "unrealised_cents": unrealised,
                "unrealised_pct": round(unrealised / cost * 100, 2) if cost else 0.0,
                "day_change_cents": day,
                "realised_cents": realised,
                "dividends_cents": p["dividends"],
                "total_cents": unrealised + realised + p["dividends"],
                "stale": bool((q or {}).get("stale")) or q is None,
                "open": True,
            }
        )
    for holding in holdings:
        holding["allocation_pct"] = (
            round(holding["value_cents"] / totals["value"] * 100, 1)
            if totals["value"]
            else 0.0
        )
    holdings.sort(key=lambda h: -h["value_cents"])
    ranked = sorted([*holdings, *closed], key=lambda h: -h["total_cents"])
    equity = totals["value"] + (
        account["cash_cents"] if account["kind"] == "practice" else 0
    )
    start = account["start_cash_cents"]
    before = totals["value"] - totals["day"]
    names = {t["symbol"]: market.NAMES.get(t["symbol"], t["symbol"]) for t in log}
    return {
        **{k: account[k] for k in ("id", "kind", "currency", "name", "started_at")},
        "cash_cents": account["cash_cents"] if account["kind"] == "practice" else None,
        "start_cash_cents": start if account["kind"] == "practice" else None,
        "value_cents": totals["value"],
        "invested_cents": totals["cost"],
        "equity_cents": equity,
        "unrealised_cents": totals["unrealised"],
        "unrealised_pct": round(totals["unrealised"] / totals["cost"] * 100, 2)
        if totals["cost"]
        else 0.0,
        "realised_cents": totals["realised"],
        "dividends_cents": totals["dividends"],
        "fees_cents": totals["fees"],
        "total_pl_cents": totals["unrealised"]
        + totals["realised"]
        + totals["dividends"],
        "day_change_cents": totals["day"],
        "day_change_pct": round(totals["day"] / before * 100, 2) if before else 0.0,
        "return_pct": round((equity - start) / start * 100, 2)
        if account["kind"] == "practice" and start
        else None,
        "holdings": holdings,
        "closed": closed,
        "best": [h for h in ranked if h["total_cents"] > 0][:3],
        "worst": [h for h in ranked[::-1] if h["total_cents"] < 0][:3],
        "transactions": [{**t, "name": names[t["symbol"]]} for t in log[::-1]],
        "trades": sum(1 for t in log if t["kind"] in ("buy", "sell")),
    }


def portfolio(
    connection: sqlite3.Connection, me: dict[str, Any], kind: str = "practice"
) -> dict[str, Any]:
    kind = kit.choice(kind, "the portfolio", ("practice", "real"), default="practice")
    if kind == "real":
        require_adult(me, "Recording real holdings")
    accounts = _accounts(connection, me, kind)
    symbols: set[str] = set()
    for account in accounts:
        symbols.update(
            r["symbol"]
            for r in kit.fetch_all(
                connection,
                "SELECT DISTINCT symbol FROM stk_txns WHERE account_id = ?",
                (account["id"],),
            )
        )
    quotes = market.quotes(connection, symbols) if symbols else {}
    return {
        "kind": kind,
        "accounts": [account_summary(connection, a, quotes=quotes) for a in accounts],
        "defaults": {code: cents for code, cents in PRACTICE_DEFAULTS.items()},
        "currencies": list(kit.CURRENCIES),
        "adult": life.is_adult(me),
        "disclaimer": DISCLAIMER,
    }


def _insert_txn(
    connection: sqlite3.Connection, account: dict[str, Any], values: dict[str, Any]
) -> str:
    txn_id = kit.new_id()
    connection.execute(
        """INSERT INTO stk_txns (id, account_id, owner_id, symbol, kind, trade_date, quantity, price_cents, fees_cents,
        amount_cents, note, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            txn_id,
            account["id"],
            account["owner_id"],
            values["symbol"],
            values["kind"],
            values["trade_date"],
            values.get("quantity", 0),
            values.get("price_cents", 0),
            values.get("fees_cents", 0),
            values.get("amount_cents", 0),
            values.get("note", ""),
            kit.now(),
        ),
    )
    return txn_id


def practice_trade(
    connection: sqlite3.Connection, me: dict[str, Any], raw: dict[str, Any]
) -> dict[str, Any]:
    """Buy or sell at the current price in the practice portfolio of the stock's currency (no fees)."""
    side = kit.choice(raw.get("side"), "buy or sell", ("buy", "sell"))
    quote = _quote(connection, raw.get("symbol"))
    if quote["index"]:
        raise ValueError("An index cannot be bought. Choose a stock.")
    account = kit.fetch_one(
        connection,
        "SELECT * FROM stk_accounts WHERE owner_id = ? AND kind = 'practice' AND currency = ?",
        (me["id"], quote["currency"]),
    )
    if not account:
        raise ValueError(
            f"Start a practice portfolio in {quote['currency']} first to trade {quote['symbol']} (Portfolio > Practice)."
        )
    price = quote["price_cents"]
    if raw.get("quantity") in (None, "") and raw.get("amount") not in (None, ""):
        amount = kit.cents(raw.get("amount"), "the amount")
        qty = math.floor(amount / price * 10_000) / 10_000
        if qty <= 0:
            raise ValueError(
                "That amount buys less than a share's ten-thousandth. Enter more."
            )
    else:
        qty = quantity(raw.get("quantity"))
    cost = int(round(qty * price))
    if cost <= 0:  # a trade worth nothing would still count in the log and the league
        raise ValueError(
            f"That is worth less than {kit.fmt_money(1, quote['currency'])}. Enter more shares."
        )
    held, _ = positions(
        kit.fetch_all(
            connection,
            "SELECT rowid AS seq, * FROM stk_txns WHERE account_id = ?",
            (account["id"],),
        )
    )
    owned = held.get(quote["symbol"], {}).get("quantity", 0.0)
    if side == "buy" and cost > account["cash_cents"]:
        raise ValueError(
            f"Not enough practice cash: {_qty_text(qty)} {quote['symbol']} cost {kit.fmt_money(cost, quote['currency'])} "
            f"and you have {kit.fmt_money(account['cash_cents'], quote['currency'])}."
        )
    if side == "sell" and qty > owned + QTY_EPS:
        raise ValueError(
            f"You hold {_qty_text(owned)} {quote['symbol']} in practice; you cannot sell {_qty_text(qty)}."
        )
    txn_id = _insert_txn(
        connection,
        account,
        {
            "symbol": quote["symbol"],
            "kind": side,
            "trade_date": kit.today(),
            "quantity": qty,
            "price_cents": price,
        },
    )
    cash = (
        account["cash_cents"] - cost if side == "buy" else account["cash_cents"] + cost
    )
    kit.update_row(connection, "stk_accounts", account["id"], {"cash_cents": cash})
    connection.commit()
    return {
        "trade": {
            "id": txn_id,
            "side": side,
            "symbol": quote["symbol"],
            "quantity": qty,
            "price_cents": price,
            "amount_cents": cost,
            "currency": quote["currency"],
        },
        "account": account_summary(
            connection,
            _own_account(connection, me, account["id"]),
            quotes={quote["symbol"]: quote},
        ),
        "message": f"{'Bought' if side == 'buy' else 'Sold'} {_qty_text(qty)} {quote['symbol']} at "
        f"{kit.fmt_money(price, quote['currency'])} (practice money).",
    }


def _txn_values(
    connection: sqlite3.Connection,
    raw: dict[str, Any],
    current: dict[str, Any] | None = None,
) -> dict[str, Any]:
    base = dict(current or {})
    for key in ("price", "fees", "amount"):
        if (
            key in raw or f"{key}_cents" in raw
        ):  # what the person typed replaces the saved value
            base.pop(key, None)
            base.pop(f"{key}_cents", None)
    merged = {**base, **raw}
    kind = kit.choice(merged.get("kind"), "the kind", TXN_KINDS)
    symbol = market.clean_symbol(merged.get("symbol"))
    if symbol != (current or {}).get("symbol"):
        data = market.series(connection, symbol)
        symbol = data["symbol"]
        if market.is_index(symbol):
            raise ValueError("An index cannot be held. Choose a stock.")
        currency = data.get("currency") or "USD"
    else:
        currency = ""
    trade_date = kit.day(merged.get("trade_date"), "the date", required=True)
    if date.fromisoformat(trade_date) > today():
        raise ValueError("The date cannot be in the future.")
    values: dict[str, Any] = {
        "kind": kind,
        "symbol": symbol,
        "trade_date": trade_date,
        "note": kit.text(merged.get("note"), "a note", max_len=200),
        "_currency": currency,
    }
    if kind == "dividend":
        values["amount_cents"] = price_input(merged, "amount", "the dividend amount")
        values.update({"quantity": 0, "price_cents": 0, "fees_cents": 0})
    else:
        values["quantity"] = quantity(merged.get("quantity"))
        values["price_cents"] = price_input(merged, "price", "the price per share")
        values["fees_cents"] = price_input(merged, "fees", "the fees", required=False)
        values["amount_cents"] = 0
    return values


def add_real(
    connection: sqlite3.Connection, me: dict[str, Any], raw: dict[str, Any]
) -> dict[str, Any]:
    require_adult(me, "Recording real holdings")
    values = _txn_values(connection, raw)
    account = _real_account(connection, me, values.pop("_currency") or "USD")
    if (
        kit.count(
            connection, "SELECT COUNT(*) FROM stk_txns WHERE owner_id = ?", (me["id"],)
        )
        >= 5000
    ):
        raise ValueError(
            "You have 5,000 transactions; export and remove old ones first."
        )
    txn_id = _insert_txn(connection, account, values)
    txns = kit.fetch_all(
        connection,
        "SELECT rowid AS seq, * FROM stk_txns WHERE account_id = ?",
        (account["id"],),
    )
    try:
        positions(txns)
    except ValueError:
        connection.rollback()
        raise
    connection.commit()
    return {
        "transaction": kit.require_row(connection, "stk_txns", txn_id, "transaction"),
        "account_id": account["id"],
    }


def _own_txn(
    connection: sqlite3.Connection, me: dict[str, Any], txn_id: str
) -> tuple[dict[str, Any], dict[str, Any]]:
    txn = kit.fetch_one(
        connection,
        "SELECT rowid AS seq, * FROM stk_txns WHERE id = ? AND owner_id = ?",
        (txn_id, me["id"]),
    )
    if not txn:
        raise LookupError("That transaction was not found.")
    account = _own_account(connection, me, txn["account_id"])
    if account["kind"] != "real":
        raise ValueError(
            "Practice trades cannot be changed. Reset the practice portfolio to start again."
        )
    return txn, account


def update_real(
    connection: sqlite3.Connection, me: dict[str, Any], txn_id: str, raw: dict[str, Any]
) -> dict[str, Any]:
    require_adult(me, "Recording real holdings")
    txn, account = _own_txn(connection, me, txn_id)
    values = _txn_values(
        connection, {k: v for k, v in raw.items() if k != "symbol"}, txn
    )
    values.pop("_currency", None)
    others = [
        t
        for t in kit.fetch_all(
            connection,
            "SELECT rowid AS seq, * FROM stk_txns WHERE account_id = ?",
            (account["id"],),
        )
        if t["id"] != txn_id
    ]
    positions([*others, {**txn, **values}])
    kit.update_row(connection, "stk_txns", txn_id, values)
    connection.commit()
    return {
        "transaction": kit.require_row(connection, "stk_txns", txn_id, "transaction")
    }


def delete_real(
    connection: sqlite3.Connection, me: dict[str, Any], txn_id: str
) -> None:
    require_adult(me, "Recording real holdings")
    txn, account = _own_txn(connection, me, txn_id)
    others = [
        t
        for t in kit.fetch_all(
            connection,
            "SELECT rowid AS seq, * FROM stk_txns WHERE account_id = ?",
            (account["id"],),
        )
        if t["id"] != txn_id
    ]
    positions(others)  # removing a buy must not leave a later sell without shares
    connection.execute("DELETE FROM stk_txns WHERE id = ?", (txn_id,))
    connection.commit()


def transactions_csv(
    connection: sqlite3.Connection, me: dict[str, Any], kind: str
) -> bytes:
    data = portfolio(connection, me, kind)
    rows = []
    for account in data["accounts"]:
        for t in account["transactions"]:
            rows.append(
                {
                    "portfolio": account["name"],
                    "date": t["trade_date"],
                    "kind": t["kind"],
                    "symbol": t["symbol"],
                    "name": t["name"],
                    "quantity": _qty_text(t["quantity"]) if t["quantity"] else "",
                    "price": f"{t['price_cents'] / 100:.2f}"
                    if t["price_cents"]
                    else "",
                    "fees": f"{t['fees_cents'] / 100:.2f}" if t["fees_cents"] else "",
                    "amount": f"{(t['amount_cents'] or round(t['quantity'] * t['price_cents'])) / 100:.2f}",
                    "realised": f"{t['realised_cents'] / 100:.2f}"
                    if t["realised_cents"] is not None
                    else "",
                    "currency": account["currency"],
                    "note": t["note"],
                }
            )
    return kit.csv_bytes(
        rows,
        (
            "portfolio",
            "date",
            "kind",
            "symbol",
            "name",
            "quantity",
            "price",
            "fees",
            "amount",
            "realised",
            "currency",
            "note",
        ),
    )


def league(connection: sqlite3.Connection, me: dict[str, Any]) -> dict[str, Any]:
    """Practice returns across the household (locally) or the organization (hosted): name and return only."""
    people = {p["id"]: p for p in life.profiles(connection)}
    accounts = [
        a
        for a in kit.fetch_all(
            connection, "SELECT * FROM stk_accounts WHERE kind = 'practice'"
        )
        if a["owner_id"] in people
    ]
    symbols = {
        r["symbol"]
        for r in kit.fetch_all(
            connection,
            "SELECT DISTINCT t.symbol FROM stk_txns t JOIN stk_accounts a ON a.id = t.account_id WHERE a.kind = 'practice'",
        )
    }
    quotes = market.quotes(connection, symbols) if symbols else {}
    rows = []
    for account in accounts:
        summary = account_summary(connection, account, quotes=quotes, fetch=False)
        person = people[account["owner_id"]]
        rows.append(
            {
                "name": person["name"],
                "emoji": person["emoji"],
                "currency": account["currency"],
                "return_pct": summary["return_pct"] or 0.0,
                "trades": summary["trades"],
                "holdings": len(summary["holdings"]),
                "since": account["started_at"],
                "me": account["owner_id"] == me["id"],
            }
        )
    rows.sort(key=lambda r: (-r["return_pct"], r["name"]))
    for rank, row in enumerate(rows, 1):
        row["rank"] = rank
    return {"rows": rows, "hosted": suite.actor() is not None}


# ---------------------------------------------------------------------------
# earning: ideas and their track record
# ---------------------------------------------------------------------------


def track_idea(
    idea: dict[str, Any], bars: list[dict[str, Any]], on: date
) -> dict[str, Any]:
    """Follow an open idea through the days after it was published.

    A buy idea is *stopped* when a day's low reaches the stop and *hit* when a day's high reaches the target (on a
    day that touched both, the stop counts: the careful reading). A sell idea is the mirror image. Past its
    expiry date an idea is *expired* at that day's close. Returns the fields to save."""
    if idea["status"] != "open":
        return {}
    buy = idea["side"] == "buy"
    entry, target, stop = idea["entry_cents"], idea["target_cents"], idea["stop_cents"]
    last = idea["last_cents"] or entry
    for bar in bars:
        if bar["date"] <= idea["opened_on"]:
            continue
        if bar["date"] > idea["expires_on"]:
            break
        high, low = market.to_cents(bar["high"]), market.to_cents(bar["low"])
        last = market.to_cents(bar["close"])
        stopped = low <= stop if buy else high >= stop
        reached = high >= target if buy else low <= target
        if stopped or reached:
            exit_price = stop if stopped else target
            return _idea_close(
                idea, "stopped" if stopped else "target_hit", exit_price, bar["date"]
            )
    if date.fromisoformat(idea["expires_on"]) < on:
        upto = [
            bar for bar in bars if idea["opened_on"] < bar["date"] <= idea["expires_on"]
        ]
        exit_price = market.to_cents(upto[-1]["close"]) if upto else last
        return _idea_close(
            idea,
            "expired",
            exit_price,
            upto[-1]["date"] if upto else idea["expires_on"],
        )
    return {"last_cents": last, "return_bp": idea_return_bp(idea["side"], entry, last)}


def idea_return_bp(side: str, entry: int, exit_price: int) -> int:
    if not entry:
        return 0
    change = (
        (exit_price - entry) / entry if side == "buy" else (entry - exit_price) / entry
    )
    return int(round(change * 10_000))


def _idea_close(
    idea: dict[str, Any], status: str, exit_price: int, on: str
) -> dict[str, Any]:
    return {
        "status": status,
        "close_cents": exit_price,
        "last_cents": exit_price,
        "closed_on": on,
        "return_bp": idea_return_bp(idea["side"], idea["entry_cents"], exit_price),
        "updated_at": kit.now(),
    }


def track_ideas(
    connection: sqlite3.Connection, owner_id: str = "", *, fetch: bool = True
) -> int:
    sql, params = "SELECT * FROM stk_ideas WHERE status = 'open'", []
    if owner_id:
        sql += " AND owner_id = ?"
        params.append(owner_id)
    ideas = kit.fetch_all(connection, sql, params)
    if not ideas:
        return 0
    data = market.load(
        connection, {i["symbol"] for i in ideas}, history=True, fetch=fetch
    )
    closed = 0
    for idea in ideas:
        found = data.get(idea["symbol"])
        if not found:
            continue
        changes = track_idea(idea, found["bars"], today())
        if changes:
            if changes.get("status") not in (None, "open"):
                closed += 1
                kit.log(
                    connection,
                    APP,
                    "idea_closed",
                    f"Idea {idea['side']} {idea['symbol']}: {changes['status'].replace('_', ' ')} "
                    f"({changes['return_bp'] / 100:+.2f}%)",
                    ref_type="idea",
                    ref_id=idea["id"],
                )
            kit.update_row(connection, "stk_ideas", idea["id"], changes)
    connection.commit()
    return closed


def _idea_view(row: dict[str, Any]) -> dict[str, Any]:
    out = dict(row)
    out["return_pct"] = round(row["return_bp"] / 100, 2)
    out["days_left"] = (
        (date.fromisoformat(row["expires_on"]) - today()).days
        if row["status"] == "open"
        else None
    )
    out["deletable"] = row["status"] == "open" and (
        kit.utcnow() - kit.parse_time(row["created_at"])
    ) <= timedelta(hours=1)
    out["upside_pct"] = (
        round(
            abs(row["target_cents"] - row["entry_cents"]) / row["entry_cents"] * 100, 2
        )
        if row["entry_cents"]
        else 0
    )
    out["risk_pct"] = (
        round(abs(row["entry_cents"] - row["stop_cents"]) / row["entry_cents"] * 100, 2)
        if row["entry_cents"]
        else 0
    )
    return out


def track_record(rows: list[dict[str, Any]]) -> dict[str, Any]:
    finished = [r for r in rows if r["status"] != "open"]
    opened = [r for r in rows if r["status"] == "open"]
    returns = [r["return_bp"] / 100 for r in finished]
    wins = [x for x in returns if x > 0]
    return {
        "ideas": len(rows),
        "open": len(opened),
        "closed": len(finished),
        "wins": len(wins),
        "losses": sum(1 for x in returns if x < 0),
        "win_rate_pct": round(len(wins) / len(finished) * 100, 1) if finished else None,
        "average_return_pct": round(sum(returns) / len(returns), 2)
        if returns
        else None,
        "best_pct": round(max(returns), 2) if returns else None,
        "worst_pct": round(min(returns), 2) if returns else None,
        "target_hit": sum(1 for r in finished if r["status"] == "target_hit"),
        "stopped": sum(1 for r in finished if r["status"] == "stopped"),
        "expired": sum(1 for r in finished if r["status"] == "expired"),
        "open_average_pct": round(
            sum(r["return_bp"] for r in opened) / len(opened) / 100, 2
        )
        if opened
        else None,
    }


def create_idea(
    connection: sqlite3.Connection, me: dict[str, Any], raw: dict[str, Any]
) -> dict[str, Any]:
    require_adult(me, "Publishing stock ideas")
    quote = _quote(connection, raw.get("symbol"))
    if quote["index"]:
        raise ValueError("Ideas are for stocks, not indices.")
    side = kit.choice(raw.get("side"), "buy or sell", IDEA_SIDES)
    entry = (
        price_input(raw, "entry", "the entry price", required=False)
        or quote["price_cents"]
    )
    target = price_input(raw, "target", "the target price")
    stop = price_input(raw, "stop", "the stop-loss price")
    if side == "buy" and not stop < entry < target:
        raise ValueError(
            "For a buy idea the stop must be below the entry and the target above it."
        )
    if side == "sell" and not target < entry < stop:
        raise ValueError(
            "For a sell idea the target must be below the entry and the stop above it."
        )
    if abs(entry - quote["price_cents"]) > quote["price_cents"] * 0.1:
        raise ValueError(
            f"The entry must be within 10% of today's price ({kit.fmt_money(quote['price_cents'], quote['currency'])}), "
            "so the track record stays honest."
        )
    horizon = kit.integer(
        raw.get("horizon_days"),
        "the horizon in days",
        default=30,
        minimum=1,
        maximum=365,
    )
    access = kit.choice(
        raw.get("access"), "who can see it", ("free", "paid"), default="free"
    )
    if access == "paid":
        analyst = kit.fetch_one(
            connection, "SELECT * FROM stk_analysts WHERE owner_id = ?", (me["id"],)
        )
        if not analyst or analyst["price_cents"] <= 0:
            raise ValueError(
                "Set a monthly price on your analyst page before publishing paid ideas."
            )
    if (
        kit.count(
            connection,
            "SELECT COUNT(*) FROM stk_ideas WHERE owner_id = ? AND status = 'open'",
            (me["id"],),
        )
        >= 100
    ):
        raise ValueError("You have 100 open ideas. Close some first.")
    idea_id = kit.new_id()
    opened = kit.today()
    connection.execute(
        """INSERT INTO stk_ideas (id, owner_id, symbol, name, currency, side, entry_cents, target_cents, stop_cents,
        horizon_days, opened_on, expires_on, reasoning, access, status, last_cents, created_at, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'open', ?, ?, ?)""",
        (
            idea_id,
            me["id"],
            quote["symbol"],
            quote["name"][:120],
            quote["currency"],
            side,
            entry,
            target,
            stop,
            horizon,
            opened,
            (today() + timedelta(days=horizon)).isoformat(),
            kit.text(
                raw.get("reasoning"), "the reasoning", max_len=2000, multiline=True
            ),
            access,
            quote["price_cents"],
            kit.now(),
            kit.now(),
        ),
    )
    kit.log(
        connection,
        APP,
        "idea",
        f"Published a {access} {side} idea on {quote['symbol']}",
        ref_type="idea",
        ref_id=idea_id,
        actor=me["name"],
    )
    connection.commit()
    return _idea_view(kit.require_row(connection, "stk_ideas", idea_id, "idea"))


def _own_idea(
    connection: sqlite3.Connection, me: dict[str, Any], idea_id: str
) -> dict[str, Any]:
    found = kit.fetch_one(
        connection,
        "SELECT * FROM stk_ideas WHERE id = ? AND owner_id = ?",
        (idea_id, me["id"]),
    )
    if not found:
        raise LookupError("That idea was not found.")
    return found


def update_idea(
    connection: sqlite3.Connection,
    me: dict[str, Any],
    idea_id: str,
    raw: dict[str, Any],
) -> dict[str, Any]:
    """Only the reasoning can change after publishing (prices are the record); ``close`` ends it at today's price."""
    require_adult(me, "Publishing stock ideas")
    idea = _own_idea(connection, me, idea_id)
    if kit.flag(raw.get("close")):
        if idea["status"] != "open":
            raise ValueError("This idea is already closed.")
        price = _quote(connection, idea["symbol"])["price_cents"]
        kit.update_row(
            connection,
            "stk_ideas",
            idea_id,
            _idea_close(idea, "closed", price, kit.today()),
        )
    if "reasoning" in raw:
        kit.update_row(
            connection,
            "stk_ideas",
            idea_id,
            {
                "reasoning": kit.text(
                    raw.get("reasoning"), "the reasoning", max_len=2000, multiline=True
                ),
                "updated_at": kit.now(),
            },
        )
    connection.commit()
    return _idea_view(_own_idea(connection, me, idea_id))


def delete_idea(
    connection: sqlite3.Connection, me: dict[str, Any], idea_id: str
) -> None:
    require_adult(me, "Publishing stock ideas")
    idea = _own_idea(connection, me, idea_id)
    age = kit.utcnow() - kit.parse_time(idea["created_at"])
    if idea["status"] != "open" or age > timedelta(hours=1):
        raise ValueError(
            "Ideas stay on your track record after the first hour. Close it instead."
        )
    connection.execute("DELETE FROM stk_ideas WHERE id = ?", (idea_id,))
    connection.commit()


def my_ideas(
    connection: sqlite3.Connection, me: dict[str, Any]
) -> list[dict[str, Any]]:
    track_ideas(connection, me["id"])
    return [
        _idea_view(r)
        for r in kit.fetch_all(
            connection,
            "SELECT * FROM stk_ideas WHERE owner_id = ? ORDER BY status = 'open' DESC, opened_on DESC, created_at DESC",
            (me["id"],),
        )
    ]


def ideas_csv(connection: sqlite3.Connection, me: dict[str, Any]) -> bytes:
    rows = [
        {
            "symbol": i["symbol"],
            "side": i["side"],
            "access": i["access"],
            "opened_on": i["opened_on"],
            "entry": f"{i['entry_cents'] / 100:.2f}",
            "target": f"{i['target_cents'] / 100:.2f}",
            "stop": f"{i['stop_cents'] / 100:.2f}",
            "expires_on": i["expires_on"],
            "status": i["status"],
            "closed_on": i["closed_on"],
            "exit": f"{i['close_cents'] / 100:.2f}" if i["close_cents"] else "",
            "return_pct": f"{i['return_pct']:.2f}",
            "currency": i["currency"],
            "reasoning": i["reasoning"],
        }
        for i in my_ideas(connection, me)
    ]
    return kit.csv_bytes(
        rows,
        (
            "symbol",
            "side",
            "access",
            "opened_on",
            "entry",
            "target",
            "stop",
            "expires_on",
            "status",
            "closed_on",
            "exit",
            "return_pct",
            "currency",
            "reasoning",
        ),
    )


# ---------------------------------------------------------------------------
# earning: the analyst page, subscribers and payments
# ---------------------------------------------------------------------------


def analyst(connection: sqlite3.Connection, owner_id: str) -> dict[str, Any] | None:
    found = kit.fetch_one(
        connection, "SELECT * FROM stk_analysts WHERE owner_id = ?", (owner_id,)
    )
    if found:
        found["accepting"] = bool(found["accepting"])
    return found


def save_analyst(
    connection: sqlite3.Connection, me: dict[str, Any], raw: dict[str, Any]
) -> dict[str, Any]:
    require_adult(me, "An analyst page")
    current = analyst(connection, me["id"]) or {}
    merged = {**current, **raw}
    values = {
        "display_name": kit.text(
            merged.get("display_name") or me["name"],
            "your public name",
            max_len=60,
            required=True,
        ),
        "bio": kit.text(merged.get("bio"), "a short bio", max_len=600, multiline=True),
        "price_cents": price_input(raw, "price", "the monthly price", required=False)
        if ("price" in raw or "price_cents" in raw)
        else int(current.get("price_cents") or 0),
        "currency": kit.currency(merged.get("currency"), default="USD"),
        "payment_link": kit.url(merged.get("payment_link"), "the payment link"),
        "payment_instructions": kit.text(
            merged.get("payment_instructions"),
            "payment instructions",
            max_len=600,
            multiline=True,
        ),
        "accepting": 1 if kit.flag(merged.get("accepting", True)) else 0,
        "updated_at": kit.now(),
    }
    if values["price_cents"] and not (
        values["payment_link"] or values["payment_instructions"]
    ):
        raise ValueError(
            "Add a payment link or payment instructions so subscribers can pay you."
        )
    if current:
        connection.execute(
            """UPDATE stk_analysts SET display_name = :display_name, bio = :bio, price_cents = :price_cents,
            currency = :currency, payment_link = :payment_link, payment_instructions = :payment_instructions,
            accepting = :accepting, updated_at = :updated_at WHERE owner_id = :owner_id""",
            {**values, "owner_id": me["id"]},
        )
    else:
        connection.execute(
            """INSERT INTO stk_analysts (owner_id, display_name, bio, price_cents, currency, payment_link,
            payment_instructions, accepting, created_at, updated_at) VALUES (:owner_id, :display_name, :bio,
            :price_cents, :currency, :payment_link, :payment_instructions, :accepting, :updated_at, :updated_at)""",
            {**values, "owner_id": me["id"]},
        )
    connection.commit()
    found = analyst(connection, me["id"])
    assert found is not None
    if public.link_for(connection, APP, PAGE_KIND, me["id"]):
        public.create_link(
            connection,
            APP,
            PAGE_KIND,
            me["id"],
            title=f"{found['display_name']}: stock ideas",
        )
    return found


def create_link(connection: sqlite3.Connection, me: dict[str, Any]) -> dict[str, Any]:
    require_adult(me, "A public analyst page")
    found = analyst(connection, me["id"]) or save_analyst(connection, me, {})
    return public.create_link(
        connection,
        APP,
        PAGE_KIND,
        me["id"],
        title=f"{found['display_name']}: stock ideas",
    )


def revoke_link(connection: sqlite3.Connection, me: dict[str, Any]) -> int:
    return public.revoke(connection, APP, PAGE_KIND, me["id"])


def _subscriber_state(row: dict[str, Any]) -> str:
    if (
        row["status"] == "active"
        and row["paid_until"]
        and row["paid_until"] < kit.today()
    ):
        return "expired"
    return row["status"]


def _subscriber_view(
    row: dict[str, Any], payments: list[dict[str, Any]] | None = None
) -> dict[str, Any]:
    out = {k: v for k, v in row.items() if k != "code_hash"}
    out["status"] = _subscriber_state(row)
    out["has_code"] = bool(row.get("code_hash"))
    if payments is not None:
        out["payments"] = payments
    return out


def _hash_code(subscriber_id: str, code: str) -> str:
    return hashlib.sha256(
        f"{subscriber_id}:{code.strip().upper()}".encode()
    ).hexdigest()


def _new_code() -> str:
    raw = "".join(secrets.choice(CODE_ALPHABET) for _ in range(12))
    return f"{raw[:4]}-{raw[4:8]}-{raw[8:]}"


def _new_reference() -> str:
    return "ST-" + "".join(secrets.choice(CODE_ALPHABET) for _ in range(6))


def subscribers(connection: sqlite3.Connection, me: dict[str, Any]) -> dict[str, Any]:
    require_adult(me, "Subscribers")
    rows = kit.fetch_all(
        connection,
        "SELECT * FROM stk_subscribers WHERE analyst_id = ? ORDER BY status = 'pending' DESC, updated_at DESC",
        (me["id"],),
    )
    payments = kit.fetch_all(
        connection,
        "SELECT * FROM stk_payments WHERE analyst_id = ? ORDER BY paid_on DESC, created_at DESC",
        (me["id"],),
    )
    by_sub: dict[str, list[dict[str, Any]]] = {}
    for payment in payments:
        by_sub.setdefault(payment["subscriber_id"], []).append(payment)
    profile = analyst(connection, me["id"])
    month = kit.today()[:7]
    valid = [p for p in payments if not p["voided_at"]]
    viewed = [_subscriber_view(r, by_sub.get(r["id"], [])) for r in rows]
    active = [s for s in viewed if s["status"] == "active"]
    return {
        "subscribers": viewed,
        "earnings": {
            "currency": (profile or {}).get("currency", "USD"),
            "total_cents": sum(p["amount_cents"] for p in valid),
            "this_month_cents": sum(
                p["amount_cents"] for p in valid if p["paid_on"][:7] == month
            ),
            "payments": len(valid),
            "active": len(active),
            "pending": sum(1 for s in viewed if s["status"] == "pending"),
            "monthly_cents": len(active) * int((profile or {}).get("price_cents") or 0),
        },
    }


def _own_subscriber(
    connection: sqlite3.Connection, me: dict[str, Any], subscriber_id: str
) -> dict[str, Any]:
    require_adult(me, "Subscribers")
    found = kit.fetch_one(
        connection,
        "SELECT * FROM stk_subscribers WHERE id = ? AND analyst_id = ?",
        (subscriber_id, me["id"]),
    )
    if not found:
        raise LookupError("That subscriber was not found.")
    return found


def _paid_until(connection: sqlite3.Connection, subscriber_id: str) -> str:
    until: date | None = None
    for payment in kit.fetch_all(
        connection,
        "SELECT * FROM stk_payments WHERE subscriber_id = ? AND voided_at IS NULL ORDER BY paid_on, created_at",
        (subscriber_id,),
    ):
        start = date.fromisoformat(payment["paid_on"])
        base = until if until and until > start else start
        until = base + timedelta(days=30 * int(payment["months"]))
    return until.isoformat() if until else ""


def _counts_as_revenue() -> bool:
    """On a person's own computer an analyst's subscription income is the account's money and joins the Business
    hub's revenue. Hosted, a member's income is their own, not the organization's (like a Videos creator payout)."""
    return suite.actor() is None


def _access_email(
    connection: sqlite3.Connection,
    me: dict[str, Any],
    sub: dict[str, Any],
    code: str,
    source: Any,
) -> None:
    profile = analyst(connection, me["id"]) or {"display_name": me["name"]}
    link = public.link_for(connection, APP, PAGE_KIND, me["id"])
    where = kit.public_url(link["path"], source) if link else "the analyst's page"
    kit.queue_email(
        connection,
        APP,
        "stocks_access",
        to=sub["email"],
        subject=f"Your access to {profile['display_name']}'s stock ideas",
        body=(
            f"Hello {sub['name']},\n\nThank you: your payment was received. Open {where} and choose "
            f'"I am a subscriber", then enter this email address and your access code:\n\n    {code}\n\n'
            f"Your subscription runs until {sub['paid_until'] or 'the end of your paid period'}.\n\n"
            "Stock ideas are opinions, not financial advice.\n"
        ),
        ref_type="subscriber",
        ref_id=sub["id"],
    )


TWIN_SECONDS = 120


def _recent_twin(
    connection: sqlite3.Connection,
    subscriber_id: str,
    amount: int,
    months: int,
    paid_on: str,
) -> dict[str, Any] | None:
    """The same payment (amount, months and date) recorded in the last two minutes, if there is one."""
    for row in kit.fetch_all(
        connection,
        """SELECT * FROM stk_payments WHERE subscriber_id = ? AND amount_cents = ? AND months = ? AND paid_on = ?
        AND voided_at IS NULL ORDER BY created_at DESC LIMIT 5""",
        (subscriber_id, amount, months, paid_on),
    ):
        age = kit.utcnow() - kit.parse_time(row["created_at"])
        if age <= timedelta(seconds=TWIN_SECONDS):
            return row
    return None


def mark_paid(
    connection: sqlite3.Connection,
    me: dict[str, Any],
    subscriber_id: str,
    raw: dict[str, Any],
    source: Any = None,
) -> dict[str, Any]:
    """The analyst confirms a payment arrived (through their own payment link or bank). Aira records it, extends the
    subscription and gives the subscriber an access code (emailed through the outbox and shown here once)."""
    sub = _own_subscriber(connection, me, subscriber_id)
    profile = analyst(connection, me["id"])
    if not profile:
        raise ValueError("Set up your analyst page first.")
    months = kit.integer(
        raw.get("months"), "the number of months", default=1, minimum=1, maximum=24
    )
    amount = (
        price_input(raw, "amount", "the amount received", required=False)
        or profile["price_cents"] * months
    )
    if amount <= 0:
        raise ValueError("Enter the amount you received.")
    paid_on = kit.day(raw.get("paid_on"), "the payment date") or kit.today()
    if date.fromisoformat(paid_on) > today():
        raise ValueError("The payment date cannot be in the future.")
    # Take the write lock before looking for a twin, so two requests at once (two tabs, a retry after a slow
    # network) cannot both record the same payment: the second waits here and then finds the first.
    connection.execute(
        "UPDATE stk_subscribers SET updated_at = updated_at WHERE id = ?", (sub["id"],)
    )
    twin = _recent_twin(connection, sub["id"], amount, months, paid_on)
    if twin:
        connection.commit()
        again = _own_subscriber(connection, me, subscriber_id)
        return {
            "subscriber": _subscriber_view(again),
            "code": "",
            "duplicate": True,
            "message": "This payment was already recorded a moment ago, so it was not counted twice.",
        }
    payment_id = kit.new_id()
    connection.execute(
        """INSERT INTO stk_payments (id, subscriber_id, analyst_id, amount_cents, currency, months, paid_on, note, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            payment_id,
            sub["id"],
            me["id"],
            amount,
            profile["currency"],
            months,
            paid_on,
            kit.text(raw.get("note"), "a note", max_len=200),
            kit.now(),
        ),
    )
    code = ""
    changes: dict[str, Any] = {
        "status": "active",
        "paid_until": _paid_until(connection, sub["id"]),
        "updated_at": kit.now(),
    }
    if not sub["code_hash"]:
        code = _new_code()
        changes["code_hash"] = _hash_code(sub["id"], code)
    kit.update_row(connection, "stk_subscribers", sub["id"], changes)
    if _counts_as_revenue():
        kit.record_revenue(
            connection,
            APP,
            source_type="idea_subscription",
            source_id=payment_id,
            amount_cents=amount,
            currency_code=profile["currency"],
            label=f"Stock ideas subscription ({months} month{'s' if months > 1 else ''})",
            customer=sub["email"],
            at=f"{paid_on}T12:00:00Z",
        )
    kit.log(
        connection,
        APP,
        "subscription_paid",
        f"{sub['name']} paid for stock ideas",
        ref_type="subscriber",
        ref_id=sub["id"],
        amount_cents=amount,
        actor=me["name"],
    )
    fresh = {**sub, **changes}
    if code:
        _access_email(connection, me, fresh, code, source)
    connection.commit()
    return {"subscriber": _subscriber_view(fresh), "code": code}


def new_code(
    connection: sqlite3.Connection,
    me: dict[str, Any],
    subscriber_id: str,
    source: Any = None,
) -> dict[str, Any]:
    sub = _own_subscriber(connection, me, subscriber_id)
    if sub["status"] != "active":
        raise ValueError(
            "Record a payment first: access codes are for paying subscribers."
        )
    code = _new_code()
    kit.update_row(
        connection,
        "stk_subscribers",
        sub["id"],
        {"code_hash": _hash_code(sub["id"], code), "updated_at": kit.now()},
    )
    _access_email(connection, me, sub, code, source)
    connection.commit()
    return {
        "subscriber": _subscriber_view(_own_subscriber(connection, me, subscriber_id)),
        "code": code,
    }


def cancel_subscriber(
    connection: sqlite3.Connection, me: dict[str, Any], subscriber_id: str
) -> dict[str, Any]:
    sub = _own_subscriber(connection, me, subscriber_id)
    kit.update_row(
        connection,
        "stk_subscribers",
        sub["id"],
        {"status": "cancelled", "code_hash": "", "updated_at": kit.now()},
    )
    connection.commit()
    return _subscriber_view(_own_subscriber(connection, me, subscriber_id))


def void_payment(
    connection: sqlite3.Connection, me: dict[str, Any], payment_id: str
) -> dict[str, Any]:
    """A payment marked by mistake (or refunded): it stops counting, and the paid period shrinks."""
    require_adult(me, "Subscribers")
    payment = kit.fetch_one(
        connection,
        "SELECT * FROM stk_payments WHERE id = ? AND analyst_id = ?",
        (payment_id, me["id"]),
    )
    if not payment:
        raise LookupError("That payment was not found.")
    if not payment["voided_at"]:
        connection.execute(
            "UPDATE stk_payments SET voided_at = ? WHERE id = ?",
            (kit.now(), payment_id),
        )
        kit.void_revenue(connection, APP, "idea_subscription", payment_id)
        until = _paid_until(connection, payment["subscriber_id"])
        sub = kit.require_row(
            connection, "stk_subscribers", payment["subscriber_id"], "subscriber"
        )
        changes: dict[str, Any] = {"paid_until": until, "updated_at": kit.now()}
        if not until and sub["status"] == "active":
            changes.update({"status": "pending", "code_hash": ""})
        kit.update_row(connection, "stk_subscribers", sub["id"], changes)
        connection.commit()
    return _subscriber_view(
        kit.require_row(
            connection, "stk_subscribers", payment["subscriber_id"], "subscriber"
        )
    )


def _public_idea(row: dict[str, Any]) -> dict[str, Any]:
    view = _idea_view(row)
    return {
        k: view[k]
        for k in (
            "id",
            "symbol",
            "name",
            "currency",
            "side",
            "entry_cents",
            "target_cents",
            "stop_cents",
            "horizon_days",
            "opened_on",
            "expires_on",
            "reasoning",
            "access",
            "status",
            "last_cents",
            "close_cents",
            "closed_on",
            "return_pct",
            "days_left",
            "upside_pct",
            "risk_pct",
        )
    }


def analyst_page(connection: sqlite3.Connection, owner_id: str) -> dict[str, Any]:
    """What anyone with the link sees: the track record (every closed idea, paid ones too), free open ideas, and
    how many paid ideas are open. Never subscribers or payments."""
    profile = analyst(connection, owner_id)
    if not profile:
        raise LookupError("This page is no longer available.")
    track_ideas(connection, owner_id)
    rows = kit.fetch_all(
        connection,
        "SELECT * FROM stk_ideas WHERE owner_id = ? ORDER BY opened_on DESC, created_at DESC",
        (owner_id,),
    )
    open_rows = [r for r in rows if r["status"] == "open"]
    return {
        "analyst": {
            k: profile[k]
            for k in (
                "display_name",
                "bio",
                "price_cents",
                "currency",
                "payment_link",
                "payment_instructions",
                "accepting",
            )
        },
        "record": track_record(rows),
        "free": [_public_idea(r) for r in open_rows if r["access"] == "free"],
        "paid_open": sum(1 for r in open_rows if r["access"] == "paid"),
        "closed": [_public_idea(r) for r in rows if r["status"] != "open"][:60],
        "subscribers": kit.count(
            connection,
            "SELECT COUNT(*) FROM stk_subscribers WHERE analyst_id = ? AND status = 'active' AND paid_until >= ?",
            (owner_id, kit.today()),
        ),
        "disclaimer": DISCLAIMER,
    }


def public_subscribe(
    connection: sqlite3.Connection, owner_id: str, payload: dict[str, Any]
) -> dict[str, Any]:
    profile = analyst(connection, owner_id)
    if not profile:
        raise LookupError("This page is no longer available.")
    if not profile["accepting"] or profile["price_cents"] <= 0:
        raise ValueError("This analyst is not taking new subscribers right now.")
    name = kit.text(payload.get("name"), "your name", max_len=80, required=True)
    email = kit.email(payload.get("email"), required=True)
    note = kit.text(payload.get("note"), "a note", max_len=300)
    existing = kit.fetch_one(
        connection,
        "SELECT * FROM stk_subscribers WHERE analyst_id = ? AND email = ?",
        (owner_id, email),
    )
    if existing:
        # The same answer whatever the row's state, so a visitor cannot learn whether an address pays this analyst,
        # and nothing the analyst sees (the name, the first note) is changed by whoever types that address.
        reference = existing["reference"]
        changes: dict[str, Any] = {"updated_at": kit.now()}
        if existing["status"] == "cancelled":
            changes["status"] = "pending"
        if note and not existing["note"]:
            changes["note"] = note
        kit.update_row(connection, "stk_subscribers", existing["id"], changes)
    else:
        if (
            kit.count(
                connection,
                "SELECT COUNT(*) FROM stk_subscribers WHERE analyst_id = ? AND status = 'pending'",
                (owner_id,),
            )
            >= 500
        ):
            raise ValueError(
                "Too many people are waiting to be confirmed. Try again later."
            )
        reference = _new_reference()
        connection.execute(
            """INSERT INTO stk_subscribers (id, analyst_id, name, email, note, reference, status, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, 'pending', ?, ?)""",
            (
                kit.new_id(),
                owner_id,
                name,
                email,
                note,
                reference,
                kit.now(),
                kit.now(),
            ),
        )
    kit.log(
        connection,
        APP,
        "subscriber",
        f"{name} asked to subscribe to your stock ideas",
        public=True,
        ref_type="analyst",
        ref_id=owner_id,
    )
    connection.commit()
    return {
        "status": "pending",
        "reference": reference,
        "price_cents": profile["price_cents"],
        "currency": profile["currency"],
        "payment_link": profile["payment_link"],
        "payment_instructions": profile["payment_instructions"],
        "message": f"Thank you, {name}. Pay with the reference {reference}. When {profile['display_name']} confirms "
        "your payment, you get an access code by email. If you already subscribe, use your access code below.",
    }


def paid_ideas(
    connection: sqlite3.Connection, owner_id: str, email: str, code: str
) -> tuple[int, dict[str, Any]]:
    """The paid open ideas for a subscriber with a valid code and a paid period that has not ended."""
    try:
        address = kit.email(email, required=True)
    except ValueError as error:
        return 400, {"error": str(error)}
    sub = kit.fetch_one(
        connection,
        "SELECT * FROM stk_subscribers WHERE analyst_id = ? AND email = ?",
        (owner_id, address),
    )
    wrong = (
        403,
        {"error": "That email and access code do not match an active subscription."},
    )
    if not sub or not sub["code_hash"] or not code:
        return wrong
    if not hmac.compare_digest(sub["code_hash"], _hash_code(sub["id"], code)):
        return wrong
    if _subscriber_state(sub) != "active":
        return 403, {"error": "Your subscription has ended. Pay again to renew it."}
    track_ideas(connection, owner_id)
    rows = kit.fetch_all(
        connection,
        "SELECT * FROM stk_ideas WHERE owner_id = ? AND access = 'paid' AND status = 'open' ORDER BY opened_on DESC",
        (owner_id,),
    )
    return 200, {
        "ideas": [_public_idea(r) for r in rows],
        "paid_until": sub["paid_until"],
        "name": sub["name"],
    }


def expire_subscribers(connection: sqlite3.Connection) -> int:
    rows = kit.fetch_all(
        connection,
        "SELECT id FROM stk_subscribers WHERE status = 'active' AND paid_until != '' AND paid_until < ?",
        (kit.today(),),
    )
    for row in rows:
        kit.update_row(
            connection,
            "stk_subscribers",
            row["id"],
            {"status": "expired", "updated_at": kit.now()},
        )
    connection.commit()
    return len(rows)


def ideas_overview(
    connection: sqlite3.Connection, me: dict[str, Any], source: Any = None
) -> dict[str, Any]:
    require_adult(me, "Publishing stock ideas")
    ideas = my_ideas(connection, me)
    link = public.link_for(connection, APP, PAGE_KIND, me["id"])
    if link:
        link = {**link, "url": kit.public_url(link["path"], source)}
    return {
        "ideas": ideas,
        "record": track_record(ideas),
        "analyst": analyst(connection, me["id"]),
        "link": link,
        **subscribers(connection, me),
        "disclaimer": DISCLAIMER,
    }


# ---------------------------------------------------------------------------
# a person's day: overview, Today, pulse and background work
# ---------------------------------------------------------------------------


def accounts_brief(
    connection: sqlite3.Connection, me: dict[str, Any], *, fetch: bool = True
) -> list[dict[str, Any]]:
    kinds = ("practice", "real") if life.is_adult(me) else ("practice",)
    out = []
    for account in _accounts(connection, me):
        if account["kind"] not in kinds:
            continue
        if (
            not kit.count(
                connection,
                "SELECT COUNT(*) FROM stk_txns WHERE account_id = ?",
                (account["id"],),
            )
            and account["kind"] == "real"
        ):
            continue
        out.append(account_summary(connection, account, fetch=fetch))
    return out


def overview(connection: sqlite3.Connection, me: dict[str, Any]) -> dict[str, Any]:
    accounts = accounts_brief(connection, me)
    watch = watchlists(connection, me)
    items = [
        i for entry in watch["lists"] for i in entry["items"] if not i.get("missing")
    ]
    movers = sorted(items, key=lambda i: -abs(i.get("change_pct") or 0))[:5]
    return {
        "accounts": [
            {
                k: a[k]
                for k in (
                    "id",
                    "kind",
                    "name",
                    "currency",
                    "equity_cents",
                    "value_cents",
                    "day_change_cents",
                    "day_change_pct",
                    "return_pct",
                    "total_pl_cents",
                )
            }
            for a in accounts
        ],
        "movers": movers,
        "alerts": recent_alerts(connection, me, unseen_only=False),
        "adult": life.is_adult(me),
        "disclaimer": DISCLAIMER,
    }


def run_background(connection: sqlite3.Connection) -> dict[str, int]:
    store.ensure(connection)
    return {
        "alerts": len(check_alerts(connection)),
        "ideas_closed": track_ideas(connection),
        "predictions_scored": score_predictions(connection),
        "subscriptions_expired": expire_subscribers(connection),
    }
