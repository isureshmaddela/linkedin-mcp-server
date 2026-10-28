"""Stocks routes (``/api/stocks/*``), the analyst's public page, the Today card, the pulse, the background task and
the StocksAgent of the AI app.

Every route works for the person asking (:func:`aira.suite.life.current`); :mod:`aira.suite.stocks.service` holds
the rules. Symbols travel as a ``symbol`` query parameter or in the body (they contain ``^``, ``.`` and ``&``).
Loaded by :func:`aira.suite.load_apps`.
"""

from __future__ import annotations

from typing import Any

from aira.airaapi.http import Raw, Request, aira_route
from aira.suite import background, kit, life, public
from aira.suite.ai import agents
from aira.suite.stocks import assistant, links, market, service, store

links.install()  # hosted: public links stay in the organization index (see aira.suite.stocks.links)

APP = store.APP
BASE = "/api/stocks"
ID = "([0-9a-f]{32})"
CSV = "text/csv; charset=utf-8"


def _open(request: Request) -> tuple[Any, dict[str, Any]]:
    connection = request.connect()
    store.ensure(connection)
    me = life.current(request, connection)
    return connection, me


def _id(request: Request) -> str:
    return request.match.group(1)


# ---------------------------------------------------------------------------
# markets and one stock
# ---------------------------------------------------------------------------


@aira_route("GET", f"{BASE}/overview")
def overview(request: Request) -> tuple[int, dict[str, Any]]:
    connection, me = _open(request)
    with connection:
        return 200, service.overview(connection, me)


@aira_route("GET", f"{BASE}/markets")
def markets(request: Request) -> tuple[int, dict[str, Any]]:
    connection, _me = _open(request)
    with connection:
        return 200, service.markets(connection, request.q("market", "us") or "us")


@aira_route("GET", f"{BASE}/search")
def search(request: Request) -> tuple[int, dict[str, Any]]:
    connection, _me = _open(request)
    with connection:
        return 200, service.search(connection, request.q("q"))


@aira_route("GET", f"{BASE}/stock")
def stock(request: Request) -> tuple[int, dict[str, Any]]:
    connection, me = _open(request)
    with connection:
        return 200, service.stock(
            connection, me, request.q("symbol"), request.q("range", "1y") or "1y"
        )


@aira_route("GET", f"{BASE}/export/log")
def log_export(request: Request) -> Raw:
    connection, _me = _open(request)
    with connection:
        body, name = service.log_csv(
            connection, request.q("symbol"), request.q("range", "1y") or "1y"
        )
    return Raw(200, body, CSV, name)


@aira_route("GET", f"{BASE}/predict")
def prediction(request: Request) -> tuple[int, dict[str, Any]]:
    connection, _me = _open(request)
    with connection:
        return 200, service.prediction(
            connection, request.q("symbol"), explain=bool(request.qbool("explain"))
        )


# ---------------------------------------------------------------------------
# the person's own predictions
# ---------------------------------------------------------------------------


@aira_route("GET", f"{BASE}/predictions")
def predictions_list(request: Request) -> tuple[int, dict[str, Any]]:
    connection, me = _open(request)
    with connection:
        return 200, service.list_predictions(connection, me)


@aira_route("POST", f"{BASE}/predictions")
def predictions_create(request: Request) -> tuple[int, dict[str, Any]]:
    connection, me = _open(request)
    with connection:
        made = service.create_prediction(connection, me, request.payload)
    request.audit("stocks.prediction_add", "prediction", made["id"])
    return 201, {"prediction": made}


@aira_route("DELETE", f"{BASE}/predictions/{ID}")
def predictions_delete(request: Request) -> tuple[int, dict[str, Any]]:
    connection, me = _open(request)
    with connection:
        service.delete_prediction(connection, me, _id(request))
    return 200, {"deleted": True}


