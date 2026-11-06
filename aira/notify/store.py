"""Tables of the notification centre, in the database of the account (or hosted space) the person uses:
their notifications, their choices per category, and their browsers' push subscriptions.

A person is a key: ``p:<profile id>`` (a household profile), ``m:<member id>`` (a hosted member) or ``owner``
(the account itself, for Careers before the household has profiles). ``owner`` and the household owner's
profile are the same person (:func:`canonical`).
"""

from __future__ import annotations

import sqlite3
from collections.abc import Iterable, Mapping
from datetime import datetime, timedelta
from typing import Any

from aira.suite import kit

CATEGORIES: dict[str, dict[str, str]] = {
    "careers": {
        "label": "Careers follow-ups",
        "hint": "Follow-ups that are due, interviews about to start.",
        "app": "careers",
    },
    "medicine": {
        "label": "Medicine reminders",
        "hint": "A dose that is due, and missed doses of people you care for (those by email too). "
        "Never names the medicine outside the app.",
        "app": "care",
    },
    "bookings": {
        "label": "Bookings",
        "hint": "Appointments you are about to give.",
        "app": "bookings",
    },
    "helpdesk": {
        "label": "Helpdesk",
        "hint": "Tickets that need you.",
        "app": "service",
    },
    "stocks": {
        "label": "Stock alerts",
        "hint": "Price alerts you set.",
        "app": "stocks",
    },
    "messages": {
        "label": "Messages",
        "hint": "New chat and Dating messages and matches.",
        "app": "social",
    },
}
URGENCIES = ("low", "normal", "high")
KEEP_DAYS = 90
KEEP_PER_PERSON = 500

SCHEMA = (
    """CREATE TABLE IF NOT EXISTS nt_items (
        id TEXT PRIMARY KEY,
        person TEXT NOT NULL,
        category TEXT NOT NULL,
        title TEXT NOT NULL,
        body TEXT NOT NULL DEFAULT '',
        detail TEXT NOT NULL DEFAULT '',
        url TEXT NOT NULL DEFAULT '',
        urgency TEXT NOT NULL DEFAULT 'normal',
        dedupe TEXT NOT NULL DEFAULT '',
        collapse TEXT NOT NULL DEFAULT '',
        count INTEGER NOT NULL DEFAULT 1,
        held INTEGER NOT NULL DEFAULT 0,
        created_at TEXT NOT NULL,
        read_at TEXT
    )""",
    "CREATE INDEX IF NOT EXISTS nt_items_person ON nt_items (person, read_at, created_at)",
    "CREATE UNIQUE INDEX IF NOT EXISTS nt_items_once ON nt_items (person, dedupe) WHERE dedupe != ''",
    """CREATE TABLE IF NOT EXISTS nt_prefs (
        person TEXT PRIMARY KEY,
        prefs TEXT NOT NULL DEFAULT '{}',
        updated_at TEXT NOT NULL
    )""",
    """CREATE TABLE IF NOT EXISTS nt_push_subs (
        id TEXT PRIMARY KEY,
        person TEXT NOT NULL,
        endpoint TEXT NOT NULL,
        p256dh TEXT NOT NULL,
        auth TEXT NOT NULL,
        device TEXT NOT NULL DEFAULT '',
        created_at TEXT NOT NULL,
        last_ok_at TEXT,
        UNIQUE (person, endpoint)
    )""",
    "CREATE INDEX IF NOT EXISTS nt_push_subs_person ON nt_push_subs (person)",
)


def ensure(connection: sqlite3.Connection) -> None:
    kit.ensure(connection, "notify.v1", SCHEMA)


# ---------------------------------------------------------------------------
# people
# ---------------------------------------------------------------------------


def person_key(person: Mapping[str, Any] | str | None) -> str:
    """The key of a household profile, a hosted member's profile, or ``owner`` for the account (Careers)."""
    if isinstance(person, str):
        return person or "owner"
    if not person:
        return "owner"
    if person.get("member_ref"):
        return f"m:{person['member_ref']}"
    if person.get("id") and "age_group" in person:
        return f"p:{person['id']}"
    return "owner"


def _has_profiles(connection: sqlite3.Connection) -> bool:
    return _table(connection, "life_profiles")


def profile_for(connection: sqlite3.Connection, key: str) -> dict[str, Any] | None:
    if not _has_profiles(connection):
        return None
    if key.startswith("p:"):
        row = kit.fetch_one(
            connection,
            "SELECT * FROM life_profiles WHERE id = ? AND archived_at IS NULL",
            (key[2:],),
        )
    elif key.startswith("m:"):
        row = kit.fetch_one(
            connection,
            "SELECT * FROM life_profiles WHERE member_ref = ? AND archived_at IS NULL",
            (key[2:],),
        )
    elif key == "owner":
        row = kit.fetch_one(
            connection,
            "SELECT * FROM life_profiles WHERE owner = 1 AND archived_at IS NULL ORDER BY created_at LIMIT 1",
        )
    else:
        return None
    return dict(row) if row else None


