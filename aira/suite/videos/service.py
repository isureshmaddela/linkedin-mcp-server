"""Videos' rules: who sees which video, how views are counted, what a channel may do, and how reports and
copyright notices are handled.

Everything here takes ``sc`` (a connection to the platform-wide Videos database, db.py).

Who sees what, in one place:
* A **public** video is listed for everyone whose age allows its rating; an **unlisted** one plays for anyone who has
  it, but is never listed; a **private** one only for its channel.
* **Ratings**: ``everyone``, ``teen`` and ``mature``. Children see only ``everyone``; teens, people in easy mode and
  anyone who turned on restricted mode see ``everyone`` and ``teen``; adults see everything.
* A video hidden by a moderator, taken down after a copyright notice, or from a suspended channel plays for nobody but
  its channel (who sees why).
* A **scheduled** video (its ``created_at``, the moment it goes out, is still to come) is like a private one until then.
* A **members-only** video is listed like any other, but plays only for the channel's members (whose membership is paid
  up) and its channel; everyone else sees its picture, title and how to join.
* Children upload only private videos and do not comment; nobody's own views count.

A **view** counts once per viewer per day after a meaningful watch: 30 seconds, or 30% of a shorter video, measured
on the server from the first progress report (a browser cannot claim more time than has passed).

**Popular** is ``(views in the last 7 days + 2 x likes + 3 x comments + 1) / (hours since upload + 2) ^ 1.2``: newer
and more watched first, nothing else (no paid placement, no personal profiling).
"""

from __future__ import annotations

import hashlib
import re
import sqlite3
from collections.abc import Callable, Iterable
from datetime import timedelta
from typing import Any

from aira.suite import kit, life
from aira.suite.videos import media, store
from aira.suite.videos.store import new_id, row, rows, stamp

KINDS = ("video", "reel", "short")
KIND_LABELS = {"video": "Video", "reel": "Reel", "short": "Short film"}
RATINGS = ("everyone", "teen", "mature")
VISIBILITIES = ("public", "unlisted", "private")
CATEGORIES = (
    "film",
    "music",
    "comedy",
    "education",
    "gaming",
    "sports",
    "news",
    "howto",
    "travel",
    "food",
    "family",
    "animals",
    "science",
    "other",
)
GENRES = (
    "drama",
    "comedy",
    "documentary",
    "animation",
    "horror",
    "thriller",
    "romance",
    "scifi",
    "experimental",
    "other",
)
REPORT_TARGETS = ("video", "comment", "channel")
REPORT_REASONS = (
    "Violent or dangerous",
    "Hateful or bullying",
    "Sexual content",
    "Not right for its rating",
    "Spam or a scam",
    "Something else",
)
REEL_SECONDS = 90
SHORT_FILM_SECONDS = 60 * 60
VIEW_SECONDS = 30
VIEW_SHARE = 0.3
STRIKES_TO_BLOCK = 3
STRIKE_DAYS = 90
LIMITS = {
    "upload": (30, "day"),
    "comment": (60, "hour"),
    "report": (30, "hour"),
    "copyright": (10, "hour"),
    "tip": (20, "day"),
}
DEFAULT_BLOCKLIST = ("fuck", "shit", "bitch", "bastard", "cunt", "motherfucker")
LANGUAGE = re.compile(r"^[a-z]{2}$")
COUNTRY = re.compile(r"^[A-Z]{2}$")

# The single-user build says which account owns the computer (its first account, whose household owner looks
# after Videos and Ads for everyone on it). None: a household owner of any account.
_computer_owner: Callable[[dict[str, Any]], bool] | None = None


def set_computer_owner(check: Callable[[dict[str, Any]], bool] | None) -> None:
    global _computer_owner
    _computer_owner = check


class ChannelSuspended(PermissionError):
    code = "channel_suspended"


# ---------------------------------------------------------------------------
# who is asking
# ---------------------------------------------------------------------------


def person_key(profile: dict[str, Any]) -> str:
    """One per person on the platform: the hosted member, or the household profile (ids are random)."""
    member = profile.get("member_ref")
    return f"m:{member}" if member else f"p:{profile['id']}"


def home_of(request: Any) -> str:
    user = getattr(request, "user", None) or {}
    return str(user.get("org_id") or f"local:{user.get('user_id') or ''}")


def identity(request: Any, connection: sqlite3.Connection) -> dict[str, Any]:
    profile = life.current(request, connection)
    user = getattr(request, "user", None) or {}
    hosted = bool(profile.get("member_ref"))
    owner = bool(profile.get("owner")) and not hosted
    operator = owner and (_computer_owner is None or bool(_computer_owner(user)))
    return {
        "key": person_key(profile),
        "profile": profile,
        "home": home_of(request),
        "owner": owner,  # moderates reports about their household
        "operator": operator,  # looks after Videos and Ads on this computer
        "hosted": hosted,
    }


def _channel(found: dict[str, Any] | None) -> dict[str, Any] | None:
    if not found:
        return None
    found["monetized"] = bool(found.get("monetized"))
    found["restricted"] = bool(found.get("restricted"))
    return found


def channel(sc: sqlite3.Connection, channel_id: str) -> dict[str, Any]:
    found = _channel(
        row(
            sc,
            "SELECT * FROM vi_channels WHERE id = ?",
            (store.check_id(channel_id, "channel"),),
        )
    )
    if not found:
        raise LookupError("That channel was not found.")
    return found


