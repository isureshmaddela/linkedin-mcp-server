"""Planner tables (prefixed ``pn_``) and small row helpers."""

from __future__ import annotations

import sqlite3
from typing import Any

from aira.suite import kit, life

APP = "planner"

SCHEMA = (
    """CREATE TABLE IF NOT EXISTS pn_tasks (
        id TEXT PRIMARY KEY,
        title TEXT NOT NULL,
        notes TEXT NOT NULL DEFAULT '',
        assignee TEXT NOT NULL DEFAULT '',
        created_by TEXT NOT NULL DEFAULT '',
        due_date TEXT NOT NULL DEFAULT '',
        due_time TEXT NOT NULL DEFAULT '',
        repeat TEXT NOT NULL DEFAULT 'none',
        repeat_days TEXT NOT NULL DEFAULT '',
        repeat_day INTEGER NOT NULL DEFAULT 0,
        repeat_every INTEGER NOT NULL DEFAULT 0,
        points INTEGER NOT NULL DEFAULT 0,
        status TEXT NOT NULL DEFAULT 'open',
        done_by TEXT NOT NULL DEFAULT '',
        done_at TEXT,
        series_id TEXT NOT NULL,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL,
        deleted_at TEXT
    )""",
    "CREATE INDEX IF NOT EXISTS pn_tasks_due ON pn_tasks (status, due_date)",
    "CREATE UNIQUE INDEX IF NOT EXISTS pn_tasks_series_due ON pn_tasks (series_id, due_date) WHERE deleted_at IS NULL",
    """CREATE TABLE IF NOT EXISTS pn_task_done (
        task_id TEXT NOT NULL,
        profile_id TEXT NOT NULL,
        at TEXT NOT NULL,
        PRIMARY KEY (task_id, profile_id)
    )""",
    """CREATE TABLE IF NOT EXISTS pn_points (
        id TEXT PRIMARY KEY,
        profile_id TEXT NOT NULL,
        delta INTEGER NOT NULL,
        reason TEXT NOT NULL DEFAULT '',
        ref_type TEXT NOT NULL DEFAULT '',
        ref_id TEXT NOT NULL DEFAULT '',
        at TEXT NOT NULL,
        UNIQUE (ref_type, ref_id, profile_id)
    )""",
    """CREATE TABLE IF NOT EXISTS pn_rewards (
        id TEXT PRIMARY KEY,
        title TEXT NOT NULL,
        points INTEGER NOT NULL,
        active INTEGER NOT NULL DEFAULT 1,
        created_by TEXT NOT NULL DEFAULT '',
        created_at TEXT NOT NULL
    )""",
    """CREATE TABLE IF NOT EXISTS pn_reward_requests (
        id TEXT PRIMARY KEY,
        reward_id TEXT NOT NULL,
        title TEXT NOT NULL,
        profile_id TEXT NOT NULL,
        points INTEGER NOT NULL,
        status TEXT NOT NULL DEFAULT 'pending',
        decided_by TEXT NOT NULL DEFAULT '',
        created_at TEXT NOT NULL,
        decided_at TEXT
    )""",
    """CREATE TABLE IF NOT EXISTS pn_lists (
        id TEXT PRIMARY KEY,
        name TEXT NOT NULL,
        created_by TEXT NOT NULL DEFAULT '',
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL,
        deleted_at TEXT
    )""",
    """CREATE TABLE IF NOT EXISTS pn_items (
        id TEXT PRIMARY KEY,
        list_id TEXT NOT NULL,
        name TEXT NOT NULL,
        quantity TEXT NOT NULL DEFAULT '',
        category TEXT NOT NULL DEFAULT 'other',
        checked INTEGER NOT NULL DEFAULT 0,
        checked_by TEXT NOT NULL DEFAULT '',
        checked_at TEXT,
        added_by TEXT NOT NULL DEFAULT '',
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL,
        deleted_at TEXT
    )""",
    "CREATE INDEX IF NOT EXISTS pn_items_list ON pn_items (list_id, updated_at)",
    """CREATE TABLE IF NOT EXISTS pn_usual (
        key TEXT PRIMARY KEY,
        name TEXT NOT NULL,
        category TEXT NOT NULL DEFAULT 'other',
        times INTEGER NOT NULL DEFAULT 0,
        last_at TEXT NOT NULL
    )""",
    """CREATE TABLE IF NOT EXISTS pn_categories (
        id TEXT PRIMARY KEY,
        name TEXT NOT NULL UNIQUE,
        monthly_cents INTEGER NOT NULL DEFAULT 0,
        created_at TEXT NOT NULL
    )""",
    """CREATE TABLE IF NOT EXISTS pn_entries (
        id TEXT PRIMARY KEY,
        kind TEXT NOT NULL,
        amount_cents INTEGER NOT NULL,
        day TEXT NOT NULL,
        category TEXT NOT NULL DEFAULT '',
        note TEXT NOT NULL DEFAULT '',
        profile_id TEXT NOT NULL DEFAULT '',
        bill_id TEXT NOT NULL DEFAULT '',
        created_at TEXT NOT NULL
    )""",
    "CREATE INDEX IF NOT EXISTS pn_entries_day ON pn_entries (day)",
    """CREATE TABLE IF NOT EXISTS pn_bills (
        id TEXT PRIMARY KEY,
        name TEXT NOT NULL,
        amount_cents INTEGER NOT NULL DEFAULT 0,
        repeat TEXT NOT NULL DEFAULT 'monthly',
        due_day INTEGER NOT NULL DEFAULT 1,
        due_date TEXT NOT NULL DEFAULT '',
        category TEXT NOT NULL DEFAULT 'Bills',
        autopay INTEGER NOT NULL DEFAULT 0,
        active INTEGER NOT NULL DEFAULT 1,
        notes TEXT NOT NULL DEFAULT '',
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL
    )""",
    """CREATE TABLE IF NOT EXISTS pn_bill_payments (
        id TEXT PRIMARY KEY,
        bill_id TEXT NOT NULL,
        period TEXT NOT NULL,
        amount_cents INTEGER NOT NULL,
        entry_id TEXT NOT NULL DEFAULT '',
        paid_by TEXT NOT NULL DEFAULT '',
        paid_at TEXT NOT NULL,
        UNIQUE (bill_id, period)
    )""",
    """CREATE TABLE IF NOT EXISTS pn_notes (
        id TEXT PRIMARY KEY,
        title TEXT NOT NULL DEFAULT '',
        body TEXT NOT NULL DEFAULT '',
        checklist TEXT NOT NULL DEFAULT '[]',
        shared INTEGER NOT NULL DEFAULT 1,
        pinned INTEGER NOT NULL DEFAULT 0,
        owner_id TEXT NOT NULL,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL,
        deleted_at TEXT
    )""",
)