def canonical(connection: sqlite3.Connection, key: str) -> str:
    """``owner`` is the household owner's profile once there is one."""
    if key != "owner":
        return key
    owner = profile_for(connection, "owner")
    return person_key(owner) if owner else "owner"


def keys_of(
    connection: sqlite3.Connection, profile: Mapping[str, Any] | None
) -> list[str]:
    """Every key a person's notifications may be stored under (the owner also reads ``owner``)."""
    key = person_key(profile)
    out = [key]
    if key != "owner" and profile and profile.get("owner"):
        out.append("owner")
    return out


# ---------------------------------------------------------------------------
# choices
# ---------------------------------------------------------------------------


def default_prefs() -> dict[str, Any]:
    return {
        # Email is the fallback for what matters away from a browser: a missed dose of someone you care for.
        "categories": {
            name: {"push": True, "email": name == "medicine"} for name in CATEGORIES
        },
        "quiet": {"on": False, "start": "22:00", "end": "07:00"},
        "timezone": "",
        "email": "",
    }


def _hhmm(value: Any, default: str) -> str:
    text = str(value or "").strip()
    try:
        parsed = datetime.strptime(text, "%H:%M")
    except ValueError:
        return default
    return parsed.strftime("%H:%M")


def clean_prefs(
    raw: Mapping[str, Any], current: Mapping[str, Any] | None = None
) -> dict[str, Any]:
    base = merge_prefs(current or {})
    categories = raw.get("categories")
    if isinstance(categories, Mapping):
        for name, choice in categories.items():
            if name not in CATEGORIES or not isinstance(choice, Mapping):
                continue
            for channel in ("push", "email"):
                if channel in choice:
                    base["categories"][name][channel] = kit.flag(choice[channel])
    quiet = raw.get("quiet")
    if isinstance(quiet, Mapping):
        if "on" in quiet:
            base["quiet"]["on"] = kit.flag(quiet["on"])
        base["quiet"]["start"] = _hhmm(quiet.get("start"), base["quiet"]["start"])
        base["quiet"]["end"] = _hhmm(quiet.get("end"), base["quiet"]["end"])
    if "timezone" in raw:
        zone = kit.text(raw.get("timezone"), "the time zone", max_len=64)
        if zone:
            from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

            try:
                ZoneInfo(zone)
            except (ZoneInfoNotFoundError, ValueError):
                raise ValueError("Choose a time zone such as Asia/Kolkata.") from None
        base["timezone"] = zone
    if "email" in raw:
        base["email"] = kit.email(raw.get("email"), "the email address")
    return base


def merge_prefs(stored: Mapping[str, Any]) -> dict[str, Any]:
    out = default_prefs()
    for name, choice in (stored.get("categories") or {}).items():
        if name in out["categories"] and isinstance(choice, Mapping):
            for channel in ("push", "email"):
                if channel in choice:
                    out["categories"][name][channel] = bool(choice[channel])
    quiet = stored.get("quiet") or {}
    if isinstance(quiet, Mapping):
        out["quiet"]["on"] = bool(quiet.get("on", False))
        out["quiet"]["start"] = _hhmm(quiet.get("start"), "22:00")
        out["quiet"]["end"] = _hhmm(quiet.get("end"), "07:00")
    out["timezone"] = str(stored.get("timezone") or "")
    out["email"] = str(stored.get("email") or "")
    return out


def prefs(connection: sqlite3.Connection, key: str) -> dict[str, Any]:
    ensure(connection)
    row = kit.fetch_one(
        connection, "SELECT prefs FROM nt_prefs WHERE person = ?", (key,)
    )
    return merge_prefs(kit.loads(row["prefs"], {}) if row else {})


def save_prefs(
    connection: sqlite3.Connection, key: str, raw: Mapping[str, Any]
) -> dict[str, Any]:
    ensure(connection)
    clean = clean_prefs(raw, prefs(connection, key))
    connection.execute(
        """INSERT INTO nt_prefs (person, prefs, updated_at) VALUES (?, ?, ?)
        ON CONFLICT(person) DO UPDATE SET prefs = excluded.prefs, updated_at = excluded.updated_at""",
        (key, kit.dumps(clean), kit.now()),
    )
    connection.commit()
    return clean


def _table(connection: sqlite3.Connection, name: str) -> bool:
    return (
        connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?", (name,)
        ).fetchone()
        is not None
    )


