"""News rules: each person's settings, which feeds they follow, refreshing, listing, saving and the briefing.

A feed is refreshed when it is older than 30 minutes (on read, and by the background task for every feed someone
follows). A failed refresh keeps the earlier articles. Children are in child-safe mode by default (and cannot turn
it off): only the Science, Technology, Sports and Entertainment sections, no headline or summary with a blocklisted
word, and no sources of their own. In child-safe mode the words a person follows and what they search for are
looked up only in those safe sections (never a search of all the news). The same story reached through two feeds
(two topics, a topic and a keyword) shows once. A person can hide a source they do not want to read, and search any
news without following it. The newsletter a person can earn from is in :mod:`.letter`.
"""

from __future__ import annotations

import sqlite3
import unicodedata
from collections.abc import Callable, Iterable, Sequence
from datetime import datetime, timedelta, timezone
from typing import Any

from aira.suite import kit, life
from aira.suite.news import feeds

STALE = timedelta(minutes=30)
KEEP_DAYS = 14
MAX_KEYWORDS = 10
MAX_SOURCES = 15
MAX_MUTED = 50
SEARCH_MAX = 60
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
        "muted": muted(connection, me),
        "can_add_sources": not _is_child(me),
        "can_publish": life.is_adult(me),
        "has_newsletter": bool(
            life.is_adult(me)
            and kit.fetch_one(
                connection, "SELECT id FROM nw_letters WHERE owner_id = ?", (me["id"],)
            )
        ),
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
        if child:  # the words are looked up in the safe sections only
            return _safe_feeds(connection, mine) if keywords(connection, me) else []
        return [
            keyword_feed(connection, k["query"], lang, region)
            for k in keywords(connection, me)
        ]
    if topic == "all":
        out = _topic_feeds(connection, mine["topics"], lang, region)
        if child:
            if keywords(connection, me):
                known = {f["id"] for f in out}
                out += [
                    f for f in _safe_feeds(connection, mine) if f["id"] not in known
                ]
        else:
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


def _safe_feeds(
    connection: sqlite3.Connection, mine: dict[str, Any]
) -> list[dict[str, Any]]:
    """In child-safe mode, where followed words and searches are looked up: the safe sections, never all the news."""
    return _topic_feeds(connection, feeds.CHILD_TOPICS, mine["lang"], mine["region"])


def _matches(row: dict[str, Any], words: Sequence[str]) -> bool:
    """The headline or summary has every word of one of ``words`` (a followed word or a search)."""
    text = f"{row['title']} {row['summary'] or ''}".casefold()
    return any(
        all(part in text for part in phrase.casefold().split())
        for phrase in words
        if phrase.strip()
    )


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


def muted(connection: sqlite3.Connection, me: dict[str, Any]) -> list[dict[str, Any]]:
    """The sources the person hid, by name."""
    return kit.fetch_all(
        connection,
        "SELECT id, source, created_at FROM nw_muted WHERE profile_id = ? ORDER BY source COLLATE NOCASE",
        (me["id"],),
    )


def _muted_names(connection: sqlite3.Connection, me: dict[str, Any]) -> set[str]:
    return {r["source"].casefold() for r in muted(connection, me)}


