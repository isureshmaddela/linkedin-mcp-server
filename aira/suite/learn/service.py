"""Learn's rules: spaced repetition, the daily goal and streak, XP and badges, quizzes and maths graded on the
server, decks on any topic, reading, and the family leaderboard.

Days are the business's local days (``kit.business(...)["timezone"]``): a streak is about the person's own
evening, not UTC. Every function that depends on the clock takes ``now`` so tests can move it.
"""

from __future__ import annotations

import math
import random
import sqlite3
from datetime import date, datetime, timedelta
from fractions import Fraction
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from aira.suite import kit, life
from aira.suite.learn import starters, store

APP = store.APP
GRADES = ("again", "hard", "good", "easy")
_QUALITY = {"again": 1, "hard": 3, "good": 4, "easy": 5}
XP_FOR = {"again": 2, "hard": 4, "good": 5, "easy": 5}
XP_NEW_CARD, XP_GOAL, XP_QUIZ_RIGHT, XP_QUIZ_PERFECT, XP_MATHS_RIGHT = 2, 20, 5, 10, 3
READING_XP_CAP = 30  # XP a day from reading (1 per minute)
MAX_INTERVAL = 3650
SECONDS_CAP = 60  # one card never counts for more than a minute (a card left open is not studying)


# ---------------------------------------------------------------------------
# time
# ---------------------------------------------------------------------------


def local_now(connection: sqlite3.Connection, now: datetime | None = None) -> datetime:
    zone = kit.business(connection).get("timezone") or "UTC"
    try:
        tz = ZoneInfo(zone)
    except (ZoneInfoNotFoundError, ValueError):
        tz = ZoneInfo("UTC")
    moment = now or datetime.now(tz)
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=ZoneInfo("UTC"))
    return moment.astimezone(tz)


def today_of(connection: sqlite3.Connection, now: datetime | None = None) -> date:
    return local_now(connection, now).date()


def week_key(day: date) -> str:
    year, week, _ = day.isocalendar()
    return f"{year}-W{week:02d}"


# ---------------------------------------------------------------------------
# spaced repetition (SM-2)
# ---------------------------------------------------------------------------


def schedule(state: dict[str, Any], grade: str, today: date) -> dict[str, Any]:
    """The next state of one card for one person after an answer.

    Ease starts at 2.5 and changes by SM-2's rule (easy +0.1, good 0, hard -0.14, again -0.54; never below
    1.3). Again sends the card back today. The first right answer comes back tomorrow (easy: in 3 days), the
    second in 6 days (hard 3, easy 8), then the interval grows by the ease (hard by 1.2, easy by ease x 1.3).
    """
    if grade not in _QUALITY:
        raise ValueError("Answer with again, hard, good or easy.")
    quality = _QUALITY[grade]
    ease = float(state.get("ease") or 2.5)
    interval = int(state.get("interval_days") or 0)
    reps = int(state.get("reps") or 0)
    lapses = int(state.get("lapses") or 0)
    ease = max(
        1.3, round(ease + (0.1 - (5 - quality) * (0.08 + (5 - quality) * 0.02)), 2)
    )
    if quality < 3:
        reps, interval, lapses = 0, 0, lapses + 1
    else:
        if reps == 0:
            interval = {"hard": 1, "good": 1, "easy": 3}[grade]
        elif reps == 1:
            interval = {"hard": 3, "good": 6, "easy": 8}[grade]
        elif grade == "hard":
            interval = max(interval + 1, round(interval * 1.2))
        elif grade == "good":
            interval = max(interval + 1, round(interval * ease))
        else:
            interval = max(interval + 2, round(interval * ease * 1.3))
        reps += 1
    interval = min(interval, MAX_INTERVAL)
    return {
        "ease": ease,
        "interval_days": interval,
        "reps": reps,
        "lapses": lapses,
        "due": (today + timedelta(days=interval)).isoformat(),
    }


def when_label(interval: int) -> str:
    if interval <= 0:
        return "Again in a moment"
    if interval == 1:
        return "Tomorrow"
    if interval < 30:
        return f"In {interval} days"
    if interval < 365:
        return f"In about {round(interval / 30)} month{'s' if round(interval / 30) != 1 else ''}"
    return f"In about {round(interval / 365)} year{'s' if round(interval / 365) != 1 else ''}"


# ---------------------------------------------------------------------------
# levels and badges
# ---------------------------------------------------------------------------


def level_of(xp: int) -> dict[str, int]:
    """Level 1 from 0 XP, 2 from 50, 3 from 200, 4 from 450: level n needs 50 x (n - 1)^2."""
    level = int(math.sqrt(max(0, xp) / 50)) + 1
    return {
        "level": level,
        "xp": xp,
        "next_at": 50 * level * level,
        "this_at": 50 * (level - 1) * (level - 1),
    }


BADGES: tuple[tuple[str, str, str, str], ...] = (
    ("first_deck", "First deck", "Added your first deck", "🎉"),
    ("first_review", "First card", "Studied your first card", "🌱"),
    ("streak_3", "3 days in a row", "Met your goal three days running", "🔥"),
    ("streak_7", "A whole week", "Met your goal seven days running", "🔥"),
    ("streak_30", "A whole month", "Met your goal thirty days running", "🏆"),
    ("cards_100", "100 cards", "Studied 100 cards", "💯"),
    ("cards_1000", "1,000 cards", "Studied 1,000 cards", "🚀"),
    ("quiz_perfect", "Perfect quiz", "Got every answer right", "⭐"),
    ("maths_25", "Maths star", "25 maths answers right", "➗"),
    ("bookworm", "Bookworm", "Read for 5 hours", "📖"),
    ("level_5", "Level 5", "Reached level 5", "🎓"),
)
BADGE_INFO = {
    key: {"key": key, "label": label, "about": about, "emoji": emoji}
    for key, label, about, emoji in BADGES
}


