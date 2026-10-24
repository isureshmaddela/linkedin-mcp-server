"""Local cache of the LinkedIn inbox so Aira can be the messenger.

LinkedIn is slow and only reachable through a browser session, so reading a
conversation must never wait on it. Aira keeps the last known list of threads
and the messages of the threads it has fetched in SQLite, shows them
immediately, and refreshes them in the background.

Nothing here talks to LinkedIn or to a model. The module holds three things:

* pure parsers for the text LinkedIn's inbox page produces (one preview row per
  thread) and for the relative dates LinkedIn prints ("TODAY", "WED", "OCT 7");
* the SQLite tables and the functions that read and write them;
* the sync-run ledger that records the progress of a background refresh.

Every function runs inside the caller's transaction and leaves committing (and
closing) the connection to the caller, like the other aira modules.

Messages are never invented. A thread's messages are exactly what the last
successful fetch returned, plus any reply Aira sent itself that LinkedIn has not
shown yet. Such a reply is appended at once and dropped when a later fetch
contains the same text.
"""

from __future__ import annotations

import hashlib
import re
import sqlite3
from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Any

STALE_AFTER_SECONDS = 600
MAX_SNIPPET_CHARS = 240
MAX_MESSAGE_CHARS = 20_000
MAX_RAW_TEXT_CHARS = 60_000
MAX_MESSAGES_PER_THREAD = 500
LOCAL_MESSAGE_TTL_SECONDS = 48 * 3600
RETENTION_DAYS = 90
LAST_SENDERS = ("them", "us", "unknown")


def ensure_schema(connection: sqlite3.Connection) -> None:
    """Create the cache tables. The definition lives in migration 11 so it stays frozen."""
    from aira.schema import inbox_cache_v1

    inbox_cache_v1(connection)


# ---------------------------------------------------------------------------
# Text helpers
# ---------------------------------------------------------------------------


def collapse(text: str) -> str:
    return re.sub(r"\s+", " ", text or "").strip()


def fold(text: str) -> str:
    return collapse(text).casefold()


def snippet_of(text: str) -> str:
    value = collapse(text)
    if len(value) <= MAX_SNIPPET_CHARS:
        return value
    return value[: MAX_SNIPPET_CHARS - 1].rstrip() + "…"


def is_stale(synced_at: int | None, now: int) -> bool:
    """True when the cache was never refreshed or is older than ten minutes."""
    return not synced_at or now - int(synced_at) > STALE_AFTER_SECONDS


# ---------------------------------------------------------------------------
# LinkedIn date and time labels
# ---------------------------------------------------------------------------

_MONTHS = {
    name: number
    for number, name in enumerate(
        (
            "jan",
            "feb",
            "mar",
            "apr",
            "may",
            "jun",
            "jul",
            "aug",
            "sep",
            "oct",
            "nov",
            "dec",
        ),
        1,
    )
}
_WEEKDAYS = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")
_WEEKDAY_NAMES = (
    "monday",
    "tuesday",
    "wednesday",
    "thursday",
    "friday",
    "saturday",
    "sunday",
)
_MONTH_DAY_RE = re.compile(r"^([a-z]{3,9})\.?\s+(\d{1,2})(?:,\s*(\d{4}))?$")
_CLOCK_RE = re.compile(r"^(\d{1,2}):(\d{2})\s?([ap])\.?m\.?$", re.IGNORECASE)


def parse_clock(label: str) -> tuple[int, int] | None:
    """Hour and minute (24h) of a label such as "12:59 PM", or None."""
    match = _CLOCK_RE.match(collapse(label))
    if not match:
        return None
    hour, minute = int(match[1]), int(match[2])
    if not 1 <= hour <= 12 or minute > 59:
        return None
    if match[3].lower() == "p":
        hour = hour % 12 + 12
    else:
        hour = hour % 12
    return hour, minute


