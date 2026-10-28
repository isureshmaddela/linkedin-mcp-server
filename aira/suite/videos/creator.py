"""The creator's side of Videos beyond ads: **analytics** (what was watched, for how long, what earned the most and
the least) and **support from viewers** (tips paid through the creator's own payment page or UPI id).

Analytics come from the platform-wide Videos database (db.py): views (``vi_views``, once a day per viewer), seconds
really watched (``vi_watch_days``, others' watching only), likes, comments, new subscribers, the creator's share of
ads (``ad_impressions``) and support marked received. Nothing about who watched is shown, only totals.

Support moves no money through Aira. A creator (an adult who accepted the creator terms) writes where to pay them;
a viewer (an adult) pledges an amount with an optional message and gets a reference to put on the payment; the
creator marks the pledge **received** (it then counts in their analytics and, on this computer, in the Business
hub's revenue) or **not received**. The supporter can withdraw a pledge until then. Children and teens neither give
nor receive money.
"""

from __future__ import annotations

import csv
import io
import re
import sqlite3
from datetime import timedelta
from typing import Any
from urllib.parse import quote, urlencode

from aira.suite import kit
from aira.suite.videos import service, store
from aira.suite.videos.store import new_id, row, rows, stamp

PERIODS = (7, 28, 90, 365)
TIP_MIN_CENTS = 100
TIP_MAX_CENTS = 100_000
TIP_STATUSES = ("pledged", "received", "declined", "cancelled")


# ---------------------------------------------------------------------------
# analytics
# ---------------------------------------------------------------------------


def _period(days: Any) -> int:
    try:
        value = int(days or 28)
    except (TypeError, ValueError):
        raise ValueError("Choose 7, 28, 90 or 365 days.") from None
    if value not in PERIODS:
        raise ValueError("Choose 7, 28, 90 or 365 days.")
    return value


def _days(days: int, end_offset: int = 0) -> list[str]:
    today = store.utcnow() - timedelta(days=end_offset)
    return [store.day(today - timedelta(days=back)) for back in range(days - 1, -1, -1)]


def _int(sc: sqlite3.Connection, sql: str, params: tuple[Any, ...]) -> int:
    return int(store.scalar(sc, sql, params) or 0)


