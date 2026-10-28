"""Ads' rules: what a business buys, which ad a viewer sees, what counts, who pays and who earns.

Everything here takes ``sc`` (a connection to the platform-wide Videos database) and money in micros of a dollar.

**Buying.** An advertiser (an organization hosted, an account on this computer) keeps a prepaid balance. A campaign
has an objective (views: pay per thousand impressions, CPM; clicks: pay per click, CPC), a total and a daily
budget, a schedule and targeting (video categories, the video's language, the viewer's country). Its creatives (a
skippable pre-roll video, a banner on the watch page, a sponsored reel) are reviewed before they run.

**Serving.** For each playback slot the eligible creatives are those approved, in an active campaign that is in its
schedule, matches the targeting, has balance and budget left, is within today's pace (the daily budget spread over
the day, an hour ahead), and has not reached its frequency cap for this viewer today. The highest effective bid
wins: a CPM bid as it is, a CPC bid times the campaign's click rate so far (smoothed: (clicks + 1) / (impressions +
50)) times 1,000. Ties are broken at random. Nobody under 13 (the ``child`` age group) is ever shown an ad; a video
made for children, or a teen viewer, gets only campaigns marked family-safe; a mature video only campaigns that
allow it; ads run only on videos of monetized channels.

**Counting.** An impression counts when the ad played or was on screen for 2 seconds (the browser says so, and the
server checks that 2 seconds have passed since it was served, within 30 minutes). It does not count, is not charged
and earns nothing when the viewer is in the advertiser's own account, or in the creator's own account (their own
videos), beyond the frequency cap, or past 60 counted impressions an hour from one viewer. A click goes through a
signed redirect and is charged (CPC) once per impression and once per viewer per campaign a day, only after the
impression counted.

**Money.** Each charge is drawn from the advertiser's balance at once; a campaign pauses when the balance cannot pay
for one more impression or click and resumes after a top-up. The creator of the video is credited a share of each
charge (55% unless the platform sets another): *estimated* at first, *confirmed* when the month is closed after
the invalid-traffic review, then *payable*. A payout request above the minimum ($10) is paid by the platform (hosted)
or the computer's owner (this computer) outside Aira and marked paid with a reference. A clawback turns counted
impressions invalid, refunds the advertisers and removes the earnings. The platform keeps the rest of each charge.
"""

from __future__ import annotations

import random
import sqlite3
from collections import defaultdict
from datetime import timedelta
from typing import Any

from aira.suite import kit
from aira.suite.ads.store import CENT, MICROS
from aira.suite.videos import media, service as videos, store
from aira.suite.videos.store import new_id, row, rows, stamp

OBJECTIVES = ("views", "clicks")
BID_TYPE = {"views": "cpm", "clicks": "cpc"}
SLOTS = ("preroll", "banner", "reel")
SLOT_LABELS = {
    "preroll": "Video before a video",
    "banner": "Banner on the watch page",
    "reel": "Sponsored reel",
}
CTAS = (
    "Learn more",
    "Shop now",
    "Sign up",
    "Book now",
    "Watch more",
    "Download",
    "Get a quote",
)
CAMPAIGN_STATUSES = ("draft", "active", "paused", "ended")
REVIEW_STATUSES = ("pending", "approved", "rejected")
SKIP_AFTER_SECONDS = 5
SEEN_SECONDS = 2.0
SEEN_WINDOW_MINUTES = 30
CLICK_WINDOW_HOURS = 2
HOURLY_CAP = 60
REVIEW_DAILY_CAP = (
    20  # month-end review: counted impressions one viewer may give one channel in a day
)
DEFAULT_SHARE = 55
DEFAULT_MIN_PAYOUT = 10 * MICROS
MIN_TOPUP_CENTS = 500
MAX_TOPUP_CENTS = 1_000_000
CPM_RANGE = (50 * CENT, 100 * MICROS)  # $0.50 to $100 per thousand
CPC_RANGE = (5 * CENT, 50 * MICROS)  # $0.05 to $50 a click
PREROLL_SECONDS = 180
SPONSORED_REEL_SECONDS = 60


def dollars(micros: int) -> str:
    value = micros / MICROS
    if 0 < abs(value) < 0.01:
        return f"${value:.4f}"
    return f"{'-' if value < 0 else ''}${abs(value):,.2f}"


# ---------------------------------------------------------------------------
# settings (the platform's: the superadmin hosted, the computer's owner here)
# ---------------------------------------------------------------------------


def settings(sc: sqlite3.Connection) -> dict[str, Any]:
    return {
        "revenue_share_percent": int(
            store.setting(sc, "ads.revenue_share", DEFAULT_SHARE)
        ),
        "min_payout_micros": int(
            store.setting(sc, "ads.min_payout_micros", DEFAULT_MIN_PAYOUT)
        ),
        "skip_after_seconds": SKIP_AFTER_SECONDS,
        "seen_seconds": SEEN_SECONDS,
    }


def save_settings(sc: sqlite3.Connection, raw: dict[str, Any]) -> dict[str, Any]:
    before = settings(sc)
    if "revenue_share_percent" in raw:
        share = kit.integer(
            raw["revenue_share_percent"],
            "the creators' share (percent)",
            minimum=0,
            maximum=90,
        )
        store.set_setting(sc, "ads.revenue_share", share)
    if "min_payout_cents" in raw:
        cents = kit.integer(
            raw["min_payout_cents"],
            "the minimum payout (cents)",
            minimum=100,
            maximum=100_000,
        )
        store.set_setting(sc, "ads.min_payout_micros", cents * CENT)
    sc.commit()
    return {"before": before, "after": settings(sc)}


# ---------------------------------------------------------------------------
# advertiser accounts and the balance
# ---------------------------------------------------------------------------


def account(sc: sqlite3.Connection, home: str, name: str = "") -> dict[str, Any]:
    found = row(sc, "SELECT * FROM ad_accounts WHERE home = ?", (home,))
    if found:
        if name and name != found["name"]:
            sc.execute(
                "UPDATE ad_accounts SET name = ?, updated_at = ? WHERE home = ?",
                (name[:120], stamp(), home),
            )
            sc.commit()
            found["name"] = name[:120]
        return found
    sc.execute(
        "INSERT OR IGNORE INTO ad_accounts (home, name, balance_micros, created_at, updated_at) VALUES (?, ?, 0, ?, ?)",
        (home, name[:120], stamp(), stamp()),
    )
    sc.commit()
    return row(sc, "SELECT * FROM ad_accounts WHERE home = ?", (home,)) or {}


def ledger(sc: sqlite3.Connection, home: str, limit: int = 100) -> list[dict[str, Any]]:
    return rows(
        sc,
        "SELECT * FROM ad_ledger WHERE home = ? ORDER BY created_at DESC LIMIT ?",
        (home, limit),
    )


def credit(
    sc: sqlite3.Connection,
    home: str,
    amount_micros: int,
    *,
    kind: str,
    reference: str = "",
    note: str = "",
    by: str = "",
) -> dict[str, Any]:
    """Add money to an advertiser's balance (a top-up billed, a payment recorded, a refund) and resume the campaigns
    that paused for want of it."""
    if amount_micros <= 0:
        raise ValueError("The amount must be more than zero.")
    account(sc, home)
    moment = stamp()
    sc.execute(
        "INSERT INTO ad_ledger (id, home, kind, amount_micros, reference, note, created_by, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (
            new_id(),
            home,
            kind,
            amount_micros,
            reference[:200],
            note[:300],
            by[:120],
            moment,
        ),
    )
    sc.execute(
        "UPDATE ad_accounts SET balance_micros = balance_micros + ?, updated_at = ? WHERE home = ?",
        (amount_micros, moment, home),
    )
    sc.execute(
        "UPDATE ad_campaigns SET status = 'active', paused_reason = '', updated_at = ? WHERE home = ? AND status = 'paused' AND paused_reason = 'no_balance'",
        (moment, home),
    )
    sc.commit()
    return account(sc, home)


def record_payment(
    sc: sqlite3.Connection, home: str, raw: dict[str, Any], by: str
) -> dict[str, Any]:
    """This computer: the owner records money an advertiser paid them outside Aira."""
    cents = kit.integer(
        raw.get("amount_cents"),
        "the amount (cents)",
        minimum=1,
        maximum=MAX_TOPUP_CENTS,
    )
    reference = kit.text(
        raw.get("reference"),
        "a reference (receipt or transfer number)",
        max_len=120,
        required=True,
    )
    note = kit.text(raw.get("note"), "a note", max_len=300)
    return credit(
        sc, home, cents * CENT, kind="payment", reference=reference, note=note, by=by
    )


def advertisers(sc: sqlite3.Connection) -> list[dict[str, Any]]:
    return rows(sc, "SELECT * FROM ad_accounts ORDER BY updated_at DESC LIMIT 500")


# ---------------------------------------------------------------------------
# campaigns and creatives
# ---------------------------------------------------------------------------


def _codes(
    value: Any, name: str, pattern: Any, *, upper: bool, options: tuple[str, ...] = ()
) -> list[str]:
    items = (
        value
        if isinstance(value, list)
        else str(value or "").replace(";", ",").split(",")
    )
    out: list[str] = []
    for item in items:
        code = str(item or "").strip()
        code = code.upper() if upper else code.lower()
        if not code:
            continue
        if options and code not in options:
            raise ValueError(
                f"{name[:1].upper()}{name[1:]} must be from: {', '.join(options)}."
            )
        if not options and not pattern.fullmatch(code):
            raise ValueError(
                f"Use two-letter codes for {name} (like {'IN, US' if upper else 'en, te'})."
            )
        if code not in out:
            out.append(code)
    return out[:30]


def _campaign_fields(
    raw: dict[str, Any], current: dict[str, Any] | None = None
) -> dict[str, Any]:
    stored = dict(current or {})
    for key in ("categories", "languages", "countries"):
        if isinstance(stored.get(key), str):
            stored[key] = store.loads(
                stored[key], []
            )  # the database keeps JSON lists, not "a, b"
    merged = {**stored, **raw}
    objective = kit.choice(
        merged.get("objective"), "the objective", OBJECTIVES, default="views"
    )
    bid_type = BID_TYPE[objective]
    low, high = CPM_RANGE if bid_type == "cpm" else CPC_RANGE
    bid = _micros(merged, "bid", "the bid")
    if not low <= bid <= high:
        raise ValueError(
            f"{'A CPM bid (per thousand impressions)' if bid_type == 'cpm' else 'A CPC bid (per click)'} is between {dollars(low)} and {dollars(high)}."
        )
    total = _micros(merged, "total_budget", "the total budget")
    daily = _micros(merged, "daily_budget", "the daily budget")
    if total < 5 * MICROS:
        raise ValueError("The total budget is at least $5.00.")
    if daily < 1 * MICROS:
        raise ValueError("The daily budget is at least $1.00.")
    if daily > total:
        raise ValueError("The daily budget cannot be more than the total budget.")
    starts = kit.day(merged.get("starts_on"), "the start date")
    ends = kit.day(merged.get("ends_on"), "the end date")
    if starts and ends and ends < starts:
        raise ValueError("The campaign must end after it starts.")
    if ends and ends < store.day() and ends != stored.get("ends_on"):
        raise ValueError(
            "The end date has passed. Choose a later one, or leave it empty."
        )
    landing = kit.url(merged.get("landing_url"), "the landing page", required=True)
    if not landing.lower().startswith("https://"):
        raise ValueError("The landing page must start with https://.")
    return {
        "name": kit.text(
            merged.get("name"), "a campaign name", max_len=80, required=True
        ),
        "objective": objective,
        "bid_type": bid_type,
        "bid_micros": bid,
        "total_budget_micros": total,
        "daily_budget_micros": daily,
        "starts_on": starts,
        "ends_on": ends,
        "categories": store.dumps(
            _codes(
                merged.get("categories"),
                "the categories",
                None,
                upper=False,
                options=videos.CATEGORIES,
            )
        ),
        "languages": store.dumps(
            _codes(merged.get("languages"), "languages", videos.LANGUAGE, upper=False)
        ),
        "countries": store.dumps(
            _codes(merged.get("countries"), "countries", videos.COUNTRY, upper=True)
        ),
        "family_safe": 1 if kit.flag(merged.get("family_safe")) else 0,
        "allow_mature": 1 if kit.flag(merged.get("allow_mature")) else 0,
        "frequency_cap": kit.integer(
            merged.get("frequency_cap"),
            "the frequency cap",
            default=3,
            minimum=1,
            maximum=20,
        ),
        "landing_url": landing,
    }


