"""News rules: each person's settings, which feeds they follow, refreshing, listing, saving and the briefing.

A feed is refreshed when it is older than 30 minutes (on read, and by the background task for every feed someone
follows). A failed refresh keeps the earlier articles. Children are in child-safe mode by default (and cannot turn
it off): only the Science, Technology, Sports and Entertainment sections, and no headline with a blocklisted word.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Callable, Iterable
from datetime import datetime, timedelta, timezone
from typing import Any

from aira.suite import kit, life
from aira.suite.news import feeds

STALE = timedelta(minutes=30)
KEEP_DAYS = 14
MAX_KEYWORDS = 10
MAX_SOURCES = 15
MAX_TOPICS = len(feeds.TOPICS)
REFRESH_ERROR = "Could not refresh; showing earlier news."
NO_NEWS_ERROR = "Could not load the news right now. Try again in a few minutes."
Reader = Callable[[str], feeds.Feed]


def _reader(reader: Reader | None) -> Reader:
    return reader or feeds.read


# ---------------------------------------------------------------------------
# settings
# ---------------------------------------------------------------------------


def _default_language(me: dict[str, Any]) -> str:
    wanted = str((me.get("prefs") or {}).get("language") or "").lower()
    return wanted if wanted in feeds.LANGUAGES else feeds.DEFAULT_LANGUAGE


def _is_child(me: dict[str, Any]) -> bool:
    return me.get("age_group") == "child"


def settings(connection: sqlite3.Connection, me: dict[str, Any]) -> dict[str, Any]:
    """The person's news settings (made on first use from their language and age)."""
    row = kit.fetch_one(
        connection, "SELECT * FROM nw_settings WHERE profile_id = ?", (me["id"],)
    )
    if row is None:
        topics = feeds.DEFAULT_CHILD_TOPICS if _is_child(me) else feeds.DEFAULT_TOPICS
        row = {
            "profile_id": me["id"],
            "lang": _default_language(me),
            "region": feeds.DEFAULT_REGION,
            "topics": kit.dumps(list(topics)),
            "child_safe": None,
            "last_opened": None,
            "updated_at": kit.now(),
        }
        connection.execute(
            """INSERT OR IGNORE INTO nw_settings (profile_id, lang, region, topics, child_safe, last_opened, updated_at)
            VALUES (:profile_id, :lang, :region, :topics, :child_safe, :last_opened, :updated_at)""",
            row,
        )
        connection.commit()
    topics = [t for t in kit.loads(row["topics"], []) if t in feeds.TOPICS] or list(
        feeds.DEFAULT_TOPICS
    )
    child_safe = (
        True
        if _is_child(me)
        else bool(row["child_safe"])
        if row["child_safe"] is not None
        else False
    )
    if child_safe:
        topics = [t for t in topics if t in feeds.CHILD_TOPICS] or list(
            feeds.DEFAULT_CHILD_TOPICS
        )
    lang = row["lang"] if row["lang"] in feeds.LANGUAGES else feeds.DEFAULT_LANGUAGE
    regions = feeds.LANGUAGES[lang]["regions"]
    region = row["region"] if row["region"] in regions else regions[0]
    return {
        "lang": lang,
        "region": region,
        "topics": topics,
        "child_safe": child_safe,
        "last_opened": row["last_opened"],
    }


def keywords(
    connection: sqlite3.Connection, me: dict[str, Any]
) -> list[dict[str, Any]]:
    rows = kit.fetch_all(
        connection,
        "SELECT * FROM nw_keywords WHERE profile_id = ? ORDER BY created_at",
        (me["id"],),
    )
    return [
        {"id": r["id"], "query": r["query"], "alert": bool(r["alert"])} for r in rows
    ]


def sources(connection: sqlite3.Connection, me: dict[str, Any]) -> list[dict[str, Any]]:
    rows = kit.fetch_all(
        connection,
        """SELECT s.id, s.title, s.url, f.last_ok, f.last_error FROM nw_sources s JOIN nw_feeds f ON f.id = s.feed_id
        WHERE s.profile_id = ? ORDER BY s.created_at""",
        (me["id"],),
    )
    return [
        {
            "id": r["id"],
            "title": r["title"],
            "url": r["url"],
            "last_ok": r["last_ok"],
            "error": r["last_error"],
        }
        for r in rows
    ]