def analytics(
    sc: sqlite3.Connection, ch: dict[str, Any], days: Any = 28
) -> dict[str, Any]:
    """The channel's last ``days`` days: totals (with the period before for comparison), views and watch time per
    day, and every video with its views, watch time, average watch, likes, comments, ads and earnings (best first)."""
    span = _period(days)
    day_list = _days(span)
    since, until = day_list[0], day_list[-1]
    before = _days(span, end_offset=span)
    since_at = f"{since}T00:00:00"
    channel_id = ch["id"]
    mine = "SELECT id FROM vi_videos WHERE channel_id = ?"

    per_day_views = {
        item["day"]: item["n"]
        for item in rows(
            sc,
            f"SELECT day, COUNT(*) AS n FROM vi_views WHERE video_id IN ({mine}) AND day >= ? GROUP BY day",  # noqa: S608
            (channel_id, since),
        )
    }
    per_day_watch = {
        item["day"]: float(item["s"] or 0)
        for item in rows(
            sc,
            f"SELECT day, SUM(seconds) AS s FROM vi_watch_days WHERE video_id IN ({mine}) AND day >= ? GROUP BY day",  # noqa: S608
            (channel_id, since),
        )
    }
    per_day_earned = {
        item["day"]: int(item["m"] or 0)
        for item in rows(
            sc,
            """SELECT day, SUM(creator_micros) AS m FROM ad_impressions WHERE channel_id = ? AND day >= ?
            AND earning_status IN ('estimated', 'confirmed') GROUP BY day""",
            (channel_id, since),
        )
    }
    views = sum(per_day_views.values())
    watch_seconds = sum(per_day_watch.values())
    previous_views = _int(
        sc,
        f"SELECT COUNT(*) FROM vi_views WHERE video_id IN ({mine}) AND day >= ? AND day <= ?",  # noqa: S608
        (channel_id, before[0], before[-1]),
    )
    public_views = _int(
        sc,
        f"SELECT COUNT(*) FROM vi_views WHERE video_id IN ({mine}) AND day >= ? AND viewer_key LIKE 'visitor:%'",  # noqa: S608
        (channel_id, since),
    )
    likes = _int(
        sc,
        f"SELECT COUNT(*) FROM vi_likes WHERE video_id IN ({mine}) AND viewer_id != ? AND at >= ?",  # noqa: S608
        (channel_id, channel_id, since_at),
    )
    comments = _int(
        sc,
        f"""SELECT COUNT(*) FROM vi_comments WHERE video_id IN ({mine}) AND author_id != ? AND created_at >= ?
        AND deleted_at IS NULL""",  # noqa: S608
        (channel_id, channel_id, since_at),
    )
    new_subscribers = _int(
        sc,
        "SELECT COUNT(*) FROM vi_subscriptions WHERE channel_id = ? AND at >= ?",
        (channel_id, since_at),
    )
    support = rows(
        sc,
        """SELECT currency, SUM(amount_cents) AS cents, COUNT(*) AS n FROM vi_tips WHERE channel_id = ?
        AND status = 'received' AND decided_at >= ? GROUP BY currency ORDER BY cents DESC""",
        (channel_id, since_at),
    )
    memberships = rows(
        sc,
        """SELECT currency, SUM(amount_cents) AS cents, COUNT(*) AS n FROM vi_member_payments WHERE channel_id = ?
        AND status = 'received' AND decided_at >= ? GROUP BY currency ORDER BY cents DESC""",
        (channel_id, since_at),
    )
    brand_deals = rows(
        sc,
        """SELECT currency, SUM(amount_cents) AS cents, COUNT(*) AS n FROM vi_deals WHERE channel_id = ?
        AND status = 'paid' AND paid_at >= ? GROUP BY currency ORDER BY cents DESC""",
        (channel_id, since_at),
    )

    videos = []
    for video in rows(
        sc,
        "SELECT * FROM vi_videos WHERE channel_id = ? AND deleted_at IS NULL ORDER BY created_at DESC LIMIT 500",
        (channel_id,),
    ):
        video_id = video["id"]
        v_views = _int(
            sc,
            "SELECT COUNT(*) FROM vi_views WHERE video_id = ? AND day >= ?",
            (video_id, since),
        )
        v_watch = float(
            store.scalar(
                sc,
                "SELECT COALESCE(SUM(seconds), 0) FROM vi_watch_days WHERE video_id = ? AND day >= ?",
                (video_id, since),
            )
            or 0
        )
        ads = (
            row(
                sc,
                """SELECT COUNT(*) AS impressions, COALESCE(SUM(click_counted), 0) AS clicks,
                COALESCE(SUM(creator_micros), 0) AS earned FROM ad_impressions WHERE video_id = ? AND channel_id = ?
                AND day >= ? AND status = 'counted' AND earning_status IN ('estimated', 'confirmed')""",
                (video_id, channel_id, since),
            )
            or {}
        )
        earned = int(ads.get("earned") or 0)
        average = v_watch / v_views if v_views else 0.0
        duration = float(video["duration"] or 0)
        videos.append(
            {
                "id": video_id,
                "title": video["title"],
                "kind": video["kind"],
                "visibility": video["visibility"],
                "created_at": video["created_at"],
                "duration": duration,
                "views": v_views,
                "watch_seconds": round(v_watch, 1),
                "average_seconds": round(average, 1),
                "average_percent": round(min(100.0, 100 * average / duration), 1)
                if duration
                else 0.0,
                "likes": _int(
                    sc,
                    "SELECT COUNT(*) FROM vi_likes WHERE video_id = ? AND viewer_id != ? AND at >= ?",
                    (video_id, channel_id, since_at),
                ),
                "comments": _int(
                    sc,
                    """SELECT COUNT(*) FROM vi_comments WHERE video_id = ? AND author_id != ? AND created_at >= ?
                    AND deleted_at IS NULL""",
                    (video_id, channel_id, since_at),
                ),
                "impressions": int(ads.get("impressions") or 0),
                "clicks": int(ads.get("clicks") or 0),
                "earned_micros": earned,
                "rpm_micros": earned * 1000 // v_views if v_views else 0,
            }
        )
    videos.sort(
        key=lambda item: (item["earned_micros"], item["views"], item["watch_seconds"]),
        reverse=True,
    )
    earning = [item for item in videos if item["visibility"] != "private"]
    top = (
        videos[0]
        if videos and (videos[0]["earned_micros"] or videos[0]["views"])
        else None
    )
    lowest = (
        min(earning, key=lambda item: (item["earned_micros"], item["views"]))
        if len(earning) > 1
        else None
    )
    most_watched = (
        max(videos, key=lambda item: item["watch_seconds"]) if videos else None
    )
    earned_total = sum(per_day_earned.values())
    return {
        "days": span,
        "from": since,
        "to": until,
        "totals": {
            "views": views,
            "previous_views": previous_views,
            "public_views": public_views,
            "watch_seconds": round(watch_seconds, 1),
            "average_seconds": round(watch_seconds / views, 1) if views else 0.0,
            "likes": likes,
            "comments": comments,
            "new_subscribers": new_subscribers,
            "subscribers": service.subscriber_count(sc, channel_id),
            "earned_micros": earned_total,
            "rpm_micros": earned_total * 1000 // views if views else 0,
            "support": support,
            "memberships": memberships,
            "deals": brand_deals,
        },
        "daily": [
            {
                "day": day,
                "views": per_day_views.get(day, 0),
                "watch_seconds": round(per_day_watch.get(day, 0.0), 1),
                "earned_micros": per_day_earned.get(day, 0),
            }
            for day in day_list
        ],
        "videos": videos,
        "top_earner": top["id"] if top and top["earned_micros"] > 0 else None,
        "top_viewed": max(videos, key=lambda item: item["views"])["id"]
        if videos and views
        else None,
        "lowest_earner": lowest["id"]
        if lowest and top and top["earned_micros"] > 0 and lowest is not top
        else None,
        "most_watched": most_watched["id"]
        if most_watched and most_watched["watch_seconds"] > 0
        else None,
    }