def _stats(
    connection: sqlite3.Connection, profile_id: str, today: date
) -> dict[str, int]:
    return {
        "decks": kit.count(
            connection,
            "SELECT COUNT(*) FROM ln_decks WHERE owner_id = ?",
            (profile_id,),
        ),
        "reviews": kit.count(
            connection,
            "SELECT COUNT(*) FROM ln_reviews WHERE profile_id = ?",
            (profile_id,),
        ),
        "streak": streak(connection, profile_id, today),
        "perfect": kit.count(
            connection,
            "SELECT COUNT(*) FROM ln_quizzes WHERE profile_id = ? AND kind = 'deck' AND status = 'done' AND total > 0 AND score = total",
            (profile_id,),
        ),
        "maths_right": kit.count(
            connection,
            "SELECT COALESCE(SUM(score), 0) FROM ln_quizzes WHERE profile_id = ? AND kind = 'maths' AND status = 'done' AND late = 0",
            (profile_id,),
        ),
        "reading": kit.count(
            connection,
            "SELECT COALESCE(SUM(minutes), 0) FROM ln_reading WHERE profile_id = ?",
            (profile_id,),
        ),
        "level": level_of(
            kit.count(
                connection,
                "SELECT xp FROM ln_people WHERE profile_id = ?",
                (profile_id,),
            )
        )["level"],
    }


_RULES = {
    "first_deck": lambda s: s["decks"] >= 1,
    "first_review": lambda s: s["reviews"] >= 1,
    "streak_3": lambda s: s["streak"] >= 3,
    "streak_7": lambda s: s["streak"] >= 7,
    "streak_30": lambda s: s["streak"] >= 30,
    "cards_100": lambda s: s["reviews"] >= 100,
    "cards_1000": lambda s: s["reviews"] >= 1000,
    "quiz_perfect": lambda s: s["perfect"] >= 1,
    "maths_25": lambda s: s["maths_right"] >= 25,
    "bookworm": lambda s: s["reading"] >= 300,
    "level_5": lambda s: s["level"] >= 5,
}


def award_badges(
    connection: sqlite3.Connection, profile: dict[str, Any], today: date
) -> list[dict[str, Any]]:
    """Award any badge newly earned; returns the new ones (shown as a celebration)."""
    me = store.person(connection, profile)
    stats = _stats(connection, profile["id"], today)
    earned = dict(me["badges"])
    new = []
    for key, rule in _RULES.items():
        if key not in earned and rule(stats):
            earned[key] = today.isoformat()
            new.append(BADGE_INFO[key])
    if new:
        connection.execute(
            "UPDATE ln_people SET badges = ? WHERE profile_id = ?",
            (kit.dumps(earned), profile["id"]),
        )
        for badge in new:
            kit.log(
                connection,
                APP,
                "badge",
                f"{profile['name']} earned {badge['label']} {badge['emoji']}",
            )
    return new


def badges_view(me: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        {
            **BADGE_INFO[key],
            "earned_on": me["badges"].get(key),
            "earned": key in me["badges"],
        }
        for key, *_ in BADGES
    ]


# ---------------------------------------------------------------------------
# the daily goal and the streak
# ---------------------------------------------------------------------------