def settings_view(connection: sqlite3.Connection, me: dict[str, Any]) -> dict[str, Any]:
    mine = settings(connection, me)
    return {
        **{k: v for k, v in mine.items() if k != "last_opened"},
        "can_turn_off_child_safe": not _is_child(me),
        "keywords": keywords(connection, me),
        "sources": sources(connection, me),
        "languages": [
            {
                "code": code,
                "name": info["name"],
                "native": info["native"],
                "regions": [
                    {"code": r, "name": feeds.REGIONS.get(r, r)}
                    for r in info["regions"]
                ],
            }
            for code, info in feeds.LANGUAGES.items()
        ],
        "topics_available": [
            {"code": code, "name": name, "child_ok": code in feeds.CHILD_TOPICS}
            for code, name in feeds.TOPICS.items()
            if not mine["child_safe"] or code in feeds.CHILD_TOPICS
        ],
    }


def save_settings(
    connection: sqlite3.Connection, me: dict[str, Any], raw: dict[str, Any]
) -> dict[str, Any]:
    current = settings(connection, me)
    lang = str(raw.get("lang") or current["lang"]).strip().lower()
    if lang not in feeds.LANGUAGES:
        raise ValueError("Choose a language from the list.")
    regions = feeds.LANGUAGES[lang]["regions"]
    region = str(raw.get("region") or current["region"]).strip().upper()
    if region not in regions:
        region = regions[0]
    if "child_safe" in raw and not _is_child(me):
        child_safe: bool | None = kit.flag(raw.get("child_safe"))
    else:
        child_safe = True if _is_child(me) else current["child_safe"]
    topics = current["topics"]
    if "topics" in raw:
        wanted = raw.get("topics")
        if not isinstance(wanted, list):
            raise ValueError("Choose the topics to follow.")
        topics = []
        for code in wanted:
            code = str(code).strip()
            code = code if code == "top" else code.upper()
            if code not in feeds.TOPICS:
                raise ValueError("Choose topics from the list.")
            if child_safe and code not in feeds.CHILD_TOPICS:
                raise ValueError(
                    "In child-safe mode the topics are Science, Technology, Sports and Entertainment."
                )
            if code not in topics:
                topics.append(code)
        if not topics:
            raise ValueError("Follow at least one topic.")
    connection.execute(
        "UPDATE nw_settings SET lang = ?, region = ?, topics = ?, child_safe = ?, updated_at = ? WHERE profile_id = ?",
        (
            lang,
            region,
            kit.dumps(topics[:MAX_TOPICS]),
            None if child_safe is None else int(child_safe),
            kit.now(),
            me["id"],
        ),
    )
    connection.commit()
    return settings_view(connection, me)


def mark_opened(connection: sqlite3.Connection, me: dict[str, Any]) -> None:
    settings(connection, me)
    connection.execute(
        "UPDATE nw_settings SET last_opened = ? WHERE profile_id = ?",
        (kit.now(), me["id"]),
    )
    connection.commit()


# ---------------------------------------------------------------------------
# feeds
# ---------------------------------------------------------------------------


