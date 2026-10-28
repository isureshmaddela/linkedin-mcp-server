"""The StocksAgent for the AI app: what it reads for a question and the actions it may take.

Reading: the person's portfolios (value and today's change), the biggest movers on their watchlist and the alerts
that fired. Actions: add a stock to the watchlist and create a price alert (minor: easy to undo), and buy or sell in
the practice portfolio (major: they wait for approval unless the person lets the agent act alone). The agent never
touches real money: real holdings are only recorded by the person.
"""

from __future__ import annotations

import re
import sqlite3
from typing import Any

from aira.suite import kit
from aira.suite.stocks import market, service, store


def resolve(connection: sqlite3.Connection, value: Any) -> str:
    """A symbol from what a person or the model wrote: "AAPL", "apple", "Reliance", "tcs.ns"."""
    text = str(value or "").strip()
    if not text:
        raise ValueError("Say which stock (for example AAPL or Reliance).")
    upper = text.upper()
    if upper in market.NAMES:
        return upper
    matches = market.search_universe(text, limit=3)  # "TCS" -> TCS.NS, "apple" -> AAPL
    if matches:
        return matches[0]["symbol"]
    # The model often writes the full company name: "Apple Inc.", "Infosys Ltd", "Apple shares".
    plain = re.sub(
        r"[\s,]+(inc|incorporated|ltd|limited|corp|corporation|co|company|plc|llc|shares?|stocks?)\.?$",
        "",
        text,
        flags=re.IGNORECASE,
    ).strip()
    if plain and plain != text:
        return resolve(connection, plain)
    return market.clean_symbol(text)


def facts(connection: sqlite3.Connection, me: dict[str, Any]) -> list[str]:
    store.ensure(connection)
    out: list[str] = []
    for account in service.accounts_brief(connection, me):
        code = account["currency"]
        what = (
            "Practice portfolio" if account["kind"] == "practice" else "Real holdings"
        )
        worth = (
            account["equity_cents"]
            if account["kind"] == "practice"
            else account["value_cents"]
        )
        line = (
            f"{what} ({code}): worth {kit.fmt_money(worth, code)}, today {service.signed_money(account['day_change_cents'], code)} "
            f"({account['day_change_pct']:+.2f}%)"
        )
        if account["return_pct"] is not None:
            line += f", {account['return_pct']:+.2f}% since it started"
        else:
            line += f", profit or loss so far {service.signed_money(account['total_pl_cents'], code)}"
        out.append(line + f"; {len(account['holdings'])} holdings.")
        if account["best"]:
            best = account["best"][0]
            out.append(
                f"Best earner in {what.lower()}: {best['name']} {service.signed_money(best['total_cents'], code)}."
            )
        if account["worst"]:
            worst = account["worst"][0]
            out.append(
                f"Biggest loser in {what.lower()}: {worst['name']} {service.signed_money(worst['total_cents'], code)}."
            )
    data = service.watchlists(connection, me)
    items = [
        i for entry in data["lists"] for i in entry["items"] if not i.get("missing")
    ]
    for item in sorted(items, key=lambda i: -abs(i.get("change_pct") or 0))[:4]:
        price = service.fmt_price(
            item["price_cents"], item["currency"], index=item["index"]
        )
        out.append(
            f"Watchlist: {item['name']} ({item['symbol']}) {price}, {service.arrow(item['change_pct'])} {item['change_pct']:+.2f}% today."
        )
    for alert in service.recent_alerts(connection, me, unseen_only=False)[:3]:
        out.append(f"Alert fired: {service.fired_text(alert)}")
    active = [a for a in service.list_alerts(connection, me) if a["active"]]
    if active:
        out.append(
            f"{len(active)} price alerts are waiting: "
            + "; ".join(a["text"] for a in active[:3])
            + "."
        )
    if not out:
        out.append("No portfolio, watchlist or alerts yet in Stocks.")
    out.append("Stocks facts are not financial advice.")
    return out


def _watch(
    connection: sqlite3.Connection, me: dict[str, Any], args: dict[str, Any]
) -> str:
    store.ensure(connection)
    symbol = resolve(connection, args.get("symbol") or args.get("stock"))
    added = service.watch(connection, me, {"symbol": symbol})
    return f"Added {symbol} to {added['list']['name']}."


