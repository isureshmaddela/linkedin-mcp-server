"""Planner savings goals: what the household is saving for ("Holiday 1,200 by July"), the money put aside and
taken out, how much is still needed each month to get there on time, and money from a sale or a side job put
straight toward a goal.

Money is for adults: children cannot see or change goals. Putting money aside is not spending, so it does not
change the budget; it is a pot the household watches grow.
"""

from __future__ import annotations

import math
import sqlite3
from datetime import date
from typing import Any

from aira.suite import kit, life
from aira.suite.planner import service, store

APP = store.APP
MAX_GOALS = 50


def _adult(me: dict[str, Any]) -> None:
    if not life.is_adult(me):
        raise PermissionError("Savings goals are for adults.")


def _goal(connection: sqlite3.Connection, goal_id: str) -> dict[str, Any]:
    return store.one(connection, "pn_goals", goal_id, "goal")


def saved(connection: sqlite3.Connection, goal_id: str) -> int:
    return kit.count(
        connection,
        "SELECT COALESCE(SUM(amount_cents), 0) FROM pn_goal_moves WHERE goal_id = ?",
        (goal_id,),
    )


def _months_left(on: date, target: date) -> int:
    """Whole months to put money aside before ``target`` (at least 1 while the day has not passed)."""
    days = (target - on).days
    if days < 0:
        return 0
    return max(1, math.ceil(days / 30.44))


