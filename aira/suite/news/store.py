"""News tables (``nw_*``) in the account's database.

Feeds and their articles are shared by the account (news is public); everything a person chooses is theirs:
settings, keywords, their own sources, saved and read articles.
"""

from __future__ import annotations

import sqlite3

from aira.suite import kit, life

APP = "news"

SCHEMA = (
    """CREATE TABLE IF NOT EXISTS nw_feeds (
        id TEXT PRIMARY KEY,
        key TEXT NOT NULL UNIQUE,
        kind TEXT NOT NULL,
        url TEXT NOT NULL,
        title TEXT NOT NULL DEFAULT '',
        lang TEXT NOT NULL DEFAULT '',
        region TEXT NOT NULL DEFAULT '',
        last_fetched TEXT,
        last_ok TEXT,
        last_error TEXT NOT NULL DEFAULT '',
        created_at TEXT NOT NULL
    )""",
    """CREATE TABLE IF NOT EXISTS nw_articles (
        id TEXT PRIMARY KEY,
        feed_id TEXT NOT NULL,
        link TEXT NOT NULL,
        title TEXT NOT NULL,
        summary TEXT NOT NULL DEFAULT '',
        source TEXT NOT NULL DEFAULT '',
        image TEXT NOT NULL DEFAULT '',
        published_at TEXT NOT NULL DEFAULT '',
        fetched_at TEXT NOT NULL,
        UNIQUE (feed_id, link)
    )""",
    "CREATE INDEX IF NOT EXISTS nw_articles_feed ON nw_articles (feed_id, fetched_at)",
    """CREATE TABLE IF NOT EXISTS nw_settings (
        profile_id TEXT PRIMARY KEY,
        lang TEXT NOT NULL,
        region TEXT NOT NULL,
        topics TEXT NOT NULL,
        child_safe INTEGER,
        last_opened TEXT,
        updated_at TEXT NOT NULL
    )""",
    """CREATE TABLE IF NOT EXISTS nw_keywords (
        id TEXT PRIMARY KEY,
        profile_id TEXT NOT NULL,
        query TEXT NOT NULL,
        alert INTEGER NOT NULL DEFAULT 0,
        created_at TEXT NOT NULL
    )""",
    "CREATE INDEX IF NOT EXISTS nw_keywords_profile ON nw_keywords (profile_id)",
    """CREATE TABLE IF NOT EXISTS nw_sources (
        id TEXT PRIMARY KEY,
        profile_id TEXT NOT NULL,
        feed_id TEXT NOT NULL,
        title TEXT NOT NULL DEFAULT '',
        url TEXT NOT NULL,
        created_at TEXT NOT NULL
    )""",
    "CREATE INDEX IF NOT EXISTS nw_sources_profile ON nw_sources (profile_id)",
    """CREATE TABLE IF NOT EXISTS nw_saved (
        profile_id TEXT NOT NULL,
        article_id TEXT NOT NULL,
        saved_at TEXT NOT NULL,
        PRIMARY KEY (profile_id, article_id)
    )""",
    """CREATE TABLE IF NOT EXISTS nw_read (
        profile_id TEXT NOT NULL,
        article_id TEXT NOT NULL,
        read_at TEXT NOT NULL,
        PRIMARY KEY (profile_id, article_id)
    )""",
)


def ensure(connection: sqlite3.Connection) -> None:
    life.ensure(connection)
    kit.ensure(connection, "news.v1", SCHEMA)
