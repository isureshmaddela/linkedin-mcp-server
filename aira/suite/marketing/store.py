"""Marketing tables (prefix ``mk_``) and plain row access."""

from __future__ import annotations

import sqlite3
from typing import Any

from aira.suite import kit

APP = "marketing"

SCHEMA = (
    """CREATE TABLE IF NOT EXISTS mk_campaigns (
        id TEXT PRIMARY KEY,
        name TEXT NOT NULL,
        goal TEXT NOT NULL DEFAULT '',
        starts_on TEXT NOT NULL DEFAULT '',
        ends_on TEXT NOT NULL DEFAULT '',
        budget_cents INTEGER NOT NULL DEFAULT 0,
        spend_cents INTEGER NOT NULL DEFAULT 0,
        revenue_cents INTEGER NOT NULL DEFAULT 0,
        channels TEXT NOT NULL DEFAULT '[]',
        utm_source TEXT NOT NULL DEFAULT '',
        utm_medium TEXT NOT NULL DEFAULT '',
        utm_campaign TEXT NOT NULL DEFAULT '',
        status TEXT NOT NULL DEFAULT 'active',
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL
    )""",
    """CREATE TABLE IF NOT EXISTS mk_posts (
        id TEXT PRIMARY KEY,
        channel TEXT NOT NULL,
        title TEXT NOT NULL DEFAULT '',
        body TEXT NOT NULL DEFAULT '',
        hashtags TEXT NOT NULL DEFAULT '',
        media_url TEXT NOT NULL DEFAULT '',
        scheduled_at TEXT NOT NULL DEFAULT '',
        status TEXT NOT NULL DEFAULT 'idea',
        campaign_id TEXT NOT NULL DEFAULT '',
        published_url TEXT NOT NULL DEFAULT '',
        published_at TEXT,
        source TEXT NOT NULL DEFAULT 'manual',
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL
    )""",
    "CREATE INDEX IF NOT EXISTS mk_posts_when ON mk_posts (scheduled_at)",
    """CREATE TABLE IF NOT EXISTS mk_links (
        id TEXT PRIMARY KEY,
        slug TEXT NOT NULL UNIQUE,
        title TEXT NOT NULL DEFAULT '',
        destination TEXT NOT NULL,
        campaign_id TEXT NOT NULL DEFAULT '',
        utm_source TEXT NOT NULL DEFAULT '',
        utm_medium TEXT NOT NULL DEFAULT '',
        utm_campaign TEXT NOT NULL DEFAULT '',
        utm_content TEXT NOT NULL DEFAULT '',
        clicks INTEGER NOT NULL DEFAULT 0,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL
    )""",
    """CREATE TABLE IF NOT EXISTS mk_clicks (
        id TEXT PRIMARY KEY,
        kind TEXT NOT NULL,
        target_id TEXT NOT NULL,
        day TEXT NOT NULL,
        at TEXT NOT NULL
    )""",
    "CREATE INDEX IF NOT EXISTS mk_clicks_target ON mk_clicks (kind, target_id, day)",
    "CREATE INDEX IF NOT EXISTS mk_clicks_at ON mk_clicks (at)",
    """CREATE TABLE IF NOT EXISTS mk_forms (
        id TEXT PRIMARY KEY,
        title TEXT NOT NULL,
        intro TEXT NOT NULL DEFAULT '',
        fields TEXT NOT NULL DEFAULT '{}',
        custom_question TEXT NOT NULL DEFAULT '',
        consent_text TEXT NOT NULL DEFAULT '',
        thank_you TEXT NOT NULL DEFAULT '',
        add_subscriber INTEGER NOT NULL DEFAULT 1,
        tag TEXT NOT NULL DEFAULT '',
        campaign_id TEXT NOT NULL DEFAULT '',
        status TEXT NOT NULL DEFAULT 'active',
        views INTEGER NOT NULL DEFAULT 0,
        submissions INTEGER NOT NULL DEFAULT 0,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL
    )""",
    """CREATE TABLE IF NOT EXISTS mk_submissions (
        id TEXT PRIMARY KEY,
        form_id TEXT NOT NULL,
        name TEXT NOT NULL DEFAULT '',
        email TEXT NOT NULL DEFAULT '',
        phone TEXT NOT NULL DEFAULT '',
        company TEXT NOT NULL DEFAULT '',
        message TEXT NOT NULL DEFAULT '',
        answer TEXT NOT NULL DEFAULT '',
        consent INTEGER NOT NULL DEFAULT 0,
        campaign_id TEXT NOT NULL DEFAULT '',
        status TEXT NOT NULL DEFAULT 'new',
        notes TEXT NOT NULL DEFAULT '',
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL
    )""",
    "CREATE INDEX IF NOT EXISTS mk_submissions_form ON mk_submissions (form_id, created_at)",
    """CREATE TABLE IF NOT EXISTS mk_subscribers (
        id TEXT PRIMARY KEY,
        email TEXT NOT NULL UNIQUE,
        name TEXT NOT NULL DEFAULT '',
        source TEXT NOT NULL DEFAULT 'manual',
        tags TEXT NOT NULL DEFAULT '[]',
        campaign_id TEXT NOT NULL DEFAULT '',
        status TEXT NOT NULL DEFAULT 'subscribed',
        consent_at TEXT,
        unsubscribed_at TEXT,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL
    )""",
    """CREATE TABLE IF NOT EXISTS mk_bio_links (
        id TEXT PRIMARY KEY,
        label TEXT NOT NULL,
        url TEXT NOT NULL,
        highlight INTEGER NOT NULL DEFAULT 0,
        position INTEGER NOT NULL DEFAULT 0,
        clicks INTEGER NOT NULL DEFAULT 0,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL
    )""",
    """CREATE TABLE IF NOT EXISTS mk_newsletters (
        id TEXT PRIMARY KEY,
        subject TEXT NOT NULL,
        body TEXT NOT NULL,
        audience_tag TEXT NOT NULL DEFAULT '',
        status TEXT NOT NULL DEFAULT 'draft',
        queued_count INTEGER NOT NULL DEFAULT 0,
        queued_at TEXT,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL
    )""",
    """CREATE TABLE IF NOT EXISTS mk_newsletter_recipients (
        newsletter_id TEXT NOT NULL,
        subscriber_id TEXT NOT NULL,
        message_id TEXT NOT NULL DEFAULT '',
        queued_at TEXT NOT NULL,
        PRIMARY KEY (newsletter_id, subscriber_id)
    )""",
)