CSV_COLUMNS = (
    ("title", "Video"),
    ("kind", "Kind"),
    ("created_at", "Uploaded"),
    ("views", "Views"),
    ("watch_minutes", "Watch time (minutes)"),
    ("average_seconds", "Average watch (seconds)"),
    ("average_percent", "Average watched (%)"),
    ("likes", "Likes"),
    ("comments", "Comments"),
    ("impressions", "Ads shown"),
    ("clicks", "Ad clicks"),
    ("earned", "Earned (USD)"),
    ("rpm", "Per 1,000 views (USD)"),
)


def _dollars(micros: int) -> str:
    return f"{micros / 1_000_000:.4f}".rstrip("0").rstrip(".") if micros else "0"


def _cell(value: Any) -> Any:
    """A spreadsheet never runs a title as a formula."""
    if isinstance(value, str) and value[:1] in ("=", "+", "-", "@", "\t", "\r"):
        return "'" + value
    return value


def analytics_csv(sc: sqlite3.Connection, ch: dict[str, Any], days: Any = 28) -> bytes:
    data = analytics(sc, ch, days)
    out = io.StringIO()
    writer = csv.writer(out)
    writer.writerow([label for _, label in CSV_COLUMNS])
    for item in data["videos"]:
        values = {
            **item,
            "created_at": str(item["created_at"])[:10],
            "watch_minutes": round(item["watch_seconds"] / 60, 1),
            "earned": _dollars(item["earned_micros"]),
            "rpm": _dollars(item["rpm_micros"]),
        }
        writer.writerow([_cell(values[key]) for key, _ in CSV_COLUMNS])
    return out.getvalue().encode("utf-8-sig")


# ---------------------------------------------------------------------------
# support from viewers
# ---------------------------------------------------------------------------

UPI = r"^[A-Za-z0-9._-]{2,64}@[A-Za-z][A-Za-z0-9.-]{1,63}$"


def _support_row(sc: sqlite3.Connection, channel_id: str) -> dict[str, Any]:
    found = row(sc, "SELECT * FROM vi_support WHERE channel_id = ?", (channel_id,))
    return found or {
        "channel_id": channel_id,
        "enabled": 0,
        "link": "",
        "upi": "",
        "note": "",
        "currency": "USD",
    }


def support_settings(sc: sqlite3.Connection, ch: dict[str, Any]) -> dict[str, Any]:
    found = _support_row(sc, ch["id"])
    return {
        "enabled": bool(found["enabled"]),
        "link": found["link"],
        "upi": found["upi"],
        "note": found["note"],
        "currency": found["currency"],
        "can_enable": service.adult(ch)
        and bool(ch.get("terms_accepted_at"))
        and not ch.get("suspended_at"),
        "currencies": list(kit.CURRENCIES),
    }


def save_support(
    sc: sqlite3.Connection, ch: dict[str, Any], raw: dict[str, Any]
) -> dict[str, Any]:
    """Turn support on or off and say where viewers pay (a payment page, a UPI id, or both)."""
    service._active(ch)
    if not service.adult(ch):
        raise PermissionError("Earning money is for adults.")
    current = _support_row(sc, ch["id"])
    merged = {**current, **raw}
    link = kit.url(merged.get("link"), "your payment page")
    if link and not link.lower().startswith("https://"):
        raise ValueError(
            "Your payment page must start with https:// so payments are safe."
        )
    upi = kit.text(merged.get("upi"), "your UPI id", max_len=130)
    if upi and not re.fullmatch(UPI, upi):
        raise ValueError("A UPI id looks like name@bank.")
    note = kit.text(
        merged.get("note"), "a note for your supporters", max_len=300, multiline=True
    )
    currency = kit.currency(merged.get("currency"), default="USD")
    enabled = kit.flag(merged.get("enabled"))
    if enabled:
        if not ch.get("terms_accepted_at"):
            raise PermissionError(
                "Accept the creator terms first (on the Earnings page)."
            )
        if not (link or upi):
            raise ValueError(
                "Add your payment page or your UPI id, so supporters know where to pay."
            )
    sc.execute(
        """INSERT INTO vi_support (channel_id, enabled, link, upi, note, currency, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(channel_id) DO UPDATE SET enabled = excluded.enabled, link = excluded.link, upi = excluded.upi,
        note = excluded.note, currency = excluded.currency, updated_at = excluded.updated_at""",
        (ch["id"], 1 if enabled else 0, link, upi, note, currency, stamp()),
    )
    sc.commit()
    return support_settings(sc, service.channel(sc, ch["id"]))