@aira_route("GET", f"{BASE}/export/predictions")
def predictions_export(request: Request) -> Raw:
    connection, me = _open(request)
    with connection:
        return Raw(
            200, service.predictions_csv(connection, me), CSV, "stock-predictions.csv"
        )


# ---------------------------------------------------------------------------
# watchlists
# ---------------------------------------------------------------------------


@aira_route("GET", f"{BASE}/watchlist")
def watchlist(request: Request) -> tuple[int, dict[str, Any]]:
    connection, me = _open(request)
    with connection:
        return 200, service.watchlists(connection, me)


@aira_route("POST", f"{BASE}/watchlist")
def watchlist_add(request: Request) -> tuple[int, dict[str, Any]]:
    connection, me = _open(request)
    with connection:
        added = service.watch(connection, me, request.payload)
    return 201, added


@aira_route("DELETE", f"{BASE}/watchlist/items/{ID}")
def watchlist_remove(request: Request) -> tuple[int, dict[str, Any]]:
    connection, me = _open(request)
    with connection:
        service.unwatch(connection, me, _id(request))
    return 200, {"deleted": True}


@aira_route("POST", f"{BASE}/watchlists")
def watchlists_create(request: Request) -> tuple[int, dict[str, Any]]:
    connection, me = _open(request)
    with connection:
        return 201, {"list": service.create_watchlist(connection, me, request.payload)}


@aira_route("PATCH", f"{BASE}/watchlists/{ID}")
def watchlists_rename(request: Request) -> tuple[int, dict[str, Any]]:
    connection, me = _open(request)
    with connection:
        return 200, {
            "list": service.rename_watchlist(
                connection, me, _id(request), request.payload
            )
        }


@aira_route("DELETE", f"{BASE}/watchlists/{ID}")
def watchlists_delete(request: Request) -> tuple[int, dict[str, Any]]:
    connection, me = _open(request)
    with connection:
        service.delete_watchlist(connection, me, _id(request))
    return 200, {"deleted": True}


# ---------------------------------------------------------------------------
# alerts
# ---------------------------------------------------------------------------


@aira_route("GET", f"{BASE}/alerts")
def alerts_list(request: Request) -> tuple[int, dict[str, Any]]:
    connection, me = _open(request)
    with connection:
        return 200, {
            "alerts": service.list_alerts(connection, me),
            "kinds": list(service.ALERT_KINDS),
        }


@aira_route("POST", f"{BASE}/alerts")
def alerts_create(request: Request) -> tuple[int, dict[str, Any]]:
    connection, me = _open(request)
    with connection:
        alert = service.create_alert(connection, me, request.payload)
    request.audit("stocks.alert_add", "alert", alert["id"])
    return 201, {"alert": alert}


@aira_route("PATCH", f"{BASE}/alerts/{ID}")
def alerts_update(request: Request) -> tuple[int, dict[str, Any]]:
    connection, me = _open(request)
    with connection:
        return 200, {
            "alert": service.update_alert(connection, me, _id(request), request.payload)
        }


@aira_route("DELETE", f"{BASE}/alerts/{ID}")
def alerts_delete(request: Request) -> tuple[int, dict[str, Any]]:
    connection, me = _open(request)
    with connection:
        service.delete_alert(connection, me, _id(request))
    return 200, {"deleted": True}


@aira_route("POST", f"{BASE}/alerts/{ID}/seen")
def alerts_seen(request: Request) -> tuple[int, dict[str, Any]]:
    connection, me = _open(request)
    with connection:
        return 200, {"alert": service.seen_alert(connection, me, _id(request))}


@aira_route("POST", f"{BASE}/alerts/check")
def alerts_check(request: Request) -> tuple[int, dict[str, Any]]:
    connection, me = _open(request)
    with connection:
        fired = service.check_alerts(connection, me["id"])
        return 200, {"fired": fired, "alerts": service.list_alerts(connection, me)}


# ---------------------------------------------------------------------------
# portfolios
# ---------------------------------------------------------------------------