# Meals, selling things the household no longer needs, and pocket money paid out for chore points.
SCHEMA_V2 = (
    """CREATE TABLE IF NOT EXISTS pn_recipes (
        id TEXT PRIMARY KEY,
        title TEXT NOT NULL,
        ingredients TEXT NOT NULL DEFAULT '[]',
        notes TEXT NOT NULL DEFAULT '',
        minutes INTEGER NOT NULL DEFAULT 0,
        created_by TEXT NOT NULL DEFAULT '',
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL,
        deleted_at TEXT
    )""",
    """CREATE TABLE IF NOT EXISTS pn_meals (
        id TEXT PRIMARY KEY,
        day TEXT NOT NULL,
        slot TEXT NOT NULL,
        title TEXT NOT NULL,
        recipe_id TEXT NOT NULL DEFAULT '',
        cook TEXT NOT NULL DEFAULT '',
        notes TEXT NOT NULL DEFAULT '',
        created_by TEXT NOT NULL DEFAULT '',
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL,
        deleted_at TEXT
    )""",
    "CREATE UNIQUE INDEX IF NOT EXISTS pn_meals_slot ON pn_meals (day, slot) WHERE deleted_at IS NULL",
    """CREATE TABLE IF NOT EXISTS pn_sale_items (
        id TEXT PRIMARY KEY,
        title TEXT NOT NULL,
        description TEXT NOT NULL DEFAULT '',
        condition TEXT NOT NULL DEFAULT 'good',
        price_cents INTEGER NOT NULL DEFAULT 0,
        status TEXT NOT NULL DEFAULT 'available',
        buyer TEXT NOT NULL DEFAULT '',
        sold_cents INTEGER NOT NULL DEFAULT 0,
        sold_at TEXT,
        entry_id TEXT NOT NULL DEFAULT '',
        created_by TEXT NOT NULL DEFAULT '',
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL,
        deleted_at TEXT
    )""",
    """CREATE TABLE IF NOT EXISTS pn_sale_requests (
        id TEXT PRIMARY KEY,
        item_id TEXT NOT NULL,
        name TEXT NOT NULL,
        contact TEXT NOT NULL,
        message TEXT NOT NULL DEFAULT '',
        offer_cents INTEGER NOT NULL DEFAULT 0,
        status TEXT NOT NULL DEFAULT 'new',
        created_at TEXT NOT NULL,
        decided_at TEXT
    )""",
    "CREATE INDEX IF NOT EXISTS pn_sale_requests_item ON pn_sale_requests (item_id, status)",
    """CREATE TABLE IF NOT EXISTS pn_payouts (
        id TEXT PRIMARY KEY,
        profile_id TEXT NOT NULL,
        points INTEGER NOT NULL,
        amount_cents INTEGER NOT NULL,
        entry_id TEXT NOT NULL DEFAULT '',
        paid_by TEXT NOT NULL DEFAULT '',
        at TEXT NOT NULL
    )""",
)