def me(sc: sqlite3.Connection, ident: dict[str, Any]) -> dict[str, Any]:
    """The person's channel, made on first use and kept in step with their profile's age group."""
    profile = ident["profile"]
    group = profile.get("age_group") or "adult"
    found = _channel(
        row(sc, "SELECT * FROM vi_channels WHERE person_key = ?", (ident["key"],))
    )
    moment = stamp()
    if not found:
        channel_id = new_id()
        sc.execute(
            """INSERT INTO vi_channels (id, person_key, home, name, avatar, age_group, language, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                channel_id,
                ident["key"],
                ident["home"],
                str(profile.get("name") or "Me")[:60],
                str(profile.get("emoji") or "🙂")[:8],
                group,
                (profile.get("prefs") or {}).get("language") or "en",
                moment,
                moment,
            ),
        )
        sc.commit()
        return channel(sc, channel_id)
    changes: dict[str, Any] = {}
    if found["age_group"] != group:
        changes["age_group"] = group
        if group not in ("adult", "senior"):
            changes["monetized"] = 0
    if found["home"] != ident["home"]:
        changes["home"] = ident["home"]
    if changes:
        store.update(sc, "vi_channels", found["id"], {**changes, "updated_at": moment})
        sc.commit()
        return channel(sc, found["id"])
    return found


def adult(ch: dict[str, Any]) -> bool:
    return ch.get("age_group") in ("adult", "senior")


def allowed_ratings(
    ch: dict[str, Any], prefs: dict[str, Any] | None = None
) -> tuple[str, ...]:
    group = ch.get("age_group") or "adult"
    if group == "child":
        return ("everyone",)
    if group == "teen" or ch.get("restricted") or (prefs or {}).get("easy_mode"):
        return ("everyone", "teen")
    return RATINGS


def viewer(sc: sqlite3.Connection, ident: dict[str, Any]) -> dict[str, Any]:
    """The person's channel plus what they may watch (``ratings``)."""
    ch = me(sc, ident)
    return {**ch, "ratings": allowed_ratings(ch, ident["profile"].get("prefs"))}


def mini(ch: dict[str, Any]) -> dict[str, Any]:
    return {"id": ch["id"], "name": ch["name"], "avatar": ch["avatar"]}


def update_channel(
    sc: sqlite3.Connection, ch: dict[str, Any], raw: dict[str, Any]
) -> dict[str, Any]:
    changes: dict[str, Any] = {}
    if "name" in raw:
        changes["name"] = kit.text(
            raw["name"], "a channel name", max_len=60, required=True
        )
    if "about" in raw:
        changes["about"] = kit.text(
            raw["about"], "about your channel", max_len=1000, multiline=True
        )
    if "avatar" in raw:
        changes["avatar"] = (
            kit.text(raw["avatar"] or "🙂", "a picture", max_len=8) or "🙂"
        )
    if "country" in raw:
        country = str(raw["country"] or "").strip().upper()
        if country and not COUNTRY.fullmatch(country):
            raise ValueError("Choose a country by its two-letter code, like IN or US.")
        changes["country"] = country
    if "language" in raw:
        language = str(raw["language"] or "").strip().lower() or "en"
        if not LANGUAGE.fullmatch(language):
            raise ValueError("Choose a language by its two-letter code, like en or te.")
        changes["language"] = language
    if "restricted" in raw:
        changes["restricted"] = 1 if kit.flag(raw["restricted"]) else 0
    if "payout_details" in raw:
        if not adult(ch):
            raise PermissionError("Earning money is for adults.")
        changes["payout_details"] = kit.text(
            raw["payout_details"],
            "where to send your money",
            max_len=500,
            multiline=True,
        )
    if changes:
        store.update(sc, "vi_channels", ch["id"], {**changes, "updated_at": stamp()})
        sc.commit()
    return channel(sc, ch["id"])


# ---------------------------------------------------------------------------
# safety: rate limits, the word filter, strikes
# ---------------------------------------------------------------------------


def rate(sc: sqlite3.Connection, key: str, kind: str) -> None:
    limit, span = LIMITS[kind]
    window = stamp()[:10] if span == "day" else stamp()[:13]
    found = row(
        sc, "SELECT window, count FROM vi_rate WHERE key = ? AND kind = ?", (key, kind)
    )
    current = found["count"] if found and found["window"] == window else 0
    if current >= limit:
        raise PermissionError(
            "You are doing that too often. Take a break and try again later."
        )
    sc.execute(
        """INSERT INTO vi_rate (key, kind, window, count) VALUES (?, ?, ?, 1)
        ON CONFLICT(key, kind) DO UPDATE SET count = CASE WHEN window = excluded.window THEN count + 1 ELSE 1 END,
        window = excluded.window""",
        (key, kind, window),
    )


def blocklist(sc: sqlite3.Connection) -> list[str]:
    return list(store.setting(sc, "blocklist", list(DEFAULT_BLOCKLIST)))


def filtered(sc: sqlite3.Connection, text: str) -> str:
    out = text
    for word in blocklist(sc):
        out = re.sub(rf"(?i)\b{re.escape(word)}\b", "*" * len(word), out)
    return out


def strikes(sc: sqlite3.Connection, channel_id: str) -> int:
    """Copyright takedowns that stand (not withdrawn or restored) in the last 90 days."""
    return int(
        store.scalar(
            sc,
            "SELECT COUNT(*) FROM vi_copyright WHERE channel_id = ? AND status IN ('taken_down', 'upheld', 'counter_filed') AND updated_at > ?",
            (channel_id, store.later(-24 * STRIKE_DAYS)),
        )
        or 0
    )


def _active(ch: dict[str, Any]) -> None:
    if ch.get("suspended_at"):
        raise ChannelSuspended("Your channel is suspended, so you cannot do that.")


# ---------------------------------------------------------------------------
# notifications (to a channel)
# ---------------------------------------------------------------------------


def notify(
    sc: sqlite3.Connection,
    channel_id: str,
    kind: str,
    text: str,
    *,
    ref_type: str = "",
    ref_id: str = "",
) -> None:
    if not channel_id:
        return
    sc.execute(
        "INSERT INTO vi_notifications (id, channel_id, kind, text, ref_type, ref_id, created_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
        (new_id(), channel_id, kind, text[:300], ref_type, ref_id, stamp()),
    )


def notifications(sc: sqlite3.Connection, ch: dict[str, Any]) -> dict[str, Any]:
    items = rows(
        sc,
        "SELECT * FROM vi_notifications WHERE channel_id = ? ORDER BY created_at DESC LIMIT 50",
        (ch["id"],),
    )
    return {
        "notifications": items,
        "unread": sum(1 for item in items if not item["read_at"]),
    }


def unread(sc: sqlite3.Connection, ch: dict[str, Any]) -> int:
    return int(
        store.scalar(
            sc,
            "SELECT COUNT(*) FROM vi_notifications WHERE channel_id = ? AND read_at IS NULL",
            (ch["id"],),
        )
        or 0
    )


def read_notifications(sc: sqlite3.Connection, ch: dict[str, Any]) -> int:
    changed = sc.execute(
        "UPDATE vi_notifications SET read_at = ? WHERE channel_id = ? AND read_at IS NULL",
        (stamp(), ch["id"]),
    ).rowcount
    sc.commit()
    return changed


# ---------------------------------------------------------------------------
# videos: who may watch, how they are shown
# ---------------------------------------------------------------------------


def _video_row(sc: sqlite3.Connection, video_id: str) -> dict[str, Any]:
    found = row(
        sc,
        "SELECT * FROM vi_videos WHERE id = ? AND deleted_at IS NULL",
        (store.check_id(video_id, "video"),),
    )
    if not found:
        raise LookupError("That video was not found.")
    return found


def blocked_reason(sc: sqlite3.Connection, video: dict[str, Any]) -> str:
    """Why nobody but its channel may watch this video ("" when it is fine)."""
    if video.get("takedown_at"):
        return "taken_down"
    if video.get("hidden_at"):
        return "hidden"
    owner = row(
        sc, "SELECT suspended_at FROM vi_channels WHERE id = ?", (video["channel_id"],)
    )
    if not owner or owner["suspended_at"]:
        return "suspended"
    return ""


def scheduled(video: dict[str, Any]) -> bool:
    """The video goes out later (its time is still to come)."""
    return str(video.get("created_at") or "") > stamp()


def can_watch(
    sc: sqlite3.Connection, me_: dict[str, Any], video: dict[str, Any]
) -> bool:
    if video.get("deleted_at"):
        return False
    if video["channel_id"] == me_["id"]:
        return True
    if blocked_reason(sc, video):
        return False
    if video["visibility"] == "private" or scheduled(video):
        return False
    return video["rating"] in me_["ratings"]


def is_member(sc: sqlite3.Connection, channel_id: str, viewer_id: str) -> bool:
    """Whether this person's membership of the channel is paid up."""
    return bool(
        row(
            sc,
            "SELECT 1 AS x FROM vi_members WHERE channel_id = ? AND member_id = ? AND paid_until > ?",
            (channel_id, viewer_id, stamp()),
        )
    )


def locked(sc: sqlite3.Connection, me_: dict[str, Any], video: dict[str, Any]) -> bool:
    """A members-only video that this person may see listed but not play (they are not a member)."""
    if not video.get("members_only") or video["channel_id"] == me_["id"]:
        return False
    return not is_member(sc, video["channel_id"], me_["id"])


def playable_video(
    sc: sqlite3.Connection, me_: dict[str, Any], video_id: str
) -> dict[str, Any]:
    """A video the person may watch and play (not a members-only one they have not joined for)."""
    found = visible_video(sc, me_, video_id)
    if locked(sc, me_, found):
        raise PermissionError(
            "This video is for the channel's members. Join to watch it."
        )
    return found


def visible_video(
    sc: sqlite3.Connection, me_: dict[str, Any], video_id: str
) -> dict[str, Any]:
    found = _video_row(sc, video_id)
    if not can_watch(sc, me_, found):
        raise LookupError("That video was not found.")
    return found


def _listable(ratings: Iterable[str], alias: str = "v") -> tuple[str, list[Any]]:
    """SQL for videos that may be listed for someone with these ratings."""
    chosen = list(ratings)
    marks = ", ".join("?" for _ in chosen)
    sql = (
        f"{alias}.deleted_at IS NULL AND {alias}.hidden_at IS NULL AND {alias}.takedown_at IS NULL "
        f"AND {alias}.visibility = 'public' AND {alias}.rating IN ({marks}) AND {alias}.created_at <= ? "
        f"AND {alias}.channel_id IN (SELECT id FROM vi_channels WHERE suspended_at IS NULL)"
    )
    return sql, [*chosen, stamp()]


def video_view(
    sc: sqlite3.Connection,
    me_: dict[str, Any] | None,
    video: dict[str, Any],
    *,
    full: bool = False,
) -> dict[str, Any]:
    owner = row(
        sc, "SELECT * FROM vi_channels WHERE id = ?", (video["channel_id"],)
    ) or {
        "id": video["channel_id"],
        "name": "Removed channel",
        "avatar": "🙂",
    }
    mine = bool(me_ and video["channel_id"] == me_["id"])
    out: dict[str, Any] = {
        "id": video["id"],
        "kind": video["kind"],
        "title": video["title"],
        "category": video["category"],
        "rating": video["rating"],
        "for_kids": bool(video["for_kids"]),
        "visibility": video["visibility"],
        "language": video["language"],
        "genre": video["genre"],
        "duration": video["duration"],
        "width": video["width"],
        "height": video["height"],
        "views": video["views"],
        "likes": video["likes"],
        "comments": video["comments"],
        "created_at": video["created_at"],
        "channel": mini(owner),
        "thumb_url": media.signed_url(sc, video["thumb_file_id"]),
        "mine": mine,
        "members_only": bool(video.get("members_only")),
        "paid_promotion": bool(video.get("paid_promotion")),
    }
    shut = me_ is not None and locked(sc, me_, video)
    if shut:
        out["locked"] = True
    if full:
        file_row = (
            row(sc, "SELECT mime FROM vi_files WHERE id = ?", (video["file_id"],)) or {}
        )
        out.update(
            {
                "description": video["description"],
                "tags": store.loads(video["tags"], []),
                "credits": store.loads(video["credits"], []),
                "stream_url": "" if shut else media.signed_url(sc, video["file_id"]),
                "captions_url": ""
                if shut
                else media.signed_url(sc, video["captions_file_id"]),
                "mime": file_row.get("mime", ""),
                "playback": media.PLAYBACK.get(file_row.get("mime", ""), ""),
                "ads_enabled": bool(video["ads_enabled"]),
            }
        )
        out["chapters"] = chapters(video["description"], float(video["duration"] or 0))
        if me_ is not None:
            out["position"] = resume_at(sc, me_, video)
            out.update(
                {
                    "liked": bool(
                        row(
                            sc,
                            "SELECT 1 AS x FROM vi_likes WHERE video_id = ? AND viewer_id = ?",
                            (video["id"], me_["id"]),
                        )
                    ),
                    "saved": bool(
                        row(
                            sc,
                            "SELECT 1 AS x FROM vi_later WHERE video_id = ? AND viewer_id = ?",
                            (video["id"], me_["id"]),
                        )
                    ),
                    "subscribed": bool(
                        row(
                            sc,
                            "SELECT 1 AS x FROM vi_subscriptions WHERE subscriber_id = ? AND channel_id = ?",
                            (me_["id"], video["channel_id"]),
                        )
                    ),
                    "subscribers": subscriber_count(sc, video["channel_id"]),
                    "can_comment": me_.get("age_group") != "child"
                    and not me_.get("suspended_at"),
                }
            )
    if mine:
        out["blocked"] = blocked_reason(sc, video)
        out["hidden_reason"] = video["hidden_reason"]
        out["scheduled"] = scheduled(video)
    return out


def subscriber_count(sc: sqlite3.Connection, channel_id: str) -> int:
    return int(
        store.scalar(
            sc,
            "SELECT COUNT(*) FROM vi_subscriptions WHERE channel_id = ?",
            (channel_id,),
        )
        or 0
    )


# ---------------------------------------------------------------------------
# uploading and editing
# ---------------------------------------------------------------------------


def _credits(value: Any) -> list[dict[str, str]]:
    items = value if isinstance(value, list) else []
    if isinstance(value, str):
        items = []
        for line in value.splitlines():
            role, _, name = line.partition(":")
            if line.strip():
                items.append(
                    {"role": role, "name": name} if name else {"role": "", "name": role}
                )
    out = []
    for item in items[:30]:
        if not isinstance(item, dict):
            continue
        role = kit.text(item.get("role"), "a role", max_len=60)
        name = kit.text(item.get("name"), "a name", max_len=80)
        if name:
            out.append({"role": role, "name": name})
    return out


def _language(value: Any, default: str = "en") -> str:
    language = str(value or "").strip().lower() or default
    if not LANGUAGE.fullmatch(language):
        raise ValueError("Choose a language by its two-letter code, like en or te.")
    return language


def _fields(
    raw: dict[str, Any], current: dict[str, Any] | None = None
) -> dict[str, Any]:
    merged = {**(current or {}), **raw}
    values = {
        "title": kit.text(merged.get("title"), "a title", max_len=100, required=True),
        "description": kit.text(
            merged.get("description"), "a description", max_len=5000, multiline=True
        ),
        "category": kit.choice(
            merged.get("category"), "the category", CATEGORIES, default="other"
        ),
        "tags": store.dumps(kit.tags(merged.get("tags"), limit=15)),
        "rating": kit.choice(
            merged.get("rating"), "the rating", RATINGS, default="everyone"
        ),
        "for_kids": 1 if kit.flag(merged.get("for_kids")) else 0,
        "visibility": kit.choice(
            merged.get("visibility"), "who can watch", VISIBILITIES, default="public"
        ),
        "language": _language(merged.get("language")),
        "ads_enabled": 0
        if "ads_enabled" in merged and not kit.flag(merged.get("ads_enabled"))
        else 1,
        "members_only": 1 if kit.flag(merged.get("members_only")) else 0,
        "paid_promotion": 1 if kit.flag(merged.get("paid_promotion")) else 0,
    }
    if values["for_kids"] and values["rating"] != "everyone":
        raise ValueError("A video made for children must be rated for everyone.")
    return values


SCHEDULE_DAYS = 365


# A chosen time this close to now (a few minutes ago, or within the next minute) means "now": the form took a
# moment to fill in. Earlier than that is a slip (a wrong date, AM for PM) and is refused, never published at once.
ABOUT_NOW_BEFORE = timedelta(minutes=5)
ABOUT_NOW_AFTER = timedelta(minutes=1)


def _publish_at(value: Any) -> str:
    """When a video goes out: "" (now) or a moment from a minute to a year ahead, as a store stamp. A time that
    has already passed is refused (it would send out a video the creator meant to hold back)."""
    cleaned = kit.moment(value, "when it goes out")
    if not cleaned:
        return ""
    moment = store.parse(cleaned)
    now = store.utcnow()
    if moment <= now + ABOUT_NOW_AFTER:
        if moment >= now - ABOUT_NOW_BEFORE:
            return ""  # about now
        raise ValueError(
            "That time has already passed. Choose a later time, or choose Now."
        )
    if moment > now + timedelta(days=SCHEDULE_DAYS):
        raise ValueError("Choose a time within the next year.")
    return stamp(moment)


def member_plan_on(sc: sqlite3.Connection, channel_id: str) -> bool:
    found = row(
        sc, "SELECT enabled FROM vi_member_plans WHERE channel_id = ?", (channel_id,)
    )
    return bool(found and found["enabled"])


def _check_extras(
    sc: sqlite3.Connection,
    ch: dict[str, Any],
    kind: str,
    values: dict[str, Any],
    *,
    was_members_only: bool = False,
) -> None:
    """Members-only rules that need the channel (a video that already was for members stays so, even if the
    channel turned memberships off since: its members keep watching it)."""
    if values.get("members_only"):
        if kind == "reel":
            raise ValueError(
                "Reels are for everyone: only videos and short films can be for members."
            )
        if values.get("for_kids"):
            raise ValueError("A video made for children cannot be for members only.")
        if not adult(ch):
            raise PermissionError("Memberships are for adults.")
        if not was_members_only and not member_plan_on(sc, ch["id"]):
            raise ValueError(
                "Turn on memberships first (Earnings > Memberships), so people can join to watch."
            )


def _precheck(
    sc: sqlite3.Connection, ch: dict[str, Any], raw: dict[str, Any]
) -> tuple[str, dict[str, Any], str]:
    """Everything about a new video that does not need its file: (kind, values, publish_at)."""
    _active(ch)
    if strikes(sc, ch["id"]) >= STRIKES_TO_BLOCK:
        raise PermissionError(
            "Your channel has three copyright strikes, so it cannot upload until one of them ends."
        )
    kind = kit.choice(raw.get("kind"), "the kind of video", KINDS, default="video")
    values = _fields(raw)
    if ch.get("age_group") == "child":
        values["visibility"] = "private"  # children's videos stay with them
    _check_extras(sc, ch, kind, values)
    return kind, values, _publish_at(raw.get("publish_at"))


def _check_shape(kind: str, duration: float, width: int, height: int) -> None:
    if kind == "reel":
        if duration and duration > REEL_SECONDS + 0.5:
            raise ValueError(f"A reel is at most {REEL_SECONDS} seconds long.")
        if width and height and height <= width:
            raise ValueError("A reel is a tall (portrait) video.")
    if kind == "short" and duration and duration > SHORT_FILM_SECONDS:
        raise ValueError("A short film is at most an hour long.")


def check_video(
    sc: sqlite3.Connection, ch: dict[str, Any], raw: dict[str, Any]
) -> dict[str, Any]:
    """The upload form asks this before sending the file: the same checks as :func:`create_video` (members only,
    the time it goes out, a reel's shape...), so a mistake is found before a large file goes up, not after."""
    kind, _values, publish_at = _precheck(sc, ch, raw)
    _check_shape(
        kind,
        _number(raw.get("duration"), "the length", 0, 24 * 3600),
        int(_number(raw.get("width"), "the width", 0, 20000)),
        int(_number(raw.get("height"), "the height", 0, 20000)),
    )
    if kind == "short":
        kit.choice(raw.get("genre"), "the genre", GENRES, default="other")
        _credits(raw.get("credits"))
    return {"ok": True, "scheduled": bool(publish_at), "publish_at": publish_at}


def create_video(
    sc: sqlite3.Connection, ch: dict[str, Any], raw: dict[str, Any]
) -> dict[str, Any]:
    kind, values, publish_at = _precheck(sc, ch, raw)
    found = media.owned_file(sc, str(raw.get("file_id") or ""), ch["id"], "video")
    if row(
        sc,
        "SELECT 1 AS x FROM vi_videos WHERE file_id = ? AND deleted_at IS NULL",
        (found["id"],),
    ):
        raise ValueError("That file is already a video.")
    duration = float(found["duration"] or 0) or _number(
        raw.get("duration"), "the length", 0, 24 * 3600
    )
    width = int(found["width"] or 0) or int(
        _number(raw.get("width"), "the width", 0, 20000)
    )
    height = int(found["height"] or 0) or int(
        _number(raw.get("height"), "the height", 0, 20000)
    )
    _check_shape(kind, duration, width, height)
    genre, credits = "", []
    if kind == "short":
        genre = kit.choice(raw.get("genre"), "the genre", GENRES, default="other")
        credits = _credits(raw.get("credits"))
    thumb = str(raw.get("thumb_file_id") or "")
    if thumb:
        media.owned_file(sc, thumb, ch["id"], "image")
    captions = str(raw.get("captions_file_id") or "")
    if captions:
        media.owned_file(sc, captions, ch["id"], "captions")
    rate(sc, ch["id"], "upload")
    video_id = new_id()
    moment = stamp()
    sc.execute(
        """INSERT INTO vi_videos (id, channel_id, kind, title, description, category, tags, rating, for_kids, visibility,
        language, genre, credits, duration, width, height, file_id, thumb_file_id, captions_file_id, ads_enabled,
        created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            video_id,
            ch["id"],
            kind,
            values["title"],
            values["description"],
            values["category"],
            values["tags"],
            values["rating"],
            values["for_kids"],
            values["visibility"],
            values["language"],
            genre,
            store.dumps(credits),
            duration,
            width,
            height,
            found["id"],
            thumb,
            captions,
            values["ads_enabled"],
            publish_at or moment,
            moment,
        ),
    )
    store.update(
        sc,
        "vi_videos",
        video_id,
        {
            "members_only": values["members_only"],
            "paid_promotion": values["paid_promotion"],
        },
    )
    sc.commit()
    return video_view(sc, viewer_of(ch), _video_row(sc, video_id), full=True)


def viewer_of(ch: dict[str, Any]) -> dict[str, Any]:
    return {**ch, "ratings": allowed_ratings(ch)}


def _number(value: Any, name: str, low: float, high: float) -> float:
    if value in (None, ""):
        return 0.0
    try:
        number = float(value)
    except (TypeError, ValueError):
        raise ValueError(f"{name[:1].upper()}{name[1:]} must be a number.") from None
    if not low <= number <= high:
        raise ValueError(f"{name[:1].upper()}{name[1:]} is out of range.")
    return number


def own_video(
    sc: sqlite3.Connection, ch: dict[str, Any], video_id: str
) -> dict[str, Any]:
    found = _video_row(sc, video_id)
    if found["channel_id"] != ch["id"]:
        raise LookupError("That video was not found.")
    return found


def edit_video(
    sc: sqlite3.Connection, ch: dict[str, Any], video_id: str, raw: dict[str, Any]
) -> dict[str, Any]:
    _active(ch)
    found = own_video(sc, ch, video_id)
    current = {**found, "tags": store.loads(found["tags"], [])}
    values = _fields(raw, current)
    if ch.get("age_group") == "child":
        values["visibility"] = "private"
    _check_extras(
        sc,
        ch,
        found["kind"],
        values,
        was_members_only=bool(found.get("members_only")),
    )
    if "publish_at" in raw:
        # Only a video that has not gone out yet can be moved (or sent out now); one that is out keeps its date.
        if scheduled(found):
            values["created_at"] = _publish_at(raw.get("publish_at")) or stamp()
        else:
            cleaned = kit.moment(raw.get("publish_at"), "when it goes out")
            if cleaned and store.parse(cleaned) > store.utcnow() + ABOUT_NOW_AFTER:
                raise ValueError(
                    "This video is already out, so it cannot be scheduled."
                )
    if "thumb_file_id" in raw:
        thumb = str(raw.get("thumb_file_id") or "")
        if thumb:
            media.owned_file(sc, thumb, ch["id"], "image")
        if found["thumb_file_id"] and found["thumb_file_id"] != thumb:
            media.delete_file(sc, found["thumb_file_id"])
        values["thumb_file_id"] = thumb
    if "captions_file_id" in raw:
        captions = str(raw.get("captions_file_id") or "")
        if captions:
            media.owned_file(sc, captions, ch["id"], "captions")
        if found["captions_file_id"] and found["captions_file_id"] != captions:
            media.delete_file(sc, found["captions_file_id"])
        values["captions_file_id"] = captions
    if found["kind"] == "short":
        if "genre" in raw:
            values["genre"] = kit.choice(
                raw.get("genre"), "the genre", GENRES, default="other"
            )
        if "credits" in raw:
            values["credits"] = store.dumps(_credits(raw.get("credits")))
    store.update(sc, "vi_videos", found["id"], {**values, "updated_at": stamp()})
    sc.commit()
    return video_view(sc, viewer_of(ch), _video_row(sc, found["id"]), full=True)


def delete_video(sc: sqlite3.Connection, ch: dict[str, Any], video_id: str) -> None:
    found = own_video(sc, ch, video_id)
    _remove_video(sc, found)
    sc.commit()


def _remove_video(sc: sqlite3.Connection, found: dict[str, Any]) -> None:
    for file_id in (
        found["file_id"],
        found["thumb_file_id"],
        found["captions_file_id"],
    ):
        media.delete_file(sc, file_id)
    store.update(
        sc, "vi_videos", found["id"], {"deleted_at": stamp(), "updated_at": stamp()}
    )
    for statement in (
        "DELETE FROM vi_likes WHERE video_id = ?",
        "DELETE FROM vi_later WHERE video_id = ?",
        "DELETE FROM vi_playlist_items WHERE video_id = ?",
        "DELETE FROM vi_watch WHERE video_id = ?",
        "DELETE FROM vi_comments WHERE video_id = ?",
    ):
        sc.execute(statement, (found["id"],))


MY_FILTERS = {
    "public": "visibility = 'public'",
    "unlisted": "visibility = 'unlisted'",
    "private": "visibility = 'private'",
    "scheduled": "created_at > ?",
    "members": "members_only = 1",
    "promoted": "paid_promotion = 1",
    "problems": "(hidden_at IS NOT NULL OR takedown_at IS NOT NULL)",
}


def my_videos(
    sc: sqlite3.Connection, ch: dict[str, Any], q: str = "", show: str = ""
) -> list[dict[str, Any]]:
    """The channel's own videos, newest first; ``q`` searches titles, descriptions and tags, ``show`` is one of
    MY_FILTERS (who can watch, scheduled, for members, paid promotion, hidden or taken down)."""
    me_ = viewer_of(ch)
    where, params = "channel_id = ? AND deleted_at IS NULL", [ch["id"]]
    if show:
        kit.choice(show, "what to show", tuple(MY_FILTERS))
        where += f" AND {MY_FILTERS[show]}"
        if "?" in MY_FILTERS[show]:
            params.append(stamp())
    text = kit.text(q, "what to search for", max_len=100)
    if text:
        like = "%" + re.sub(r"([!%_])", r"!\1", text.lower()) + "%"
        where += " AND (lower(title) LIKE ? ESCAPE '!' OR lower(description) LIKE ? ESCAPE '!' OR lower(tags) LIKE ? ESCAPE '!')"
        params += [like, like, like]
    return [
        video_view(sc, me_, item)
        for item in rows(
            sc,
            f"SELECT * FROM vi_videos WHERE {where} ORDER BY created_at DESC LIMIT 500",  # noqa: S608 - fragments are ours
            tuple(params),
        )
    ]


# ---------------------------------------------------------------------------
# finding videos
# ---------------------------------------------------------------------------

POPULAR_FORMULA = "(views in the last 7 days + 2 × likes + 3 × comments + 1) ÷ (hours since upload + 2)^1.2"


def _views_since(sc: sqlite3.Connection, ids: list[str], days: int) -> dict[str, int]:
    if not ids:
        return {}
    since = store.day(store.utcnow() - timedelta(days=days))
    marks = ", ".join("?" for _ in ids)
    return {
        item["video_id"]: item["n"]
        for item in rows(
            sc,
            f"SELECT video_id, COUNT(*) AS n FROM vi_views WHERE day >= ? AND video_id IN ({marks}) GROUP BY video_id",  # noqa: S608
            (since, *ids),
        )
    }


def popular_score(video: dict[str, Any], recent_views: int) -> float:
    age_hours = max(
        0.0, (store.utcnow() - store.parse(video["created_at"])).total_seconds() / 3600
    )
    return (recent_views + 2 * video["likes"] + 3 * video["comments"] + 1) / (
        age_hours + 2
    ) ** 1.2


def _ranked(
    sc: sqlite3.Connection, candidates: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    recent = _views_since(sc, [item["id"] for item in candidates], 7)
    return sorted(
        candidates,
        key=lambda item: popular_score(item, recent.get(item["id"], 0)),
        reverse=True,
    )


def _list(
    sc: sqlite3.Connection,
    me_: dict[str, Any],
    where: str = "",
    params: tuple[Any, ...] = (),
    *,
    order: str = "v.created_at DESC",
    limit: int = 24,
) -> list[dict[str, Any]]:
    sql, chosen = _listable(me_["ratings"])
    found = rows(
        sc,
        f"SELECT v.* FROM vi_videos v WHERE {sql} {('AND ' + where) if where else ''} ORDER BY {order} LIMIT ?",  # noqa: S608 - fragments are ours
        (*chosen, *params, limit),
    )
    return found


def home(
    sc: sqlite3.Connection, me_: dict[str, Any], category: str = ""
) -> dict[str, Any]:
    where, params = "v.kind != 'reel'", ()
    if category:
        kit.choice(category, "the category", CATEGORIES)
        where, params = "v.kind != 'reel' AND v.category = ?", (category,)
    subscribed = _list(
        sc,
        me_,
        f"{where} AND v.channel_id IN (SELECT channel_id FROM vi_subscriptions WHERE subscriber_id = ?)",
        (*params, me_["id"]),
        limit=12,
    )
    recent = _list(sc, me_, where, params, limit=24)
    window = store.later(-24 * 90)
    popular = _ranked(
        sc,
        _list(sc, me_, f"{where} AND v.created_at > ?", (*params, window), limit=400),
    )[:24]
    reel_where, reel_params = (
        ("v.kind = 'reel' AND v.category = ?", (category,))
        if category
        else ("v.kind = 'reel'", ())
    )
    shelf = _ranked(sc, _list(sc, me_, reel_where, reel_params, limit=60))[:12]
    view = lambda items: [video_view(sc, me_, item) for item in items]  # noqa: E731
    return {
        "reels": view(shelf),
        "subscriptions": view(subscribed),
        "recent": view(recent),
        "popular": view(popular),
        "categories": list(CATEGORIES),
        "ranking": POPULAR_FORMULA,
    }


def search(
    sc: sqlite3.Connection, me_: dict[str, Any], q: str, kind: str = ""
) -> dict[str, Any]:
    text = kit.text(q, "what to search for", max_len=100)
    where, params = "1 = 1", ()
    if kind:
        kit.choice(kind, "the kind of video", KINDS)
        where, params = "v.kind = ?", (kind,)
    if not text:
        return {"videos": [], "channels": []}
    like = "%" + re.sub(r"([!%_])", r"!\1", text.lower()) + "%"
    found = _list(
        sc,
        me_,
        f"{where} AND (lower(v.title) LIKE ? ESCAPE '!' OR lower(v.description) LIKE ? ESCAPE '!' "
        "OR lower(v.tags) LIKE ? ESCAPE '!' "
        "OR v.channel_id IN (SELECT id FROM vi_channels WHERE lower(name) LIKE ? ESCAPE '!'))",
        (*params, like, like, like, like),
        limit=60,
    )
    channels = rows(
        sc,
        """SELECT * FROM vi_channels WHERE suspended_at IS NULL AND lower(name) LIKE ? ESCAPE '!' AND age_group != 'child'
        AND id IN (SELECT channel_id FROM vi_videos WHERE visibility = 'public' AND deleted_at IS NULL) LIMIT 10""",
        (like,),
    )
    return {
        "videos": [video_view(sc, me_, item) for item in _ranked(sc, found)],
        "channels": [
            {**mini(item), "subscribers": subscriber_count(sc, item["id"])}
            for item in channels
        ],
    }


def reels(
    sc: sqlite3.Connection, me_: dict[str, Any], start: str = ""
) -> dict[str, Any]:
    """Reels newest first, the popular ones of the last two weeks woven in; ``start`` (a reel id) comes first."""
    recent = _list(sc, me_, "v.kind = 'reel'", (), limit=60)
    popular = _ranked(
        sc,
        _list(
            sc,
            me_,
            "v.kind = 'reel' AND v.created_at > ?",
            (store.later(-24 * 14),),
            limit=200,
        ),
    )
    seen: set[str] = set()
    ordered = []
    for pair in zip(recent, popular + [None] * len(recent), strict=False):
        for item in pair:
            if item and item["id"] not in seen:
                seen.add(item["id"])
                ordered.append(item)
    if start:
        try:
            first = visible_video(sc, me_, start)
            ordered = [first, *[item for item in ordered if item["id"] != first["id"]]]
        except LookupError:
            pass
    return {"reels": [video_view(sc, me_, item, full=True) for item in ordered[:60]]}


def films(
    sc: sqlite3.Connection, me_: dict[str, Any], genre: str = ""
) -> dict[str, Any]:
    where, params = "v.kind = 'short'", ()
    if genre:
        kit.choice(genre, "the genre", GENRES)
        where, params = "v.kind = 'short' AND v.genre = ?", (genre,)
    found = _list(sc, me_, where, params, limit=60)
    return {
        "films": [video_view(sc, me_, item) for item in found],
        "genres": list(GENRES),
    }


def channel_page(
    sc: sqlite3.Connection, me_: dict[str, Any], channel_id: str
) -> dict[str, Any]:
    target = channel(sc, channel_id)
    if target["id"] != me_["id"]:
        if target.get("suspended_at"):
            raise LookupError("That channel was not found.")
        if target["age_group"] == "child":
            raise LookupError(
                "That channel was not found."
            )  # children's channels are not shown to others
        found = _list(sc, me_, "v.channel_id = ?", (target["id"],), limit=200)
    else:
        found = rows(
            sc,
            "SELECT * FROM vi_videos WHERE channel_id = ? AND deleted_at IS NULL ORDER BY created_at DESC LIMIT 200",
            (target["id"],),
        )
    return {
        "channel": {
            **mini(target),
            "about": target["about"],
            "subscribers": subscriber_count(sc, target["id"]),
            "created_at": target["created_at"],
            "mine": target["id"] == me_["id"],
            "subscribed": bool(
                row(
                    sc,
                    "SELECT 1 AS x FROM vi_subscriptions WHERE subscriber_id = ? AND channel_id = ?",
                    (me_["id"], target["id"]),
                )
            ),
        },
        "videos": [video_view(sc, me_, item) for item in found],
    }


# ---------------------------------------------------------------------------
# watching: progress, views, history, likes, subscriptions, watch later, playlists
# ---------------------------------------------------------------------------


def progress(
    sc: sqlite3.Connection, me_: dict[str, Any], video_id: str, raw: dict[str, Any]
) -> dict[str, Any]:
    """Remember where the person is in a video and count a view after a meaningful watch."""
    video = playable_video(sc, me_, video_id)
    position = _number(raw.get("position"), "the position", 0, 24 * 3600)
    watched = _number(raw.get("watched"), "the time watched", 0, 24 * 3600)
    moment = store.utcnow()
    found = row(
        sc,
        "SELECT * FROM vi_watch WHERE viewer_id = ? AND video_id = ?",
        (me_["id"], video["id"]),
    )
    # A watch "session" restarts after six hours away; the server's own clock bounds the time watched.
    if not found or (moment - store.parse(found["last_at"])).total_seconds() > 6 * 3600:
        started = moment
        sc.execute(
            """INSERT INTO vi_watch (viewer_id, video_id, position, seconds, started_at, last_at, cleared)
            VALUES (?, ?, ?, ?, ?, ?, 0) ON CONFLICT(viewer_id, video_id) DO UPDATE SET position = excluded.position,
            seconds = excluded.seconds, started_at = excluded.started_at, last_at = excluded.last_at, cleared = 0""",
            (me_["id"], video["id"], position, 0, stamp(started), stamp(moment)),
        )
        elapsed = 0.0
    else:
        started = store.parse(found["started_at"])
        elapsed = (moment - started).total_seconds()
        # The page reports the time watched since it opened; a page opened again starts from 0. Only the new part
        # counts, and never more than the time since the last report.
        reported = min(watched, elapsed + 1)
        before = float(found["seconds"] or 0)
        gained = reported - before if reported >= before else reported
        gained = min(
            gained, (moment - store.parse(found["last_at"])).total_seconds() + 1
        )
        if gained > 0 and video["channel_id"] != me_["id"]:
            log_watch(sc, video["id"], gained)
        sc.execute(
            "UPDATE vi_watch SET position = ?, seconds = ?, last_at = ?, cleared = 0 WHERE viewer_id = ? AND video_id = ?",
            (
                position,
                min(watched, elapsed + 1),
                stamp(moment),
                me_["id"],
                video["id"],
            ),
        )
    counted = False
    need = min(
        VIEW_SECONDS, VIEW_SHARE * float(video["duration"] or VIEW_SECONDS / VIEW_SHARE)
    )
    if video["channel_id"] != me_["id"] and min(watched, elapsed + 1) >= need:
        counted = count_view(sc, video, me_["id"])
    sc.commit()
    return {"counted": counted, "views": _video_row(sc, video["id"])["views"]}


def log_watch(sc: sqlite3.Connection, video_id: str, seconds: float) -> None:
    """Add seconds really watched to today's total for the video (the creator's watch time)."""
    sc.execute(
        """INSERT INTO vi_watch_days (video_id, day, seconds) VALUES (?, ?, ?)
        ON CONFLICT(video_id, day) DO UPDATE SET seconds = seconds + excluded.seconds""",
        (video_id, store.day(), round(float(seconds), 2)),
    )


RESUME_MIN_SECONDS = 5
CHAPTER = re.compile(r"^\s*(?:(\d{1,2}):)?(\d{1,2}):(\d{2})\s*[-–—:]?\s+(\S.{0,79})$")


def resume_at(
    sc: sqlite3.Connection, me_: dict[str, Any], video: dict[str, Any]
) -> float:
    """Where the person stopped last time (0 when they finished it, barely started, or cleared it from history)."""
    found = row(
        sc,
        "SELECT position FROM vi_watch WHERE viewer_id = ? AND video_id = ? AND cleared = 0",
        (me_["id"], video["id"]),
    )
    position = float(found["position"] or 0) if found else 0.0
    duration = float(video.get("duration") or 0)
    if position < RESUME_MIN_SECONDS or (
        duration and position > duration - RESUME_MIN_SECONDS
    ):
        return 0.0
    return round(position, 1)


def chapters(description: str, duration: float = 0) -> list[dict[str, Any]]:
    """Chapters written in the description, one per line ("0:00 Intro", "1:05:30 The end"): the first at 0:00, at
    least two, each later than the one before (and inside the video). Anything else is not chapters."""
    found: list[dict[str, Any]] = []
    for line in str(description or "").splitlines():
        match = CHAPTER.match(line)
        if not match:
            continue
        hours, minutes, seconds = (
            int(match.group(1) or 0),
            int(match.group(2)),
            int(match.group(3)),
        )
        if seconds > 59 or (match.group(1) and minutes > 59):
            continue
        at = hours * 3600 + minutes * 60 + seconds
        if found and at <= found[-1]["at"]:
            return []
        if duration and at >= duration:
            break
        found.append({"at": at, "title": match.group(4).strip()})
    if len(found) < 2 or found[0]["at"] != 0:
        return []
    return found[:100]


def continue_watching(
    sc: sqlite3.Connection, me_: dict[str, Any], limit: int = 12
) -> list[dict[str, Any]]:
    """Videos the person started and did not finish (newest first), with where they stopped."""
    out = []
    for item in rows(
        sc,
        f"SELECT * FROM vi_watch WHERE viewer_id = ? AND cleared = 0 AND position >= {RESUME_MIN_SECONDS} "  # noqa: S608
        "ORDER BY last_at DESC LIMIT 60",
        (me_["id"],),
    ):
        try:
            video = visible_video(sc, me_, item["video_id"])
        except LookupError:
            continue
        if video["kind"] == "reel":
            continue
        at = resume_at(sc, me_, video)
        if not at:
            continue
        out.append(
            {
                **video_view(sc, me_, video),
                "position": at,
                "watched_at": item["last_at"],
            }
        )
        if len(out) >= limit:
            break
    return out


def count_view(sc: sqlite3.Connection, video: dict[str, Any], viewer_key: str) -> bool:
    inserted = sc.execute(
        "INSERT OR IGNORE INTO vi_views (video_id, viewer_key, day, at) VALUES (?, ?, ?, ?)",
        (video["id"], viewer_key, store.day(), stamp()),
    ).rowcount
    if inserted:
        sc.execute(
            "UPDATE vi_videos SET views = views + 1 WHERE id = ?", (video["id"],)
        )
    return bool(inserted)


def visitor_key(client: str, user_agent: str = "") -> str:
    """A public-page visitor, for counting their view once a day (an address is never stored)."""
    digest = hashlib.sha256(
        f"{client}|{user_agent}|{store.day()}".encode()
    ).hexdigest()[:24]
    return f"visitor:{digest}"


def history(sc: sqlite3.Connection, me_: dict[str, Any]) -> list[dict[str, Any]]:
    out = []
    for item in rows(
        sc,
        "SELECT * FROM vi_watch WHERE viewer_id = ? AND cleared = 0 ORDER BY last_at DESC LIMIT 100",
        (me_["id"],),
    ):
        try:
            video = visible_video(sc, me_, item["video_id"])
        except LookupError:
            continue
        out.append(
            {
                **video_view(sc, me_, video),
                "position": item["position"],
                "watched_at": item["last_at"],
            }
        )
    return out


def clear_history(
    sc: sqlite3.Connection, me_: dict[str, Any], video_id: str = ""
) -> None:
    if video_id:
        changed = sc.execute(
            "UPDATE vi_watch SET cleared = 1 WHERE viewer_id = ? AND video_id = ? AND cleared = 0",
            (me_["id"], video_id),
        ).rowcount
        if not changed:
            raise LookupError("That video is not in your history.")
    else:
        sc.execute("UPDATE vi_watch SET cleared = 1 WHERE viewer_id = ?", (me_["id"],))
    sc.commit()


def like(
    sc: sqlite3.Connection, me_: dict[str, Any], video_id: str, on: bool
) -> dict[str, Any]:
    video = playable_video(sc, me_, video_id)
    if on:
        changed = sc.execute(
            "INSERT OR IGNORE INTO vi_likes (video_id, viewer_id, at) VALUES (?, ?, ?)",
            (video["id"], me_["id"], stamp()),
        ).rowcount
        if changed:
            sc.execute(
                "UPDATE vi_videos SET likes = likes + 1 WHERE id = ?", (video["id"],)
            )
    else:
        changed = sc.execute(
            "DELETE FROM vi_likes WHERE video_id = ? AND viewer_id = ?",
            (video["id"], me_["id"]),
        ).rowcount
        if changed:
            sc.execute(
                "UPDATE vi_videos SET likes = MAX(0, likes - 1) WHERE id = ?",
                (video["id"],),
            )
    sc.commit()
    return {"liked": on, "likes": _video_row(sc, video["id"])["likes"]}


def subscribe(
    sc: sqlite3.Connection, me_: dict[str, Any], channel_id: str, on: bool
) -> dict[str, Any]:
    target = channel(sc, channel_id)
    if target["id"] == me_["id"]:
        raise ValueError("You cannot subscribe to your own channel.")
    if target.get("suspended_at") or target["age_group"] == "child":
        raise LookupError("That channel was not found.")
    if on:
        changed = sc.execute(
            "INSERT OR IGNORE INTO vi_subscriptions (subscriber_id, channel_id, at) VALUES (?, ?, ?)",
            (me_["id"], target["id"], stamp()),
        ).rowcount
        if changed:
            notify(
                sc,
                target["id"],
                "subscriber",
                f"{me_['name']} subscribed to your channel.",
                ref_type="channel",
                ref_id=me_["id"],
            )
    else:
        sc.execute(
            "DELETE FROM vi_subscriptions WHERE subscriber_id = ? AND channel_id = ?",
            (me_["id"], target["id"]),
        )
    sc.commit()
    return {"subscribed": on, "subscribers": subscriber_count(sc, target["id"])}


def subscriptions(sc: sqlite3.Connection, me_: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        {**mini(item), "subscribers": subscriber_count(sc, item["id"])}
        for item in rows(
            sc,
            """SELECT c.* FROM vi_channels c JOIN vi_subscriptions s ON s.channel_id = c.id
            WHERE s.subscriber_id = ? AND c.suspended_at IS NULL ORDER BY s.at DESC""",
            (me_["id"],),
        )
    ]


def later(
    sc: sqlite3.Connection, me_: dict[str, Any], video_id: str, on: bool
) -> dict[str, Any]:
    video = visible_video(sc, me_, video_id)
    if on:
        sc.execute(
            "INSERT OR IGNORE INTO vi_later (viewer_id, video_id, at) VALUES (?, ?, ?)",
            (me_["id"], video["id"], stamp()),
        )
    else:
        sc.execute(
            "DELETE FROM vi_later WHERE viewer_id = ? AND video_id = ?",
            (me_["id"], video["id"]),
        )
    sc.commit()
    return {"saved": on}


def later_list(sc: sqlite3.Connection, me_: dict[str, Any]) -> list[dict[str, Any]]:
    out = []
    for item in rows(
        sc,
        "SELECT video_id FROM vi_later WHERE viewer_id = ? ORDER BY at DESC LIMIT 200",
        (me_["id"],),
    ):
        try:
            out.append(video_view(sc, me_, visible_video(sc, me_, item["video_id"])))
        except LookupError:
            continue
    return out


def _playlist(
    sc: sqlite3.Connection, me_: dict[str, Any], playlist_id: str
) -> dict[str, Any]:
    found = row(
        sc,
        "SELECT * FROM vi_playlists WHERE id = ? AND channel_id = ?",
        (store.check_id(playlist_id, "playlist"), me_["id"]),
    )
    if not found:
        raise LookupError("That playlist was not found.")
    return found


def playlists(sc: sqlite3.Connection, me_: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        {
            **item,
            "count": int(
                store.scalar(
                    sc,
                    "SELECT COUNT(*) FROM vi_playlist_items WHERE playlist_id = ?",
                    (item["id"],),
                )
                or 0
            ),
        }
        for item in rows(
            sc,
            "SELECT * FROM vi_playlists WHERE channel_id = ? ORDER BY updated_at DESC",
            (me_["id"],),
        )
    ]


def create_playlist(
    sc: sqlite3.Connection, me_: dict[str, Any], raw: dict[str, Any]
) -> dict[str, Any]:
    name = kit.text(raw.get("name"), "a playlist name", max_len=80, required=True)
    if (
        int(
            store.scalar(
                sc,
                "SELECT COUNT(*) FROM vi_playlists WHERE channel_id = ?",
                (me_["id"],),
            )
            or 0
        )
        >= 100
    ):
        raise ValueError("You can have at most 100 playlists.")
    playlist_id = new_id()
    sc.execute(
        "INSERT INTO vi_playlists (id, channel_id, name, created_at, updated_at) VALUES (?, ?, ?, ?, ?)",
        (playlist_id, me_["id"], name, stamp(), stamp()),
    )
    sc.commit()
    return playlist(sc, me_, playlist_id)


def rename_playlist(
    sc: sqlite3.Connection, me_: dict[str, Any], playlist_id: str, raw: dict[str, Any]
) -> dict[str, Any]:
    found = _playlist(sc, me_, playlist_id)
    name = kit.text(raw.get("name"), "a playlist name", max_len=80, required=True)
    sc.execute(
        "UPDATE vi_playlists SET name = ?, updated_at = ? WHERE id = ?",
        (name, stamp(), found["id"]),
    )
    sc.commit()
    return playlist(sc, me_, found["id"])


def delete_playlist(
    sc: sqlite3.Connection, me_: dict[str, Any], playlist_id: str
) -> None:
    found = _playlist(sc, me_, playlist_id)
    sc.execute("DELETE FROM vi_playlist_items WHERE playlist_id = ?", (found["id"],))
    sc.execute("DELETE FROM vi_playlists WHERE id = ?", (found["id"],))
    sc.commit()


def playlist(
    sc: sqlite3.Connection, me_: dict[str, Any], playlist_id: str
) -> dict[str, Any]:
    found = _playlist(sc, me_, playlist_id)
    videos = []
    for item in rows(
        sc,
        "SELECT video_id FROM vi_playlist_items WHERE playlist_id = ? ORDER BY position, added_at",
        (found["id"],),
    ):
        try:
            videos.append(video_view(sc, me_, visible_video(sc, me_, item["video_id"])))
        except LookupError:
            continue
    return {**found, "videos": videos, "count": len(videos)}


def playlist_item(
    sc: sqlite3.Connection,
    me_: dict[str, Any],
    playlist_id: str,
    video_id: str,
    on: bool,
) -> dict[str, Any]:
    found = _playlist(sc, me_, playlist_id)
    video = visible_video(sc, me_, video_id)
    if on:
        size = int(
            store.scalar(
                sc,
                "SELECT COUNT(*) FROM vi_playlist_items WHERE playlist_id = ?",
                (found["id"],),
            )
            or 0
        )
        if size >= 500:
            raise ValueError("A playlist holds at most 500 videos.")
        sc.execute(
            "INSERT OR IGNORE INTO vi_playlist_items (playlist_id, video_id, position, added_at) VALUES (?, ?, ?, ?)",
            (found["id"], video["id"], size, stamp()),
        )
    else:
        sc.execute(
            "DELETE FROM vi_playlist_items WHERE playlist_id = ? AND video_id = ?",
            (found["id"], video["id"]),
        )
    sc.execute(
        "UPDATE vi_playlists SET updated_at = ? WHERE id = ?", (stamp(), found["id"])
    )
    sc.commit()
    return playlist(sc, me_, found["id"])


# ---------------------------------------------------------------------------
# comments
# ---------------------------------------------------------------------------


def comments(
    sc: sqlite3.Connection, me_: dict[str, Any], video_id: str
) -> list[dict[str, Any]]:
    video = playable_video(sc, me_, video_id)
    out = []
    for item in rows(
        sc,
        """SELECT c.* FROM vi_comments c JOIN vi_channels a ON a.id = c.author_id
        WHERE c.video_id = ? AND c.deleted_at IS NULL AND c.hidden_at IS NULL AND a.suspended_at IS NULL
        ORDER BY c.created_at DESC LIMIT 200""",
        (video["id"],),
    ):
        author = row(sc, "SELECT * FROM vi_channels WHERE id = ?", (item["author_id"],))
        out.append(
            {
                "id": item["id"],
                "text": item["text"],
                "created_at": item["created_at"],
                "author": mini(author)
                if author
                else {"id": "", "name": "Someone", "avatar": "🙂"},
                "mine": item["author_id"] == me_["id"],
                "can_delete": item["author_id"] == me_["id"]
                or video["channel_id"] == me_["id"],
            }
        )
    return out


def comment(
    sc: sqlite3.Connection, me_: dict[str, Any], video_id: str, raw: dict[str, Any]
) -> dict[str, Any]:
    _active(me_)
    if me_.get("age_group") == "child":
        raise PermissionError("Children cannot comment on videos.")
    video = playable_video(sc, me_, video_id)
    if video["visibility"] == "private" and video["channel_id"] != me_["id"]:
        raise LookupError("That video was not found.")
    text = filtered(
        sc,
        kit.text(
            raw.get("text"), "a comment", max_len=1000, required=True, multiline=True
        ),
    )
    rate(sc, me_["id"], "comment")
    comment_id = new_id()
    sc.execute(
        "INSERT INTO vi_comments (id, video_id, author_id, text, created_at) VALUES (?, ?, ?, ?, ?)",
        (comment_id, video["id"], me_["id"], text, stamp()),
    )
    sc.execute(
        "UPDATE vi_videos SET comments = comments + 1 WHERE id = ?", (video["id"],)
    )
    if video["channel_id"] != me_["id"]:
        notify(
            sc,
            video["channel_id"],
            "comment",
            f"{me_['name']} commented on {video['title']}.",
            ref_type="video",
            ref_id=video["id"],
        )
    sc.commit()
    return {"comments": comments(sc, me_, video["id"])}


def delete_comment(
    sc: sqlite3.Connection, me_: dict[str, Any], comment_id: str
) -> None:
    found = row(
        sc,
        "SELECT * FROM vi_comments WHERE id = ? AND deleted_at IS NULL",
        (store.check_id(comment_id, "comment"),),
    )
    if not found:
        raise LookupError("That comment was not found.")
    video = (
        row(sc, "SELECT channel_id FROM vi_videos WHERE id = ?", (found["video_id"],))
        or {}
    )
    if found["author_id"] != me_["id"] and video.get("channel_id") != me_["id"]:
        raise PermissionError(
            "You can delete your own comments, and comments on your videos."
        )
    sc.execute(
        "UPDATE vi_comments SET deleted_at = ? WHERE id = ?", (stamp(), found["id"])
    )
    sc.execute(
        "UPDATE vi_videos SET comments = MAX(0, comments - 1) WHERE id = ?",
        (found["video_id"],),
    )
    sc.commit()


# ---------------------------------------------------------------------------
# reports and moderation
# ---------------------------------------------------------------------------


def report(
    sc: sqlite3.Connection, me_: dict[str, Any], raw: dict[str, Any]
) -> dict[str, Any]:
    target_type = kit.choice(
        raw.get("target_type"), "what you are reporting", REPORT_TARGETS
    )
    target_id = store.check_id(str(raw.get("target_id") or ""), target_type)
    reason = (
        kit.text(raw.get("reason"), "a reason", max_len=500, multiline=True)
        or "No reason given"
    )
    if target_type == "video":
        visible_video(sc, me_, target_id)
    elif target_type == "comment":
        found = row(
            sc,
            "SELECT video_id FROM vi_comments WHERE id = ? AND deleted_at IS NULL",
            (target_id,),
        )
        if not found:
            raise LookupError("That comment was not found.")
        visible_video(sc, me_, found["video_id"])
    else:
        target = channel(sc, target_id)
        if target.get("age_group") == "child" and target["id"] != me_["id"]:
            raise LookupError("That channel was not found.")
    if row(
        sc,
        "SELECT 1 AS x FROM vi_reports WHERE reporter_id = ? AND target_type = ? AND target_id = ? AND status = 'open'",
        (me_["id"], target_type, target_id),
    ):
        return {"reported": True}
    rate(sc, me_["id"], "report")
    sc.execute(
        "INSERT INTO vi_reports (id, reporter_id, target_type, target_id, reason, created_at) VALUES (?, ?, ?, ?, ?, ?)",
        (new_id(), me_["id"], target_type, target_id, reason, stamp()),
    )
    sc.commit()
    return {"reported": True}


def _target(sc: sqlite3.Connection, target_type: str, target_id: str) -> dict[str, Any]:
    table = {"video": "vi_videos", "comment": "vi_comments", "channel": "vi_channels"}[
        target_type
    ]
    return row(sc, f"SELECT * FROM {table} WHERE id = ?", (target_id,)) or {}  # noqa: S608 - fixed names


def _owner_of(target_type: str, target: dict[str, Any]) -> str:
    if target_type == "video":
        return str(target.get("channel_id") or "")
    if target_type == "comment":
        return str(target.get("author_id") or "")
    return str(target.get("id") or "")


def _home(sc: sqlite3.Connection, channel_id: str) -> str:
    found = row(sc, "SELECT home FROM vi_channels WHERE id = ?", (channel_id,))
    return found["home"] if found else ""


def _in_scope(sc: sqlite3.Connection, item: dict[str, Any], home: str | None) -> bool:
    """A household owner sees reports their household filed or about their household's people; the platform's
    moderators (``home`` None) see every report."""
    if home is None:
        return True
    target = _target(sc, item["target_type"], item["target_id"])
    return home in (
        _home(sc, item["reporter_id"]),
        _home(sc, _owner_of(item["target_type"], target)),
    )


def reports(
    sc: sqlite3.Connection, *, status: str = "open", home: str | None = None
) -> list[dict[str, Any]]:
    kit.choice(status, "the status", ("open", "resolved", "dismissed"))
    out = []
    for item in rows(
        sc,
        "SELECT * FROM vi_reports WHERE status = ? ORDER BY created_at DESC LIMIT 500",
        (status,),
    ):
        if not _in_scope(sc, item, home):
            continue
        target = _target(sc, item["target_type"], item["target_id"])
        owner_id = _owner_of(item["target_type"], target)
        owner = row(sc, "SELECT * FROM vi_channels WHERE id = ?", (owner_id,))
        reporter = row(
            sc, "SELECT * FROM vi_channels WHERE id = ?", (item["reporter_id"],)
        )
        preview = target.get("title") or target.get("text") or target.get("name") or ""
        out.append(
            {
                **item,
                "preview": str(preview)[:300],
                "author": mini(owner) if owner else None,
                "reporter": mini(reporter) if reporter else None,
                "hidden": bool(
                    target.get("hidden_at")
                    or target.get("suspended_at")
                    or target.get("takedown_at")
                ),
                "can_suspend": home is None
                or (owner is not None and owner["home"] == home),
            }
        )
        if len(out) >= 200:
            break
    return out


def set_hidden(
    sc: sqlite3.Connection, target_type: str, target_id: str, on: bool, reason: str = ""
) -> None:
    moment = stamp() if on else None
    if target_type == "video":
        sc.execute(
            "UPDATE vi_videos SET hidden_at = ?, hidden_reason = ?, updated_at = ? WHERE id = ?",
            (moment, reason[:200] if on else "", stamp(), target_id),
        )
        found = row(
            sc, "SELECT channel_id, title FROM vi_videos WHERE id = ?", (target_id,)
        )
        if found and on:
            notify(
                sc,
                found["channel_id"],
                "hidden",
                f"Your video {found['title']} was hidden by a moderator.",
                ref_type="video",
                ref_id=target_id,
            )
    elif target_type == "comment":
        sc.execute(
            "UPDATE vi_comments SET hidden_at = ? WHERE id = ?", (moment, target_id)
        )


def set_suspended(
    sc: sqlite3.Connection, channel_id: str, on: bool, reason: str = ""
) -> None:
    sc.execute(
        "UPDATE vi_channels SET suspended_at = ?, suspended_reason = ?, monetized = CASE WHEN ? THEN 0 ELSE monetized END, updated_at = ? WHERE id = ?",
        (
            stamp() if on else None,
            reason[:200] if on else "",
            1 if on else 0,
            stamp(),
            channel_id,
        ),
    )
    sc.commit()


def moderate(
    sc: sqlite3.Connection,
    moderator: str,
    report_id: str,
    action: str,
    *,
    home: str | None = None,
) -> dict[str, Any]:
    """``hide`` the reported thing, ``restore`` it, ``suspend`` its channel, or ``dismiss`` the report. A household
    owner (``home``) acts on reports in their scope and suspends only their own household's people."""
    found = row(
        sc,
        "SELECT * FROM vi_reports WHERE id = ?",
        (store.check_id(report_id, "report"),),
    )
    if not found or not _in_scope(sc, found, home):
        raise LookupError("That report was not found.")
    action = kit.choice(action, "the action", ("hide", "restore", "suspend", "dismiss"))
    target = _target(sc, found["target_type"], found["target_id"])
    owner_id = _owner_of(found["target_type"], target)
    if action == "suspend" or (
        found["target_type"] == "channel" and action in ("hide", "restore")
    ):
        if home is not None and _home(sc, owner_id) != home:
            raise PermissionError(
                "You can hide what was reported; only Monosage's moderators suspend someone outside your household."
            )
    if action in ("hide", "restore") and found["target_type"] != "channel":
        set_hidden(
            sc,
            found["target_type"],
            found["target_id"],
            action == "hide",
            "Reported and reviewed",
        )
    elif action == "suspend" or (
        action == "hide" and found["target_type"] == "channel"
    ):
        if owner_id:
            set_suspended(sc, owner_id, True, "Reported and reviewed")
    elif action == "restore" and found["target_type"] == "channel" and owner_id:
        set_suspended(sc, owner_id, False)
    status = "dismissed" if action == "dismiss" else "resolved"
    if action == "restore":
        sc.execute(
            "UPDATE vi_reports SET status = 'resolved', action = 'restore', resolved_at = ?, resolved_by = ? WHERE id = ?",
            (stamp(), moderator[:120], found["id"]),
        )
    else:
        sc.execute(
            """UPDATE vi_reports SET status = ?, action = ?, resolved_at = ?, resolved_by = ?
            WHERE target_type = ? AND target_id = ? AND status = 'open'""",
            (
                status,
                action,
                stamp(),
                moderator[:120],
                found["target_type"],
                found["target_id"],
            ),
        )
    sc.commit()
    return {"action": action}


# ---------------------------------------------------------------------------
# copyright: notices from anyone, takedowns, counter-notices
# ---------------------------------------------------------------------------

NOTICE_STATUSES = (
    "received",
    "taken_down",
    "rejected",
    "counter_filed",
    "restored",
    "upheld",
)
VIDEO_LINK = re.compile(r"([0-9a-f]{32})")


def file_notice(
    sc: sqlite3.Connection, raw: dict[str, Any], client: str
) -> dict[str, Any]:
    """A copyright owner (anyone, no account needed) asks for a video to come down."""
    client_hash = hashlib.sha256(f"copyright|{client}".encode()).hexdigest()[:24]
    rate(sc, client_hash, "copyright")
    link = kit.text(
        raw.get("video"), "the video's address or id", max_len=500, required=True
    )
    match = VIDEO_LINK.search(link)
    video = (
        row(
            sc,
            "SELECT * FROM vi_videos WHERE id = ? AND deleted_at IS NULL",
            (match.group(1),),
        )
        if match
        else None
    )
    if not video:
        token = re.search(r"/p/([A-Za-z0-9_-]{40,64})", link)
        if token:
            raise ValueError(
                "Send the video's id from the page (below the video): public links are checked by hand."
            )
        raise ValueError(
            "We could not find that video. Paste the address of the video page or its id."
        )
    name = kit.text(raw.get("name"), "your full name", max_len=120, required=True)
    email = kit.email(raw.get("email"), "your email address", required=True)
    organization = kit.text(raw.get("organization"), "who you act for", max_len=160)
    work = kit.text(
        raw.get("work"),
        "the work that was copied",
        max_len=2000,
        required=True,
        multiline=True,
    )
    signature = kit.text(
        raw.get("signature"),
        "your signature (type your full name)",
        max_len=120,
        required=True,
    )
    if not (kit.flag(raw.get("good_faith")) and kit.flag(raw.get("accurate"))):
        raise ValueError(
            "Confirm both statements: that you believe in good faith the use is not allowed, and that the notice is accurate and you may act for the owner."
        )
    notice_id = new_id()
    moment = stamp()
    sc.execute(
        """INSERT INTO vi_copyright (id, video_id, channel_id, claimant_name, claimant_email, claimant_organization, work,
        signature, status, client_hash, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'received', ?, ?, ?)""",
        (
            notice_id,
            video["id"],
            video["channel_id"],
            name,
            email,
            organization,
            work,
            signature,
            client_hash,
            moment,
            moment,
        ),
    )
    sc.commit()
    return {"notice": {"id": notice_id, "status": "received"}}


def notice_view(
    sc: sqlite3.Connection, item: dict[str, Any], *, for_uploader: bool = False
) -> dict[str, Any]:
    video = (
        row(
            sc,
            "SELECT id, title, channel_id FROM vi_videos WHERE id = ?",
            (item["video_id"],),
        )
        or {}
    )
    out = {
        "id": item["id"],
        "status": item["status"],
        "video": {
            "id": video.get("id", item["video_id"]),
            "title": video.get("title", "Removed video"),
        },
        "work": item["work"],
        "claimant_name": item["claimant_name"],
        "claimant_organization": item["claimant_organization"],
        "counter_text": item["counter_text"],
        "counter_at": item["counter_at"],
        "decision_note": item["decision_note"],
        "created_at": item["created_at"],
        "updated_at": item["updated_at"],
    }
    if not for_uploader:
        owner = row(sc, "SELECT * FROM vi_channels WHERE id = ?", (item["channel_id"],))
        out.update(
            {
                "claimant_email": item["claimant_email"],
                "signature": item["signature"],
                "counter_name": item["counter_name"],
                "channel": mini(owner) if owner else None,
                "strikes": strikes(sc, item["channel_id"]) if item["channel_id"] else 0,
            }
        )
    return out


def notices(sc: sqlite3.Connection, status: str = "") -> list[dict[str, Any]]:
    if status:
        kit.choice(status, "the status", NOTICE_STATUSES)
        found = rows(
            sc,
            "SELECT * FROM vi_copyright WHERE status = ? ORDER BY created_at DESC LIMIT 200",
            (status,),
        )
    else:
        found = rows(
            sc, "SELECT * FROM vi_copyright ORDER BY created_at DESC LIMIT 200"
        )
    return [notice_view(sc, item) for item in found]


def my_notices(sc: sqlite3.Connection, ch: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        notice_view(sc, item, for_uploader=True)
        for item in rows(
            sc,
            "SELECT * FROM vi_copyright WHERE channel_id = ? AND status != 'received' AND status != 'rejected' ORDER BY updated_at DESC LIMIT 100",
            (ch["id"],),
        )
    ]


def decide_notice(
    sc: sqlite3.Connection, notice_id: str, action: str, by: str, note: str = ""
) -> dict[str, Any]:
    """``takedown`` (the video comes down, a strike), ``reject`` (the notice is not valid), and after a
    counter-notice ``restore`` (the video returns, the strike ends) or ``uphold`` (it stays down)."""
    found = row(
        sc,
        "SELECT * FROM vi_copyright WHERE id = ?",
        (store.check_id(notice_id, "notice"),),
    )
    if not found:
        raise LookupError("That notice was not found.")
    action = kit.choice(
        action, "the decision", ("takedown", "reject", "restore", "uphold")
    )
    allowed = {
        "takedown": ("received",),
        "reject": ("received",),
        "restore": ("taken_down", "counter_filed", "upheld"),
        "uphold": ("counter_filed",),
    }[action]
    if found["status"] not in allowed:
        raise ValueError(
            f"This notice is {found['status'].replace('_', ' ')}: it cannot be changed that way now."
        )
    note = kit.text(note, "a note", max_len=500, multiline=True)
    status = {
        "takedown": "taken_down",
        "reject": "rejected",
        "restore": "restored",
        "uphold": "upheld",
    }[action]
    moment = stamp()
    sc.execute(
        "UPDATE vi_copyright SET status = ?, decision_note = ?, decided_by = ?, updated_at = ? WHERE id = ?",
        (status, note, by[:120], moment, found["id"]),
    )
    video = row(sc, "SELECT * FROM vi_videos WHERE id = ?", (found["video_id"],))
    if video and action == "takedown":
        sc.execute(
            "UPDATE vi_videos SET takedown_at = ?, updated_at = ? WHERE id = ?",
            (moment, moment, video["id"]),
        )
        notify(
            sc,
            video["channel_id"],
            "copyright",
            f"Your video {video['title']} was taken down after a copyright notice. You can send a counter-notice from Studio.",
            ref_type="notice",
            ref_id=found["id"],
        )
    if video and action == "restore":
        still = row(
            sc,
            "SELECT 1 AS x FROM vi_copyright WHERE video_id = ? AND id != ? AND status IN ('taken_down', 'upheld', 'counter_filed')",
            (video["id"], found["id"]),
        )
        if not still:
            sc.execute(
                "UPDATE vi_videos SET takedown_at = NULL, updated_at = ? WHERE id = ?",
                (moment, video["id"]),
            )
        notify(
            sc,
            video["channel_id"],
            "copyright",
            f"Your video {video['title']} is back.",
            ref_type="notice",
            ref_id=found["id"],
        )
    if video and action == "uphold":
        notify(
            sc,
            video["channel_id"],
            "copyright",
            f"Your counter-notice for {video['title']} was reviewed: the video stays down.",
            ref_type="notice",
            ref_id=found["id"],
        )
    sc.commit()
    return notice_view(
        sc, row(sc, "SELECT * FROM vi_copyright WHERE id = ?", (found["id"],)) or found
    )


def counter_notice(
    sc: sqlite3.Connection, ch: dict[str, Any], notice_id: str, raw: dict[str, Any]
) -> dict[str, Any]:
    """The uploader says the takedown was a mistake (their own work, licensed, or fair use)."""
    found = row(
        sc,
        "SELECT * FROM vi_copyright WHERE id = ? AND channel_id = ?",
        (store.check_id(notice_id, "notice"), ch["id"]),
    )
    if not found:
        raise LookupError("That notice was not found.")
    if found["status"] != "taken_down":
        raise ValueError("A counter-notice can be sent once, after a takedown.")
    if not adult(ch):
        raise PermissionError(
            "Ask an adult in your household to send a counter-notice."
        )
    name = kit.text(raw.get("name"), "your full name", max_len=120, required=True)
    text = kit.text(
        raw.get("statement"),
        "why the video should come back",
        max_len=3000,
        required=True,
        multiline=True,
    )
    if not kit.flag(raw.get("sworn")):
        raise ValueError(
            "Confirm that you believe in good faith the video was removed by mistake."
        )
    moment = stamp()
    sc.execute(
        "UPDATE vi_copyright SET status = 'counter_filed', counter_name = ?, counter_text = ?, counter_at = ?, updated_at = ? WHERE id = ?",
        (name, text, moment, moment, found["id"]),
    )
    sc.commit()
    return notice_view(
        sc,
        row(sc, "SELECT * FROM vi_copyright WHERE id = ?", (found["id"],)) or found,
        for_uploader=True,
    )


# ---------------------------------------------------------------------------
# a person's data: export and erasure (an organization deleted, a member removed)
# ---------------------------------------------------------------------------


def channels_for(
    sc: sqlite3.Connection, *, home: str = "", person_keys: Iterable[str] = ()
) -> list[str]:
    found: list[str] = []
    if home:
        found += [
            item["id"]
            for item in rows(sc, "SELECT id FROM vi_channels WHERE home = ?", (home,))
        ]
    for key in person_keys:
        hit = row(sc, "SELECT id FROM vi_channels WHERE person_key = ?", (key,))
        if hit:
            found.append(hit["id"])
    return list(dict.fromkeys(found))


def export_channel(sc: sqlite3.Connection, channel_id: str) -> dict[str, Any]:
    """What this person made or set (never other people's words); video files stay on the server."""
    found = row(sc, "SELECT * FROM vi_channels WHERE id = ?", (channel_id,))
    if not found:
        raise LookupError("That channel was not found.")
    found.pop("person_key", None)
    return {
        "channel": found,
        "videos": rows(
            sc,
            "SELECT id, kind, title, description, category, tags, rating, visibility, duration, views, likes, created_at FROM vi_videos WHERE channel_id = ? AND deleted_at IS NULL",
            (channel_id,),
        ),
        "comments": rows(
            sc,
            "SELECT id, video_id, text, created_at FROM vi_comments WHERE author_id = ? AND deleted_at IS NULL",
            (channel_id,),
        ),
        "playlists": rows(
            sc,
            "SELECT id, name, created_at FROM vi_playlists WHERE channel_id = ?",
            (channel_id,),
        ),
        "subscriptions": [
            item["channel_id"]
            for item in rows(
                sc,
                "SELECT channel_id FROM vi_subscriptions WHERE subscriber_id = ?",
                (channel_id,),
            )
        ],
        "earnings": rows(
            sc,
            "SELECT month, COUNT(*) AS impressions, SUM(creator_micros) AS earned_micros, earning_status FROM ad_impressions WHERE channel_id = ? AND creator_micros > 0 GROUP BY month, earning_status",
            (channel_id,),
        ),
        "payouts": rows(
            sc,
            "SELECT id, amount_micros, status, reference, requested_at, decided_at FROM ad_payouts WHERE channel_id = ?",
            (channel_id,),
        ),
        "support": row(
            sc,
            "SELECT enabled, link, upi, note, currency, updated_at FROM vi_support WHERE channel_id = ?",
            (channel_id,),
        ),
        "support_received": rows(
            sc,
            "SELECT id, amount_cents, currency, status, reference, created_at, decided_at FROM vi_tips WHERE channel_id = ?",
            (channel_id,),
        ),
        "support_given": rows(
            sc,
            "SELECT id, channel_id, amount_cents, currency, message, status, reference, created_at FROM vi_tips WHERE supporter_id = ?",
            (channel_id,),
        ),
        "membership_plan": row(
            sc,
            "SELECT enabled, price_cents, currency, perks, updated_at FROM vi_member_plans WHERE channel_id = ?",
            (channel_id,),
        ),
        "membership_payments_received": rows(
            sc,
            "SELECT id, months, amount_cents, currency, reference, status, created_at, decided_at FROM vi_member_payments WHERE channel_id = ?",
            (channel_id,),
        ),
        "memberships": rows(
            sc,
            "SELECT channel_id, paid_until, created_at FROM vi_members WHERE member_id = ?",
            (channel_id,),
        ),
        "membership_payments_made": rows(
            sc,
            "SELECT id, channel_id, months, amount_cents, currency, reference, status, created_at FROM vi_member_payments WHERE member_id = ?",
            (channel_id,),
        ),
        "brand_deals": rows(
            sc,
            "SELECT id, brand, contact, amount_cents, currency, video_id, status, due_on, notes, paid_at, created_at FROM vi_deals WHERE channel_id = ?",
            (channel_id,),
        ),
    }


def erase_channels(sc: sqlite3.Connection, channel_ids: Iterable[str]) -> int:
    """Remove these people from Videos: their channel, videos and files, comments, likes, history, playlists,
    subscriptions and reports. Money records stay for the books without the person: impressions on their videos keep
    the amount (advertisers paid it) under a removed channel, unpaid earnings and open payout requests end."""
    ids = [str(item) for item in channel_ids if item]
    for channel_id in ids:
        for video in rows(
            sc, "SELECT * FROM vi_videos WHERE channel_id = ?", (channel_id,)
        ):
            _remove_video(sc, video)
            sc.execute("DELETE FROM vi_views WHERE video_id = ?", (video["id"],))
            sc.execute("DELETE FROM vi_watch_days WHERE video_id = ?", (video["id"],))
            sc.execute(
                "DELETE FROM vi_reports WHERE target_type = 'video' AND target_id = ?",
                (video["id"],),
            )
            sc.execute("DELETE FROM vi_copyright WHERE video_id = ?", (video["id"],))
            sc.execute("DELETE FROM vi_videos WHERE id = ?", (video["id"],))
        media.delete_owner(sc, channel_id)
        playlist_ids = [
            item["id"]
            for item in rows(
                sc, "SELECT id FROM vi_playlists WHERE channel_id = ?", (channel_id,)
            )
        ]
        for playlist_id in playlist_ids:
            sc.execute(
                "DELETE FROM vi_playlist_items WHERE playlist_id = ?", (playlist_id,)
            )
        for statement in (
            "DELETE FROM vi_playlists WHERE channel_id = ?",
            "DELETE FROM vi_comments WHERE author_id = ?",
            "DELETE FROM vi_likes WHERE viewer_id = ?",
            "DELETE FROM vi_later WHERE viewer_id = ?",
            "DELETE FROM vi_watch WHERE viewer_id = ?",
            "DELETE FROM vi_views WHERE viewer_key = ?",
            "DELETE FROM vi_subscriptions WHERE subscriber_id = ? OR channel_id = ?",
            "DELETE FROM vi_notifications WHERE channel_id = ?",
            "DELETE FROM vi_reports WHERE reporter_id = ? OR (target_type = 'channel' AND target_id = ?)",
            "DELETE FROM vi_rate WHERE key = ?",
            "DELETE FROM vi_support WHERE channel_id = ?",
            # pledges to them end; what they gave others stays with the creator, without their name
            "DELETE FROM vi_tips WHERE channel_id = ? OR (supporter_id = ? AND status = 'pledged')",
            "UPDATE vi_tips SET supporter_id = '', show_name = 0, message = '' WHERE supporter_id = ?",
            # their memberships and plan end; payments they made that arrived stay with the creator, without them
            "DELETE FROM vi_member_plans WHERE channel_id = ?",
            "DELETE FROM vi_members WHERE channel_id = ? OR member_id = ?",
            "DELETE FROM vi_member_payments WHERE channel_id = ? OR (member_id = ? AND status = 'pledged')",
            "UPDATE vi_member_payments SET member_id = '' WHERE member_id = ?",
            "DELETE FROM vi_deals WHERE channel_id = ?",
            "UPDATE ad_impressions SET earning_status = CASE WHEN earning_status = 'estimated' THEN 'clawed' ELSE earning_status END, channel_id = '', viewer_key = CASE WHEN viewer_key = ? THEN '' ELSE viewer_key END WHERE channel_id = ?",
            "UPDATE ad_payouts SET status = CASE WHEN status = 'requested' THEN 'rejected' ELSE status END, note = CASE WHEN status = 'requested' THEN 'The account was removed.' ELSE note END, channel_id = '' WHERE channel_id = ?",
            "DELETE FROM vi_channels WHERE id = ?",
        ):
            sc.execute(statement, (channel_id,) * statement.count("?"))
    sc.commit()
    return len(ids)


# ---------------------------------------------------------------------------
# Today and pulse
# ---------------------------------------------------------------------------


def clock(seconds: float) -> str:
    total = max(0, round(float(seconds or 0)))
    hours, minutes, secs = total // 3600, (total % 3600) // 60, total % 60
    return f"{hours}:{minutes:02d}:{secs:02d}" if hours else f"{minutes}:{secs:02d}"


def today_lines(sc: sqlite3.Connection, me_: dict[str, Any]) -> list[dict[str, Any]]:
    """New videos from the channels the person follows (last three days)."""
    found = _list(
        sc,
        me_,
        "v.channel_id IN (SELECT channel_id FROM vi_subscriptions WHERE subscriber_id = ?) AND v.created_at > ?",
        (me_["id"], store.later(-72)),
        limit=3,
    )
    channels = {
        item["id"]: item
        for item in rows(
            sc,
            "SELECT id, name FROM vi_channels WHERE id IN (SELECT channel_id FROM vi_subscriptions WHERE subscriber_id = ?)",
            (me_["id"],),
        )
    }
    saved = {
        item["video_id"]
        for item in rows(
            sc, "SELECT video_id FROM vi_later WHERE viewer_id = ?", (me_["id"],)
        )
    }
    out = []
    for item in found:
        line: dict[str, Any] = {
            "text": item["title"],
            "sub": f"New from {channels.get(item['channel_id'], {}).get('name', 'a channel')}",
            "done": False,  # saved is not watched: no tick, no line through it
        }
        if item["id"] in saved:
            line["sub"] += " · saved to watch later"
        else:  # saved from Today with one press (the route is idempotent)
            line["action"] = life.action(
                "Watch later",
                f"/api/videos/videos/{item['id']}/later",
                body={"on": True},
            )
        out.append(line)
    return out


# ---------------------------------------------------------------------------
# the public video page
# ---------------------------------------------------------------------------


def public_video(sc: sqlite3.Connection, video_id: str, token: str) -> dict[str, Any]:
    try:
        video = _video_row(sc, video_id)
    except LookupError:
        return {"available": False, "title": "This video is no longer shared."}
    if (
        blocked_reason(sc, video)
        or video["visibility"] == "private"
        or video["rating"] == "mature"
        or video.get("members_only")
    ):
        return {"available": False, "title": "This video is not available."}
    if scheduled(video):
        return {"available": False, "title": "This video is not out yet."}
    owner = (
        row(sc, "SELECT * FROM vi_channels WHERE id = ?", (video["channel_id"],)) or {}
    )
    base = f"/api/public/{token}/media"
    file_row = (
        row(sc, "SELECT mime FROM vi_files WHERE id = ?", (video["file_id"],)) or {}
    )
    return {
        "available": True,
        "title": video["title"],
        "video": {
            "id": video["id"],
            "kind": video["kind"],
            "title": video["title"],
            "description": video["description"],
            "rating": video["rating"],
            "genre": video["genre"],
            "credits": store.loads(video["credits"], []),
            "chapters": chapters(video["description"], float(video["duration"] or 0)),
            "duration": video["duration"],
            "width": video["width"],
            "height": video["height"],
            "views": video["views"],
            "likes": video["likes"],
            "created_at": video["created_at"],
            "paid_promotion": bool(video.get("paid_promotion")),
            "channel": {
                "name": owner.get("name", ""),
                "avatar": owner.get("avatar", "🙂"),
            },
            "stream_url": f"{base}/{video['file_id']}",
            "thumb_url": f"{base}/{video['thumb_file_id']}"
            if video["thumb_file_id"]
            else "",
            "captions_url": f"{base}/{video['captions_file_id']}"
            if video["captions_file_id"]
            else "",
            "mime": file_row.get("mime", ""),
        },
    }
