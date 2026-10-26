"""Play's tables (prefix ``pl_``), made on first use."""

from __future__ import annotations

import sqlite3

from aira.suite import kit, life

APP = "play"

SCHEMA = (
    # One game played by one person: the secret (answers, layout) never leaves the server.
    """CREATE TABLE IF NOT EXISTS pl_sessions (
        id TEXT PRIMARY KEY,
        profile_id TEXT NOT NULL,
        game TEXT NOT NULL,
        level TEXT NOT NULL,
        mode TEXT NOT NULL DEFAULT 'relaxed',
        seed INTEGER NOT NULL,
        daily_date TEXT NOT NULL DEFAULT '',
        puzzle TEXT NOT NULL,
        secret TEXT NOT NULL,
        status TEXT NOT NULL DEFAULT 'playing',
        score INTEGER NOT NULL DEFAULT 0,
        result TEXT NOT NULL DEFAULT '{}',
        best INTEGER NOT NULL DEFAULT 0,
        started_at TEXT NOT NULL,
        finished_at TEXT,
        local_date TEXT NOT NULL DEFAULT ''
    )""",
    "CREATE INDEX IF NOT EXISTS pl_sessions_person ON pl_sessions (profile_id, status, game)",
    "CREATE INDEX IF NOT EXISTS pl_sessions_daily ON pl_sessions (daily_date, status)",
    "CREATE UNIQUE INDEX IF NOT EXISTS pl_sessions_one_daily ON pl_sessions (profile_id, daily_date) WHERE daily_date != ''",
    # Live trivia rooms.
    """CREATE TABLE IF NOT EXISTS pl_rooms (
        id TEXT PRIMARY KEY,
        code TEXT NOT NULL,
        host_id TEXT NOT NULL,
        title TEXT NOT NULL DEFAULT '',
        category TEXT NOT NULL DEFAULT '',
        level TEXT NOT NULL DEFAULT 'medium',
        seconds INTEGER NOT NULL DEFAULT 30,
        auto_advance INTEGER NOT NULL DEFAULT 1,
        questions TEXT NOT NULL,
        status TEXT NOT NULL DEFAULT 'lobby',
        question_index INTEGER NOT NULL DEFAULT 0,
        question_started_at TEXT,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL,
        expires_at TEXT NOT NULL
    )""",
    "CREATE INDEX IF NOT EXISTS pl_rooms_code ON pl_rooms (code, status)",
    """CREATE TABLE IF NOT EXISTS pl_room_players (
        id TEXT PRIMARY KEY,
        room_id TEXT NOT NULL,
        profile_id TEXT,
        guest_name TEXT NOT NULL DEFAULT '',
        guest_token_hash TEXT NOT NULL DEFAULT '',
        score INTEGER NOT NULL DEFAULT 0,
        joined_at TEXT NOT NULL
    )""",
    "CREATE UNIQUE INDEX IF NOT EXISTS pl_room_players_person ON pl_room_players (room_id, profile_id) WHERE profile_id IS NOT NULL",
    """CREATE TABLE IF NOT EXISTS pl_room_answers (
        room_id TEXT NOT NULL,
        player_id TEXT NOT NULL,
        question_index INTEGER NOT NULL,
        choice INTEGER NOT NULL,
        correct INTEGER NOT NULL,
        points INTEGER NOT NULL,
        answered_at TEXT NOT NULL,
        PRIMARY KEY (room_id, player_id, question_index)
    )""",
    # Things to watch, read, listen to or play, shared by the household.
    """CREATE TABLE IF NOT EXISTS pl_watch (
        id TEXT PRIMARY KEY,
        kind TEXT NOT NULL,
        title TEXT NOT NULL,
        creator TEXT NOT NULL DEFAULT '',
        year INTEGER,
        link TEXT NOT NULL DEFAULT '',
        recommended_by TEXT NOT NULL DEFAULT '',
        added_by TEXT NOT NULL,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL
    )""",
    """CREATE TABLE IF NOT EXISTS pl_watch_status (
        item_id TEXT NOT NULL,
        profile_id TEXT NOT NULL,
        status TEXT NOT NULL DEFAULT 'want',
        rating INTEGER,
        notes TEXT NOT NULL DEFAULT '',
        updated_at TEXT NOT NULL,
        PRIMARY KEY (item_id, profile_id)
    )""",
    """CREATE TABLE IF NOT EXISTS pl_watch_plans (
        id TEXT PRIMARY KEY,
        item_id TEXT NOT NULL,
        planned_for TEXT NOT NULL,
        who TEXT NOT NULL DEFAULT '[]',
        note TEXT NOT NULL DEFAULT '',
        created_by TEXT NOT NULL,
        created_at TEXT NOT NULL
    )""",
    "CREATE INDEX IF NOT EXISTS pl_watch_plans_time ON pl_watch_plans (planned_for)",
)


def ensure(connection: sqlite3.Connection) -> None:
    life.ensure(connection)
    kit.ensure(connection, "play.v1", SCHEMA)
