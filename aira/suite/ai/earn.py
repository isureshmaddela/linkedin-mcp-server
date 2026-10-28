"""Earn (``#/ai?tab=earn``) and EarnAgent: what the account's apps earned, the person's earning goals with their
pace (what is needed a week to reach each one by its date, against what the apps earned a week lately), the next
steps that would bring money in, and the ways each app the account has can earn.

Money is the account's: only people who may see it (adults on this computer; owners, admins, coaches and auditors
in an organization, :func:`aira.suite.journey.service.may_count_earnings`) see amounts, goals and next steps.
Everyone sees the ways to earn with the apps they have.
"""

from __future__ import annotations

import math
import sqlite3
from datetime import timedelta
from typing import Any

from aira import apps as app_catalog
from aira import suite
from aira.suite import kit

# How a person earns with each app (catalog app id, or a section id for an app with several), and where to start.
WAYS: dict[str, tuple[str, str]] = {
    "studio": (
        "Send quotes and invoices to clients and mark them paid.",
        "#/studio",
    ),
    "shop": ("Sell products from your own shop page.", "#/shop"),
    "bookings": ("Take paid appointments from your own booking page.", "#/bookings"),
    "academy": ("Sell courses and enrol paying students.", "#/academy"),
    "events": ("Sell tickets to your events and check people in.", "#/events"),
    "marketing": (
        "Turn leads from your forms and campaigns into paying customers.",
        "#/marketing",
    ),
    "service": (
        "Answer customers quickly so they come back and buy again.",
        "#/service",
    ),
    "ads": ("Advertise your business on videos and reach new customers.", "#/ads"),
    "videos": (
        "Upload videos and earn a share of the ads that run on them.",
        "#/videos",
    ),
    "stocks": (
        "Publish stock ideas and earn from people who subscribe to them.",
        "#/stocks",
    ),
    "journey": (
        "Set an earning goal: it counts what your apps earn by itself.",
        "#/journey",
    ),
    "careers": ("Find and apply for better-paid jobs.", "#/jobs"),
}
MAX_STEPS = 8
PACE_DAYS = 28  # the recent weeks an earning goal's pace is measured over
_MONEY_WORDS = ("pay", "price", "invoice", "sell", "ticket", "product", "service")


def _name(app_id: str) -> str:
    owner = app_catalog.owner_of(app_id)
    return owner.name if owner else app_id.title()


def ways() -> list[dict[str, Any]]:
    """The ways to earn with the apps this account has."""
    out = []
    seen: set[str] = set()
    for app in app_catalog.APPS:
        if not suite.app_enabled(app.id):
            continue
        for key in (app.id, *app.sections):
            if key in WAYS and key not in seen:
                seen.add(key)
                text, link = WAYS[key]
                label = app.name if key == app.id else key.title()
                out.append({"app": app.id, "name": label, "text": text, "link": link})
    return out


def next_steps(connection: sqlite3.Connection) -> list[dict[str, Any]]:
    """Setup steps the business apps still need, the ones that bring money in first (the Business hub's lists)."""
    steps = []
    for app in app_catalog.INDUSTRY_APPS:
        if not suite.app_enabled(app.id):
            continue
        for section in app.sections:
            fn = kit.CHECKLISTS.get(section)
            if fn is None:
                continue
            try:
                items = fn(connection)
            except Exception:  # noqa: BLE001 - one app's checklist must not break the page
                suite.logger.exception("Checklist for %s failed", section)
                continue
            for item in items:
                if item.get("done"):
                    continue
                words = f"{item.get('id', '')} {item.get('label', '')}".lower()
                steps.append(
                    {
                        "app": app.id,
                        "name": app.name,
                        "label": str(item.get("label") or ""),
                        "hint": str(item.get("hint") or ""),
                        "link": f"#/{section}",
                        "money": any(word in words for word in _MONEY_WORDS),
                    }
                )
    steps.sort(key=lambda step: not step["money"])
    return steps[:MAX_STEPS]