def _alert(
    connection: sqlite3.Connection, me: dict[str, Any], args: dict[str, Any]
) -> str:
    store.ensure(connection)
    symbol = resolve(connection, args.get("symbol") or args.get("stock"))
    condition = str(args.get("condition") or args.get("kind") or "").strip().lower()
    kind = {
        "above": "above",
        "over": "above",
        "rises": "above",
        "below": "below",
        "under": "below",
        "falls": "below",
        "move": "move",
        "moves": "move",
    }.get(condition, "")
    if not kind:
        kind = "move" if args.get("percent") not in (None, "") else "above"
    body: dict[str, Any] = {"symbol": symbol, "kind": kind}
    if kind == "move":
        body["move_pct"] = args.get("percent") or args.get("move_pct")
    else:
        body["price"] = args.get("price")
    alert = service.create_alert(connection, me, body)
    return f"I will tell you when {alert['text']}."


def _trade(side: str):
    def run(
        connection: sqlite3.Connection, me: dict[str, Any], args: dict[str, Any]
    ) -> str:
        store.ensure(connection)
        symbol = resolve(connection, args.get("symbol") or args.get("stock"))
        body: dict[str, Any] = {"symbol": symbol, "side": side}
        if args.get("quantity") not in (None, ""):
            body["quantity"] = args.get("quantity")
        elif args.get("amount") not in (None, ""):
            body["amount"] = args.get("amount")
        else:
            raise ValueError("Say how many shares.")
        return service.practice_trade(connection, me, body)["message"]

    return run


def _describe_alert(args: dict[str, Any]) -> str:
    what = args.get("symbol") or args.get("stock") or "a stock"
    if args.get("percent") not in (None, ""):
        return f"Alert me when {what} moves {args['percent']}% in a day"
    return f"Alert me when {what} goes {args.get('condition') or 'above'} {args.get('price', '?')}"


def agent():
    from aira.suite.ai import agents

    actions = (
        agents.Action(
            "add_to_watchlist",
            "Add a stock to the watchlist",
            "minor",
            {"symbol": "the stock symbol or company name (AAPL, Reliance, TCS.NS)"},
            _watch,
            lambda a: (
                f"Add {a.get('symbol') or a.get('stock') or 'a stock'} to the watchlist"
            ),
        ),
        agents.Action(
            "create_price_alert",
            "Create a price alert",
            "minor",
            {
                "symbol": "the stock symbol or company name",
                "condition": "above, below or move",
                "price": "the price for above or below (a number)",
                "percent": "for move: the daily move in percent (optional)",
            },
            _alert,
            _describe_alert,
        ),
        agents.Action(
            "practice_buy",
            "Buy in the practice portfolio",
            "major",
            {
                "symbol": "the stock symbol or company name",
                "quantity": "how many shares",
            },
            _trade("buy"),
            lambda a: (
                f"Buy {a.get('quantity', '?')} {a.get('symbol') or a.get('stock') or ''} with practice money".replace(
                    "  ", " "
                )
            ),
        ),
        agents.Action(
            "practice_sell",
            "Sell in the practice portfolio",
            "major",
            {
                "symbol": "the stock symbol or company name",
                "quantity": "how many shares",
            },
            _trade("sell"),
            lambda a: (
                f"Sell {a.get('quantity', '?')} {a.get('symbol') or a.get('stock') or ''} from the practice portfolio".replace(
                    "  ", " "
                )
            ),
        ),
    )
    return agents.Agent(
        "stocks",
        "StocksAgent",
        "stocks",
        "",
        "Your portfolios, watchlist movers and price alerts; practice trades.",
        (
            "stock",
            "stocks",
            "share",
            "shares",
            "market",
            "markets",
            "portfolio",
            "invest",
            "investing",
            "investment",
            "nifty",
            "sensex",
            "nasdaq",
            "ticker",
            "trade",
            "trading",
            "watchlist",
            "dividend",
            "equity",
            "equities",
            "nyse",
            "nse",
            "bse",
            "s&p 500",
            "dow jones",
            "price alert",
            "holdings",
            "gainers",
            "losers",
        ),
        "auto_minor",
        actions,
        reader=facts,
    )
