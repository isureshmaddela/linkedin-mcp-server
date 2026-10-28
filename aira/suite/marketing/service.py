"""Marketing domain logic: posts and drafts, campaigns and ROI, tracked links, forms, the bio page, subscribers
and newsletters. Every function takes an open connection on the account's database and validates its input
with the kit (``ValueError`` -> 400, ``LookupError`` -> 404)."""

from __future__ import annotations

import csv
import io
import re
import sqlite3
from collections.abc import Mapping, Sequence
from datetime import datetime, timedelta, timezone
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from aira.suite import kit, public
from aira.suite.marketing import store
from aira.suite.marketing.store import APP

# ---------------------------------------------------------------------------
# constants
# ---------------------------------------------------------------------------

# Each channel's length limit (characters), how many hashtags suit it and the voice a draft takes.
CHANNELS: dict[str, dict[str, Any]] = {
    "instagram": {
        "label": "Instagram",
        "limit": 2200,
        "hashtags": 12,
        "tone": "warm and visual, short paragraphs, a few emoji",
    },
    "facebook": {
        "label": "Facebook",
        "limit": 5000,
        "hashtags": 3,
        "tone": "friendly and conversational, invites comments",
    },
    "linkedin": {
        "label": "LinkedIn",
        "limit": 3000,
        "hashtags": 4,
        "tone": "professional, insight first, no emoji walls",
    },
    "x": {
        "label": "X",
        "limit": 280,
        "hashtags": 2,
        "tone": "punchy, one idea, under 280 characters",
    },
    "tiktok": {
        "label": "TikTok",
        "limit": 2200,
        "hashtags": 5,
        "tone": "casual, a hook in the first line, a script outline",
    },
    "youtube": {
        "label": "YouTube",
        "limit": 5000,
        "hashtags": 3,
        "tone": "a video description with chapters and a call to subscribe",
    },
    "email": {
        "label": "Email",
        "limit": 10000,
        "hashtags": 0,
        "tone": "personal, one clear call to action",
    },
    "blog": {
        "label": "Blog",
        "limit": 20000,
        "hashtags": 0,
        "tone": "helpful, scannable headings, practical steps",
    },
    "whatsapp": {
        "label": "WhatsApp",
        "limit": 1000,
        "hashtags": 0,
        "tone": "short, personal, like a message to a friend",
    },
}
POST_STATUSES = ("idea", "draft", "scheduled", "published")
CAMPAIGN_STATUSES = ("active", "paused", "ended")
SUBMISSION_STATUSES = ("new", "contacted", "won", "lost")
SUBSCRIBER_STATUSES = ("subscribed", "unsubscribed")
FORM_FIELDS = ("name", "email", "phone", "company", "message", "custom")
NEWSLETTER_CAP = 500
MAX_CENTS = 10_000_000_00  # the kit's own ceiling for an amount
PILLARS = (
    ("tip", "A practical tip"),
    ("behind", "Behind the scenes"),
    ("proof", "A customer story"),
    ("offer", "An offer"),
    ("question", "A question for your audience"),
    ("myth", "A myth, busted"),
    ("how", "How it works"),
)
DEFAULT_FIELDS = {
    "name": {"show": True, "required": True},
    "email": {"show": True, "required": True},
    "phone": {"show": False, "required": False},
    "company": {"show": False, "required": False},
    "message": {"show": True, "required": False},
    "custom": {"show": False, "required": False},
}


def _campaign_id(connection: sqlite3.Connection, value: Any) -> str:
    campaign_id = str(value or "").strip()
    if not campaign_id:
        return ""
    if not re.fullmatch(r"[0-9a-f]{32}", campaign_id):
        raise ValueError("Choose a campaign from the list.")
    kit.require_row(connection, "mk_campaigns", campaign_id, "campaign")
    return campaign_id


def _https(value: Any, name: str, *, required: bool = False) -> str:
    cleaned = kit.url(value, name, required=required)
    if cleaned and not cleaned.lower().startswith("https://"):
        raise ValueError(f"{name[:1].upper()}{name[1:]} must start with https://.")
    return cleaned


_NOT_A_NUMBER = re.compile(r"nan|inf", re.IGNORECASE)


def amount(value: Any, name: str, *, default: int = 0) -> int:
    """An amount typed as text ("12.50", "1,200") -> cents, with a 400 for anything that is not a real number.

    ``kit.cents`` lets "Infinity", "sNaN" and huge exponents ("1e400") through to ``Decimal`` arithmetic, which
    raises ``InvalidOperation`` (an ``ArithmeticError``, so the route answered 500), and "NaN" gives Python's own
    message. They are refused here first, in plain words."""
    label = f"{name[:1].upper()}{name[1:]}"
    if value is not None and _NOT_A_NUMBER.search(str(value)):
        raise ValueError(f"{label} must be a number like 12.50.")
    try:
        return kit.cents(value, name, default=default)
    except ArithmeticError:  # an exponent too large for Decimal ("1e400")
        raise ValueError(f"{label} is too large.") from None


def money_field(value: Any, name: str) -> int:
    """Money from the app: an integer is already cents (the UI kit sends cents); text is read as an amount."""
    if isinstance(value, bool):
        raise ValueError(f"{name[:1].upper()}{name[1:]} must be an amount.")
    if isinstance(value, int):
        if value < 0:
            raise ValueError(f"{name[:1].upper()}{name[1:]} cannot be negative.")
        if value > MAX_CENTS:  # too large to store, and never a real amount
            raise ValueError(f"{name[:1].upper()}{name[1:]} is too large.")
        return value
    if isinstance(
        value, float
    ):  # JSON also allows Infinity, NaN and 1e400 (read as inf)
        return amount(repr(value), name)
    return amount(value, name)


def _apply(raw: Mapping[str, Any], current: Mapping[str, Any] | None) -> dict[str, Any]:
    merged = dict(current or {})
    merged.update({key: value for key, value in raw.items()})
    return merged


# ---------------------------------------------------------------------------
# campaigns
# ---------------------------------------------------------------------------


def clean_campaign(
    raw: Mapping[str, Any], current: Mapping[str, Any] | None = None
) -> dict[str, Any]:
    merged = _apply(raw, current)
    name = kit.text(merged.get("name"), "the campaign name", max_len=120, required=True)
    starts = kit.day(merged.get("starts_on"), "the start date")
    ends = kit.day(merged.get("ends_on"), "the end date")
    if starts and ends and ends < starts:
        raise ValueError("The campaign cannot end before it starts.")
    channels = [
        kit.choice(item, "a channel", tuple(CHANNELS))
        for item in (merged.get("channels") or [])
    ]
    utm_campaign = kit.slug(merged.get("utm_campaign") or name)
    return {
        "name": name,
        "goal": kit.text(merged.get("goal"), "the goal", max_len=300),
        "starts_on": starts,
        "ends_on": ends,
        "budget_cents": money_field(merged.get("budget_cents"), "the budget"),
        "spend_cents": money_field(merged.get("spend_cents"), "the spend"),
        "revenue_cents": money_field(merged.get("revenue_cents"), "the revenue"),
        "channels": sorted(set(channels)),
        "utm_source": kit.slug(merged.get("utm_source") or "aira", max_len=60),
        "utm_medium": kit.slug(merged.get("utm_medium") or "social", max_len=60),
        "utm_campaign": utm_campaign,
        "status": kit.choice(
            merged.get("status"), "the status", CAMPAIGN_STATUSES, default="active"
        ),
    }


def _store_values(values: dict[str, Any]) -> dict[str, Any]:
    out = dict(values)
    for column in store.JSON_COLUMNS & out.keys():
        out[column] = kit.dumps(out[column])
    for column in ("add_subscriber", "highlight", "consent", "active"):
        if column in out:
            out[column] = 1 if out[column] else 0
    return out


def _insert(connection: sqlite3.Connection, table: str, values: dict[str, Any]) -> str:
    row_id = kit.new_id()
    stamp = kit.now()
    row = {
        "id": row_id,
        **_store_values(values),
        "created_at": stamp,
        "updated_at": stamp,
    }
    columns = ", ".join(row)
    marks = ", ".join(f":{column}" for column in row)
    connection.execute(f"INSERT INTO {table} ({columns}) VALUES ({marks})", row)  # noqa: S608 - fixed names
    return row_id


def _update(
    connection: sqlite3.Connection, table: str, row_id: str, values: dict[str, Any]
) -> None:
    kit.update_row(
        connection, table, row_id, {**_store_values(values), "updated_at": kit.now()}
    )


def roi_percent(revenue_cents: int, spend_cents: int) -> int | None:
    """Return on spend: (revenue - spend) / spend, in whole percent; None without spend."""
    if spend_cents <= 0:
        return None
    return round((revenue_cents - spend_cents) * 100 / spend_cents)


def campaign_results(
    connection: sqlite3.Connection, campaign: Mapping[str, Any]
) -> dict[str, Any]:
    campaign_id = campaign["id"]
    clicks = store.count(
        connection,
        "SELECT COALESCE(SUM(clicks), 0) FROM mk_links WHERE campaign_id = ?",
        (campaign_id,),
    )
    leads = store.count(
        connection,
        "SELECT COUNT(*) FROM mk_submissions WHERE campaign_id = ?",
        (campaign_id,),
    )
    subscribers = store.count(
        connection,
        "SELECT COUNT(*) FROM mk_subscribers WHERE campaign_id = ?",
        (campaign_id,),
    )
    posts = store.count(
        connection,
        "SELECT COUNT(*) FROM mk_posts WHERE campaign_id = ?",
        (campaign_id,),
    )
    paid = kit.fetch_one(
        connection,
        "SELECT COUNT(*) AS n, COALESCE(SUM(value_cents), 0) AS total FROM mk_submissions WHERE campaign_id = ? AND paid_at IS NOT NULL",
        (campaign_id,),
    ) or {"n": 0, "total": 0}
    spend = int(campaign["spend_cents"] or 0)
    paid_cents = int(paid["total"] or 0)
    # What the business typed in (sales the app cannot see) plus what its leads really paid.
    revenue = int(campaign["revenue_cents"] or 0) + paid_cents
    return {
        "clicks": clicks,
        "leads": leads,
        "subscribers": subscribers,
        "posts": posts,
        "customers": int(paid["n"] or 0),
        "paid_cents": paid_cents,
        "revenue_total_cents": revenue,
        "roi_percent": roi_percent(revenue, spend),
        "cost_per_lead_cents": round(spend / leads) if leads else None,
        "budget_left_cents": int(campaign["budget_cents"] or 0) - spend,
    }


def list_campaigns(connection: sqlite3.Connection) -> list[dict[str, Any]]:
    rows = store.all_rows(
        connection,
        "SELECT * FROM mk_campaigns ORDER BY CASE status WHEN 'active' THEN 0 WHEN 'paused' THEN 1 ELSE 2 END, created_at DESC",
    )
    for row in rows:
        row["results"] = campaign_results(connection, row)
    return rows


def create_campaign(
    connection: sqlite3.Connection, raw: Mapping[str, Any]
) -> dict[str, Any]:
    values = clean_campaign(raw)
    row_id = _insert(connection, "mk_campaigns", values)
    kit.log(
        connection,
        APP,
        "campaign",
        f"Campaign {values['name']} created",
        ref_type="campaign",
        ref_id=row_id,
    )
    connection.commit()
    return get_campaign(connection, row_id)


def get_campaign(connection: sqlite3.Connection, campaign_id: str) -> dict[str, Any]:
    row = store.get(connection, "mk_campaigns", campaign_id, "campaign")
    row["results"] = campaign_results(connection, row)
    return row