def resolve_date(label: str, today: date) -> str:
    """The calendar date (ISO) a LinkedIn date header refers to, or "".

    Headers are relative to the moment the page was read: "TODAY", "YESTERDAY",
    a weekday ("WEDNESDAY", "Wed") for the last week, or a month and day with
    an optional year. A month and day without a year is the most recent such
    day that is not in the future.
    """
    value = fold(label)
    if not value:
        return ""
    if value == "today":
        return today.isoformat()
    if value == "yesterday":
        return (today - timedelta(days=1)).isoformat()
    index = None
    if value in _WEEKDAY_NAMES:
        index = _WEEKDAY_NAMES.index(value)
    elif value in _WEEKDAYS:
        index = _WEEKDAYS.index(value)
    if index is not None:
        # The same weekday as today is printed as "TODAY", so it means a week ago.
        delta = (today.weekday() - index) % 7 or 7
        return (today - timedelta(days=delta)).isoformat()
    match = _MONTH_DAY_RE.match(value)
    if not match:
        return ""
    month = _MONTHS.get(match[1][:3])
    if month is None:
        return ""
    year = int(match[3]) if match[3] else today.year
    try:
        resolved = date(year, month, int(match[2]))
        if not match[3] and resolved > today:
            resolved = date(year - 1, month, int(match[2]))
    except ValueError:
        return ""
    return resolved.isoformat()


def resolve_message_time(date_label: str, time_label: str, today: date) -> str:
    """ "YYYY-MM-DDTHH:MM" (local time) for a message, "YYYY-MM-DD" without a clock."""
    resolved = resolve_date(date_label, today)
    if not resolved:
        return ""
    clock = parse_clock(time_label)
    if clock is None:
        return resolved
    return f"{resolved}T{clock[0]:02d}:{clock[1]:02d}"


def resolve_list_time(label: str, now: datetime) -> str:
    """When the newest message of a thread was written, from the inbox list label.

    The list prints only a clock for today ("12:59 PM") and a date or weekday
    for older threads. A clock later than now belongs to yesterday.
    """
    clock = parse_clock(label)
    if clock is None:
        return resolve_date(label, now.date())
    moment = now.replace(hour=clock[0], minute=clock[1], second=0, microsecond=0)
    if moment > now + timedelta(minutes=5):
        moment -= timedelta(days=1)
    return moment.strftime("%Y-%m-%dT%H:%M")


def clock_label(moment: datetime) -> str:
    """ "9:05 AM" style label, matching how LinkedIn prints message times."""
    return moment.strftime("%I:%M %p").lstrip("0")


# ---------------------------------------------------------------------------
# Inbox list text
# ---------------------------------------------------------------------------
# The inbox page lists each thread as: name, time label (printed twice), a blank
# line, a preview ("Prem: If not, let me know" or "You: I am interested"),
# accessibility lines, and "Open the options list in your conversation with
# <names>". The list ends at "Load more conversations"; text after it is the
# open thread and is ignored.

_OPTIONS_RE = re.compile(
    r"^Open the options list in your conversation with (?P<who>.+)$", re.IGNORECASE
)
_LIST_END_RE = re.compile(r"^load more conversations$", re.IGNORECASE)
_LABEL_RE = re.compile(
    r"^(?:\d{1,2}:\d{2}\s?[AP]M|yesterday|today"
    r"|(?:mon(?:day)?|tue(?:s(?:day)?)?|wed(?:nesday)?|thu(?:r(?:s(?:day)?)?)?"
    r"|fri(?:day)?|sat(?:urday)?|sun(?:day)?)"
    r"|[A-Za-z]{3,9}\.?\s+\d{1,2}(?:,\s*\d{4})?)$",
    re.IGNORECASE,
)
_PREVIEW_PREFIX_RE = re.compile(r"^(?P<who>[^:\n]{1,60}?):\s+(?P<text>\S.*)$")


@dataclass(frozen=True, slots=True)
class InboxRow:
    name: str
    who: str
    label: str
    preview: str


def _is_noise(line: str) -> bool:
    lowered = line.casefold()
    return lowered.startswith(("status is ", ". ")) or lowered in {
        "active now",
        "conversation list",
    }


def _row_from_block(block: Sequence[str], who: str) -> InboxRow | None:
    lines = [collapse(line) for line in block if collapse(line)]
    for index, line in enumerate(lines):
        if index == 0 or not _LABEL_RE.match(line):
            continue
        name = next(
            (item for item in reversed(lines[:index]) if not _is_noise(item)), ""
        )
        if not name:
            return None
        rest = lines[index + 1 :]
        while rest and rest[0].casefold() == line.casefold():
            rest = rest[1:]
        preview: list[str] = []
        for item in rest:
            if item.startswith(". "):
                break
            preview.append(item)
        return InboxRow(name, collapse(who), line, collapse(" ".join(preview)))
    return None


def parse_inbox_list(text: str) -> list[InboxRow]:
    """One row per thread listed on the inbox page, newest first. Best effort."""
    rows: list[InboxRow] = []
    block: list[str] = []
    for raw in (text or "").splitlines():
        line = raw.strip()
        if _LIST_END_RE.match(line):
            break
        match = _OPTIONS_RE.match(line)
        if match:
            row = _row_from_block(block, match["who"])
            if row is not None:
                rows.append(row)
            block = []
            continue
        block.append(line)
    return rows


