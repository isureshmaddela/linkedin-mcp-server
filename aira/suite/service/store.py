"""Service tables (prefix ``sv_``), settings and plain row access."""

from __future__ import annotations

import sqlite3
from typing import Any

from aira.suite import kit

APP = "service"

PRIORITIES = ("low", "normal", "high", "urgent")
STATUSES = ("new", "open", "waiting", "solved", "closed")
CHANNELS = ("web", "chat", "email", "phone", "manual")
OPEN_STATUSES = ("new", "open", "waiting")

SCHEMA = (
    """CREATE TABLE IF NOT EXISTS sv_cases (
        id TEXT PRIMARY KEY,
        number INTEGER NOT NULL UNIQUE,
        subject TEXT NOT NULL,
        description TEXT NOT NULL DEFAULT '',
        customer_name TEXT NOT NULL DEFAULT '',
        customer_email TEXT NOT NULL DEFAULT '',
        customer_phone TEXT NOT NULL DEFAULT '',
        channel TEXT NOT NULL DEFAULT 'manual',
        priority TEXT NOT NULL DEFAULT 'normal',
        status TEXT NOT NULL DEFAULT 'new',
        assignee TEXT NOT NULL DEFAULT '',
        tags TEXT NOT NULL DEFAULT '[]',
        first_response_due TEXT,
        resolution_due TEXT,
        first_responded_at TEXT,
        solved_at TEXT,
        closed_at TEXT,
        overdue INTEGER NOT NULL DEFAULT 0,
        merged_into TEXT NOT NULL DEFAULT '',
        csat_score INTEGER,
        csat_comment TEXT NOT NULL DEFAULT '',
        csat_at TEXT,
        csat_requested INTEGER NOT NULL DEFAULT 0,
        last_customer_at TEXT,
        last_agent_at TEXT,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL
    )""",
    "CREATE INDEX IF NOT EXISTS sv_cases_status ON sv_cases (status, updated_at)",
    """CREATE TABLE IF NOT EXISTS sv_messages (
        id TEXT PRIMARY KEY,
        case_id TEXT NOT NULL,
        author_kind TEXT NOT NULL,
        author_name TEXT NOT NULL DEFAULT '',
        body TEXT NOT NULL,
        internal INTEGER NOT NULL DEFAULT 0,
        attachments TEXT NOT NULL DEFAULT '[]',
        created_at TEXT NOT NULL
    )""",
    "CREATE INDEX IF NOT EXISTS sv_messages_case ON sv_messages (case_id, created_at)",
    """CREATE TABLE IF NOT EXISTS sv_macros (
        id TEXT PRIMARY KEY,
        title TEXT NOT NULL,
        body TEXT NOT NULL,
        uses INTEGER NOT NULL DEFAULT 0,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL
    )""",
    """CREATE TABLE IF NOT EXISTS sv_categories (
        id TEXT PRIMARY KEY,
        name TEXT NOT NULL,
        position INTEGER NOT NULL DEFAULT 0,
        created_at TEXT NOT NULL
    )""",
    """CREATE TABLE IF NOT EXISTS sv_articles (
        id TEXT PRIMARY KEY,
        category_id TEXT NOT NULL DEFAULT '',
        title TEXT NOT NULL,
        body TEXT NOT NULL DEFAULT '',
        status TEXT NOT NULL DEFAULT 'draft',
        views INTEGER NOT NULL DEFAULT 0,
        helpful_yes INTEGER NOT NULL DEFAULT 0,
        helpful_no INTEGER NOT NULL DEFAULT 0,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL
    )""",
    """CREATE TABLE IF NOT EXISTS sv_article_votes (
        article_id TEXT NOT NULL,
        client TEXT NOT NULL,
        helpful INTEGER NOT NULL,
        at TEXT NOT NULL,
        PRIMARY KEY (article_id, client)
    )""",
    """CREATE TABLE IF NOT EXISTS sv_chats (
        id TEXT PRIMARY KEY,
        secret_hash TEXT NOT NULL,
        visitor_name TEXT NOT NULL DEFAULT '',
        visitor_email TEXT NOT NULL DEFAULT '',
        status TEXT NOT NULL DEFAULT 'waiting',
        assignee TEXT NOT NULL DEFAULT '',
        case_id TEXT NOT NULL DEFAULT '',
        last_visitor_at TEXT,
        last_agent_at TEXT,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL
    )""",
    """CREATE TABLE IF NOT EXISTS sv_chat_messages (
        id TEXT PRIMARY KEY,
        chat_id TEXT NOT NULL,
        author_kind TEXT NOT NULL,
        author_name TEXT NOT NULL DEFAULT '',
        body TEXT NOT NULL,
        created_at TEXT NOT NULL
    )""",
    "CREATE INDEX IF NOT EXISTS sv_chat_messages_chat ON sv_chat_messages (chat_id, created_at)",
)


