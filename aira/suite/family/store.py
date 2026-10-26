"""Family's tables (``fm_*``) and the small helpers every part of the app uses."""

from __future__ import annotations

import re
import sqlite3
from datetime import datetime, timezone
from typing import Any

from aira.suite import kit, life

APP = "family"
REACTIONS = ("❤️", "👍", "😂", "😮", "🎉")
MILESTONES = (
    "",
    "birthday",
    "new_job",
    "holiday",
    "baby",
    "wedding",
    "graduation",
    "home",
    "achievement",
    "other",
)
RSVP_ANSWERS = ("yes", "maybe", "no")
DATE_KINDS = ("birthday", "anniversary", "other")
MAX_PHOTOS = 6
_STAMP = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(\.\d{1,6})?Z$")

SCHEMA = (
    """CREATE TABLE IF NOT EXISTS fm_posts (
        id TEXT PRIMARY KEY,
        author_id TEXT NOT NULL DEFAULT '',
        author_name TEXT NOT NULL DEFAULT '',
        source TEXT NOT NULL DEFAULT 'member',
        text TEXT NOT NULL DEFAULT '',
        photos TEXT NOT NULL DEFAULT '[]',
        milestone TEXT NOT NULL DEFAULT '',
        pinned INTEGER NOT NULL DEFAULT 0,
        status TEXT NOT NULL DEFAULT 'live',
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL
    )""",
    "CREATE INDEX IF NOT EXISTS fm_posts_created ON fm_posts (status, created_at)",
    "CREATE INDEX IF NOT EXISTS fm_posts_updated ON fm_posts (updated_at)",
    """CREATE TABLE IF NOT EXISTS fm_reactions (
        post_id TEXT NOT NULL,
        who TEXT NOT NULL,
        emoji TEXT NOT NULL,
        created_at TEXT NOT NULL,
        PRIMARY KEY (post_id, who, emoji)
    )""",
    """CREATE TABLE IF NOT EXISTS fm_comments (
        id TEXT PRIMARY KEY,
        post_id TEXT NOT NULL,
        author_id TEXT NOT NULL DEFAULT '',
        author_name TEXT NOT NULL DEFAULT '',
        text TEXT NOT NULL,
        created_at TEXT NOT NULL,
        removed_at TEXT
    )""",
    "CREATE INDEX IF NOT EXISTS fm_comments_post ON fm_comments (post_id, created_at)",
    """CREATE TABLE IF NOT EXISTS fm_rooms (
        id TEXT PRIMARY KEY,
        kind TEXT NOT NULL,
        member_a TEXT NOT NULL DEFAULT '',
        member_b TEXT NOT NULL DEFAULT '',
        created_at TEXT NOT NULL,
        UNIQUE (kind, member_a, member_b)
    )""",
    """CREATE TABLE IF NOT EXISTS fm_messages (
        id TEXT PRIMARY KEY,
        room_id TEXT NOT NULL,
        author_id TEXT NOT NULL,
        text TEXT NOT NULL DEFAULT '',
        photo TEXT NOT NULL DEFAULT '',
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL,
        deleted_at TEXT
    )""",
    "CREATE INDEX IF NOT EXISTS fm_messages_room ON fm_messages (room_id, created_at)",
    "CREATE INDEX IF NOT EXISTS fm_messages_updated ON fm_messages (room_id, updated_at)",
    """CREATE TABLE IF NOT EXISTS fm_reads (
        room_id TEXT NOT NULL,
        profile_id TEXT NOT NULL,
        last_read_at TEXT NOT NULL,
        PRIMARY KEY (room_id, profile_id)
    )""",
    """CREATE TABLE IF NOT EXISTS fm_seen (
        profile_id TEXT NOT NULL,
        what TEXT NOT NULL,
        seen_at TEXT NOT NULL,
        PRIMARY KEY (profile_id, what)
    )""",
    """CREATE TABLE IF NOT EXISTS fm_events (
        id TEXT PRIMARY KEY,
        title TEXT NOT NULL,
        starts_at TEXT NOT NULL,
        ends_at TEXT NOT NULL DEFAULT '',
        all_day INTEGER NOT NULL DEFAULT 0,
        place TEXT NOT NULL DEFAULT '',
        notes TEXT NOT NULL DEFAULT '',
        invitees TEXT NOT NULL DEFAULT '[]',
        created_by TEXT NOT NULL,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL,
        cancelled_at TEXT
    )""",
    "CREATE INDEX IF NOT EXISTS fm_events_start ON fm_events (starts_at)",
    """CREATE TABLE IF NOT EXISTS fm_rsvps (
        event_id TEXT NOT NULL,
        profile_id TEXT NOT NULL,
        answer TEXT NOT NULL,
        answered_at TEXT NOT NULL,
        PRIMARY KEY (event_id, profile_id)
    )""",
    """CREATE TABLE IF NOT EXISTS fm_dates (
        id TEXT PRIMARY KEY,
        name TEXT NOT NULL,
        date TEXT NOT NULL,
        kind TEXT NOT NULL DEFAULT 'birthday',
        relation TEXT NOT NULL DEFAULT '',
        year_known INTEGER NOT NULL DEFAULT 1,
        created_by TEXT NOT NULL,
        created_at TEXT NOT NULL
    )""",
)


def ensure(connection: sqlite3.Connection) -> None:
    life.ensure(connection)
    kit.ensure(connection, "family.v1", SCHEMA)


def stamp() -> str:
    """A sortable time with microseconds: messages in the same second keep their order and ``since`` works."""
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def since_value(value: Any) -> str:
    """A ``since`` the browser sent back (one of our stamps); empty for none."""
    text = str(value or "").strip()
    if not text:
        return ""
    if not _STAMP.match(text):
        raise ValueError(
            "since must be a time this app sent (like 2026-10-25T09:30:00.000000Z)."
        )
    if "." not in text:
        text = text[:-1] + ".000000Z"
    whole, fraction = text[:-1].split(".")
    return f"{whole}.{fraction.ljust(6, '0')[:6]}Z"


def photos(value: Any) -> list[str]:
    items = (
        value if isinstance(value, list) else [p for p in str(value or "").split() if p]
    )
    out: list[str] = []
    for item in items:
        link = kit.url(item, "a photo link")
        if not link:
            continue
        if not link.lower().startswith("https://"):
            raise ValueError("Photo links must start with https://.")
        if link not in out:
            out.append(link)
    if len(out) > MAX_PHOTOS:
        raise ValueError(f"A post can have at most {MAX_PHOTOS} photos.")
    return out


def people(connection: sqlite3.Connection) -> dict[str, dict[str, Any]]:
    """Everyone in the household, by id (public fields only)."""
    return {p["id"]: life.public(p) for p in life.profiles(connection)}


def seen_at(connection: sqlite3.Connection, profile_id: str, what: str) -> str:
    row = connection.execute(
        "SELECT seen_at FROM fm_seen WHERE profile_id = ? AND what = ?",
        (profile_id, what),
    ).fetchone()
    return str(row[0]) if row else ""


def mark_seen(connection: sqlite3.Connection, profile_id: str, what: str) -> str:
    moment = stamp()
    connection.execute(
        """INSERT INTO fm_seen (profile_id, what, seen_at) VALUES (?, ?, ?)
        ON CONFLICT(profile_id, what) DO UPDATE SET seen_at = excluded.seen_at""",
        (profile_id, what, moment),
    )
    return moment