# Side jobs the household does for neighbours (each job done and paid is money in), and savings goals.
SCHEMA_V3 = (
    """CREATE TABLE IF NOT EXISTS pn_jobs (
        id TEXT PRIMARY KEY,
        item_id TEXT NOT NULL,
        request_id TEXT NOT NULL DEFAULT '',
        customer TEXT NOT NULL DEFAULT '',
        hours REAL NOT NULL DEFAULT 0,
        amount_cents INTEGER NOT NULL,
        day TEXT NOT NULL,
        note TEXT NOT NULL DEFAULT '',
        entry_id TEXT NOT NULL DEFAULT '',
        done_by TEXT NOT NULL DEFAULT '',
        created_at TEXT NOT NULL
    )""",
    "CREATE INDEX IF NOT EXISTS pn_jobs_item ON pn_jobs (item_id, day)",
    """CREATE TABLE IF NOT EXISTS pn_goals (
        id TEXT PRIMARY KEY,
        title TEXT NOT NULL,
        target_cents INTEGER NOT NULL,
        target_day TEXT NOT NULL DEFAULT '',
        created_by TEXT NOT NULL DEFAULT '',
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL,
        deleted_at TEXT
    )""",
    """CREATE TABLE IF NOT EXISTS pn_goal_moves (
        id TEXT PRIMARY KEY,
        goal_id TEXT NOT NULL,
        amount_cents INTEGER NOT NULL,
        note TEXT NOT NULL DEFAULT '',
        source_type TEXT NOT NULL DEFAULT '',
        source_id TEXT NOT NULL DEFAULT '',
        profile_id TEXT NOT NULL DEFAULT '',
        at TEXT NOT NULL
    )""",
    "CREATE INDEX IF NOT EXISTS pn_goal_moves_goal ON pn_goal_moves (goal_id, at)",
)

# Columns added after a table first shipped: (table, column, declaration). Added when missing, so an existing
# household's database gains them on the next start.
COLUMNS = (
    # Take turns: the people a repeating chore rotates between, in order ("id1,id2,id3").
    ("pn_tasks", "rotation", "TEXT NOT NULL DEFAULT ''"),
    # A thing to sell, or a side job offered to neighbours (priced per job or per hour).
    ("pn_sale_items", "kind", "TEXT NOT NULL DEFAULT 'thing'"),
    ("pn_sale_items", "price_unit", "TEXT NOT NULL DEFAULT ''"),
)


def _add_columns(connection: sqlite3.Connection) -> None:
    added = False
    for table in {t for t, _, _ in COLUMNS}:
        have = {row[1] for row in connection.execute(f"PRAGMA table_info({table})")}  # noqa: S608 - fixed names
        for name, declaration in ((c, d) for t, c, d in COLUMNS if t == table):
            if name not in have:
                connection.execute(
                    f"ALTER TABLE {table} ADD COLUMN {name} {declaration}"  # noqa: S608 - fixed names
                )
                added = True
    if added:
        connection.commit()


def ensure(connection: sqlite3.Connection) -> None:
    life.ensure(connection)
    kit.ensure(connection, "planner.v1", SCHEMA)
    kit.ensure(connection, "planner.v2", SCHEMA_V2)
    kit.ensure(connection, "planner.v3", SCHEMA_V3)
    _add_columns(connection)


def one(
    connection: sqlite3.Connection, table: str, row_id: str, label: str
) -> dict[str, Any]:
    found = kit.fetch_one(
        connection,
        f"SELECT * FROM {table} WHERE id = ?",  # noqa: S608 - table names are constants here
        (row_id,),
    )
    if not found or found.get("deleted_at"):
        raise LookupError(f"That {label} was not found.")
    return found
