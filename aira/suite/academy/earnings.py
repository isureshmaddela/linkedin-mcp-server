"""Earnings for Courses & Events: what every course and every event has earned, from the top earner to the
lowest, with this month against last month, refunds, partner commissions and a log of every payment.

Read from the suite's revenue ledger (``suite_revenue``), which only holds money the business marked received,
so the report never counts a payment that did not arrive. A refund stays in the log, marked refunded, and stops
counting.
"""

from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta
from typing import Any

from aira.suite import kit
from aira.suite.academy import partners
from aira.suite.academy import store as academy_store
from aira.suite.events import store as events_store

APPS = ("academy", "events")
CSV_COLUMNS = (
    "kind",
    "title",
    "status",
    "revenue",
    "this_month",
    "last_month",
    "sales",
    "refunds",
    "refunded",
    "commission",
)


def _months(moment: datetime) -> tuple[str, str]:
    this = moment.strftime("%Y-%m")
    previous = (moment.date().replace(day=1) - timedelta(days=1)).strftime("%Y-%m")
    return this, previous


def _sums(
    connection: sqlite3.Connection, sql: str, params: tuple, this: str, last: str
) -> dict[str, dict[str, int]]:
    """Per item id: revenue, this month, last month, sales, refunds (count and amount)."""
    out: dict[str, dict[str, int]] = {}
    for row in kit.fetch_all(connection, sql, params):
        item = out.setdefault(
            row["item_id"],
            {
                "revenue_cents": 0,
                "this_month_cents": 0,
                "last_month_cents": 0,
                "sales": 0,
                "refunds": 0,
                "refunded_cents": 0,
            },
        )
        amount = int(row["amount_cents"] or 0)
        if row["voided_at"]:
            item["refunds"] += 1
            item["refunded_cents"] += amount
            continue
        item["revenue_cents"] += amount
        item["sales"] += 1
        month = str(row["at"])[:7]
        if month == this:
            item["this_month_cents"] += amount
        elif month == last:
            item["last_month_cents"] += amount
    return out


def _commissions(connection: sqlite3.Connection) -> dict[tuple[str, str], int]:
    partners.ensure(connection)
    rows = kit.fetch_all(
        connection,
        "SELECT item_type, item_id, SUM(commission_cents) AS total FROM xp_referrals WHERE status IN ('earned', 'paid') GROUP BY item_type, item_id",
    )
    return {(row["item_type"], row["item_id"]): int(row["total"] or 0) for row in rows}


def items(
    connection: sqlite3.Connection, *, now: datetime | None = None
) -> list[dict[str, Any]]:
    """Every course and event with what it earned, highest first."""
    academy_store.ensure(connection)
    events_store.ensure(connection)
    this, last = _months(now or kit.utcnow())
    commissions = _commissions(connection)
    courses = _sums(
        connection,
        """SELECT e.course_id AS item_id, r.amount_cents, r.at, r.voided_at FROM suite_revenue r
        JOIN ac_enrolments e ON e.id = r.source_id WHERE r.app = 'academy' AND r.source_type = 'enrolment'""",
        (),
        this,
        last,
    )
    tickets = _sums(
        connection,
        """SELECT o.event_id AS item_id, r.amount_cents, r.at, r.voided_at FROM suite_revenue r
        JOIN ev_orders o ON o.id = r.source_id WHERE r.app = 'events' AND r.source_type = 'order'""",
        (),
        this,
        last,
    )
    sponsors = _sums(
        connection,
        """SELECT s.event_id AS item_id, r.amount_cents, r.at, r.voided_at FROM suite_revenue r
        JOIN ev_sponsors s ON s.id = r.source_id WHERE r.app = 'events' AND r.source_type = 'sponsor'""",
        (),
        this,
        last,
    )
    empty = {
        "revenue_cents": 0,
        "this_month_cents": 0,
        "last_month_cents": 0,
        "sales": 0,
        "refunds": 0,
        "refunded_cents": 0,
    }
    out: list[dict[str, Any]] = []
    for course in kit.fetch_all(
        connection, "SELECT id, title, status, price_cents FROM ac_courses"
    ):
        sums = courses.get(course["id"], empty)
        out.append(
            {
                "kind": "course",
                "id": course["id"],
                "title": course["title"],
                "status": course["status"],
                **sums,
                "sponsor_cents": 0,
                "commission_cents": commissions.get(("course", course["id"]), 0),
            }
        )
    now_text = kit.now()
    for event in kit.fetch_all(
        connection, "SELECT id, title, status, ends_at FROM ev_events"
    ):
        ticket = tickets.get(event["id"], empty)
        sponsor = sponsors.get(event["id"], empty)
        status = (
            "ended"
            if event["status"] == "published" and str(event["ends_at"]) <= now_text
            else event["status"]
        )
        out.append(
            {
                "kind": "event",
                "id": event["id"],
                "title": event["title"],
                "status": status,
                **{key: ticket[key] + sponsor[key] for key in empty},
                "sales": ticket["sales"],
                "sponsor_cents": sponsor["revenue_cents"],
                "commission_cents": commissions.get(("event", event["id"]), 0),
            }
        )
    season = sponsors.get("", empty)
    if season["revenue_cents"] or season["refunds"]:
        out.append(
            {
                "kind": "sponsors",
                "id": "",
                "title": "Season sponsors (not tied to one event)",
                "status": "",
                **season,
                "sales": 0,
                "sponsor_cents": season["revenue_cents"],
                "commission_cents": 0,
            }
        )
    out.sort(
        key=lambda row: (
            -row["revenue_cents"],
            -row["this_month_cents"],
            row["title"].lower(),
        )
    )
    return out


