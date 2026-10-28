"""A person's progress over time: a calendar of the days they practised, how much they remember, what comes up in
the next days, their hardest cards and a daily log to download. Everything is the asking person's own numbers.
"""

from __future__ import annotations

import sqlite3
from datetime import date, datetime, timedelta
from typing import Any

from aira.suite import kit
from aira.suite.learn import service, store

CALENDAR_DAYS = 84  # twelve weeks
RETENTION_DAYS = 30
RETENTION_MIN_REVIEWS = 5
FORECAST_DAYS = 7
HARDEST = 10
LOG_COLUMNS = (
    "day",
    "cards",
    "new_cards",
    "minutes_studied",
    "reading_minutes",
    "xp",
    "goal_met",
    "streak_freeze",
)

_VISIBLE = "(d.owner_id = :me OR d.shared = 1) AND d.archived_at IS NULL"
# A card is hard while it keeps being missed (or has a low ease) and is not yet known well; once it comes back
# only every three weeks or more, it is no longer one of the hardest, however often it was missed before.
KNOWN_WELL_DAYS = 21
_HARD = f"(p.lapses > 0 OR p.ease < 2.3) AND p.interval_days < {KNOWN_WELL_DAYS}"
# Answered right today (its last answer, today, was not "again"): practised enough for one day. The last answer is
# the newest row (rowid), since two answers can share a second.
_DONE_TODAY = """EXISTS (SELECT 1 FROM ln_reviews r WHERE r.profile_id = :me AND r.card_id = c.id AND r.day = :today
    AND r.grade != 'again' AND r.rowid = (SELECT MAX(r2.rowid) FROM ln_reviews r2 WHERE r2.profile_id = :me
    AND r2.card_id = c.id))"""


def _days(
    connection: sqlite3.Connection, profile_id: str, since: str = ""
) -> list[dict[str, Any]]:
    return kit.fetch_all(
        connection,
        "SELECT * FROM ln_days WHERE profile_id = ? AND day >= ? ORDER BY day",
        (profile_id, since),
    )


def best_streak(rows: list[dict[str, Any]]) -> int:
    """The longest run of days in a row with the goal met (or kept by a streak freeze)."""
    best = run = 0
    previous: date | None = None
    for row in rows:
        if not (row["goal_met"] or row["frozen"]):
            continue
        day = date.fromisoformat(row["day"])
        run = (
            run + 1
            if previous is not None and day - previous == timedelta(days=1)
            else 1
        )
        best = max(best, run)
        previous = day
    return best