# Tables added after the first release (created on any database, old or new).
SCHEMA_V2 = (
    """CREATE TABLE IF NOT EXISTS mk_packages (
        id TEXT PRIMARY KEY,
        name TEXT NOT NULL,
        price_cents INTEGER NOT NULL DEFAULT 0,
        details TEXT NOT NULL DEFAULT '',
        days INTEGER NOT NULL DEFAULT 0,
        position INTEGER NOT NULL DEFAULT 0,
        active INTEGER NOT NULL DEFAULT 1,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL
    )""",
)

# Columns added after the first release: (table, column, definition).
ADDED_COLUMNS = (
    ("mk_submissions", "value_cents", "INTEGER NOT NULL DEFAULT 0"),
    ("mk_submissions", "follow_up_on", "TEXT NOT NULL DEFAULT ''"),
    ("mk_submissions", "source", "TEXT NOT NULL DEFAULT 'form'"),
    ("mk_submissions", "package", "TEXT NOT NULL DEFAULT ''"),
    ("mk_submissions", "paid_at", "TEXT"),
    ("mk_submissions", "contacted_at", "TEXT"),
    ("mk_posts", "reminded_at", "TEXT"),
)

JSON_COLUMNS = {"channels", "tags", "fields"}


def _add_columns(connection: sqlite3.Connection) -> None:
    """Add the newer columns to a database made by an older Marketing (safe to run any number of times)."""
    marker = (kit._db_file(connection), "marketing.columns.v2")  # noqa: SLF001 - kit.ensure's own marker,
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
    connection.execute(
        "CREATE INDEX IF NOT EXISTS mk_submissions_follow ON mk_submissions (follow_up_on)"
    )
    connection.commit()
    kit._ensured.add(marker)  # noqa: SLF001


def ensure(connection: sqlite3.Connection) -> None:
    kit.ensure_core(connection)
    kit.ensure(connection, "marketing.v1", SCHEMA)
    kit.ensure(connection, "marketing.v2", SCHEMA_V2)
    _add_columns(connection)


def decode(row: dict[str, Any] | None) -> dict[str, Any]:
    if not row:
        return {}
    out = dict(row)
    for column in JSON_COLUMNS & out.keys():
        out[column] = kit.loads(out[column], [] if column != "fields" else {})
    for column in ("add_subscriber", "highlight", "consent", "active"):
        if column in out:
            out[column] = bool(out[column])
    return out


def get(
    connection: sqlite3.Connection, table: str, row_id: str, label: str
) -> dict[str, Any]:
    return decode(kit.require_row(connection, table, row_id, label))


def all_rows(
    connection: sqlite3.Connection, sql: str, params: tuple[Any, ...] | list[Any] = ()
) -> list[dict[str, Any]]:
    return [decode(row) for row in kit.fetch_all(connection, sql, params)]


def delete(connection: sqlite3.Connection, table: str, row_id: str, label: str) -> None:
    kit.require_row(connection, table, row_id, label)
    connection.execute(f"DELETE FROM {table} WHERE id = ?", (row_id,))  # noqa: S608 - table names are constants


def count(
    connection: sqlite3.Connection, sql: str, params: tuple[Any, ...] = ()
) -> int:
    return kit.count(connection, sql, params)