def report(
    connection: sqlite3.Connection, *, now: datetime | None = None
) -> dict[str, Any]:
    rows = items(connection, now=now)
    totals = [kit.revenue(connection, app=app, moment=now, months=6) for app in APPS]
    by_month: dict[str, int] = {}
    for total in totals:
        for month in total["by_month"]:
            by_month[month["month"]] = by_month.get(month["month"], 0) + int(
                month["cents"]
            )
    this_month = sum(t["this_month_cents"] for t in totals)
    last_month = sum(t["last_month_cents"] for t in totals)
    total = sum(t["total_cents"] for t in totals)
    commission = partners.summary(connection)
    selling = [
        row
        for row in rows
        if row["status"] in ("published", "ended") or row["revenue_cents"]
    ]
    top = [row for row in rows if row["revenue_cents"] > 0][:3]
    # The lowest earners never repeat a top earner (with only a few items on sale the lists would be the same).
    top_keys = {(row["kind"], row["id"]) for row in top}
    lowest = sorted(
        (row for row in selling if (row["kind"], row["id"]) not in top_keys),
        key=lambda row: (row["revenue_cents"], row["title"].lower()),
    )[:3]
    return {
        "currency": kit.business(connection)["currency"],
        "totals": {
            "this_month_cents": this_month,
            "last_month_cents": last_month,
            "change_percent": round((this_month - last_month) * 100 / last_month)
            if last_month
            else None,
            "total_cents": total,
            "courses_cents": totals[0]["total_cents"],
            "events_cents": totals[1]["total_cents"],
            "refunded_cents": sum(row["refunded_cents"] for row in rows),
            "commission_cents": commission["earned_cents"],
            "commission_owed_cents": commission["owed_cents"],
            "kept_cents": total - commission["earned_cents"],
        },
        "by_month": [
            {"month": key, "cents": by_month[key]} for key in sorted(by_month)
        ],
        "items": rows,
        "top": top,
        "lowest": lowest,
        "log": log(connection),
    }


def log(connection: sqlite3.Connection, limit: int = 100) -> list[dict[str, Any]]:
    """Every payment, newest first; a refunded one stays with ``refunded`` set."""
    kit.ensure_core(connection)
    rows = kit.fetch_all(
        connection,
        "SELECT app, source_type, label, customer, amount_cents, at, voided_at FROM suite_revenue WHERE app IN ('academy', 'events') ORDER BY at DESC LIMIT ?",
        (max(1, min(limit, 1000)),),
    )
    for row in rows:
        row["refunded"] = bool(row.pop("voided_at"))
    return rows


def csv_rows(connection: sqlite3.Connection) -> list[dict[str, Any]]:
    def money(cents: int) -> str:
        return f"{int(cents or 0) / 100:.2f}"

    return [
        {
            "kind": row["kind"],
            "title": row["title"],
            "status": row["status"],
            "revenue": money(row["revenue_cents"]),
            "this_month": money(row["this_month_cents"]),
            "last_month": money(row["last_month_cents"]),
            "sales": row["sales"],
            "refunds": row["refunds"],
            "refunded": money(row["refunded_cents"]),
            "commission": money(row["commission_cents"]),
        }
        for row in items(connection)
    ]
