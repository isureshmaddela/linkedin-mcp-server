"""Planner search: what the search box across the top of the Aira frame finds (``GET /api/planner/search``).

One answer for every kind of thing the household keeps here, each list capped, and only what the person asking may
see: their visible chores, every shopping item and list, recipes, shared notes and their own private ones, rewards;
adults also bills, things for sale and side jobs, and savings goals.
"""

from __future__ import annotations

import sqlite3
from typing import Any

from aira.suite import kit, life
from aira.suite.planner import service

KINDS = (
    "tasks",
    "lists",
    "items",
    "recipes",
    "notes",
    "rewards",
    "bills",
    "sell",
    "goals",
)


def _like(q: str) -> str:
    return "%" + q.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"


def _rows(
    connection: sqlite3.Connection, sql: str, pattern: str, columns: int, limit: int
) -> list[dict[str, Any]]:
    return kit.fetch_all(connection, f"{sql} LIMIT {int(limit)}", (pattern,) * columns)


def find(
    connection: sqlite3.Connection, me: dict[str, Any], q: Any, limit: Any = 8
) -> dict[str, Any]:
    """Everything matching ``q`` (in names, titles, notes, descriptions and ingredients), up to ``limit`` of each
    kind. An empty search finds nothing."""
    q = kit.text(q, "the search", max_len=80)
    try:
        limit = max(1, min(int(limit), 25))
    except (TypeError, ValueError):
        limit = 8
    out: dict[str, Any] = {
        "query": q,
        "currency": kit.business(connection)["currency"],
        **{kind: [] for kind in KINDS},
    }
    if not q:
        return out
    pattern = _like(q)
    esc = "ESCAPE '\\'"
    everyone = service.people(connection)
    on = service.today(connection).isoformat()

    # Chores: open ones first (soonest due), then done ones; a repeating chore shows once, by its next turn.
    seen: set[str] = set()
    for row in kit.fetch_all(
        connection,
        f"""SELECT * FROM pn_tasks WHERE deleted_at IS NULL AND (title LIKE ? {esc} OR notes LIKE ? {esc})
        ORDER BY CASE status WHEN 'open' THEN 0 ELSE 1 END, CASE WHEN due_date = '' THEN 1 ELSE 0 END, due_date,
        done_at DESC LIMIT 200""",  # noqa: S608
        (pattern, pattern),
    ):
        if row["series_id"] in seen or not service.visible_to(row, me):
            continue
        seen.add(row["series_id"])
        out["tasks"].append(
            {
                "id": row["id"],
                "title": row["title"],
                "done": row["status"] == "done",
                "due_date": row["due_date"],
                "overdue": row["status"] == "open"
                and bool(row["due_date"])
                and row["due_date"] < on,
                "assignee_name": "Everyone"
                if row["assignee"] == service.EVERYONE
                else service._name(everyone, row["assignee"])
                if row["assignee"]
                else "Anyone",
            }
        )
        if len(out["tasks"]) >= limit:
            break

    names = {row["id"]: row["name"] for row in service.lists(connection)}
    out["lists"] = [
        {"id": key, "name": name}
        for key, name in names.items()
        if q.lower() in name.lower()
    ][:limit]
    out["items"] = [
        {
            "id": row["id"],
            "name": row["name"],
            "quantity": row["quantity"],
            "checked": bool(row["checked"]),
            "list_id": row["list_id"],
            "list_name": names[row["list_id"]],
        }
        for row in _rows(
            connection,
            f"SELECT * FROM pn_items WHERE deleted_at IS NULL AND name LIKE ? {esc} ORDER BY checked, updated_at DESC",
            pattern,
            1,
            limit * 3,
        )
        if row["list_id"] in names
    ][:limit]

    # Recipes match on their name or any ingredient (the ingredients are stored as JSON, so a LIKE finds them).
    out["recipes"] = [
        {
            "id": row["id"],
            "title": row["title"],
            "ingredients": len(kit.loads(row["ingredients"], []) or []),
            "minutes": row["minutes"],
        }
        for row in _rows(
            connection,
            f"SELECT * FROM pn_recipes WHERE deleted_at IS NULL AND (title LIKE ? {esc} OR ingredients LIKE ? {esc}) ORDER BY title COLLATE NOCASE",
            pattern,
            2,
            limit,
        )
    ]
    out["notes"] = [
        {
            "id": row["id"],
            "title": row["title"],
            "snippet": " ".join(row["body"].split())[:80],
            "shared": bool(row["shared"]),
        }
        for row in kit.fetch_all(
            connection,
            f"""SELECT * FROM pn_notes WHERE deleted_at IS NULL AND (shared = 1 OR owner_id = ?)
            AND (title LIKE ? {esc} OR body LIKE ? {esc} OR checklist LIKE ? {esc}) ORDER BY pinned DESC, updated_at DESC LIMIT {limit}""",  # noqa: S608
            (me["id"], pattern, pattern, pattern),
        )
    ]
    out["rewards"] = [
        {"id": row["id"], "title": row["title"], "points": row["points"]}
        for row in _rows(
            connection,
            f"SELECT * FROM pn_rewards WHERE active = 1 AND title LIKE ? {esc} ORDER BY points, title",
            pattern,
            1,
            limit,
        )
    ]
    if not life.is_adult(me):
        return out
    out["bills"] = [
        {
            "id": row["id"],
            "name": row["name"],
            "amount_cents": row["amount_cents"],
            "repeat": row["repeat"],
        }
        for row in _rows(
            connection,
            f"SELECT * FROM pn_bills WHERE active = 1 AND (name LIKE ? {esc} OR notes LIKE ? {esc}) ORDER BY name COLLATE NOCASE",
            pattern,
            2,
            limit,
        )
    ]
    out["sell"] = [
        {
            "id": row["id"],
            "title": row["title"],
            "kind": row.get("kind") or "thing",
            "status": row["status"],
            "price_cents": row["price_cents"],
        }
        for row in _rows(
            connection,
            f"SELECT * FROM pn_sale_items WHERE deleted_at IS NULL AND (title LIKE ? {esc} OR description LIKE ? {esc}) ORDER BY CASE status WHEN 'sold' THEN 1 ELSE 0 END, created_at DESC",
            pattern,
            2,
            limit,
        )
    ]
    out["goals"] = [
        {"id": row["id"], "title": row["title"], "target_cents": row["target_cents"]}
        for row in _rows(
            connection,
            f"SELECT * FROM pn_goals WHERE deleted_at IS NULL AND title LIKE ? {esc} ORDER BY created_at",
            pattern,
            1,
            limit,
        )
    ]
    return out