def _micros(merged: dict[str, Any], key: str, name: str) -> int:
    """A money field: ``<key>_cents`` from the browser, or ``<key>_micros`` (a value read back from the database)."""
    if merged.get(f"{key}_cents") not in (None, ""):
        cents = kit.integer(
            merged.get(f"{key}_cents"),
            f"{name} (cents)",
            minimum=0,
            maximum=100_000_000,
        )
        return cents * CENT
    if merged.get(f"{key}_micros") not in (None, ""):
        return kit.integer(merged.get(f"{key}_micros"), name, minimum=0, maximum=10**15)
    raise ValueError(f"Enter {name}.")


def _campaign_row(
    sc: sqlite3.Connection, home: str, campaign_id: str
) -> dict[str, Any]:
    found = row(
        sc,
        "SELECT * FROM ad_campaigns WHERE id = ? AND home = ? AND deleted_at IS NULL",
        (store.check_id(campaign_id, "campaign"), home),
    )
    if not found:
        raise LookupError("That campaign was not found.")
    return found


def unit_cost(campaign: dict[str, Any]) -> int:
    """What one more impression (CPM) or click (CPC) would cost."""
    if campaign["bid_type"] == "cpm":
        return max(1, (int(campaign["bid_micros"]) + 500) // 1000)
    return int(campaign["bid_micros"])


def _spend(sc: sqlite3.Connection, campaign_id: str, day: str = "") -> int:
    if day:
        value = store.scalar(
            sc,
            "SELECT COALESCE(SUM(cost_micros), 0) FROM ad_impressions WHERE campaign_id = ? AND day = ? AND status = 'counted'",
            (campaign_id, day),
        )
    else:
        value = store.scalar(
            sc,
            "SELECT COALESCE(SUM(cost_micros), 0) FROM ad_impressions WHERE campaign_id = ? AND status = 'counted'",
            (campaign_id,),
        )
    return int(value or 0)


def _stats(
    sc: sqlite3.Connection, where: str, params: tuple[Any, ...]
) -> dict[str, Any]:
    found = row(
        sc,
        f"""SELECT COUNT(*) AS impressions, COALESCE(SUM(click_counted), 0) AS clicks,
        COALESCE(SUM(cost_micros), 0) AS spend_micros FROM ad_impressions WHERE status = 'counted' AND {where}""",  # noqa: S608 - fragments are ours
        params,
    ) or {"impressions": 0, "clicks": 0, "spend_micros": 0}
    impressions = int(found["impressions"] or 0)
    clicks = int(found["clicks"] or 0)
    spend = int(found["spend_micros"] or 0)
    return {
        "impressions": impressions,
        "clicks": clicks,
        "spend_micros": spend,
        "ctr_percent": round(clicks * 100 / impressions, 2) if impressions else 0.0,
        "ecpm_micros": spend * 1000 // impressions if impressions else 0,
    }


def campaign_view(
    sc: sqlite3.Connection, found: dict[str, Any], *, full: bool = False
) -> dict[str, Any]:
    out = {
        **{
            k: v
            for k, v in found.items()
            if k not in ("categories", "languages", "countries", "deleted_at")
        },
        "categories": store.loads(found["categories"], []),
        "languages": store.loads(found["languages"], []),
        "countries": store.loads(found["countries"], []),
        "family_safe": bool(found["family_safe"]),
        "allow_mature": bool(found["allow_mature"]),
        "utm": options(sc, found["id"])["utm"],
        "restartable": found["status"] == "ended"
        and found["paused_reason"] in RESTARTABLE,
        "ready_to_restart": found["status"] == "ended"
        and found["paused_reason"] in RESTARTABLE
        and _spend(sc, found["id"]) + unit_cost(found) <= found["total_budget_micros"]
        and (not found["ends_on"] or found["ends_on"] >= store.day()),
        "sales": _sales_totals(
            sc, "campaign_id = ?", (found["id"],), _spend(sc, found["id"])
        ),
        "spent_micros": _spend(sc, found["id"]),
        "spent_today_micros": _spend(sc, found["id"], store.day()),
        "stats": _stats(sc, "campaign_id = ?", (found["id"],)),
        "creatives_count": int(
            store.scalar(
                sc,
                "SELECT COUNT(*) FROM ad_creatives WHERE campaign_id = ? AND archived_at IS NULL",
                (found["id"],),
            )
            or 0
        ),
        "approved_count": int(
            store.scalar(
                sc,
                "SELECT COUNT(*) FROM ad_creatives WHERE campaign_id = ? AND archived_at IS NULL AND review_status = 'approved'",
                (found["id"],),
            )
            or 0
        ),
    }
    if full:
        out["creatives"] = [
            creative_view(sc, item)
            for item in rows(
                sc,
                "SELECT * FROM ad_creatives WHERE campaign_id = ? AND archived_at IS NULL ORDER BY created_at",
                (found["id"],),
            )
        ]
    return out


def campaigns(sc: sqlite3.Connection, home: str) -> list[dict[str, Any]]:
    return [
        campaign_view(sc, item)
        for item in rows(
            sc,
            "SELECT * FROM ad_campaigns WHERE home = ? AND deleted_at IS NULL ORDER BY created_at DESC LIMIT 200",
            (home,),
        )
    ]


def creatives_index(sc: sqlite3.Connection, home: str) -> list[dict[str, Any]]:
    """Every ad of the advertiser's campaigns, without media or numbers: what the search box looks through."""
    return [
        {**item, "kind_label": SLOT_LABELS.get(item["kind"], item["kind"])}
        for item in rows(
            sc,
            """SELECT cr.id, cr.campaign_id, c.name AS campaign_name, c.status AS campaign_status, cr.kind,
                      cr.headline, cr.body, cr.cta, cr.review_status
               FROM ad_creatives cr JOIN ad_campaigns c ON c.id = cr.campaign_id
               WHERE cr.home = ? AND c.home = ? AND cr.archived_at IS NULL AND c.deleted_at IS NULL
               ORDER BY cr.created_at DESC LIMIT 2000""",
            (home, home),
        )
    ]


def campaign(sc: sqlite3.Connection, home: str, campaign_id: str) -> dict[str, Any]:
    return campaign_view(sc, _campaign_row(sc, home, campaign_id), full=True)


def create_campaign(
    sc: sqlite3.Connection, home: str, advertiser: str, raw: dict[str, Any]
) -> dict[str, Any]:
    values = _campaign_fields(raw)
    if (
        int(
            store.scalar(
                sc,
                "SELECT COUNT(*) FROM ad_campaigns WHERE home = ? AND deleted_at IS NULL",
                (home,),
            )
            or 0
        )
        >= 200
    ):
        raise ValueError("An advertiser can have at most 200 campaigns.")
    account(sc, home, advertiser)
    campaign_id = new_id()
    moment = stamp()
    sc.execute(
        f"""INSERT INTO ad_campaigns (id, home, advertiser, status, created_at, updated_at, {", ".join(values)})
        VALUES (?, ?, ?, 'draft', ?, ?, {", ".join("?" for _ in values)})""",  # noqa: S608 - column names are ours
        (campaign_id, home, advertiser[:120], moment, moment, *values.values()),
    )
    if "utm" in raw:
        set_options(sc, home, campaign_id, utm=kit.flag(raw.get("utm")))
    sc.commit()
    return campaign(sc, home, campaign_id)


RESTARTABLE = (
    "budget_spent",
    "schedule_over",
)  # ended by itself: raise the budget or move the end date, then start again


def _ended_for_good(found: dict[str, Any]) -> bool:
    return found["status"] == "ended" and found["paused_reason"] not in RESTARTABLE


def update_campaign(
    sc: sqlite3.Connection, home: str, campaign_id: str, raw: dict[str, Any]
) -> dict[str, Any]:
    found = _campaign_row(sc, home, campaign_id)
    if _ended_for_good(found):
        raise ValueError("This campaign has ended. Make a new one, or copy it.")
    values = _campaign_fields(raw, found)
    if values["landing_url"] != found["landing_url"]:
        # ads already served keep sending their clicks to the page that was reviewed when they were served
        sc.execute(
            "INSERT INTO ad_campaign_pages (id, campaign_id, home, landing_url, until_at) VALUES (?, ?, ?, ?, ?)",
            (new_id(), found["id"], home, found["landing_url"], stamp()),
        )
    store.update(sc, "ad_campaigns", found["id"], {**values, "updated_at": stamp()})
    if values["landing_url"] != found["landing_url"] or (
        values["family_safe"] and not found["family_safe"]
    ):
        # reviewers approved the ads for that page and that audience: a new page or a family-safe claim is checked again
        sc.execute(
            "UPDATE ad_creatives SET review_status = 'pending', review_note = '', reviewed_at = NULL, updated_at = ? "
            "WHERE campaign_id = ? AND archived_at IS NULL",
            (stamp(), found["id"]),
        )
    if "utm" in raw:
        set_options(sc, home, found["id"], utm=kit.flag(raw.get("utm")))
    fresh = _campaign_row(sc, home, found["id"])
    if (
        fresh["status"] in ("active", "paused")
        and _spend(sc, fresh["id"]) + unit_cost(fresh) > fresh["total_budget_micros"]
    ):
        store.update(
            sc,
            "ad_campaigns",
            fresh["id"],
            {"status": "ended", "paused_reason": "budget_spent", "updated_at": stamp()},
        )
    sc.commit()
    return campaign(sc, home, found["id"])


def set_status(
    sc: sqlite3.Connection, home: str, campaign_id: str, status: str
) -> dict[str, Any]:
    found = _campaign_row(sc, home, campaign_id)
    status = kit.choice(status, "the status", ("active", "paused", "ended"))
    if _ended_for_good(found):
        raise ValueError("This campaign has ended. Make a new one, or copy it.")
    if found["status"] == "ended" and status != "active":
        raise ValueError("This campaign has already stopped.")
    reason = ""
    if status == "active":
        if not int(
            store.scalar(
                sc,
                "SELECT COUNT(*) FROM ad_creatives WHERE campaign_id = ? AND archived_at IS NULL",
                (found["id"],),
            )
            or 0
        ):
            raise ValueError(
                "Add an ad (a video, a banner or a reel) before you start the campaign."
            )
        if _spend(sc, found["id"]) + unit_cost(found) > found["total_budget_micros"]:
            raise ValueError("The total budget is spent. Raise it to start again.")
        if found["ends_on"] and found["ends_on"] < store.day():
            raise ValueError("The end date has passed. Move it later to start again.")
        if int(account(sc, home).get("balance_micros") or 0) < unit_cost(found):
            status, reason = "paused", "no_balance"
    store.update(
        sc,
        "ad_campaigns",
        found["id"],
        {"status": status, "paused_reason": reason, "updated_at": stamp()},
    )
    sc.commit()
    return campaign(sc, home, found["id"])


def duplicate_campaign(
    sc: sqlite3.Connection, home: str, campaign_id: str
) -> dict[str, Any]:
    """A copy of a campaign as a draft: the same settings and the same ads (already reviewed for that page and
    audience, so they keep their review), with nothing spent."""
    found = _campaign_row(sc, home, campaign_id)
    raw = {
        **{key: found[key] for key in found if key not in ("ends_on", "starts_on")},
        "name": f"{found['name'][:72]} (copy)",
        "categories": store.loads(found["categories"], []),
        "languages": store.loads(found["languages"], []),
        "countries": store.loads(found["countries"], []),
    }
    today = store.day()
    if (
        found["ends_on"] >= today
    ):  # an end date already passed is dropped (a copy cannot end in the past)
        raw["ends_on"] = found["ends_on"]
    if (
        found["starts_on"] >= today
    ):  # a later start stays; one already passed means nothing for a new draft
        raw["starts_on"] = found["starts_on"]
    made = create_campaign(sc, home, found["advertiser"], raw)
    set_options(sc, home, made["id"], utm=options(sc, found["id"])["utm"])
    moment = stamp()
    for item in rows(
        sc,
        "SELECT * FROM ad_creatives WHERE campaign_id = ? AND archived_at IS NULL ORDER BY created_at",
        (found["id"],),
    ):
        sc.execute(
            """INSERT INTO ad_creatives (id, campaign_id, home, kind, headline, body, cta, file_id, duration, review_status,
            review_note, reviewed_by, reviewed_at, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                new_id(),
                made["id"],
                home,
                item["kind"],
                item["headline"],
                item["body"],
                item["cta"],
                item["file_id"],
                item["duration"],
                item["review_status"],
                item["review_note"],
                item["reviewed_by"],
                item["reviewed_at"],
                moment,
                moment,
            ),
        )
    sc.commit()
    return campaign(sc, home, made["id"])


def options(sc: sqlite3.Connection, campaign_id: str) -> dict[str, Any]:
    found = row(
        sc, "SELECT * FROM ad_campaign_options WHERE campaign_id = ?", (campaign_id,)
    )
    return {"utm": bool(found["utm"]) if found else True}


def set_options(
    sc: sqlite3.Connection, home: str, campaign_id: str, *, utm: bool
) -> None:
    sc.execute(
        """INSERT INTO ad_campaign_options (campaign_id, home, utm, updated_at) VALUES (?, ?, ?, ?)
        ON CONFLICT(campaign_id) DO UPDATE SET utm = excluded.utm, updated_at = excluded.updated_at""",
        (campaign_id, home, 1 if utm else 0, stamp()),
    )
    sc.commit()


def tracked_url(landing: str, campaign: dict[str, Any], slot: str) -> str:
    """The page with tracking tags (UTM) added, so the advertiser's own analytics and shop see where a visit came
    from. Tags the page already carries are kept as they are."""
    from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

    parts = urlsplit(landing)
    query = parse_qsl(parts.query, keep_blank_values=True)
    present = {key for key, _ in query}
    slug = "".join(ch if ch.isalnum() else "-" for ch in campaign["name"].lower())
    slug = "-".join(piece for piece in slug.split("-") if piece)[:60] or "campaign"
    for key, value in (
        ("utm_source", "aira"),
        ("utm_medium", "video_ad"),
        ("utm_campaign", slug),
        ("utm_content", slot),
    ):
        if key not in present:
            query.append((key, value))
    return urlunsplit(parts._replace(path=parts.path or "/", query=urlencode(query)))


def delete_campaign(sc: sqlite3.Connection, home: str, campaign_id: str) -> None:
    found = _campaign_row(sc, home, campaign_id)
    store.update(
        sc,
        "ad_campaigns",
        found["id"],
        {"status": "ended", "deleted_at": stamp(), "updated_at": stamp()},
    )
    sc.execute(
        "UPDATE ad_creatives SET archived_at = ? WHERE campaign_id = ? AND archived_at IS NULL",
        (stamp(), found["id"]),
    )
    # the sales recorded for it go too: they could no longer be seen or removed, but would stay in the totals
    sc.execute("DELETE FROM ad_sales WHERE campaign_id = ?", (found["id"],))
    sc.commit()


def owner_key(home: str) -> str:
    """Whose uploads an advertiser's media files are (kept apart from people's channels)."""
    return f"ad:{home}"


def creative_view(
    sc: sqlite3.Connection,
    found: dict[str, Any],
    *,
    media_prefix: str = "/api/ads/media",
) -> dict[str, Any]:
    file_row = (
        row(
            sc,
            "SELECT mime, width, height FROM vi_files WHERE id = ?",
            (found["file_id"],),
        )
        or {}
    )
    return {
        "id": found["id"],
        "campaign_id": found["campaign_id"],
        "kind": found["kind"],
        "kind_label": SLOT_LABELS.get(found["kind"], found["kind"]),
        "headline": found["headline"],
        "body": found["body"],
        "cta": found["cta"],
        "duration": found["duration"],
        "media_url": media.signed_url(sc, found["file_id"], media_prefix),
        "mime": file_row.get("mime", ""),
        "review_status": found["review_status"],
        "review_note": found["review_note"],
        "reviewed_at": found["reviewed_at"],
        "created_at": found["created_at"],
        "stats": _stats(sc, "creative_id = ?", (found["id"],)),
    }


def _creative_fields(
    sc: sqlite3.Connection,
    home: str,
    raw: dict[str, Any],
    current: dict[str, Any] | None = None,
) -> dict[str, Any]:
    merged = {**(current or {}), **raw}
    kind = kit.choice(merged.get("kind"), "the kind of ad", SLOTS)
    headline = kit.text(merged.get("headline"), "a headline", max_len=60, required=True)
    body = kit.text(merged.get("body"), "the ad's text", max_len=140)
    cta = str(merged.get("cta") or "Learn more").strip()
    if cta not in CTAS:
        raise ValueError(f"The button says one of: {', '.join(CTAS)}.")
    file_id = str(merged.get("file_id") or "")
    duration = 0.0
    if kind == "banner":
        media.owned_file(sc, file_id, owner_key(home), "image")
    else:
        found = media.owned_file(sc, file_id, owner_key(home), "video")
        duration = float(found["duration"] or 0) or float(merged.get("duration") or 0)
        limit = PREROLL_SECONDS if kind == "preroll" else SPONSORED_REEL_SECONDS
        if duration and duration > limit + 0.5:
            raise ValueError(f"This ad is at most {limit} seconds long.")
        if (
            kind == "reel"
            and found["width"]
            and found["height"]
            and found["height"] <= found["width"]
        ):
            raise ValueError("A sponsored reel is a tall (portrait) video.")
    return {
        "kind": kind,
        "headline": headline,
        "body": body,
        "cta": cta,
        "file_id": file_id,
        "duration": duration,
    }


def create_creative(
    sc: sqlite3.Connection, home: str, campaign_id: str, raw: dict[str, Any]
) -> dict[str, Any]:
    found = _campaign_row(sc, home, campaign_id)
    if (
        int(
            store.scalar(
                sc,
                "SELECT COUNT(*) FROM ad_creatives WHERE campaign_id = ? AND archived_at IS NULL",
                (found["id"],),
            )
            or 0
        )
        >= 20
    ):
        raise ValueError("A campaign has at most 20 ads.")
    values = _creative_fields(sc, home, raw)
    creative_id = new_id()
    moment = stamp()
    sc.execute(
        """INSERT INTO ad_creatives (id, campaign_id, home, kind, headline, body, cta, file_id, duration, review_status,
        created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'pending', ?, ?)""",
        (
            creative_id,
            found["id"],
            home,
            values["kind"],
            values["headline"],
            values["body"],
            values["cta"],
            values["file_id"],
            values["duration"],
            moment,
            moment,
        ),
    )
    sc.commit()
    return creative_view(
        sc, row(sc, "SELECT * FROM ad_creatives WHERE id = ?", (creative_id,)) or {}
    )


def _creative_row(
    sc: sqlite3.Connection, home: str, creative_id: str
) -> dict[str, Any]:
    found = row(
        sc,
        "SELECT * FROM ad_creatives WHERE id = ? AND home = ? AND archived_at IS NULL",
        (store.check_id(creative_id, "ad"), home),
    )
    if not found:
        raise LookupError("That ad was not found.")
    return found


def update_creative(
    sc: sqlite3.Connection, home: str, creative_id: str, raw: dict[str, Any]
) -> dict[str, Any]:
    found = _creative_row(sc, home, creative_id)
    values = _creative_fields(sc, home, raw, found)
    changed = any(
        values[key] != found[key]
        for key in ("kind", "headline", "body", "cta", "file_id")
    )
    if values["file_id"] != found["file_id"] and not store.scalar(
        sc,
        "SELECT COUNT(*) FROM ad_creatives WHERE file_id = ? AND id != ? AND archived_at IS NULL",
        (found["file_id"], found["id"]),
    ):
        media.delete_file(
            sc, found["file_id"]
        )  # a copied campaign's ads share the file: keep it while one uses it
    extra = (
        {"review_status": "pending", "review_note": "", "reviewed_at": None}
        if changed
        else {}
    )
    store.update(
        sc, "ad_creatives", found["id"], {**values, **extra, "updated_at": stamp()}
    )
    sc.commit()
    return creative_view(
        sc, row(sc, "SELECT * FROM ad_creatives WHERE id = ?", (found["id"],)) or {}
    )


def archive_creative(sc: sqlite3.Connection, home: str, creative_id: str) -> None:
    found = _creative_row(sc, home, creative_id)
    sc.execute(
        "UPDATE ad_creatives SET archived_at = ?, updated_at = ? WHERE id = ?",
        (stamp(), stamp(), found["id"]),
    )
    sc.commit()


# ---------------------------------------------------------------------------
# review (the platform's moderators hosted, the computer's owner here)
# ---------------------------------------------------------------------------


def review_queue(
    sc: sqlite3.Connection,
    status: str = "pending",
    *,
    media_prefix: str = "/api/ads/media",
) -> list[dict[str, Any]]:
    kit.choice(status, "the status", REVIEW_STATUSES)
    out = []
    for item in rows(
        sc,
        """SELECT cr.* FROM ad_creatives cr JOIN ad_campaigns c ON c.id = cr.campaign_id
        WHERE cr.review_status = ? AND cr.archived_at IS NULL AND c.deleted_at IS NULL ORDER BY cr.updated_at LIMIT 200""",
        (status,),
    ):
        found = (
            row(sc, "SELECT * FROM ad_campaigns WHERE id = ?", (item["campaign_id"],))
            or {}
        )
        out.append(
            {
                **creative_view(sc, item, media_prefix=media_prefix),
                "advertiser": found.get("advertiser", ""),
                "campaign": {
                    "id": found.get("id", ""),
                    "name": found.get("name", ""),
                    "landing_url": found.get("landing_url", ""),
                    "family_safe": bool(found.get("family_safe")),
                    "allow_mature": bool(found.get("allow_mature")),
                    "categories": store.loads(found.get("categories"), []),
                    "countries": store.loads(found.get("countries"), []),
                },
            }
        )
    return out


def review(
    sc: sqlite3.Connection, creative_id: str, decision: str, note: str, by: str
) -> dict[str, Any]:
    found = row(
        sc,
        "SELECT * FROM ad_creatives WHERE id = ? AND archived_at IS NULL",
        (store.check_id(creative_id, "ad"),),
    )
    if not found:
        raise LookupError("That ad was not found.")
    decision = kit.choice(decision, "the decision", ("approve", "reject"))
    note = kit.text(note, "a note for the advertiser", max_len=500, multiline=True)
    if decision == "reject" and not note:
        raise ValueError("Tell the advertiser why the ad was not approved.")
    status = "approved" if decision == "approve" else "rejected"
    sc.execute(
        "UPDATE ad_creatives SET review_status = ?, review_note = ?, reviewed_by = ?, reviewed_at = ? WHERE id = ?",
        (status, note, by[:120], stamp(), found["id"]),
    )
    sc.commit()
    return {"id": found["id"], "review_status": status}


# ---------------------------------------------------------------------------
# monetization (who may earn)
# ---------------------------------------------------------------------------


def eligibility(sc: sqlite3.Connection, ch: dict[str, Any]) -> dict[str, Any]:
    """The simple rules a channel meets before ads run on its videos."""
    public_videos = int(
        store.scalar(
            sc,
            "SELECT COUNT(*) FROM vi_videos WHERE channel_id = ? AND visibility = 'public' AND deleted_at IS NULL AND hidden_at IS NULL AND takedown_at IS NULL",
            (ch["id"],),
        )
        or 0
    )
    recent_hidden = int(
        store.scalar(
            sc,
            "SELECT COUNT(*) FROM vi_videos WHERE channel_id = ? AND hidden_at IS NOT NULL AND hidden_at > ?",
            (ch["id"], store.later(-24 * 30)),
        )
        or 0
    )
    checks = [
        {
            "id": "adult",
            "label": "You are an adult (18 or older)",
            "done": videos.adult(ch),
        },
        {
            "id": "terms",
            "label": "You accepted the creator terms",
            "done": bool(ch.get("terms_accepted_at")),
        },
        {
            "id": "video",
            "label": "At least one public video",
            "done": public_videos > 0,
        },
        {
            "id": "standing",
            "label": "Your channel is not suspended",
            "done": not ch.get("suspended_at"),
        },
        {
            "id": "copyright",
            "label": "No copyright strikes",
            "done": videos.strikes(sc, ch["id"]) == 0,
        },
        {
            "id": "moderation",
            "label": "No video hidden by a moderator in the last 30 days",
            "done": recent_hidden == 0,
        },
    ]
    return {
        "eligible": all(item["done"] for item in checks),
        "checks": checks,
        "monetized": bool(ch.get("monetized")),
    }


def accept_terms(sc: sqlite3.Connection, ch: dict[str, Any]) -> dict[str, Any]:
    if not videos.adult(ch):
        raise PermissionError("Earning money from videos is for adults.")
    sc.execute(
        "UPDATE vi_channels SET terms_accepted_at = ?, updated_at = ? WHERE id = ?",
        (stamp(), stamp(), ch["id"]),
    )
    sc.commit()
    return videos.channel(sc, ch["id"])


def set_monetized(
    sc: sqlite3.Connection, ch: dict[str, Any], on: bool
) -> dict[str, Any]:
    if on:
        state = eligibility(sc, ch)
        if not state["eligible"]:
            missing = [item["label"] for item in state["checks"] if not item["done"]]
            raise PermissionError("Not yet: " + "; ".join(missing) + ".")
    sc.execute(
        "UPDATE vi_channels SET monetized = ?, updated_at = ? WHERE id = ?",
        (1 if on else 0, stamp(), ch["id"]),
    )
    sc.commit()
    return videos.channel(sc, ch["id"])


def _earns(sc: sqlite3.Connection, ch: dict[str, Any]) -> bool:
    """Fast check at serving time (the full list is :func:`eligibility`)."""
    return bool(
        ch.get("monetized")
        and ch.get("terms_accepted_at")
        and videos.adult(ch)
        and not ch.get("suspended_at")
        and videos.strikes(sc, ch["id"]) == 0
    )


# ---------------------------------------------------------------------------
# serving, counting, clicks
# ---------------------------------------------------------------------------


def _ecpm(sc: sqlite3.Connection, campaign: dict[str, Any]) -> float:
    if campaign["bid_type"] == "cpm":
        return float(campaign["bid_micros"])
    stats = row(
        sc,
        "SELECT COUNT(*) AS n, COALESCE(SUM(click_counted), 0) AS clicks FROM ad_impressions WHERE campaign_id = ? AND status = 'counted'",
        (campaign["id"],),
    ) or {"n": 0, "clicks": 0}
    rate = (int(stats["clicks"] or 0) + 1) / (int(stats["n"] or 0) + 50)
    return float(campaign["bid_micros"]) * rate * 1000


def _pace_allows(sc: sqlite3.Connection, campaign: dict[str, Any]) -> bool:
    moment = store.utcnow()
    seconds = moment.hour * 3600 + moment.minute * 60 + moment.second
    allowed = campaign["daily_budget_micros"] * min(1.0, seconds / 86400 + 1 / 24)
    cost = unit_cost(campaign)
    return (
        _spend(sc, campaign["id"], store.day()) + cost <= allowed
        and _spend(sc, campaign["id"]) + cost <= campaign["total_budget_micros"]
    )


def serve(
    sc: sqlite3.Connection, me_: dict[str, Any], video: dict[str, Any], slot: str
) -> dict[str, Any] | None:
    """Pick the ad for one slot of one playback (or none) and remember it was served."""
    slot = kit.choice(slot, "the ad slot", SLOTS)
    if me_.get("age_group") == "child":
        return None  # never advertise to children
    if (
        not video.get("ads_enabled")
        or video["visibility"] == "private"
        or videos.blocked_reason(sc, video)
    ):
        return None
    if video["channel_id"] == me_["id"]:
        return None  # a creator watching their own video
    creator = row(sc, "SELECT * FROM vi_channels WHERE id = ?", (video["channel_id"],))
    if not creator or not _earns(sc, videos._channel(creator) or {}):
        return None
    today = store.day()
    found = rows(
        sc,
        """SELECT cr.id AS creative_id, c.* FROM ad_creatives cr JOIN ad_campaigns c ON c.id = cr.campaign_id
        WHERE cr.kind = ? AND cr.review_status = 'approved' AND cr.archived_at IS NULL AND c.status = 'active'
        AND c.deleted_at IS NULL AND (c.starts_on = '' OR c.starts_on <= ?) AND (c.ends_on = '' OR c.ends_on >= ?)""",
        (slot, today, today),
    )
    needs_family_safe = bool(video["for_kids"]) or me_.get("age_group") == "teen"
    by_campaign: dict[str, list[str]] = defaultdict(list)
    campaigns_by_id: dict[str, dict[str, Any]] = {}
    for item in found:
        by_campaign[item["id"]].append(item["creative_id"])
        campaigns_by_id[item["id"]] = item
    eligible: list[tuple[float, float, dict[str, Any]]] = []
    for campaign_id, candidate in campaigns_by_id.items():
        categories = store.loads(candidate["categories"], [])
        languages = store.loads(candidate["languages"], [])
        countries = store.loads(candidate["countries"], [])
        if categories and video["category"] not in categories:
            continue
        if languages and video["language"] not in languages:
            continue
        if countries and (me_.get("country") or "") not in countries:
            continue
        if needs_family_safe and not candidate["family_safe"]:
            continue
        if video["rating"] == "mature" and not candidate["allow_mature"]:
            continue
        balance = int(
            store.scalar(
                sc,
                "SELECT balance_micros FROM ad_accounts WHERE home = ?",
                (candidate["home"],),
            )
            or 0
        )
        if balance < unit_cost(candidate):
            continue
        if not _pace_allows(sc, candidate):
            continue
        shown = int(
            store.scalar(
                sc,
                "SELECT COUNT(*) FROM ad_impressions WHERE viewer_key = ? AND campaign_id = ? AND day = ? AND status != 'invalid'",
                (me_["id"], campaign_id, today),
            )
            or 0
        )
        if shown >= int(candidate["frequency_cap"]):
            continue
        eligible.append((_ecpm(sc, candidate), random.random(), candidate))  # noqa: S311 - a tie-break, not security
    if not eligible:
        return None
    eligible.sort(key=lambda item: (item[0], item[1]), reverse=True)
    chosen = eligible[0][2]
    creative_id = random.choice(by_campaign[chosen["id"]])  # noqa: S311
    creative = row(sc, "SELECT * FROM ad_creatives WHERE id = ?", (creative_id,)) or {}
    impression_id = new_id()
    moment = store.utcnow()
    sc.execute(
        """INSERT INTO ad_impressions (id, campaign_id, creative_id, home, slot, video_id, channel_id, viewer_key, viewer_home,
        served_at, status, day, month) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'served', ?, ?)""",
        (
            impression_id,
            chosen["id"],
            creative_id,
            chosen["home"],
            slot,
            video["id"],
            video["channel_id"],
            me_["id"],
            me_.get("home", ""),
            stamp(moment),
            store.day(moment),
            store.month(moment),
        ),
    )
    sc.commit()
    landing = chosen["landing_url"]
    return {
        "impression_id": impression_id,
        "slot": slot,
        "kind": creative.get("kind", slot),
        "headline": creative.get("headline", ""),
        "body": creative.get("body", ""),
        "cta": creative.get("cta", "Learn more"),
        "advertiser": chosen["advertiser"] or "Advertiser",
        "media_url": media.signed_url(sc, creative.get("file_id", "")),
        "mime": (
            row(
                sc,
                "SELECT mime FROM vi_files WHERE id = ?",
                (creative.get("file_id", ""),),
            )
            or {}
        ).get("mime", ""),
        "click_url": click_path(sc, impression_id),
        "domain": landing.split("/")[2] if landing.count("/") >= 2 else landing,
        "skip_after": SKIP_AFTER_SECONDS,
        "seen_after": SEEN_SECONDS,
    }


def click_path(sc: sqlite3.Connection, impression_id: str) -> str:
    return f"/api/videos/ads/go/{impression_id}?s={media.sign(sc, f'click:{impression_id}')}"


def _invalid(sc: sqlite3.Connection, impression_id: str, reason: str) -> dict[str, Any]:
    sc.execute(
        "UPDATE ad_impressions SET status = 'invalid', invalid_reason = ?, seen_at = COALESCE(seen_at, ?) WHERE id = ? AND status = 'served'",
        (reason, stamp(), impression_id),
    )
    sc.commit()
    return {"counted": False, "reason": reason}


def _charge(sc: sqlite3.Connection, campaign: dict[str, Any], cost: int) -> bool:
    """Draw ``cost`` from the advertiser if the balance and the campaign's total budget allow it (one statement
    each, so two charges at once cannot overdraw)."""
    if cost <= 0:
        return True
    if _spend(sc, campaign["id"]) + cost > campaign["total_budget_micros"]:
        sc.execute(
            "UPDATE ad_campaigns SET status = 'ended', paused_reason = 'budget_spent', updated_at = ? WHERE id = ? AND status = 'active'",
            (stamp(), campaign["id"]),
        )
        return False
    taken = sc.execute(
        "UPDATE ad_accounts SET balance_micros = balance_micros - ?, updated_at = ? WHERE home = ? AND balance_micros >= ?",
        (cost, stamp(), campaign["home"], cost),
    ).rowcount
    return bool(taken)


def _after_charge(sc: sqlite3.Connection, campaign: dict[str, Any]) -> None:
    """Pause what the balance can no longer pay for; end a campaign whose total budget is spent."""
    balance = int(
        store.scalar(
            sc,
            "SELECT balance_micros FROM ad_accounts WHERE home = ?",
            (campaign["home"],),
        )
        or 0
    )
    for item in rows(
        sc,
        "SELECT * FROM ad_campaigns WHERE home = ? AND status = 'active'",
        (campaign["home"],),
    ):
        if balance < unit_cost(item):
            sc.execute(
                "UPDATE ad_campaigns SET status = 'paused', paused_reason = 'no_balance', updated_at = ? WHERE id = ?",
                (stamp(), item["id"]),
            )
    if (
        _spend(sc, campaign["id"]) + unit_cost(campaign)
        > campaign["total_budget_micros"]
    ):
        sc.execute(
            "UPDATE ad_campaigns SET status = 'ended', paused_reason = 'budget_spent', updated_at = ? WHERE id = ? AND status IN ('active', 'paused')",
            (stamp(), campaign["id"]),
        )


def _share(sc: sqlite3.Connection, cost: int) -> int:
    return cost * settings(sc)["revenue_share_percent"] // 100


def seen(
    sc: sqlite3.Connection, me_: dict[str, Any], impression_id: str
) -> dict[str, Any]:
    """The browser says the ad played (or was on screen) for 2 seconds: count it, charge it and credit the creator,
    unless a rule says it does not count."""
    found = row(
        sc,
        "SELECT * FROM ad_impressions WHERE id = ? AND viewer_key = ?",
        (store.check_id(impression_id, "ad"), me_["id"]),
    )
    if not found:
        raise LookupError("That ad was not found.")
    if found["status"] != "served":
        return {
            "counted": found["status"] == "counted",
            "reason": found["invalid_reason"],
        }
    elapsed = (store.utcnow() - store.parse(found["served_at"])).total_seconds()
    if elapsed < SEEN_SECONDS:
        return {"counted": False, "reason": "too_soon"}
    if elapsed > SEEN_WINDOW_MINUTES * 60:
        return _invalid(sc, found["id"], "late")
    campaign = row(
        sc, "SELECT * FROM ad_campaigns WHERE id = ?", (found["campaign_id"],)
    )
    if not campaign:
        return _invalid(sc, found["id"], "campaign_gone")
    if found["viewer_home"] and found["viewer_home"] == campaign["home"]:
        return _invalid(sc, found["id"], "own_ads")
    creator = row(
        sc, "SELECT id, home FROM vi_channels WHERE id = ?", (found["channel_id"],)
    )
    if (
        not creator
        or found["viewer_key"] == creator["id"]
        or (found["viewer_home"] and found["viewer_home"] == creator["home"])
    ):
        return _invalid(sc, found["id"], "own_video")
    counted_today = int(
        store.scalar(
            sc,
            "SELECT COUNT(*) FROM ad_impressions WHERE viewer_key = ? AND campaign_id = ? AND day = ? AND status = 'counted'",
            (found["viewer_key"], campaign["id"], found["day"]),
        )
        or 0
    )
    if counted_today >= int(campaign["frequency_cap"]):
        return _invalid(sc, found["id"], "repeat")
    last_hour = int(
        store.scalar(
            sc,
            "SELECT COUNT(*) FROM ad_impressions WHERE viewer_key = ? AND status = 'counted' AND seen_at > ?",
            (found["viewer_key"], store.later(-1)),
        )
        or 0
    )
    if last_hour >= HOURLY_CAP:
        return _invalid(sc, found["id"], "too_many")
    claimed = sc.execute(
        "UPDATE ad_impressions SET status = 'counting', seen_at = ? WHERE id = ? AND status = 'served'",
        (stamp(), found["id"]),
    ).rowcount
    if not claimed:
        sc.commit()
        return {"counted": False, "reason": "already"}
    cost = unit_cost(campaign) if campaign["bid_type"] == "cpm" else 0
    if not _charge(sc, campaign, cost):
        sc.execute(
            "UPDATE ad_impressions SET status = 'invalid', invalid_reason = 'budget' WHERE id = ?",
            (found["id"],),
        )
        _after_charge(sc, campaign)
        sc.commit()
        return {"counted": False, "reason": "budget"}
    earned = _share(sc, cost)
    sc.execute(
        "UPDATE ad_impressions SET status = 'counted', cost_micros = ?, creator_micros = ?, earning_status = ? WHERE id = ?",
        (cost, earned, "estimated" if earned else "none", found["id"]),
    )
    _after_charge(sc, campaign)
    sc.commit()
    return {"counted": True, "cost_micros": cost}


def click(sc: sqlite3.Connection, impression_id: str, signature: str) -> str:
    """Record a click through the signed redirect and return where it goes."""
    import hmac

    impression_id = store.check_id(impression_id, "ad")
    if not hmac.compare_digest(
        media.sign(sc, f"click:{impression_id}"), str(signature or "")
    ):
        raise LookupError("That ad was not found.")
    found = row(sc, "SELECT * FROM ad_impressions WHERE id = ?", (impression_id,))
    if not found:
        raise LookupError("That ad was not found.")
    campaign = row(
        sc, "SELECT * FROM ad_campaigns WHERE id = ?", (found["campaign_id"],)
    )
    if not campaign:
        raise LookupError("That ad was not found.")
    creative = row(
        sc,
        "SELECT review_status FROM ad_creatives WHERE id = ?",
        (found["creative_id"],),
    )
    if not creative or creative["review_status"] == "rejected":
        raise LookupError(
            "This ad is no longer shown."
        )  # a reviewer turned it down after it was served
    moment = stamp()
    sc.execute(
        "UPDATE ad_impressions SET clicked_at = COALESCE(clicked_at, ?) WHERE id = ?",
        (moment, found["id"]),
    )
    recent = (store.utcnow() - store.parse(found["served_at"])) < timedelta(
        hours=CLICK_WINDOW_HOURS
    )
    already = int(
        store.scalar(
            sc,
            "SELECT COUNT(*) FROM ad_impressions WHERE viewer_key = ? AND campaign_id = ? AND day = ? AND click_counted = 1",
            (found["viewer_key"], campaign["id"], store.day()),
        )
        or 0
    )
    if (
        found["status"] == "counted"
        and not found["click_counted"]
        and recent
        and not already
    ):
        cost = unit_cost(campaign) if campaign["bid_type"] == "cpc" else 0
        if _charge(sc, campaign, cost):
            earned = _share(sc, cost)
            sc.execute(
                """UPDATE ad_impressions SET click_counted = 1, cost_micros = cost_micros + ?, creator_micros = creator_micros + ?,
                earning_status = CASE WHEN creator_micros + ? > 0 THEN 'estimated' ELSE earning_status END WHERE id = ? AND click_counted = 0""",
                (cost, earned, earned, found["id"]),
            )
            _after_charge(sc, campaign)
    sc.commit()
    landing = landing_when_served(sc, found)
    if options(sc, campaign["id"])["utm"]:
        return tracked_url(landing, campaign, found["slot"])
    return landing


# ---------------------------------------------------------------------------
# reports for the advertiser
# ---------------------------------------------------------------------------


def landing_when_served(sc: sqlite3.Connection, impression: dict[str, Any]) -> str:
    """The page the campaign sent clicks to when this impression was served (the one its ads were reviewed for):
    a page set later has not been reviewed for that ad, so an old click link never leads there."""
    earlier = row(
        sc,
        "SELECT landing_url FROM ad_campaign_pages WHERE campaign_id = ? AND until_at >= ? ORDER BY until_at LIMIT 1",
        (impression["campaign_id"], impression["served_at"]),
    )
    if earlier:
        return str(earlier["landing_url"])
    found = row(
        sc,
        "SELECT landing_url FROM ad_campaigns WHERE id = ?",
        (impression["campaign_id"],),
    )
    if not found:
        raise LookupError("That ad was not found.")
    return str(found["landing_url"])


def overview(sc: sqlite3.Connection, home: str) -> dict[str, Any]:
    acct = account(sc, home)
    this_month = store.month()
    return {
        "balance_micros": int(acct.get("balance_micros") or 0),
        "month": _stats(sc, "home = ? AND month = ?", (home, this_month)),
        "today": _stats(sc, "home = ? AND day = ?", (home, store.day())),
        **_runway(sc, home, int(acct.get("balance_micros") or 0)),
        "all_time": _stats(sc, "home = ?", (home,)),
        "active": int(
            store.scalar(
                sc,
                "SELECT COUNT(*) FROM ad_campaigns WHERE home = ? AND status = 'active' AND deleted_at IS NULL",
                (home,),
            )
            or 0
        ),
        "paused_no_balance": int(
            store.scalar(
                sc,
                "SELECT COUNT(*) FROM ad_campaigns WHERE home = ? AND status = 'paused' AND paused_reason = 'no_balance' AND deleted_at IS NULL",
                (home,),
            )
            or 0
        ),
        "pending_review": int(
            store.scalar(
                sc,
                "SELECT COUNT(*) FROM ad_creatives WHERE home = ? AND review_status = 'pending' AND archived_at IS NULL",
                (home,),
            )
            or 0
        ),
        "rejected": int(
            store.scalar(
                sc,
                "SELECT COUNT(*) FROM ad_creatives WHERE home = ? AND review_status = 'rejected' AND archived_at IS NULL",
                (home,),
            )
            or 0
        ),
    }


def _runway(sc: sqlite3.Connection, home: str, balance: int) -> dict[str, Any]:
    """How long the balance lasts at the last 7 days' spending."""
    week = _stats(
        sc,
        "home = ? AND day >= ?",
        (home, store.day(store.utcnow() - timedelta(days=6))),
    )["spend_micros"]
    per_day = week // 7
    return {
        "spend_per_day_micros": per_day,
        "balance_days": round(balance / per_day, 1) if per_day else None,
    }