def _story_key(title: str) -> str:
    """Headlines that differ only in case, spaces or punctuation are the same story.

    Letters, digits and combining marks are kept: Python's word pattern does not match the vowel signs (matras) of Hindi
    and Telugu, and dropping them would make different headlines ("दिल की बात", "दाल की बात") the same.
    """
    kept = "".join(
        ch if unicodedata.category(ch)[0] in "LNM" else " "
        for ch in unicodedata.normalize("NFC", title.casefold())
    )
    return " ".join(kept.split())


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
    words: Sequence[str] | None = None,
) -> list[dict[str, Any]]:
    """``words``: keep only articles about one of them (child-safe mode looks words up in the safe sections)."""
    if words is not None and not any(w.strip() for w in words):
        return []
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
    hidden = _muted_names(connection, me)
    seen: set[str] = set()
    stories: set[str] = set()
    out = []
    for row in rows:
        story = _story_key(row["title"])
        if row["link"] in seen or (story and story in stories):
            continue  # the same story through another feed
        if child_safe and not (
            feeds.child_safe_title(row["title"])
            and feeds.child_safe_title(row["summary"])
        ):
            continue
        if row["source"] and row["source"].casefold() in hidden:
            continue
        if words is not None and not _matches(row, words):
            continue
        seen.add(row["link"])
        stories.add(story)
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
    words = (
        [k["query"] for k in keywords(connection, me)]
        if mine["child_safe"] and (topic or "top") == "foryou"
        else None
    )
    items = _articles(
        connection,
        me,
        feed_rows,
        since=since,
        limit=limit,
        child_safe=mine["child_safe"],
        words=words,
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
    if _is_child(me):
        raise PermissionError("Ask an adult to add a news source.")
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


def search(
    connection: sqlite3.Connection,
    me: dict[str, Any],
    query: Any,
    *,
    reader: Reader | None = None,
) -> dict[str, Any]:
    """Any news about ``query`` (in the person's language and country) without following it.

    A search is a feed like a keyword's (kept 30 minutes, shared by everyone who searched the same words); one
    nobody follows is dropped by the background task after a day.
    """
    words = " ".join(
        kit.text(query, "what to search for", max_len=60, required=True).split()
    )
    mine = settings(connection, me)
    if mine["child_safe"]:
        # Child-safe mode: only the safe sections are searched (a search of all the news would get around them).
        scope = _safe_feeds(connection, mine)
        ok = all([refresh(connection, f, reader=reader)["ok"] for f in scope])
        scope = [
            kit.fetch_one(connection, "SELECT * FROM nw_feeds WHERE id = ?", (f["id"],))
            or f
            for f in scope
        ]
        items = _articles(
            connection, me, scope, limit=SEARCH_MAX, child_safe=True, words=[words]
        )
    else:
        feed = keyword_feed(connection, words, mine["lang"], mine["region"])
        ok = refresh(connection, feed, reader=reader)["ok"]
        feed = (
            kit.fetch_one(
                connection, "SELECT * FROM nw_feeds WHERE id = ?", (feed["id"],)
            )
            or feed
        )
        items = _articles(connection, me, [feed], limit=SEARCH_MAX, child_safe=False)
    error = ""
    if not ok:
        error = REFRESH_ERROR if items else NO_NEWS_ERROR
    following = any(
        k["query"].casefold() == words.casefold() for k in keywords(connection, me)
    )
    return {
        "query": words,
        "articles": items,
        "error": error,
        "following": following,
        "child_safe": mine["child_safe"],
    }


def mute(
    connection: sqlite3.Connection, me: dict[str, Any], raw: dict[str, Any]
) -> dict[str, Any]:
    """Hide every headline from a source (by its name, as the cards show it). Hiding it again changes nothing."""
    name = kit.text(raw.get("source"), "the source to hide", max_len=120, required=True)
    existing = kit.fetch_one(
        connection,
        "SELECT id, source, created_at FROM nw_muted WHERE profile_id = ? AND lower(source) = lower(?)",
        (me["id"], name),
    )
    if existing:
        return existing
    if len(muted(connection, me)) >= MAX_MUTED:
        raise ValueError(
            f"You can hide up to {MAX_MUTED} sources. Show one again first."
        )
    row = {"id": kit.new_id(), "source": name, "created_at": kit.now()}
    connection.execute(
        "INSERT INTO nw_muted (id, profile_id, source, created_at) VALUES (?, ?, ?, ?)",
        (row["id"], me["id"], name, row["created_at"]),
    )
    connection.commit()
    return row


def unmute(connection: sqlite3.Connection, me: dict[str, Any], muted_id: str) -> None:
    removed = connection.execute(
        "DELETE FROM nw_muted WHERE id = ? AND profile_id = ?", (muted_id, me["id"])
    ).rowcount
    connection.commit()
    if not removed:
        raise LookupError("That hidden source was not found.")


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


SAVED_COLUMNS = ("title", "source", "link", "published_at", "saved_at", "summary")


def saved_rows(
    connection: sqlite3.Connection, me: dict[str, Any]
) -> list[dict[str, Any]]:
    """The person's saved articles for a CSV download (with when each was saved)."""
    return kit.fetch_all(
        connection,
        """SELECT a.title, a.source, a.link, COALESCE(NULLIF(a.published_at, ''), a.fetched_at) AS published_at,
        s.saved_at, a.summary FROM nw_saved s JOIN nw_articles a ON a.id = s.article_id
        WHERE s.profile_id = ? ORDER BY s.saved_at DESC""",
        (me["id"],),
    )


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
        if mine["child_safe"]:
            scope, words = _safe_feeds(connection, mine), [keyword["query"]]
        else:
            scope = [
                keyword_feed(connection, keyword["query"], mine["lang"], mine["region"])
            ]
            words = None
        for article in _articles(
            connection,
            me,
            scope,
            since=since,
            limit=3,
            child_safe=mine["child_safe"],
            words=words,
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
    failed = False
    for feed in feed_rows:
        if (
            not feed.get("last_fetched")
            and not refresh(connection, feed, reader=reader)["ok"]
        ):
            failed = True
    feed_rows = [
        kit.fetch_one(connection, "SELECT * FROM nw_feeds WHERE id = ?", (f["id"],))
        or f
        for f in feed_rows
    ]
    items = _articles(connection, me, feed_rows, limit=5, child_safe=mine["child_safe"])
    broken = failed or any(
        f.get("last_error") and not f.get("last_ok") for f in feed_rows
    )
    return {"articles": items, "error": NO_NEWS_ERROR if broken and not items else ""}


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
    # One-off searches nobody follows: drop the feed (and its articles nobody saved) after a day.
    old_search = kit.iso(kit.utcnow() - timedelta(days=1))
    for row in kit.fetch_all(
        connection,
        "SELECT id FROM nw_feeds WHERE kind = 'search' AND created_at < ?",
        (old_search,),
    ):
        if row["id"] in wanted:
            continue
        pruned += connection.execute(
            "DELETE FROM nw_articles WHERE feed_id = ? AND id NOT IN (SELECT article_id FROM nw_saved)",
            (row["id"],),
        ).rowcount
        if not kit.count(
            connection,
            "SELECT COUNT(*) FROM nw_articles WHERE feed_id = ?",
            (row["id"],),
        ):
            connection.execute("DELETE FROM nw_feeds WHERE id = ?", (row["id"],))
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
