"""Videos' tables (platform-wide: see db.py) and small row helpers.

Every table starts with ``vi_``. A *channel* is a person's identity in Videos (one per person, made on first use);
it also stands for that person as a viewer, so likes, comments, history and subscriptions are keyed by channel id.
"""

from __future__ import annotations

import json
import re
import sqlite3
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

APP = "videos"

SCHEMA = (
    """CREATE TABLE IF NOT EXISTS vi_channels (
        id TEXT PRIMARY KEY,
        person_key TEXT NOT NULL UNIQUE,
        home TEXT NOT NULL DEFAULT '',
        name TEXT NOT NULL,
        about TEXT NOT NULL DEFAULT '',
        avatar TEXT NOT NULL DEFAULT '🙂',
        age_group TEXT NOT NULL DEFAULT 'adult',
        country TEXT NOT NULL DEFAULT '',
        language TEXT NOT NULL DEFAULT 'en',
        restricted INTEGER NOT NULL DEFAULT 0,
        terms_accepted_at TEXT,
        monetized INTEGER NOT NULL DEFAULT 0,
        payout_details TEXT NOT NULL DEFAULT '',
        suspended_at TEXT,
        suspended_reason TEXT NOT NULL DEFAULT '',
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL
    )""",
    "CREATE INDEX IF NOT EXISTS vi_channels_home ON vi_channels (home)",
    """CREATE TABLE IF NOT EXISTS vi_files (
        id TEXT PRIMARY KEY,
        owner_key TEXT NOT NULL,
        home TEXT NOT NULL DEFAULT '',
        kind TEXT NOT NULL,
        mime TEXT NOT NULL,
        size INTEGER NOT NULL,
        path TEXT NOT NULL,
        duration REAL,
        width INTEGER,
        height INTEGER,
        created_at TEXT NOT NULL
    )""",
    "CREATE INDEX IF NOT EXISTS vi_files_owner ON vi_files (owner_key)",
    "CREATE INDEX IF NOT EXISTS vi_files_home ON vi_files (home)",
    """CREATE TABLE IF NOT EXISTS vi_uploads (
        id TEXT PRIMARY KEY,
        owner_key TEXT NOT NULL,
        home TEXT NOT NULL DEFAULT '',
        kind TEXT NOT NULL,
        filename TEXT NOT NULL DEFAULT '',
        size INTEGER NOT NULL,
        received INTEGER NOT NULL DEFAULT 0,
        mime TEXT NOT NULL DEFAULT '',
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL
    )""",
    """CREATE TABLE IF NOT EXISTS vi_videos (
        id TEXT PRIMARY KEY,
        channel_id TEXT NOT NULL,
        kind TEXT NOT NULL DEFAULT 'video',
        title TEXT NOT NULL,
        description TEXT NOT NULL DEFAULT '',
        category TEXT NOT NULL DEFAULT 'other',
        tags TEXT NOT NULL DEFAULT '[]',
        rating TEXT NOT NULL DEFAULT 'everyone',
        for_kids INTEGER NOT NULL DEFAULT 0,
        visibility TEXT NOT NULL DEFAULT 'public',
        language TEXT NOT NULL DEFAULT 'en',
        genre TEXT NOT NULL DEFAULT '',
        credits TEXT NOT NULL DEFAULT '[]',
        duration REAL NOT NULL DEFAULT 0,
        width INTEGER NOT NULL DEFAULT 0,
        height INTEGER NOT NULL DEFAULT 0,
        file_id TEXT NOT NULL,
        thumb_file_id TEXT NOT NULL DEFAULT '',
        captions_file_id TEXT NOT NULL DEFAULT '',
        ads_enabled INTEGER NOT NULL DEFAULT 1,
        views INTEGER NOT NULL DEFAULT 0,
        likes INTEGER NOT NULL DEFAULT 0,
        comments INTEGER NOT NULL DEFAULT 0,
        hidden_at TEXT,
        hidden_reason TEXT NOT NULL DEFAULT '',
        takedown_at TEXT,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL,
        deleted_at TEXT
    )""",
    "CREATE INDEX IF NOT EXISTS vi_videos_channel ON vi_videos (channel_id, created_at)",
    "CREATE INDEX IF NOT EXISTS vi_videos_recent ON vi_videos (visibility, created_at)",
    """CREATE TABLE IF NOT EXISTS vi_views (
        video_id TEXT NOT NULL,
        viewer_key TEXT NOT NULL,
        day TEXT NOT NULL,
        at TEXT NOT NULL,
        PRIMARY KEY (video_id, viewer_key, day)
    )""",
    "CREATE INDEX IF NOT EXISTS vi_views_day ON vi_views (day, video_id)",
    """CREATE TABLE IF NOT EXISTS vi_watch (
        viewer_id TEXT NOT NULL,
        video_id TEXT NOT NULL,
        position REAL NOT NULL DEFAULT 0,
        seconds REAL NOT NULL DEFAULT 0,
        started_at TEXT NOT NULL,
        last_at TEXT NOT NULL,
        cleared INTEGER NOT NULL DEFAULT 0,
        PRIMARY KEY (viewer_id, video_id)
    )""",
    "CREATE INDEX IF NOT EXISTS vi_watch_last ON vi_watch (viewer_id, last_at)",
    """CREATE TABLE IF NOT EXISTS vi_likes (
        video_id TEXT NOT NULL,
        viewer_id TEXT NOT NULL,
        at TEXT NOT NULL,
        PRIMARY KEY (video_id, viewer_id)
    )""",
    """CREATE TABLE IF NOT EXISTS vi_comments (
        id TEXT PRIMARY KEY,
        video_id TEXT NOT NULL,
        author_id TEXT NOT NULL,
        text TEXT NOT NULL,
        created_at TEXT NOT NULL,
        deleted_at TEXT,
        hidden_at TEXT
    )""",
    "CREATE INDEX IF NOT EXISTS vi_comments_video ON vi_comments (video_id, created_at)",
    """CREATE TABLE IF NOT EXISTS vi_subscriptions (
        subscriber_id TEXT NOT NULL,
        channel_id TEXT NOT NULL,
        at TEXT NOT NULL,
        PRIMARY KEY (subscriber_id, channel_id)
    )""",
    "CREATE INDEX IF NOT EXISTS vi_subscriptions_channel ON vi_subscriptions (channel_id)",
    """CREATE TABLE IF NOT EXISTS vi_later (
        viewer_id TEXT NOT NULL,
        video_id TEXT NOT NULL,
        at TEXT NOT NULL,
        PRIMARY KEY (viewer_id, video_id)
    )""",
    """CREATE TABLE IF NOT EXISTS vi_playlists (
        id TEXT PRIMARY KEY,
        channel_id TEXT NOT NULL,
        name TEXT NOT NULL,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL
    )""",
    """CREATE TABLE IF NOT EXISTS vi_playlist_items (
        playlist_id TEXT NOT NULL,
        video_id TEXT NOT NULL,
        position INTEGER NOT NULL DEFAULT 0,
        added_at TEXT NOT NULL,
        PRIMARY KEY (playlist_id, video_id)
    )""",
    """CREATE TABLE IF NOT EXISTS vi_reports (
        id TEXT PRIMARY KEY,
        reporter_id TEXT NOT NULL,
        target_type TEXT NOT NULL,
        target_id TEXT NOT NULL,
        reason TEXT NOT NULL DEFAULT '',
        status TEXT NOT NULL DEFAULT 'open',
        action TEXT NOT NULL DEFAULT '',
        created_at TEXT NOT NULL,
        resolved_at TEXT,
        resolved_by TEXT NOT NULL DEFAULT ''
    )""",
    "CREATE INDEX IF NOT EXISTS vi_reports_status ON vi_reports (status, created_at)",
    """CREATE TABLE IF NOT EXISTS vi_copyright (
        id TEXT PRIMARY KEY,
        video_id TEXT NOT NULL,
        channel_id TEXT NOT NULL DEFAULT '',
        claimant_name TEXT NOT NULL,
        claimant_email TEXT NOT NULL,
        claimant_organization TEXT NOT NULL DEFAULT '',
        work TEXT NOT NULL,
        signature TEXT NOT NULL,
        status TEXT NOT NULL DEFAULT 'received',
        counter_name TEXT NOT NULL DEFAULT '',
        counter_text TEXT NOT NULL DEFAULT '',
        counter_at TEXT,
        decision_note TEXT NOT NULL DEFAULT '',
        decided_by TEXT NOT NULL DEFAULT '',
        client_hash TEXT NOT NULL DEFAULT '',
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL
    )""",
    "CREATE INDEX IF NOT EXISTS vi_copyright_video ON vi_copyright (video_id)",
    """CREATE TABLE IF NOT EXISTS vi_notifications (
        id TEXT PRIMARY KEY,
        channel_id TEXT NOT NULL,
        kind TEXT NOT NULL,
        text TEXT NOT NULL,
        ref_type TEXT NOT NULL DEFAULT '',
        ref_id TEXT NOT NULL DEFAULT '',
        created_at TEXT NOT NULL,
        read_at TEXT
    )""",
    "CREATE INDEX IF NOT EXISTS vi_notifications_channel ON vi_notifications (channel_id, created_at)",
    """CREATE TABLE IF NOT EXISTS vi_rate (
        key TEXT NOT NULL,
        kind TEXT NOT NULL,
        window TEXT NOT NULL,
        count INTEGER NOT NULL DEFAULT 0,
        PRIMARY KEY (key, kind)
    )""",
    """CREATE TABLE IF NOT EXISTS vi_settings (
        key TEXT PRIMARY KEY,
        value TEXT NOT NULL
    )""",
    # Seconds really watched, per video per day (others' watching only): the creator's analytics.
    """CREATE TABLE IF NOT EXISTS vi_watch_days (
        video_id TEXT NOT NULL,
        day TEXT NOT NULL,
        seconds REAL NOT NULL DEFAULT 0,
        PRIMARY KEY (video_id, day)
    )""",
    # A creator's own way to be supported by viewers (their payment page or UPI id; Aira moves no money).
    """CREATE TABLE IF NOT EXISTS vi_support (
        channel_id TEXT PRIMARY KEY,
        enabled INTEGER NOT NULL DEFAULT 0,
        link TEXT NOT NULL DEFAULT '',
        upi TEXT NOT NULL DEFAULT '',
        note TEXT NOT NULL DEFAULT '',
        currency TEXT NOT NULL DEFAULT 'USD',
        updated_at TEXT NOT NULL
    )""",
    # A viewer's pledge to support a channel; the creator marks it received (or not) once the money arrives.
    """CREATE TABLE IF NOT EXISTS vi_tips (
        id TEXT PRIMARY KEY,
        channel_id TEXT NOT NULL,
        supporter_id TEXT NOT NULL,
        video_id TEXT NOT NULL DEFAULT '',
        amount_cents INTEGER NOT NULL,
        currency TEXT NOT NULL DEFAULT 'USD',
        message TEXT NOT NULL DEFAULT '',
        show_name INTEGER NOT NULL DEFAULT 1,
        reference TEXT NOT NULL,
        status TEXT NOT NULL DEFAULT 'pledged',
        created_at TEXT NOT NULL,
        decided_at TEXT
    )""",
    "CREATE INDEX IF NOT EXISTS vi_tips_channel ON vi_tips (channel_id, created_at)",
    "CREATE INDEX IF NOT EXISTS vi_tips_supporter ON vi_tips (supporter_id, created_at)",
    # A creator's paid membership: a monthly price, what members get, paid on the creator's own page or UPI id.
    """CREATE TABLE IF NOT EXISTS vi_member_plans (
        channel_id TEXT PRIMARY KEY,
        enabled INTEGER NOT NULL DEFAULT 0,
        price_cents INTEGER NOT NULL DEFAULT 0,
        currency TEXT NOT NULL DEFAULT 'USD',
        perks TEXT NOT NULL DEFAULT '',
        updated_at TEXT NOT NULL
    )""",
    # One row per member of a channel; they may watch members-only videos until paid_until.
    """CREATE TABLE IF NOT EXISTS vi_members (
        id TEXT PRIMARY KEY,
        channel_id TEXT NOT NULL,
        member_id TEXT NOT NULL,
        paid_until TEXT,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL,
        UNIQUE (channel_id, member_id)
    )""",
    "CREATE INDEX IF NOT EXISTS vi_members_member ON vi_members (member_id)",
    # Each month (or months) a member pays for; the creator marks it received when the money arrives.
    """CREATE TABLE IF NOT EXISTS vi_member_payments (
        id TEXT PRIMARY KEY,
        membership_id TEXT NOT NULL,
        channel_id TEXT NOT NULL,
        member_id TEXT NOT NULL,
        months INTEGER NOT NULL DEFAULT 1,
        amount_cents INTEGER NOT NULL,
        currency TEXT NOT NULL DEFAULT 'USD',
        reference TEXT NOT NULL,
        status TEXT NOT NULL DEFAULT 'pledged',
        created_at TEXT NOT NULL,
        decided_at TEXT
    )""",
    "CREATE INDEX IF NOT EXISTS vi_member_payments_channel ON vi_member_payments (channel_id, created_at)",
    "CREATE INDEX IF NOT EXISTS vi_member_payments_member ON vi_member_payments (member_id, created_at)",
    # A creator's brand deals (sponsorships): their own records, from first contact to the money received.
    """CREATE TABLE IF NOT EXISTS vi_deals (
        id TEXT PRIMARY KEY,
        channel_id TEXT NOT NULL,
        brand TEXT NOT NULL,
        contact TEXT NOT NULL DEFAULT '',
        amount_cents INTEGER NOT NULL DEFAULT 0,
        currency TEXT NOT NULL DEFAULT 'USD',
        video_id TEXT NOT NULL DEFAULT '',
        status TEXT NOT NULL DEFAULT 'proposed',
        due_on TEXT NOT NULL DEFAULT '',
        notes TEXT NOT NULL DEFAULT '',
        paid_at TEXT,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL
    )""",
    "CREATE INDEX IF NOT EXISTS vi_deals_channel ON vi_deals (channel_id, updated_at)",
)