def ensure(connection: sqlite3.Connection) -> None:
    kit.ensure_core(connection)
    kit.ensure(connection, "service.v1", SCHEMA)


# ---------------------------------------------------------------------------
# settings: SLA targets, business hours, live chat
# ---------------------------------------------------------------------------

# Minutes to the first reply and to a solution, by priority.
DEFAULT_SLA: dict[str, dict[str, int]] = {
    "low": {"first": 24 * 60, "resolve": 5 * 24 * 60},
    "normal": {"first": 8 * 60, "resolve": 2 * 24 * 60},
    "high": {"first": 4 * 60, "resolve": 24 * 60},
    "urgent": {"first": 60, "resolve": 8 * 60},
}
DEFAULT_SETTINGS: dict[str, Any] = {
    "sla": DEFAULT_SLA,
    "business_hours": False,  # count SLA time only inside business hours
    "days": [0, 1, 2, 3, 4],  # Monday = 0
    "start": "09:00",
    "end": "18:00",
    "chat_enabled": True,
    "chat_in_hours_only": False,
    "offline_message": "We are away right now. Leave a message and we will reply by email.",
    "auto_close_days": 7,
}


def settings(connection: sqlite3.Connection) -> dict[str, Any]:
    stored = kit.get_setting(connection, APP, "settings", {}) or {}
    merged = {
        **DEFAULT_SETTINGS,
        **{k: v for k, v in stored.items() if k in DEFAULT_SETTINGS},
    }
    sla = {p: {**DEFAULT_SLA[p], **(merged["sla"].get(p) or {})} for p in PRIORITIES}
    merged["sla"] = sla
    return merged


def _hhmm(value: Any, name: str) -> str:
    text = kit.text(value, name, max_len=5, required=True)
    parts = text.split(":")
    if len(parts) != 2 or not all(part.isdigit() for part in parts):
        raise ValueError(f"{name[:1].upper()}{name[1:]} must look like 09:00.")
    hour, minute = int(parts[0]), int(parts[1])
    if not (0 <= hour <= 23 and 0 <= minute <= 59):
        raise ValueError(f"{name[:1].upper()}{name[1:]} must be a time of day.")
    return f"{hour:02d}:{minute:02d}"