@aira_route("GET", f"{BASE}/portfolio")
def portfolio(request: Request) -> tuple[int, dict[str, Any]]:
    connection, me = _open(request)
    with connection:
        return 200, service.portfolio(
            connection, me, request.q("kind", "practice") or "practice"
        )


@aira_route("POST", f"{BASE}/practice/start")
def practice_start(request: Request) -> tuple[int, dict[str, Any]]:
    connection, me = _open(request)
    with connection:
        account = service.start_practice(connection, me, request.payload)
    request.audit("stocks.practice_start", "account", account["id"])
    return 201, {"account": account}


@aira_route("POST", f"{BASE}/practice/{ID}/reset")
def practice_reset(request: Request) -> tuple[int, dict[str, Any]]:
    connection, me = _open(request)
    with connection:
        account = service.reset_practice(connection, me, _id(request), request.payload)
    request.audit("stocks.practice_reset", "account", account["id"])
    return 200, {"account": account}


@aira_route("POST", f"{BASE}/practice/trade")
def practice_trade(request: Request) -> tuple[int, dict[str, Any]]:
    connection, me = _open(request)
    with connection:
        result = service.practice_trade(connection, me, request.payload)
    request.audit(
        "stocks.practice_trade",
        "transaction",
        result["trade"]["id"],
        {"side": result["trade"]["side"]},
    )
    return 201, result


@aira_route("POST", f"{BASE}/real/transactions")
def real_add(request: Request) -> tuple[int, dict[str, Any]]:
    connection, me = _open(request)
    with connection:
        result = service.add_real(connection, me, request.payload)
    request.audit("stocks.holding_record", "transaction", result["transaction"]["id"])
    return 201, result


@aira_route("PATCH", f"{BASE}/real/transactions/{ID}")
def real_update(request: Request) -> tuple[int, dict[str, Any]]:
    connection, me = _open(request)
    with connection:
        result = service.update_real(connection, me, _id(request), request.payload)
    request.audit("stocks.holding_update", "transaction", _id(request))
    return 200, result


@aira_route("DELETE", f"{BASE}/real/transactions/{ID}")
def real_delete(request: Request) -> tuple[int, dict[str, Any]]:
    connection, me = _open(request)
    with connection:
        service.delete_real(connection, me, _id(request))
    request.audit("stocks.holding_delete", "transaction", _id(request))
    return 200, {"deleted": True}


@aira_route("GET", f"{BASE}/export/transactions")
def transactions_export(request: Request) -> Raw:
    connection, me = _open(request)
    kind = request.q("kind", "practice") or "practice"
    with connection:
        body = service.transactions_csv(connection, me, kind)
    return Raw(200, body, CSV, f"stock-{kind}-transactions.csv")


@aira_route("GET", f"{BASE}/league")
def league(request: Request) -> tuple[int, dict[str, Any]]:
    connection, me = _open(request)
    with connection:
        return 200, service.league(connection, me)


# ---------------------------------------------------------------------------
# earning: ideas, the analyst page, subscribers
# ---------------------------------------------------------------------------


@aira_route("GET", f"{BASE}/ideas")
def ideas(request: Request) -> tuple[int, dict[str, Any]]:
    connection, me = _open(request)
    with connection:
        return 200, service.ideas_overview(connection, me, request)


@aira_route("POST", f"{BASE}/ideas")
def ideas_create(request: Request) -> tuple[int, dict[str, Any]]:
    connection, me = _open(request)
    with connection:
        idea = service.create_idea(connection, me, request.payload)
    request.audit("stocks.idea_publish", "idea", idea["id"])
    return 201, {"idea": idea}


@aira_route("PATCH", f"{BASE}/ideas/{ID}")
def ideas_update(request: Request) -> tuple[int, dict[str, Any]]:
    connection, me = _open(request)
    with connection:
        idea = service.update_idea(connection, me, _id(request), request.payload)
    request.audit("stocks.idea_update", "idea", idea["id"])
    return 200, {"idea": idea}