def match_rows(
    conversations: Sequence[Mapping[str, Any]], rows: Sequence[InboxRow]
) -> dict[str, InboxRow]:
    """Pair each conversation (thread_id, participant) with its list row.

    A row matches by exact name first, then by the whole name appearing in the
    "conversation with A and B" text. The second pass needs word boundaries, so
    "Ann" never claims the row of "Joanne Smith". Each row is used once.
    """
    used: set[int] = set()
    matched: dict[str, InboxRow] = {}
    for exact in (True, False):
        for conversation in conversations:
            thread_id = str(conversation["thread_id"])
            name = fold(str(conversation.get("participant") or ""))
            if thread_id in matched or not name or name == "conversation":
                continue
            inside = re.compile(rf"(?<!\w){re.escape(name)}(?!\w)")
            for index, row in enumerate(rows):
                if index in used:
                    continue
                if (exact and fold(row.name) == name) or (
                    not exact and inside.search(fold(row.who))
                ):
                    matched[thread_id] = row
                    used.add(index)
                    break
    return matched


def classify_preview(
    preview: str, participant: str, own_names: Sequence[str]
) -> tuple[str, str]:
    """Split a list preview into (who wrote it, message text).

    "You: ..." and "<own first name>: ..." are the account owner, a prefix that
    starts the participant's name is the other person. Anything else is left
    whole with an "unknown" sender, because a colon inside a message ("Thanks:
    see you") must not be mistaken for a sender label.
    """
    match = _PREVIEW_PREFIX_RE.match(collapse(preview))
    if not match:
        return "unknown", collapse(preview)
    token = fold(match["who"]).rstrip(".")
    text = match["text"].strip()
    if token == "you":
        return "us", text
    owns = {fold(name) for name in own_names if name}
    owns |= {name.split(" ")[0] for name in owns}
    if token in owns:
        return "us", text
    other = fold(participant)
    if (
        token
        and other
        and (other.startswith(token) or token.split(" ")[0] == other.split(" ")[0])
    ):
        return "them", text
    return "unknown", collapse(preview)


# ---------------------------------------------------------------------------
# Rows
# ---------------------------------------------------------------------------


def _thread_dict(row: sqlite3.Row | None) -> dict[str, Any] | None:
    if row is None:
        return None
    item = dict(row)
    item["needs_reply"] = bool(item["needs_reply"])
    item["is_recruiter"] = bool(item["is_recruiter"])
    return item


def get_thread(connection: sqlite3.Connection, thread_id: str) -> dict[str, Any] | None:
    return _thread_dict(
        connection.execute(
            "SELECT * FROM inbox_threads WHERE thread_id = ?", (thread_id,)
        ).fetchone()
    )


def get_messages(
    connection: sqlite3.Connection, thread_id: str
) -> list[dict[str, Any]]:
    rows = connection.execute(
        "SELECT * FROM inbox_messages WHERE thread_id = ? ORDER BY ordinal, message_id",
        (thread_id,),
    ).fetchall()
    return [
        {
            "sender": row["sender"],
            "own": bool(row["own"]),
            "date": row["date_label"],
            "sent_on": row["sent_on"],
            "time": row["time_label"],
            "text": row["text"],
            "source": row["source"],
        }
        for row in rows
    ]


def _like(value: str) -> str:
    """A LIKE pattern matching ``value`` anywhere; the escape character is ^."""
    escaped = value.replace("^", "^^").replace("%", "^%").replace("_", "^_")
    return f"%{escaped}%"


_LATEST_MESSAGE = (
    "(SELECT {column} FROM inbox_messages m WHERE m.thread_id = inbox_threads.thread_id"
    " ORDER BY m.ordinal DESC, m.message_id DESC LIMIT 1)"
)


