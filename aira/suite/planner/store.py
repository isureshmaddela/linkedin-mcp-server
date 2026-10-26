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


def ensure(connection: sqlite3.Connection) -> None:
    life.ensure(connection)
    kit.ensure(connection, "planner.v1", SCHEMA)


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