def _open_for_support(
    sc: sqlite3.Connection, channel_id: str
) -> tuple[dict[str, Any], dict[str, Any]] | None:
    target = row(sc, "SELECT * FROM vi_channels WHERE id = ?", (channel_id,))
    if (
        not target
        or target["suspended_at"]
        or not service.adult(target)
        or not target["terms_accepted_at"]
    ):
        return None
    settings = _support_row(sc, channel_id)
    if not settings["enabled"] or not (settings["link"] or settings["upi"]):
        return None
    return target, settings


def support_for_viewer(
    sc: sqlite3.Connection, me_: dict[str, Any], channel_id: str
) -> dict[str, Any] | None:
    """What a viewer sees under a video or on a channel: whether they can support it, and the creator's note."""
    if channel_id == me_["id"]:
        return None
    found = _open_for_support(sc, channel_id)
    if not found:
        return None
    _, settings = found
    return {
        "currency": settings["currency"],
        "note": settings["note"],
        "can_give": service.adult(me_) and not me_.get("suspended_at"),
        "supporters": supporters(sc, channel_id),
    }


def supporters(
    sc: sqlite3.Connection, channel_id: str, limit: int = 12
) -> list[dict[str, Any]]:
    """Names of people whose support arrived and who chose to be thanked by name (no amounts)."""
    out: list[dict[str, Any]] = []
    for item in rows(
        sc,
        """SELECT c.id, c.name, c.avatar, MAX(t.decided_at) AS last FROM vi_tips t JOIN vi_channels c ON c.id = t.supporter_id
        WHERE t.channel_id = ? AND t.status = 'received' AND t.show_name = 1 AND c.suspended_at IS NULL
        GROUP BY c.id ORDER BY last DESC LIMIT ?""",
        (channel_id, limit),
    ):
        out.append(service.mini(item))
    return out


def _reference(tip_id: str) -> str:
    return f"TIP-{tip_id[:8].upper()}"


def tip_view(
    sc: sqlite3.Connection, item: dict[str, Any], *, for_creator: bool
) -> dict[str, Any]:
    other_id = item["supporter_id"] if for_creator else item["channel_id"]
    other = (
        row(sc, "SELECT * FROM vi_channels WHERE id = ?", (other_id,))
        if other_id
        else None
    )
    video = row(
        sc,
        "SELECT id, title FROM vi_videos WHERE id = ? AND deleted_at IS NULL",
        (item["video_id"],),
    )
    out = {
        "id": item["id"],
        "amount_cents": item["amount_cents"],
        "currency": item["currency"],
        "amount": kit.fmt_money(item["amount_cents"], item["currency"]),
        "message": item["message"],
        "show_name": bool(item["show_name"]),
        "reference": item["reference"],
        "status": item["status"],
        "created_at": item["created_at"],
        "decided_at": item["decided_at"],
        "video": {"id": video["id"], "title": video["title"]} if video else None,
    }
    if for_creator:
        out["supporter"] = (
            service.mini(other)
            if other
            else {"id": "", "name": "A former member", "avatar": "🙂"}
        )
    else:
        out["channel"] = (
            service.mini(other)
            if other
            else {"id": "", "name": "Removed channel", "avatar": "🙂"}
        )
    return out