def _month_by_app(
    connection: sqlite3.Connection, currency: str, start: str
) -> list[dict[str, Any]]:
    rows = kit.fetch_all(
        connection,
        """SELECT app, SUM(amount_cents) AS total FROM suite_revenue WHERE voided_at IS NULL AND currency = ?
        AND at >= ? GROUP BY app ORDER BY total DESC""",
        (currency, start),
    )
    return [
        {"app": row["app"], "name": _name(row["app"]), "cents": int(row["total"] or 0)}
        for row in rows
        if int(row["total"] or 0)
    ]


def _goals(
    connection: sqlite3.Connection, me: dict[str, Any]
) -> list[dict[str, Any]] | None:
    """The person's earning goals (None when the account has no Life app to keep them)."""
    if not suite.app_enabled("journey"):
        return None
    from aira.suite.journey import service as journey, store as journey_store

    journey_store.ensure(connection)
    out = []
    recent: dict[str, int] = {}
    for goal in journey.list_goals(connection, me):
        if goal.get("source") != "earnings" or goal["status"] == "dropped":
            continue
        out.append(
            {
                "id": goal["id"],
                "title": goal["title"],
                "status": goal["status"],
                "progress": goal["progress"],
                "target_cents": goal["target_amount"],
                "current_cents": goal["current_amount"],
                "currency": goal["currency"],
                "target_date": goal["target_date"],
                "days_left": goal["days_left"],
                "start_date": goal["start_date"],
                "pace": pace(connection, goal, recent),
            }
        )
    return out


def recent_week_cents(connection: sqlite3.Connection, currency: str) -> int:
    """What the apps earned a week, on average, over the last four weeks (in one currency)."""
    since = kit.iso(kit.utcnow() - timedelta(days=PACE_DAYS))
    total = kit.count(
        connection,
        """SELECT COALESCE(SUM(amount_cents), 0) FROM suite_revenue WHERE voided_at IS NULL AND currency = ?
        AND at >= ?""",
        (currency, since),
    )
    return round(total * 7 / PACE_DAYS)


def pace(
    connection: sqlite3.Connection,
    goal: dict[str, Any],
    cache: dict[str, int] | None = None,
) -> dict[str, Any] | None:
    """Whether an active earning goal with a date will be reached at the recent rate.

    ``need_week_cents``: what is still needed, spread over the weeks left; ``recent_week_cents``: what the apps
    earned a week lately; ``projected_cents``: where the goal ends up by its date at that rate."""
    if goal["status"] != "active" or not goal.get("target_date"):
        return None
    target = int(goal.get("target_amount") or 0)
    current = int(goal.get("current_amount") or 0)
    if target <= 0:
        return None
    currency = str(goal.get("currency") or kit.business(connection)["currency"])
    cache = {} if cache is None else cache
    if currency not in cache:
        cache[currency] = recent_week_cents(connection, currency)
    rate = cache[currency]
    remaining = max(0, target - current)
    days_left = goal.get("days_left")
    days = int(days_left) if days_left is not None else 0
    if days < 0:
        state = "late"
        need = remaining
    else:
        need = math.ceil(remaining * 7 / max(1, days)) if remaining else 0
        state = "reached" if not remaining else "on_track" if rate >= need else "behind"
    projected = current + round(rate * max(0, days) / 7)
    return {
        "state": state,
        "need_week_cents": need,
        "recent_week_cents": rate,
        "remaining_cents": remaining,
        "projected_cents": projected,
        "currency": currency,
    }


PACE_WORDS = {
    "on_track": "on track",
    "behind": "behind",
    "late": "past its date",
    "reached": "reached",
}


def pace_text(found: dict[str, Any] | None) -> str:
    """A goal's pace in words: "needs $300.00 a week; lately $250.00 a week: behind"."""
    if not found:
        return ""
    money = lambda cents: kit.fmt_money(cents, found["currency"])  # noqa: E731
    if found["state"] == "reached":
        return "reached"
    if found["state"] == "late":
        return f"past its date with {money(found['remaining_cents'])} to go"
    return (
        f"needs {money(found['need_week_cents'])} a week; lately {money(found['recent_week_cents'])} a week: "
        f"{PACE_WORDS[found['state']]}"
    )


