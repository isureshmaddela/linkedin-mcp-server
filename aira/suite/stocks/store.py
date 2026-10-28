"""Stocks tables (prefixed ``stk_``: ``st_`` is Studio's). Prices and money are integer cents of the stock's currency; share quantities
are decimals (practice trades may buy part of a share)."""

from __future__ import annotations

import sqlite3

from aira.suite import kit, life

APP = "stocks"

SCHEMA = (
    # the market cache (shared by everyone in the account: prices are not personal)
    """CREATE TABLE IF NOT EXISTS stk_market (
        symbol TEXT PRIMARY KEY,
        meta TEXT NOT NULL DEFAULT '{}',
        history TEXT NOT NULL DEFAULT '[]',
        history_on TEXT NOT NULL DEFAULT '',
        quote_ts REAL NOT NULL DEFAULT 0,
        error TEXT NOT NULL DEFAULT '',
        updated_at TEXT NOT NULL
    )""",
    # watchlists
    """CREATE TABLE IF NOT EXISTS stk_watchlists (
        id TEXT PRIMARY KEY,
        owner_id TEXT NOT NULL,
        name TEXT NOT NULL,
        position INTEGER NOT NULL DEFAULT 0,
        created_at TEXT NOT NULL
    )""",
    "CREATE INDEX IF NOT EXISTS stk_watchlists_owner ON stk_watchlists (owner_id, position)",
    """CREATE TABLE IF NOT EXISTS stk_watch_items (
        id TEXT PRIMARY KEY,
        list_id TEXT NOT NULL,
        owner_id TEXT NOT NULL,
        symbol TEXT NOT NULL,
        note TEXT NOT NULL DEFAULT '',
        added_at TEXT NOT NULL,
        UNIQUE (list_id, symbol)
    )""",
    "CREATE INDEX IF NOT EXISTS stk_watch_items_owner ON stk_watch_items (owner_id, symbol)",
    # price alerts
    """CREATE TABLE IF NOT EXISTS stk_alerts (
        id TEXT PRIMARY KEY,
        owner_id TEXT NOT NULL,
        symbol TEXT NOT NULL,
        kind TEXT NOT NULL,
        price_cents INTEGER NOT NULL DEFAULT 0,
        move_pct REAL NOT NULL DEFAULT 0,
        note TEXT NOT NULL DEFAULT '',
        active INTEGER NOT NULL DEFAULT 1,
        triggered_at TEXT,
        triggered_on TEXT NOT NULL DEFAULT '',
        triggered_price_cents INTEGER NOT NULL DEFAULT 0,
        triggered_change_pct REAL NOT NULL DEFAULT 0,
        seen_at TEXT,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL
    )""",
    "CREATE INDEX IF NOT EXISTS stk_alerts_owner ON stk_alerts (owner_id, active)",
    "CREATE INDEX IF NOT EXISTS stk_alerts_active ON stk_alerts (active, symbol)",
    # portfolios: a practice account (virtual cash) and real holdings, one of each per currency
    """CREATE TABLE IF NOT EXISTS stk_accounts (
        id TEXT PRIMARY KEY,
        owner_id TEXT NOT NULL,
        kind TEXT NOT NULL,
        currency TEXT NOT NULL,
        name TEXT NOT NULL,
        start_cash_cents INTEGER NOT NULL DEFAULT 0,
        cash_cents INTEGER NOT NULL DEFAULT 0,
        started_at TEXT NOT NULL,
        created_at TEXT NOT NULL,
        UNIQUE (owner_id, kind, currency)
    )""",
    """CREATE TABLE IF NOT EXISTS stk_txns (
        id TEXT PRIMARY KEY,
        account_id TEXT NOT NULL,
        owner_id TEXT NOT NULL,
        symbol TEXT NOT NULL,
        kind TEXT NOT NULL,
        trade_date TEXT NOT NULL,
        quantity REAL NOT NULL DEFAULT 0,
        price_cents INTEGER NOT NULL DEFAULT 0,
        fees_cents INTEGER NOT NULL DEFAULT 0,
        amount_cents INTEGER NOT NULL DEFAULT 0,
        note TEXT NOT NULL DEFAULT '',
        created_at TEXT NOT NULL
    )""",
    "CREATE INDEX IF NOT EXISTS stk_txns_account ON stk_txns (account_id, trade_date, created_at)",
    "CREATE INDEX IF NOT EXISTS stk_txns_owner ON stk_txns (owner_id, symbol)",
    # a person's own predictions, scored when their date passes
    """CREATE TABLE IF NOT EXISTS stk_predictions (
        id TEXT PRIMARY KEY,
        owner_id TEXT NOT NULL,
        symbol TEXT NOT NULL,
        currency TEXT NOT NULL DEFAULT 'USD',
        direction TEXT NOT NULL,
        made_on TEXT NOT NULL,
        target_date TEXT NOT NULL,
        base_cents INTEGER NOT NULL,
        target_cents INTEGER NOT NULL DEFAULT 0,
        model_cents INTEGER NOT NULL DEFAULT 0,
        note TEXT NOT NULL DEFAULT '',
        status TEXT NOT NULL DEFAULT 'open',
        actual_cents INTEGER NOT NULL DEFAULT 0,
        hit INTEGER,
        model_hit INTEGER,
        error_pct REAL,
        scored_at TEXT,
        created_at TEXT NOT NULL
    )""",
    "CREATE INDEX IF NOT EXISTS stk_predictions_owner ON stk_predictions (owner_id, status)",
    # earning: an analyst's profile, published ideas, subscribers and their payments
    """CREATE TABLE IF NOT EXISTS stk_analysts (
        owner_id TEXT PRIMARY KEY,
        display_name TEXT NOT NULL,
        bio TEXT NOT NULL DEFAULT '',
        price_cents INTEGER NOT NULL DEFAULT 0,
        currency TEXT NOT NULL DEFAULT 'USD',
        payment_link TEXT NOT NULL DEFAULT '',
        payment_instructions TEXT NOT NULL DEFAULT '',
        accepting INTEGER NOT NULL DEFAULT 1,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL
    )""",
    """CREATE TABLE IF NOT EXISTS stk_ideas (
        id TEXT PRIMARY KEY,
        owner_id TEXT NOT NULL,
        symbol TEXT NOT NULL,
        name TEXT NOT NULL DEFAULT '',
        currency TEXT NOT NULL DEFAULT 'USD',
        side TEXT NOT NULL,
        entry_cents INTEGER NOT NULL,
        target_cents INTEGER NOT NULL,
        stop_cents INTEGER NOT NULL,
        horizon_days INTEGER NOT NULL,
        opened_on TEXT NOT NULL,
        expires_on TEXT NOT NULL,
        reasoning TEXT NOT NULL DEFAULT '',
        access TEXT NOT NULL DEFAULT 'free',
        status TEXT NOT NULL DEFAULT 'open',
        last_cents INTEGER NOT NULL DEFAULT 0,
        close_cents INTEGER NOT NULL DEFAULT 0,
        closed_on TEXT NOT NULL DEFAULT '',
        return_bp INTEGER NOT NULL DEFAULT 0,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL
    )""",
    "CREATE INDEX IF NOT EXISTS stk_ideas_owner ON stk_ideas (owner_id, status)",
    """CREATE TABLE IF NOT EXISTS stk_subscribers (
        id TEXT PRIMARY KEY,
        analyst_id TEXT NOT NULL,
        name TEXT NOT NULL,
        email TEXT NOT NULL,
        note TEXT NOT NULL DEFAULT '',
        reference TEXT NOT NULL,
        status TEXT NOT NULL DEFAULT 'pending',
        code_hash TEXT NOT NULL DEFAULT '',
        paid_until TEXT NOT NULL DEFAULT '',
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL,
        UNIQUE (analyst_id, email)
    )""",
    "CREATE INDEX IF NOT EXISTS stk_subscribers_analyst ON stk_subscribers (analyst_id, status)",
    """CREATE TABLE IF NOT EXISTS stk_payments (
        id TEXT PRIMARY KEY,
        subscriber_id TEXT NOT NULL,
        analyst_id TEXT NOT NULL,
        amount_cents INTEGER NOT NULL,
        currency TEXT NOT NULL,
        months INTEGER NOT NULL,
        paid_on TEXT NOT NULL,
        note TEXT NOT NULL DEFAULT '',
        created_at TEXT NOT NULL,
        voided_at TEXT
    )""",
    "CREATE INDEX IF NOT EXISTS stk_payments_subscriber ON stk_payments (subscriber_id, paid_on)",
    "CREATE INDEX IF NOT EXISTS stk_payments_analyst ON stk_payments (analyst_id, paid_on)",
)


def ensure(connection: sqlite3.Connection) -> None:
    kit.ensure_core(connection)
    life.ensure(connection)
    kit.ensure(connection, "stocks.v2", SCHEMA)