def pledge(
    sc: sqlite3.Connection, me_: dict[str, Any], channel_id: str, raw: dict[str, Any]
) -> dict[str, Any]:
    """A viewer pledges to support a channel; the answer says where and how to pay (with the reference)."""
    service._active(me_)
    if not service.adult(me_):
        raise PermissionError("Supporting creators with money is for adults.")
    channel_id = store.check_id(channel_id, "channel")
    if channel_id == me_["id"]:
        raise ValueError("You cannot support your own channel.")
    found = _open_for_support(sc, channel_id)
    if not found:
        raise LookupError("This channel does not take support right now.")
    target, settings = found
    amount = kit.cents_value(raw.get("amount_cents"), "the amount")
    if not TIP_MIN_CENTS <= amount <= TIP_MAX_CENTS:
        low = kit.fmt_money(TIP_MIN_CENTS, settings["currency"])
        high = kit.fmt_money(TIP_MAX_CENTS, settings["currency"])
        raise ValueError(f"Choose an amount from {low} to {high}.")
    message = service.filtered(
        sc, kit.text(raw.get("message"), "a message", max_len=200, multiline=True)
    )
    video_id = str(raw.get("video_id") or "")
    if video_id:
        video = service.visible_video(sc, me_, video_id)
        if video["channel_id"] != channel_id:
            raise ValueError("That video is not on this channel.")
    service.rate(sc, me_["id"], "tip")
    tip_id = new_id()
    sc.execute(
        """INSERT INTO vi_tips (id, channel_id, supporter_id, video_id, amount_cents, currency, message, show_name, reference,
        status, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'pledged', ?)""",
        (
            tip_id,
            channel_id,
            me_["id"],
            video_id,
            amount,
            settings["currency"],
            message,
            0 if "show_name" in raw and not kit.flag(raw.get("show_name")) else 1,
            _reference(tip_id),
            stamp(),
        ),
    )
    service.notify(
        sc,
        channel_id,
        "support",
        f"{me_['name']} is sending you {kit.fmt_money(amount, settings['currency'])} to support your channel. "
        "Mark it received on the Earnings page when it arrives.",
        ref_type="tip",
        ref_id=tip_id,
    )
    sc.commit()
    item = row(sc, "SELECT * FROM vi_tips WHERE id = ?", (tip_id,)) or {}
    return {
        "tip": tip_view(sc, item, for_creator=False),
        "pay": {
            "link": settings["link"],
            "upi": settings["upi"],
            "name": target["name"],
            "note": settings["note"],
            "upi_link": upi_link(
                settings["upi"],
                target["name"],
                amount,
                settings["currency"],
                _reference(tip_id),
            ),
        },
    }


def my_tips(sc: sqlite3.Connection, me_: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        tip_view(sc, item, for_creator=False)
        for item in rows(
            sc,
            "SELECT * FROM vi_tips WHERE supporter_id = ? ORDER BY created_at DESC LIMIT 100",
            (me_["id"],),
        )
    ]


def cancel_tip(
    sc: sqlite3.Connection, me_: dict[str, Any], tip_id: str
) -> dict[str, Any]:
    found = row(
        sc,
        "SELECT * FROM vi_tips WHERE id = ? AND supporter_id = ?",
        (store.check_id(tip_id, "pledge"), me_["id"]),
    )
    if not found:
        raise LookupError("That pledge was not found.")
    if found["status"] != "pledged":
        raise ValueError(
            "Only a pledge that has not been received yet can be withdrawn."
        )
    sc.execute(
        "UPDATE vi_tips SET status = 'cancelled', decided_at = ? WHERE id = ?",
        (stamp(), found["id"]),
    )
    sc.commit()
    return tip_view(sc, {**found, "status": "cancelled"}, for_creator=False)


def received_tips(
    sc: sqlite3.Connection, ch: dict[str, Any], status: str = ""
) -> dict[str, Any]:
    if status:
        kit.choice(status, "the status", TIP_STATUSES)
        found = rows(
            sc,
            "SELECT * FROM vi_tips WHERE channel_id = ? AND status = ? ORDER BY created_at DESC LIMIT 200",
            (ch["id"], status),
        )
    else:
        found = rows(
            sc,
            "SELECT * FROM vi_tips WHERE channel_id = ? ORDER BY created_at DESC LIMIT 200",
            (ch["id"],),
        )
    totals = rows(
        sc,
        """SELECT currency, SUM(amount_cents) AS cents, COUNT(*) AS n FROM vi_tips WHERE channel_id = ? AND status = 'received'
        GROUP BY currency ORDER BY cents DESC""",
        (ch["id"],),
    )
    return {
        "tips": [tip_view(sc, item, for_creator=True) for item in found],
        "waiting": _int(
            sc,
            "SELECT COUNT(*) FROM vi_tips WHERE channel_id = ? AND status = 'pledged'",
            (ch["id"],),
        ),
        "totals": [
            {**item, "amount": kit.fmt_money(int(item["cents"] or 0), item["currency"])}
            for item in totals
        ],
    }