def _earner_lines(
    connection: sqlite3.Connection, me: dict[str, Any]
) -> list[dict[str, Any]]:
    from aira.suite.ai import agents as registry

    out = []
    for agent in registry.available(me):
        if agent.earner is None:
            continue
        try:
            lines = registry.unique(agent.earner(connection, me))[:6]
        except Exception:  # noqa: BLE001 - one app must not break the page
            suite.logger.exception("Earnings of %s failed", agent.id)
            continue
        if lines:
            out.append(
                {
                    "agent": agent.id,
                    "name": agent.name,
                    "app": agent.app,
                    "lines": lines,
                }
            )
    return out


def overview(connection: sqlite3.Connection, me: dict[str, Any]) -> dict[str, Any]:
    """Everything the Earn tab shows for this person."""
    from aira.suite.journey import service as journey

    kit.ensure_core(connection)
    may = journey.may_count_earnings(me)
    data: dict[str, Any] = {
        "may_see": may,
        "ways": ways(),
        "from_agents": _earner_lines(connection, me),
    }
    if not may:
        return data
    revenue = kit.revenue(connection, months=6)
    currency = revenue["currency"]
    start = kit.utcnow().date().replace(day=1).isoformat()
    counts = kit.outbox_counts(connection)
    data.update(
        {
            "currency": currency,
            "this_month_cents": revenue["this_month_cents"],
            "last_month_cents": revenue["last_month_cents"],
            "change_percent": revenue["change_percent"],
            "total_cents": revenue["total_cents"],
            "payments": revenue["payments"],
            "by_month": revenue["by_month"],
            "by_app": _month_by_app(connection, currency, start),
            "steps": next_steps(connection),
            "outbox_pending": int(counts.get("pending") or 0),
            "goals": _goals(connection, me),
        }
    )
    return data


def facts(connection: sqlite3.Connection, me: dict[str, Any]) -> list[str]:
    """EarnAgent's lines for the master agent."""
    data = overview(connection, me)
    out: list[str] = []
    if data["may_see"]:
        currency = data["currency"]
        money = lambda cents: kit.fmt_money(cents, currency)  # noqa: E731
        change = (
            f" ({data['change_percent']:+d}% on last month)"
            if data["change_percent"] is not None
            else ""
        )
        out.append(
            f"Earned this month: {money(data['this_month_cents'])}{change}; last month {money(data['last_month_cents'])}; "
            f"all time {money(data['total_cents'])} from {data['payments']} payments."
        )
        if data["by_app"]:
            out.append(
                "This month by app: "
                + ", ".join(
                    f"{a['name']} {money(a['cents'])}" for a in data["by_app"][:5]
                )
                + "."
            )
        for goal in (data["goals"] or [])[:3]:
            due = f", by {goal['target_date']}" if goal["target_date"] else ""
            how = pace_text(goal.get("pace"))
            out.append(
                f'Earning goal "{goal["title"]}": {goal["progress"]}% '
                f"({kit.fmt_money(goal['current_cents'], goal['currency'] or currency)} of "
                f"{kit.fmt_money(goal['target_cents'], goal['currency'] or currency)}){due}"
                + (f"; {how}" if how else "")
                + "."
            )
        if data["goals"] == []:
            out.append(
                'No earning goal yet: say "add an earning goal: 50000 by 2026-12-31".'
            )
        for step in data["steps"][:4]:
            hint = f" ({step['hint']})" if step["hint"] else ""
            out.append(f"Next step to earn in {step['name']}: {step['label']}{hint}.")
        if data["outbox_pending"]:
            out.append(
                f"{data['outbox_pending']} emails to customers wait for your OK in the Business hub outbox."
            )
    for entry in data["from_agents"]:
        out.extend(entry["lines"][:3])
    for way in data["ways"][:4]:
        out.append(f"You can earn with {way['name']}: {way['text']}")
    return out