def update_campaign(
    connection: sqlite3.Connection, campaign_id: str, raw: Mapping[str, Any]
) -> dict[str, Any]:
    current = store.get(connection, "mk_campaigns", campaign_id, "campaign")
    _update(connection, "mk_campaigns", campaign_id, clean_campaign(raw, current))
    connection.commit()
    return get_campaign(connection, campaign_id)


def delete_campaign(connection: sqlite3.Connection, campaign_id: str) -> None:
    store.delete(connection, "mk_campaigns", campaign_id, "campaign")
    for table in (
        "mk_posts",
        "mk_links",
        "mk_forms",
        "mk_submissions",
        "mk_subscribers",
    ):
        connection.execute(
            f"UPDATE {table} SET campaign_id = '' WHERE campaign_id = ?", (campaign_id,)
        )  # noqa: S608
    connection.commit()


# ---------------------------------------------------------------------------
# posts, drafts, repurposing and the 30-day plan
# ---------------------------------------------------------------------------


def hashtags(value: Any, *, limit: int = 30) -> str:
    words = re.split(r"[\s,]+", str(value or ""))
    out: list[str] = []
    for word in words:
        tag = re.sub(r"[^0-9A-Za-z_]", "", word.lstrip("#"))[:40]
        if tag and f"#{tag}".lower() not in (item.lower() for item in out):
            out.append(f"#{tag}")
    return " ".join(out[:limit])


def fit_to_channel(text: str, channel: str) -> str:
    """Trim ``text`` to the channel's limit at a word boundary (with an ellipsis when it was cut)."""
    limit = int(CHANNELS[channel]["limit"])
    text = text.strip()
    if len(text) <= limit:
        return text
    cut = text[: limit - 1]
    if " " in cut[int(limit * 0.6) :]:
        cut = cut[: cut.rfind(" ")]
    return cut.rstrip(" ,.;:-") + "…"