def goal_state(
    connection: sqlite3.Connection, profile: dict[str, Any], day: date
) -> dict[str, Any]:
    me = store.person(connection, profile)
    row = store.day_row(connection, profile["id"], day.isoformat())
    minutes = row["seconds"] // 60 + (
        row["reading_minutes"] if me["reading_counts"] else 0
    )
    cards = row["cards"] + (
        (row["reading_minutes"] // 2) if me["reading_counts"] else 0
    )
    progress = cards if me["goal_kind"] == "cards" else minutes
    goal = int(me["goal_value"])
    return {
        "kind": me["goal_kind"],
        "goal": goal,
        "progress": min(progress, goal),
        "done": bool(row["goal_met"]) or progress >= goal,
        "cards_today": row["cards"],
        "minutes_today": minutes,
        "reading_today": row["reading_minutes"],
        "label": f"{min(progress, goal)} of {goal} {'cards' if me['goal_kind'] == 'cards' else 'minutes'}",
    }


def _kept_days(
    connection: sqlite3.Connection, profile_id: str, since: date
) -> set[str]:
    rows = connection.execute(
        "SELECT day FROM ln_days WHERE profile_id = ? AND day >= ? AND (goal_met = 1 OR frozen = 1)",
        (profile_id, since.isoformat()),
    ).fetchall()
    return {row[0] for row in rows}


def streak(connection: sqlite3.Connection, profile_id: str, today: date) -> int:
    """Days in a row the goal was met (or a freeze covered a miss), ending today, or yesterday while today's goal
    is still open."""
    kept = _kept_days(connection, profile_id, today - timedelta(days=800))
    cursor = today if today.isoformat() in kept else today - timedelta(days=1)
    count = 0
    while cursor.isoformat() in kept:
        count += 1
        cursor -= timedelta(days=1)
    return count


def _apply_freeze(
    connection: sqlite3.Connection, profile: dict[str, Any], today: date
) -> bool:
    """Meeting today's goal after one missed day keeps the streak, once a week (that day's week)."""
    me = store.person(connection, profile)
    kept = _kept_days(connection, profile["id"], today - timedelta(days=3))
    missed, before = today - timedelta(days=1), today - timedelta(days=2)
    if missed.isoformat() in kept or before.isoformat() not in kept:
        return False
    week = week_key(missed)
    if week in me["freeze_weeks"]:
        return False
    store.touch_day(connection, profile["id"], missed.isoformat())
    connection.execute(
        "UPDATE ln_days SET frozen = 1 WHERE profile_id = ? AND day = ?",
        (profile["id"], missed.isoformat()),
    )
    weeks = [*me["freeze_weeks"], week][-12:]
    connection.execute(
        "UPDATE ln_people SET freeze_weeks = ? WHERE profile_id = ?",
        (kit.dumps(weeks), profile["id"]),
    )
    kit.log(
        connection,
        APP,
        "freeze",
        f"{profile['name']}'s streak was kept by this week's streak freeze",
    )
    return True


def _check_goal(
    connection: sqlite3.Connection, profile: dict[str, Any], today: date
) -> dict[str, Any]:
    """After any activity: mark the goal met (once), use a freeze if it bridges one missed day, add the bonus."""
    state = goal_state(connection, profile, today)
    row = store.day_row(connection, profile["id"], today.isoformat())
    newly = state["done"] and not row["goal_met"]
    froze = False
    if newly:
        store.touch_day(connection, profile["id"], today.isoformat())
        connection.execute(
            "UPDATE ln_days SET goal_met = 1 WHERE profile_id = ? AND day = ?",
            (profile["id"], today.isoformat()),
        )
        froze = _apply_freeze(connection, profile, today)
        store.add_xp(connection, profile["id"], today.isoformat(), XP_GOAL)
        kit.log(connection, APP, "goal", f"{profile['name']} met today's learning goal")
    return {
        **goal_state(connection, profile, today),
        "just_met": newly,
        "freeze_used": froze,
    }


def _finish(
    connection: sqlite3.Connection, profile: dict[str, Any], today: date, xp: int
) -> dict[str, Any]:
    goal = _check_goal(connection, profile, today)
    new_badges = award_badges(connection, profile, today)
    me = store.person(connection, profile)
    connection.commit()
    return {
        "xp_gained": xp + (XP_GOAL if goal["just_met"] else 0),
        "goal": goal,
        "streak": streak(connection, profile["id"], today),
        "level": level_of(int(me["xp"])),
        "new_badges": new_badges,
    }


# ---------------------------------------------------------------------------
# studying
# ---------------------------------------------------------------------------

_VISIBLE = "(d.owner_id = :me OR d.shared = 1) AND d.archived_at IS NULL"


def _named_count(
    connection: sqlite3.Connection, sql: str, params: dict[str, Any]
) -> int:
    row = connection.execute(sql, params).fetchone()
    return int(row[0] or 0) if row else 0


def _named_rows(
    connection: sqlite3.Connection, sql: str, params: dict[str, Any]
) -> list[dict[str, Any]]:
    return [kit.as_dict(row) for row in connection.execute(sql, params).fetchall()]


def _card_view(row: dict[str, Any], is_new: bool) -> dict[str, Any]:
    return {
        "id": row["id"],
        "deck_id": row["deck_id"],
        "deck_title": row.get("deck_title", ""),
        "front": row["front"],
        "back": row["back"],
        "hint": row["hint"],
        "example": row["example"],
        "is_new": is_new,
        "interval_days": int(row.get("interval_days") or 0),
    }


card_view = _card_view


def counts(
    connection: sqlite3.Connection,
    profile: dict[str, Any],
    today: date,
    deck_id: str = "",
) -> dict[str, int]:
    """Cards due and new cards waiting for this person (within today's limits)."""
    me = store.person(connection, profile)
    row = store.day_row(connection, profile["id"], today.isoformat())
    params: dict[str, Any] = {
        "me": profile["id"],
        "today": today.isoformat(),
        "deck": deck_id,
    }
    deck_clause = "AND d.id = :deck" if deck_id else ""
    due = _named_count(
        connection,
        f"""SELECT COUNT(*) FROM ln_progress p JOIN ln_cards c ON c.id = p.card_id JOIN ln_decks d ON d.id = c.deck_id
        WHERE p.profile_id = :me AND p.due <= :today AND {_VISIBLE} {deck_clause}""",  # noqa: S608
        params,
    )
    fresh = _named_count(
        connection,
        f"""SELECT COUNT(*) FROM ln_cards c JOIN ln_decks d ON d.id = c.deck_id
        WHERE {_VISIBLE} {deck_clause}
        AND NOT EXISTS (SELECT 1 FROM ln_progress p WHERE p.profile_id = :me AND p.card_id = c.id)""",  # noqa: S608
        params,
    )
    reviews_left = max(
        0, int(me["reviews_per_day"]) - (row["cards"] - row["new_cards"])
    )
    new_left = max(0, int(me["new_per_day"]) - row["new_cards"])
    return {
        "due": min(due, reviews_left),
        "due_total": due,
        "new": min(fresh, new_left),
        "new_total": fresh,
        "reviews_left": reviews_left,
        "new_left": new_left,
    }


def study_queue(
    connection: sqlite3.Connection,
    profile: dict[str, Any],
    *,
    deck_id: str = "",
    now: datetime | None = None,
    limit: int = 20,
) -> dict[str, Any]:
    """The next cards to study: reviews that are due first, with new cards mixed in, within today's limits."""
    today = today_of(connection, now)
    if deck_id:
        store.visible_deck(connection, profile, deck_id)
    numbers = counts(connection, profile, today, deck_id)
    params: dict[str, Any] = {
        "me": profile["id"],
        "today": today.isoformat(),
        "deck": deck_id,
    }
    deck_clause = "AND d.id = :deck" if deck_id else ""
    due_rows = _named_rows(
        connection,
        f"""SELECT c.*, d.title AS deck_title, p.interval_days FROM ln_progress p JOIN ln_cards c ON c.id = p.card_id
        JOIN ln_decks d ON d.id = c.deck_id WHERE p.profile_id = :me AND p.due <= :today AND {_VISIBLE} {deck_clause}
        ORDER BY p.due, p.last_reviewed_at LIMIT :n""",  # noqa: S608
        {**params, "n": max(0, min(limit, numbers["reviews_left"]))},
    )
    new_rows = _named_rows(
        connection,
        f"""SELECT c.*, d.title AS deck_title FROM ln_cards c JOIN ln_decks d ON d.id = c.deck_id
        WHERE {_VISIBLE} {deck_clause}
        AND NOT EXISTS (SELECT 1 FROM ln_progress p WHERE p.profile_id = :me AND p.card_id = c.id)
        ORDER BY d.created_at, c.position LIMIT :n""",  # noqa: S608
        {**params, "n": max(0, min(limit, numbers["new_left"]))},
    )
    queue: list[dict[str, Any]] = []
    due_list = [_card_view(row, False) for row in due_rows]
    new_list = [_card_view(row, True) for row in new_rows]
    while (due_list or new_list) and len(queue) < limit:
        for _ in range(3):  # three reviews, then one new card
            if due_list and len(queue) < limit:
                queue.append(due_list.pop(0))
        if new_list and len(queue) < limit:
            queue.append(new_list.pop(0))
    return {
        "cards": queue,
        "counts": numbers,
        "goal": goal_state(connection, profile, today),
    }


def answer(
    connection: sqlite3.Connection,
    profile: dict[str, Any],
    card_id: str,
    grade: str,
    *,
    seconds: int = 0,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Grade one card. A card that is due (or new) is scheduled by SM-2.

    A card answered before it is due (extra practice: "Practise my hardest cards") is practice, not a review: a right
    answer leaves its schedule as it was (practising early must not push it months away), a miss sends it back for
    today like any miss. Practice counts toward XP and the goal once per card a day, so repeating it earns nothing.
    """
    grade = kit.choice(grade, "the answer", GRADES)
    found = store.card(connection, card_id)
    store.visible_deck(connection, profile, found["deck_id"])
    today = today_of(connection, now)
    day = today.isoformat()
    state = kit.fetch_one(
        connection,
        "SELECT * FROM ln_progress WHERE profile_id = ? AND card_id = ?",
        (profile["id"], card_id),
    )
    was_new = state is None
    if was_new and counts(connection, profile, today)["new_left"] <= 0:
        raise ValueError(
            "That's all the new cards for today. Come back tomorrow for more!"
        )
    practice = state is not None and str(state["due"]) > day
    counted = not practice or not kit.count(
        connection,
        "SELECT COUNT(*) FROM ln_reviews WHERE profile_id = ? AND card_id = ? AND day = ?",
        (profile["id"], card_id, day),
    )
    if practice and grade != "again":
        assert state is not None
        nxt = {
            "interval_days": int(state["interval_days"]),
            "due": str(state["due"]),
        }
    else:
        nxt = schedule(state or {}, grade, today)
        connection.execute(
            """INSERT INTO ln_progress (profile_id, card_id, deck_id, ease, interval_days, reps, lapses, due, last_reviewed_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(profile_id, card_id) DO UPDATE SET ease = excluded.ease, interval_days = excluded.interval_days,
            reps = excluded.reps, lapses = excluded.lapses, due = excluded.due, last_reviewed_at = excluded.last_reviewed_at""",
            (
                profile["id"],
                card_id,
                found["deck_id"],
                nxt["ease"],
                nxt["interval_days"],
                nxt["reps"],
                nxt["lapses"],
                nxt["due"],
                kit.now(),
            ),
        )
    spent = max(0, min(int(seconds or 0), SECONDS_CAP))
    connection.execute(
        """INSERT INTO ln_reviews (id, profile_id, card_id, deck_id, grade, was_new, seconds, day, at, practice)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            kit.new_id(),
            profile["id"],
            card_id,
            found["deck_id"],
            grade,
            1 if was_new else 0,
            spent,
            day,
            kit.now(),
            1 if practice else 0,
        ),
    )
    store.touch_day(connection, profile["id"], day)
    xp = 0
    if counted:
        connection.execute(
            "UPDATE ln_days SET cards = cards + 1, new_cards = new_cards + ?, seconds = seconds + ? WHERE profile_id = ? AND day = ?",
            (1 if was_new else 0, spent, profile["id"], day),
        )
        xp = XP_FOR[grade] + (XP_NEW_CARD if was_new else 0)
        store.add_xp(connection, profile["id"], day, xp)
    result = _finish(connection, profile, today, xp)
    days_left = (date.fromisoformat(str(nxt["due"])) - today).days
    return {
        **result,
        "card_id": card_id,
        "grade": grade,
        "practice": practice,
        "interval_days": nxt["interval_days"],
        "due": nxt["due"],
        "when": when_label(max(0, days_left)),
    }


# ---------------------------------------------------------------------------
# decks
# ---------------------------------------------------------------------------


def add_starter(
    connection: sqlite3.Connection,
    profile: dict[str, Any],
    key: str,
    *,
    now: datetime | None = None,
) -> dict[str, Any]:
    starter = starters.BY_KEY.get(str(key or ""))
    if starter is None:
        raise LookupError("That starter deck was not found.")
    deck = store.create_deck(
        connection,
        profile["id"],
        {
            "title": starter["title"],
            "description": starter["description"],
            "emoji": starter["emoji"],
            "age_group": starter["age_group"],
        },
        source=f"starter:{starter['key']}",
    )
    for front, back, hint in starter["cards"]:
        store.add_card(
            connection, deck["id"], {"front": front, "back": back, "hint": hint}
        )
    kit.log(
        connection,
        APP,
        "deck",
        f"{profile['name']} added the deck {starter['title']}",
        ref_type="deck",
        ref_id=deck["id"],
    )
    award_badges(connection, profile, today_of(connection, now))
    connection.commit()
    return deck


def starter_list(profile: dict[str, Any]) -> list[dict[str, Any]]:
    """Starter decks, the ones written for this person's age first."""
    group = profile.get("age_group", "adult")
    order = {group: 0, "all": 1}
    listed = [
        {
            "key": s["key"],
            "title": s["title"],
            "description": s["description"],
            "emoji": s["emoji"],
            "age_group": s["age_group"],
            "cards": len(s["cards"]),
            "for_you": s["age_group"] in (group, "all")
            or (group == "teen" and s["age_group"] == "adult"),
        }
        for s in starters.STARTERS
    ]
    return sorted(listed, key=lambda s: (order.get(s["age_group"], 2), s["title"]))


_REFLECT = (
    (
        "In one sentence, what is {t}?",
        "Say it out loud, then check a trusted source and write the answer here.",
    ),
    ("Give one example of {t}.", "Write your favourite example here once you find it."),
    ("Why does {t} matter?", "Think of one reason it matters to you or to others."),
    (
        "Name three words you link with {t}.",
        "Write them here, then look up any you are unsure of.",
    ),
    (
        "Who could you ask about {t}?",
        "A teacher, a friend, a library, or a trusted website.",
    ),
    ("What is one surprising fact about {t}?", "Find one and write it here."),
    (
        "How would you explain {t} to a child?",
        "Use simple words and a picture in your head.",
    ),
    (
        "What is one question you still have about {t}?",
        "Write it down and look for the answer this week.",
    ),
    ("Where do you see {t} in daily life?", "Look around today and note one place."),
    (
        "What is a common mistake about {t}?",
        "Write the mistake and the right idea here.",
    ),
)


def template_deck(topic: str, count: int) -> dict[str, Any]:
    """A usable deck without a model: a matching starter deck's cards, or prompts that guide the person to learn
    the topic themselves (clearly marked so they can edit the answers)."""
    starter = starters.match(topic)
    if starter:
        cards = [
            {"front": f, "back": b, "hint": h} for f, b, h in starter["cards"][:count]
        ]
        return {
            "title": topic.strip()[:80].title(),
            "cards": cards,
            "note": f"Made from the starter deck {starter['title']}.",
        }
    subject = topic.strip()[:60]
    cards = [
        {"front": q.format(t=subject), "back": a, "hint": ""} for q, a in _REFLECT
    ][: max(5, min(count, len(_REFLECT)))]
    return {
        "title": subject.title(),
        "cards": cards,
        "note": "Written from a template (no writing model was available): edit the answers to make it yours.",
    }


def generate_deck(
    connection: sqlite3.Connection,
    profile: dict[str, Any],
    raw: dict[str, Any],
    *,
    now: datetime | None = None,
) -> dict[str, Any]:
    topic = kit.text(raw.get("topic"), "a topic", max_len=80, required=True)
    count = kit.integer(
        raw.get("count"), "the number of cards", default=12, minimum=5, maximum=30
    )
    group = kit.choice(
        raw.get("age_group") or profile.get("age_group"),
        "the age group",
        life.AGE_GROUPS,
        default="adult",
    )
    fallback = template_deck(topic, count)
    words = {
        "child": "a child of about 8: very simple words and short answers",
        "teen": "a teenager",
        "adult": "an adult",
        "senior": "an older adult: clear, friendly, practical",
    }[group]
    prompt = (
        f"Write {count} flashcards about this topic: {topic}. They are for {words}. Each card has a short question "
        "on the front and a short, accurate answer on the back, and an optional hint. Reply only with JSON: "
        '{"title": "a short deck title", "cards": [{"front": "...", "back": "...", "hint": ""}]}'
    )
    result = kit.draft(prompt, {"title": fallback["title"], "cards": fallback["cards"]})
    cards = []
    for item in result.get("cards") or []:
        if not isinstance(item, dict):
            continue
        try:
            cards.append(store.clean_card(item))
        except ValueError:
            continue
        if len(cards) >= count:
            break
    source = "ai" if result.get("source") == "model" else "template"
    note = (
        "Written by the writing model: check the answers."
        if source == "ai"
        else fallback["note"]
    )
    title = str(result.get("title") or fallback["title"])
    if len(cards) < 3:
        cards, source, note, title = (
            [store.clean_card(c) for c in fallback["cards"]],
            "template",
            fallback["note"],
            fallback["title"],
        )
    deck = store.create_deck(
        connection,
        profile["id"],
        {
            "title": kit.text(title, "a title", max_len=80) or topic[:80],
            "description": note,
            "emoji": "✨",
            "age_group": group if group != "adult" else "all",
        },
        source=source,
    )
    for item in cards:
        store.add_card(connection, deck["id"], item)
    kit.log(
        connection,
        APP,
        "deck",
        f"{profile['name']} made a deck about {topic}",
        ref_type="deck",
        ref_id=deck["id"],
    )
    award_badges(connection, profile, today_of(connection, now))
    connection.commit()
    return {**deck, "note": note, "cards_added": len(cards)}


def copy_deck(
    connection: sqlite3.Connection, profile: dict[str, Any], deck_id: str
) -> dict[str, Any]:
    """This person's own copy of a deck they may study (a household member's shared deck), to change freely.
    The copy starts with fresh progress. A deck someone else is selling is theirs to sell: it is studied here, not
    copied (a copy could be sold under it), and a copy of another person's deck cannot be put on sale."""
    found = store.visible_deck(connection, profile, deck_id)
    if found["owner_id"] != profile["id"] and int(found.get("price_cents") or 0) > 0:
        raise PermissionError(
            f"{life.name_of(connection, found['owner_id'])} sells this deck, so it cannot be copied. "
            "You can still study it here."
        )
    copy = store.create_deck(
        connection,
        profile["id"],
        {
            "title": f"{found['title']} (my copy)"[:80],
            "description": found["description"],
            "emoji": found["emoji"],
            "age_group": found["age_group"],
        },
        source="copy",
    )
    # Whose deck it first was (a copy of a copy keeps the first owner).
    origin = (
        found.get("copied_from")
        if found.get("source") == "copy" and found.get("copied_from")
        else found["owner_id"]
    )
    connection.execute(
        "UPDATE ln_decks SET copied_from = ? WHERE id = ?", (origin, copy["id"])
    )
    copy["copied_from"] = origin
    for row in store.cards(connection, found["id"]):
        store.add_card(connection, copy["id"], row)
    kit.log(
        connection,
        APP,
        "deck",
        f"{profile['name']} made a copy of {found['title']}",
        ref_type="deck",
        ref_id=copy["id"],
    )
    award_badges(connection, profile, today_of(connection))
    connection.commit()
    return copy


def delete_deck(
    connection: sqlite3.Connection, profile: dict[str, Any], deck_id: str
) -> None:
    store.own_deck(connection, profile, deck_id)
    connection.execute(
        "UPDATE ln_decks SET archived_at = ?, shared = 0 WHERE id = ?",
        (kit.now(), deck_id),
    )
    connection.commit()


def deck_progress(
    connection: sqlite3.Connection, profile: dict[str, Any], deck_id: str, today: date
) -> dict[str, int]:
    total = kit.count(
        connection, "SELECT COUNT(*) FROM ln_cards WHERE deck_id = ?", (deck_id,)
    )
    seen = kit.count(
        connection,
        "SELECT COUNT(*) FROM ln_progress WHERE profile_id = ? AND deck_id = ?",
        (profile["id"], deck_id),
    )
    learned = kit.count(
        connection,
        "SELECT COUNT(*) FROM ln_progress WHERE profile_id = ? AND deck_id = ? AND interval_days >= 6",
        (profile["id"], deck_id),
    )
    due = kit.count(
        connection,
        "SELECT COUNT(*) FROM ln_progress WHERE profile_id = ? AND deck_id = ? AND due <= ?",
        (profile["id"], deck_id, today.isoformat()),
    )
    return {
        "total": total,
        "seen": seen,
        "learned": learned,
        "due": due,
        "new": total - seen,
    }


# ---------------------------------------------------------------------------
# quizzes (from a deck) and maths
# ---------------------------------------------------------------------------


def _public_quiz(row: dict[str, Any]) -> dict[str, Any]:
    questions = kit.loads(row["questions"], []) or []
    out = {
        "id": row["id"],
        "kind": row["kind"],
        "title": row["title"],
        "level": row["level"],
        "mode": row["mode"],
        "status": row["status"],
        "time_limit": row["time_limit"],
        "created_at": row["created_at"],
        "questions": [
            {"prompt": q["prompt"], "options": q.get("options", [])} for q in questions
        ],
    }
    if row["status"] == "done":
        out.update(
            {"score": row["score"], "total": row["total"], "late": bool(row["late"])}
        )
    return out


def create_quiz(
    connection: sqlite3.Connection,
    profile: dict[str, Any],
    deck_id: str,
    count: int = 10,
    *,
    rng: random.Random | None = None,
) -> dict[str, Any]:
    rng = rng or random.SystemRandom()
    deck = store.visible_deck(connection, profile, deck_id)
    rows = store.cards(connection, deck_id)
    backs = list(dict.fromkeys(row["back"] for row in rows))
    if len(rows) < 2 or len(backs) < 2:
        raise ValueError("A quiz needs a deck with at least two different answers.")
    count = max(1, min(int(count or 10), 30, len(rows)))
    questions = []
    for row in rng.sample(rows, count):
        others = [back for back in backs if back != row["back"]]
        options = [row["back"], *rng.sample(others, min(3, len(others)))]
        rng.shuffle(options)
        questions.append(
            {
                "card_id": row["id"],
                "prompt": row["front"],
                "options": options,
                "correct": options.index(row["back"]),
                "explanation": row["example"] or "",
            }
        )
    quiz_id = kit.new_id()
    connection.execute(
        """INSERT INTO ln_quizzes (id, profile_id, kind, deck_id, title, questions, total, created_at)
        VALUES (?, ?, 'deck', ?, ?, ?, ?, ?)""",
        (
            quiz_id,
            profile["id"],
            deck_id,
            f"Quiz: {deck['title']}"[:120],
            kit.dumps(questions),
            len(questions),
            kit.now(),
        ),
    )
    connection.commit()
    return _public_quiz(_quiz(connection, profile, quiz_id))


def _quiz(
    connection: sqlite3.Connection, profile: dict[str, Any], quiz_id: str
) -> dict[str, Any]:
    row = kit.fetch_one(
        connection,
        "SELECT * FROM ln_quizzes WHERE id = ? AND profile_id = ?",
        (quiz_id, profile["id"]),
    )
    if not row:
        raise LookupError("That quiz was not found.")
    return row


def submit_quiz(
    connection: sqlite3.Connection,
    profile: dict[str, Any],
    quiz_id: str,
    answers: Any,
    *,
    now: datetime | None = None,
) -> dict[str, Any]:
    row = _quiz(connection, profile, quiz_id)
    if row["kind"] != "deck":
        raise LookupError("That quiz was not found.")
    if row["status"] != "open":
        raise ValueError(
            "This quiz was already handed in. Start a new one to try again."
        )
    questions = kit.loads(row["questions"], []) or []
    given = list(answers) if isinstance(answers, list) else []
    results, score = [], 0
    for index, question in enumerate(questions):
        raw = given[index] if index < len(given) else None
        chosen = (
            raw
            if isinstance(raw, int)
            and not isinstance(raw, bool)
            and 0 <= raw < len(question["options"])
            else None
        )
        right = chosen == question["correct"]
        score += 1 if right else 0
        results.append(
            {
                "prompt": question["prompt"],
                "options": question["options"],
                "your": chosen,
                "correct": question["correct"],
                "right": right,
                "answer": question["options"][question["correct"]],
            }
        )
    total = len(questions)
    today = today_of(connection, now)
    xp = score * XP_QUIZ_RIGHT + (XP_QUIZ_PERFECT if total and score == total else 0)
    connection.execute(
        "UPDATE ln_quizzes SET answers = ?, score = ?, status = 'done', finished_at = ? WHERE id = ?",
        (kit.dumps([r["your"] for r in results]), score, kit.now(), quiz_id),
    )
    store.add_xp(connection, profile["id"], today.isoformat(), xp)
    kit.log(
        connection,
        APP,
        "quiz",
        f"{profile['name']} scored {score} of {total} in {row['title']}",
    )
    summary = _finish(connection, profile, today, xp)
    return {
        **summary,
        "score": score,
        "total": total,
        "results": results,
        "message": cheer(score, total),
    }


MATHS_LEVELS = {
    "add20": "Adding and taking away to 20",
    "times": "Times tables",
    "division": "Dividing",
    "fractions": "Adding fractions",
    "percent": "Percentages",
}


def maths_question(level: str, rng: random.Random) -> dict[str, str]:
    if level == "add20":
        a = rng.randint(0, 20)
        if rng.random() < 0.5:
            b = rng.randint(0, 20 - a)
            return {"prompt": f"{a} + {b}", "answer": str(a + b)}
        b = rng.randint(0, a)
        return {"prompt": f"{a} − {b}", "answer": str(a - b)}
    if level == "times":
        a, b = rng.randint(2, 12), rng.randint(2, 12)
        return {"prompt": f"{a} × {b}", "answer": str(a * b)}
    if level == "division":
        b, q = rng.randint(2, 12), rng.randint(1, 12)
        return {"prompt": f"{b * q} ÷ {b}", "answer": str(q)}
    if level == "fractions":
        d = rng.choice((2, 3, 4, 5, 6, 8, 10))
        a, b = rng.randint(1, d - 1), rng.randint(1, d - 1)
        total = Fraction(a, d) + Fraction(b, d)
        return {
            "prompt": f"{a}/{d} + {b}/{d}",
            "answer": f"{total.numerator}/{total.denominator}"
            if total.denominator != 1
            else str(total.numerator),
        }
    if level == "percent":
        p = rng.choice((5, 10, 15, 20, 25, 50, 75))
        n = rng.choice(range(20, 420, 20))
        return {
            "prompt": f"{p}% of {n}",
            "answer": str(Fraction(p * n, 100)) if (p * n) % 100 else str(p * n // 100),
        }
    raise ValueError(f"Choose a level: {', '.join(MATHS_LEVELS)}.")


def parse_number(text: Any) -> Fraction | None:
    """Read an answer as a number: 12, 3.5, 3/4, 1 1/4 (mixed), 12% or 0,75 (a decimal comma)."""
    raw = (
        str(text if text is not None else "")
        .strip()
        .replace("−", "-")
        .rstrip("%")
        .strip()
    )
    if not raw:
        return None
    if raw.count(",") == 1 and "." not in raw and len(raw.split(",")[1]) != 3:
        raw = raw.replace(",", ".")
    raw = raw.replace(",", "")
    try:
        parts = raw.split()
        if len(parts) == 2 and "/" in parts[1]:
            whole = Fraction(parts[0])
            return whole + (Fraction(parts[1]) if whole >= 0 else -Fraction(parts[1]))
        return Fraction(raw)
    except (ValueError, ZeroDivisionError):
        return None


def create_maths(
    connection: sqlite3.Connection,
    profile: dict[str, Any],
    raw: dict[str, Any],
    *,
    rng: random.Random | None = None,
) -> dict[str, Any]:
    rng = rng or random.SystemRandom()
    level = kit.choice(raw.get("level"), "the level", tuple(MATHS_LEVELS))
    mode = kit.choice(
        raw.get("mode"), "the mode", ("relaxed", "timed"), default="relaxed"
    )
    count = kit.integer(
        raw.get("count"), "the number of questions", default=10, minimum=3, maximum=30
    )
    questions = [maths_question(level, rng) for _ in range(count)]
    limit = (
        count * (12 if profile.get("age_group") in ("child", "senior") else 8)
        if mode == "timed"
        else 0
    )
    quiz_id = kit.new_id()
    connection.execute(
        """INSERT INTO ln_quizzes (id, profile_id, kind, level, mode, title, questions, total, time_limit, created_at)
        VALUES (?, ?, 'maths', ?, ?, ?, ?, ?, ?, ?)""",
        (
            quiz_id,
            profile["id"],
            level,
            mode,
            MATHS_LEVELS[level],
            kit.dumps(questions),
            count,
            limit,
            kit.now(),
        ),
    )
    connection.commit()
    return _public_quiz(_quiz(connection, profile, quiz_id))


def submit_maths(
    connection: sqlite3.Connection,
    profile: dict[str, Any],
    quiz_id: str,
    answers: Any,
    *,
    now: datetime | None = None,
) -> dict[str, Any]:
    row = _quiz(connection, profile, quiz_id)
    if row["kind"] != "maths":
        raise LookupError("That practice was not found.")
    if row["status"] != "open":
        raise ValueError(
            "This practice was already handed in. Start a new one to try again."
        )
    questions = kit.loads(row["questions"], []) or []
    given = list(answers) if isinstance(answers, list) else []
    results, score = [], 0
    for index, question in enumerate(questions):
        typed = given[index] if index < len(given) else ""
        value = parse_number(typed)
        right = value is not None and value == Fraction(question["answer"])
        score += 1 if right else 0
        results.append(
            {
                "prompt": question["prompt"],
                "your": str(typed or "")[:20],
                "answer": question["answer"],
                "right": right,
            }
        )
    moment = now or kit.utcnow()
    elapsed = (moment - kit.parse_time(row["created_at"])).total_seconds()
    late = bool(row["time_limit"]) and elapsed > row["time_limit"] + 5
    today = today_of(connection, now)
    xp = 0 if late else score * XP_MATHS_RIGHT
    connection.execute(
        "UPDATE ln_quizzes SET answers = ?, score = ?, status = 'done', late = ?, finished_at = ? WHERE id = ?",
        (
            kit.dumps([r["your"] for r in results]),
            score,
            1 if late else 0,
            kit.now(),
            quiz_id,
        ),
    )
    store.add_xp(connection, profile["id"], today.isoformat(), xp)
    kit.log(
        connection,
        APP,
        "maths",
        f"{profile['name']} got {score} of {len(questions)} in {row['title']}",
    )
    summary = _finish(connection, profile, today, xp)
    message = (
        "Time ran out, so no points this time. Try relaxed mode!"
        if late
        else cheer(score, len(questions))
    )
    return {
        **summary,
        "score": score,
        "total": len(questions),
        "late": late,
        "results": results,
        "message": message,
    }


def cheer(score: int, total: int) -> str:
    if not total:
        return "Done!"
    share = score / total
    if share == 1:
        return "Perfect! Every answer right. ⭐"
    if share >= 0.8:
        return "Brilliant work!"
    if share >= 0.5:
        return "Good going: practice makes it stick."
    return "Nice try! Each go makes you better."


def history(
    connection: sqlite3.Connection, profile: dict[str, Any], limit: int = 20
) -> list[dict[str, Any]]:
    rows = kit.fetch_all(
        connection,
        "SELECT * FROM ln_quizzes WHERE profile_id = ? AND status = 'done' ORDER BY finished_at DESC LIMIT ?",
        (profile["id"], limit),
    )
    return [
        {
            "id": r["id"],
            "kind": r["kind"],
            "title": r["title"],
            "score": r["score"],
            "total": r["total"],
            "late": bool(r["late"]),
            "finished_at": r["finished_at"],
        }
        for r in rows
    ]


# ---------------------------------------------------------------------------
# reading
# ---------------------------------------------------------------------------


def add_book(
    connection: sqlite3.Connection, profile: dict[str, Any], raw: dict[str, Any]
) -> dict[str, Any]:
    book_id = kit.new_id()
    connection.execute(
        "INSERT INTO ln_books (id, profile_id, title, author, created_at) VALUES (?, ?, ?, ?, ?)",
        (
            book_id,
            profile["id"],
            kit.text(raw.get("title"), "the book's title", max_len=150, required=True),
            kit.text(raw.get("author"), "the author", max_len=100),
            kit.now(),
        ),
    )
    connection.commit()
    return book(connection, profile, book_id)


def book(
    connection: sqlite3.Connection, profile: dict[str, Any], book_id: str
) -> dict[str, Any]:
    row = kit.fetch_one(
        connection,
        "SELECT * FROM ln_books WHERE id = ? AND profile_id = ?",
        (book_id, profile["id"]),
    )
    if not row:
        raise LookupError("That book was not found.")
    row["minutes"] = kit.count(
        connection,
        "SELECT COALESCE(SUM(minutes), 0) FROM ln_reading WHERE book_id = ?",
        (book_id,),
    )
    return row


def books(
    connection: sqlite3.Connection, profile: dict[str, Any]
) -> list[dict[str, Any]]:
    rows = kit.fetch_all(
        connection,
        """SELECT b.*, (SELECT COALESCE(SUM(minutes), 0) FROM ln_reading r WHERE r.book_id = b.id) AS minutes
        FROM ln_books b WHERE b.profile_id = ? ORDER BY b.status = 'finished', b.created_at DESC""",
        (profile["id"],),
    )
    return rows


def finish_book(
    connection: sqlite3.Connection,
    profile: dict[str, Any],
    book_id: str,
    finished: bool = True,
) -> dict[str, Any]:
    found = book(connection, profile, book_id)
    connection.execute(
        "UPDATE ln_books SET status = ?, finished_at = ? WHERE id = ?",
        (
            "finished" if finished else "reading",
            kit.now() if finished else None,
            book_id,
        ),
    )
    if finished:
        kit.log(
            connection, APP, "book", f"{profile['name']} finished {found['title']} 📖"
        )
    connection.commit()
    return book(connection, profile, book_id)


def update_book(
    connection: sqlite3.Connection,
    profile: dict[str, Any],
    book_id: str,
    raw: dict[str, Any],
) -> dict[str, Any]:
    """Change a book's title or author, and/or mark it finished (``finished: true``) or back to reading."""
    found = book(connection, profile, book_id)
    if "title" in raw or "author" in raw:
        connection.execute(
            "UPDATE ln_books SET title = ?, author = ? WHERE id = ?",
            (
                kit.text(
                    raw.get("title", found["title"]),
                    "the book's title",
                    max_len=150,
                    required=True,
                ),
                kit.text(raw.get("author", found["author"]), "the author", max_len=100),
                book_id,
            ),
        )
        connection.commit()
    if "finished" in raw:
        finished = kit.flag(raw.get("finished"))
        if finished != (found["status"] == "finished"):
            return finish_book(connection, profile, book_id, finished)
    return book(connection, profile, book_id)


def delete_book(
    connection: sqlite3.Connection, profile: dict[str, Any], book_id: str
) -> None:
    book(connection, profile, book_id)
    connection.execute(
        "DELETE FROM ln_reading WHERE book_id = ? AND profile_id = ?",
        (book_id, profile["id"]),
    )
    connection.execute("DELETE FROM ln_books WHERE id = ?", (book_id,))
    connection.commit()


def log_reading(
    connection: sqlite3.Connection,
    profile: dict[str, Any],
    raw: dict[str, Any],
    *,
    now: datetime | None = None,
) -> dict[str, Any]:
    minutes = kit.integer(raw.get("minutes"), "minutes", minimum=1, maximum=600)
    book_id = str(raw.get("book_id") or "")
    if book_id:
        book(connection, profile, book_id)
    today = today_of(connection, now)
    day = today.isoformat()
    already = store.day_row(connection, profile["id"], day)["reading_minutes"]
    connection.execute(
        "INSERT INTO ln_reading (id, profile_id, book_id, day, minutes, at) VALUES (?, ?, ?, ?, ?, ?)",
        (kit.new_id(), profile["id"], book_id, day, minutes, kit.now()),
    )
    store.touch_day(connection, profile["id"], day)
    connection.execute(
        "UPDATE ln_days SET reading_minutes = reading_minutes + ? WHERE profile_id = ? AND day = ?",
        (minutes, profile["id"], day),
    )
    xp = max(0, min(minutes, READING_XP_CAP - min(already, READING_XP_CAP)))
    store.add_xp(connection, profile["id"], day, xp)
    return _finish(connection, profile, today, xp)


# ---------------------------------------------------------------------------
# the household
# ---------------------------------------------------------------------------


def leaderboard(
    connection: sqlite3.Connection, *, now: datetime | None = None
) -> list[dict[str, Any]]:
    """Names and numbers only: streak, XP and cards this week, level."""
    today = today_of(connection, now)
    monday = (today - timedelta(days=today.weekday())).isoformat()
    out = []
    for person in life.profiles(connection):
        row = kit.fetch_one(
            connection,
            "SELECT COALESCE(SUM(xp), 0) AS xp, COALESCE(SUM(cards), 0) AS cards FROM ln_days WHERE profile_id = ? AND day >= ?",
            (person["id"], monday),
        ) or {"xp": 0, "cards": 0}
        total_xp = kit.count(
            connection,
            "SELECT COALESCE(xp, 0) FROM ln_people WHERE profile_id = ?",
            (person["id"],),
        )
        out.append(
            {
                "profile_id": person["id"],
                "name": person["name"],
                "emoji": person["emoji"],
                "color": person["color"],
                "streak": streak(connection, person["id"], today),
                "xp_week": int(row["xp"] or 0),
                "cards_week": int(row["cards"] or 0),
                "level": level_of(total_xp)["level"],
            }
        )
    return sorted(out, key=lambda p: (-p["xp_week"], -p["streak"], p["name"]))


def overview(
    connection: sqlite3.Connection,
    profile: dict[str, Any],
    *,
    now: datetime | None = None,
) -> dict[str, Any]:
    today = today_of(connection, now)
    me = store.person(connection, profile)
    decks = store.decks_for(connection, profile)
    return {
        "settings": {
            k: me[k]
            for k in (
                "goal_kind",
                "goal_value",
                "new_per_day",
                "reviews_per_day",
                "reading_counts",
            )
        },
        "goal": goal_state(connection, profile, today),
        "streak": streak(connection, profile["id"], today),
        "freeze_available": week_key(today) not in me["freeze_weeks"],
        "level": level_of(int(me["xp"])),
        "badges": badges_view(me),
        "counts": counts(connection, profile, today),
        "decks": len(decks),
        "recent": history(connection, profile, 5),
        "today": today.isoformat(),
    }


def streak_at_risk(
    connection: sqlite3.Connection,
    profile: dict[str, Any],
    *,
    now: datetime | None = None,
) -> bool:
    """In the evening, a streak that today's practice would keep but has not yet."""
    moment = local_now(connection, now)
    today = moment.date()
    if moment.hour < 18:
        return False
    if goal_state(connection, profile, today)["done"]:
        return False
    return streak(connection, profile["id"], today) > 0
