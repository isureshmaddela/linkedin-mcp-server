"""Earnings: what the people you give API keys and rank reports to owe you, and what it cost.

Two ways to earn from the Developers app:

* **Resell search access.** An API key can carry a client's name, a price per
  1,000 searches, a monthly allowance and the engines it may use. Every ``/v1``
  call is counted per key and day in ``airaapi_key_usage`` (never pruned, so a
  statement outlives the history window). Successful calls are billable, cached
  answers included: they cost you nothing, so they are pure margin.
* **Sell rank tracking.** A rank-tracker project carries the client's name and
  a monthly fee; every check is one live search. The fee is billed for each
  month the project existed (from the month it was created to the month it was
  deleted), and its checks are counted in a monthly ledger
  (``airaapi_rank_usage``) that outlives removed keywords and deleted projects.
  A project with no client and no fee is your own site: its checks are a cost
  (``totals.own_checks``), not a client line.

:func:`month_report` adds both up for a calendar month against your own cost
per search (``cost_per_search``, what your SerpApi plan charges you), and
:func:`statement` builds the per-client statement (CSV or HTML) to send.
"""

from __future__ import annotations

import csv
import html
import io
import re
import sqlite3
from collections.abc import Mapping
from datetime import date, datetime, timedelta
from typing import Any

from aira.airaapi import compat, rank, store

PREF = "earnings"
DEFAULTS: dict[str, Any] = {
    "currency": "USD",
    "cost_per_search": 0.0,
    "business_name": "",
}
_CURRENCY_RE = re.compile(r"^[A-Z]{3}$")
_MONTH_RE = re.compile(r"^\d{4}-(0[1-9]|1[0-2])$")


def settings(connection: sqlite3.Connection) -> dict[str, Any]:
    stored = store.get_pref(connection, PREF)
    merged = dict(DEFAULTS)
    if isinstance(stored, Mapping):
        merged.update({k: stored[k] for k in DEFAULTS if k in stored})
    return merged


def save_settings(
    connection: sqlite3.Connection, payload: Mapping[str, Any]
) -> dict[str, Any]:
    current = settings(connection)
    if "currency" in payload:
        code = str(payload.get("currency") or "").strip().upper()
        if not _CURRENCY_RE.match(code):
            raise ValueError(
                "Currency must be a three-letter code such as USD, EUR or INR."
            )
        current["currency"] = code
    if "cost_per_search" in payload:
        raw = payload.get("cost_per_search")
        try:
            cost = round(float(raw if raw not in (None, "") else 0), 6)
        except (TypeError, ValueError):
            raise ValueError(
                "Your cost per search must be a number, for example 0.015."
            ) from None
        if not 0 <= cost <= 100:
            raise ValueError("Your cost per search must be between 0 and 100.")
        current["cost_per_search"] = cost
    if "business_name" in payload:
        name = " ".join(str(payload.get("business_name") or "").split())
        if len(name) > 120:
            raise ValueError("The business name must be 120 characters or fewer.")
        current["business_name"] = name
    store.set_pref(connection, PREF, current)
    connection.commit()
    return current


def _month(text: str, now: datetime) -> str:
    value = (text or "").strip() or now.strftime("%Y-%m")
    if not _MONTH_RE.match(value):
        raise ValueError("Month must look like 2026-10.")
    if value > now.strftime("%Y-%m"):
        raise ValueError("That month has not started yet.")
    return value


def _active_in(created_at: Any, ended_at: Any, month: str) -> bool:
    """Whether something created at ``created_at`` and ended at ``ended_at`` (or still running) existed in ``month``."""
    start = str(created_at or "")[:7]
    end = str(ended_at or "")[:7]
    return (not start or start <= month) and (not end or end >= month)


def _months_between(first: str, last: str) -> list[str]:
    """Every ``YYYY-MM`` from ``first`` to ``last``, newest first."""
    year, month = int(last[:4]), int(last[5:7])
    out: list[str] = []
    while f"{year:04d}-{month:02d}" >= first and len(out) < 36:
        out.append(f"{year:04d}-{month:02d}")
        month -= 1
        if not month:
            year, month = year - 1, 12
    return out


def _is_client_key(row: sqlite3.Row | Mapping[str, Any]) -> bool:
    return compat.is_client_key(dict(row))


def _bounds(month: str) -> tuple[str, str, date]:
    first = date.fromisoformat(month + "-01")
    nxt = (first.replace(day=28) + timedelta(days=4)).replace(day=1)
    last = nxt - timedelta(days=1)
    return first.isoformat(), last.isoformat(), last


def _money(value: float) -> float:
    return round(value + 0.0, 2)