def zone_name(connection: sqlite3.Connection, choice: Mapping[str, Any]) -> str:
    """The person's own time zone, else the business's (Business hub), else Careers' setting, else UTC."""
    if choice.get("timezone"):
        return str(choice["timezone"])
    try:
        if _table(connection, "suite_settings"):
            row = connection.execute(
                "SELECT value FROM suite_settings WHERE app = 'core' AND key = 'business'"
            ).fetchone()
            zone = (
                str((kit.loads(row[0], {}) or {}).get("timezone") or "") if row else ""
            )
            if zone and zone != "UTC":
                return zone
        if _table(connection, "settings"):
            row = connection.execute(
                "SELECT value FROM settings WHERE key = 'timezone'"
            ).fetchone()
            if row and row[0]:
                return str(row[0])
    except sqlite3.Error:
        pass
    return "UTC"


def in_quiet(choice: Mapping[str, Any], moment: datetime, zone: str) -> bool:
    quiet = choice.get("quiet") or {}
    if not quiet.get("on"):
        return False
    from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

    try:
        local = moment.astimezone(ZoneInfo(zone))
    except (ZoneInfoNotFoundError, ValueError):
        local = moment
    now = local.strftime("%H:%M")
    start, end = str(quiet.get("start") or "22:00"), str(quiet.get("end") or "07:00")
    if start == end:
        return False
    if start < end:
        return start <= now < end
    return now >= start or now < end


# ---------------------------------------------------------------------------
# the notification centre
# ---------------------------------------------------------------------------


def _public(row: Mapping[str, Any]) -> dict[str, Any]:
    out = dict(row)
    out["read"] = bool(out.get("read_at"))
    out["held"] = bool(out.get("held"))
    return out


def add_item(
    connection: sqlite3.Connection,
    key: str,
    *,
    category: str,
    title: str,
    body: str,
    detail: str,
    url: str,
    urgency: str,
    dedupe: str,
    collapse: str,
    held: bool,
) -> tuple[dict[str, Any] | None, bool]:
    """Store one notification; ``(item, new)``. A known ``dedupe`` stores nothing (``(None, False)``); an unread
    item with the same ``collapse`` key is brought up to date instead (``new`` False)."""
    now = kit.now()
    if dedupe and kit.fetch_one(
        connection,
        "SELECT 1 AS x FROM nt_items WHERE person = ? AND dedupe = ?",
        (key, dedupe),
    ):
        return None, False
    if collapse:
        found = kit.fetch_one(
            connection,
            "SELECT * FROM nt_items WHERE person = ? AND collapse = ? AND read_at IS NULL ORDER BY created_at DESC LIMIT 1",
            (key, collapse),
        )
        if found:
            connection.execute(
                "UPDATE nt_items SET title = ?, body = ?, detail = ?, url = ?, urgency = ?, count = count + 1, held = ?, created_at = ? WHERE id = ?",
                (title, body, detail, url, urgency, 1 if held else 0, now, found["id"]),
            )
            row = kit.fetch_one(
                connection, "SELECT * FROM nt_items WHERE id = ?", (found["id"],)
            )
            return (_public(row) if row else None), False
    item = {
        "id": kit.new_id(),
        "person": key,
        "category": category,
        "title": title,
        "body": body,
        "detail": detail,
        "url": url,
        "urgency": urgency,
        "dedupe": dedupe,
        "collapse": collapse,
        "held": 1 if held else 0,
        "created_at": now,
    }
    try:
        connection.execute(
            """INSERT INTO nt_items (id, person, category, title, body, detail, url, urgency, dedupe, collapse, held, created_at)
            VALUES (:id, :person, :category, :title, :body, :detail, :url, :urgency, :dedupe, :collapse, :held, :created_at)""",
            item,
        )
    except sqlite3.IntegrityError:
        return None, False  # another thread stored the same reminder a moment ago
    _prune(connection, key)
    return _public({**item, "count": 1, "read_at": None}), True


def _prune(connection: sqlite3.Connection, key: str) -> None:
    cutoff = kit.iso(kit.utcnow() - timedelta(days=KEEP_DAYS))
    connection.execute(
        "DELETE FROM nt_items WHERE person = ? AND created_at < ?", (key, cutoff)
    )
    connection.execute(
        """DELETE FROM nt_items WHERE person = ? AND id NOT IN (
            SELECT id FROM nt_items WHERE person = ? ORDER BY created_at DESC LIMIT ?)""",
        (key, key, KEEP_PER_PERSON),
    )


def _marks(keys: Iterable[str]) -> tuple[str, list[str]]:
    listed = list(keys)
    return ",".join("?" for _ in listed), listed