def decide_tip(
    sc: sqlite3.Connection, ch: dict[str, Any], tip_id: str, action: str
) -> dict[str, Any]:
    """``received`` (the money arrived) or ``not_received``. A pledge marked received by mistake can still be
    marked not received, and one marked not received by mistake can still be marked received; the supporter is
    thanked when it is received."""
    found = row(
        sc,
        "SELECT * FROM vi_tips WHERE id = ? AND channel_id = ?",
        (store.check_id(tip_id, "pledge"), ch["id"]),
    )
    if not found:
        raise LookupError("That pledge was not found.")
    action = kit.choice(action, "the answer", ("received", "not_received"))
    allowed = {
        "received": ("pledged", "declined"),
        "not_received": ("pledged", "received"),
    }[action]
    if found["status"] not in allowed:
        raise ValueError(
            "The supporter withdrew this pledge."
            if found["status"] == "cancelled"
            else "This pledge was already answered."
        )
    status = "received" if action == "received" else "declined"
    sc.execute(
        "UPDATE vi_tips SET status = ?, decided_at = ? WHERE id = ?",
        (status, stamp(), found["id"]),
    )
    if status == "received":
        service.notify(
            sc,
            found["supporter_id"],
            "thanks",
            f"{ch['name']} received your support of {kit.fmt_money(found['amount_cents'], found['currency'])}. Thank you!",
            ref_type="channel",
            ref_id=ch["id"],
        )
    sc.commit()
    return tip_view(
        sc,
        row(sc, "SELECT * FROM vi_tips WHERE id = ?", (found["id"],)) or found,
        for_creator=True,
    )


def waiting_count(sc: sqlite3.Connection, channel_id: str) -> int:
    return _int(
        sc,
        "SELECT COUNT(*) FROM vi_tips WHERE channel_id = ? AND status = 'pledged'",
        (channel_id,),
    )


# ---------------------------------------------------------------------------
# paying a creator by UPI from a phone
# ---------------------------------------------------------------------------


def upi_link(upi: str, name: str, amount_cents: int, currency: str, note: str) -> str:
    """A ``upi://pay`` address that opens a UPI app with the creator's id, name and the reference filled in (and the
    amount, when it is in rupees: UPI pays only in rupees). "" without a UPI id."""
    if not upi:
        return ""
    query = {"pa": upi, "pn": name[:50], "tn": note[:50]}
    if currency == "INR" and amount_cents > 0:
        query.update({"am": f"{amount_cents / 100:.2f}", "cu": "INR"})
    return "upi://pay?" + urlencode(query, safe="@", quote_via=quote)


# ---------------------------------------------------------------------------
# all income: ads, support, memberships and brand deals, by month, with a log and this month's outlook
# ---------------------------------------------------------------------------

SOURCES = ("ads", "support", "memberships", "deals")
SOURCE_LABELS = {
    "ads": "Ads on your videos",
    "support": "Support from viewers",
    "memberships": "Memberships",
    "deals": "Brand deals",
}


def _months_back(count: int) -> list[str]:
    moment = store.utcnow()
    year, month = moment.year, moment.month
    out = []
    for _ in range(count):
        out.append(f"{year:04d}-{month:02d}")
        month -= 1
        if month == 0:
            year, month = year - 1, 12
    return out


def _add(bucket: dict[str, int], currency: str, cents: int) -> None:
    if cents:
        bucket[currency] = bucket.get(currency, 0) + int(cents)


def _money_list(bucket: dict[str, int]) -> list[dict[str, Any]]:
    return [
        {"currency": code, "cents": cents, "amount": kit.fmt_money(cents, code)}
        for code, cents in sorted(bucket.items(), key=lambda item: -item[1])
    ]


def _log_line(
    at: Any,
    source: str,
    who: Any,
    cents: Any,
    currency: str,
    *,
    estimated: bool = False,
) -> dict[str, Any]:
    return {
        "at": str(at or ""),
        "source": source,
        "label": SOURCE_LABELS[source],
        "from": str(who or "A former member"),
        "cents": int(cents or 0),
        "currency": currency,
        "amount": kit.fmt_money(int(cents or 0), currency),
        "estimated": estimated,
    }


