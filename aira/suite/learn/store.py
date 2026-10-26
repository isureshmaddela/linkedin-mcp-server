"""Learn's tables (``ln_*``) and plain reads and writes. Rules live in :mod:`aira.suite.learn.service`."""

from __future__ import annotations

import sqlite3
from typing import Any

from aira.suite import kit, life

APP = "learn"

SCHEMA = (
    """CREATE TABLE IF NOT EXISTS ln_people (
        profile_id TEXT PRIMARY KEY,
        goal_kind TEXT NOT NULL DEFAULT 'cards',
        goal_value INTEGER NOT NULL DEFAULT 10,
        new_per_day INTEGER NOT NULL DEFAULT 10,
        reviews_per_day INTEGER NOT NULL DEFAULT 100,
        reading_counts INTEGER NOT NULL DEFAULT 1,
        xp INTEGER NOT NULL DEFAULT 0,
        badges TEXT NOT NULL DEFAULT '{}',
        freeze_weeks TEXT NOT NULL DEFAULT '[]',
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL
    )""",
    """CREATE TABLE IF NOT EXISTS ln_decks (
        id TEXT PRIMARY KEY,
        owner_id TEXT NOT NULL,
        title TEXT NOT NULL,
        description TEXT NOT NULL DEFAULT '',
        emoji TEXT NOT NULL DEFAULT '📚',
        shared INTEGER NOT NULL DEFAULT 0,
        source TEXT NOT NULL DEFAULT 'manual',
        age_group TEXT NOT NULL DEFAULT 'all',
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL,
        archived_at TEXT
    )""",
    "CREATE INDEX IF NOT EXISTS ln_decks_owner ON ln_decks (owner_id)",
    """CREATE TABLE IF NOT EXISTS ln_cards (
        id TEXT PRIMARY KEY,
        deck_id TEXT NOT NULL,
        front TEXT NOT NULL,
        back TEXT NOT NULL,
        hint TEXT NOT NULL DEFAULT '',
        example TEXT NOT NULL DEFAULT '',
        position INTEGER NOT NULL DEFAULT 0,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL
    )""",
    "CREATE INDEX IF NOT EXISTS ln_cards_deck ON ln_cards (deck_id, position)",
    """CREATE TABLE IF NOT EXISTS ln_progress (
        profile_id TEXT NOT NULL,
        card_id TEXT NOT NULL,
        deck_id TEXT NOT NULL,
        ease REAL NOT NULL DEFAULT 2.5,
        interval_days INTEGER NOT NULL DEFAULT 0,
        reps INTEGER NOT NULL DEFAULT 0,
        lapses INTEGER NOT NULL DEFAULT 0,
        due TEXT NOT NULL,
        last_reviewed_at TEXT,
        PRIMARY KEY (profile_id, card_id)
    )""",
    "CREATE INDEX IF NOT EXISTS ln_progress_due ON ln_progress (profile_id, due)",
    """CREATE TABLE IF NOT EXISTS ln_reviews (
        id TEXT PRIMARY KEY,
        profile_id TEXT NOT NULL,
        card_id TEXT NOT NULL,
        deck_id TEXT NOT NULL,
        grade TEXT NOT NULL,
        was_new INTEGER NOT NULL DEFAULT 0,
        seconds INTEGER NOT NULL DEFAULT 0,
        day TEXT NOT NULL,
        at TEXT NOT NULL
    )""",
    "CREATE INDEX IF NOT EXISTS ln_reviews_day ON ln_reviews (profile_id, day)",
    """CREATE TABLE IF NOT EXISTS ln_days (
        profile_id TEXT NOT NULL,
        day TEXT NOT NULL,
        cards INTEGER NOT NULL DEFAULT 0,
        new_cards INTEGER NOT NULL DEFAULT 0,
        seconds INTEGER NOT NULL DEFAULT 0,
        reading_minutes INTEGER NOT NULL DEFAULT 0,
        xp INTEGER NOT NULL DEFAULT 0,
        goal_met INTEGER NOT NULL DEFAULT 0,
        frozen INTEGER NOT NULL DEFAULT 0,
        PRIMARY KEY (profile_id, day)
    )""",
    """CREATE TABLE IF NOT EXISTS ln_quizzes (
        id TEXT PRIMARY KEY,
        profile_id TEXT NOT NULL,
        kind TEXT NOT NULL,
        deck_id TEXT NOT NULL DEFAULT '',
        level TEXT NOT NULL DEFAULT '',
        mode TEXT NOT NULL DEFAULT 'relaxed',
        title TEXT NOT NULL DEFAULT '',
        questions TEXT NOT NULL,
        answers TEXT NOT NULL DEFAULT '[]',
        score INTEGER NOT NULL DEFAULT 0,
        total INTEGER NOT NULL DEFAULT 0,
        time_limit INTEGER NOT NULL DEFAULT 0,
        late INTEGER NOT NULL DEFAULT 0,
        status TEXT NOT NULL DEFAULT 'open',
        created_at TEXT NOT NULL,
        finished_at TEXT
    )""",
    "CREATE INDEX IF NOT EXISTS ln_quizzes_person ON ln_quizzes (profile_id, created_at)",
    """CREATE TABLE IF NOT EXISTS ln_books (
        id TEXT PRIMARY KEY,
        profile_id TEXT NOT NULL,
        title TEXT NOT NULL,
        author TEXT NOT NULL DEFAULT '',
        status TEXT NOT NULL DEFAULT 'reading',
        created_at TEXT NOT NULL,
        finished_at TEXT
    )""",
    """CREATE TABLE IF NOT EXISTS ln_reading (
        id TEXT PRIMARY KEY,
        profile_id TEXT NOT NULL,
        book_id TEXT NOT NULL DEFAULT '',
        day TEXT NOT NULL,
        minutes INTEGER NOT NULL,
        at TEXT NOT NULL
    )""",
    "CREATE INDEX IF NOT EXISTS ln_reading_person ON ln_reading (profile_id, day)",
)