def report(
    sc: sqlite3.Connection, home: str, campaign_id: str = "", days: int = 30
) -> dict[str, Any]:
    where, params = "home = ?", (home,)
    if campaign_id:
        _campaign_row(sc, home, campaign_id)
        where, params = "home = ? AND campaign_id = ?", (home, campaign_id)
    since = store.day(store.utcnow() - timedelta(days=days - 1))
    by_day = []
    for item in rows(
        sc,
        f"""SELECT day, COUNT(*) AS impressions, COALESCE(SUM(click_counted), 0) AS clicks, COALESCE(SUM(cost_micros), 0) AS spend_micros
        FROM ad_impressions WHERE status = 'counted' AND {where} AND day >= ? GROUP BY day ORDER BY day""",  # noqa: S608
        (*params, since),
    ):
        by_day.append(_rates(item))
    by_creative = []
    for item in rows(
        sc,
        f"""SELECT creative_id, campaign_id, COUNT(*) AS impressions, COALESCE(SUM(click_counted), 0) AS clicks,
        COALESCE(SUM(cost_micros), 0) AS spend_micros FROM ad_impressions WHERE status = 'counted' AND {where} AND day >= ?
        GROUP BY creative_id, campaign_id ORDER BY spend_micros DESC""",  # noqa: S608
        (*params, since),
    ):
        creative = (
            row(
                sc,
                "SELECT headline, kind FROM ad_creatives WHERE id = ?",
                (item["creative_id"],),
            )
            or {}
        )
        found = (
            row(
                sc, "SELECT name FROM ad_campaigns WHERE id = ?", (item["campaign_id"],)
            )
            or {}
        )
        by_creative.append(
            {
                **_rates(item),
                "headline": creative.get("headline", ""),
                "kind": creative.get("kind", ""),
                "campaign": found.get("name", ""),
            }
        )
    invalid = rows(
        sc,
        f"SELECT invalid_reason AS reason, COUNT(*) AS n FROM ad_impressions WHERE status = 'invalid' AND {where} AND day >= ? GROUP BY invalid_reason",  # noqa: S608
        (*params, since),
    )
    sales_by_day = {
        item["day"]: item
        for item in rows(
            sc,
            f"""SELECT day, COALESCE(SUM(orders), 0) AS orders, COALESCE(SUM(value_micros), 0) AS sales_micros
            FROM ad_sales WHERE {where} AND day >= ? GROUP BY day""",  # noqa: S608
            (*params, since),
        )
    }
    seen_days = {item["day"] for item in by_day}
    for day_ in sales_by_day:
        if (
            day_ not in seen_days
        ):  # a sale on a day the ads did not run still belongs in the report
            by_day.append(
                _rates({"day": day_, "impressions": 0, "clicks": 0, "spend_micros": 0})
            )
    by_day.sort(key=lambda item: item["day"])
    for item in by_day:
        found = sales_by_day.get(item["day"]) or {}
        item["orders"] = int(found.get("orders") or 0)
        item["sales_micros"] = int(found.get("sales_micros") or 0)
    totals = _stats(sc, f"{where} AND day >= ?", (*params, since))
    totals.update(
        _sales_totals(
            sc, f"{where} AND day >= ?", (*params, since), totals["spend_micros"]
        )
    )
    return {
        "since": since,
        "days": by_day,
        "creatives": by_creative,
        "campaigns": _campaign_results(sc, home, since, campaign_id),
        "invalid": invalid,
        "totals": totals,
    }