def items(
    connection: sqlite3.Connection,
    keys: Iterable[str],
    *,
    unread_only: bool = False,
    limit: int = 50,
) -> list[dict[str, Any]]:
    ensure(connection)
    marks, params = _marks(keys)
    where = f"person IN ({marks})" + (" AND read_at IS NULL" if unread_only else "")
    rows = kit.fetch_all(
        connection,
        f"SELECT * FROM nt_items WHERE {where} ORDER BY created_at DESC, id DESC LIMIT ?",  # noqa: S608
        (*params, max(1, min(limit, 200))),
    )
    return [_public(row) for row in rows]


def unread(connection: sqlite3.Connection, keys: Iterable[str]) -> int:
    ensure(connection)
    marks, params = _marks(keys)
    return kit.count(
        connection,
        f"SELECT COUNT(*) FROM nt_items WHERE person IN ({marks}) AND read_at IS NULL",  # noqa: S608
        params,
    )


def mark_read(
    connection: sqlite3.Connection,
    keys: Iterable[str],
    ids: Iterable[str] | None = None,
) -> int:
    ensure(connection)
    marks, params = _marks(keys)
    if ids is None:
        changed = connection.execute(
            f"UPDATE nt_items SET read_at = ? WHERE person IN ({marks}) AND read_at IS NULL",  # noqa: S608
            (kit.now(), *params),
        ).rowcount
    else:
        wanted = [str(item) for item in ids][:500]
        if not wanted:
            return 0
        id_marks = ",".join("?" for _ in wanted)
        changed = connection.execute(
            f"UPDATE nt_items SET read_at = ? WHERE person IN ({marks}) AND id IN ({id_marks}) AND read_at IS NULL",  # noqa: S608
            (kit.now(), *params, *wanted),
        ).rowcount
    connection.commit()
    return changed


# ---------------------------------------------------------------------------
# browsers that receive pushes
# ---------------------------------------------------------------------------

MAX_DEVICES = 10


def subscribe(
    connection: sqlite3.Connection, key: str, raw: Mapping[str, Any]
) -> dict[str, Any]:
    ensure(connection)
    from aira.notify import webpush

    endpoint = str(raw.get("endpoint") or "").strip()
    keys = raw.get("keys") if isinstance(raw.get("keys"), Mapping) else raw
    p256dh = str((keys or {}).get("p256dh") or "").strip()
    auth = str((keys or {}).get("auth") or "").strip()
    if not webpush.endpoint_allowed(endpoint):
        raise ValueError("This browser's push service is not one Monosage sends to.")
    try:
        receiver = webpush.unb64url(p256dh)
        secret = webpush.unb64url(auth)
    except (ValueError, UnicodeError):
        raise ValueError("The browser sent push keys Monosage cannot read.") from None
    if len(receiver) != 65 or receiver[0] != 4 or len(secret) < 16:
        raise ValueError("The browser sent push keys Monosage cannot read.")
    device = kit.text(raw.get("device"), "the device name", max_len=80)
    connection.execute(
        """INSERT INTO nt_push_subs (id, person, endpoint, p256dh, auth, device, created_at) VALUES (?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(person, endpoint) DO UPDATE SET p256dh = excluded.p256dh, auth = excluded.auth, device = excluded.device""",
        (kit.new_id(), key, endpoint, p256dh, auth, device, kit.now()),
    )
    # the oldest go first when one person has many browsers
    connection.execute(
        """DELETE FROM nt_push_subs WHERE person = ? AND id NOT IN (
            SELECT id FROM nt_push_subs WHERE person = ? ORDER BY created_at DESC LIMIT ?)""",
        (key, key, MAX_DEVICES),
    )
    connection.commit()
    row = kit.fetch_one(
        connection,
        "SELECT id, device, created_at FROM nt_push_subs WHERE person = ? AND endpoint = ?",
        (key, endpoint),
    )
    return dict(row) if row else {}


def unsubscribe(
    connection: sqlite3.Connection, keys: Iterable[str], endpoint: str
) -> int:
    ensure(connection)
    marks, params = _marks(keys)
    removed = connection.execute(
        f"DELETE FROM nt_push_subs WHERE person IN ({marks}) AND endpoint = ?",  # noqa: S608
        (*params, str(endpoint or "")),
    ).rowcount
    connection.commit()
    return removed


def subscriptions(
    connection: sqlite3.Connection, keys: Iterable[str]
) -> list[dict[str, Any]]:
    ensure(connection)
    marks, params = _marks(keys)
    return kit.fetch_all(
        connection,
        f"SELECT * FROM nt_push_subs WHERE person IN ({marks}) ORDER BY created_at",  # noqa: S608
        params,
    )


def forget_subscription(connection: sqlite3.Connection, sub_id: str) -> None:
    connection.execute("DELETE FROM nt_push_subs WHERE id = ?", (sub_id,))
    connection.commit()