def list_threads(
    connection: sqlite3.Connection, *, q: str = "", limit: int = 500
) -> list[dict[str, Any]]:
    """Cached threads, newest listing first. ``q`` searches names and message text.

    Each thread also carries ``latest_text``, the newest cached message, so a
    row whose list preview was unreadable can still show something real.
    """
    sql = f"SELECT inbox_threads.*, {_LATEST_MESSAGE.format(column='text')} AS latest_text FROM inbox_threads"
    params: list[Any] = []
    needle = fold(q)
    if needle:
        # SQLite's LIKE only ignores the case of ASCII letters: fold both sides.
        connection.create_function("fold", 1, fold, deterministic=True)
        pattern = _like(needle)
        sql += (
            " WHERE fold(participant) LIKE ? ESCAPE '^' OR fold(snippet) LIKE ? ESCAPE '^'"
            " OR EXISTS (SELECT 1 FROM inbox_messages m WHERE m.thread_id = inbox_threads.thread_id"
            " AND fold(m.text) LIKE ? ESCAPE '^')"
        )
        params += [pattern, pattern, pattern]
    sql += " ORDER BY listed_at DESC, position ASC, thread_id LIMIT ?"
    params.append(limit)
    return [
        item
        for item in (_thread_dict(row) for row in connection.execute(sql, params))
        if item is not None
    ]


def latest_listing_time(connection: sqlite3.Connection) -> int:
    row = connection.execute("SELECT MAX(listed_at) FROM inbox_threads").fetchone()
    return int(row[0] or 0)


def last_synced_at(connection: sqlite3.Connection) -> int:
    """When the saved inbox was last confirmed current: a new list or a good sync run.

    A sync that finds nothing new still confirms the copy, so it counts too. A
    sync that ended in an error does not: the list it saved may be new, but the
    run could not refresh the conversations, so it must not read as "just synced".
    """
    good = connection.execute(
        "SELECT MAX(finished_at) FROM inbox_sync_runs WHERE status IN ('ok', 'partial')"
    ).fetchone()
    newest = latest_sync_run(connection)
    if (
        newest is not None
        and newest["status"] in ("error", "interrupted")
        and newest["finished_at"] is not None
    ):
        # Lists saved by the failed run are not proof that the copy is current.
        row = connection.execute(
            "SELECT MAX(listed_at) FROM inbox_threads WHERE listed_at NOT BETWEEN ? AND ?",
            (newest["started_at"], newest["finished_at"]),
        ).fetchone()
        listing = int(row[0] or 0)
    else:
        listing = latest_listing_time(connection)
    return max(listing, int(good[0] or 0))


def last_attempt_at(connection: sqlite3.Connection) -> int:
    """When the newest sync run ended, whatever its outcome."""
    row = connection.execute("SELECT MAX(finished_at) FROM inbox_sync_runs").fetchone()
    return int(row[0] or 0)


def confirmed_at(thread: Mapping[str, Any] | None) -> int:
    """When a thread's cached messages were last known to be current.

    The messages are as old as the last fetch, unless the inbox list has been
    read since and still shows the preview the fetch started from: then nothing
    new arrived and the list read confirms them.
    """
    if not thread:
        return 0
    fetched = int(thread.get("messages_synced_at") or 0)
    if (
        fetched
        and thread.get("signature")
        and thread.get("fetched_signature") == thread.get("signature")
    ):
        return max(fetched, int(thread.get("listed_at") or 0))
    return fetched


def is_unread(thread: Mapping[str, Any]) -> bool:
    """New activity from the other person that has not been opened in Aira."""
    if thread["last_sender"] == "us" or not thread["signature"]:
        return False
    return thread["read_signature"] != thread["signature"]


# ---------------------------------------------------------------------------
# Writes
# ---------------------------------------------------------------------------


def signature_of(label: str, sender: str, snippet: str) -> str:
    """Fingerprint of a list row, or "" when the row carried nothing readable.

    An empty fingerprint means "unknown": every unreadable row would otherwise
    share one constant value and hide real changes.
    """
    if not fold(label) and not fold(snippet):
        return ""
    raw = "\x1f".join((fold(label), sender, fold(snippet)))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:24]