def _sales_totals(
    sc: sqlite3.Connection, where: str, params: tuple[Any, ...], spend: int
) -> dict[str, Any]:
    """Orders and their value from ``ad_sales`` matching ``where``, against ``spend``: the return on ad spend (ROAS,
    sales for each dollar spent), the cost of one order and what is left after the ads."""
    found = row(
        sc,
        f"SELECT COALESCE(SUM(orders), 0) AS orders, COALESCE(SUM(value_micros), 0) AS sales FROM ad_sales WHERE {where}",  # noqa: S608
        params,
    ) or {"orders": 0, "sales": 0}
    orders = int(found["orders"] or 0)
    sales_ = int(found["sales"] or 0)
    return {
        "orders": orders,
        "sales_micros": sales_,
        "roas": round(sales_ / spend, 2) if spend else None,
        "cost_per_order_micros": spend // orders if orders else None,
        "profit_micros": sales_ - spend,
    }


def _campaign_results(
    sc: sqlite3.Connection, home: str, since: str, campaign_id: str = ""
) -> list[dict[str, Any]]:
    """Each campaign's spend against the sales it brought in the period, best return first."""
    out = []
    for item in rows(
        sc,
        "SELECT id, name, status FROM ad_campaigns WHERE home = ? AND deleted_at IS NULL"
        + (" AND id = ?" if campaign_id else "")
        + " ORDER BY created_at DESC LIMIT 200",
        (home, campaign_id) if campaign_id else (home,),
    ):
        stats = _stats(sc, "campaign_id = ? AND day >= ?", (item["id"], since))
        result = _sales_totals(
            sc,
            "campaign_id = ? AND day >= ?",
            (item["id"], since),
            stats["spend_micros"],
        )
        if not stats["impressions"] and not result["orders"]:
            continue
        out.append({**item, **stats, **result})
    out.sort(key=lambda item: (item["roas"] or 0, item["sales_micros"]), reverse=True)
    return out