def save_settings(
    connection: sqlite3.Connection, raw: dict[str, Any]
) -> dict[str, Any]:
    current = settings(connection)
    merged = {**current, **{k: v for k, v in raw.items() if k in DEFAULT_SETTINGS}}
    given_sla = raw.get("sla")
    sla_raw: dict[str, Any] = given_sla if isinstance(given_sla, dict) else {}
    sla = {}
    for priority in PRIORITIES:
        entry = sla_raw.get(priority)
        given: dict[str, Any] = entry if isinstance(entry, dict) else {}
        first = kit.integer(
            given.get("first", current["sla"][priority]["first"]),
            f"the {priority} first reply target (minutes)",
            minimum=5,
            maximum=60 * 24 * 30,
        )
        resolve = kit.integer(
            given.get("resolve", current["sla"][priority]["resolve"]),
            f"the {priority} solution target (minutes)",
            minimum=5,
            maximum=60 * 24 * 90,
        )
        if resolve < first:
            raise ValueError(
                f"The {priority} solution target cannot be shorter than its first reply target."
            )
        sla[priority] = {"first": first, "resolve": resolve}
    days = merged.get("days")
    if not isinstance(days, list) or not days:
        raise ValueError("Choose at least one working day.")
    clean_days = sorted(
        {kit.integer(day, "a working day", minimum=0, maximum=6) for day in days}
    )
    start = _hhmm(merged["start"], "the opening time")
    end = _hhmm(merged["end"], "the closing time")
    if end <= start:
        raise ValueError("The closing time must be after the opening time.")
    clean = {
        "sla": sla,
        "business_hours": kit.flag(merged["business_hours"]),
        "days": clean_days,
        "start": start,
        "end": end,
        "chat_enabled": kit.flag(merged["chat_enabled"]),
        "chat_in_hours_only": kit.flag(merged["chat_in_hours_only"]),
        "offline_message": kit.text(
            merged["offline_message"], "the away message", max_len=300, multiline=True
        )
        or DEFAULT_SETTINGS["offline_message"],
        "auto_close_days": kit.integer(
            merged["auto_close_days"],
            "the auto-close delay (days)",
            minimum=1,
            maximum=60,
        ),
    }
    kit.set_setting(connection, APP, "settings", clean)
    connection.commit()
    return clean


# ---------------------------------------------------------------------------
# rows
# ---------------------------------------------------------------------------


def case_row(row: dict[str, Any] | None) -> dict[str, Any] | None:
    if not row:
        return None
    row["tags"] = kit.loads(row.get("tags"), []) or []
    row["overdue"] = bool(row.get("overdue"))
    row["csat_requested"] = bool(row.get("csat_requested"))
    row["reference"] = f"#{row['number']}"
    return row


def get_case(connection: sqlite3.Connection, case_id: str) -> dict[str, Any]:
    found = case_row(
        kit.fetch_one(connection, "SELECT * FROM sv_cases WHERE id = ?", (case_id,))
    )
    if not found:
        raise LookupError("That case was not found.")
    return found


def messages(
    connection: sqlite3.Connection, case_id: str, *, public_only: bool = False
) -> list[dict[str, Any]]:
    where = "case_id = ?" + (" AND internal = 0" if public_only else "")
    rows = kit.fetch_all(
        connection,
        f"SELECT * FROM sv_messages WHERE {where} ORDER BY created_at, rowid",
        (case_id,),  # noqa: S608
    )
    for row in rows:
        row["internal"] = bool(row["internal"])
        row["attachments"] = kit.loads(row.get("attachments"), []) or []
    return rows


def next_number(connection: sqlite3.Connection) -> int:
    row = connection.execute("SELECT MAX(number) FROM sv_cases").fetchone()
    return max(1000, int(row[0] or 1000)) + 1


def get_article(connection: sqlite3.Connection, article_id: str) -> dict[str, Any]:
    found = kit.fetch_one(
        connection, "SELECT * FROM sv_articles WHERE id = ?", (article_id,)
    )
    if not found:
        raise LookupError("That article was not found.")
    return found


def get_chat(connection: sqlite3.Connection, chat_id: str) -> dict[str, Any]:
    found = kit.fetch_one(connection, "SELECT * FROM sv_chats WHERE id = ?", (chat_id,))
    if not found:
        raise LookupError("That chat was not found.")
    return found


def get_macro(connection: sqlite3.Connection, macro_id: str) -> dict[str, Any]:
    found = kit.fetch_one(
        connection, "SELECT * FROM sv_macros WHERE id = ?", (macro_id,)
    )
    if not found:
        raise LookupError("That saved reply was not found.")
    return found