def upsert_listing(
    connection: sqlite3.Connection, rows: Sequence[Mapping[str, Any]], now: int
) -> int:
    """Store the newest inbox list. Keys per row: thread_id, participant, snippet,
    label, at, last_sender, needs_reply, is_recruiter.

    A thread whose preview did not change since its messages were last fetched
    keeps the values derived from those messages, which are more precise than
    anything read from a one-line preview.
    """
    stored = 0
    for position, row in enumerate(rows):
        thread_id = str(row["thread_id"])
        sender = row.get("last_sender") or "unknown"
        if sender not in LAST_SENDERS:
            sender = "unknown"
        snippet = snippet_of(str(row.get("snippet") or ""))
        label = collapse(str(row.get("label") or ""))
        signature = signature_of(label, sender, snippet)
        needs_reply = int(sender == "them" and bool(row.get("needs_reply")))
        participant = collapse(str(row.get("participant") or ""))[:200]
        existing = connection.execute(
            "SELECT * FROM inbox_threads WHERE thread_id = ?", (thread_id,)
        ).fetchone()
        if existing is None:
            connection.execute(
                """INSERT INTO inbox_threads (thread_id, participant, snippet, last_message_label,
                    last_message_at, last_sender, needs_reply, is_recruiter, position, signature,
                    listed_at, synced_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    thread_id,
                    participant,
                    snippet,
                    label,
                    str(row.get("at") or ""),
                    sender,
                    needs_reply,
                    int(bool(row.get("is_recruiter"))),
                    position,
                    signature,
                    now,
                    now,
                ),
            )
            stored += 1
            continue
        keep_name = existing["participant"]
        if participant and participant.casefold() != "conversation":
            keep_name = participant
        if not signature:
            # Nothing readable in this row (a parse miss, an unmatched name, a
            # label in another language): keep what the earlier list or fetch
            # taught us instead of blanking the preview and the reply flag. The
            # empty signature marks the thread as "not confirmed", so a sync
            # reads it again instead of trusting an old fingerprint.
            connection.execute(
                """UPDATE inbox_threads SET participant = ?, position = ?, signature = '', listed_at = ?,
                    synced_at = ? WHERE thread_id = ?""",
                (keep_name, position, now, now, thread_id),
            )
            stored += 1
            continue
        unchanged = bool(
            existing["fetched_signature"] and existing["fetched_signature"] == signature
        )
        connection.execute(
            """UPDATE inbox_threads SET participant = ?, position = ?, signature = ?, listed_at = ?,
                synced_at = ?, snippet = ?, last_message_label = ?, last_message_at = ?,
                last_sender = ?, needs_reply = ?, is_recruiter = ?, read_signature = ?
            WHERE thread_id = ?""",
            (
                keep_name,
                position,
                signature,
                now,
                now,
                existing["snippet"] if unchanged else snippet,
                existing["last_message_label"] if unchanged else label,
                existing["last_message_at"]
                if unchanged
                else str(row.get("at") or existing["last_message_at"]),
                existing["last_sender"] if unchanged else sender,
                existing["needs_reply"] if unchanged else needs_reply,
                (existing["is_recruiter"] or int(bool(row.get("is_recruiter"))))
                if not existing["messages_synced_at"]
                else existing["is_recruiter"],
                # A thread opened before it was ever listed counts as read.
                signature
                if existing["read_signature"] == "*"
                else existing["read_signature"],
                thread_id,
            ),
        )
        stored += 1
    return stored


def _message_key(
    sender: str, sent_on: str, time_label: str, text: str, occurrence: int
) -> str:
    raw = "\x1f".join(
        (fold(sender), sent_on, fold(time_label), fold(text), str(occurrence))
    )
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:32]


def _local_stamp(sent_on: str, time_label: str) -> str:
    """ "YYYY-MM-DDTHH:MM" from a stored ISO date and a clock label, or the date alone."""
    clock = parse_clock(time_label)
    if not sent_on or clock is None:
        return sent_on
    return f"{sent_on}T{clock[0]:02d}:{clock[1]:02d}"


def _insert_message(
    connection: sqlite3.Connection,
    thread_id: str,
    ordinal: int,
    key: str,
    message: Mapping[str, Any],
    source: str,
    now: int,
) -> None:
    connection.execute(
        """INSERT OR IGNORE INTO inbox_messages (thread_id, ordinal, msg_key, sender, own, date_label,
            sent_on, time_label, text, source, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            thread_id,
            ordinal,
            key,
            collapse(str(message.get("sender") or ""))[:200],
            int(bool(message.get("own"))),
            collapse(str(message.get("date") or ""))[:40],
            str(message.get("sent_on") or ""),
            collapse(str(message.get("time") or ""))[:20],
            str(message.get("text") or "")[:MAX_MESSAGE_CHARS],
            source,
            now,
        ),
    )