def _rates(item: dict[str, Any]) -> dict[str, Any]:
    impressions = int(item["impressions"] or 0)
    clicks = int(item["clicks"] or 0)
    spend = int(item["spend_micros"] or 0)
    return {
        **item,
        "ctr_percent": round(clicks * 100 / impressions, 2) if impressions else 0.0,
        "ecpm_micros": spend * 1000 // impressions if impressions else 0,
    }


def report_csv(
    sc: sqlite3.Connection, home: str, campaign_id: str = "", days: int = 90
) -> bytes:
    data = report(sc, home, campaign_id, days)
    lines = [
        {
            "day": item["day"],
            "impressions": item["impressions"],
            "clicks": item["clicks"],
            "ctr_percent": item["ctr_percent"],
            "spend_usd": f"{item['spend_micros'] / MICROS:.4f}",
            "ecpm_usd": f"{item['ecpm_micros'] / MICROS:.4f}",
        }
        for item in data["days"]
    ]
    for line, item in zip(lines, data["days"], strict=True):
        line["orders"] = item["orders"]
        line["sales_usd"] = f"{item['sales_micros'] / MICROS:.2f}"
    return kit.csv_bytes(
        lines,
        (
            "day",
            "impressions",
            "clicks",
            "ctr_percent",
            "spend_usd",
            "ecpm_usd",
            "orders",
            "sales_usd",
        ),
    )