GOAL_KINDS = ("cards", "minutes")
AGE_TAGS = ("all", "child", "teen", "adult", "senior")


def ensure(connection: sqlite3.Connection) -> None:
    life.ensure(connection)
    kit.ensure(connection, "learn.v1", SCHEMA)


# ---------------------------------------------------------------------------
# a person's settings
# ---------------------------------------------------------------------------


def person(connection: sqlite3.Connection, profile: dict[str, Any]) -> dict[str, Any]:
    """The person's Learn settings (made on first use; a child starts with a smaller daily goal)."""
    row = kit.fetch_one(
        connection, "SELECT * FROM ln_people WHERE profile_id = ?", (profile["id"],)
    )
    if row is None:
        child = profile.get("age_group") == "child"
        connection.execute(
            """INSERT OR IGNORE INTO ln_people (profile_id, goal_kind, goal_value, new_per_day, reviews_per_day,
            created_at, updated_at) VALUES (?, 'cards', ?, ?, 100, ?, ?)""",
            (
                profile["id"],
                10 if child else 15,
                8 if child else 10,
                kit.now(),
                kit.now(),
            ),
        )
        connection.commit()
        row = kit.fetch_one(
            connection, "SELECT * FROM ln_people WHERE profile_id = ?", (profile["id"],)
        )
    assert row is not None
    row["badges"] = kit.loads(row["badges"], {}) or {}
    row["freeze_weeks"] = kit.loads(row["freeze_weeks"], []) or []
    row["reading_counts"] = bool(row["reading_counts"])
    return row


def save_settings(
    connection: sqlite3.Connection, profile: dict[str, Any], raw: dict[str, Any]
) -> dict[str, Any]:
    current = person(connection, profile)
    values = {
        "goal_kind": kit.choice(
            raw.get("goal_kind", current["goal_kind"]), "the goal", GOAL_KINDS
        ),
        "goal_value": kit.integer(
            raw.get("goal_value", current["goal_value"]),
            "the daily goal",
            minimum=1,
            maximum=500,
        ),
        "new_per_day": kit.integer(
            raw.get("new_per_day", current["new_per_day"]),
            "new cards a day",
            minimum=0,
            maximum=200,
        ),
        "reviews_per_day": kit.integer(
            raw.get("reviews_per_day", current["reviews_per_day"]),
            "reviews a day",
            minimum=1,
            maximum=1000,
        ),
        "reading_counts": 1
        if kit.flag(raw.get("reading_counts", current["reading_counts"]))
        else 0,
        "updated_at": kit.now(),
    }
    connection.execute(
        f"UPDATE ln_people SET {', '.join(f'{k} = ?' for k in values)} WHERE profile_id = ?",  # noqa: S608 - fixed keys
        (*values.values(), profile["id"]),
    )
    connection.commit()
    return person(connection, profile)


def add_xp(connection: sqlite3.Connection, profile_id: str, day: str, xp: int) -> None:
    if xp <= 0:
        return
    connection.execute(
        "UPDATE ln_people SET xp = xp + ? WHERE profile_id = ?", (xp, profile_id)
    )
    touch_day(connection, profile_id, day)
    connection.execute(
        "UPDATE ln_days SET xp = xp + ? WHERE profile_id = ? AND day = ?",
        (xp, profile_id, day),
    )


def touch_day(connection: sqlite3.Connection, profile_id: str, day: str) -> None:
    connection.execute(
        "INSERT OR IGNORE INTO ln_days (profile_id, day) VALUES (?, ?)",
        (profile_id, day),
    )


def day_row(
    connection: sqlite3.Connection, profile_id: str, day: str
) -> dict[str, Any]:
    return kit.fetch_one(
        connection,
        "SELECT * FROM ln_days WHERE profile_id = ? AND day = ?",
        (profile_id, day),
    ) or {
        "profile_id": profile_id,
        "day": day,
        "cards": 0,
        "new_cards": 0,
        "seconds": 0,
        "reading_minutes": 0,
        "xp": 0,
        "goal_met": 0,
        "frozen": 0,
    }


# ---------------------------------------------------------------------------
# decks and cards
# ---------------------------------------------------------------------------