def store_conversation(
    connection: sqlite3.Connection,
    thread_id: str,
    *,
    participant: str,
    messages: Sequence[Mapping[str, Any]],
    raw_text: str,
    last_sender: str,
    needs_reply: bool,
    is_recruiter: bool,
    now: int,
) -> bool:
    """Replace a thread's messages with a fresh fetch. Returns False when kept.

    ``messages`` are dicts with sender, own, date, sent_on, time, text, oldest
    first. A fetch that parsed no messages never wipes a cache that has some;
    only the raw text is refreshed. Replies Aira sent that the fetch now shows
    are dropped; the others stay until they show up or expire.
    """
    today = datetime.fromtimestamp(now).date()
    fresh = [
        {
            **m,
            "sent_on": m.get("sent_on")
            or resolve_date(str(m.get("date") or ""), today),
        }
        for m in messages
        if str(m.get("text") or "").strip()
    ][-MAX_MESSAGES_PER_THREAD:]
    sender = last_sender if last_sender in LAST_SENDERS else "unknown"
    # Take the write lock before reading anything: a reply Aira appends while
    # this fetch is being stored must either be seen by the reads below or wait
    # until this transaction ends (a no-op write opens it in sqlite3's default
    # transaction mode).
    connection.execute("DELETE FROM inbox_messages WHERE 0")
    existing = connection.execute(
        "SELECT * FROM inbox_threads WHERE thread_id = ?", (thread_id,)
    ).fetchone()
    had_messages = bool(
        existing
        and connection.execute(
            "SELECT 1 FROM inbox_messages WHERE thread_id = ? LIMIT 1", (thread_id,)
        ).fetchone()
    )
    if not fresh and had_messages:
        connection.execute(
            "UPDATE inbox_threads SET raw_text = ?, synced_at = ? WHERE thread_id = ?",
            (raw_text[:MAX_RAW_TEXT_CHARS], now, thread_id),
        )
        return False
    pending = connection.execute(
        "SELECT * FROM inbox_messages WHERE thread_id = ? AND source = 'aira' ORDER BY ordinal",
        (thread_id,),
    ).fetchall()
    # What LinkedIn showed as the account owner's own messages before this
    # fetch: a pending reply counts as delivered only when this fetch adds one.
    earlier_own = [
        (fold(row["text"]), row["sent_on"])
        for row in connection.execute(
            "SELECT text, sent_on FROM inbox_messages"
            " WHERE thread_id = ? AND source != 'aira' AND own = 1",
            (thread_id,),
        )
    ]
    connection.execute(
        "DELETE FROM inbox_messages WHERE thread_id = ? AND source != 'aira'",
        (thread_id,),
    )
    seen: dict[str, int] = {}
    ordinal = 0
    for message in fresh:
        base = _message_key(
            str(message.get("sender") or ""),
            str(message.get("sent_on") or ""),
            str(message.get("time") or ""),
            str(message.get("text") or ""),
            0,
        )
        seen[base] = seen.get(base, -1) + 1
        key = (
            base
            if seen[base] == 0
            else _message_key(
                str(message.get("sender") or ""),
                str(message.get("sent_on") or ""),
                str(message.get("time") or ""),
                str(message.get("text") or ""),
                seen[base],
            )
        )
        _insert_message(connection, thread_id, ordinal, key, message, "linkedin", now)
        ordinal += 1
    # A reply Aira appended is delivered when this fetch lists one more own
    # message with the same text than the fetch before it, written no earlier
    # than the day before the reply was sent. Matching on text alone would let an
    # older identical message ("Thanks, that works for me.") stand in for it.
    delivered: Counter[str] = Counter()
    kept: list[sqlite3.Row] = []
    for row in pending:
        text_key = fold(row["text"])
        since = (
            datetime.fromtimestamp(int(row["created_at"])).date() - timedelta(days=1)
        ).isoformat()
        listed = sum(
            1
            for m in fresh
            if m.get("own")
            and fold(str(m.get("text") or "")) == text_key
            and (not m.get("sent_on") or str(m["sent_on"]) >= since)
        )
        before = sum(
            1
            for text, sent_on in earlier_own
            if text == text_key and (not sent_on or sent_on >= since)
        )
        if listed - before - delivered[text_key] > 0:
            delivered[text_key] += 1
            connection.execute(
                "DELETE FROM inbox_messages WHERE message_id = ?",
                (row["message_id"],),
            )
        elif now - int(row["created_at"]) > LOCAL_MESSAGE_TTL_SECONDS:
            connection.execute(
                "DELETE FROM inbox_messages WHERE message_id = ?",
                (row["message_id"],),
            )
        else:
            connection.execute(
                "UPDATE inbox_messages SET ordinal = ? WHERE message_id = ?",
                (ordinal, row["message_id"]),
            )
            ordinal += 1
            kept.append(row)
    total = connection.execute(
        "SELECT COUNT(*) FROM inbox_messages WHERE thread_id = ?", (thread_id,)
    ).fetchone()[0]
    newest = fresh[-1] if fresh else None
    newest_at = (
        resolve_message_time(
            str(newest.get("date") or ""), str(newest.get("time") or ""), today
        )
        if newest
        else ""
    )
    newest_snippet = snippet_of(str(newest["text"])) if newest else ""
    newest_label = collapse(str(newest.get("time") or "")) if newest else ""
    last_reply = kept[-1] if kept else None
    if last_reply is not None:
        # The reply Aira sent is still the newest message and LinkedIn has not
        # listed it yet. A fetch that began before the send must not turn the
        # thread back into "they wrote last".
        sender, needs_reply = "us", False
        newest_snippet = snippet_of(last_reply["text"])
        newest_label = last_reply["time_label"]
        newest_at = _local_stamp(last_reply["sent_on"], last_reply["time_label"])
    if existing is None:
        connection.execute(
            """INSERT INTO inbox_threads (thread_id, participant, snippet, last_message_label,
                last_message_at, last_sender, needs_reply, is_recruiter, message_count, raw_text,
                read_signature, messages_synced_at, synced_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, '*', ?, ?)""",
            (
                thread_id,
                collapse(participant)[:200],
                newest_snippet,
                newest_label,
                newest_at,
                sender,
                int(needs_reply and sender == "them"),
                int(is_recruiter),
                total,
                raw_text[:MAX_RAW_TEXT_CHARS] if not fresh else "",
                now,
                now,
            ),
        )
        return True
    snippet = (
        newest_snippet
        if last_reply is not None
        else (existing["snippet"] or newest_snippet)
    )
    label = (
        newest_label
        if last_reply is not None
        else (existing["last_message_label"] or newest_label)
    )
    connection.execute(
        """UPDATE inbox_threads SET participant = ?, snippet = ?, last_message_label = ?,
            last_message_at = ?, last_sender = ?, needs_reply = ?, is_recruiter = ?,
            message_count = ?, raw_text = ?, fetched_signature = signature,
            messages_synced_at = ?, synced_at = ?
        WHERE thread_id = ?""",
        (
            collapse(participant)[:200] or existing["participant"],
            snippet,
            label,
            newest_at or existing["last_message_at"],
            sender,
            int(needs_reply and sender == "them"),
            int(is_recruiter or bool(existing["is_recruiter"])),
            total,
            raw_text[:MAX_RAW_TEXT_CHARS] if not fresh else "",
            now,
            now,
            thread_id,
        ),
    )
    return True


