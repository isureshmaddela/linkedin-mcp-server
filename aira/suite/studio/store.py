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

# Columns added after the first release: (table, column, definition). Added once, when a database lacks them.
ADDED_COLUMNS = (
    # a proposal can ask for a deposit (hundredths of a percent of the total before tax) when it is accepted
    ("st_proposals", "deposit_bp", "INTEGER NOT NULL DEFAULT 0"),
    ("st_proposals", "deposit_invoice_id", "TEXT NOT NULL DEFAULT ''"),
    ("st_proposals", "followed_up_at", "TEXT"),
    # an invoice can take off what was already invoiced as a deposit; deposits are marked as such
    ("st_invoices", "credit_cents", "INTEGER NOT NULL DEFAULT 0"),
    ("st_invoices", "credit_label", "TEXT NOT NULL DEFAULT ''"),
    ("st_invoices", "kind", "TEXT NOT NULL DEFAULT ''"),
    ("st_invoices", "reminder_count", "INTEGER NOT NULL DEFAULT 0"),
    # the deposit invoice a final invoice's credit came from, so voiding that deposit gives the credit back
    ("st_invoices", "credit_invoice_id", "TEXT NOT NULL DEFAULT ''"),
)

# One-time repairs of rows an older Studio wrote (each is safe to run again: it only touches rows still unfixed).
REPAIRS = (
    # a final invoice remembers which deposit it took off
    """UPDATE st_invoices SET credit_invoice_id = COALESCE((SELECT p.deposit_invoice_id FROM st_proposals p
        WHERE p.id = st_invoices.proposal_id), '')
        WHERE kind = '' AND credit_cents > 0 AND credit_invoice_id = '' AND proposal_id != ''""",
    # a deposit belongs to the project its proposal became, so the project's earnings include it
    """UPDATE st_invoices SET project_id = (SELECT p.project_id FROM st_proposals p
        WHERE p.deposit_invoice_id = st_invoices.id)
        WHERE kind = 'deposit' AND project_id = '' AND EXISTS (SELECT 1 FROM st_proposals p
        JOIN st_projects j ON j.id = p.project_id WHERE p.deposit_invoice_id = st_invoices.id)""",
    # a proposal whose final invoice was voided can be started again
    """UPDATE st_proposals SET invoice_id = '' WHERE invoice_id != ''
        AND invoice_id IN (SELECT id FROM st_invoices WHERE state = 'void' AND kind = '')""",
)

# Numbers are never given twice, even when two requests make an invoice at the same moment. A database that already
# has a repeated number (made before this) keeps working without the index.
UNIQUE_NUMBERS = (
    "CREATE UNIQUE INDEX IF NOT EXISTS st_invoices_seq ON st_invoices (seq)",
    "CREATE UNIQUE INDEX IF NOT EXISTS st_proposals_seq ON st_proposals (seq)",
)


def _add_columns(connection: sqlite3.Connection) -> None:
    """Add the newer columns to a database made by an older Studio (safe to run any number of times)."""
    marker = (kit._db_file(connection), "studio.columns.v3")  # noqa: SLF001 - kit.ensure's own marker,
    if marker in kit._ensured:  # noqa: SLF001 - so kit.forget_schema() resets it too
        return
    tables: dict[str, set[str]] = {}
    for table, column, definition in ADDED_COLUMNS:
        if table not in tables:
            tables[table] = {
                row[1] for row in connection.execute(f"PRAGMA table_info({table})")
            }
        if column not in tables[table]:
            connection.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")
            tables[table].add(column)
    for repair in REPAIRS:
        connection.execute(repair)
    for index in UNIQUE_NUMBERS:
        try:
            connection.execute(index)
        except sqlite3.IntegrityError:
            pass  # an older run gave a number twice; new numbers still skip ahead (see insert_numbered)
    connection.commit()
    kit._ensured.add(marker)  # noqa: SLF001


def ensure(connection: sqlite3.Connection) -> None:
    from aira.suite.studio import (
        sales,
    )  # contacts, deals and activities (its own tables)

    kit.ensure_core(connection)
    kit.ensure(connection, "studio.v1", SCHEMA)
    _add_columns(connection)
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


def insert_numbered(
    connection: sqlite3.Connection,
    table: str,
    values: dict[str, Any],
    number: Any,
) -> None:
    """Insert an invoice or proposal with the next free ``seq`` and its ``number`` (``number(seq)``). When another
    request took that number a moment ago, the unique index refuses it and the next one is tried."""
    for attempt in range(8):
        seq = next_seq(connection, table)
        values["seq"], values["number"] = seq, number(seq)
        try:
            insert(connection, table, values)
            return
        except sqlite3.IntegrityError as error:
            if f"{table}.seq" not in str(error) or attempt == 7:
                raise
