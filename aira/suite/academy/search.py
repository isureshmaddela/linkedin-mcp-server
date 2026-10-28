"""Search across Courses & Events, for the search box at the top of the app: the courses, events, students,
ticket orders and partners whose name, email, reference, code, title or venue holds the words typed.

One request answers the whole box (the list under it as the person types, and the results page on Enter), so it
stays quick however many students and orders a business has: each kind is a single indexed-or-small query with a
limit, newest first, and the totals say when there are more than shown.
"""

from __future__ import annotations

import sqlite3
from typing import Any

from aira.suite import kit
from aira.suite.academy import partners
from aira.suite.academy import store as academy_store
from aira.suite.events import service as events_service
from aira.suite.events import store as events_store

KINDS = ("courses", "events", "students", "orders", "partners")


def _like(text: str) -> str:
    """A LIKE pattern for the words typed, with % and _ taken literally (``ESCAPE '\\'``)."""
    escaped = text.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return f"%{escaped}%"


def _found(
    connection: sqlite3.Connection,
    select: str,
    where: str,
    params: list[Any],
    limit: int,
) -> tuple[list[dict[str, Any]], int]:
    total = kit.count(connection, f"SELECT COUNT(*) {where}", params)  # noqa: S608 - fixed SQL
    rows = (
        kit.fetch_all(connection, f"{select} {where} LIMIT ?", [*params, limit])
        if total
        else []
    )  # noqa: S608
    return rows, total


def search(connection: sqlite3.Connection, q: Any, *, limit: int = 5) -> dict[str, Any]:
    text = kit.text(q, "the search", max_len=100)
    limit = max(1, min(int(limit or 5), 50))
    empty: dict[str, Any] = {
        "q": text,
        **{kind: [] for kind in KINDS},
        "totals": dict.fromkeys(KINDS, 0),
    }
    if not text:
        return empty
    academy_store.ensure(connection)
    events_store.ensure(connection)
    partners.ensure(connection)
    like = _like(text)
    esc = " ESCAPE '\\'"
    out: dict[str, Any] = {"q": text, "totals": {}}

    out["courses"], out["totals"]["courses"] = _found(
        connection,
        "SELECT id, title, subtitle, status, price_cents",
        f"FROM ac_courses WHERE (title LIKE ?{esc} OR subtitle LIKE ?{esc}) ORDER BY updated_at DESC, rowid DESC",
        [like, like],
        limit,
    )

    rows, out["totals"]["events"] = _found(
        connection,
        "SELECT id, title, starts_at, ends_at, venue_name, online_url, status",
        f"FROM ev_events WHERE (title LIKE ?{esc} OR venue_name LIKE ?{esc}) ORDER BY starts_at DESC, rowid DESC",
        [like, like],
        limit,
    )
    now = kit.now()
    out["events"] = [{**row, "phase": events_service.phase(row, now)} for row in rows]

    out["students"], out["totals"]["students"] = _found(
        connection,
        "SELECT e.id, e.name, e.email, e.status, e.reference, e.course_id, c.title AS course_title",
        f"""FROM ac_enrolments e JOIN ac_courses c ON c.id = e.course_id
        WHERE (e.name LIKE ?{esc} OR e.email LIKE ?{esc} OR e.reference LIKE ?{esc}) ORDER BY e.created_at DESC, e.rowid DESC""",
        [like, like, like],
        limit,
    )

    out["orders"], out["totals"]["orders"] = _found(
        connection,
        "SELECT o.id, o.reference, o.buyer_name, o.buyer_email, o.status, o.total_cents, o.event_id, e.title AS event_title",
        f"""FROM ev_orders o JOIN ev_events e ON e.id = o.event_id
        WHERE (o.buyer_name LIKE ?{esc} OR o.buyer_email LIKE ?{esc} OR o.reference LIKE ?{esc}) ORDER BY o.created_at DESC, o.rowid DESC""",
        [like, like, like],
        limit,
    )

    rows, out["totals"]["partners"] = _found(
        connection,
        "SELECT id, name, email, code, commission_percent, active",
        f"FROM xp_partners WHERE (name LIKE ?{esc} OR email LIKE ?{esc} OR code LIKE ?{esc}) ORDER BY active DESC, name COLLATE NOCASE",
        [like, like, like],
        limit,
    )
    out["partners"] = [{**row, "active": bool(row["active"])} for row in rows]
    return out
