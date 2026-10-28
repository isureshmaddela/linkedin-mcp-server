"""Quizzes people write themselves: family trivia ("What year did Grandma and Grandpa marry?"), a class quiz, a pub
quiz. The writer sees the answers; everyone else only plays. A quiz is played alone like the built-in games
(checked on the server, scores on its own family board) or used for live trivia and paid quiz nights.

Rules: the owner, or an adult in the household, may change or remove a quiz; a quiz kept private is seen by its
owner only; the right answers never reach anyone but the owner (and the server)."""

from __future__ import annotations

import sqlite3
from typing import Any

from aira.suite import kit, life
from aira.suite.play import games, store
from aira.suite.play.store import APP

DRAFT_MAX = 10


def _row(connection: sqlite3.Connection, quiz_id: str) -> dict[str, Any]:
    return kit.require_row(connection, "pl_quizzes", quiz_id, "quiz")


def _can_see(me: dict[str, Any], row: dict[str, Any]) -> bool:
    return row["owner_id"] == me["id"] or bool(row["shared"])


def _can_change(me: dict[str, Any], row: dict[str, Any]) -> bool:
    return row["owner_id"] == me["id"] or (bool(row["shared"]) and life.is_adult(me))


def visible(
    connection: sqlite3.Connection, me: dict[str, Any], quiz_id: str
) -> dict[str, Any]:
    """The quiz if this person may play it (their own, or one shared with the family); otherwise 404."""
    store.ensure(connection)
    row = _row(connection, quiz_id)
    if not _can_see(me, row):
        raise LookupError("That quiz was not found.")
    return row


def questions_of(row: dict[str, Any]) -> list[dict[str, Any]]:
    return kit.loads(row["questions"], []) or []


def titles(connection: sqlite3.Connection) -> dict[str, str]:
    store.ensure(connection)
    return {
        r["id"]: r["title"]
        for r in kit.fetch_all(connection, "SELECT id, title FROM pl_quizzes")
    }


def _clean(
    payload: dict[str, Any], current: dict[str, Any] | None = None
) -> dict[str, Any]:
    merged = {**(current or {}), **payload}
    questions = merged.get("questions")
    if isinstance(questions, str):  # the stored form when only the title changes
        questions = kit.loads(questions, [])
    shared = merged.get("shared", True)
    return {
        "title": kit.text(
            merged.get("title"), "a name for the quiz", max_len=80, required=True
        ),
        "about": kit.text(merged.get("about"), "what it is about", max_len=200),
        "questions": kit.dumps(games.clean_quiz_questions(questions)),
        "shared": 1
        if (shared if isinstance(shared, bool) else kit.flag(shared))
        else 0,
    }


def view(
    connection: sqlite3.Connection,
    me: dict[str, Any],
    row: dict[str, Any],
    *,
    people: dict[str, dict[str, Any]] | None = None,
    with_answers: bool = False,
) -> dict[str, Any]:
    people = (
        people
        if people is not None
        else {p["id"]: life.public(p) for p in life.profiles(connection)}
    )
    questions = questions_of(row)
    stats = connection.execute(
        """SELECT COUNT(*), MAX(CASE WHEN profile_id = ? THEN score END) FROM pl_sessions
        WHERE game = 'quiz' AND level = ? AND status = 'finished'""",
        (me["id"], row["id"]),
    ).fetchone()
    mine = row["owner_id"] == me["id"]
    out: dict[str, Any] = {
        "id": row["id"],
        "title": row["title"],
        "about": row["about"],
        "count": len(questions),
        "shared": bool(row["shared"]),
        "owner": people.get(row["owner_id"], {}).get("name", "Someone"),
        "owner_emoji": people.get(row["owner_id"], {}).get("emoji", ""),
        "mine": mine,
        "can_change": _can_change(me, row),
        "plays": int(stats[0] or 0),
        "my_best": None if stats[1] is None else int(stats[1]),
        "updated_at": row["updated_at"],
    }
    if with_answers and out["can_change"]:
        out["questions"] = questions
    return out


def list_quizzes(
    connection: sqlite3.Connection, me: dict[str, Any]
) -> list[dict[str, Any]]:
    store.ensure(connection)
    rows = kit.fetch_all(
        connection,
        "SELECT * FROM pl_quizzes WHERE owner_id = ? OR shared = 1 ORDER BY updated_at DESC LIMIT 200",
        (me["id"],),
    )
    people = {p["id"]: life.public(p) for p in life.profiles(connection)}
    return [view(connection, me, row, people=people) for row in rows]


def get_quiz(
    connection: sqlite3.Connection, me: dict[str, Any], quiz_id: str
) -> dict[str, Any]:
    """A quiz with its questions and answers, for whoever may edit it."""
    row = visible(connection, me, quiz_id)
    if not _can_change(me, row):
        raise PermissionError("Only whoever wrote this quiz can see its answers.")
    return view(connection, me, row, with_answers=True)