def clean_post(
    connection: sqlite3.Connection,
    raw: Mapping[str, Any],
    current: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    merged = _apply(raw, current)
    channel = kit.choice(merged.get("channel"), "the channel", tuple(CHANNELS))
    body = kit.text(merged.get("body"), "the post text", max_len=20000, multiline=True)
    limit = int(CHANNELS[channel]["limit"])
    if len(body) > limit:
        raise ValueError(
            f"{CHANNELS[channel]['label']} allows {limit} characters; this post has {len(body)}."
        )
    status = kit.choice(
        merged.get("status"), "the status", POST_STATUSES, default="draft"
    )
    scheduled = kit.moment(merged.get("scheduled_at"), "the time to post")
    if status == "scheduled" and not scheduled:
        raise ValueError("Choose when to post it, or keep it as a draft.")
    return {
        "channel": channel,
        "title": kit.text(merged.get("title"), "the title", max_len=200),
        "body": body,
        "hashtags": hashtags(merged.get("hashtags")),
        "media_url": _https(merged.get("media_url"), "the media link"),
        "scheduled_at": scheduled,
        "status": status,
        "campaign_id": _campaign_id(connection, merged.get("campaign_id")),
        "source": kit.choice(
            merged.get("source"),
            "the source",
            ("manual", "draft", "repurpose", "plan"),
            default="manual",
        ),
    }


def list_posts(
    connection: sqlite3.Connection,
    *,
    start: str = "",
    end: str = "",
    status: str = "",
    channel: str = "",
    campaign_id: str = "",
    unscheduled: bool = False,
    query: str = "",
) -> list[dict[str, Any]]:
    """Posts on the calendar between ``start`` and ``end``, or (``unscheduled``) the ones with no date yet."""
    clauses, params = [], []
    if unscheduled:
        clauses.append("scheduled_at = ''")
    if query:
        clauses.append("(title LIKE ? OR body LIKE ? OR hashtags LIKE ?)")
        params.extend([f"%{query.strip()[:80]}%"] * 3)
    if start:
        clauses.append("scheduled_at >= ?")
        params.append(kit.moment(start, "the start"))
    if end:
        clauses.append("scheduled_at <> '' AND scheduled_at < ?")
        params.append(kit.moment(end, "the end"))
    if status:
        clauses.append("status = ?")
        params.append(kit.choice(status, "the status", POST_STATUSES))
    if channel:
        clauses.append("channel = ?")
        params.append(kit.choice(channel, "the channel", tuple(CHANNELS)))
    if campaign_id:
        clauses.append("campaign_id = ?")
        params.append(campaign_id)
    where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    return store.all_rows(
        connection,
        f"SELECT * FROM mk_posts {where} ORDER BY CASE WHEN scheduled_at = '' THEN 1 ELSE 0 END, scheduled_at, created_at DESC LIMIT 1000",  # noqa: S608
        params,
    )


def create_post(
    connection: sqlite3.Connection, raw: Mapping[str, Any]
) -> dict[str, Any]:
    values = clean_post(connection, raw)
    row_id = _insert(connection, "mk_posts", values)
    connection.commit()
    return store.get(connection, "mk_posts", row_id, "post")


def update_post(
    connection: sqlite3.Connection, post_id: str, raw: Mapping[str, Any]
) -> dict[str, Any]:
    current = store.get(connection, "mk_posts", post_id, "post")
    values = clean_post(connection, raw, current)
    if values["scheduled_at"] != current["scheduled_at"]:
        values["reminded_at"] = None  # moved to a new time: remind again then
    _update(connection, "mk_posts", post_id, values)
    connection.commit()
    return store.get(connection, "mk_posts", post_id, "post")


def publish_post(
    connection: sqlite3.Connection, post_id: str, url: Any
) -> dict[str, Any]:
    post = store.get(connection, "mk_posts", post_id, "post")
    published_url = kit.url(url, "the post's link")
    _update(
        connection,
        "mk_posts",
        post_id,
        {
            "status": "published",
            "published_url": published_url,
            "published_at": kit.now(),
        },
    )
    kit.log(
        connection,
        APP,
        "published",
        f"{CHANNELS[post['channel']]['label']} post published",
        detail=post["title"] or post["body"][:80],
        ref_type="post",
        ref_id=post_id,
    )
    connection.commit()
    return store.get(connection, "mk_posts", post_id, "post")


def template_post(
    channel: str, topic: str, goal: str, business: Mapping[str, Any]
) -> dict[str, str]:
    """A useful structure for the channel when no model runs: hook, value, proof, call to action."""
    name = business.get("name") or "we"
    topic = topic or "what we do"
    cta = {
        "awareness": "Follow for more.",
        "leads": "Message us or tap the link to get started.",
        "sales": "Book or order today, the link is in our bio.",
        "engagement": "Tell us in the comments.",
    }.get(goal, "Get in touch to learn more.")
    tag_words = [word for word in re.findall(r"[A-Za-z]{3,}", topic)][:3]
    base_tags = hashtags(
        " ".join(
            tag_words + [re.sub(r"[^A-Za-z]", "", str(business.get("name") or ""))]
        )
    )
    if channel == "x":
        text = f"{topic.capitalize()}: the one thing most people get wrong, and how {name} fixes it. {cta}"
    elif channel == "linkedin":
        text = (
            f"{topic.capitalize()} is harder than it looks.\n\n"
            "Here is what we have learned working on it every day:\n"
            "1. Start with the outcome your customer wants.\n"
            "2. Remove one step that slows them down.\n"
            "3. Measure what changed.\n\n"
            f"At {name}, this is how we work. {cta}"
        )
    elif channel == "tiktok":
        text = (
            f'HOOK (first 2 seconds): "Stop doing this if you care about {topic}."\n'
            "SHOW: the common mistake.\nTEACH: the better way, in three quick cuts.\n"
            f"END: {cta}"
        )
    elif channel == "youtube":
        text = (
            f"{topic.capitalize()}, explained step by step.\n\n"
            "Chapters:\n00:00 Why it matters\n01:00 The common mistake\n03:00 The better way\n06:00 What to do next\n\n"
            f"Subscribe for more from {name}. {cta}"
        )
    elif channel == "email":
        text = (
            "Hi there,\n\n"
            f"A quick note about {topic}. Most people we talk to want the result without the hassle, so here is the "
            "shortest path we know:\n\n- One thing to start today\n- One thing to stop\n- One thing to ask us\n\n"
            f"{cta}\n\nThanks for reading,\n{name}"
        )
    elif channel == "blog":
        text = (
            f"# {topic.capitalize()}: a practical guide\n\n"
            "## Why it matters\nExplain the problem in your customer's words.\n\n"
            "## The common mistake\nWhat people try first, and why it does not work.\n\n"
            "## A better way\nThree steps, each with an example.\n\n"
            f"## Next step\n{cta}"
        )
    elif channel == "whatsapp":
        text = f"Hi! Quick one from {name}: we have something new on {topic}. {cta}"
    else:  # instagram, facebook
        text = (
            f"{topic.capitalize()} ✨\n\n"
            "Swipe through for the three things we wish everyone knew.\n\n"
            f"Save this for later and share it with a friend who needs it. {cta}"
        )
    limit = int(CHANNELS[channel]["hashtags"])
    return {
        "title": f"{topic.capitalize()}"[:120],
        "text": fit_to_channel(text, channel),
        "hashtags": " ".join(base_tags.split()[:limit]) if limit else "",
    }


def draft_post(
    connection: sqlite3.Connection, raw: Mapping[str, Any]
) -> dict[str, Any]:
    """A draft for one channel from a topic and a goal (the model when it runs, the template otherwise)."""
    channel = kit.choice(raw.get("channel"), "the channel", tuple(CHANNELS))
    topic = kit.text(raw.get("topic"), "a topic", max_len=300, required=True)
    goal = kit.choice(
        raw.get("goal"),
        "the goal",
        ("awareness", "leads", "sales", "engagement"),
        default="leads",
    )
    profile = kit.business(connection)
    spec = CHANNELS[channel]
    fallback = template_post(channel, topic, goal, profile)
    prompt = (
        f"Write one {spec['label']} post for the business {profile.get('name') or 'a small business'}"
        f" ({profile.get('tagline') or 'no tagline'}). Topic: {topic}. Goal: {goal}. Tone: {spec['tone']}. "
        f"At most {spec['limit']} characters for text and at most {spec['hashtags']} hashtags. "
        'Answer JSON {"title": short title, "text": the post, "hashtags": "#a #b"}. '
        "Do not invent prices, offers, statistics or customer names."
    )
    result = kit.draft(prompt, fallback)
    return {
        "channel": channel,
        "title": kit.text(str(result["title"])[:200], "the title", max_len=200),
        "text": fit_to_channel(str(result["text"]), channel),
        "hashtags": " ".join(
            hashtags(result["hashtags"]).split()[: int(spec["hashtags"])]
        )
        if spec["hashtags"]
        else "",
        "source": result["source"],
    }


def repurpose(
    connection: sqlite3.Connection, post_id: str, channels: Sequence[Any]
) -> list[dict[str, Any]]:
    """Turn one post into drafts for other channels, each fitted to that channel."""
    post = store.get(connection, "mk_posts", post_id, "post")
    targets = [
        kit.choice(item, "a channel", tuple(CHANNELS))
        for item in channels
        if str(item) != post["channel"]
    ]
    if not targets:
        raise ValueError("Choose at least one other channel.")
    profile = kit.business(connection)
    created = []
    for channel in dict.fromkeys(targets):
        spec = CHANNELS[channel]
        fallback = {
            "title": post["title"] or post["body"][:60],
            "text": fit_to_channel(post["body"], channel),
            "hashtags": " ".join(post["hashtags"].split()[: int(spec["hashtags"])])
            if spec["hashtags"]
            else "",
        }
        prompt = (
            f"Rewrite this {CHANNELS[post['channel']]['label']} post for {spec['label']} for the business "
            f"{profile.get('name') or 'a small business'}. Tone: {spec['tone']}. At most {spec['limit']} characters. "
            'Keep every fact; add nothing new. Answer JSON {"title", "text", "hashtags"}.\n\n'
            f"{post['body'][:4000]}"
        )
        result = kit.draft(prompt, fallback)
        values = {
            "channel": channel,
            "title": str(result["title"])[:200],
            "body": fit_to_channel(str(result["text"]), channel),
            "hashtags": result["hashtags"] if spec["hashtags"] else "",
            "status": "draft",
            "campaign_id": post["campaign_id"],
            "media_url": post["media_url"],
            "source": "repurpose",
        }
        created.append(create_post(connection, values))
    return created


def _zone(name: Any) -> ZoneInfo | None:
    """A time zone by its IANA name (``America/New_York``); None when it is empty or unknown (then UTC)."""
    text = str(name or "").strip()
    if not text or len(text) > 60 or text.upper() == "UTC":
        return None
    try:
        return ZoneInfo(text)
    except (ZoneInfoNotFoundError, ValueError):
        return None


def _local_moment(day: str, hour: int, zone: ZoneInfo | None) -> str:
    """``hour``:00 on ``day`` where the person is, as an ISO UTC moment."""
    local = datetime.fromisoformat(day).replace(hour=hour, tzinfo=zone or timezone.utc)
    return local.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def content_plan(
    connection: sqlite3.Connection, raw: Mapping[str, Any]
) -> dict[str, Any]:
    """A 30-day plan (one idea a day, rotating channels and content pillars); ``save`` adds them as ideas."""
    description = (
        kit.text(raw.get("description"), "what the business does", max_len=500)
        or kit.business(connection)["tagline"]
    )
    if not description:
        raise ValueError("Describe what your business does so the plan fits it.")
    goals = kit.text(raw.get("goals"), "your goals", max_len=300) or "more customers"
    channels = [
        kit.choice(item, "a channel", tuple(CHANNELS))
        for item in (raw.get("channels") or ["instagram", "facebook", "linkedin"])
    ]
    if not channels:
        raise ValueError("Choose at least one channel.")
    days = kit.integer(
        raw.get("days"), "the number of days", default=30, minimum=7, maximum=60
    )
    start = (
        kit.day(raw.get("start"), "the first day")
        or (kit.utcnow().date() + timedelta(days=1)).isoformat()
    )
    first = datetime.fromisoformat(start)
    zone = _zone(raw.get("time_zone") or kit.business(connection).get("timezone"))
    post_hour = kit.integer(
        raw.get("hour"), "the hour to post", default=9, minimum=0, maximum=23
    )
    fallback_ideas = []
    for index in range(days):
        pillar, label = PILLARS[index % len(PILLARS)]
        channel = channels[index % len(channels)]
        fallback_ideas.append(
            {
                "day": (first + timedelta(days=index)).date().isoformat(),
                "channel": channel,
                "pillar": pillar,
                "title": f"{label}: {description[:60]}",
                "text": f"{label} about {description[:120]}. Goal: {goals[:80]}.",
            }
        )
    prompt = (
        f"Plan {days} days of social posts for a business that does: {description}. Goals: {goals}. "
        f"Channels to rotate: {', '.join(channels)}. Mix tips, behind the scenes, customer stories, offers and questions. "
        'Answer JSON {"ideas": [{"day": "YYYY-MM-DD", "channel": one of the channels, "pillar": short word, '
        f'"title": short title, "text": one or two sentences}}]}} starting {start}, one idea per day.'
    )
    result = kit.draft(prompt, {"ideas": fallback_ideas})
    ideas = []
    for index, item in enumerate(result["ideas"][:days]):
        if not isinstance(item, dict):
            continue
        fallback = fallback_ideas[min(index, len(fallback_ideas) - 1)]
        channel = str(item.get("channel") or fallback["channel"])
        if channel not in CHANNELS:
            channel = fallback["channel"]
        try:
            day = kit.day(item.get("day")) or fallback["day"]
        except ValueError:
            day = fallback["day"]
        ideas.append(
            {
                "day": day,
                "channel": channel,
                "pillar": kit.text(
                    str(item.get("pillar") or fallback["pillar"])[:40],
                    "the pillar",
                    max_len=40,
                ),
                "title": str(item.get("title") or fallback["title"])[:200],
                "text": fit_to_channel(
                    str(item.get("text") or fallback["text"]), channel
                ),
            }
        )
    if len(ideas) < days:
        ideas.extend(fallback_ideas[len(ideas) :])
    saved: list[str] = []
    if kit.flag(raw.get("save")):
        for idea in ideas:
            post = create_post(
                connection,
                {
                    "channel": idea["channel"],
                    "title": idea["title"],
                    "body": idea["text"],
                    "scheduled_at": _local_moment(idea["day"], post_hour, zone),
                    "status": "idea",
                    "campaign_id": raw.get("campaign_id") or "",
                    "source": "plan",
                },
            )
            saved.append(post["id"])
        kit.log(
            connection,
            APP,
            "plan",
            f"A {days}-day content plan was added to the calendar",
        )
        connection.commit()
    return {"ideas": ideas, "saved": len(saved), "source": result["source"]}


# ---------------------------------------------------------------------------
# tracked links and clicks
# ---------------------------------------------------------------------------


def utm_url(
    destination: str,
    *,
    source: str = "",
    medium: str = "",
    campaign: str = "",
    content: str = "",
) -> str:
    """The destination with UTM tags added (replacing any already there); other query values are kept."""
    parts = urlsplit(destination)
    query = [
        (key, value)
        for key, value in parse_qsl(parts.query, keep_blank_values=True)
        if not key.lower().startswith("utm_")
    ]
    for key, value in (
        ("utm_source", source),
        ("utm_medium", medium),
        ("utm_campaign", campaign),
        ("utm_content", content),
    ):
        if value:
            query.append((key, value))
    return urlunsplit(
        (parts.scheme, parts.netloc, parts.path, urlencode(query), parts.fragment)
    )


def unique_slug(
    connection: sqlite3.Connection, wanted: str, exclude_id: str = ""
) -> str:
    base = kit.slug(wanted, max_len=50)
    candidate, number = base, 1
    while kit.fetch_one(
        connection,
        "SELECT id FROM mk_links WHERE slug = ? AND id != ?",
        (candidate, exclude_id),
    ):
        number += 1
        candidate = f"{base}-{number}"
    return candidate


def clean_link(
    connection: sqlite3.Connection,
    raw: Mapping[str, Any],
    current: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    merged = _apply(raw, current)
    destination = _https(merged.get("destination"), "the destination", required=True)
    title = kit.text(merged.get("title"), "the name", max_len=120)
    campaign_id = _campaign_id(connection, merged.get("campaign_id"))
    campaign = (
        kit.fetch_one(
            connection, "SELECT * FROM mk_campaigns WHERE id = ?", (campaign_id,)
        )
        if campaign_id
        else None
    )
    slug_source = merged.get("slug") or title or urlsplit(destination).netloc
    return {
        "slug": unique_slug(
            connection, str(slug_source), str((current or {}).get("id") or "")
        ),
        "title": title,
        "destination": destination,
        "campaign_id": campaign_id,
        "utm_source": kit.slug(
            merged.get("utm_source") or (campaign or {}).get("utm_source") or "aira",
            max_len=60,
        ),
        "utm_medium": kit.slug(
            merged.get("utm_medium") or (campaign or {}).get("utm_medium") or "link",
            max_len=60,
        ),
        "utm_campaign": kit.slug(merged["utm_campaign"], max_len=60)
        if merged.get("utm_campaign")
        else str((campaign or {}).get("utm_campaign") or ""),
        "utm_content": kit.slug(merged.get("utm_content"), max_len=60)
        if merged.get("utm_content")
        else "",
    }


def final_url(link: Mapping[str, Any]) -> str:
    return utm_url(
        link["destination"],
        source=link["utm_source"],
        medium=link["utm_medium"],
        campaign=link["utm_campaign"],
        content=link["utm_content"],
    )


def _link_view(connection: sqlite3.Connection, link: dict[str, Any]) -> dict[str, Any]:
    link["final_url"] = final_url(link)
    link["public"] = public.link_for(connection, APP, "go", link["id"])
    link["clicks_month"] = store.count(
        connection,
        "SELECT COUNT(*) FROM mk_clicks WHERE kind = 'link' AND target_id = ? AND at >= ?",
        (link["id"], _month_start()),
    )
    return link


def list_links(connection: sqlite3.Connection) -> list[dict[str, Any]]:
    return [
        _link_view(connection, row)
        for row in store.all_rows(
            connection, "SELECT * FROM mk_links ORDER BY created_at DESC"
        )
    ]


def get_link(connection: sqlite3.Connection, link_id: str) -> dict[str, Any]:
    return _link_view(connection, store.get(connection, "mk_links", link_id, "link"))


def create_link(
    connection: sqlite3.Connection, raw: Mapping[str, Any]
) -> dict[str, Any]:
    values = clean_link(connection, raw)
    row_id = _insert(connection, "mk_links", values)
    connection.commit()
    public.create_link(
        connection, APP, "go", row_id, title=values["title"] or values["slug"]
    )
    kit.log(
        connection,
        APP,
        "link",
        f"Tracked link {values['slug']} created",
        ref_type="link",
        ref_id=row_id,
    )
    connection.commit()
    return get_link(connection, row_id)


def update_link(
    connection: sqlite3.Connection, link_id: str, raw: Mapping[str, Any]
) -> dict[str, Any]:
    current = store.get(connection, "mk_links", link_id, "link")
    _update(connection, "mk_links", link_id, clean_link(connection, raw, current))
    connection.commit()
    return get_link(connection, link_id)


def delete_link(connection: sqlite3.Connection, link_id: str) -> None:
    store.delete(connection, "mk_links", link_id, "link")
    public.revoke(connection, APP, "go", link_id)
    connection.execute(
        "DELETE FROM mk_clicks WHERE kind = 'link' AND target_id = ?", (link_id,)
    )
    connection.commit()


def record_click(connection: sqlite3.Connection, kind: str, target_id: str) -> None:
    stamp = kit.now()
    connection.execute(
        "INSERT INTO mk_clicks (id, kind, target_id, day, at) VALUES (?, ?, ?, ?, ?)",
        (kit.new_id(), kind, target_id, stamp[:10], stamp),
    )
    table = {"link": "mk_links", "bio": "mk_bio_links"}[kind]
    connection.execute(
        f"UPDATE {table} SET clicks = clicks + 1 WHERE id = ?", (target_id,)
    )  # noqa: S608


def clicks_by_day(
    connection: sqlite3.Connection, kind: str, target_id: str, days: int = 30
) -> list[dict[str, Any]]:
    today = kit.utcnow().date()
    keys = [
        (today - timedelta(days=offset)).isoformat()
        for offset in range(days - 1, -1, -1)
    ]
    counts = {
        row["day"]: int(row["n"])
        for row in kit.fetch_all(
            connection,
            "SELECT day, COUNT(*) AS n FROM mk_clicks WHERE kind = ? AND target_id = ? AND day >= ? GROUP BY day",
            (kind, target_id, keys[0]),
        )
    }
    return [{"day": key, "clicks": counts.get(key, 0)} for key in keys]


# ---------------------------------------------------------------------------
# lead-capture forms and submissions
# ---------------------------------------------------------------------------


def clean_form(
    connection: sqlite3.Connection,
    raw: Mapping[str, Any],
    current: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    merged = _apply(raw, current)
    raw_fields = merged.get("fields")
    given: dict[str, Any] = raw_fields if isinstance(raw_fields, dict) else {}
    fields: dict[str, dict[str, bool]] = {}
    for name in FORM_FIELDS:
        candidate = given.get(name)
        spec: dict[str, Any] = (
            candidate if isinstance(candidate, dict) else DEFAULT_FIELDS[name]
        )
        show = kit.flag(spec.get("show", DEFAULT_FIELDS[name]["show"]))
        fields[name] = {
            "show": show,
            "required": show and kit.flag(spec.get("required", False)),
        }
    if not (fields["email"]["show"] or fields["phone"]["show"]):
        raise ValueError(
            "Ask for an email or a phone number, or you will not be able to reply."
        )
    custom_question = kit.text(
        merged.get("custom_question"), "the custom question", max_len=200
    )
    if fields["custom"]["show"] and not custom_question:
        raise ValueError("Write the custom question, or hide it.")
    return {
        "title": kit.text(
            merged.get("title"), "the form title", max_len=120, required=True
        ),
        "intro": kit.text(
            merged.get("intro"), "the introduction", max_len=1000, multiline=True
        ),
        "fields": fields,
        "custom_question": custom_question,
        "consent_text": kit.text(
            merged.get("consent_text"), "the consent text", max_len=300
        )
        or "Yes, send me news and offers by email. I can unsubscribe at any time.",
        "thank_you": kit.text(
            merged.get("thank_you"),
            "the thank-you message",
            max_len=500,
            multiline=True,
        )
        or "Thank you! We will get back to you soon.",
        "add_subscriber": kit.flag(merged.get("add_subscriber", True)),
        "tag": kit.tags(merged.get("tag"))[0] if kit.tags(merged.get("tag")) else "",
        "campaign_id": _campaign_id(connection, merged.get("campaign_id")),
        "status": kit.choice(
            merged.get("status"), "the status", ("active", "closed"), default="active"
        ),
    }


def conversion(submissions: int, views: int) -> int | None:
    """Leads per page view, in whole percent, never above 100 (one visit can send a form twice)."""
    if not views:
        return None
    return min(100, round(submissions * 100 / views))


def _form_view(connection: sqlite3.Connection, form: dict[str, Any]) -> dict[str, Any]:
    form["public"] = public.link_for(connection, APP, "form", form["id"])
    form["conversion_percent"] = conversion(form["submissions"], form["views"])
    form["new_submissions"] = store.count(
        connection,
        "SELECT COUNT(*) FROM mk_submissions WHERE form_id = ? AND status = 'new'",
        (form["id"],),
    )
    return form


def list_forms(connection: sqlite3.Connection) -> list[dict[str, Any]]:
    return [
        _form_view(connection, row)
        for row in store.all_rows(
            connection, "SELECT * FROM mk_forms ORDER BY created_at DESC"
        )
    ]


def get_form(connection: sqlite3.Connection, form_id: str) -> dict[str, Any]:
    return _form_view(connection, store.get(connection, "mk_forms", form_id, "form"))


def create_form(
    connection: sqlite3.Connection, raw: Mapping[str, Any]
) -> dict[str, Any]:
    values = clean_form(connection, raw)
    row_id = _insert(connection, "mk_forms", values)
    kit.log(
        connection,
        APP,
        "form",
        f"Form {values['title']} created",
        ref_type="form",
        ref_id=row_id,
    )
    connection.commit()
    return get_form(connection, row_id)


def update_form(
    connection: sqlite3.Connection, form_id: str, raw: Mapping[str, Any]
) -> dict[str, Any]:
    current = store.get(connection, "mk_forms", form_id, "form")
    _update(connection, "mk_forms", form_id, clean_form(connection, raw, current))
    connection.commit()
    return get_form(connection, form_id)


def delete_form(connection: sqlite3.Connection, form_id: str) -> None:
    store.delete(connection, "mk_forms", form_id, "form")
    public.revoke(connection, APP, "form", form_id)
    connection.commit()


def public_form(form: Mapping[str, Any]) -> dict[str, Any]:
    """What a visitor sees: the questions, never the submissions or settings."""
    return {
        "title": form["title"],
        "intro": form["intro"],
        "fields": {
            name: spec for name, spec in form["fields"].items() if spec.get("show")
        },
        "custom_question": form["custom_question"]
        if form["fields"].get("custom", {}).get("show")
        else "",
        "consent_text": form["consent_text"] if form["add_subscriber"] else "",
        "open": form["status"] == "active",
    }


FIELD_LABELS = {
    "name": "your name",
    "email": "your email",
    "phone": "your phone number",
    "company": "your company",
    "message": "a message",
    "custom": "an answer",
}


def submit(
    connection: sqlite3.Connection, form_id: str, payload: Mapping[str, Any]
) -> dict[str, Any]:
    """A visitor sends the form. Validates against the form's own fields; returns the thank-you message."""
    form = store.get(connection, "mk_forms", form_id, "form")
    if form["status"] != "active":
        raise ValueError("This form is closed.")
    if str(payload.get("website") or "").strip():
        return {
            "thank_you": form["thank_you"],
            "stored": False,
        }  # a bot filled the hidden field
    fields = form["fields"]
    values: dict[str, str] = {}
    for name in FORM_FIELDS:
        spec = fields.get(name, {})
        if not spec.get("show"):
            continue
        raw = payload.get(name)
        if name == "email":
            values[name] = kit.email(
                raw, FIELD_LABELS[name], required=bool(spec.get("required"))
            )
        elif name == "phone":
            values[name] = kit.phone(
                raw, FIELD_LABELS[name], required=bool(spec.get("required"))
            )
        else:
            long = name in ("message", "custom")
            values[name] = kit.text(
                raw,
                FIELD_LABELS[name],
                max_len=2000 if long else 120,
                required=bool(spec.get("required")),
                multiline=long,
            )
    if not (values.get("email") or values.get("phone")):
        raise ValueError("Enter your email or phone number so we can reply.")
    consent = bool(form["add_subscriber"]) and kit.flag(payload.get("consent"))
    if consent and not values.get("email"):
        raise ValueError("Enter your email to receive news.")
    submission_id = _insert(
        connection,
        "mk_submissions",
        {
            "form_id": form_id,
            "name": values.get("name", ""),
            "email": values.get("email", ""),
            "phone": values.get("phone", ""),
            "company": values.get("company", ""),
            "message": values.get("message", ""),
            "answer": values.get("custom", ""),
            "consent": consent,
            "campaign_id": form["campaign_id"],
            "status": "new",
        },
    )
    connection.execute(
        "UPDATE mk_forms SET submissions = submissions + 1 WHERE id = ?", (form_id,)
    )
    if consent:
        add_subscriber(
            connection,
            {
                "email": values["email"],
                "name": values.get("name", ""),
                "tags": [form["tag"]] if form["tag"] else [],
            },
            source=f"form:{form['title'][:40]}",
            campaign_id=form["campaign_id"],
            consent=True,
            commit=False,
        )
    who = values.get("name") or values.get("email") or values.get("phone") or "Someone"
    kit.log(
        connection,
        APP,
        "submission",
        f"{who} sent {form['title']}",
        ref_type="submission",
        ref_id=submission_id,
        public=True,
    )
    profile = kit.business(connection)
    if profile.get("email"):
        lines = [
            f"{label.split(' ', 1)[-1].capitalize()}: {values[name]}"
            for name, label in FIELD_LABELS.items()
            if values.get(name)
        ]
        kit.queue_email(
            connection,
            APP,
            "form_submission",
            to=profile["email"],
            subject=f"New enquiry: {form['title']}",
            body="Someone filled in your form.\n\n"
            + "\n".join(lines)
            + f"\nWants news by email: {'yes' if consent else 'no'}",
            ref_type="submission",
            ref_id=submission_id,
        )
    connection.commit()
    return {"thank_you": form["thank_you"], "stored": True}


SUBMISSION_SOURCES = ("form", "media_kit")
SOURCE_LABELS = {"form": "Form", "media_kit": "Sponsorship"}


def list_submissions(
    connection: sqlite3.Connection,
    *,
    form_id: str = "",
    status: str = "",
    query: str = "",
    source: str = "",
    due: bool = False,
    limit: int = 500,
) -> list[dict[str, Any]]:
    """The leads inbox: filter by form, status, where it came from, words, or follow-ups that are due."""
    clauses, params = [], []
    if form_id:
        clauses.append("s.form_id = ?")
        params.append(form_id)
    if status:
        clauses.append("s.status = ?")
        params.append(kit.choice(status, "the status", SUBMISSION_STATUSES))
    if source:
        clauses.append("s.source = ?")
        params.append(kit.choice(source, "the source", SUBMISSION_SOURCES))
    if query:
        clauses.append(
            "(s.name LIKE ? OR s.email LIKE ? OR s.phone LIKE ? OR s.company LIKE ? OR s.message LIKE ? OR s.notes LIKE ?)"
        )
        params.extend([f"%{query.strip()[:80]}%"] * 6)
    if due:
        clauses.append(
            "s.follow_up_on <> '' AND s.follow_up_on <= ? AND s.status IN ('new', 'contacted')"
        )
        params.append(kit.today())
    where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    rows = store.all_rows(
        connection,
        f"SELECT s.*, f.title AS form_title FROM mk_submissions s LEFT JOIN mk_forms f ON f.id = s.form_id {where} ORDER BY s.created_at DESC LIMIT ?",  # noqa: S608
        (*params, max(1, min(limit, 5000))),
    )
    for row in rows:
        _lead_view(row)
    return rows


def _lead_view(row: dict[str, Any]) -> dict[str, Any]:
    row["source_label"] = SOURCE_LABELS.get(row.get("source") or "form", "Form")
    if not row.get("form_title"):
        row["form_title"] = (
            "Media kit" if row.get("source") == "media_kit" else "A deleted form"
        )
    row["paid"] = bool(row.get("paid_at"))
    row["value"] = f"{int(row.get('value_cents') or 0) / 100:.2f}"
    row["follow_up_due"] = bool(
        row.get("follow_up_on")
        and row["follow_up_on"] <= kit.today()
        and row.get("status") in ("new", "contacted")
    )
    return row


def get_submission(
    connection: sqlite3.Connection, submission_id: str
) -> dict[str, Any]:
    row = kit.fetch_one(
        connection,
        "SELECT s.*, f.title AS form_title FROM mk_submissions s LEFT JOIN mk_forms f ON f.id = s.form_id WHERE s.id = ?",
        (submission_id,),
    )
    if not row:
        raise LookupError("That lead was not found.")
    return _lead_view(store.decode(row))


def _who(lead: Mapping[str, Any]) -> str:
    return str(
        lead.get("company")
        or lead.get("name")
        or lead.get("email")
        or lead.get("phone")
        or "A lead"
    )


def _record_lead_revenue(
    connection: sqlite3.Connection, lead: Mapping[str, Any]
) -> None:
    what = lead.get("package") or lead.get("form_title") or "Marketing lead"
    kit.record_revenue(
        connection,
        APP,
        source_type="lead",
        source_id=lead["id"],
        amount_cents=int(lead["value_cents"] or 0),
        label=f"{what}: {_who(lead)}"[:200],
        customer=_who(lead),
        at=lead.get("paid_at") or "",
    )
    if lead.get("paid_at"):
        # kit.record_revenue keeps the first date when the line already exists (paid, undone, paid again later):
        # the payment counts in the month it really came, the same month the Marketing overview shows.
        connection.execute(
            "UPDATE suite_revenue SET at = ? WHERE app = ? AND source_type = 'lead' AND source_id = ?",
            (lead["paid_at"], APP, lead["id"]),
        )


def update_submission(
    connection: sqlite3.Connection, submission_id: str, raw: Mapping[str, Any]
) -> dict[str, Any]:
    """Status, notes, the deal's value and a follow-up date. A paid lead keeps its revenue line in step with
    its value; moving it away from won takes the payment off (the money did not come)."""
    current = get_submission(connection, submission_id)
    values: dict[str, Any] = {
        "status": kit.choice(
            raw.get("status", current["status"]), "the status", SUBMISSION_STATUSES
        ),
        "notes": kit.text(
            raw.get("notes", current["notes"]),
            "the notes",
            max_len=2000,
            multiline=True,
        ),
    }
    if "value_cents" in raw:
        values["value_cents"] = money_field(raw.get("value_cents"), "the value")
    if "follow_up_on" in raw:
        values["follow_up_on"] = kit.day(raw.get("follow_up_on"), "the follow-up date")
    if values["status"] != "new" and not current.get("contacted_at"):
        values["contacted_at"] = kit.now()
    paid = bool(current["paid_at"])
    if paid and (
        values["status"] != "won" or int(values.get("value_cents", 1) or 0) <= 0
    ):
        values["paid_at"] = None
        kit.void_revenue(connection, APP, "lead", submission_id)
        paid = False
    _update(connection, "mk_submissions", submission_id, values)
    if values["status"] != current["status"] and values["status"] == "won":
        kit.log(
            connection,
            APP,
            "won",
            f"{current['name'] or current['email'] or current['phone']} became a customer",
            ref_type="submission",
            ref_id=submission_id,
        )
    lead = get_submission(connection, submission_id)
    if paid:
        _record_lead_revenue(connection, lead)
    connection.commit()
    return lead


def mark_paid(
    connection: sqlite3.Connection, submission_id: str, raw: Mapping[str, Any]
) -> dict[str, Any]:
    """The customer paid you (through your own payment link, a transfer or cash): the lead becomes a won
    customer and its value counts as revenue in the Business hub, once."""
    lead = get_submission(connection, submission_id)
    value = (
        money_field(raw.get("value_cents"), "the amount")
        if raw.get("value_cents") not in (None, "")
        else int(lead["value_cents"] or 0)
    )
    if value <= 0:
        raise ValueError("Enter how much they paid.")
    first_time = not lead["paid_at"]
    _update(
        connection,
        "mk_submissions",
        submission_id,
        {
            "status": "won",
            "value_cents": value,
            "paid_at": lead["paid_at"] or kit.now(),
        },
    )
    lead = get_submission(connection, submission_id)
    _record_lead_revenue(connection, lead)
    if first_time:
        kit.log(
            connection,
            APP,
            "paid",
            f"{_who(lead)} paid",
            ref_type="submission",
            ref_id=submission_id,
            amount_cents=value,
        )
    connection.commit()
    return lead


def mark_unpaid(connection: sqlite3.Connection, submission_id: str) -> dict[str, Any]:
    """Marked paid by mistake, or refunded: the revenue line stops counting (it stays for the record)."""
    get_submission(connection, submission_id)
    _update(connection, "mk_submissions", submission_id, {"paid_at": None})
    kit.void_revenue(connection, APP, "lead", submission_id)
    connection.commit()
    return get_submission(connection, submission_id)


def delete_submission(connection: sqlite3.Connection, submission_id: str) -> None:
    store.delete(connection, "mk_submissions", submission_id, "submission")
    kit.void_revenue(connection, APP, "lead", submission_id)
    connection.commit()


def reply_template(
    connection: sqlite3.Connection, lead: Mapping[str, Any]
) -> dict[str, str]:
    """A starting point for a reply: a greeting, a thank-you and your name (you write the middle)."""
    profile = kit.business(connection)
    first = str(lead.get("name") or "").split(" ")[0]
    about = lead.get("package") or lead.get("form_title") or "your message"
    return {
        "subject": f"Re: {about}"[:200],
        "body": f"Hi {first or 'there'},\n\nThank you for getting in touch with {profile.get('name') or 'us'}.\n\n"
        f"\n\nBest wishes,\n{profile.get('name') or ''}".rstrip(),
    }


def reply_to_lead(
    connection: sqlite3.Connection, submission_id: str, raw: Mapping[str, Any]
) -> dict[str, Any]:
    """Write back to a lead by email: the message waits in Business hub > Outbox until you approve it (or goes
    by itself if you turned on automatic sending for ``lead_reply``). A new lead becomes contacted."""
    lead = get_submission(connection, submission_id)
    if not lead["email"]:
        raise ValueError("This lead left no email address. Call them instead.")
    subject = kit.text(raw.get("subject"), "the subject", max_len=200, required=True)
    body = kit.text(
        raw.get("body"), "the message", max_len=10000, required=True, multiline=True
    )
    message = kit.queue_email(
        connection,
        APP,
        "lead_reply",
        to=lead["email"],
        subject=subject,
        body=body,
        ref_type="submission",
        ref_id=submission_id,
    )
    values: dict[str, Any] = {}
    if lead["status"] == "new":
        values["status"] = "contacted"
    if not lead.get("contacted_at"):
        values["contacted_at"] = kit.now()
    if values:
        _update(connection, "mk_submissions", submission_id, values)
    kit.log(
        connection,
        APP,
        "reply",
        f"Reply to {lead['name'] or lead['email']} written",
        ref_type="submission",
        ref_id=submission_id,
    )
    connection.commit()
    return {"lead": get_submission(connection, submission_id), "message": message}


def payment_request(
    connection: sqlite3.Connection, submission_id: str, raw: Mapping[str, Any]
) -> dict[str, Any]:
    """Ask a lead to pay: an email with the amount and your own payment link or instructions (Aira never takes
    the money). It waits in the outbox for your approval. Mark the lead paid when the money arrives."""
    lead = get_submission(connection, submission_id)
    if not lead["email"]:
        raise ValueError(
            "This lead left no email address to send a payment request to."
        )
    value = (
        money_field(raw.get("value_cents"), "the amount")
        if raw.get("value_cents") not in (None, "")
        else int(lead["value_cents"] or 0)
    )
    if value <= 0:
        raise ValueError("Enter the amount to ask for.")
    profile = kit.business(connection)
    if not (profile.get("payment_link") or profile.get("payment_instructions")):
        raise ValueError(
            "Add your payment link or payment instructions in Business hub > Business profile first."
        )
    what = kit.text(raw.get("for"), "what it is for", max_len=200) or (
        lead.get("package") or "our work together"
    )
    amount = kit.fmt_money(value, profile.get("currency") or "USD")
    first = str(lead["name"] or "").split(" ")[0]
    lines = [
        f"Hi {first or 'there'},",
        "",
        f"Thank you for choosing {profile.get('name') or 'us'}. Here is how to pay {amount} for {what}.",
        "",
    ]
    if profile.get("payment_link"):
        lines.append(f"Pay online: {profile['payment_link']}")
    if profile.get("payment_instructions"):
        lines.append(profile["payment_instructions"])
    lines += [
        "",
        f"Please mention this reference: {submission_id[:8].upper()}",
        "",
        profile.get("name") or "",
    ]
    message = kit.queue_email(
        connection,
        APP,
        "payment_request",
        to=lead["email"],
        subject=f"Payment for {what}"[:200],
        body="\n".join(lines).strip(),
        ref_type="submission",
        ref_id=submission_id,
    )
    values: dict[str, Any] = {"value_cents": value}
    if lead["status"] in ("new", "contacted"):
        values["status"] = "won"
    if not lead.get("contacted_at"):
        values["contacted_at"] = kit.now()
    _update(connection, "mk_submissions", submission_id, values)
    if lead["paid_at"]:
        _record_lead_revenue(connection, get_submission(connection, submission_id))
    kit.log(
        connection,
        APP,
        "payment_request",
        f"Payment request for {amount} to {lead['name'] or lead['email']}",
        ref_type="submission",
        ref_id=submission_id,
        amount_cents=value,
    )
    connection.commit()
    return {"lead": get_submission(connection, submission_id), "message": message}


SUBMISSION_COLUMNS = (
    "created_at",
    "source_label",
    "form_title",
    "name",
    "email",
    "phone",
    "company",
    "package",
    "message",
    "answer",
    "consent",
    "status",
    "value",
    "paid_at",
    "follow_up_on",
    "notes",
)


# ---------------------------------------------------------------------------
# subscribers
# ---------------------------------------------------------------------------


def add_subscriber(
    connection: sqlite3.Connection,
    raw: Mapping[str, Any],
    *,
    source: str = "manual",
    campaign_id: str = "",
    consent: bool = False,
    commit: bool = True,
    resubscribe: bool | None = None,
) -> dict[str, Any]:
    """Add an address to the list (or update its name and tags). An address that unsubscribed stays
    unsubscribed unless the person opts in again themselves (``consent`` from a form)."""
    address = kit.email(raw.get("email"), "an email address", required=True)
    name = kit.text(raw.get("name"), "the name", max_len=120)
    tags = kit.tags(raw.get("tags"))
    existing = kit.fetch_one(
        connection, "SELECT * FROM mk_subscribers WHERE email = ?", (address,)
    )
    stamp = kit.now()
    again = consent if resubscribe is None else resubscribe
    if existing:
        current = store.decode(existing)
        values: dict[str, Any] = {
            "name": name or current["name"],
            "tags": sorted(set(current["tags"]) | set(tags)),
        }
        if current["status"] == "unsubscribed" and again:
            values.update(
                {"status": "subscribed", "consent_at": stamp, "unsubscribed_at": None}
            )
        elif consent and not current["consent_at"]:
            values["consent_at"] = stamp
        _update(connection, "mk_subscribers", current["id"], values)
        row_id = current["id"]
    else:
        row_id = _insert(
            connection,
            "mk_subscribers",
            {
                "email": address,
                "name": name,
                "source": kit.text(source, "the source", max_len=60) or "manual",
                "tags": tags,
                "campaign_id": campaign_id,
                "status": "subscribed",
                "consent_at": stamp if consent else None,
            },
        )
        kit.log(
            connection,
            APP,
            "subscriber",
            f"{name or address} subscribed",
            ref_type="subscriber",
            ref_id=row_id,
            public=source.startswith("form:"),
        )
    if commit:
        connection.commit()
    return store.get(connection, "mk_subscribers", row_id, "subscriber")


def update_subscriber(
    connection: sqlite3.Connection, subscriber_id: str, raw: Mapping[str, Any]
) -> dict[str, Any]:
    current = store.get(connection, "mk_subscribers", subscriber_id, "subscriber")
    values: dict[str, Any] = {}
    if "name" in raw:
        values["name"] = kit.text(raw["name"], "the name", max_len=120)
    if "tags" in raw:
        values["tags"] = kit.tags(raw["tags"])
    if "status" in raw:
        status = kit.choice(raw["status"], "the status", SUBSCRIBER_STATUSES)
        if status == "subscribed" and current["status"] == "unsubscribed":
            raise ValueError(
                "This person unsubscribed. Only they can subscribe again, through one of your forms."
            )
        if status == "unsubscribed" and current["status"] != "unsubscribed":
            values.update({"status": "unsubscribed", "unsubscribed_at": kit.now()})
    if values:
        _update(connection, "mk_subscribers", subscriber_id, values)
        connection.commit()
    return store.get(connection, "mk_subscribers", subscriber_id, "subscriber")


def unsubscribe(connection: sqlite3.Connection, subscriber_id: str) -> dict[str, Any]:
    current = store.get(connection, "mk_subscribers", subscriber_id, "subscriber")
    if current["status"] != "unsubscribed":
        _update(
            connection,
            "mk_subscribers",
            subscriber_id,
            {"status": "unsubscribed", "unsubscribed_at": kit.now()},
        )
        kit.log(
            connection,
            APP,
            "unsubscribed",
            f"{current['email']} unsubscribed",
            ref_type="subscriber",
            ref_id=subscriber_id,
            public=True,
        )
        connection.commit()
    return store.get(connection, "mk_subscribers", subscriber_id, "subscriber")


def list_subscribers(
    connection: sqlite3.Connection,
    *,
    status: str = "",
    tag: str = "",
    query: str = "",
    limit: int = 1000,
) -> list[dict[str, Any]]:
    clauses, params = [], []
    if status:
        clauses.append("status = ?")
        params.append(kit.choice(status, "the status", SUBSCRIBER_STATUSES))
    if query:
        clauses.append("(email LIKE ? OR name LIKE ?)")
        params.extend([f"%{query[:80]}%"] * 2)
    wanted = tag.strip().lower()
    if wanted:
        # The tag list is stored as JSON: narrow in SQL (before the limit), then match exactly below.
        clauses.append("tags LIKE ?")
        params.append(f"%{kit.dumps(wanted)}%")
    where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    rows = store.all_rows(
        connection,
        f"SELECT * FROM mk_subscribers {where} ORDER BY created_at DESC LIMIT ?",  # noqa: S608
        (*params, max(1, min(limit, 20000))),
    )
    if wanted:
        rows = [row for row in rows if wanted in row["tags"]]
    return rows


def all_tags(connection: sqlite3.Connection) -> list[str]:
    """Every tag on the list (the filter offers all of them, whatever is filtered now)."""
    found: set[str] = set()
    for row in kit.fetch_all(
        connection, "SELECT tags FROM mk_subscribers WHERE tags <> '[]'"
    ):
        found.update(kit.loads(row["tags"], []))
    return sorted(found)


def import_subscribers(connection: sqlite3.Connection, text: str) -> dict[str, int]:
    """Paste CSV (``email,name,tags``; a header row is optional) or one address per line."""
    raw = str(text or "")
    if len(raw) > 2_000_000:
        raise ValueError("Paste at most 2 MB at a time.")
    result = {"added": 0, "updated": 0, "skipped": 0, "invalid": 0}
    reader = csv.reader(io.StringIO(raw))
    for index, row in enumerate(reader):
        cells = [cell.strip() for cell in row]
        if not cells or not any(cells):
            continue
        if index == 0 and cells[0].lower() in ("email", "e-mail", "email address"):
            continue
        address = cells[0].lower()
        try:
            kit.email(address, required=True)
        except ValueError:
            result["invalid"] += 1
            continue
        existing = kit.fetch_one(
            connection, "SELECT status FROM mk_subscribers WHERE email = ?", (address,)
        )
        if existing and existing["status"] == "unsubscribed":
            result["skipped"] += 1
            continue
        tags = cells[2].replace(";", ",") if len(cells) > 2 else ""
        add_subscriber(
            connection,
            {
                "email": address,
                "name": cells[1] if len(cells) > 1 else "",
                "tags": tags,
            },
            source="import",
            commit=False,
            resubscribe=False,
        )
        result["updated" if existing else "added"] += 1
    connection.commit()
    if result["added"]:
        kit.log(connection, APP, "import", f"{result['added']} subscribers imported")
        connection.commit()
    return result


SUBSCRIBER_COLUMNS = (
    "email",
    "name",
    "status",
    "source",
    "tags",
    "consent_at",
    "unsubscribed_at",
    "created_at",
)


# ---------------------------------------------------------------------------
# the link-in-bio page
# ---------------------------------------------------------------------------

BIO_DEFAULTS = {
    "title": "",
    "bio": "",
    "avatar_url": "",
    "featured_form_id": "",
    "featured_label": "Get in touch",
}


def bio_profile(connection: sqlite3.Connection) -> dict[str, Any]:
    stored = kit.get_setting(connection, APP, "bio", {}) or {}
    return {
        **BIO_DEFAULTS,
        **{key: value for key, value in stored.items() if key in BIO_DEFAULTS},
    }


def save_bio(connection: sqlite3.Connection, raw: Mapping[str, Any]) -> dict[str, Any]:
    merged = {
        **bio_profile(connection),
        **{key: value for key, value in raw.items() if key in BIO_DEFAULTS},
    }
    form_id = str(merged.get("featured_form_id") or "")
    if form_id:
        kit.require_row(connection, "mk_forms", form_id, "form")
    clean = {
        "title": kit.text(merged["title"], "the page title", max_len=80),
        "bio": kit.text(merged["bio"], "the bio", max_len=300, multiline=True),
        "avatar_url": _https(merged["avatar_url"], "the picture link"),
        "featured_form_id": form_id,
        "featured_label": kit.text(
            merged["featured_label"], "the button label", max_len=40
        )
        or "Get in touch",
    }
    kit.set_setting(connection, APP, "bio", clean)
    connection.commit()
    return clean


def bio_links(connection: sqlite3.Connection) -> list[dict[str, Any]]:
    return store.all_rows(
        connection, "SELECT * FROM mk_bio_links ORDER BY position, created_at"
    )


def add_bio_link(
    connection: sqlite3.Connection, raw: Mapping[str, Any]
) -> dict[str, Any]:
    if store.count(connection, "SELECT COUNT(*) FROM mk_bio_links") >= 50:
        raise ValueError("A bio page holds at most 50 links.")
    position = store.count(
        connection, "SELECT COALESCE(MAX(position), -1) + 1 FROM mk_bio_links"
    )
    row_id = _insert(
        connection,
        "mk_bio_links",
        {
            "label": kit.text(raw.get("label"), "the label", max_len=80, required=True),
            "url": kit.url(raw.get("url"), "the link", required=True),
            "highlight": kit.flag(raw.get("highlight")),
            "position": position,
        },
    )
    connection.commit()
    return store.get(connection, "mk_bio_links", row_id, "link")


def update_bio_link(
    connection: sqlite3.Connection, link_id: str, raw: Mapping[str, Any]
) -> dict[str, Any]:
    current = store.get(connection, "mk_bio_links", link_id, "link")
    values = {
        "label": kit.text(
            raw.get("label", current["label"]), "the label", max_len=80, required=True
        ),
        "url": kit.url(raw.get("url", current["url"]), "the link", required=True),
        "highlight": kit.flag(raw.get("highlight", current["highlight"])),
    }
    _update(connection, "mk_bio_links", link_id, values)
    connection.commit()
    return store.get(connection, "mk_bio_links", link_id, "link")


def move_bio_link(
    connection: sqlite3.Connection, link_id: str, direction: str
) -> list[dict[str, Any]]:
    links = bio_links(connection)
    ids = [link["id"] for link in links]
    if link_id not in ids:
        raise LookupError("That link was not found.")
    index = ids.index(link_id)
    target = (
        index - 1
        if kit.choice(direction, "the direction", ("up", "down")) == "up"
        else index + 1
    )
    if 0 <= target < len(ids):
        ids[index], ids[target] = ids[target], ids[index]
    for position, row_id in enumerate(ids):
        connection.execute(
            "UPDATE mk_bio_links SET position = ? WHERE id = ?", (position, row_id)
        )
    connection.commit()
    return bio_links(connection)


def public_bio(connection: sqlite3.Connection) -> dict[str, Any]:
    profile = bio_profile(connection)
    featured = None
    if profile["featured_form_id"]:
        link = public.link_for(connection, APP, "form", profile["featured_form_id"])
        if link:
            featured = {"label": profile["featured_label"], "path": link["path"]}
    return {
        "title": profile["title"] or kit.business(connection)["name"],
        "bio": profile["bio"],
        "avatar_url": profile["avatar_url"],
        "links": [
            {
                "id": link["id"],
                "label": link["label"],
                "url": link["url"],
                "highlight": link["highlight"],
            }
            for link in bio_links(connection)
        ],
        "featured": featured,
    }


# ---------------------------------------------------------------------------
# newsletters
# ---------------------------------------------------------------------------


def clean_newsletter(
    raw: Mapping[str, Any], current: Mapping[str, Any] | None = None
) -> dict[str, Any]:
    merged = _apply(raw, current)
    tags = kit.tags(merged.get("audience_tag"))
    return {
        "subject": kit.text(
            merged.get("subject"), "the subject", max_len=200, required=True
        ),
        "body": kit.text(
            merged.get("body"),
            "the newsletter",
            max_len=20000,
            required=True,
            multiline=True,
        ),
        "audience_tag": tags[0] if tags else "",
    }


def recipients(
    connection: sqlite3.Connection, newsletter: Mapping[str, Any]
) -> list[dict[str, Any]]:
    """Subscribed addresses in the audience that have not been queued this newsletter yet."""
    rows = store.all_rows(
        connection,
        """SELECT s.* FROM mk_subscribers s WHERE s.status = 'subscribed'
        AND NOT EXISTS (SELECT 1 FROM mk_newsletter_recipients r WHERE r.newsletter_id = ? AND r.subscriber_id = s.id)
        ORDER BY s.created_at""",
        (newsletter["id"],),
    )
    tag = newsletter["audience_tag"]
    return [row for row in rows if not tag or tag in row["tags"]]


def _newsletter_view(
    connection: sqlite3.Connection, newsletter: dict[str, Any]
) -> dict[str, Any]:
    newsletter["remaining"] = len(recipients(connection, newsletter))
    newsletter["sent"] = store.count(
        connection,
        "SELECT COUNT(*) FROM suite_outbox WHERE app = ? AND ref_type = 'newsletter' AND ref_id = ? AND status = 'sent'",
        (APP, newsletter["id"]),
    )
    return newsletter


def list_newsletters(connection: sqlite3.Connection) -> list[dict[str, Any]]:
    return [
        _newsletter_view(connection, row)
        for row in store.all_rows(
            connection, "SELECT * FROM mk_newsletters ORDER BY created_at DESC"
        )
    ]


def get_newsletter(
    connection: sqlite3.Connection, newsletter_id: str
) -> dict[str, Any]:
    return _newsletter_view(
        connection, store.get(connection, "mk_newsletters", newsletter_id, "newsletter")
    )


def create_newsletter(
    connection: sqlite3.Connection, raw: Mapping[str, Any]
) -> dict[str, Any]:
    row_id = _insert(
        connection, "mk_newsletters", {**clean_newsletter(raw), "status": "draft"}
    )
    connection.commit()
    return get_newsletter(connection, row_id)


def update_newsletter(
    connection: sqlite3.Connection, newsletter_id: str, raw: Mapping[str, Any]
) -> dict[str, Any]:
    current = store.get(connection, "mk_newsletters", newsletter_id, "newsletter")
    _update(connection, "mk_newsletters", newsletter_id, clean_newsletter(raw, current))
    connection.commit()
    return get_newsletter(connection, newsletter_id)


def queue_newsletter(
    connection: sqlite3.Connection,
    newsletter_id: str,
    *,
    confirm: Any,
    source: Any = None,
    cap: int = NEWSLETTER_CAP,
) -> dict[str, Any]:
    """Put one outbox message per subscribed recipient (up to ``cap``), each with its own unsubscribe link.
    Calling it again continues with the addresses not yet queued. ``source`` is the route's request (the
    address the links use; the hosted build's configured address wins)."""
    if not kit.flag(confirm):
        raise ValueError(
            "Confirm that you want to send this newsletter to your subscribers."
        )
    newsletter = store.get(connection, "mk_newsletters", newsletter_id, "newsletter")
    pending = recipients(connection, newsletter)
    if not pending:
        raise ValueError(
            "Everyone in this audience already has it, or nobody is subscribed yet."
        )
    batch = pending[: max(1, min(cap, NEWSLETTER_CAP))]
    profile = kit.business(connection)
    queued = 0
    for subscriber in batch:
        link = public.create_link(
            connection, APP, "unsubscribe", subscriber["id"], title="Unsubscribe"
        )
        greeting = (
            f"Hi {subscriber['name'].split(' ')[0]},\n\n" if subscriber["name"] else ""
        )
        body = (
            f"{greeting}{newsletter['body']}\n\n"
            f"You are receiving this because you subscribed to {profile.get('name') or 'our news'}. "
            f"Unsubscribe: {kit.public_url(link['path'], source)}"
        )
        message = kit.queue_email(
            connection,
            APP,
            "newsletter",
            to=subscriber["email"],
            subject=newsletter["subject"],
            body=body,
            ref_type="newsletter",
            ref_id=newsletter_id,
        )
        connection.execute(
            "INSERT OR IGNORE INTO mk_newsletter_recipients (newsletter_id, subscriber_id, message_id, queued_at) VALUES (?, ?, ?, ?)",
            (newsletter_id, subscriber["id"], (message or {}).get("id", ""), kit.now()),
        )
        queued += 1
    remaining = len(pending) - queued
    _update(
        connection,
        "mk_newsletters",
        newsletter_id,
        {
            "status": "queued" if not remaining else "partial",
            "queued_count": int(newsletter["queued_count"]) + queued,
            "queued_at": kit.now(),
        },
    )
    kit.log(
        connection,
        APP,
        "newsletter",
        f"Newsletter {newsletter['subject']} queued for {queued} subscribers",
        ref_type="newsletter",
        ref_id=newsletter_id,
    )
    connection.commit()
    return {
        "queued": queued,
        "remaining": remaining,
        "newsletter": get_newsletter(connection, newsletter_id),
    }


# ---------------------------------------------------------------------------
# the media kit: a creator's or business's public "work with me" page, with a rate card and sponsorship requests
# ---------------------------------------------------------------------------

MEDIA_KIT_DEFAULTS: dict[str, Any] = {
    "headline": "",
    "about": "",
    "niche": "",
    "audience": {},
    "show_subscribers": True,
    "open": True,
    "thank_you": "",
}
PACKAGE_CAP = 20


def media_kit(connection: sqlite3.Connection) -> dict[str, Any]:
    stored = kit.get_setting(connection, APP, "media_kit", {}) or {}
    out = {
        **MEDIA_KIT_DEFAULTS,
        **{key: value for key, value in stored.items() if key in MEDIA_KIT_DEFAULTS},
    }
    stored_audience = out.get("audience")
    audience: dict[str, Any] = (
        stored_audience if isinstance(stored_audience, dict) else {}
    )
    out["audience"] = {channel: int(audience.get(channel) or 0) for channel in CHANNELS}
    return out


def save_media_kit(
    connection: sqlite3.Connection, raw: Mapping[str, Any]
) -> dict[str, Any]:
    current = media_kit(connection)
    merged = {
        **current,
        **{key: value for key, value in raw.items() if key in MEDIA_KIT_DEFAULTS},
    }
    raw_audience = merged.get("audience")
    given: dict[str, Any] = raw_audience if isinstance(raw_audience, dict) else {}
    audience = {}
    for channel in CHANNELS:
        value = given.get(channel)
        audience[channel] = kit.integer(
            value if value not in (None, "") else 0,
            f"the {CHANNELS[channel]['label']} audience",
            default=0,
            minimum=0,
            maximum=10_000_000_000,
        )
    clean = {
        "headline": kit.text(merged.get("headline"), "the headline", max_len=120),
        "about": kit.text(
            merged.get("about"), "the introduction", max_len=1500, multiline=True
        ),
        "niche": kit.text(
            merged.get("niche"), "what you make content about", max_len=120
        ),
        "audience": audience,
        "show_subscribers": kit.flag(merged.get("show_subscribers")),
        "open": kit.flag(merged.get("open")),
        "thank_you": kit.text(
            merged.get("thank_you"),
            "the thank-you message",
            max_len=500,
            multiline=True,
        ),
    }
    kit.set_setting(connection, APP, "media_kit", clean)
    connection.commit()
    return media_kit(connection)


def clean_package(
    raw: Mapping[str, Any], current: Mapping[str, Any] | None = None
) -> dict[str, Any]:
    merged = _apply(raw, current)
    return {
        "name": kit.text(
            merged.get("name"), "the package name", max_len=80, required=True
        ),
        "price_cents": money_field(merged.get("price_cents"), "the price"),
        "details": kit.text(
            merged.get("details"), "what is included", max_len=1000, multiline=True
        ),
        "days": kit.integer(
            merged.get("days"), "the days to deliver", default=0, minimum=0, maximum=365
        ),
        "active": kit.flag(merged.get("active", True)),
    }


def list_packages(
    connection: sqlite3.Connection, *, active_only: bool = False
) -> list[dict[str, Any]]:
    where = "WHERE active = 1" if active_only else ""
    return store.all_rows(
        connection,
        f"SELECT * FROM mk_packages {where} ORDER BY position, created_at",  # noqa: S608 - fixed text
    )


def create_package(
    connection: sqlite3.Connection, raw: Mapping[str, Any]
) -> dict[str, Any]:
    if store.count(connection, "SELECT COUNT(*) FROM mk_packages") >= PACKAGE_CAP:
        raise ValueError(f"A rate card holds at most {PACKAGE_CAP} packages.")
    values = clean_package(raw)
    values["position"] = store.count(
        connection, "SELECT COALESCE(MAX(position), -1) + 1 FROM mk_packages"
    )
    row_id = _insert(connection, "mk_packages", values)
    connection.commit()
    return store.get(connection, "mk_packages", row_id, "package")


def update_package(
    connection: sqlite3.Connection, package_id: str, raw: Mapping[str, Any]
) -> dict[str, Any]:
    current = store.get(connection, "mk_packages", package_id, "package")
    _update(connection, "mk_packages", package_id, clean_package(raw, current))
    connection.commit()
    return store.get(connection, "mk_packages", package_id, "package")


def delete_package(connection: sqlite3.Connection, package_id: str) -> None:
    store.delete(connection, "mk_packages", package_id, "package")
    connection.commit()


def _reach(profile: Mapping[str, Any], subscribers: int) -> int:
    return sum(int(v or 0) for v in profile["audience"].values()) + (
        subscribers if profile["show_subscribers"] else 0
    )


def media_kit_view(connection: sqlite3.Connection) -> dict[str, Any]:
    """What the owner edits: the profile, the packages and the numbers the page will show."""
    profile = media_kit(connection)
    subscribers = store.count(
        connection, "SELECT COUNT(*) FROM mk_subscribers WHERE status = 'subscribed'"
    )
    sponsorships = kit.fetch_one(
        connection,
        """SELECT COUNT(*) AS n,
        COALESCE(SUM(CASE WHEN paid_at IS NOT NULL THEN value_cents ELSE 0 END), 0) AS earned,
        COALESCE(SUM(CASE WHEN status = 'new' THEN 1 ELSE 0 END), 0) AS waiting
        FROM mk_submissions WHERE source = 'media_kit'""",
    ) or {"n": 0, "earned": 0, "waiting": 0}
    return {
        "profile": profile,
        "packages": list_packages(connection),
        "subscribers": subscribers,
        "reach": _reach(profile, subscribers),
        "requests": int(sponsorships["n"] or 0),
        "waiting": int(sponsorships["waiting"] or 0),
        "earned_cents": int(sponsorships["earned"] or 0),
        "channels": [
            {"id": key, "label": value["label"]} for key, value in CHANNELS.items()
        ],
    }


def public_media_kit(connection: sqlite3.Connection) -> dict[str, Any]:
    """What a brand sees: who you are, your audience, your packages and prices. Never your leads or notes."""
    profile = media_kit(connection)
    bio = bio_profile(connection)
    subscribers = store.count(
        connection, "SELECT COUNT(*) FROM mk_subscribers WHERE status = 'subscribed'"
    )
    audience = [
        {"label": CHANNELS[channel]["label"], "count": count}
        for channel, count in profile["audience"].items()
        if count > 0
    ]
    if profile["show_subscribers"] and subscribers:
        audience.append({"label": "Email list", "count": subscribers})
    return {
        "title": bio["title"] or kit.business(connection)["name"],
        "avatar_url": bio["avatar_url"],
        "headline": profile["headline"],
        "about": profile["about"],
        "niche": profile["niche"],
        "audience": audience,
        "reach": sum(item["count"] for item in audience),
        "packages": [
            {
                "id": package["id"],
                "name": package["name"],
                "price_cents": package["price_cents"],
                "details": package["details"],
                "days": package["days"],
            }
            for package in list_packages(connection, active_only=True)
        ],
        "open": bool(profile["open"]),
    }


def sponsorship_request(
    connection: sqlite3.Connection, payload: Mapping[str, Any]
) -> dict[str, Any]:
    """A brand asks to work with you from the media kit page. It lands in the leads inbox as a sponsorship
    with its budget as the value, and you get an email in the outbox. Prices are checked on the server."""
    profile = media_kit(connection)
    if not profile["open"]:
        raise ValueError("Sponsorship requests are closed for now.")
    thank_you = (
        profile["thank_you"]
        or "Thank you! We will reply by email within two working days."
    )
    if str(payload.get("website") or "").strip():
        return {
            "thank_you": thank_you,
            "stored": False,
        }  # a bot filled the hidden field
    brand = kit.text(
        payload.get("company"), "your brand or company", max_len=120, required=True
    )
    name = kit.text(payload.get("name"), "your name", max_len=120, required=True)
    address = kit.email(payload.get("email"), "your email", required=True)
    package_id = str(payload.get("package_id") or "").strip()
    package: dict[str, Any] | None = None
    if package_id:
        row = kit.fetch_one(
            connection,
            "SELECT * FROM mk_packages WHERE id = ? AND active = 1",
            (package_id,),
        )
        if not row:
            raise ValueError("That package is no longer offered. Choose another one.")
        package = store.decode(row)
    budget = amount(payload.get("budget"), "your budget")
    message = kit.text(
        payload.get("message"), "your message", max_len=2000, multiline=True
    )
    dates = kit.text(payload.get("dates"), "the dates", max_len=120)
    if not (package or budget or message):
        raise ValueError(
            "Choose a package, enter a budget or tell us what you have in mind."
        )
    value = budget or (int(package["price_cents"]) if package else 0)
    submission_id = _insert(
        connection,
        "mk_submissions",
        {
            "form_id": "",
            "source": "media_kit",
            "name": name,
            "email": address,
            "company": brand,
            "package": package["name"] if package else "",
            "message": message,
            "answer": dates,
            "value_cents": value,
            "status": "new",
        },
    )
    kit.log(
        connection,
        APP,
        "sponsorship",
        f"{brand} asked about a sponsorship",
        ref_type="submission",
        ref_id=submission_id,
        amount_cents=value,
        public=True,
    )
    owner = kit.business(connection)
    if owner.get("email"):
        currency = owner.get("currency") or "USD"
        lines = [
            f"Brand: {brand}",
            f"Name: {name}",
            f"Email: {address}",
            f"Package: {package['name'] if package else 'not chosen'}",
            f"Budget: {kit.fmt_money(budget, currency) if budget else 'not given'}",
        ]
        if dates:
            lines.append(f"Dates: {dates}")
        if message:
            lines += ["", message]
        kit.queue_email(
            connection,
            APP,
            "sponsorship_request",
            to=owner["email"],
            subject=f"Sponsorship request from {brand}"[:200],
            body="A brand wants to work with you.\n\n" + "\n".join(lines),
            ref_type="submission",
            ref_id=submission_id,
        )
    connection.commit()
    return {"thank_you": thank_you, "stored": True}


# ---------------------------------------------------------------------------
# the overview
# ---------------------------------------------------------------------------


def _month_start(moment: datetime | None = None) -> str:
    now = moment or kit.utcnow()
    return now.replace(day=1, hour=0, minute=0, second=0).strftime("%Y-%m-%dT%H:%M:%SZ")


def _last_month_start(moment: datetime | None = None) -> str:
    now = moment or kit.utcnow()
    first = now.replace(day=1, hour=0, minute=0, second=0)
    return (first - timedelta(days=1)).replace(day=1).strftime("%Y-%m-%dT%H:%M:%SZ")


def overview(connection: sqlite3.Connection) -> dict[str, Any]:
    month, last_month = _month_start(), _last_month_start()
    now = kit.utcnow()
    week_end = (now + timedelta(days=7)).strftime("%Y-%m-%dT%H:%M:%SZ")
    subscribed = store.count(
        connection, "SELECT COUNT(*) FROM mk_subscribers WHERE status = 'subscribed'"
    )
    new_month = store.count(
        connection,
        "SELECT COUNT(*) FROM mk_subscribers WHERE created_at >= ?",
        (month,),
    )
    new_last = store.count(
        connection,
        "SELECT COUNT(*) FROM mk_subscribers WHERE created_at >= ? AND created_at < ?",
        (last_month, month),
    )
    views = store.count(connection, "SELECT COALESCE(SUM(views), 0) FROM mk_forms")
    submissions = store.count(
        connection, "SELECT COALESCE(SUM(submissions), 0) FROM mk_forms"
    )
    campaigns = list_campaigns(connection)
    best = None
    scored = [
        c
        for c in campaigns
        if c["results"]["leads"]
        or c["results"]["clicks"]
        or c["results"]["roi_percent"] is not None
    ]
    if scored:
        top = max(
            scored,
            key=lambda c: (
                c["results"]["roi_percent"]
                if c["results"]["roi_percent"] is not None
                else -(10**9),
                c["results"]["leads"],
                c["results"]["clicks"],
            ),
        )
        best = {"id": top["id"], "name": top["name"], **top["results"]}
    return {
        "subscribers": subscribed,
        "new_subscribers_month": new_month,
        "new_subscribers_last_month": new_last,
        "growth_percent": round((new_month - new_last) * 100 / new_last)
        if new_last
        else None,
        "clicks_month": store.count(
            connection, "SELECT COUNT(*) FROM mk_clicks WHERE at >= ?", (month,)
        ),
        "form_views": views,
        "form_submissions": submissions,
        "conversion_percent": conversion(submissions, views),
        "leads_month": store.count(
            connection,
            "SELECT COUNT(*) FROM mk_submissions WHERE created_at >= ?",
            (month,),
        ),
        "new_leads": store.count(
            connection, "SELECT COUNT(*) FROM mk_submissions WHERE status = 'new'"
        ),
        "scheduled_week": store.count(
            connection,
            "SELECT COUNT(*) FROM mk_posts WHERE status = 'scheduled' AND scheduled_at >= ? AND scheduled_at < ?",
            (kit.now(), week_end),
        ),
        "published_month": store.count(
            connection,
            "SELECT COUNT(*) FROM mk_posts WHERE status = 'published' AND published_at >= ?",
            (month,),
        ),
        "best_campaign": best,
        "upcoming": store.all_rows(
            connection,
            "SELECT * FROM mk_posts WHERE status IN ('scheduled', 'draft', 'idea') AND scheduled_at >= ? ORDER BY scheduled_at LIMIT 6",
            (kit.now(),),
        ),
        "recent_leads": list_submissions(connection, limit=6),
        "earned_month_cents": store.count(
            connection,
            "SELECT COALESCE(SUM(value_cents), 0) FROM mk_submissions WHERE paid_at >= ?",
            (month,),
        ),
        "customers_month": store.count(
            connection,
            "SELECT COUNT(*) FROM mk_submissions WHERE paid_at >= ?",
            (month,),
        ),
        "open_value_cents": store.count(
            connection,
            "SELECT COALESCE(SUM(value_cents), 0) FROM mk_submissions WHERE paid_at IS NULL AND status IN ('new', 'contacted', 'won')",
        ),
        "follow_ups": list_submissions(connection, due=True, limit=8),
        "follow_ups_due": store.count(
            connection,
            "SELECT COUNT(*) FROM mk_submissions WHERE follow_up_on <> '' AND follow_up_on <= ? AND status IN ('new', 'contacted')",
            (kit.today(),),
        ),
        "due_posts": due_posts(connection),
        "channels": [{"id": key, **value} for key, value in CHANNELS.items()],
    }


# ---------------------------------------------------------------------------
# reminders: Aira does not post to social networks, so it tells you when a post is due
# ---------------------------------------------------------------------------

REMIND_WINDOW_DAYS = (
    2  # a post more than this late gets no email (only the "Ready to post" list)
)


def due_posts(
    connection: sqlite3.Connection, *, days: int = 14
) -> list[dict[str, Any]]:
    """Scheduled posts whose time has come (in the last ``days``) and that are not marked published yet."""
    since = (kit.utcnow() - timedelta(days=days)).strftime("%Y-%m-%dT%H:%M:%SZ")
    return store.all_rows(
        connection,
        "SELECT * FROM mk_posts WHERE status = 'scheduled' AND scheduled_at <> '' AND scheduled_at <= ? AND scheduled_at >= ? ORDER BY scheduled_at LIMIT 20",
        (kit.now(), since),
    )


def remind_due_posts(connection: sqlite3.Connection) -> int:
    """Queue one "time to post" email per due post (to the business's own address), once. Safe to run again:
    a post is reminded only while ``reminded_at`` is empty. Returns how many reminders were written."""
    since = (kit.utcnow() - timedelta(days=REMIND_WINDOW_DAYS)).strftime(
        "%Y-%m-%dT%H:%M:%SZ"
    )
    rows = store.all_rows(
        connection,
        "SELECT * FROM mk_posts WHERE status = 'scheduled' AND scheduled_at <> '' AND scheduled_at <= ? AND scheduled_at >= ? AND reminded_at IS NULL ORDER BY scheduled_at LIMIT 50",
        (kit.now(), since),
    )
    address = kit.business(connection).get("email") or ""
    written = 0
    for post in rows:
        label = CHANNELS[post["channel"]]["label"]
        if address:
            text = "\n\n".join(
                part for part in (post["body"], post["hashtags"]) if part
            )
            kit.queue_email(
                connection,
                APP,
                "post_reminder",
                to=address,
                subject=f"Time to post on {label}: {post['title'] or post['body'][:60] or 'your post'}"[
                    :200
                ],
                body=(
                    f"Your {label} post is due now. Copy the text below, post it, then mark it published in "
                    f"Marketing > Calendar so its link is kept.\n\n{text or '(no text yet)'}"
                    + (
                        f"\n\nImage or video: {post['media_url']}"
                        if post["media_url"]
                        else ""
                    )
                ),
                ref_type="post",
                ref_id=post["id"],
            )
            written += 1
            # Only a reminder that was really written uses up the post's one reminder: with no business email
            # yet, the post is reminded once one is added (while it is still inside the window).
            connection.execute(
                "UPDATE mk_posts SET reminded_at = ? WHERE id = ?",
                (kit.now(), post["id"]),
            )
    connection.commit()
    return written


def checklist(connection: sqlite3.Connection) -> list[dict[str, Any]]:
    profile = kit.business(connection)
    shared_form = store.count(
        connection,
        "SELECT COUNT(*) FROM suite_public_links WHERE app = ? AND kind = 'form' AND revoked_at IS NULL",
        (APP,),
    )
    return [
        kit.step(
            "profile",
            "Add your business details",
            bool(profile["name"]),
            "Business hub > Business profile",
        ),
        kit.step(
            "post",
            "Plan your first post",
            store.count(connection, "SELECT COUNT(*) FROM mk_posts") > 0,
            "Try the 30-day plan",
        ),
        kit.step(
            "link",
            "Create a tracked link",
            store.count(connection, "SELECT COUNT(*) FROM mk_links") > 0,
            "See which posts bring visitors",
        ),
        kit.step(
            "form", "Share a lead form", shared_form > 0, "Put it in your bio and posts"
        ),
        kit.step(
            "subscriber",
            "Get your first subscriber",
            store.count(connection, "SELECT COUNT(*) FROM mk_subscribers") > 0,
            "Forms with the email opt-in add them",
        ),
        kit.step(
            "newsletter",
            "Send your first newsletter",
            store.count(
                connection, "SELECT COUNT(*) FROM mk_newsletters WHERE queued_count > 0"
            )
            > 0,
            "Keep your list warm",
        ),
    ]


# ---------------------------------------------------------------------------
# search (the search box across the top of the app)
# ---------------------------------------------------------------------------

# What each kind of record is matched on, newest first. Leads and subscribers (people) come first in the answer.
_SEARCH: tuple[tuple[str, str, tuple[str, ...]], ...] = (
    (
        "submissions",
        "mk_submissions",
        ("name", "email", "phone", "company", "message", "notes"),
    ),
    ("subscribers", "mk_subscribers", ("email", "name")),
    ("posts", "mk_posts", ("title", "body", "hashtags")),
    ("campaigns", "mk_campaigns", ("name", "goal")),
    ("forms", "mk_forms", ("title", "intro")),
    ("links", "mk_links", ("title", "slug", "destination")),
    ("newsletters", "mk_newsletters", ("subject", "body")),
)


def search(
    connection: sqlite3.Connection, query: str, *, limit: int = 5
) -> dict[str, list[dict[str, Any]]]:
    """Up to ``limit`` records of each kind whose words contain ``query``, each shaped as its own list returns it
    (so the page opens a found record with the same drawer as from its tab). An empty query finds nothing."""
    text = str(query or "").strip()[:80]
    limit = max(1, min(int(limit), 25))
    found: dict[str, list[dict[str, Any]]] = {kind: [] for kind, _t, _c in _SEARCH}
    if not text:
        return found
    pattern = f"%{text}%"
    for kind, table, columns in _SEARCH:
        where = " OR ".join(f"{column} LIKE ?" for column in columns)
        if kind == "submissions":
            found[kind] = list_submissions(connection, query=text, limit=limit)
            continue
        rows = store.all_rows(
            connection,
            f"SELECT * FROM {table} WHERE {where} ORDER BY created_at DESC LIMIT ?",  # noqa: S608 - constant names
            (*([pattern] * len(columns)), limit),
        )
        if kind == "campaigns":
            for row in rows:
                row["results"] = campaign_results(connection, row)
        elif kind == "links":
            rows = [_link_view(connection, row) for row in rows]
        elif kind == "forms":
            rows = [_form_view(connection, row) for row in rows]
        elif kind == "newsletters":
            rows = [_newsletter_view(connection, row) for row in rows]
        found[kind] = rows
    return found