def _feed(
    connection: sqlite3.Connection,
    kind: str,
    key: str,
    url: str,
    *,
    lang: str = "",
    region: str = "",
    title: str = "",
) -> dict[str, Any]:
    row = kit.fetch_one(connection, "SELECT * FROM nw_feeds WHERE key = ?", (key,))
    if row:
        return row
    connection.execute(
        """INSERT OR IGNORE INTO nw_feeds (id, key, kind, url, title, lang, region, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
        (kit.new_id(), key, kind, url, title, lang, region, kit.now()),
    )
    connection.commit()
    return (
        kit.fetch_one(connection, "SELECT * FROM nw_feeds WHERE key = ?", (key,)) or {}
    )


def topic_feed(
    connection: sqlite3.Connection, topic: str, lang: str, region: str
) -> dict[str, Any]:
    return _feed(
        connection,
        "topic",
        f"topic:{topic}:{lang}:{region}",
        feeds.topic_url(topic, lang, region),
        lang=lang,
        region=region,
        title=feeds.TOPICS.get(topic, topic),
    )


def keyword_feed(
    connection: sqlite3.Connection, query: str, lang: str, region: str
) -> dict[str, Any]:
    return _feed(
        connection,
        "search",
        f"search:{lang}:{region}:{query.lower()}",
        feeds.search_url(query, lang, region),
        lang=lang,
        region=region,
        title=query,
    )


def _stale(feed: dict[str, Any], now: datetime) -> bool:
    if not feed.get("last_fetched"):
        return True
    try:
        return now - kit.parse_time(feed["last_fetched"]) >= STALE
    except ValueError:
        return True


def refresh(
    connection: sqlite3.Connection,
    feed: dict[str, Any],
    *,
    force: bool = False,
    reader: Reader | None = None,
) -> dict[str, Any]:
    """Fetch one feed if it is stale (or ``force``); a failure keeps the earlier articles."""
    now = kit.utcnow()
    if not force and not _stale(feed, now):
        return {"ok": True, "added": 0, "skipped": True}
    stamp = kit.now()
    try:
        parsed = _reader(reader)(feed["url"])
    except (feeds.FeedError, ValueError) as error:
        connection.execute(
            "UPDATE nw_feeds SET last_fetched = ?, last_error = ? WHERE id = ?",
            (stamp, str(error)[:300], feed["id"]),
        )
        connection.commit()
        return {"ok": False, "added": 0, "error": str(error)}
    added = 0
    for article in parsed.items:
        added += connection.execute(
            """INSERT OR IGNORE INTO nw_articles (id, feed_id, link, title, summary, source, image, published_at, fetched_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                kit.new_id(),
                feed["id"],
                article.link,
                article.title,
                article.summary,
                article.source,
                article.image,
                article.published_at,
                stamp,
            ),
        ).rowcount
    title = feed.get("title") or parsed.title
    connection.execute(
        "UPDATE nw_feeds SET last_fetched = ?, last_ok = ?, last_error = '', title = ? WHERE id = ?",
        (stamp, stamp, title or "", feed["id"]),
    )
    connection.commit()
    return {"ok": True, "added": added}


def _topic_feeds(
    connection: sqlite3.Connection, topics: Iterable[str], lang: str, region: str
) -> list[dict[str, Any]]:
    return [topic_feed(connection, topic, lang, region) for topic in topics]


def feeds_for(
    connection: sqlite3.Connection, me: dict[str, Any], topic: str
) -> list[dict[str, Any]]:
    """The feeds behind a tab: "top", a topic code, "foryou" (keywords), "source:<id>" or "all" (everything followed)."""
    mine = settings(connection, me)
    lang, region = mine["lang"], mine["region"]
    child = mine["child_safe"]
    if topic.startswith("source:"):
        row = kit.fetch_one(
            connection,
            "SELECT f.* FROM nw_sources s JOIN nw_feeds f ON f.id = s.feed_id WHERE s.id = ? AND s.profile_id = ?",
            (topic.split(":", 1)[1], me["id"]),
        )
        if not row:
            raise LookupError("That source was not found.")
        return [row]
    if topic == "foryou":
        return [
            keyword_feed(connection, k["query"], lang, region)
            for k in keywords(connection, me)
        ]
    if topic == "all":
        out = _topic_feeds(connection, mine["topics"], lang, region)
        out += [
            keyword_feed(connection, k["query"], lang, region)
            for k in keywords(connection, me)
        ]
        out += kit.fetch_all(
            connection,
            "SELECT f.* FROM nw_sources s JOIN nw_feeds f ON f.id = s.feed_id WHERE s.profile_id = ?",
            (me["id"],),
        )
        return out
    code = topic if topic == "top" else topic.upper()
    if code not in feeds.TOPICS:
        raise ValueError("Choose a topic from the list.")
    if child and code not in feeds.CHILD_TOPICS:
        return _topic_feeds(
            connection, mine["topics"], lang, region
        )  # a child's front page is their safe topics
    return [topic_feed(connection, code, lang, region)]


def _saved_links(connection: sqlite3.Connection, me: dict[str, Any]) -> set[str]:
    rows = kit.fetch_all(
        connection,
        "SELECT a.link FROM nw_saved s JOIN nw_articles a ON a.id = s.article_id WHERE s.profile_id = ?",
        (me["id"],),
    )
    return {r["link"] for r in rows}