def create(
    connection: sqlite3.Connection, me: dict[str, Any], payload: dict[str, Any]
) -> dict[str, Any]:
    store.ensure(connection)
    values = _clean(payload)
    quiz_id = kit.new_id()
    connection.execute(
        """INSERT INTO pl_quizzes (id, owner_id, title, about, questions, shared, created_at, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            quiz_id,
            me["id"],
            values["title"],
            values["about"],
            values["questions"],
            values["shared"],
            kit.now(),
            kit.now(),
        ),
    )
    if values["shared"]:
        kit.log(
            connection,
            APP,
            "quiz",
            f"{me['name']} wrote a quiz: {values['title']}",
            ref_type="quiz",
            ref_id=quiz_id,
            actor=me["name"],
        )
    connection.commit()
    return view(connection, me, _row(connection, quiz_id), with_answers=True)


def update(
    connection: sqlite3.Connection,
    me: dict[str, Any],
    quiz_id: str,
    payload: dict[str, Any],
) -> dict[str, Any]:
    row = visible(connection, me, quiz_id)
    if not _can_change(me, row):
        raise PermissionError(
            "Ask whoever wrote this quiz, or a grown-up, to change it."
        )
    values = _clean(payload, row)
    kit.update_row(
        connection, "pl_quizzes", quiz_id, {**values, "updated_at": kit.now()}
    )
    if not values["shared"]:
        # kept private now: nobody else can play it, so challenges waiting on it go
        connection.execute(
            "DELETE FROM pl_challenges WHERE game = 'quiz' AND level = ? AND status = 'open' AND to_id != ?",
            (quiz_id, row["owner_id"]),
        )
    connection.commit()
    return view(connection, me, _row(connection, quiz_id), with_answers=True)


def delete(connection: sqlite3.Connection, me: dict[str, Any], quiz_id: str) -> None:
    row = visible(connection, me, quiz_id)
    if not _can_change(me, row):
        raise PermissionError(
            "Ask whoever wrote this quiz, or a grown-up, to remove it."
        )
    connection.execute("DELETE FROM pl_quizzes WHERE id = ?", (quiz_id,))
    connection.execute(
        "DELETE FROM pl_challenges WHERE game = 'quiz' AND level = ? AND status = 'open'",
        (quiz_id,),
    )
    connection.commit()


def board(
    connection: sqlite3.Connection, me: dict[str, Any], quiz_id: str
) -> list[dict[str, Any]]:
    """Each person's best on this quiz (the writer knows the answers, so their own plays do not count)."""
    row = visible(connection, me, quiz_id)
    people = {p["id"]: life.public(p) for p in life.profiles(connection)}
    rows = kit.fetch_all(
        connection,
        """SELECT profile_id, MAX(score) AS best, COUNT(*) AS plays FROM pl_sessions
        WHERE game = 'quiz' AND level = ? AND status = 'finished' AND profile_id != ? GROUP BY profile_id""",
        (quiz_id, row["owner_id"]),
    )
    out = [
        {
            "profile_id": r["profile_id"],
            "name": people[r["profile_id"]]["name"],
            "emoji": people[r["profile_id"]]["emoji"],
            "best": int(r["best"] or 0),
            "plays": int(r["plays"]),
        }
        for r in rows
        if r["profile_id"] in people
    ]
    out.sort(key=lambda b: (-b["best"], b["name"]))
    for rank, entry in enumerate(out, start=1):
        entry["rank"] = rank
    return out


def _model_questions(raw: Any, count: int) -> list[dict[str, Any]]:
    """Questions from the writing model, kept only when each one is complete and sensible."""
    out: list[dict[str, Any]] = []
    if not isinstance(raw, list):
        return out
    for item in raw[: count * 2]:
        if not isinstance(item, dict):
            continue
        text = " ".join(str(item.get("question") or item.get("text") or "").split())[
            :200
        ]
        right = " ".join(str(item.get("answer") or "").split())[:80]
        wrong_raw = item.get("wrong") if isinstance(item.get("wrong"), list) else []
        wrong = [" ".join(str(w or "").split())[:80] for w in wrong_raw]
        wrong = [w for w in wrong if w and w.lower() != right.lower()][:3]
        if (
            not text
            or not right
            or len(wrong) < 1
            or len({w.lower() for w in wrong}) != len(wrong)
        ):
            continue
        out.append({"text": text, "choices": [right, *wrong], "answer": 0})
        if len(out) >= count:
            break
    return out


def draft(
    connection: sqlite3.Connection, me: dict[str, Any], payload: dict[str, Any]
) -> dict[str, Any]:
    """Suggested questions on a topic from the writing model, or from the built-in quiz book when no model runs.
    Nothing is saved: the person checks and edits them first."""
    store.ensure(connection)
    topic = kit.text(payload.get("topic"), "a topic", max_len=80, required=True)
    count = kit.integer(
        payload.get("count"),
        "the number of questions",
        default=5,
        minimum=1,
        maximum=DRAFT_MAX,
    )
    young = me.get("age_group") == "child"
    prompt = (
        f"Write {count} multiple-choice quiz questions about: {topic}.\n"
        f"{'They are for a child: short, simple words, nothing scary. ' if young else ''}"
        "Each question has one right answer and three wrong but believable answers. Facts must be correct.\n"
        'Answer only with JSON: {"questions": [{"question": "...", "answer": "...", "wrong": ["...", "...", "..."]}]}'
    )
    reply = kit.draft(prompt, {"questions": []})
    questions = (
        _model_questions(reply.get("questions"), count)
        if reply.get("source") == "model"
        else []
    )
    if questions:
        return {"questions": questions, "source": "model"}
    return {"questions": games.template_questions(topic, count), "source": "template"}