@aira_route("DELETE", f"{BASE}/ideas/{ID}")
def ideas_delete(request: Request) -> tuple[int, dict[str, Any]]:
    connection, me = _open(request)
    with connection:
        service.delete_idea(connection, me, _id(request))
    request.audit("stocks.idea_delete", "idea", _id(request))
    return 200, {"deleted": True}


@aira_route("GET", f"{BASE}/export/ideas")
def ideas_export(request: Request) -> Raw:
    connection, me = _open(request)
    with connection:
        return Raw(200, service.ideas_csv(connection, me), CSV, "stock-ideas.csv")


@aira_route("POST", f"{BASE}/analyst")
def analyst_save(request: Request) -> tuple[int, dict[str, Any]]:
    connection, me = _open(request)
    with connection:
        profile = service.save_analyst(connection, me, request.payload)
    request.audit("stocks.analyst_save", "analyst", me["id"])
    return 200, {"analyst": profile}


@aira_route("POST", f"{BASE}/analyst/link")
def analyst_link(request: Request) -> tuple[int, dict[str, Any]]:
    connection, me = _open(request)
    with connection:
        link = service.create_link(connection, me)
    request.audit("stocks.analyst_link", "analyst", me["id"])
    return 200, {"link": {**link, "url": kit.public_url(link["path"], request)}}


@aira_route("DELETE", f"{BASE}/analyst/link")
def analyst_unlink(request: Request) -> tuple[int, dict[str, Any]]:
    connection, me = _open(request)
    with connection:
        revoked = service.revoke_link(connection, me)
    request.audit("stocks.analyst_unlink", "analyst", me["id"])
    return 200, {"revoked": revoked}


@aira_route("POST", f"{BASE}/subscribers/{ID}/paid")
def subscriber_paid(request: Request) -> tuple[int, dict[str, Any]]:
    connection, me = _open(request)
    with connection, kit.origin_of(request):
        result = service.mark_paid(
            connection, me, _id(request), request.payload, request
        )
    request.audit("stocks.subscription_paid", "subscriber", _id(request))
    return 200, result


@aira_route("POST", f"{BASE}/subscribers/{ID}/code")
def subscriber_code(request: Request) -> tuple[int, dict[str, Any]]:
    connection, me = _open(request)
    with connection, kit.origin_of(request):
        result = service.new_code(connection, me, _id(request), request)
    request.audit("stocks.subscriber_code", "subscriber", _id(request))
    return 200, result


@aira_route("POST", f"{BASE}/subscribers/{ID}/cancel")
def subscriber_cancel(request: Request) -> tuple[int, dict[str, Any]]:
    connection, me = _open(request)
    with connection:
        result = service.cancel_subscriber(connection, me, _id(request))
    request.audit("stocks.subscriber_cancel", "subscriber", _id(request))
    return 200, {"subscriber": result}


@aira_route("POST", f"{BASE}/payments/{ID}/void")
def payment_void(request: Request) -> tuple[int, dict[str, Any]]:
    connection, me = _open(request)
    with connection:
        result = service.void_payment(connection, me, _id(request))
    request.audit("stocks.payment_void", "payment", _id(request))
    return 200, {"subscriber": result}


# ---------------------------------------------------------------------------
# the analyst's public page (anyone with the link, no account)
# ---------------------------------------------------------------------------


@public.page(APP, service.PAGE_KIND)
def analyst_page(connection: Any, link: dict[str, Any]) -> dict[str, Any]:
    store.ensure(connection)
    # The analyst's own page and payment link, never the business's.
    return {**service.analyst_page(connection, str(link["ref_id"])), "brand": False}


@public.action(APP, service.PAGE_KIND, "subscribe")
def analyst_subscribe(
    connection: Any,
    link: dict[str, Any],
    payload: dict[str, Any],
    visitor: dict[str, Any],
) -> tuple[int, dict[str, Any]]:
    store.ensure(connection)
    try:
        return 201, service.public_subscribe(connection, str(link["ref_id"]), payload)
    except ValueError as error:
        return 400, {"error": str(error)}