def income(
    sc: sqlite3.Connection, ch: dict[str, Any], months: int = 12
) -> dict[str, Any]:
    """Every way the channel earned in the last ``months`` months: per month and source (each currency on its own,
    nothing is converted), the totals, the best and lowest month and the top source (when it is all one currency), a
    log of the money (ads one line a day), and this month's outlook."""
    month_list = _months_back(months)
    first = f"{month_list[-1]}-01"
    this_month = store.month()
    table: dict[str, dict[str, dict[str, int]]] = {
        month: {source: {} for source in SOURCES} for month in month_list
    }
    log: list[dict[str, Any]] = []
    channel_id = ch["id"]

    def count(source: str, at: Any, cents: Any, currency: str) -> None:
        month = str(at or "")[:7]
        if month in table:
            _add(table[month][source], currency, int(cents or 0))

    for item in rows(
        sc,
        """SELECT day, SUM(creator_micros) AS m FROM ad_impressions WHERE channel_id = ? AND day >= ?
        AND earning_status IN ('estimated', 'confirmed') AND creator_micros > 0 GROUP BY day""",
        (channel_id, first),
    ):
        cents = int(item["m"] or 0) // 10_000
        if not cents:
            continue
        count("ads", item["day"], cents, "USD")
        log.append(
            _log_line(
                f"{item['day']}T23:59:59Z",
                "ads",
                "Ads on your videos",
                cents,
                "USD",
                estimated=str(item["day"])[:7] == this_month,
            )
        )
    for item in rows(
        sc,
        """SELECT t.amount_cents, t.currency, t.decided_at, c.name FROM vi_tips t
        LEFT JOIN vi_channels c ON c.id = t.supporter_id
        WHERE t.channel_id = ? AND t.status = 'received' AND t.decided_at >= ?""",
        (channel_id, first),
    ):
        count("support", item["decided_at"], item["amount_cents"], item["currency"])
        log.append(
            _log_line(
                item["decided_at"],
                "support",
                item["name"],
                item["amount_cents"],
                item["currency"],
            )
        )
    for item in rows(
        sc,
        """SELECT p.amount_cents, p.currency, p.decided_at, c.name FROM vi_member_payments p
        LEFT JOIN vi_channels c ON c.id = p.member_id
        WHERE p.channel_id = ? AND p.status = 'received' AND p.decided_at >= ?""",
        (channel_id, first),
    ):
        count("memberships", item["decided_at"], item["amount_cents"], item["currency"])
        log.append(
            _log_line(
                item["decided_at"],
                "memberships",
                item["name"],
                item["amount_cents"],
                item["currency"],
            )
        )
    for item in rows(
        sc,
        "SELECT brand, amount_cents, currency, paid_at FROM vi_deals WHERE channel_id = ? AND status = 'paid' AND paid_at >= ?",
        (channel_id, first),
    ):
        count("deals", item["paid_at"], item["amount_cents"], item["currency"])
        log.append(
            _log_line(
                item["paid_at"],
                "deals",
                item["brand"],
                item["amount_cents"],
                item["currency"],
            )
        )

    by_source: dict[str, dict[str, int]] = {source: {} for source in SOURCES}
    grand: dict[str, int] = {}
    out_months = []
    for month in month_list:
        total: dict[str, int] = {}
        for source in SOURCES:
            for code, cents in table[month][source].items():
                _add(total, code, cents)
                _add(by_source[source], code, cents)
                _add(grand, code, cents)
        out_months.append(
            {
                "month": month,
                **{source: _money_list(table[month][source]) for source in SOURCES},
                "total": _money_list(total),
                "total_cents": sum(total.values()),
            }
        )
    one_currency = len(grand) == 1
    earning = [item for item in out_months if item["total_cents"]]
    best = (
        max(earning, key=lambda item: item["total_cents"])
        if one_currency and earning
        else None
    )
    lowest = (
        min(earning, key=lambda item: item["total_cents"])
        if one_currency and len(earning) > 1
        else None
    )
    top_source = (
        max(SOURCES, key=lambda source: sum(by_source[source].values()))
        if one_currency and grand
        else None
    )
    log.sort(key=lambda item: item["at"], reverse=True)
    return {
        "months": out_months,
        "by_source": {source: _money_list(by_source[source]) for source in SOURCES},
        "total": _money_list(grand),
        "best_month": best["month"] if best else None,
        "lowest_month": lowest["month"] if lowest and lowest is not best else None,
        "top_source": top_source,
        "labels": SOURCE_LABELS,
        "log": log[:300],
        "outlook": outlook(sc, ch),
        "ways": ways(sc, ch),
    }


def ways(sc: sqlite3.Connection, ch: dict[str, Any]) -> dict[str, bool]:
    """Which ways to earn the channel has turned on (the Earnings overview's checklist)."""
    support = row(
        sc, "SELECT enabled FROM vi_support WHERE channel_id = ?", (ch["id"],)
    )
    plan = row(
        sc, "SELECT enabled FROM vi_member_plans WHERE channel_id = ?", (ch["id"],)
    )
    return {
        "terms": bool(ch.get("terms_accepted_at")),
        "ads": bool(ch.get("monetized")),
        "support": bool(support and support["enabled"]),
        "memberships": bool(plan and plan["enabled"]),
        "deals": bool(
            _int(sc, "SELECT COUNT(*) FROM vi_deals WHERE channel_id = ?", (ch["id"],))
        ),
    }


