"""Studio's tables and the row helpers the service builds on (tables are prefixed ``st_``)."""

from __future__ import annotations

import sqlite3
from typing import Any

from aira.suite import kit

SCHEMA = (
    """CREATE TABLE IF NOT EXISTS st_clients (
        id TEXT PRIMARY KEY,
        name TEXT NOT NULL,
        company TEXT NOT NULL DEFAULT '',
        email TEXT NOT NULL DEFAULT '',
        phone TEXT NOT NULL DEFAULT '',
        tags TEXT NOT NULL DEFAULT '[]',
        notes TEXT NOT NULL DEFAULT '',
        status TEXT NOT NULL DEFAULT 'lead',
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL
    )""",
    """CREATE TABLE IF NOT EXISTS st_proposals (
        id TEXT PRIMARY KEY,
        number TEXT NOT NULL,
        seq INTEGER NOT NULL,
        client_id TEXT NOT NULL,
        title TEXT NOT NULL,
        intro TEXT NOT NULL DEFAULT '',
        scope TEXT NOT NULL DEFAULT '',
        items TEXT NOT NULL DEFAULT '[]',
        discount_cents INTEGER NOT NULL DEFAULT 0,
        tax_rate_bp INTEGER NOT NULL DEFAULT 0,
        subtotal_cents INTEGER NOT NULL DEFAULT 0,
        tax_cents INTEGER NOT NULL DEFAULT 0,
        total_cents INTEGER NOT NULL DEFAULT 0,
        valid_until TEXT NOT NULL DEFAULT '',
        status TEXT NOT NULL DEFAULT 'draft',
        sent_at TEXT,
        accepted_at TEXT,
        accepted_name TEXT NOT NULL DEFAULT '',
        accepted_from TEXT NOT NULL DEFAULT '',
        declined_at TEXT,
        decline_reason TEXT NOT NULL DEFAULT '',
        project_id TEXT NOT NULL DEFAULT '',
        invoice_id TEXT NOT NULL DEFAULT '',
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL
    )""",
    "CREATE INDEX IF NOT EXISTS st_proposals_client ON st_proposals (client_id)",
    """CREATE TABLE IF NOT EXISTS st_projects (
        id TEXT PRIMARY KEY,
        client_id TEXT NOT NULL,
        proposal_id TEXT NOT NULL DEFAULT '',
        name TEXT NOT NULL,
        status TEXT NOT NULL DEFAULT 'planned',
        budget_cents INTEGER NOT NULL DEFAULT 0,
        deadline TEXT NOT NULL DEFAULT '',
        notes TEXT NOT NULL DEFAULT '',
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL
    )""",
    "CREATE INDEX IF NOT EXISTS st_projects_client ON st_projects (client_id)",
    """CREATE TABLE IF NOT EXISTS st_tasks (
        id TEXT PRIMARY KEY,
        project_id TEXT NOT NULL,
        title TEXT NOT NULL,
        status TEXT NOT NULL DEFAULT 'todo',
        due_date TEXT NOT NULL DEFAULT '',
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL
    )""",
    "CREATE INDEX IF NOT EXISTS st_tasks_project ON st_tasks (project_id)",
    """CREATE TABLE IF NOT EXISTS st_time (
        id TEXT PRIMARY KEY,
        project_id TEXT NOT NULL,
        day TEXT NOT NULL,
        minutes INTEGER NOT NULL,
        note TEXT NOT NULL DEFAULT '',
        billable INTEGER NOT NULL DEFAULT 1,
        rate_cents INTEGER NOT NULL DEFAULT 0,
        invoice_id TEXT NOT NULL DEFAULT '',
        created_at TEXT NOT NULL
    )""",
    "CREATE INDEX IF NOT EXISTS st_time_project ON st_time (project_id)",
    """CREATE TABLE IF NOT EXISTS st_invoices (
        id TEXT PRIMARY KEY,
        number TEXT NOT NULL,
        seq INTEGER NOT NULL,
        client_id TEXT NOT NULL,
        project_id TEXT NOT NULL DEFAULT '',
        proposal_id TEXT NOT NULL DEFAULT '',
        retainer_id TEXT,
        period TEXT,
        items TEXT NOT NULL DEFAULT '[]',
        discount_cents INTEGER NOT NULL DEFAULT 0,
        tax_rate_bp INTEGER NOT NULL DEFAULT 0,
        subtotal_cents INTEGER NOT NULL DEFAULT 0,
        tax_cents INTEGER NOT NULL DEFAULT 0,
        total_cents INTEGER NOT NULL DEFAULT 0,
        issue_date TEXT NOT NULL,
        due_date TEXT NOT NULL,
        state TEXT NOT NULL DEFAULT 'draft',
        notes TEXT NOT NULL DEFAULT '',
        internal_notes TEXT NOT NULL DEFAULT '',
        customer_notices TEXT NOT NULL DEFAULT '[]',
        sent_at TEXT,
        reminded_at TEXT,
        voided_at TEXT,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL,
        UNIQUE (retainer_id, period)
    )""",
    "CREATE INDEX IF NOT EXISTS st_invoices_client ON st_invoices (client_id)",
    """CREATE TABLE IF NOT EXISTS st_payments (
        id TEXT PRIMARY KEY,
        invoice_id TEXT NOT NULL,
        amount_cents INTEGER NOT NULL,
        day TEXT NOT NULL,
        method TEXT NOT NULL DEFAULT 'bank_transfer',
        note TEXT NOT NULL DEFAULT '',
        created_at TEXT NOT NULL
    )""",
    "CREATE INDEX IF NOT EXISTS st_payments_invoice ON st_payments (invoice_id)",
    """CREATE TABLE IF NOT EXISTS st_retainers (
        id TEXT PRIMARY KEY,
        client_id TEXT NOT NULL,
        title TEXT NOT NULL,
        amount_cents INTEGER NOT NULL,
        interval TEXT NOT NULL DEFAULT 'monthly',
        anchor_day INTEGER NOT NULL,
        next_date TEXT NOT NULL,
        active INTEGER NOT NULL DEFAULT 1,
        last_invoice_id TEXT NOT NULL DEFAULT '',
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL
    )""",
)