def goal_view(
    connection: sqlite3.Connection,
    row: dict[str, Any],
    on: date,
    everyone: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    have = saved(connection, row["id"])
    target = int(row["target_cents"])
    left = max(0, target - have)
    reached = have >= target
    months = (
        _months_left(on, date.fromisoformat(row["target_day"]))
        if row["target_day"]
        else None
    )
    if reached:
        status, per_month = "reached", 0
    elif months == 0:
        status, per_month = "late", left
    else:
        status = "saving"
        per_month = math.ceil(left / months) if months else 0
    moves = kit.fetch_all(
        connection,
        "SELECT * FROM pn_goal_moves WHERE goal_id = ? ORDER BY at DESC, rowid DESC LIMIT 10",
        (row["id"],),
    )
    return {
        "id": row["id"],
        "title": row["title"],
        "target_cents": target,
        "target_day": row["target_day"],
        "saved_cents": have,
        "left_cents": left,
        "percent": max(0, min(100, have * 100 // target)) if target else 0,
        "status": status,
        "months_left": months,
        "per_month_cents": per_month,
        "moves": [
            {
                "id": m["id"],
                "amount_cents": m["amount_cents"],
                "note": m["note"],
                "source_type": m["source_type"],
                "who": service._name(everyone, m["profile_id"]),
                "at": m["at"],
            }
            for m in moves
        ],
    }


def goals(connection: sqlite3.Connection, me: dict[str, Any]) -> dict[str, Any]:
    _adult(me)
    on = service.today(connection)
    everyone = service.people(connection)
    rows = kit.fetch_all(
        connection,
        "SELECT * FROM pn_goals WHERE deleted_at IS NULL ORDER BY CASE WHEN target_day = '' THEN 1 ELSE 0 END, target_day, created_at",
    )
    views = [goal_view(connection, r, on, everyone) for r in rows]
    month = on.strftime("%Y-%m")
    this_month = kit.count(
        connection,
        """SELECT COALESCE(SUM(m.amount_cents), 0) FROM pn_goal_moves m JOIN pn_goals g ON g.id = m.goal_id
        WHERE g.deleted_at IS NULL AND substr(m.at, 1, 7) = ?""",
        (month,),
    )
    return {
        "currency": kit.business(connection)["currency"],
        "today": on.isoformat(),
        "goals": views,
        "saved_cents": sum(g["saved_cents"] for g in views),
        "target_cents": sum(g["target_cents"] for g in views),
        "this_month_cents": this_month,
        "per_month_cents": sum(
            g["per_month_cents"] for g in views if g["status"] == "saving"
        ),
    }


def choices(connection: sqlite3.Connection) -> list[dict[str, str]]:
    """Goals money can go toward (not reached yet), for the "put it toward a goal" pickers."""
    rows = kit.fetch_all(
        connection,
        "SELECT id, title, target_cents FROM pn_goals WHERE deleted_at IS NULL ORDER BY created_at",
    )
    return [
        {"id": r["id"], "title": r["title"]}
        for r in rows
        if saved(connection, r["id"]) < int(r["target_cents"])
    ]


def save_goal(
    connection: sqlite3.Connection,
    me: dict[str, Any],
    raw: dict[str, Any],
    goal_id: str = "",
) -> dict[str, Any]:
    _adult(me)
    current = _goal(connection, goal_id) if goal_id else None
    merged = {**(current or {}), **raw}
    values = {
        "title": kit.text(
            merged.get("title"), "what you are saving for", max_len=80, required=True
        ),
        "target_cents": kit.cents_value(
            merged.get("target_cents"), "how much you need", minimum=1
        ),
        "target_day": kit.day(merged.get("target_day"), "the day you need it by"),
    }
    stamp = kit.now()
    if goal_id:
        kit.update_row(connection, "pn_goals", goal_id, {**values, "updated_at": stamp})
    else:
        if (
            kit.count(
                connection, "SELECT COUNT(*) FROM pn_goals WHERE deleted_at IS NULL"
            )
            >= MAX_GOALS
        ):
            raise ValueError(
                f"You can save for {MAX_GOALS} things at once. Remove one you reached first."
            )
        goal_id = kit.new_id()
        connection.execute(
            "INSERT INTO pn_goals (id, title, target_cents, target_day, created_by, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                goal_id,
                values["title"],
                values["target_cents"],
                values["target_day"],
                me["id"],
                stamp,
                stamp,
            ),
        )
        kit.log(
            connection,
            APP,
            "goal",
            f"{me['name']} started saving for {values['title']}",
            ref_type="goal",
            ref_id=goal_id,
            amount_cents=values["target_cents"],
            actor=me["name"],
        )
    connection.commit()
    return goal_view(
        connection,
        _goal(connection, goal_id),
        service.today(connection),
        service.people(connection),
    )


def delete_goal(
    connection: sqlite3.Connection, me: dict[str, Any], goal_id: str
) -> None:
    """Stop saving for something. The money already put aside is the household's to spend as it likes."""
    _adult(me)
    _goal(connection, goal_id)
    stamp = kit.now()
    kit.update_row(
        connection, "pn_goals", goal_id, {"deleted_at": stamp, "updated_at": stamp}
    )
    connection.commit()


def _insert_move(
    connection: sqlite3.Connection,
    me: dict[str, Any],
    goal_id: str,
    amount: int,
    note: str,
    source_type: str = "",
    source_id: str = "",
) -> str:
    move_id = kit.new_id()
    connection.execute(
        "INSERT INTO pn_goal_moves (id, goal_id, amount_cents, note, source_type, source_id, profile_id, at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (
            move_id,
            goal_id,
            amount,
            note[:120],
            source_type,
            source_id,
            me["id"],
            kit.now(),
        ),
    )
    return move_id


def move(
    connection: sqlite3.Connection,
    me: dict[str, Any],
    goal_id: str,
    raw: dict[str, Any],
    *,
    take_out: bool = False,
) -> dict[str, Any]:
    """Put money aside for a goal, or take some out (never more than is there)."""
    _adult(me)
    goal = _goal(connection, goal_id)
    amount = kit.cents_value(raw.get("amount_cents"), "the amount", minimum=1)
    note = kit.text(raw.get("note"), "a note", max_len=120)
    if take_out:
        have = saved(connection, goal_id)
        if amount > have:
            raise ValueError(
                f"There is {kit.fmt_money(have, kit.business(connection)['currency'])} put aside for this."
            )
        amount = -amount
    _insert_move(connection, me, goal_id, amount, note)
    if not take_out:
        kit.log(
            connection,
            APP,
            "goal",
            f"{me['name']} put money aside for {goal['title']}",
            ref_type="goal",
            ref_id=goal_id,
            amount_cents=amount,
            actor=me["name"],
        )
    connection.commit()
    return goal_view(
        connection, goal, service.today(connection), service.people(connection)
    )


def undo_move(connection: sqlite3.Connection, me: dict[str, Any], move_id: str) -> None:
    """Added by mistake: the money leaves the goal (or comes back, for money taken out)."""
    _adult(me)
    row = kit.fetch_one(
        connection, "SELECT * FROM pn_goal_moves WHERE id = ?", (move_id,)
    )
    if not row:
        raise LookupError("That was not found.")
    if (
        row["amount_cents"] > 0
        and saved(connection, row["goal_id"]) - int(row["amount_cents"]) < 0
    ):
        raise ValueError("Some of that money was taken out again. Undo that first.")
    connection.execute("DELETE FROM pn_goal_moves WHERE id = ?", (move_id,))
    connection.commit()


def add_from(
    connection: sqlite3.Connection,
    me: dict[str, Any],
    goal_id: str,
    amount: int,
    source_type: str,
    source_id: str,
    note: str,
) -> None:
    """Money earned (a sale, a side job) put straight toward a goal. The caller commits."""
    _goal(connection, goal_id)
    if amount > 0:
        connection.execute(
            "DELETE FROM pn_goal_moves WHERE source_type = ? AND source_id = ?",
            (source_type, source_id),
        )
        _insert_move(connection, me, goal_id, amount, note, source_type, source_id)


def _source_move(
    connection: sqlite3.Connection, source_type: str, source_id: str
) -> dict[str, Any] | None:
    return kit.fetch_one(
        connection,
        """SELECT m.id, m.goal_id, m.amount_cents, g.title, g.deleted_at AS goal_deleted FROM pn_goal_moves m
        JOIN pn_goals g ON g.id = m.goal_id WHERE m.source_type = ? AND m.source_id = ?""",
        (source_type, source_id),
    )


def check_change(
    connection: sqlite3.Connection, source_type: str, source_id: str, amount: int = 0
) -> None:
    """Refuse to take a sale's or a job's money out of its goal (``amount`` 0) or lower it to ``amount`` when
    that much is no longer there: the goal would hold less than nothing. Changes nothing."""
    row = _source_move(connection, source_type, source_id)
    if not row or row["goal_deleted"]:
        return
    drop = int(row["amount_cents"]) - max(0, int(amount))
    if drop > 0 and saved(connection, row["goal_id"]) - drop < 0:
        raise ValueError(
            f"Some of this money was taken out of the savings goal {row['title']} since. "
            "Put it back in that goal first (or undo taking it out), then try again."
        )


def remove_from(
    connection: sqlite3.Connection, source_type: str, source_id: str
) -> None:
    """A sale or job undone: its money leaves the goal too (refused when that money was taken out of the goal
    since). The caller commits."""
    check_change(connection, source_type, source_id)
    connection.execute(
        "DELETE FROM pn_goal_moves WHERE source_type = ? AND source_id = ?",
        (source_type, source_id),
    )


def change_from(
    connection: sqlite3.Connection, source_type: str, source_id: str, amount: int
) -> None:
    """A sale's or a job's amount changed in the budget: the money it put toward a goal changes with it. The
    caller commits."""
    check_change(connection, source_type, source_id, amount)
    if amount > 0:
        connection.execute(
            "UPDATE pn_goal_moves SET amount_cents = ? WHERE source_type = ? AND source_id = ?",
            (int(amount), source_type, source_id),
        )
    else:
        connection.execute(
            "DELETE FROM pn_goal_moves WHERE source_type = ? AND source_id = ?",
            (source_type, source_id),
        )
