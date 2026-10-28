"""News tables (``nw_*``) in the account's database.

Feeds and their articles are shared by the account (news is public); everything a person chooses is theirs:
settings, keywords, their own sources, hidden sources, saved and read articles, and their newsletter (its picks,
issues, subscribers and payments).
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


# v2: hidden sources and the newsletter a person can earn from.
SCHEMA_V2 = (
    """CREATE TABLE IF NOT EXISTS nw_muted (
        id TEXT PRIMARY KEY,
        profile_id TEXT NOT NULL,
        source TEXT NOT NULL,
        created_at TEXT NOT NULL,
        UNIQUE (profile_id, source)
    )""",
    """CREATE TABLE IF NOT EXISTS nw_letters (
        id TEXT PRIMARY KEY,
        owner_id TEXT NOT NULL UNIQUE,
        name TEXT NOT NULL,
        about TEXT NOT NULL DEFAULT '',
        price_cents INTEGER NOT NULL DEFAULT 0,
        currency TEXT NOT NULL DEFAULT '',
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL
    )""",
    """CREATE TABLE IF NOT EXISTS nw_picks (
        id TEXT PRIMARY KEY,
        letter_id TEXT NOT NULL,
        issue_id TEXT,
        title TEXT NOT NULL,
        link TEXT NOT NULL,
        source TEXT NOT NULL DEFAULT '',
        note TEXT NOT NULL DEFAULT '',
        position INTEGER NOT NULL DEFAULT 0,
        added_at TEXT NOT NULL
    )""",
    "CREATE INDEX IF NOT EXISTS nw_picks_letter ON nw_picks (letter_id, issue_id)",
    """CREATE TABLE IF NOT EXISTS nw_issues (
        id TEXT PRIMARY KEY,
        letter_id TEXT NOT NULL,
        number INTEGER NOT NULL,
        title TEXT NOT NULL,
        intro TEXT NOT NULL DEFAULT '',
        emailed INTEGER NOT NULL DEFAULT 0,
        published_at TEXT NOT NULL
    )""",
    "CREATE INDEX IF NOT EXISTS nw_issues_letter ON nw_issues (letter_id, number)",
    """CREATE TABLE IF NOT EXISTS nw_subscribers (
        id TEXT PRIMARY KEY,
        letter_id TEXT NOT NULL,
        name TEXT NOT NULL,
        email TEXT NOT NULL,
        status TEXT NOT NULL,
        reference TEXT NOT NULL,
        code_hash TEXT NOT NULL DEFAULT '',
        paid_until TEXT,
        created_at TEXT NOT NULL,
        cancelled_at TEXT
    )""",
    "CREATE INDEX IF NOT EXISTS nw_subscribers_letter ON nw_subscribers (letter_id, status)",
    """CREATE TABLE IF NOT EXISTS nw_payments (
        id TEXT PRIMARY KEY,
        subscriber_id TEXT NOT NULL,
        letter_id TEXT NOT NULL,
        amount_cents INTEGER NOT NULL,
        currency TEXT NOT NULL,
        paid_at TEXT NOT NULL,
        voided_at TEXT
    )""",
    "CREATE INDEX IF NOT EXISTS nw_payments_subscriber ON nw_payments (subscriber_id)",
)


def ensure(connection: sqlite3.Connection) -> None:
    life.ensure(connection)
    kit.ensure_core(
        connection
    )  # the revenue ledger, the outbox and public links (the newsletter)
    kit.ensure(connection, "news.v1", SCHEMA)
    kit.ensure(connection, "news.v2", SCHEMA_V2)