JSON_COLUMNS = ("tags", "items", "customer_notices")


def ensure(connection: sqlite3.Connection) -> None:
    from aira.suite.studio import (
        sales,
    )  # contacts, deals and activities (its own tables)

    kit.ensure_core(connection)
    kit.ensure(connection, "studio.v1", SCHEMA)
    sales.ensure(connection)


def decode(row: dict[str, Any] | None) -> dict[str, Any]:
    if not row:
        return {}
    out = dict(row)
    for column in JSON_COLUMNS:
        if column in out:
            out[column] = kit.loads(out[column], [])
    for column in ("billable", "active"):
        if column in out:
            out[column] = bool(out[column])
    return out


def get(
    connection: sqlite3.Connection, table: str, row_id: str, label: str
) -> dict[str, Any]:
    return decode(kit.require_row(connection, table, row_id, label))


def rows(
    connection: sqlite3.Connection, sql: str, params: tuple[Any, ...] = ()
) -> list[dict[str, Any]]:
    return [decode(row) for row in kit.fetch_all(connection, sql, params)]


def next_seq(connection: sqlite3.Connection, table: str) -> int:
    if table not in ("st_invoices", "st_proposals"):
        raise ValueError("Bad table.")
    row = connection.execute(f"SELECT COALESCE(MAX(seq), 0) FROM {table}").fetchone()  # noqa: S608 - fixed table
    return int(row[0] or 0) + 1


def insert(connection: sqlite3.Connection, table: str, values: dict[str, Any]) -> None:
    if not table.startswith("st_"):
        raise ValueError("Bad table.")
    columns = list(values)
    connection.execute(
        f"INSERT INTO {table} ({', '.join(columns)}) VALUES ({', '.join('?' for _ in columns)})",  # noqa: S608
        tuple(values[column] for column in columns),
    )
