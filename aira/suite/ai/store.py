"""AI tables (prefixed ``ai_``)."""

from __future__ import annotations

import sqlite3

from aira.suite import kit, life

APP = "ai"

SCHEMA = (
    """CREATE TABLE IF NOT EXISTS ai_agents (
        owner_id TEXT NOT NULL,
        agent TEXT NOT NULL,
        enabled INTEGER NOT NULL DEFAULT 1,
        consent TEXT NOT NULL,
        updated_at TEXT NOT NULL,
        PRIMARY KEY (owner_id, agent)
    )""",
    """CREATE TABLE IF NOT EXISTS ai_ops (
        id TEXT PRIMARY KEY,
        owner_id TEXT NOT NULL,
        agent TEXT NOT NULL,
        kind TEXT NOT NULL,
        action TEXT NOT NULL DEFAULT '',
        risk TEXT NOT NULL DEFAULT '',
        summary TEXT NOT NULL,
        args TEXT NOT NULL DEFAULT '{}',
        status TEXT NOT NULL,
        result TEXT NOT NULL DEFAULT '',
        session_id TEXT NOT NULL DEFAULT '',
        created_at TEXT NOT NULL,
        decided_at TEXT
    )""",
    "CREATE INDEX IF NOT EXISTS ai_ops_owner ON ai_ops (owner_id, created_at)",
    "CREATE INDEX IF NOT EXISTS ai_ops_status ON ai_ops (owner_id, status)",
    """CREATE TABLE IF NOT EXISTS ai_sessions (
        id TEXT PRIMARY KEY,
        owner_id TEXT NOT NULL,
        title TEXT NOT NULL,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL
    )""",
    "CREATE INDEX IF NOT EXISTS ai_sessions_owner ON ai_sessions (owner_id, updated_at)",
    """CREATE TABLE IF NOT EXISTS ai_messages (
        id TEXT PRIMARY KEY,
        session_id TEXT NOT NULL,
        role TEXT NOT NULL,
        content TEXT NOT NULL,
        agent TEXT NOT NULL DEFAULT '',
        agents TEXT NOT NULL DEFAULT '[]',
        tokens INTEGER NOT NULL DEFAULT 0,
        duration_ms INTEGER NOT NULL DEFAULT 0,
        source TEXT NOT NULL DEFAULT '',
        actions TEXT NOT NULL DEFAULT '[]',
        created_at TEXT NOT NULL
    )""",
    "CREATE INDEX IF NOT EXISTS ai_messages_session ON ai_messages (session_id, created_at)",
    # Routines: a question or command Aira runs for the person at a time on chosen days ("my morning brief at 7").
    """CREATE TABLE IF NOT EXISTS ai_routines (
        id TEXT PRIMARY KEY,
        owner_id TEXT NOT NULL,
        title TEXT NOT NULL,
        prompt TEXT NOT NULL,
        at TEXT NOT NULL,
        days TEXT NOT NULL DEFAULT '0,1,2,3,4,5,6',
        enabled INTEGER NOT NULL DEFAULT 1,
        alert INTEGER NOT NULL DEFAULT 1,
        session_id TEXT NOT NULL DEFAULT '',
        last_run_on TEXT NOT NULL DEFAULT '',
        last_answer TEXT NOT NULL DEFAULT '',
        seen_at TEXT,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL
    )""",
    "CREATE INDEX IF NOT EXISTS ai_routines_owner ON ai_routines (owner_id, enabled)",
    # Memory: what the person asked Aira to remember ("I am vegetarian"); every answer takes it into account.
    """CREATE TABLE IF NOT EXISTS ai_memories (
        id TEXT PRIMARY KEY,
        owner_id TEXT NOT NULL,
        text TEXT NOT NULL,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL
    )""",
    "CREATE INDEX IF NOT EXISTS ai_memories_owner ON ai_memories (owner_id, created_at)",
)
# Columns added after the first release (added once to an older database).
COLUMNS = {
    "ai_ops": (
        ("undo", "TEXT NOT NULL DEFAULT ''"),
        ("undone_at", "TEXT"),
        # 1: the agent took this action on its own (it counts toward its daily limit); 0: the person approved it.
        ("alone", "INTEGER NOT NULL DEFAULT 0"),
    ),
    # The most actions an agent may take alone in any 24 hours (0: no limit); more wait in Approvals.
    # ``limit_set``: 1 once the person chose the limit; until then the agent's own default_limit applies.
    "ai_agents": (
        ("daily_limit", "INTEGER NOT NULL DEFAULT 0"),
        ("limit_set", "INTEGER NOT NULL DEFAULT 0"),
    ),
    # ``reply``: the answer without the lines that say what its actions did (the action cards show those, and
    # they change when the person approves or undoes); ``rating``: 1 helpful, -1 not helpful, 0 not rated.
    "ai_messages": (
        ("reply", "TEXT NOT NULL DEFAULT ''"),
        ("rating", "INTEGER NOT NULL DEFAULT 0"),
    ),
    # ``starts_on``: the first day (account clock) a routine may run; a routine added after its time today waits
    # for its next day instead of answering at once ('' : from the day it was added).
    "ai_routines": (("starts_on", "TEXT NOT NULL DEFAULT ''"),),
}


# Filling a column for the rows written before it existed (run once, when the column is added).
BACKFILL = {
    # Before ``alone``, an action taken alone was the one decided the moment it was made.
    ("ai_ops", "alone"): "UPDATE ai_ops SET alone = 1 WHERE kind = 'action' AND decided_at = created_at "
    "AND status IN ('done', 'failed', 'undone')",
    # A limit above 0 was the person's own; 0 was only ever the default, so the agent's default applies.
    ("ai_agents", "limit_set"): "UPDATE ai_agents SET limit_set = 1 WHERE daily_limit > 0",
}  # fmt: skip


def _add_columns(connection: sqlite3.Connection) -> None:
    for table, columns in COLUMNS.items():
        have = {row[1] for row in connection.execute(f"PRAGMA table_info({table})")}  # noqa: S608 - fixed names
        for name, kind in columns:
            if name not in have:
                connection.execute(f"ALTER TABLE {table} ADD COLUMN {name} {kind}")  # noqa: S608 - fixed names
                if (table, name) in BACKFILL:
                    connection.execute(BACKFILL[(table, name)])
    connection.commit()


def ensure(connection: sqlite3.Connection) -> None:
    kit.ensure_core(connection)
    life.ensure(connection)
    kit.ensure(connection, "ai.v1", SCHEMA)
    _add_columns(connection)
