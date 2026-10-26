"""Ads' tables, kept in the platform-wide Videos database (aira/suite/videos/db.py) because an ad bought by one
business is shown on another person's video and pays that person.

Every table starts with ``ad_``. Money is integer **micros** of a US dollar (1 cent = 10,000 micros): a CPM of $2 is
0.2 cents an impression, which cents cannot hold.

* ``ad_accounts`` - one per advertiser (an organization hosted, a local account on this computer): its prepaid balance.
* ``ad_ledger`` - every change to a balance that is not an impression: top-ups, payments recorded, refunds.
* ``ad_campaigns`` / ``ad_creatives`` - what is bought and what is shown (each creative is reviewed before it runs).
* ``ad_impressions`` - one row per ad served; ``counted`` ones were charged and credit the creator a share.
* ``ad_payouts`` - creators' requests to be paid what they earned, and the payments made.
* ``ad_months`` - months closed after the invalid-traffic review (their earnings became payable).
"""

from __future__ import annotations

APP = "ads"
MICROS = 1_000_000  # a dollar
CENT = 10_000

SCHEMA = (
    """CREATE TABLE IF NOT EXISTS ad_accounts (
        home TEXT PRIMARY KEY,
        name TEXT NOT NULL DEFAULT '',
        balance_micros INTEGER NOT NULL DEFAULT 0,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL
    )""",
    """CREATE TABLE IF NOT EXISTS ad_ledger (
        id TEXT PRIMARY KEY,
        home TEXT NOT NULL,
        kind TEXT NOT NULL,
        amount_micros INTEGER NOT NULL,
        reference TEXT NOT NULL DEFAULT '',
        note TEXT NOT NULL DEFAULT '',
        created_by TEXT NOT NULL DEFAULT '',
        created_at TEXT NOT NULL
    )""",
    "CREATE INDEX IF NOT EXISTS ad_ledger_home ON ad_ledger (home, created_at)",
    """CREATE TABLE IF NOT EXISTS ad_campaigns (
        id TEXT PRIMARY KEY,
        home TEXT NOT NULL,
        advertiser TEXT NOT NULL DEFAULT '',
        name TEXT NOT NULL,
        objective TEXT NOT NULL DEFAULT 'views',
        bid_type TEXT NOT NULL DEFAULT 'cpm',
        bid_micros INTEGER NOT NULL,
        total_budget_micros INTEGER NOT NULL,
        daily_budget_micros INTEGER NOT NULL,
        starts_on TEXT NOT NULL DEFAULT '',
        ends_on TEXT NOT NULL DEFAULT '',
        status TEXT NOT NULL DEFAULT 'draft',
        paused_reason TEXT NOT NULL DEFAULT '',
        categories TEXT NOT NULL DEFAULT '[]',
        languages TEXT NOT NULL DEFAULT '[]',
        countries TEXT NOT NULL DEFAULT '[]',
        family_safe INTEGER NOT NULL DEFAULT 0,
        allow_mature INTEGER NOT NULL DEFAULT 0,
        frequency_cap INTEGER NOT NULL DEFAULT 3,
        landing_url TEXT NOT NULL,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL,
        deleted_at TEXT
    )""",
    "CREATE INDEX IF NOT EXISTS ad_campaigns_home ON ad_campaigns (home)",
    "CREATE INDEX IF NOT EXISTS ad_campaigns_status ON ad_campaigns (status)",
    """CREATE TABLE IF NOT EXISTS ad_creatives (
        id TEXT PRIMARY KEY,
        campaign_id TEXT NOT NULL,
        home TEXT NOT NULL,
        kind TEXT NOT NULL,
        headline TEXT NOT NULL,
        body TEXT NOT NULL DEFAULT '',
        cta TEXT NOT NULL DEFAULT 'Learn more',
        file_id TEXT NOT NULL DEFAULT '',
        duration REAL NOT NULL DEFAULT 0,
        review_status TEXT NOT NULL DEFAULT 'pending',
        review_note TEXT NOT NULL DEFAULT '',
        reviewed_by TEXT NOT NULL DEFAULT '',
        reviewed_at TEXT,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL,
        archived_at TEXT
    )""",
    "CREATE INDEX IF NOT EXISTS ad_creatives_campaign ON ad_creatives (campaign_id)",
    "CREATE INDEX IF NOT EXISTS ad_creatives_review ON ad_creatives (review_status)",
    """CREATE TABLE IF NOT EXISTS ad_impressions (
        id TEXT PRIMARY KEY,
        campaign_id TEXT NOT NULL,
        creative_id TEXT NOT NULL,
        home TEXT NOT NULL,
        slot TEXT NOT NULL,
        video_id TEXT NOT NULL DEFAULT '',
        channel_id TEXT NOT NULL DEFAULT '',
        viewer_key TEXT NOT NULL,
        viewer_home TEXT NOT NULL DEFAULT '',
        served_at TEXT NOT NULL,
        seen_at TEXT,
        clicked_at TEXT,
        click_counted INTEGER NOT NULL DEFAULT 0,
        status TEXT NOT NULL DEFAULT 'served',
        invalid_reason TEXT NOT NULL DEFAULT '',
        cost_micros INTEGER NOT NULL DEFAULT 0,
        creator_micros INTEGER NOT NULL DEFAULT 0,
        earning_status TEXT NOT NULL DEFAULT 'none',
        day TEXT NOT NULL,
        month TEXT NOT NULL
    )""",
    "CREATE INDEX IF NOT EXISTS ad_impressions_campaign ON ad_impressions (campaign_id, day)",
    "CREATE INDEX IF NOT EXISTS ad_impressions_viewer ON ad_impressions (viewer_key, campaign_id, day)",
    "CREATE INDEX IF NOT EXISTS ad_impressions_channel ON ad_impressions (channel_id, month)",
    "CREATE INDEX IF NOT EXISTS ad_impressions_month ON ad_impressions (month, status)",
    """CREATE TABLE IF NOT EXISTS ad_payouts (
        id TEXT PRIMARY KEY,
        channel_id TEXT NOT NULL,
        home TEXT NOT NULL DEFAULT '',
        amount_micros INTEGER NOT NULL,
        details TEXT NOT NULL DEFAULT '',
        status TEXT NOT NULL DEFAULT 'requested',
        reference TEXT NOT NULL DEFAULT '',
        note TEXT NOT NULL DEFAULT '',
        requested_at TEXT NOT NULL,
        decided_at TEXT,
        decided_by TEXT NOT NULL DEFAULT ''
    )""",
    "CREATE INDEX IF NOT EXISTS ad_payouts_channel ON ad_payouts (channel_id)",
    """CREATE TABLE IF NOT EXISTS ad_months (
        month TEXT PRIMARY KEY,
        closed_at TEXT NOT NULL,
        closed_by TEXT NOT NULL DEFAULT '',
        invalid INTEGER NOT NULL DEFAULT 0,
        refunded_micros INTEGER NOT NULL DEFAULT 0,
        confirmed_micros INTEGER NOT NULL DEFAULT 0
    )""",
)
