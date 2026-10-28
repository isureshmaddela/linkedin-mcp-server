"""Life tables (prefixed ``jr_``)."""

from __future__ import annotations

import sqlite3

from aira.suite import kit, life

APP = "journey"

SCHEMA = (
    """CREATE TABLE IF NOT EXISTS jr_goals (
        id TEXT PRIMARY KEY,
        owner_id TEXT NOT NULL,
        title TEXT NOT NULL,
        description TEXT NOT NULL DEFAULT '',
        category TEXT NOT NULL DEFAULT 'personal',
        status TEXT NOT NULL DEFAULT 'active',
        progress INTEGER NOT NULL DEFAULT 0,
        target_date TEXT NOT NULL DEFAULT '',
        completed_at TEXT,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL
    )""",
    "CREATE INDEX IF NOT EXISTS jr_goals_owner ON jr_goals (owner_id, status)",
    """CREATE TABLE IF NOT EXISTS jr_steps (
        id TEXT PRIMARY KEY,
        goal_id TEXT NOT NULL,
        title TEXT NOT NULL,
        done INTEGER NOT NULL DEFAULT 0,
        position INTEGER NOT NULL DEFAULT 0,
        done_at TEXT,
        created_at TEXT NOT NULL
    )""",
    "CREATE INDEX IF NOT EXISTS jr_steps_goal ON jr_steps (goal_id, position)",
    """CREATE TABLE IF NOT EXISTS jr_checkins (
        id TEXT PRIMARY KEY,
        goal_id TEXT NOT NULL,
        progress INTEGER NOT NULL,
        note TEXT NOT NULL DEFAULT '',
        at TEXT NOT NULL
    )""",
    "CREATE INDEX IF NOT EXISTS jr_checkins_goal ON jr_checkins (goal_id, at)",
    """CREATE TABLE IF NOT EXISTS jr_events (
        id TEXT PRIMARY KEY,
        owner_id TEXT NOT NULL,
        title TEXT NOT NULL,
        description TEXT NOT NULL DEFAULT '',
        kind TEXT NOT NULL DEFAULT 'life',
        event_date TEXT NOT NULL,
        location TEXT NOT NULL DEFAULT '',
        shared INTEGER NOT NULL DEFAULT 0,
        goal_id TEXT NOT NULL DEFAULT '',
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL
    )""",
    "CREATE INDEX IF NOT EXISTS jr_events_owner ON jr_events (owner_id, event_date)",
    """CREATE TABLE IF NOT EXISTS jr_documents (
        id TEXT PRIMARY KEY,
        owner_id TEXT NOT NULL,
        title TEXT NOT NULL,
        kind TEXT NOT NULL DEFAULT 'other',
        issuer TEXT NOT NULL DEFAULT '',
        number_cipher BLOB,
        number_hint TEXT NOT NULL DEFAULT '',
        issued_on TEXT NOT NULL DEFAULT '',
        expires_on TEXT NOT NULL DEFAULT '',
        tags TEXT NOT NULL DEFAULT '[]',
        notes TEXT NOT NULL DEFAULT '',
        file_name TEXT NOT NULL DEFAULT '',
        file_type TEXT NOT NULL DEFAULT '',
        file_size INTEGER NOT NULL DEFAULT 0,
        file_cipher BLOB,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL
    )""",
    "CREATE INDEX IF NOT EXISTS jr_documents_owner ON jr_documents (owner_id, expires_on)",
)