CAMPAIGN_CSV_COLUMNS = (
    "name",
    "status",
    "pays_for",
    "bid_usd",
    "bid_per",
    "total_budget_usd",
    "daily_budget_usd",
    "starts_on",
    "ends_on",
    "spent_usd",
    "impressions",
    "clicks",
    "ctr_percent",
    "orders",
    "sales_usd",
    "roas",
    "landing_url",
)


def campaigns_csv(sc: sqlite3.Connection, home: str) -> bytes:
    """Every campaign with its settings and results so far (the Campaigns list as a spreadsheet)."""
    lines = []
    for item in campaigns(sc, home):
        result = item["sales"]
        lines.append(
            {
                "name": item["name"],
                "status": item["status"],
                "pays_for": item["objective"],
                "bid_usd": f"{item['bid_micros'] / MICROS:.2f}",
                "bid_per": "1000 impressions" if item["bid_type"] == "cpm" else "click",
                "total_budget_usd": f"{item['total_budget_micros'] / MICROS:.2f}",
                "daily_budget_usd": f"{item['daily_budget_micros'] / MICROS:.2f}",
                "starts_on": item["starts_on"],
                "ends_on": item["ends_on"],
                "spent_usd": f"{item['spent_micros'] / MICROS:.4f}",
                "impressions": item["stats"]["impressions"],
                "clicks": item["stats"]["clicks"],
                "ctr_percent": item["stats"]["ctr_percent"],
                "orders": result["orders"],
                "sales_usd": f"{result['sales_micros'] / MICROS:.2f}",
                "roas": "" if result["roas"] is None else result["roas"],
                "landing_url": item["landing_url"],
            }
        )
    return kit.csv_bytes(lines, CAMPAIGN_CSV_COLUMNS)


# ---------------------------------------------------------------------------
# sales from ads (the advertiser counts them; Aira does not see their shop)
# ---------------------------------------------------------------------------


def sales(sc: sqlite3.Connection, home: str, campaign_id: str) -> list[dict[str, Any]]:
    found = _campaign_row(sc, home, campaign_id)
    return rows(
        sc,
        "SELECT * FROM ad_sales WHERE campaign_id = ? ORDER BY day DESC, created_at DESC LIMIT 500",
        (found["id"],),
    )


def add_sale(
    sc: sqlite3.Connection, home: str, campaign_id: str, raw: dict[str, Any], by: str
) -> dict[str, Any]:
    """Record sales a campaign brought (orders on a day and what they were worth), for the return on ad spend."""
    found = _campaign_row(sc, home, campaign_id)
    day_ = kit.day(raw.get("day"), "the day of the sale") or store.day()
    if day_ > store.day():
        raise ValueError("A sale cannot be in the future.")
    if day_ < found["created_at"][:10]:
        raise ValueError("That day is before the campaign was made.")
    orders = kit.integer(
        raw.get("orders"), "the number of orders", default=1, minimum=1, maximum=100_000
    )
    cents = kit.integer(
        raw.get("value_cents"),
        "what the orders were worth",
        minimum=0,
        maximum=100_000_000,
    )
    note = kit.text(raw.get("note"), "a note", max_len=200)
    if (
        int(
            store.scalar(
                sc,
                "SELECT COUNT(*) FROM ad_sales WHERE campaign_id = ?",
                (found["id"],),
            )
            or 0
        )
        >= 5000
    ):
        raise ValueError("A campaign keeps at most 5,000 sales lines.")
    sale_id = new_id()
    sc.execute(
        "INSERT INTO ad_sales (id, campaign_id, home, day, orders, value_micros, note, created_by, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            sale_id,
            found["id"],
            home,
            day_,
            orders,
            cents * CENT,
            note,
            by[:120],
            stamp(),
        ),
    )
    sc.commit()
    return row(sc, "SELECT * FROM ad_sales WHERE id = ?", (sale_id,)) or {}


def delete_sale(sc: sqlite3.Connection, home: str, sale_id: str) -> None:
    gone = sc.execute(
        "DELETE FROM ad_sales WHERE id = ? AND home = ?",
        (store.check_id(sale_id, "sale"), home),
    ).rowcount
    if not gone:
        raise LookupError("That sale was not found.")
    sc.commit()


# ---------------------------------------------------------------------------
# what a campaign can expect (before money is spent)
# ---------------------------------------------------------------------------

ESTIMATE_DAYS = 7


def _platform_ctr(sc: sqlite3.Connection) -> float:
    """The click rate of every counted ad in the last 30 days, smoothed towards 1% while there are few."""
    found = row(
        sc,
        "SELECT COUNT(*) AS n, COALESCE(SUM(click_counted), 0) AS clicks FROM ad_impressions WHERE status = 'counted' AND day >= ?",
        (store.day(store.utcnow() - timedelta(days=30)),),
    ) or {"n": 0, "clicks": 0}
    return (int(found["clicks"] or 0) + 1) / (int(found["n"] or 0) + 100)


def _eligible_views(sc: sqlite3.Connection, target: dict[str, Any]) -> int:
    """Views in the last week, on videos that can carry this campaign's ads, by people it may be shown to."""
    sql = [
        """SELECT COUNT(*) FROM vi_views w JOIN vi_videos v ON v.id = w.video_id JOIN vi_channels c ON c.id = v.channel_id
        LEFT JOIN vi_channels p ON p.id = w.viewer_key
        WHERE w.day >= ? AND v.ads_enabled = 1 AND v.visibility != 'private' AND v.deleted_at IS NULL
        AND v.hidden_at IS NULL AND v.takedown_at IS NULL AND c.monetized = 1 AND c.terms_accepted_at IS NOT NULL
        AND c.suspended_at IS NULL AND w.viewer_key != v.channel_id AND COALESCE(p.age_group, 'adult') != 'child'"""
    ]
    params: list[Any] = [store.day(store.utcnow() - timedelta(days=ESTIMATE_DAYS - 1))]
    for column, values in (
        ("v.category", target["categories"]),
        ("v.language", target["languages"]),
        ("COALESCE(p.country, '')", target["countries"]),
    ):
        if values:
            sql.append(f"AND {column} IN ({', '.join('?' for _ in values)})")
            params.extend(values)
    if not target["family_safe"]:
        sql.append("AND v.for_kids = 0 AND COALESCE(p.age_group, 'adult') != 'teen'")
    if not target["allow_mature"]:
        sql.append("AND v.rating != 'mature'")
    return int(store.scalar(sc, " ".join(sql), tuple(params)) or 0)


def _competitors(
    sc: sqlite3.Connection, home: str, target: dict[str, Any]
) -> list[float]:
    """The effective bids (per 1,000) of other advertisers' running campaigns that could want the same views."""
    out = []
    for item in rows(
        sc,
        """SELECT DISTINCT c.* FROM ad_campaigns c JOIN ad_creatives cr ON cr.campaign_id = c.id
        JOIN ad_accounts a ON a.home = c.home
        WHERE c.status = 'active' AND c.deleted_at IS NULL AND c.home != ? AND cr.review_status = 'approved'
        AND cr.archived_at IS NULL AND a.balance_micros > 0""",
        (home,),
    ):
        overlap = True
        for key in ("categories", "languages", "countries"):
            theirs = store.loads(item[key], [])
            if theirs and target[key] and not set(theirs) & set(target[key]):
                overlap = False
        if overlap:
            out.append(_ecpm(sc, item))
    return out


def _bid_range_text(bid_type: str) -> str:
    low, high = CPM_RANGE if bid_type == "cpm" else CPC_RANGE
    per = "per 1,000 views" if bid_type == "cpm" else "per click"
    return f"between {dollars(low)} and {dollars(high)} {per}"


def bid_step(ecpm: float) -> int:
    """How coarsely another advertiser's bid (per 1,000) is shown in a forecast: 50 cents up to $10, $1 up to $50,
    $5 above."""
    if ecpm <= 10 * MICROS:
        return 50 * CENT
    if ecpm <= 50 * MICROS:
        return MICROS
    return 5 * MICROS


