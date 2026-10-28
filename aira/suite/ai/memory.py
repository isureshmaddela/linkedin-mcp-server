"""Memory (``#/ai?tab=memory``): what a person asked Aira to remember ("I am vegetarian", "My daughter's school
is Oakfield"). Every answer the writing model gives takes it into account, and MemoryAgent reads it out when asked
("What do you remember about me?").

A memory belongs to the person who said it (``owner_id``); nobody else in the household or organization sees it.
"Remember that ..." and "Forget that ..." in the chat are MemoryAgent's actions: they pass its consent gate, show in
Activity and can be undone.
"""

from __future__ import annotations

import re
import sqlite3
from typing import Any

from aira.suite import kit

MAX_MEMORIES = 100
MAX_TEXT = 300
IN_PROMPT = 30  # memories the writing model sees with every question
LIKE_ESCAPE = str.maketrans({"\\": "\\\\", "%": "\\%", "_": "\\_"})


def _view(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": row["id"],
        "text": row["text"],
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
    }


def _clean(value: Any) -> str:
    text = kit.text(value, "what to remember", max_len=MAX_TEXT, required=True)
    return text[:1].upper() + text[1:]


def list_memories(
    connection: sqlite3.Connection, me: dict[str, Any], q: str = ""
) -> list[dict[str, Any]]:
    """The person's memories, newest first; ``q`` finds words in them."""
    sql = "SELECT * FROM ai_memories WHERE owner_id = ?"
    params: list[Any] = [me["id"]]
    words = " ".join(str(q or "").split())[:100]
    if words:
        sql += " AND text LIKE ? ESCAPE '\\'"
        params.append("%" + words.translate(LIKE_ESCAPE) + "%")
    sql += " ORDER BY created_at DESC, rowid DESC LIMIT ?"
    params.append(MAX_MEMORIES)
    return [_view(row) for row in kit.fetch_all(connection, sql, params)]


def _row(
    connection: sqlite3.Connection, me: dict[str, Any], memory_id: str
) -> dict[str, Any]:
    found = kit.fetch_one(
        connection,
        "SELECT * FROM ai_memories WHERE id = ? AND owner_id = ?",
        (memory_id, me["id"]),
    )
    if not found:
        raise LookupError("That memory was not found.")
    return found


def get(
    connection: sqlite3.Connection, me: dict[str, Any], memory_id: str
) -> dict[str, Any]:
    """One of the person's memories (LookupError when it is gone)."""
    return _view(_row(connection, me, memory_id))


def add(
    connection: sqlite3.Connection, me: dict[str, Any], value: Any
) -> dict[str, Any]:
    """Remember something (the same words twice are kept once)."""
    text = _clean(value)
    same = kit.fetch_one(
        connection,
        "SELECT * FROM ai_memories WHERE owner_id = ? AND lower(text) = lower(?)",
        (me["id"], text),
    )
    if same:
        return {**_view(same), "existing": True}
    if (
        kit.count(
            connection,
            "SELECT COUNT(*) FROM ai_memories WHERE owner_id = ?",
            (me["id"],),
        )
        >= MAX_MEMORIES
    ):
        raise ValueError(
            f"Monosage keeps {MAX_MEMORIES} memories for you. Remove one in AI > Memory first."
        )
    stamp = kit.now()
    memory_id = kit.new_id()
    connection.execute(
        "INSERT INTO ai_memories (id, owner_id, text, created_at, updated_at) VALUES (?, ?, ?, ?, ?)",
        (memory_id, me["id"], text, stamp, stamp),
    )
    connection.commit()
    return _view(_row(connection, me, memory_id))


def update(
    connection: sqlite3.Connection,
    me: dict[str, Any],
    memory_id: str,
    raw: dict[str, Any],
) -> dict[str, Any]:
    _row(connection, me, memory_id)
    kit.update_row(
        connection,
        "ai_memories",
        memory_id,
        {"text": _clean(raw.get("text")), "updated_at": kit.now()},
    )
    connection.commit()
    return _view(_row(connection, me, memory_id))


def delete(connection: sqlite3.Connection, me: dict[str, Any], memory_id: str) -> None:
    _row(connection, me, memory_id)
    connection.execute("DELETE FROM ai_memories WHERE id = ?", (memory_id,))
    connection.commit()


def clear(connection: sqlite3.Connection, me: dict[str, Any]) -> int:
    """Forget everything this person asked Aira to remember; returns how many."""
    removed = connection.execute(
        "DELETE FROM ai_memories WHERE owner_id = ?", (me["id"],)
    ).rowcount
    connection.commit()
    return int(removed or 0)


def find(
    connection: sqlite3.Connection, me: dict[str, Any], words: Any
) -> dict[str, Any]:
    """The one memory a person means ("forget that I am vegetarian", "forget vegetarian")."""
    wanted = " ".join(str(words or "").lower().split()).strip(" .!")
    if not wanted:
        raise ValueError("Say what to forget.")
    memories = list_memories(connection, me)
    exact = [m for m in memories if m["text"].lower().strip(" .!") == wanted]
    partial = [
        m
        for m in memories
        if wanted in m["text"].lower() or m["text"].lower().strip(" .!") in wanted
    ]
    # "Forget that I'm vegetarian" for "I am vegetarian": every longer word of what was said is in the memory.
    words_said = [w for w in re.findall(r"[a-z0-9']+", wanted) if len(w) >= 4]
    by_words = [
        m
        for m in memories
        if words_said and all(w in m["text"].lower() for w in words_said)
    ]
    found = exact or partial or by_words
    if not found:
        raise LookupError(f'I do not remember anything about "{words}".')
    if len(found) > 1 and not exact:
        names = "; ".join(f'"{m["text"]}"' for m in found[:4])
        raise ValueError(f"Which one should I forget: {names}?")
    return found[0]


def lines(connection: sqlite3.Connection, me: dict[str, Any]) -> list[str]:
    """The memories for the writing model, newest first."""
    return [m["text"] for m in list_memories(connection, me)[:IN_PROMPT]]