def deck(connection: sqlite3.Connection, deck_id: str) -> dict[str, Any]:
    row = kit.fetch_one(
        connection,
        "SELECT * FROM ln_decks WHERE id = ? AND archived_at IS NULL",
        (deck_id,),
    )
    if not row:
        raise LookupError("That deck was not found.")
    row["shared"] = bool(row["shared"])
    return row


def visible_deck(
    connection: sqlite3.Connection, profile: dict[str, Any], deck_id: str
) -> dict[str, Any]:
    """A deck this person may study: their own, or one shared with the household."""
    found = deck(connection, deck_id)
    if found["owner_id"] != profile["id"] and not found["shared"]:
        raise LookupError("That deck was not found.")
    return found


def own_deck(
    connection: sqlite3.Connection, profile: dict[str, Any], deck_id: str
) -> dict[str, Any]:
    found = visible_deck(connection, profile, deck_id)
    if found["owner_id"] != profile["id"]:
        raise PermissionError("Only the person who made this deck can change it.")
    return found


def clean_deck(
    raw: dict[str, Any], current: dict[str, Any] | None = None
) -> dict[str, Any]:
    merged = {**(current or {}), **raw}
    return {
        "title": kit.text(merged.get("title"), "a title", max_len=80, required=True),
        "description": kit.text(
            merged.get("description"), "a description", max_len=300
        ),
        "emoji": kit.text(merged.get("emoji") or "📚", "an emoji", max_len=8) or "📚",
        "shared": 1 if kit.flag(merged.get("shared")) else 0,
        "age_group": kit.choice(
            merged.get("age_group"), "the age group", AGE_TAGS, default="all"
        ),
    }


def create_deck(
    connection: sqlite3.Connection,
    owner_id: str,
    raw: dict[str, Any],
    *,
    source: str = "manual",
) -> dict[str, Any]:
    values = clean_deck(raw)
    deck_id = kit.new_id()
    connection.execute(
        """INSERT INTO ln_decks (id, owner_id, title, description, emoji, shared, source, age_group, created_at, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            deck_id,
            owner_id,
            values["title"],
            values["description"],
            values["emoji"],
            values["shared"],
            source[:40],
            values["age_group"],
            kit.now(),
            kit.now(),
        ),
    )
    return deck(connection, deck_id)


def clean_card(
    raw: dict[str, Any], current: dict[str, Any] | None = None
) -> dict[str, Any]:
    merged = {**(current or {}), **raw}
    return {
        "front": kit.text(
            merged.get("front"),
            "the front of the card",
            max_len=300,
            required=True,
            multiline=True,
        ),
        "back": kit.text(
            merged.get("back"),
            "the back of the card",
            max_len=500,
            required=True,
            multiline=True,
        ),
        "hint": kit.text(merged.get("hint"), "a hint", max_len=200),
        "example": kit.text(
            merged.get("example"), "an example", max_len=300, multiline=True
        ),
    }


def add_card(
    connection: sqlite3.Connection, deck_id: str, raw: dict[str, Any]
) -> dict[str, Any]:
    if (
        kit.count(
            connection, "SELECT COUNT(*) FROM ln_cards WHERE deck_id = ?", (deck_id,)
        )
        >= 1000
    ):
        raise ValueError("A deck can hold at most 1000 cards. Start a second deck.")
    values = clean_card(raw)
    position = kit.count(
        connection,
        "SELECT COALESCE(MAX(position), 0) + 1 FROM ln_cards WHERE deck_id = ?",
        (deck_id,),
    )
    card_id = kit.new_id()
    connection.execute(
        """INSERT INTO ln_cards (id, deck_id, front, back, hint, example, position, created_at, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            card_id,
            deck_id,
            values["front"],
            values["back"],
            values["hint"],
            values["example"],
            position,
            kit.now(),
            kit.now(),
        ),
    )
    connection.execute(
        "UPDATE ln_decks SET updated_at = ? WHERE id = ?", (kit.now(), deck_id)
    )
    return card(connection, card_id)


def card(connection: sqlite3.Connection, card_id: str) -> dict[str, Any]:
    row = kit.fetch_one(connection, "SELECT * FROM ln_cards WHERE id = ?", (card_id,))
    if not row:
        raise LookupError("That card was not found.")
    return row


def cards(connection: sqlite3.Connection, deck_id: str) -> list[dict[str, Any]]:
    return kit.fetch_all(
        connection,
        "SELECT * FROM ln_cards WHERE deck_id = ? ORDER BY position, created_at",
        (deck_id,),
    )


def decks_for(
    connection: sqlite3.Connection, profile: dict[str, Any]
) -> list[dict[str, Any]]:
    """This person's decks and the household's shared ones."""
    rows = kit.fetch_all(
        connection,
        """SELECT d.*, (SELECT COUNT(*) FROM ln_cards c WHERE c.deck_id = d.id) AS card_count FROM ln_decks d
        WHERE d.archived_at IS NULL AND (d.owner_id = ? OR d.shared = 1) ORDER BY d.updated_at DESC""",
        (profile["id"],),
    )
    for row in rows:
        row["shared"] = bool(row["shared"])
        row["mine"] = row["owner_id"] == profile["id"]
    return rows