def _read_links(connection: sqlite3.Connection, me: dict[str, Any]) -> set[str]:
    rows = kit.fetch_all(
        connection,
        "SELECT a.link FROM nw_read r JOIN nw_articles a ON a.id = r.article_id WHERE r.profile_id = ?",
        (me["id"],),
    )
    return {r["link"] for r in rows}


def _view(row: dict[str, Any], saved: set[str], read: set[str]) -> dict[str, Any]:
    return {
        "id": row["id"],
        "title": row["title"],
        "link": row["link"],
        "summary": row["summary"],
        "source": row["source"],
        "image": row["image"],
        "published_at": row["published_at"] or row["fetched_at"],
        "ago": ago(row["published_at"] or row["fetched_at"]),
        "fetched_at": row["fetched_at"],
        "saved": row["link"] in saved,
        "read": row["link"] in read,
    }


def _articles(
    connection: sqlite3.Connection,
    me: dict[str, Any],
    feed_rows: list[dict[str, Any]],
    *,
    since: str = "",
    limit: int = 40,
    child_safe: bool = False,
) -> list[dict[str, Any]]:
    ids = [f["id"] for f in feed_rows if f.get("id")]
    if not ids:
        return []
    marks = ",".join("?" for _ in ids)
    where = f"feed_id IN ({marks})"
    params: list[Any] = list(ids)
    if since:
        where += " AND fetched_at > ?"
        params.append(since)
    rows = kit.fetch_all(
        connection,
        f"""SELECT * FROM nw_articles WHERE {where}
        ORDER BY COALESCE(NULLIF(published_at, ''), fetched_at) DESC, rowid DESC LIMIT 600""",  # noqa: S608 - placeholders only
        params,
    )
    saved, read = _saved_links(connection, me), _read_links(connection, me)
    seen: set[str] = set()
    out = []
    for row in rows:
        if row["link"] in seen:
            continue
        if child_safe and not feeds.child_safe_title(row["title"]):
            continue
        seen.add(row["link"])
        out.append(_view(row, saved, read))
        if len(out) >= limit:
            break
    return out


def articles(
    connection: sqlite3.Connection,
    me: dict[str, Any],
    *,
    topic: str = "top",
    since: str = "",
    limit: int = 40,
    reader: Reader | None = None,
    refresh_stale: bool = True,
) -> dict[str, Any]:
    """A tab's articles, newest first (``since``: only those that arrived after it), refreshing stale feeds."""
    mine = settings(connection, me)
    feed_rows = feeds_for(connection, me, topic or "top")
    failed = False
    if refresh_stale:
        for feed in feed_rows:
            if not refresh(connection, feed, reader=reader)["ok"]:
                failed = True
        feed_rows = [
            kit.fetch_one(connection, "SELECT * FROM nw_feeds WHERE id = ?", (f["id"],))
            or f
            for f in feed_rows
        ]
    items = _articles(
        connection,
        me,
        feed_rows,
        since=since,
        limit=limit,
        child_safe=mine["child_safe"],
    )
    error = ""
    if failed:
        error = REFRESH_ERROR if (items or since) else NO_NEWS_ERROR
    return {
        "topic": topic or "top",
        "articles": items,
        "now": kit.now(),
        "error": error,
        "child_safe": mine["child_safe"],
        "lang": mine["lang"],
    }


# ---------------------------------------------------------------------------
# keywords and sources
# ---------------------------------------------------------------------------


def add_keyword(
    connection: sqlite3.Connection, me: dict[str, Any], raw: dict[str, Any]
) -> dict[str, Any]:
    query = kit.text(
        raw.get("query"), "a word or topic to follow", max_len=60, required=True
    )
    if len(keywords(connection, me)) >= MAX_KEYWORDS:
        raise ValueError(
            f"You can follow up to {MAX_KEYWORDS} keywords. Remove one first."
        )
    existing = kit.fetch_one(
        connection,
        "SELECT id FROM nw_keywords WHERE profile_id = ? AND lower(query) = lower(?)",
        (me["id"], query),
    )
    if existing:
        raise ValueError(f"You already follow {query}.")
    keyword_id = kit.new_id()
    connection.execute(
        "INSERT INTO nw_keywords (id, profile_id, query, alert, created_at) VALUES (?, ?, ?, ?, ?)",
        (
            keyword_id,
            me["id"],
            query,
            1 if kit.flag(raw.get("alert")) else 0,
            kit.now(),
        ),
    )
    connection.commit()
    return next(k for k in keywords(connection, me) if k["id"] == keyword_id)