# Version 2: goals measured in a number or money (savings and earning goals), habits with streaks, and cheers
# that friends leave on a goal's public page.
SCHEMA_V2 = (
    """CREATE TABLE IF NOT EXISTS jr_habits (
        id TEXT PRIMARY KEY,
        owner_id TEXT NOT NULL,
        title TEXT NOT NULL,
        per_week INTEGER NOT NULL DEFAULT 7,
        goal_id TEXT NOT NULL DEFAULT '',
        amount INTEGER NOT NULL DEFAULT 0,
        archived INTEGER NOT NULL DEFAULT 0,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL
    )""",
    "CREATE INDEX IF NOT EXISTS jr_habits_owner ON jr_habits (owner_id, archived)",
    """CREATE TABLE IF NOT EXISTS jr_habit_days (
        habit_id TEXT NOT NULL,
        day TEXT NOT NULL,
        checkin_id TEXT NOT NULL DEFAULT '',
        at TEXT NOT NULL,
        PRIMARY KEY (habit_id, day)
    )""",
    """CREATE TABLE IF NOT EXISTS jr_cheers (
        id TEXT PRIMARY KEY,
        goal_id TEXT NOT NULL,
        owner_id TEXT NOT NULL,
        name TEXT NOT NULL DEFAULT '',
        message TEXT NOT NULL,
        client TEXT NOT NULL DEFAULT '',
        at TEXT NOT NULL,
        read_at TEXT
    )""",
    "CREATE INDEX IF NOT EXISTS jr_cheers_goal ON jr_cheers (goal_id, at)",
    "CREATE INDEX IF NOT EXISTS jr_cheers_owner ON jr_cheers (owner_id, read_at)",
)

# Version 3: a private journal (a few lines and a mood a day) and supporters who back a goal from its public page.
SCHEMA_V3 = (
    """CREATE TABLE IF NOT EXISTS jr_journal (
        id TEXT PRIMARY KEY,
        owner_id TEXT NOT NULL,
        day TEXT NOT NULL,
        mood INTEGER NOT NULL DEFAULT 0,
        body TEXT NOT NULL DEFAULT '',
        grateful TEXT NOT NULL DEFAULT '',
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL,
        UNIQUE (owner_id, day)
    )""",
    """CREATE TABLE IF NOT EXISTS jr_pledges (
        id TEXT PRIMARY KEY,
        goal_id TEXT NOT NULL,
        owner_id TEXT NOT NULL,
        name TEXT NOT NULL DEFAULT '',
        message TEXT NOT NULL DEFAULT '',
        amount INTEGER NOT NULL,
        currency TEXT NOT NULL,
        status TEXT NOT NULL DEFAULT 'promised',
        client TEXT NOT NULL DEFAULT '',
        checkin_id TEXT NOT NULL DEFAULT '',
        at TEXT NOT NULL,
        received_at TEXT,
        read_at TEXT
    )""",
    "CREATE INDEX IF NOT EXISTS jr_pledges_goal ON jr_pledges (goal_id, at)",
    "CREATE INDEX IF NOT EXISTS jr_pledges_owner ON jr_pledges (owner_id, read_at)",
)

# Columns added after version 1 (a database made by version 1 gets them on first use).
COLUMNS = {
    "jr_goals": (
        (
            "measure",
            "TEXT NOT NULL DEFAULT ''",
        ),  # '' (percent or steps), 'number' or 'money'
        ("unit", "TEXT NOT NULL DEFAULT ''"),
        (
            "target_amount",
            "INTEGER NOT NULL DEFAULT 0",
        ),  # hundredths of the unit (cents for money)
        ("current_amount", "INTEGER NOT NULL DEFAULT 0"),
        ("currency", "TEXT NOT NULL DEFAULT ''"),
        (
            "source",
            "TEXT NOT NULL DEFAULT 'manual'",
        ),  # 'manual' or 'earnings' (the account's revenue)
        ("start_date", "TEXT NOT NULL DEFAULT ''"),
        # Supporters (version 3): the goal's page takes pledges, paid through the owner's own link or details.
        ("support", "INTEGER NOT NULL DEFAULT 0"),
        ("pay_link", "TEXT NOT NULL DEFAULT ''"),
        ("pay_note", "TEXT NOT NULL DEFAULT ''"),
    ),
    "jr_checkins": (("amount", "INTEGER"),),
}


def _add_columns(connection: sqlite3.Connection) -> None:
    for table, columns in COLUMNS.items():
        have = {row[1] for row in connection.execute(f"PRAGMA table_info({table})")}
        for name, kind in columns:
            if name not in have:
                connection.execute(f"ALTER TABLE {table} ADD COLUMN {name} {kind}")
    connection.commit()


def ensure(connection: sqlite3.Connection) -> None:
    kit.ensure_core(connection)
    life.ensure(connection)
    kit.ensure(connection, "journey.v1", SCHEMA)
    kit.ensure(connection, "journey.v2", SCHEMA_V2)
    kit.ensure(connection, "journey.v3", SCHEMA_V3)
    _add_columns(connection)