@public.query(APP, service.PAGE_KIND, "paid")
def analyst_paid(
    connection: Any, link: dict[str, Any], params: dict[str, Any]
) -> tuple[int, dict[str, Any]]:
    store.ensure(connection)
    return service.paid_ideas(
        connection,
        str(link["ref_id"]),
        str(params.get("email") or ""),
        str(params.get("secret") or ""),
    )


# ---------------------------------------------------------------------------
# Today, pulse, background work and the agent
# ---------------------------------------------------------------------------


def _day_line(account: dict[str, Any]) -> dict[str, Any]:
    code = account["currency"]
    worth = (
        account["equity_cents"]
        if account["kind"] == "practice"
        else account["value_cents"]
    )
    change = account["day_change_cents"]
    what = "Practice portfolio" if account["kind"] == "practice" else "My holdings"
    return {
        "text": f"{what} ({code}): {kit.fmt_money(worth, code)}",
        "sub": f"{service.arrow(change)} {service.signed_money(change, code)} ({account['day_change_pct']:+.2f}%) today",
        "done": False,
        "link": f"#/stocks?tab=portfolio&kind={account['kind']}",
    }


@life.today(APP)
def _today(connection: Any, me: dict[str, Any]) -> list[dict[str, Any]]:
    store.ensure(connection)
    has_accounts = kit.count(
        connection,
        "SELECT COUNT(*) FROM stk_txns WHERE owner_id = ?",
        (me["id"],),
    )
    fired = service.recent_alerts(connection, me)
    pending = (
        kit.count(
            connection,
            "SELECT COUNT(*) FROM stk_subscribers WHERE analyst_id = ? AND status = 'pending'",
            (me["id"],),
        )
        if life.is_adult(me)
        else 0
    )
    if not has_accounts and not fired and not pending:
        return []
    lines: list[dict[str, Any]] = []
    if has_accounts:
        try:
            lines += [
                _day_line(a)
                for a in service.accounts_brief(connection, me)
                if a["holdings"]
            ]
        except market.MarketUnavailable:
            lines.append(
                {
                    "text": "Prices cannot be reached right now",
                    "sub": "Open Stocks later",
                    "done": False,
                }
            )
    for alert in fired[:4]:
        lines.append(
            {
                "text": service.fired_text(alert),
                "sub": "Price alert",
                "done": False,
                "action": life.action("Got it", f"{BASE}/alerts/{alert['id']}/seen"),
            }
        )
    if pending:
        lines.append(
            {
                "text": f"{pending} {'person wants' if pending == 1 else 'people want'} to subscribe to your stock ideas",
                "sub": "Confirm their payment in Stocks > Ideas",
                "done": False,
                "link": "#/stocks?tab=ideas",
            }
        )
    if not lines:
        return []
    return [
        life.card(
            APP,
            "Stocks today",
            lines=lines[:6],
            tone="warn" if fired else "",
            link="#/stocks",
        )
    ]


@life.pulse(APP)
def _pulse(connection: Any, me: dict[str, Any], since: str) -> dict[str, Any]:
    store.ensure(connection)
    service.check_alerts(
        connection, me["id"], fetch=False
    )  # saved prices only: the pulse never waits on the network
    fired = service.recent_alerts(connection, me)
    return {
        "badge": len(fired),
        "alerts": [
            {
                "id": f"stocks:alert:{alert['id']}:{alert['triggered_at']}",
                "title": f"Price alert: {alert['symbol']}",
                "body": service.fired_text(alert),
                "tone": "warn",
                "speak": False,
                "actions": [life.action("Got it", f"{BASE}/alerts/{alert['id']}/seen")],
            }
            for alert in fired[:5]
        ],
    }


@background.task(APP)
def _tick(connection: Any) -> dict[str, int]:
    return service.run_background(connection)


agents.register(assistant.agent())