# Columns added after the first release: (table, column, declaration). ``migrate`` adds the missing ones to a
# database made before them (CREATE TABLE IF NOT EXISTS does not change a table that exists).
COLUMNS = (
    ("vi_videos", "members_only", "INTEGER NOT NULL DEFAULT 0"),
    ("vi_videos", "paid_promotion", "INTEGER NOT NULL DEFAULT 0"),
    # 1 when this deal turned on its video's "Includes paid promotion" (it goes off again with the deal)
    ("vi_deals", "promoted", "INTEGER NOT NULL DEFAULT 0"),
)


def migrate(connection: sqlite3.Connection) -> None:
    for table, column, declaration in COLUMNS:
        present = {
            item[1] for item in connection.execute(f"PRAGMA table_info({table})")
        }
        if column in present:
            continue
        try:
            connection.execute(f"ALTER TABLE {table} ADD COLUMN {column} {declaration}")
        except sqlite3.OperationalError as error:  # another process added it first
            if "duplicate column" not in str(error).lower():
                raise


HEX = re.compile(r"[0-9a-f]{32}")


def new_id() -> str:
    return uuid.uuid4().hex


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def stamp(moment: datetime | None = None) -> str:
    """ISO 8601 UTC with microseconds (an ad's two seconds on screen are measured with these)."""
    return (
        (moment or utcnow()).astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")
    )