def month_report(
    connection: sqlite3.Connection, month: str = "", *, now: datetime | None = None
) -> dict[str, Any]:
    moment = now or store.now_utc()
    chosen = _month(month, moment)
    first, last, _ = _bounds(chosen)
    prefs = settings(connection)
    cost_per = float(prefs["cost_per_search"] or 0)
    keys = {row["id"]: row for row in store.list_api_keys(connection)}
    usage: dict[str, dict[str, int]] = {}
    daily: dict[str, dict[str, float]] = {}
    for row in store.key_usage_between(connection, first, last):
        item = usage.setdefault(
            row["key_id"], {"calls": 0, "cached": 0, "errors": 0, "credits": 0}
        )
        for name in item:
            item[name] += int(row[name])
        key = keys.get(row["key_id"])
        price = float(key["price_per_1000"] or 0) if key is not None else 0.0
        day = daily.setdefault(row["day"], {"billed": 0.0, "cost": 0.0})
        day["billed"] += int(row["calls"]) * price / 1000
        day["cost"] += int(row["credits"]) * cost_per
    key_lines: list[dict[str, Any]] = []
    for key_id, row in keys.items():
        used = usage.get(key_id)
        price = float(row["price_per_1000"] or 0)
        if used is None and (
            not _is_client_key(row)
            or not _active_in(row["created_at"], row["revoked_at"], chosen)
        ):
            continue  # an unused personal key, or a client key that did not exist that month
        used = used or {"calls": 0, "cached": 0, "errors": 0, "credits": 0}
        billed = used["calls"] * price / 1000
        cost = used["credits"] * cost_per
        key_lines.append(
            {
                "kind": "api_key",
                "id": key_id,
                "name": row["name"],
                "prefix": row["prefix"],
                "client_name": row["client_name"] or "",
                "revoked": bool(row["revoked_at"]),
                "price_per_1000": price,
                "monthly_quota": int(row["monthly_quota"] or 0),
                **used,
                "billed": _money(billed),
                "cost": _money(cost),
                "margin": _money(billed - cost),
            }
        )
    rank_lines: list[dict[str, Any]] = []
    own_checks = 0
    store.ensure_extras(connection)
    for project in connection.execute(
        "SELECT * FROM airaapi_rank_projects ORDER BY created_at"
    ).fetchall():
        checks = rank.checks_in_month(connection, project["id"], chosen)
        existed = _active_in(project["created_at"], project["deleted_at"], chosen)
        fee = float(project["monthly_fee"] or 0) if existed else 0.0
        if not float(project["monthly_fee"] or 0) and not project["client_name"]:
            own_checks += checks  # your own site: a cost, not something you sell
            continue
        if not existed and not checks:
            continue
        cost = checks * cost_per
        rank_lines.append(
            {
                "kind": "rank",
                "id": project["id"],
                "name": project["name"],
                "domain": project["domain"],
                "client_name": project["client_name"] or "",
                "monthly_fee": fee,
                "deleted": bool(project["deleted_at"]),
                "checks": checks,
                "billed": _money(fee),
                "cost": _money(cost),
                "margin": _money(fee - cost),
            }
        )
    lines = key_lines + rank_lines
    billed = sum(line["billed"] for line in lines)
    cost = sum(line["cost"] for line in lines)
    current = moment.strftime("%Y-%m")
    starts = [
        *store.key_usage_months(connection),
        *store.rank_usage_months(connection),
        *(
            str(row["created_at"])[:7]
            for row in keys.values()
            if _is_client_key(row) and row["created_at"]
        ),
        *(
            str(row[0])[:7]
            for row in connection.execute(
                "SELECT created_at FROM airaapi_rank_projects "
                "WHERE monthly_fee > 0 OR client_name != ''"
            )
            if row[0]
        ),
    ]
    earliest = min([chosen, current, *(m for m in starts if _MONTH_RE.match(m))])
    months = _months_between(earliest, current)
    return {
        "month": chosen,
        "settings": prefs,
        "keys": key_lines,
        "rank": rank_lines,
        "totals": {
            "billed": _money(billed),
            "cost": _money(cost),
            "margin": _money(billed - cost),
            "calls": sum(line["calls"] for line in key_lines),
            "cached": sum(line["cached"] for line in key_lines),
            "checks": sum(line["checks"] for line in rank_lines),
            "clients": len(
                {(line["client_name"] or line["name"]).casefold() for line in lines}
            ),
            "own_checks": own_checks,
            "own_cost": _money(own_checks * cost_per),
        },
        "by_day": [
            {"day": day, "billed": _money(v["billed"]), "cost": _money(v["cost"])}
            for day, v in sorted(daily.items())
        ],
        "months": months[:24],
        "cost_known": cost_per > 0,
    }


def _safe(text: str) -> str:
    return "'" + text if text[:1] in ("=", "+", "-", "@") else text