def append_local_message(
    connection: sqlite3.Connection,
    thread_id: str,
    *,
    text: str,
    sender: str,
    participant: str,
    moment: datetime,
    now: int,
    send_id: str,
) -> None:
    """Show a reply Aira just sent without waiting for LinkedIn to list it."""
    body = text.strip()
    if not body:
        return
    label = clock_label(moment)
    existing = connection.execute(
        "SELECT 1 FROM inbox_threads WHERE thread_id = ?", (thread_id,)
    ).fetchone()
    if existing is None:
        connection.execute(
            "INSERT INTO inbox_threads (thread_id, participant, read_signature, synced_at) VALUES (?, ?, '*', ?)",
            (thread_id, collapse(participant)[:200], now),
        )
    ordinal = connection.execute(
        "SELECT COALESCE(MAX(ordinal), -1) + 1 FROM inbox_messages WHERE thread_id = ?",
        (thread_id,),
    ).fetchone()[0]
    _insert_message(
        connection,
        thread_id,
        ordinal,
        f"aira:{send_id}",
        {
            "sender": sender,
            "own": True,
            "date": "Today",
            "sent_on": moment.date().isoformat(),
            "time": label,
            "text": body,
        },
        "aira",
        now,
    )
    total = connection.execute(
        "SELECT COUNT(*) FROM inbox_messages WHERE thread_id = ?", (thread_id,)
    ).fetchone()[0]
    connection.execute(
        """UPDATE inbox_threads SET snippet = ?, last_message_label = ?, last_message_at = ?,
            last_sender = 'us', needs_reply = 0, message_count = ?,
            read_signature = CASE WHEN signature = '' THEN '*' ELSE signature END
        WHERE thread_id = ?""",
        (
            snippet_of(body),
            label,
            moment.strftime("%Y-%m-%dT%H:%M"),
            total,
            thread_id,
        ),
    )


def mark_read(connection: sqlite3.Connection, thread_id: str) -> None:
    """Remember that the current activity of a thread has been seen in Aira."""
    connection.execute(
        "UPDATE inbox_threads SET read_signature = CASE WHEN signature = '' THEN '*' ELSE signature END WHERE thread_id = ?",
        (thread_id,),
    )