def outlook(sc: sqlite3.Connection, ch: dict[str, Any]) -> dict[str, Any]:
    """This month so far and where it is heading: ads at this month's daily pace (an estimate, not a promise),
    members' monthly payments, and brand deals agreed or delivered that are due by the month's end."""
    now = store.utcnow()
    month = store.month(now)
    days_in_month = (
        (now.replace(day=28) + timedelta(days=4)).replace(day=1) - timedelta(days=1)
    ).day
    elapsed = now.day
    ads_so_far = _int(
        sc,
        """SELECT COALESCE(SUM(creator_micros), 0) FROM ad_impressions WHERE channel_id = ? AND month = ?
        AND earning_status IN ('estimated', 'confirmed')""",
        (ch["id"], month),
    )
    active = _int(
        sc,
        "SELECT COUNT(*) FROM vi_members WHERE channel_id = ? AND paid_until > ?",
        (ch["id"], stamp()),
    )
    month_end = f"{month}-{days_in_month:02d}T23:59:59.999999Z"
    renewing = _int(
        sc,
        "SELECT COUNT(*) FROM vi_members WHERE channel_id = ? AND paid_until > ? AND paid_until <= ?",
        (ch["id"], stamp(), month_end),
    )
    deals_due: dict[str, int] = {}
    for item in rows(
        sc,
        """SELECT currency, SUM(amount_cents) AS cents FROM vi_deals WHERE channel_id = ?
        AND status IN ('agreed', 'delivered') AND (due_on = '' OR due_on <= ?) GROUP BY currency""",
        (ch["id"], month_end[:10]),
    ):
        _add(deals_due, item["currency"], int(item["cents"] or 0))
    from aira.suite.videos import members  # members imports this module

    # by what each member paid (not today's price: a plan's price or currency can change after they paid)
    monthly = members.money_list(members.monthly_from_members(sc, ch["id"]))
    return {
        "month": month,
        "days_elapsed": elapsed,
        "days_in_month": days_in_month,
        "ads_so_far_micros": ads_so_far,
        "ads_projected_micros": ads_so_far * days_in_month // max(1, elapsed),
        "members_active": active,
        "members_monthly": {
            "cents": monthly[0]["cents"] if len(monthly) == 1 else 0,
            "currency": monthly[0]["currency"] if monthly else "USD",
            "amount": " + ".join(item["amount"] for item in monthly)
            or kit.fmt_money(0, "USD"),
            "by_currency": monthly,
        },
        "members_renewing": renewing,
        "deals_due": _money_list(deals_due),
    }


INCOME_CSV = (
    ("date", "Date"),
    ("label", "Source"),
    ("from", "From"),
    ("amount", "Amount"),
    ("currency", "Currency"),
    ("note", "Note"),
)


def income_csv(sc: sqlite3.Connection, ch: dict[str, Any]) -> bytes:
    """The income log (the last 12 months) as a spreadsheet."""
    out = io.StringIO()
    writer = csv.writer(out)
    writer.writerow([label for _, label in INCOME_CSV])
    for item in income(sc, ch)["log"]:
        values = {
            **item,
            "date": item["at"][:10],
            "amount": f"{item['cents'] / 100:.2f}",
            "note": "estimated until the month closes" if item["estimated"] else "",
        }
        writer.writerow([_cell(values[key]) for key, _ in INCOME_CSV])
    return out.getvalue().encode("utf-8-sig")


# ---------------------------------------------------------------------------
# the creator's own videos as a spreadsheet
# ---------------------------------------------------------------------------

VIDEOS_CSV = (
    ("title", "Title"),
    ("kind", "Kind"),
    ("visibility", "Who can watch"),
    ("rating", "Suitable for"),
    ("members_only", "Members only"),
    ("paid_promotion", "Paid promotion"),
    ("created_at", "Out on (UTC)"),
    ("duration", "Length (seconds)"),
    ("views", "Views"),
    ("likes", "Likes"),
    ("comments", "Comments"),
    ("id", "Video id"),
)


def videos_csv(
    sc: sqlite3.Connection, ch: dict[str, Any], q: str = "", show: str = ""
) -> bytes:
    out = io.StringIO()
    writer = csv.writer(out)
    writer.writerow([label for _, label in VIDEOS_CSV])
    for item in service.my_videos(sc, ch, q, show):
        values = {
            **item,
            "created_at": str(item["created_at"])[:16].replace("T", " "),
            "duration": round(float(item["duration"] or 0)),
            "members_only": "yes" if item["members_only"] else "",
            "paid_promotion": "yes" if item["paid_promotion"] else "",
        }
        writer.writerow([_cell(values[key]) for key, _ in VIDEOS_CSV])
    return out.getvalue().encode("utf-8-sig")