def statement(
    connection: sqlite3.Connection,
    key_id: str,
    month: str = "",
    fmt: str = "csv",
    *,
    now: datetime | None = None,
) -> tuple[bytes, str, str]:
    """A client's statement for one API key and month: searches per day and the amount due."""
    moment = now or store.now_utc()
    chosen = _month(month, moment)
    first, last, _ = _bounds(chosen)
    key = store.get_api_key(connection, key_id)
    if key is None:
        raise LookupError("Key not found.")
    prefs = settings(connection)
    price = float(key["price_per_1000"] or 0)
    days: dict[str, dict[str, int]] = {}
    for row in store.key_usage_between(connection, first, last, key_id):
        day = days.setdefault(row["day"], {"calls": 0, "cached": 0, "errors": 0})
        for name in day:
            day[name] += int(row[name])
    total_calls = sum(d["calls"] for d in days.values())
    total = _money(total_calls * price / 1000)
    client = key["client_name"] or key["name"]
    slug = re.sub(r"[^a-z0-9]+", "-", client.lower()).strip("-") or "client"
    currency = prefs["currency"]
    if fmt == "csv":
        out = io.StringIO()
        writer = csv.writer(out)
        writer.writerow(
            [
                "date",
                "searches",
                "answered from cache",
                "failed (not billed)",
                f"amount ({currency})",
            ]
        )
        for day, d in sorted(days.items()):
            writer.writerow(
                [
                    day,
                    d["calls"],
                    d["cached"],
                    d["errors"],
                    f"{d['calls'] * price / 1000:.2f}",
                ]
            )
        writer.writerow(
            [
                "total",
                total_calls,
                sum(d["cached"] for d in days.values()),
                sum(d["errors"] for d in days.values()),
                f"{total:.2f}",
            ]
        )
        writer.writerow([])
        writer.writerow(["client", _safe(client)])
        writer.writerow(["key", key["prefix"] + "…"])
        writer.writerow(["price per 1,000 searches", f"{price:g} {currency}"])
        if prefs["business_name"]:
            writer.writerow(["from", _safe(prefs["business_name"])])
        return (
            out.getvalue().encode("utf-8-sig"),
            "text/csv; charset=utf-8",
            f"statement-{slug}-{chosen}.csv",
        )
    if fmt != "html":
        raise ValueError("Statement format must be csv or html.")
    e = html.escape
    rows = (
        "".join(
            f"<tr><td>{e(day)}</td><td class=n>{d['calls']:,}</td><td class=n>{d['cached']:,}</td>"
            f"<td class=n>{d['calls'] * price / 1000:,.2f}</td></tr>"
            for day, d in sorted(days.items())
        )
        or '<tr><td colspan="4">No searches this month.</td></tr>'
    )
    label = date.fromisoformat(first).strftime("%B %Y")
    sender = f" from {e(prefs['business_name'])}" if prefs["business_name"] else ""
    body = f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<meta http-equiv="Content-Security-Policy" content="default-src 'none'; style-src 'unsafe-inline'">
<title>Search API statement: {e(client)}, {e(label)}</title>
<style>
body{{font:15px/1.5 system-ui,-apple-system,"Segoe UI",sans-serif;color:#1d2a24;margin:0;padding:24px;background:#f6f8f6}}
main{{max-width:760px;margin:0 auto;background:#fff;border:1px solid #dfe6e1;border-radius:12px;padding:24px}}
h1{{font-size:22px;margin:0 0 4px}} .muted{{color:#5b6b63}} table{{width:100%;border-collapse:collapse;margin-top:16px}}
th,td{{text-align:left;padding:8px;border-bottom:1px solid #e6ece8}} .n{{text-align:right}}
tfoot td{{font-weight:700;border-bottom:0}} @media print{{body{{background:#fff;padding:0}} main{{border:0}}}}
</style></head><body><main>
<h1>Search API statement: {e(client)}</h1>
<p class="muted">{e(label)}{sender} · key {e(key["prefix"])}… · {price:g} {e(currency)} per 1,000 searches</p>
<table><thead><tr><th>Date</th><th class=n>Searches</th><th class=n>From cache</th><th class=n>Amount ({e(currency)})</th></tr></thead>
<tbody>{rows}</tbody>
<tfoot><tr><td>Total</td><td class=n>{total_calls:,}</td><td></td><td class=n>{total:,.2f}</td></tr></tfoot></table>
<p class="muted">Only searches that returned results are counted. Failed searches are not billed.</p>
</main></body></html>
"""
    return (
        body.encode("utf-8"),
        "text/html; charset=utf-8",
        f"statement-{slug}-{chosen}.html",
    )


__all__ = ["month_report", "save_settings", "settings", "statement"]