def parse(value: str) -> datetime:
    return datetime.fromisoformat(str(value).replace("Z", "+00:00"))


def later(hours: float = 0, moment: datetime | None = None) -> str:
    return stamp((moment or utcnow()) + timedelta(hours=hours))


def day(moment: datetime | None = None) -> str:
    return (moment or utcnow()).astimezone(timezone.utc).date().isoformat()


def month(moment: datetime | None = None) -> str:
    return day(moment)[:7]


def row(
    connection: sqlite3.Connection, sql: str, params: tuple[Any, ...] = ()
) -> dict[str, Any] | None:
    found = connection.execute(sql, params).fetchone()
    return dict(found) if found else None


def rows(
    connection: sqlite3.Connection, sql: str, params: tuple[Any, ...] = ()
) -> list[dict[str, Any]]:
    return [dict(found) for found in connection.execute(sql, params).fetchall()]


def scalar(
    connection: sqlite3.Connection, sql: str, params: tuple[Any, ...] = ()
) -> Any:
    found = connection.execute(sql, params).fetchone()
    return found[0] if found else None


def loads(value: Any, default: Any) -> Any:
    try:
        return json.loads(value) if value else default
    except (TypeError, ValueError):
        return default


def dumps(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def setting(connection: sqlite3.Connection, key: str, default: Any) -> Any:
    found = row(connection, "SELECT value FROM vi_settings WHERE key = ?", (key,))
    return loads(found["value"], default) if found else default


def set_setting(connection: sqlite3.Connection, key: str, value: Any) -> None:
    connection.execute(
        "INSERT INTO vi_settings (key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value",
        (key, dumps(value)),
    )


def check_id(value: str, label: str = "item") -> str:
    if not HEX.fullmatch(str(value or "")):
        raise LookupError(f"That {label} was not found.")
    return str(value)


def update(
    connection: sqlite3.Connection, table: str, row_id: str, changes: dict[str, Any]
) -> None:
    if not changes:
        return
    columns = ", ".join(f"{name} = ?" for name in changes)
    connection.execute(
        f"UPDATE {table} SET {columns} WHERE id = ?",  # noqa: S608 - table and column names are ours
        (*changes.values(), row_id),
    )