# ---------------------------------------------------------------------------
# Retention
# ---------------------------------------------------------------------------


def prune(
    connection: sqlite3.Connection,
    now: int,
    *,
    max_age_days: int = RETENTION_DAYS,
) -> dict[str, int]:
    """Drop what Aira no longer needs: idle threads and replies LinkedIn never listed.

    A thread is idle when nothing has listed, fetched or touched it for
    ``max_age_days``. Replies Aira appended are removed after
    ``LOCAL_MESSAGE_TTL_SECONDS`` in every thread, not only in ones that happen to
    be fetched again.
    """
    cutoff = now - max_age_days * 86400
    stale = [
        row["thread_id"]
        for row in connection.execute(
            "SELECT thread_id FROM inbox_threads"
            " WHERE MAX(listed_at, messages_synced_at, synced_at) < ?",
            (cutoff,),
        )
    ]
    for thread_id in stale:
        connection.execute(
            "DELETE FROM inbox_messages WHERE thread_id = ?", (thread_id,)
        )
        connection.execute(
            "DELETE FROM inbox_threads WHERE thread_id = ?", (thread_id,)
        )
    expired = connection.execute(
        "SELECT DISTINCT thread_id FROM inbox_messages WHERE source = 'aira' AND created_at < ?",
        (now - LOCAL_MESSAGE_TTL_SECONDS,),
    ).fetchall()
    connection.execute(
        "DELETE FROM inbox_messages WHERE source = 'aira' AND created_at < ?",
        (now - LOCAL_MESSAGE_TTL_SECONDS,),
    )
    for row in expired:
        connection.execute(
            "UPDATE inbox_threads SET message_count ="
            " (SELECT COUNT(*) FROM inbox_messages WHERE thread_id = ?) WHERE thread_id = ?",
            (row["thread_id"], row["thread_id"]),
        )
    return {"threads": len(stale), "expired_replies": len(expired)}


def purge_all(connection: sqlite3.Connection) -> dict[str, int]:
    """Delete the whole local copy: every thread, message and sync record."""
    threads = connection.execute("SELECT COUNT(*) FROM inbox_threads").fetchone()[0]
    messages = connection.execute("SELECT COUNT(*) FROM inbox_messages").fetchone()[0]
    connection.execute("DELETE FROM inbox_messages")
    connection.execute("DELETE FROM inbox_threads")
    connection.execute("DELETE FROM inbox_sync_runs")
    return {"threads": int(threads), "messages": int(messages)}


# ---------------------------------------------------------------------------
# Sync runs
# ---------------------------------------------------------------------------


def start_sync_run(connection: sqlite3.Connection, trigger: str, now: int) -> int:
    # Only one sync runs at a time, so an unfinished row is a crashed run.
    connection.execute(
        "UPDATE inbox_sync_runs SET status = 'interrupted', finished_at = ? WHERE finished_at IS NULL",
        (now,),
    )
    cursor = connection.execute(
        "INSERT INTO inbox_sync_runs (trigger, started_at) VALUES (?, ?)",
        (trigger[:20], now),
    )
    connection.execute(
        "DELETE FROM inbox_sync_runs WHERE run_id NOT IN (SELECT run_id FROM inbox_sync_runs ORDER BY run_id DESC LIMIT 50)"
    )
    return int(cursor.lastrowid or 0)


def update_sync_run(
    connection: sqlite3.Connection,
    run_id: int,
    *,
    phase: str | None = None,
    total: int | None = None,
    done: int | None = None,
    failed: int | None = None,
) -> None:
    fields = {
        "phase": phase,
        "total": total,
        "done": done,
        "failed": failed,
    }
    chosen = {name: value for name, value in fields.items() if value is not None}
    if not chosen:
        return
    assignments = ", ".join(f"{name} = ?" for name in chosen)
    connection.execute(
        f"UPDATE inbox_sync_runs SET {assignments} WHERE run_id = ?",
        (*chosen.values(), run_id),
    )


def finish_sync_run(
    connection: sqlite3.Connection, run_id: int, *, status: str, error: str, now: int
) -> None:
    connection.execute(
        "UPDATE inbox_sync_runs SET status = ?, error = ?, phase = 'done', finished_at = ? WHERE run_id = ?",
        (status, error[:400], now, run_id),
    )


def latest_sync_run(connection: sqlite3.Connection) -> dict[str, Any] | None:
    row = connection.execute(
        "SELECT * FROM inbox_sync_runs ORDER BY run_id DESC LIMIT 1"
    ).fetchone()
    return dict(row) if row else None