def bid_bucket(ecpm: float) -> int:
    """A rival's bid rounded up to the next step strictly above it: beating the step beats the bid."""
    step = bid_step(ecpm)
    return (int(ecpm) // step + 1) * step


def estimate(
    sc: sqlite3.Connection, home: str, raw: dict[str, Any], campaign_id: str = ""
) -> dict[str, Any]:
    """A rough forecast of what a campaign gets each day for its bid, budget and audience, from last week's viewing
    and the bids it competes with, and the bid that would win most often. Nothing is promised: real results move
    with what people watch."""
    current: dict[str, Any] = {}
    if campaign_id:
        current = _campaign_row(sc, home, campaign_id)
        for key in ("categories", "languages", "countries"):
            current[key] = store.loads(current[key], [])
    merged = {**current, **raw}
    objective = kit.choice(
        merged.get("objective"), "the objective", OBJECTIVES, default="views"
    )
    bid_type = BID_TYPE[objective]
    target = {
        "categories": _codes(
            merged.get("categories"),
            "the categories",
            None,
            upper=False,
            options=videos.CATEGORIES,
        ),
        "languages": _codes(
            merged.get("languages"), "languages", videos.LANGUAGE, upper=False
        ),
        "countries": _codes(
            merged.get("countries"), "countries", videos.COUNTRY, upper=True
        ),
        "family_safe": kit.flag(merged.get("family_safe")),
        "allow_mature": kit.flag(merged.get("allow_mature")),
    }

    def money(key: str) -> int:
        try:
            return _micros(merged, key, key)
        except ValueError:
            return 0

    low, high = CPM_RANGE if bid_type == "cpm" else CPC_RANGE
    try:
        bid = _micros(merged, "bid", "your bid")
    except ValueError:
        raise ValueError(f"Enter your bid ({_bid_range_text(bid_type)}).") from None
    if not low <= bid <= high:
        raise ValueError(f"Your bid must be {_bid_range_text(bid_type)}.")
    daily, total = money("daily_budget"), money("total_budget")
    week_views = _eligible_views(sc, target)
    views_per_day = week_views / ESTIMATE_DAYS
    ctr = _platform_ctr(sc)
    # Other advertisers' bids are their own business: the forecast compares against them rounded up to a coarse
    # step, so no answer (nor many answers for nearby bids) gives one away.
    rivals = sorted(
        (bid_bucket(other) for other in _competitors(sc, home, target)), reverse=True
    )
    if bid_type == "cpm":
        mine = float(bid)
    elif current and current.get("bid_type") == "cpc" and current.get("id"):
        mine = _ecpm(sc, {**current, "bid_micros": bid})
    else:
        mine = (
            bid * (1 / 50) * 1000
        )  # a new click campaign is ranked at a 2% click rate until it has its own
    if not rivals or mine >= rivals[0]:
        share = 1.0
    else:
        beaten = sum(1.0 if mine >= other else 0.0 for other in rivals)
        share = max(0.1, beaten / len(rivals))
    top = rivals[0] if rivals else 0.0
    if not top:
        suggested = low
    elif bid_type == "cpm":
        suggested = int(top)
    else:  # the click bid that ranks at that step at a 2% click rate, rounded up to 5 cents
        suggested = -(-int(top / 1000 * 50) // (5 * CENT)) * (5 * CENT)
    suggested = min(high, max(low, -(-suggested // CENT) * CENT))
    reachable = views_per_day * share
    unit = max(1, (bid + 500) // 1000) if bid_type == "cpm" else bid
    impressions = reachable
    if unit and daily:
        cap = daily / unit if bid_type == "cpm" else (daily / unit) / ctr
        impressions = min(reachable, cap)
    clicks = impressions * ctr
    spend = impressions * unit if bid_type == "cpm" else clicks * unit
    balance = int(account(sc, home).get("balance_micros") or 0)
    return {
        "views_per_day": round(views_per_day, 1),
        "win_share_percent": round(share * 100),
        "competitors": len(rivals),
        "impressions_per_day": round(impressions, 1),
        "clicks_per_day": round(clicks, 2),
        "ctr_percent": round(ctr * 100, 2),
        "spend_per_day_micros": int(spend),
        "creators_per_day_micros": int(spend)
        * settings(sc)["revenue_share_percent"]
        // 100,
        "budget_days": round(total / spend, 1) if spend and total else None,
        "balance_days": round(balance / spend, 1) if spend else None,
        "suggested_bid_micros": int(suggested),
        "bid_type": bid_type,
        "budget_limited": bool(unit and daily and reachable > impressions + 1e-9),
        "confidence": "good"
        if week_views >= 1000
        else "some"
        if week_views >= 100
        else "low",
    }


# ---------------------------------------------------------------------------
# creators' earnings and payouts
# ---------------------------------------------------------------------------


def _sum(sc: sqlite3.Connection, sql: str, params: tuple[Any, ...]) -> int:
    return int(store.scalar(sc, sql, params) or 0)


def balances(sc: sqlite3.Connection, channel_id: str) -> dict[str, int]:
    estimated = _sum(
        sc,
        "SELECT COALESCE(SUM(creator_micros), 0) FROM ad_impressions WHERE channel_id = ? AND earning_status = 'estimated'",
        (channel_id,),
    )
    confirmed = _sum(
        sc,
        "SELECT COALESCE(SUM(creator_micros), 0) FROM ad_impressions WHERE channel_id = ? AND earning_status = 'confirmed'",
        (channel_id,),
    )
    paid = _sum(
        sc,
        "SELECT COALESCE(SUM(amount_micros), 0) FROM ad_payouts WHERE channel_id = ? AND status = 'paid'",
        (channel_id,),
    )
    requested = _sum(
        sc,
        "SELECT COALESCE(SUM(amount_micros), 0) FROM ad_payouts WHERE channel_id = ? AND status = 'requested'",
        (channel_id,),
    )
    return {
        "estimated_micros": estimated,
        "confirmed_micros": confirmed,
        "paid_micros": paid,
        "requested_micros": requested,
        "payable_micros": confirmed - paid - requested,
    }


def earnings(sc: sqlite3.Connection, ch: dict[str, Any]) -> dict[str, Any]:
    """Everything the Earnings page shows: today, this month, per video (with RPM), payouts and the rules."""
    this_month = store.month()
    today = _sum(
        sc,
        "SELECT COALESCE(SUM(creator_micros), 0) FROM ad_impressions WHERE channel_id = ? AND day = ? AND earning_status IN ('estimated', 'confirmed')",
        (ch["id"], store.day()),
    )
    month_total = _sum(
        sc,
        "SELECT COALESCE(SUM(creator_micros), 0) FROM ad_impressions WHERE channel_id = ? AND month = ? AND earning_status IN ('estimated', 'confirmed')",
        (ch["id"], this_month),
    )
    first_day = f"{this_month}-01"
    per_video = []
    for item in rows(
        sc,
        """SELECT video_id, COUNT(*) AS impressions, COALESCE(SUM(click_counted), 0) AS clicks, COALESCE(SUM(creator_micros), 0) AS earned_micros
        FROM ad_impressions WHERE channel_id = ? AND month = ? AND status = 'counted' AND earning_status IN ('estimated', 'confirmed')
        GROUP BY video_id ORDER BY earned_micros DESC LIMIT 100""",
        (ch["id"], this_month),
    ):
        video = (
            row(sc, "SELECT title FROM vi_videos WHERE id = ?", (item["video_id"],))
            or {}
        )
        views = _sum(
            sc,
            "SELECT COUNT(*) FROM vi_views WHERE video_id = ? AND day >= ?",
            (item["video_id"], first_day),
        )
        per_video.append(
            {
                **item,
                "title": video.get("title", "Removed video"),
                "views": views,
                "rpm_micros": int(item["earned_micros"]) * 1000 // views
                if views
                else 0,
            }
        )
    months = rows(
        sc,
        """SELECT month, COALESCE(SUM(CASE WHEN earning_status IN ('estimated', 'confirmed') THEN creator_micros ELSE 0 END), 0) AS earned_micros,
        COALESCE(SUM(CASE WHEN earning_status = 'clawed' THEN creator_micros ELSE 0 END), 0) AS clawed_micros,
        MAX(CASE WHEN earning_status = 'confirmed' THEN 1 ELSE 0 END) AS confirmed
        FROM ad_impressions WHERE channel_id = ? AND creator_micros > 0 GROUP BY month ORDER BY month DESC LIMIT 12""",
        (ch["id"],),
    )
    payouts = rows(
        sc,
        "SELECT * FROM ad_payouts WHERE channel_id = ? ORDER BY requested_at DESC LIMIT 50",
        (ch["id"],),
    )
    return {
        "eligibility": eligibility(sc, ch),
        "settings": settings(sc),
        "today_micros": today,
        "month_micros": month_total,
        "balances": balances(sc, ch["id"]),
        "videos": per_video,
        "months": months,
        "payouts": payouts,
        "payout_details": ch.get("payout_details", ""),
        "terms_accepted_at": ch.get("terms_accepted_at"),
    }


def request_payout(
    sc: sqlite3.Connection, ch: dict[str, Any], raw: dict[str, Any]
) -> dict[str, Any]:
    if not videos.adult(ch):
        raise PermissionError("Earning money from videos is for adults.")
    videos._active(ch)
    details = kit.text(
        raw.get("details") or ch.get("payout_details"),
        "where to send your money (bank or UPI details)",
        max_len=500,
        required=True,
        multiline=True,
    )
    if row(
        sc,
        "SELECT 1 AS x FROM ad_payouts WHERE channel_id = ? AND status = 'requested'",
        (ch["id"],),
    ):
        raise ValueError(
            "You already asked to be paid. Wait until that payment is made."
        )
    payable = balances(sc, ch["id"])["payable_micros"]
    minimum = settings(sc)["min_payout_micros"]
    if raw.get("amount_cents") in (None, ""):
        amount = payable
    else:
        amount = (
            kit.integer(
                raw.get("amount_cents"), "the amount (cents)", minimum=1, maximum=10**9
            )
            * CENT
        )
    if amount < minimum:
        raise ValueError(
            f"The smallest payout is {dollars(minimum)}; you can ask for up to {dollars(max(0, payable))}."
        )
    if amount > payable:
        raise ValueError(
            f"You can ask for up to {dollars(max(0, payable))} (confirmed earnings not yet paid)."
        )
    payout_id = new_id()
    sc.execute(
        "INSERT INTO ad_payouts (id, channel_id, home, amount_micros, details, status, requested_at) VALUES (?, ?, ?, ?, ?, 'requested', ?)",
        (payout_id, ch["id"], ch.get("home", ""), amount, details, stamp()),
    )
    sc.execute(
        "UPDATE vi_channels SET payout_details = ? WHERE id = ?", (details, ch["id"])
    )
    sc.commit()
    return row(sc, "SELECT * FROM ad_payouts WHERE id = ?", (payout_id,)) or {}


def payouts(sc: sqlite3.Connection, status: str = "requested") -> list[dict[str, Any]]:
    kit.choice(status, "the status", ("requested", "paid", "rejected"))
    out = []
    for item in rows(
        sc,
        "SELECT * FROM ad_payouts WHERE status = ? ORDER BY requested_at LIMIT 200",
        (status,),
    ):
        ch = row(sc, "SELECT * FROM vi_channels WHERE id = ?", (item["channel_id"],))
        out.append(
            {
                **item,
                "channel": videos.mini(ch) if ch else None,
                "balances": balances(sc, item["channel_id"])
                if item["channel_id"]
                else {},
            }
        )
    return out


def decide_payout(
    sc: sqlite3.Connection,
    payout_id: str,
    action: str,
    reference: str,
    note: str,
    by: str,
) -> dict[str, Any]:
    found = row(
        sc,
        "SELECT * FROM ad_payouts WHERE id = ?",
        (store.check_id(payout_id, "payout"),),
    )
    if not found:
        raise LookupError("That payout was not found.")
    if found["status"] != "requested":
        raise ValueError(f"This payout is already {found['status']}.")
    action = kit.choice(action, "the action", ("paid", "reject"))
    reference = kit.text(
        reference, "the payment reference", max_len=120, required=action == "paid"
    )
    note = kit.text(note, "a note", max_len=300, required=action == "reject")
    status = "paid" if action == "paid" else "rejected"
    sc.execute(
        "UPDATE ad_payouts SET status = ?, reference = ?, note = ?, decided_at = ?, decided_by = ? WHERE id = ?",
        (status, reference, note, stamp(), by[:120], found["id"]),
    )
    if found["channel_id"]:
        text = (
            f"You were paid {dollars(found['amount_micros'])} (reference {reference})."
            if status == "paid"
            else f"Your payout request was not paid: {note}"
        )
        videos.notify(
            sc,
            found["channel_id"],
            "payout",
            text,
            ref_type="payout",
            ref_id=found["id"],
        )
    sc.commit()
    return row(sc, "SELECT * FROM ad_payouts WHERE id = ?", (found["id"],)) or {}


def _refund(
    sc: sqlite3.Connection, impressions: list[dict[str, Any]], reason: str
) -> int:
    """Turn counted impressions invalid: the advertisers get their money back, the earnings go."""
    refunded = 0
    per_home: dict[str, int] = defaultdict(int)
    for item in impressions:
        changed = sc.execute(
            "UPDATE ad_impressions SET status = 'invalid', invalid_reason = ?, earning_status = CASE WHEN creator_micros > 0 THEN 'clawed' ELSE earning_status END WHERE id = ? AND status = 'counted'",
            (reason, item["id"]),
        ).rowcount
        if changed and item["cost_micros"]:
            per_home[item["home"]] += int(item["cost_micros"])
    for home, amount in per_home.items():
        if not row(sc, "SELECT 1 AS x FROM ad_accounts WHERE home = ?", (home,)):
            continue  # the advertiser left Aira: nothing to give back to
        sc.execute(
            "INSERT INTO ad_ledger (id, home, kind, amount_micros, reference, note, created_by, created_at) VALUES (?, ?, 'refund', ?, '', ?, 'aira', ?)",
            (new_id(), home, amount, f"Invalid traffic: {reason}"[:300], stamp()),
        )
        sc.execute(
            "UPDATE ad_accounts SET balance_micros = balance_micros + ? WHERE home = ?",
            (amount, home),
        )
        sc.execute(
            "UPDATE ad_campaigns SET status = 'active', paused_reason = '' WHERE home = ? AND status = 'paused' AND paused_reason = 'no_balance'",
            (home,),
        )
        refunded += amount
    return refunded


def close_month(sc: sqlite3.Connection, month: str, by: str) -> dict[str, Any]:
    """Review a finished month for invalid traffic, then confirm what creators earned in it."""
    if (
        not isinstance(month, str)
        or len(month) != 7
        or month[4] != "-"
        or not (month[:4] + month[5:]).isdigit()
    ):
        raise ValueError("Choose a month like 2026-09.")
    if month >= store.month():
        raise ValueError("A month can be closed once it is over.")
    if row(sc, "SELECT 1 AS x FROM ad_months WHERE month = ?", (month,)):
        raise ValueError(f"{month} is already closed.")
    excess: list[dict[str, Any]] = []
    for group in rows(
        sc,
        """SELECT viewer_key, channel_id, day, COUNT(*) AS n FROM ad_impressions WHERE month = ? AND status = 'counted'
        GROUP BY viewer_key, channel_id, day HAVING COUNT(*) > ?""",
        (month, REVIEW_DAILY_CAP),
    ):
        found = rows(
            sc,
            "SELECT * FROM ad_impressions WHERE month = ? AND status = 'counted' AND viewer_key = ? AND channel_id = ? AND day = ? ORDER BY seen_at",
            (month, group["viewer_key"], group["channel_id"], group["day"]),
        )
        excess.extend(found[REVIEW_DAILY_CAP:])
    refunded = _refund(sc, excess, "review_excess")
    confirmed = _sum(
        sc,
        "SELECT COALESCE(SUM(creator_micros), 0) FROM ad_impressions WHERE month = ? AND earning_status = 'estimated'",
        (month,),
    )
    sc.execute(
        "UPDATE ad_impressions SET earning_status = 'confirmed' WHERE month = ? AND earning_status = 'estimated'",
        (month,),
    )
    sc.execute(
        "INSERT INTO ad_months (month, closed_at, closed_by, invalid, refunded_micros, confirmed_micros) VALUES (?, ?, ?, ?, ?, ?)",
        (month, stamp(), by[:120], len(excess), refunded, confirmed),
    )
    sc.commit()
    return {
        "month": month,
        "invalid": len(excess),
        "refunded_micros": refunded,
        "confirmed_micros": confirmed,
    }


def due_months(sc: sqlite3.Connection) -> list[str]:
    """Finished months with earnings still estimated: closed automatically from the 2nd of the next month."""
    current = store.month()
    moment = store.utcnow()
    out = []
    for item in rows(
        sc,
        "SELECT DISTINCT month FROM ad_impressions WHERE earning_status = 'estimated' AND month < ? ORDER BY month",
        (current,),
    ):
        if row(sc, "SELECT 1 AS x FROM ad_months WHERE month = ?", (item["month"],)):
            continue
        previous = store.month(moment.replace(day=1) - timedelta(days=1))
        if item["month"] < previous or moment.day >= 2:
            out.append(item["month"])
    return out


def clawback(
    sc: sqlite3.Connection, channel_id: str, month: str, reason: str, by: str
) -> dict[str, Any]:
    """Take back a channel's earnings for a month (invalid traffic found later): advertisers are refunded."""
    channel_id = store.check_id(channel_id, "channel")
    if not isinstance(month, str) or len(month) != 7:
        raise ValueError("Choose a month like 2026-09.")
    reason = kit.text(reason, "the reason", max_len=200, required=True)
    found = rows(
        sc,
        "SELECT * FROM ad_impressions WHERE channel_id = ? AND month = ? AND status = 'counted'",
        (channel_id, month),
    )
    earned = sum(int(item["creator_micros"] or 0) for item in found)
    refunded = _refund(sc, found, "clawback")
    if found:
        videos.notify(
            sc,
            channel_id,
            "clawback",
            f"Earnings of {dollars(earned)} for {month} were taken back: {reason}",
            ref_type="month",
            ref_id=month,
        )
    sc.commit()
    return {
        "impressions": len(found),
        "clawed_micros": earned,
        "refunded_micros": refunded,
        "by": by,
    }


def totals(sc: sqlite3.Connection) -> dict[str, Any]:
    """The platform's books: what advertisers paid, what creators earned, what Aira keeps."""
    gross = _sum(
        sc,
        "SELECT COALESCE(SUM(cost_micros), 0) FROM ad_impressions WHERE status = 'counted'",
        (),
    )
    creators = _sum(
        sc,
        "SELECT COALESCE(SUM(creator_micros), 0) FROM ad_impressions WHERE status = 'counted' AND earning_status IN ('estimated', 'confirmed')",
        (),
    )
    months = rows(
        sc,
        """SELECT month, COALESCE(SUM(CASE WHEN status = 'counted' THEN cost_micros ELSE 0 END), 0) AS gross_micros,
        COALESCE(SUM(CASE WHEN status = 'counted' AND earning_status IN ('estimated', 'confirmed') THEN creator_micros ELSE 0 END), 0) AS creators_micros,
        COALESCE(SUM(CASE WHEN status = 'counted' THEN 1 ELSE 0 END), 0) AS impressions,
        COALESCE(SUM(CASE WHEN status = 'invalid' THEN 1 ELSE 0 END), 0) AS invalid
        FROM ad_impressions GROUP BY month ORDER BY month DESC LIMIT 12""",
        (),
    )
    for item in months:
        item["platform_micros"] = int(item["gross_micros"]) - int(
            item["creators_micros"]
        )
        item["closed"] = bool(
            row(sc, "SELECT 1 AS x FROM ad_months WHERE month = ?", (item["month"],))
        )
    return {
        "gross_micros": gross,
        "creators_micros": creators,
        "platform_micros": gross - creators,
        "estimated_micros": _sum(
            sc,
            "SELECT COALESCE(SUM(creator_micros), 0) FROM ad_impressions WHERE earning_status = 'estimated'",
            (),
        ),
        "paid_micros": _sum(
            sc,
            "SELECT COALESCE(SUM(amount_micros), 0) FROM ad_payouts WHERE status = 'paid'",
            (),
        ),
        "requested_micros": _sum(
            sc,
            "SELECT COALESCE(SUM(amount_micros), 0) FROM ad_payouts WHERE status = 'requested'",
            (),
        ),
        "refunded_micros": _sum(
            sc,
            "SELECT COALESCE(SUM(amount_micros), 0) FROM ad_ledger WHERE kind = 'refund'",
            (),
        ),
        "balances_micros": _sum(
            sc, "SELECT COALESCE(SUM(balance_micros), 0) FROM ad_accounts", ()
        ),
        "topped_up_micros": _sum(
            sc,
            "SELECT COALESCE(SUM(amount_micros), 0) FROM ad_ledger WHERE kind IN ('topup', 'payment')",
            (),
        ),
        "months": months,
        "pending_review": _sum(
            sc,
            "SELECT COUNT(*) FROM ad_creatives WHERE review_status = 'pending' AND archived_at IS NULL",
            (),
        ),
        "payouts_waiting": _sum(
            sc, "SELECT COUNT(*) FROM ad_payouts WHERE status = 'requested'", ()
        ),
        "settings": settings(sc),
    }


def tick(sc: sqlite3.Connection) -> dict[str, Any]:
    """Background: end campaigns past their end date and close finished months (both idempotent)."""
    ended = sc.execute(
        "UPDATE ad_campaigns SET status = 'ended', paused_reason = 'schedule_over', updated_at = ? WHERE status IN ('active', 'paused') AND ends_on != '' AND ends_on < ?",
        (stamp(), store.day()),
    ).rowcount
    sc.commit()
    closed = [close_month(sc, month, "aira")["month"] for month in due_months(sc)]
    return {"ended": ended, "closed": closed}


# ---------------------------------------------------------------------------
# an advertiser leaving (an organization deleted)
# ---------------------------------------------------------------------------


def erase_advertiser(sc: sqlite3.Connection, home: str) -> int:
    """Remove an advertiser's campaigns, ads, files, ledger and balance. Impressions stay for the creators who
    earned from them (without the advertiser's name)."""
    found = int(
        store.scalar(sc, "SELECT COUNT(*) FROM ad_campaigns WHERE home = ?", (home,))
        or 0
    )
    media.delete_owner(sc, owner_key(home))
    for statement in (
        "DELETE FROM ad_creatives WHERE home = ?",
        "DELETE FROM ad_campaigns WHERE home = ?",
        "DELETE FROM ad_ledger WHERE home = ?",
        "DELETE FROM ad_sales WHERE home = ?",
        "DELETE FROM ad_campaign_options WHERE home = ?",
        "DELETE FROM ad_campaign_pages WHERE home = ?",
        "DELETE FROM ad_accounts WHERE home = ?",
        "UPDATE ad_impressions SET viewer_home = '' WHERE viewer_home = ?",
    ):
        sc.execute(statement, (home,))
    sc.commit()
    return found


def paid_payouts(sc: sqlite3.Connection, home: str) -> list[dict[str, Any]]:
    """Payouts made to the channels of one account (the single-user build counts them as revenue)."""
    return rows(
        sc,
        "SELECT * FROM ad_payouts WHERE home = ? AND status = 'paid' ORDER BY decided_at",
        (home,),
    )