def stats(
    connection: sqlite3.Connection,
    profile: dict[str, Any],
    *,
    now: datetime | None = None,
) -> dict[str, Any]:
    today = service.today_of(connection, now)
    me = profile["id"]
    start = today - timedelta(days=CALENDAR_DAYS - 1)
    by_day = {row["day"]: row for row in _days(connection, me, start.isoformat())}
    calendar = []
    for offset in range(CALENDAR_DAYS):
        day = (start + timedelta(days=offset)).isoformat()
        row = by_day.get(day) or {}
        calendar.append(
            {
                "day": day,
                "cards": int(row.get("cards") or 0),
                "minutes": int(row.get("seconds") or 0) // 60
                + int(row.get("reading_minutes") or 0),
                "goal_met": bool(row.get("goal_met")),
                "frozen": bool(row.get("frozen")),
            }
        )
    every_day = _days(connection, me)
    since = (today - timedelta(days=RETENTION_DAYS - 1)).isoformat()
    graded = kit.fetch_one(
        connection,
        """SELECT COUNT(*) AS total, COALESCE(SUM(CASE WHEN grade != 'again' THEN 1 ELSE 0 END), 0) AS right
        FROM ln_reviews WHERE profile_id = ? AND was_new = 0 AND practice = 0 AND day >= ?""",
        (me, since),
    ) or {"total": 0, "right": 0}
    total = int(graded["total"] or 0)
    retention = (
        round(int(graded["right"] or 0) * 100 / total)
        if total >= RETENTION_MIN_REVIEWS
        else None
    )
    params = {
        "me": me,
        "today": today.isoformat(),
        "end": (today + timedelta(days=FORECAST_DAYS)).isoformat(),
    }
    due_rows = connection.execute(
        f"""SELECT CASE WHEN p.due <= :today THEN :today ELSE p.due END AS day, COUNT(*) FROM ln_progress p
        JOIN ln_cards c ON c.id = p.card_id JOIN ln_decks d ON d.id = c.deck_id
        WHERE p.profile_id = :me AND p.due <= :end AND {_VISIBLE} GROUP BY 1""",  # noqa: S608 - fixed clause
        params,
    ).fetchall()
    due_by_day = {row[0]: int(row[1]) for row in due_rows}
    forecast = [
        {
            "day": (today + timedelta(days=offset)).isoformat(),
            "cards": due_by_day.get((today + timedelta(days=offset)).isoformat(), 0),
        }
        for offset in range(FORECAST_DAYS + 1)
    ]
    hardest = [
        kit.as_dict(row)
        for row in connection.execute(
            f"""SELECT c.id, c.deck_id, d.title AS deck_title, c.front, c.back, p.lapses, p.ease, p.due
            FROM ln_progress p JOIN ln_cards c ON c.id = p.card_id JOIN ln_decks d ON d.id = c.deck_id
            WHERE p.profile_id = :me AND p.lapses > 0 AND p.interval_days < {KNOWN_WELL_DAYS} AND {_VISIBLE}
            ORDER BY p.lapses DESC, p.ease ASC, p.last_reviewed_at DESC LIMIT {HARDEST}""",  # noqa: S608
            params,
        ).fetchall()
    ]
    week_start = today - timedelta(days=today.weekday())
    this_week = [c for c in calendar if c["day"] >= week_start.isoformat()]
    learned = kit.count(
        connection,
        f"""SELECT COUNT(*) FROM ln_progress p JOIN ln_decks d ON d.id = p.deck_id
        WHERE p.profile_id = :me AND p.interval_days >= {KNOWN_WELL_DAYS} AND {_VISIBLE}""",  # noqa: S608
        params,
    )
    return {
        "today": today.isoformat(),
        "calendar": calendar,
        "totals": {
            "cards": kit.count(
                connection,
                "SELECT COUNT(*) FROM ln_reviews WHERE profile_id = ?",
                (me,),
            ),
            "days": sum(
                1 for row in every_day if row["cards"] or row["reading_minutes"]
            ),
            "streak": service.streak(connection, me, today),
            "best_streak": best_streak(every_day),
            "mastered": learned,
            "minutes_week": sum(c["minutes"] for c in this_week),
            "cards_week": sum(c["cards"] for c in this_week),
        },
        "retention": retention,
        "retention_reviews": total,
        "forecast": forecast,
        "hardest": hardest,
    }


def daily_log(connection: sqlite3.Connection, profile: dict[str, Any]) -> bytes:
    rows = [
        {
            "day": row["day"],
            "cards": row["cards"],
            "new_cards": row["new_cards"],
            "minutes_studied": row["seconds"] // 60,
            "reading_minutes": row["reading_minutes"],
            "xp": row["xp"],
            "goal_met": "yes" if row["goal_met"] else "no",
            "streak_freeze": "yes" if row["frozen"] else "no",
        }
        for row in reversed(_days(connection, profile["id"]))
    ]
    return kit.csv_bytes(rows, LOG_COLUMNS)


def weak_queue(
    connection: sqlite3.Connection,
    profile: dict[str, Any],
    *,
    deck_id: str = "",
    limit: int = 20,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Cards this person keeps missing and does not know well yet, hardest first, due or not; a card answered
    right today waits until tomorrow. ``done_today`` counts the hard cards already practised today."""
    if deck_id:
        store.visible_deck(connection, profile, deck_id)
    params = {
        "me": profile["id"],
        "deck": deck_id,
        "n": limit,
        "today": service.today_of(connection, now).isoformat(),
    }
    deck_clause = "AND d.id = :deck" if deck_id else ""
    rows = connection.execute(
        f"""SELECT c.*, d.title AS deck_title, p.interval_days FROM ln_progress p JOIN ln_cards c ON c.id = p.card_id
        JOIN ln_decks d ON d.id = c.deck_id WHERE p.profile_id = :me AND {_HARD}
        AND {_VISIBLE} {deck_clause} AND NOT {_DONE_TODAY}
        ORDER BY p.lapses DESC, p.ease ASC LIMIT :n""",  # noqa: S608 - fixed clauses
        params,
    ).fetchall()
    done = connection.execute(
        f"""SELECT COUNT(*) FROM ln_progress p JOIN ln_cards c ON c.id = p.card_id JOIN ln_decks d ON d.id = c.deck_id
        WHERE p.profile_id = :me AND {_HARD} AND {_VISIBLE} {deck_clause} AND {_DONE_TODAY}""",  # noqa: S608
        params,
    ).fetchone()
    return {
        "cards": [service.card_view(kit.as_dict(row), False) for row in rows],
        "done_today": int(done[0] or 0) if done else 0,
    }
