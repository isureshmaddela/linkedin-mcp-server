"""Social's tables (platform-wide: see db.py) and small row helpers."""

from __future__ import annotations

import json
import re
import sqlite3
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any
from aira import dbutil

APP = "social"

SCHEMA = (
    """CREATE TABLE IF NOT EXISTS so_accounts (
        id TEXT PRIMARY KEY,
        person_key TEXT NOT NULL UNIQUE,
        handle TEXT NOT NULL UNIQUE,
        display_name TEXT NOT NULL,
        avatar TEXT NOT NULL DEFAULT '🙂',
        bio TEXT NOT NULL DEFAULT '',
        age_group TEXT NOT NULL DEFAULT 'adult',
        visibility TEXT NOT NULL DEFAULT 'public',
        open_messages INTEGER NOT NULL DEFAULT 0,
        home TEXT NOT NULL DEFAULT '',
        headline TEXT NOT NULL DEFAULT '',
        open_to_work INTEGER NOT NULL DEFAULT 0,
        skills TEXT NOT NULL DEFAULT '[]',
        experience TEXT NOT NULL DEFAULT '[]',
        moderator INTEGER NOT NULL DEFAULT 0,
        suspended_at TEXT,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL,
        last_seen_at TEXT
    )""",
    """CREATE TABLE IF NOT EXISTS so_friends (
        a_id TEXT NOT NULL,
        b_id TEXT NOT NULL,
        status TEXT NOT NULL DEFAULT 'pending',
        requested_by TEXT NOT NULL,
        created_at TEXT NOT NULL,
        accepted_at TEXT,
        PRIMARY KEY (a_id, b_id)
    )""",
    "CREATE INDEX IF NOT EXISTS so_friends_b ON so_friends (b_id)",
    """CREATE TABLE IF NOT EXISTS so_follows (
        follower_id TEXT NOT NULL,
        target_type TEXT NOT NULL,
        target_id TEXT NOT NULL,
        created_at TEXT NOT NULL,
        PRIMARY KEY (follower_id, target_type, target_id)
    )""",
    "CREATE INDEX IF NOT EXISTS so_follows_target ON so_follows (target_type, target_id)",
    """CREATE TABLE IF NOT EXISTS so_blocks (
        blocker_id TEXT NOT NULL,
        blocked_id TEXT NOT NULL,
        created_at TEXT NOT NULL,
        PRIMARY KEY (blocker_id, blocked_id)
    )""",
    """CREATE TABLE IF NOT EXISTS so_posts (
        id TEXT PRIMARY KEY,
        author_id TEXT NOT NULL,
        audience TEXT NOT NULL,
        group_id TEXT,
        page_id TEXT,
        text TEXT NOT NULL DEFAULT '',
        photos TEXT NOT NULL DEFAULT '[]',
        shared_post_id TEXT,
        tags TEXT NOT NULL DEFAULT '',
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL,
        deleted_at TEXT,
        hidden_at TEXT
    )""",
    "CREATE INDEX IF NOT EXISTS so_posts_author ON so_posts (author_id, created_at)",
    "CREATE INDEX IF NOT EXISTS so_posts_updated ON so_posts (updated_at)",
    "CREATE INDEX IF NOT EXISTS so_posts_group ON so_posts (group_id, created_at)",
    "CREATE INDEX IF NOT EXISTS so_posts_page ON so_posts (page_id, created_at)",
    """CREATE TABLE IF NOT EXISTS so_post_tags (
        tag TEXT NOT NULL,
        post_id TEXT NOT NULL,
        created_at TEXT NOT NULL,
        PRIMARY KEY (tag, post_id)
    )""",
    """CREATE TABLE IF NOT EXISTS so_reactions (
        post_id TEXT NOT NULL,
        account_id TEXT NOT NULL,
        emoji TEXT NOT NULL,
        created_at TEXT NOT NULL,
        PRIMARY KEY (post_id, account_id)
    )""",
    """CREATE TABLE IF NOT EXISTS so_comments (
        id TEXT PRIMARY KEY,
        post_id TEXT NOT NULL,
        author_id TEXT NOT NULL,
        text TEXT NOT NULL,
        created_at TEXT NOT NULL,
        deleted_at TEXT,
        hidden_at TEXT
    )""",
    "CREATE INDEX IF NOT EXISTS so_comments_post ON so_comments (post_id, created_at)",
    """CREATE TABLE IF NOT EXISTS so_stories (
        id TEXT PRIMARY KEY,
        author_id TEXT NOT NULL,
        text TEXT NOT NULL DEFAULT '',
        photo TEXT NOT NULL DEFAULT '',
        color TEXT NOT NULL DEFAULT '#2563eb',
        created_at TEXT NOT NULL,
        expires_at TEXT NOT NULL,
        deleted_at TEXT,
        hidden_at TEXT
    )""",
    "CREATE INDEX IF NOT EXISTS so_stories_author ON so_stories (author_id, expires_at)",
    """CREATE TABLE IF NOT EXISTS so_story_views (
        story_id TEXT NOT NULL,
        viewer_id TEXT NOT NULL,
        at TEXT NOT NULL,
        PRIMARY KEY (story_id, viewer_id)
    )""",
    """CREATE TABLE IF NOT EXISTS so_chats (
        id TEXT PRIMARY KEY,
        kind TEXT NOT NULL,
        title TEXT NOT NULL DEFAULT '',
        direct_key TEXT UNIQUE,
        created_by TEXT NOT NULL,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL
    )""",
    """CREATE TABLE IF NOT EXISTS so_chat_members (
        chat_id TEXT NOT NULL,
        account_id TEXT NOT NULL,
        role TEXT NOT NULL DEFAULT 'member',
        joined_at TEXT NOT NULL,
        left_at TEXT,
        last_read_at TEXT NOT NULL DEFAULT '',
        muted INTEGER NOT NULL DEFAULT 0,
        PRIMARY KEY (chat_id, account_id)
    )""",
    "CREATE INDEX IF NOT EXISTS so_chat_members_account ON so_chat_members (account_id)",
    """CREATE TABLE IF NOT EXISTS so_messages (
        id TEXT PRIMARY KEY,
        chat_id TEXT NOT NULL,
        author_id TEXT NOT NULL,
        text TEXT NOT NULL DEFAULT '',
        photo TEXT NOT NULL DEFAULT '',
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL,
        deleted_at TEXT,
        hidden_at TEXT
    )""",
    "CREATE INDEX IF NOT EXISTS so_messages_chat ON so_messages (chat_id, updated_at)",
    """CREATE TABLE IF NOT EXISTS so_groups (
        id TEXT PRIMARY KEY,
        name TEXT NOT NULL,
        about TEXT NOT NULL DEFAULT '',
        visibility TEXT NOT NULL DEFAULT 'public',
        created_by TEXT NOT NULL,
        created_at TEXT NOT NULL,
        archived_at TEXT
    )""",
    """CREATE TABLE IF NOT EXISTS so_group_members (
        group_id TEXT NOT NULL,
        account_id TEXT NOT NULL,
        role TEXT NOT NULL DEFAULT 'member',
        status TEXT NOT NULL DEFAULT 'member',
        joined_at TEXT NOT NULL,
        PRIMARY KEY (group_id, account_id)
    )""",
    "CREATE INDEX IF NOT EXISTS so_group_members_account ON so_group_members (account_id)",
    """CREATE TABLE IF NOT EXISTS so_pages (
        id TEXT PRIMARY KEY,
        name TEXT NOT NULL,
        about TEXT NOT NULL DEFAULT '',
        category TEXT NOT NULL DEFAULT '',
        link TEXT NOT NULL DEFAULT '',
        created_by TEXT NOT NULL,
        created_at TEXT NOT NULL,
        archived_at TEXT
    )""",
    """CREATE TABLE IF NOT EXISTS so_page_admins (
        page_id TEXT NOT NULL,
        account_id TEXT NOT NULL,
        PRIMARY KEY (page_id, account_id)
    )""",
    """CREATE TABLE IF NOT EXISTS so_notifications (
        id TEXT PRIMARY KEY,
        account_id TEXT NOT NULL,
        kind TEXT NOT NULL,
        actor_id TEXT NOT NULL DEFAULT '',
        ref_type TEXT NOT NULL DEFAULT '',
        ref_id TEXT NOT NULL DEFAULT '',
        text TEXT NOT NULL DEFAULT '',
        created_at TEXT NOT NULL,
        read_at TEXT
    )""",
    "CREATE INDEX IF NOT EXISTS so_notifications_account ON so_notifications (account_id, created_at)",
    """CREATE TABLE IF NOT EXISTS so_reports (
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
    """CREATE TABLE IF NOT EXISTS so_rate (
        account_id TEXT NOT NULL,
        kind TEXT NOT NULL,
        window TEXT NOT NULL,
        count INTEGER NOT NULL DEFAULT 0,
        PRIMARY KEY (account_id, kind)
    )""",
    """CREATE TABLE IF NOT EXISTS so_settings (
        key TEXT PRIMARY KEY,
        value TEXT NOT NULL
    )""",
    """CREATE TABLE IF NOT EXISTS so_listings (
        id TEXT PRIMARY KEY,
        seller_id TEXT NOT NULL,
        title TEXT NOT NULL,
        about TEXT NOT NULL DEFAULT '',
        price_cents INTEGER NOT NULL DEFAULT 0,
        currency TEXT NOT NULL DEFAULT 'USD',
        category TEXT NOT NULL DEFAULT 'other',
        condition TEXT NOT NULL DEFAULT 'used',
        place TEXT NOT NULL DEFAULT '',
        photos TEXT NOT NULL DEFAULT '[]',
        audience TEXT NOT NULL DEFAULT 'public',
        payment_link TEXT NOT NULL DEFAULT '',
        for_business INTEGER NOT NULL DEFAULT 0,
        business_link TEXT NOT NULL DEFAULT '',
        status TEXT NOT NULL DEFAULT 'available',
        buyer TEXT NOT NULL DEFAULT '',
        sold_cents INTEGER NOT NULL DEFAULT 0,
        sold_at TEXT,
        views INTEGER NOT NULL DEFAULT 0,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL,
        deleted_at TEXT,
        hidden_at TEXT
    )""",
    "CREATE INDEX IF NOT EXISTS so_listings_seller ON so_listings (seller_id, created_at)",
    "CREATE INDEX IF NOT EXISTS so_listings_status ON so_listings (status, created_at)",
    """CREATE TABLE IF NOT EXISTS so_guardian (
        child_id TEXT NOT NULL,
        other_id TEXT NOT NULL,
        action TEXT NOT NULL DEFAULT 'request',
        created_at TEXT NOT NULL,
        PRIMARY KEY (child_id, other_id)
    )""",
    """CREATE TABLE IF NOT EXISTS so_listing_saves (
        account_id TEXT NOT NULL,
        listing_id TEXT NOT NULL,
        created_at TEXT NOT NULL,
        PRIMARY KEY (account_id, listing_id)
    )""",
    # photos uploaded from a device (photos.py): the file is on disk under the media folder, never in here
    """CREATE TABLE IF NOT EXISTS so_media (
        id TEXT PRIMARY KEY,
        owner_id TEXT NOT NULL,
        home TEXT NOT NULL DEFAULT '',
        mime TEXT NOT NULL,
        size INTEGER NOT NULL DEFAULT 0,
        width INTEGER NOT NULL DEFAULT 0,
        height INTEGER NOT NULL DEFAULT 0,
        path TEXT NOT NULL,
        created_at TEXT NOT NULL
    )""",
    "CREATE INDEX IF NOT EXISTS so_media_owner ON so_media (owner_id, created_at)",
    # a poll on a post: 2 to 4 options, one vote each, open until closes_at
    """CREATE TABLE IF NOT EXISTS so_polls (
        post_id TEXT PRIMARY KEY,
        options TEXT NOT NULL DEFAULT '[]',
        closes_at TEXT NOT NULL,
        notified_at TEXT
    )""",
    """CREATE TABLE IF NOT EXISTS so_poll_votes (
        post_id TEXT NOT NULL,
        account_id TEXT NOT NULL,
        choice INTEGER NOT NULL,
        created_at TEXT NOT NULL,
        PRIMARY KEY (post_id, account_id)
    )""",
    """CREATE TABLE IF NOT EXISTS so_comment_likes (
        comment_id TEXT NOT NULL,
        account_id TEXT NOT NULL,
        created_at TEXT NOT NULL,
        PRIMARY KEY (comment_id, account_id)
    )""",
    # the close friends list: a smaller audience for posts and stories
    """CREATE TABLE IF NOT EXISTS so_close_friends (
        owner_id TEXT NOT NULL,
        friend_id TEXT NOT NULL,
        created_at TEXT NOT NULL,
        PRIMARY KEY (owner_id, friend_id)
    )""",
    # saved posts, each in one of the person's named collections ('' is "All saved")
    """CREATE TABLE IF NOT EXISTS so_collections (
        id TEXT PRIMARY KEY,
        owner_id TEXT NOT NULL,
        name TEXT NOT NULL,
        created_at TEXT NOT NULL
    )""",
    """CREATE TABLE IF NOT EXISTS so_saves (
        account_id TEXT NOT NULL,
        post_id TEXT NOT NULL,
        collection_id TEXT NOT NULL DEFAULT '',
        created_at TEXT NOT NULL,
        PRIMARY KEY (account_id, post_id)
    )""",
    # story highlights keep chosen stories on the profile after their day is over
    """CREATE TABLE IF NOT EXISTS so_highlights (
        id TEXT PRIMARY KEY,
        owner_id TEXT NOT NULL,
        title TEXT NOT NULL,
        created_at TEXT NOT NULL
    )""",
    """CREATE TABLE IF NOT EXISTS so_highlight_items (
        highlight_id TEXT NOT NULL,
        story_id TEXT NOT NULL,
        added_at TEXT NOT NULL,
        PRIMARY KEY (highlight_id, story_id)
    )""",
    """CREATE TABLE IF NOT EXISTS so_story_reactions (
        story_id TEXT NOT NULL,
        account_id TEXT NOT NULL,
        emoji TEXT NOT NULL,
        created_at TEXT NOT NULL,
        PRIMARY KEY (story_id, account_id)
    )""",
    # writing the automatic check (moderation.py) kept back: hidden until a moderator releases or removes it
    """CREATE TABLE IF NOT EXISTS so_held (
        target_type TEXT NOT NULL,
        target_id TEXT NOT NULL,
        author_id TEXT NOT NULL,
        report_id TEXT NOT NULL DEFAULT '',
        reasons TEXT NOT NULL DEFAULT '',
        homes TEXT NOT NULL DEFAULT '',
        created_at TEXT NOT NULL,
        PRIMARY KEY (target_type, target_id)
    )""",
    # what a moderator sees with a report: the chat and the profiles as they were when it was filed (only
    # moderators read it; purged EVIDENCE_DAYS after the report is closed)
    """CREATE TABLE IF NOT EXISTS so_evidence (
        id TEXT PRIMARY KEY,
        report_id TEXT NOT NULL,
        kind TEXT NOT NULL,
        data TEXT NOT NULL DEFAULT '{}',
        created_at TEXT NOT NULL,
        purge_after TEXT NOT NULL,
        legal_hold TEXT NOT NULL DEFAULT ''
    )""",
    "CREATE INDEX IF NOT EXISTS so_evidence_report ON so_evidence (report_id)",
    # the people a guardian approved to befriend and message their child or teen
    """CREATE TABLE IF NOT EXISTS so_approved_contacts (
        young_id TEXT NOT NULL,
        contact_id TEXT NOT NULL,
        approved_by TEXT NOT NULL,
        created_at TEXT NOT NULL,
        PRIMARY KEY (young_id, contact_id)
    )""",
)
RETENTION_DAYS = 90  # ended Dating chats and report evidence, for moderators only

# Columns added after a table first shipped: (table, column, definition). db.connect adds the missing ones.
COLUMNS = (
    ("so_posts", "edited_at", "TEXT"),
    ("so_chats", "context", "TEXT NOT NULL DEFAULT ''"),
    ("so_chats", "listing_id", "TEXT NOT NULL DEFAULT ''"),
    # a listing is the seller's own unless they sell it for the business they run (owner, admin, coach)
    ("so_listings", "for_business", "INTEGER NOT NULL DEFAULT 0"),
    ("so_listings", "business_link", "TEXT NOT NULL DEFAULT ''"),
    # posts: a feeling or activity, a place, a photo filter, alt text per photo, pinned to the profile
    ("so_posts", "feeling", "TEXT NOT NULL DEFAULT ''"),
    ("so_posts", "place", "TEXT NOT NULL DEFAULT ''"),
    ("so_posts", "filter", "TEXT NOT NULL DEFAULT ''"),
    ("so_posts", "alts", "TEXT NOT NULL DEFAULT '[]'"),
    ("so_posts", "pinned_at", "TEXT"),
    # one level of replies under a comment
    ("so_comments", "parent_id", "TEXT NOT NULL DEFAULT ''"),
    # a cover photo and links in the bio
    ("so_accounts", "cover", "TEXT NOT NULL DEFAULT ''"),
    ("so_accounts", "links", "TEXT NOT NULL DEFAULT '[]'"),
    # a story for close friends only
    ("so_stories", "audience", "TEXT NOT NULL DEFAULT 'friends'"),
    # a picture for a group or a page
    ("so_groups", "photo", "TEXT NOT NULL DEFAULT ''"),
    ("so_pages", "photo", "TEXT NOT NULL DEFAULT ''"),
    # evidence of an open report about a person who was erased: kept under a legal hold until the report closes
    # (service.erase_account, service.purge_kept)
    ("so_evidence", "legal_hold", "TEXT NOT NULL DEFAULT ''"),
    ("so_dating_ended", "legal_hold", "TEXT NOT NULL DEFAULT ''"),
)


def migrate(connection: sqlite3.Connection) -> None:
    """Add the columns of :data:`COLUMNS` that an older database does not have yet."""
    for table, column, definition in COLUMNS:
        have = {r[1] for r in connection.execute(f"PRAGMA table_info({table})")}  # noqa: S608 - fixed names
        if have and column not in have:  # (a table made later, such as Dating's, gets it when it is made)
            dbutil.add_column(connection, table, column, definition)
    # the key that signs photo addresses (photos.py), made here so a view never writes while it reads
    connection.execute(
        "INSERT OR IGNORE INTO so_settings (key, value) VALUES ('photo_key', ?)",
        (json.dumps(uuid.uuid4().hex + uuid.uuid4().hex),),
    )
    clear_external_pictures(connection)


EXTERNAL = (
    "lower({c}) LIKE 'http:%' OR lower({c}) LIKE 'https:%' OR {c} LIKE '//%' OR lower({c}) LIKE 'data:%'"
)


def clear_external_pictures(connection: sqlite3.Connection) -> None:
    """Once per database: profile, cover, group and page pictures that link to another website are cleared (an
    emoji takes the profile picture's place), and Dating photos keep only uploads. Such a link let its website see
    every viewer; Social now takes only uploaded photos. Posts keep their old links, which are never shown
    (photos.display)."""
    done = connection.execute(
        "SELECT 1 FROM so_settings WHERE key = 'external_pictures_cleared'"
    ).fetchone()
    if done:
        return
    for table, column, empty in (
        ("so_accounts", "avatar", "🙂"),
        ("so_accounts", "cover", ""),
        ("so_groups", "photo", ""),
        ("so_pages", "photo", ""),
    ):
        connection.execute(
            f"UPDATE {table} SET {column} = ? WHERE {EXTERNAL.format(c=column)}",  # noqa: S608 - fixed names
            (empty,),
        )
    has_dating = connection.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'so_dating_profiles'"
    ).fetchone()
    if has_dating:
        for found in connection.execute(
            "SELECT id, photos FROM so_dating_profiles WHERE photos LIKE '%:%'"
        ).fetchall():
            kept = [
                p
                for p in loads(found[1], [])
                if isinstance(p, str) and p.startswith("media:")
            ]
            connection.execute(
                "UPDATE so_dating_profiles SET photos = ? WHERE id = ?",
                (dumps(kept), found[0]),
            )
    connection.execute(
        "INSERT OR IGNORE INTO so_settings (key, value) VALUES ('external_pictures_cleared', ?)",
        (json.dumps(stamp()),),
    )


HEX = re.compile(r"[0-9a-f]{32}")


def new_id() -> str:
    return uuid.uuid4().hex


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def stamp(moment: datetime | None = None) -> str:
    """ISO 8601 UTC with microseconds: ``since`` polling never misses two changes in the same second."""
    return (
        (moment or utcnow()).astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")
    )


def parse(value: str) -> datetime:
    return datetime.fromisoformat(str(value).replace("Z", "+00:00"))


def later(hours: float = 0, moment: datetime | None = None) -> str:
    return stamp((moment or utcnow()) + timedelta(hours=hours))


def since_value(value: Any) -> str:
    """A ``since`` the browser sent back (one of our stamps), or "" for "everything"."""
    text = str(value or "").strip()
    if not text:
        return ""
    try:
        return stamp(parse(text))
    except ValueError:
        raise ValueError("since must be a time this server returned.") from None


def row(
    connection: sqlite3.Connection, sql: str, params: tuple[Any, ...] = ()
) -> dict[str, Any] | None:
    found = connection.execute(sql, params).fetchone()
    return dict(found) if found else None


def rows(
    connection: sqlite3.Connection, sql: str, params: tuple[Any, ...] = ()
) -> list[dict[str, Any]]:
    return [dict(found) for found in connection.execute(sql, params).fetchall()]


def loads(value: Any, default: Any) -> Any:
    try:
        return json.loads(value) if value else default
    except (TypeError, ValueError):
        return default


def dumps(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def setting(connection: sqlite3.Connection, key: str, default: Any) -> Any:
    found = row(connection, "SELECT value FROM so_settings WHERE key = ?", (key,))
    return loads(found["value"], default) if found else default


def set_setting(connection: sqlite3.Connection, key: str, value: Any) -> None:
    connection.execute(
        "INSERT INTO so_settings (key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value",
        (key, dumps(value)),
    )


def check_id(value: str, label: str = "item") -> str:
    if not HEX.fullmatch(str(value or "")):
        raise LookupError(f"That {label} was not found.")
    return str(value)