def update_keyword(
    connection: sqlite3.Connection,
    me: dict[str, Any],
    keyword_id: str,
    raw: dict[str, Any],
) -> dict[str, Any]:
    _own_keyword(connection, me, keyword_id)
    if "alert" in raw:
        connection.execute(
            "UPDATE nw_keywords SET alert = ? WHERE id = ?",
            (1 if kit.flag(raw["alert"]) else 0, keyword_id),
        )
        connection.commit()
    return next(k for k in keywords(connection, me) if k["id"] == keyword_id)


def _own_keyword(
    connection: sqlite3.Connection, me: dict[str, Any], keyword_id: str
) -> dict[str, Any]:
    row = kit.fetch_one(
        connection,
        "SELECT * FROM nw_keywords WHERE id = ? AND profile_id = ?",
        (keyword_id, me["id"]),
    )
    if not row:
        raise LookupError("That keyword was not found.")
    return row


def remove_keyword(
    connection: sqlite3.Connection, me: dict[str, Any], keyword_id: str
) -> None:
    _own_keyword(connection, me, keyword_id)
    connection.execute("DELETE FROM nw_keywords WHERE id = ?", (keyword_id,))
    connection.commit()


def add_source(
    connection: sqlite3.Connection,
    me: dict[str, Any],
    raw: dict[str, Any],
    *,
    reader: Reader | None = None,
) -> dict[str, Any]:
    url = kit.url(raw.get("url"), "the feed address", required=True)
    if not url.lower().startswith("https://") and not feeds._allow_local():
        raise ValueError("A news feed address starts with https://.")
    if len(sources(connection, me)) >= MAX_SOURCES:
        raise ValueError(f"You can add up to {MAX_SOURCES} sources. Remove one first.")
    if kit.fetch_one(
        connection,
        "SELECT s.id FROM nw_sources s JOIN nw_feeds f ON f.id = s.feed_id WHERE s.profile_id = ? AND f.url = ?",
        (me["id"], url),
    ):
        raise ValueError("You already follow that source.")
    try:
        parsed = _reader(reader)(url)
    except feeds.FeedError as error:
        raise ValueError(f"Could not read that feed: {error}") from None
    title = kit.text(raw.get("title"), "a name", max_len=80) or parsed.title or url
    feed = _feed(connection, "custom", f"custom:{url}", url, title=title)
    stamp = kit.now()
    for article in parsed.items:
        connection.execute(
            """INSERT OR IGNORE INTO nw_articles (id, feed_id, link, title, summary, source, image, published_at, fetched_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                kit.new_id(),
                feed["id"],
                article.link,
                article.title,
                article.summary,
                article.source or title,
                article.image,
                article.published_at,
                stamp,
            ),
        )
    connection.execute(
        "UPDATE nw_feeds SET last_fetched = ?, last_ok = ?, last_error = '' WHERE id = ?",
        (stamp, stamp, feed["id"]),
    )
    source_id = kit.new_id()
    connection.execute(
        "INSERT INTO nw_sources (id, profile_id, feed_id, title, url, created_at) VALUES (?, ?, ?, ?, ?, ?)",
        (source_id, me["id"], feed["id"], title, url, stamp),
    )
    connection.commit()
    return next(s for s in sources(connection, me) if s["id"] == source_id)


def remove_source(
    connection: sqlite3.Connection, me: dict[str, Any], source_id: str
) -> None:
    row = kit.fetch_one(
        connection,
        "SELECT * FROM nw_sources WHERE id = ? AND profile_id = ?",
        (source_id, me["id"]),
    )
    if not row:
        raise LookupError("That source was not found.")
    connection.execute("DELETE FROM nw_sources WHERE id = ?", (source_id,))
    connection.commit()


# ---------------------------------------------------------------------------
# saved and read
# ---------------------------------------------------------------------------


def _article(connection: sqlite3.Connection, article_id: str) -> dict[str, Any]:
    row = kit.fetch_one(
        connection, "SELECT * FROM nw_articles WHERE id = ?", (article_id,)
    )
    if not row:
        raise LookupError("That article was not found.")
    return row


def save(
    connection: sqlite3.Connection, me: dict[str, Any], article_id: str
) -> dict[str, Any]:
    row = _article(connection, article_id)
    connection.execute(
        "INSERT OR IGNORE INTO nw_saved (profile_id, article_id, saved_at) VALUES (?, ?, ?)",
        (me["id"], row["id"], kit.now()),
    )
    connection.commit()
    return _view(row, _saved_links(connection, me), _read_links(connection, me))


def unsave(
    connection: sqlite3.Connection, me: dict[str, Any], article_id: str
) -> dict[str, Any]:
    row = _article(connection, article_id)
    connection.execute(
        """DELETE FROM nw_saved WHERE profile_id = ? AND article_id IN
        (SELECT id FROM nw_articles WHERE link = ?)""",
        (me["id"], row["link"]),
    )
    connection.commit()
    return _view(row, _saved_links(connection, me), _read_links(connection, me))


def mark_read(
    connection: sqlite3.Connection, me: dict[str, Any], article_id: str
) -> dict[str, Any]:
    row = _article(connection, article_id)
    connection.execute(
        "INSERT OR IGNORE INTO nw_read (profile_id, article_id, read_at) VALUES (?, ?, ?)",
        (me["id"], row["id"], kit.now()),
    )
    connection.commit()
    return _view(row, _saved_links(connection, me), _read_links(connection, me))


def saved(connection: sqlite3.Connection, me: dict[str, Any]) -> list[dict[str, Any]]:
    rows = kit.fetch_all(
        connection,
        """SELECT a.* FROM nw_saved s JOIN nw_articles a ON a.id = s.article_id
        WHERE s.profile_id = ? ORDER BY s.saved_at DESC LIMIT 200""",
        (me["id"],),
    )
    saved_links, read_links = _saved_links(connection, me), _read_links(connection, me)
    seen: set[str] = set()
    out = []
    for row in rows:
        if row["link"] not in seen:
            seen.add(row["link"])
            out.append(_view(row, saved_links, read_links))
    return out


# ---------------------------------------------------------------------------
# briefing, badge and alerts
# ---------------------------------------------------------------------------


def briefing(
    connection: sqlite3.Connection, me: dict[str, Any], *, reader: Reader | None = None
) -> dict[str, Any]:
    """The top five headlines across what the person follows, and a short spoken summary in their language."""
    data = articles(connection, me, topic="all", limit=5, reader=reader)
    headlines = data["articles"]
    lang = data["lang"]
    language = feeds.LANGUAGES[lang]["name"]
    listed = "\n".join(
        f"{i}. {a['title']}" + (f" ({a['source']})" if a["source"] else "")
        for i, a in enumerate(headlines, 1)
    )
    fallback = {"summary": listed}
    if not headlines:
        return {
            "headlines": [],
            "summary": "",
            "source": "template",
            "language": lang,
            "error": data["error"],
        }
    prompt = (
        f"Write a short news briefing in {language} to be read aloud, three or four sentences, plain words, no "
        'opinions, only what these headlines say. Answer as JSON {"summary": "..."}.\n\nHeadlines:\n'
        + listed
    )
    drafted = kit.draft(prompt, fallback)
    return {
        "headlines": headlines,
        "summary": str(drafted.get("summary") or listed)[:2000],
        "source": drafted.get("source", "template"),
        "language": lang,
        "error": data["error"],
    }


def _since_opened(mine: dict[str, Any], hours: int) -> str:
    floor = kit.iso(kit.utcnow() - timedelta(hours=hours))
    opened = mine.get("last_opened") or ""
    return max(opened, floor) if opened else floor


def badge(connection: sqlite3.Connection, me: dict[str, Any]) -> int:
    """New headlines in what the person follows since they last opened News (at most 99; cache only)."""
    mine = settings(connection, me)
    since = mine["last_opened"] or kit.iso(kit.utcnow() - timedelta(hours=12))
    rows = _articles(
        connection,
        me,
        feeds_for(connection, me, "all"),
        since=since,
        limit=99,
        child_safe=mine["child_safe"],
    )
    return min(99, len(rows))


def keyword_alerts(
    connection: sqlite3.Connection, me: dict[str, Any]
) -> list[dict[str, Any]]:
    """A keyword marked "alert me" in a new headline (one alert per article; cache only)."""
    mine = settings(connection, me)
    since = _since_opened(mine, 6)
    out: list[dict[str, Any]] = []
    for keyword in keywords(connection, me):
        if not keyword["alert"]:
            continue
        feed = keyword_feed(connection, keyword["query"], mine["lang"], mine["region"])
        for article in _articles(
            connection, me, [feed], since=since, limit=3, child_safe=mine["child_safe"]
        ):
            if article["read"]:
                continue
            out.append(
                {
                    "id": f"news:{article['id']}",
                    "title": f"News about {keyword['query']}",
                    "body": article["title"],
                    "tone": "",
                    "speak": True,
                    "actions": [
                        life.action(
                            "Mark as read", f"/api/news/articles/{article['id']}/read"
                        )
                    ],
                }
            )
    return out[:5]


def today_lines(
    connection: sqlite3.Connection, me: dict[str, Any], *, reader: Reader | None = None
) -> dict[str, Any]:
    """Five headlines for Today. Uses the cache; fetches only feeds that were never fetched (a first visit)."""
    mine = settings(connection, me)
    feed_rows = feeds_for(connection, me, "all")
    for feed in feed_rows:
        if not feed.get("last_fetched"):
            refresh(connection, feed, reader=reader)
    feed_rows = [
        kit.fetch_one(connection, "SELECT * FROM nw_feeds WHERE id = ?", (f["id"],))
        or f
        for f in feed_rows
    ]
    items = _articles(connection, me, feed_rows, limit=5, child_safe=mine["child_safe"])
    return {"articles": items, "error": "" if items else NO_NEWS_ERROR}


# ---------------------------------------------------------------------------
# background
# ---------------------------------------------------------------------------


def background_refresh(
    connection: sqlite3.Connection, *, reader: Reader | None = None
) -> dict[str, Any]:
    """Refresh every stale feed someone follows, and drop old articles nobody saved."""
    people = {p["id"]: p for p in life.profiles(connection)}
    rows = kit.fetch_all(connection, "SELECT profile_id FROM nw_settings")
    wanted: dict[str, dict[str, Any]] = {}
    for row in rows:
        me = people.get(row["profile_id"])
        if me is None:
            continue
        for feed in feeds_for(connection, me, "all"):
            wanted[feed["id"]] = feed
    refreshed = failed = 0
    for feed in wanted.values():
        result = refresh(connection, feed, reader=reader)
        if result.get("skipped"):
            continue
        refreshed += 1
        if not result["ok"]:
            failed += 1
    cutoff = kit.iso(kit.utcnow() - timedelta(days=KEEP_DAYS))
    pruned = connection.execute(
        "DELETE FROM nw_articles WHERE fetched_at < ? AND id NOT IN (SELECT article_id FROM nw_saved)",
        (cutoff,),
    ).rowcount
    connection.execute(
        "DELETE FROM nw_read WHERE article_id NOT IN (SELECT id FROM nw_articles)"
    )
    connection.commit()
    return {
        "feeds": len(wanted),
        "refreshed": refreshed,
        "failed": failed,
        "pruned": pruned,
    }


# ---------------------------------------------------------------------------
# words
# ---------------------------------------------------------------------------


def ago(value: str, *, now: datetime | None = None) -> str:
    """ "5 minutes ago", "2 hours ago", "Yesterday", "3 days ago" (whole sentences, for translation)."""
    if not value:
        return ""
    try:
        then = kit.parse_time(value)
    except ValueError:
        return ""
    seconds = int(((now or datetime.now(timezone.utc)) - then).total_seconds())
    if seconds < 60:
        return "Just now"
    minutes = seconds // 60
    if minutes < 60:
        return "1 minute ago" if minutes == 1 else f"{minutes} minutes ago"
    hours = minutes // 60
    if hours < 24:
        return "1 hour ago" if hours == 1 else f"{hours} hours ago"
    days = hours // 24
    if days == 1:
        return "Yesterday"
    return f"{days} days ago"
