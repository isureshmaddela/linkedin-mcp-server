"""Play's rules: game sessions checked on the server, the daily challenge, scores, live trivia rooms and the
watch list. Every function takes the person (``me``, a life profile) and refuses what is not theirs."""

from __future__ import annotations

import hashlib
import random
import secrets
import sqlite3
from datetime import date, datetime, timedelta, timezone
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from aira.suite import kit, life
from aira.suite.play import games, quizzes, store
from aira.suite.play.store import APP

MODES = ("relaxed", "timed")
# The daily challenge by weekday (Monday first): one game and level for everyone that day.
DAILY_ROTATION = (
    ("trivia", "medium"),
    ("maths", "medium"),
    ("scramble", "easy"),
    ("memory", "medium"),
    ("sudoku", "easy"),
    ("trivia", "medium"),
    ("memory", "medium"),
)
ROOM_HOURS = 3
REVEAL_SECONDS = 6
MAX_GUESTS = 24  # guests from the public link; household members always get a place
GUESTS_PER_VISITOR = 4  # a family sharing one phone can join a few names, not a crowd
CODE_LETTERS = "ABCDEFGHJKLMNPQRSTUVWXYZ"  # no I or O: easy to read aloud
WATCH_KINDS = ("film", "show", "book", "podcast", "game")
WATCH_STATUSES = ("want", "doing", "done")
CHALLENGE_MAX_PEOPLE = 12
PLAN_GRACE_MINUTES = (
    2  # a time of "now" typed into a form is a minute or so old by the time it is saved
)
MAX_ENTRY_CENTS = (
    100_000  # an entry fee for a quiz night: at most 1,000 in the business currency
)


# ---------------------------------------------------------------------------
# time
# ---------------------------------------------------------------------------


def _zone(connection: sqlite3.Connection) -> ZoneInfo:
    try:
        return ZoneInfo(kit.business(connection).get("timezone") or "UTC")
    except (ZoneInfoNotFoundError, ValueError):
        return ZoneInfo("UTC")


def _now(now: datetime | None) -> datetime:
    return (now or kit.utcnow()).astimezone(timezone.utc).replace(microsecond=0)


def local_day(connection: sqlite3.Connection, now: datetime | None = None) -> date:
    return _now(now).astimezone(_zone(connection)).date()


def local_time(connection: sqlite3.Connection, value: str) -> datetime:
    return kit.parse_time(value).astimezone(_zone(connection))


def _seconds_between(start: str, end: datetime) -> float:
    return max(0.0, (end - kit.parse_time(start)).total_seconds())


# ---------------------------------------------------------------------------
# game sessions
# ---------------------------------------------------------------------------


def default_mode(me: dict[str, Any]) -> str:
    """No clock for children and seniors unless they ask for one."""
    return "relaxed" if me.get("age_group") in ("child", "senior") else "timed"


def daily_game(day: date) -> tuple[str, str]:
    return DAILY_ROTATION[day.weekday()]


def catalog(
    connection: sqlite3.Connection, me: dict[str, Any], now: datetime | None = None
) -> dict[str, Any]:
    day = local_day(connection, now)
    game, level = daily_game(day)
    return {
        "games": [
            {
                "id": game_id,
                "name": spec["name"],
                "tagline": spec["tagline"],
                "levels": [
                    {"id": key, "label": label} for key, label in spec["levels"].items()
                ],
                "suggested": games.suggested_level(game_id, me.get("age_group") or ""),
            }
            for game_id, spec in games.GAMES.items()
        ],
        "default_mode": default_mode(me),
        "suggested_level": "kids"
        if me.get("age_group") == "child"
        else "easy"
        if me.get("age_group") == "senior"
        else "",
        "daily": {
            "date": day.isoformat(),
            "game": game,
            "level": level,
            "name": games.GAMES[game]["name"],
        },
        "categories": [
            {"id": key, "label": label}
            for key, label in games.TRIVIA_CATEGORIES.items()
        ],
    }


def game_name(game: str, level: str = "", titles: dict[str, str] | None = None) -> str:
    """The name to show for a game; a person's quiz shows its title."""
    if game == "quiz":
        return (titles or {}).get(level) or "A family quiz"
    spec = games.GAMES.get(game)
    return spec["name"] if spec else game


def level_label(game: str, level: str) -> str:
    if game == "quiz":
        return "Family quiz"
    spec = games.GAMES.get(game)
    return spec["levels"].get(level, level) if spec else level


def _progress(row: dict[str, Any], secret: dict[str, Any]) -> dict[str, Any]:
    """What a player resuming a game may see again (found pairs, earlier guesses), never the answers."""
    game = row["game"]
    if game == "memory":
        layout = secret["layout"]
        return {
            "matched": {str(i): layout[i] for i in secret["matched"]},
            "moves": len(secret["flips"]) // 2,
            "pending": None
            if secret.get("pending") is None
            else {"index": secret["pending"], "card": layout[secret["pending"]]},
        }
    if game == "word":
        return {
            "guesses": [
                {"word": g, "feedback": games.word_feedback(g, secret["answer"])}
                for g in secret["guesses"]
            ]
        }
    if game == "sudoku":
        return {"hints": int(secret.get("hints") or 0)}
    return {}


def public_session(row: dict[str, Any]) -> dict[str, Any]:
    secret = kit.loads(row["secret"], {}) or {}
    puzzle = kit.loads(row["puzzle"], {}) or {}
    return {
        "id": row["id"],
        "game": row["game"],
        "name": puzzle.get("title") or game_name(row["game"], row["level"]),
        "level": row["level"],
        "level_label": level_label(row["game"], row["level"]),
        "mode": row["mode"],
        "daily_date": row["daily_date"],
        "puzzle": puzzle,
        "progress": _progress(row, secret),
        "status": row["status"],
        "score": int(row["score"] or 0),
        "best": bool(row["best"]),
        "result": kit.loads(row["result"], {}),
        "started_at": row["started_at"],
        "finished_at": row["finished_at"],
    }


def _mine(
    connection: sqlite3.Connection, me: dict[str, Any], session_id: str
) -> dict[str, Any]:
    row = kit.fetch_one(
        connection,
        "SELECT * FROM pl_sessions WHERE id = ? AND profile_id = ?",
        (session_id, me["id"]),
    )
    if not row:
        raise LookupError("That game was not found.")
    return row


def start(
    connection: sqlite3.Connection,
    me: dict[str, Any],
    payload: dict[str, Any],
    *,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Start a game (or return the one already started for today's challenge or today's word)."""
    store.ensure(connection)
    moment = _now(now)
    day = local_day(connection, moment)
    daily = kit.flag(payload.get("daily"))
    if daily:
        game, level = daily_game(day)
        mode = "relaxed"  # the daily challenge is the same for every age: no clock
        seed = games.seed_for(day.isoformat(), "daily", game, level)
        existing = kit.fetch_one(
            connection,
            "SELECT * FROM pl_sessions WHERE profile_id = ? AND daily_date = ?",
            (me["id"], day.isoformat()),
        )
        if existing:
            if existing["status"] == "playing":
                return public_session(existing)
            raise ValueError(
                "You have played today's challenge. Come back tomorrow for a new one!"
            )
    elif payload.get("quiz"):
        quiz = quizzes.visible(connection, me, str(payload.get("quiz")))
        mode = kit.choice(
            payload.get("mode"), "the mode", MODES, default=default_mode(me)
        )
        return create_session(
            connection,
            me,
            "quiz",
            quiz["id"],
            mode,
            secrets.randbits(48),
            moment=moment,
        )
    else:
        game = str(payload.get("game") or "")
        level = games.check_level(game, str(payload.get("level") or ""))
        mode = kit.choice(
            payload.get("mode"), "the mode", MODES, default=default_mode(me)
        )
        if game == "word" and level == "daily":
            mode = "relaxed"
            seed = games.seed_for(day.isoformat(), "word")
            existing = kit.fetch_one(
                connection,
                "SELECT * FROM pl_sessions WHERE profile_id = ? AND game = 'word' AND level = 'daily' AND local_date = ?",
                (me["id"], day.isoformat()),
            )
            if existing:
                if existing["status"] == "playing":
                    return public_session(existing)
                raise ValueError(
                    "You have played today's word. A new one comes tomorrow; try a practice word."
                )
        else:
            seed = secrets.randbits(48)
    return create_session(
        connection,
        me,
        game,
        level,
        mode,
        seed,
        daily_date=day.isoformat() if daily else "",
        moment=moment,
    )


def create_session(
    connection: sqlite3.Connection,
    me: dict[str, Any],
    game: str,
    level: str,
    mode: str,
    seed: int,
    *,
    daily_date: str = "",
    moment: datetime | None = None,
) -> dict[str, Any]:
    """Make the puzzle from the seed and keep its secret on the server. The same game, level and seed always make
    the same puzzle (the daily challenge, a challenge between two people)."""
    moment = _now(moment)
    day = local_day(connection, moment)
    if game == "quiz":
        quiz = quizzes.visible(connection, me, level)
        puzzle, secret = games.make_quiz(
            quizzes.questions_of(quiz), quiz["title"], seed
        )
    else:
        games.check_level(game, level)
        puzzle, secret = games.make(game, level, seed)
    session_id = kit.new_id()
    connection.execute(
        """INSERT INTO pl_sessions (id, profile_id, game, level, mode, seed, daily_date, puzzle, secret, started_at, local_date)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            session_id,
            me["id"],
            game,
            level,
            mode,
            seed,
            daily_date,
            kit.dumps(puzzle),
            kit.dumps(secret),
            kit.iso(moment),
            day.isoformat(),
        ),
    )
    connection.commit()
    return public_session(_mine(connection, me, session_id))


def get_session(
    connection: sqlite3.Connection, me: dict[str, Any], session_id: str
) -> dict[str, Any]:
    store.ensure(connection)
    return with_challenge(connection, public_session(_mine(connection, me, session_id)))


def move(
    connection: sqlite3.Connection,
    me: dict[str, Any],
    session_id: str,
    payload: dict[str, Any],
    *,
    now: datetime | None = None,
) -> dict[str, Any]:
    """One step of a game the server plays along (a card turned, a word guessed, a hint)."""
    store.ensure(connection)
    row = _mine(connection, me, session_id)
    if row["status"] != "playing":
        raise ValueError("This game is over. Start a new one.")
    secret = kit.loads(row["secret"], {})
    result = games.move(row["game"], secret, payload)
    connection.execute(
        "UPDATE pl_sessions SET secret = ? WHERE id = ?",
        (kit.dumps(secret), session_id),
    )
    connection.commit()
    if row["game"] == "word" and result.get("complete"):
        result["session"] = finish(connection, me, session_id, {}, now=now)
    return result


def promotable(
    connection: sqlite3.Connection, profile_id: str, game: str, level: str
) -> bool:
    """Whether a score may be shown to the rest of the family (Today, the activity feed). A quiz written by the
    player does not count (they know the answers), and a private quiz is seen by its writer only."""
    if game != "quiz":
        return True
    row = connection.execute(
        "SELECT owner_id, shared FROM pl_quizzes WHERE id = ?", (level,)
    ).fetchone()
    return bool(row) and bool(row[1]) and row[0] != profile_id


def _best_before(
    connection: sqlite3.Connection,
    profile_id: str,
    game: str,
    level: str,
    session_id: str,
) -> int:
    row = connection.execute(
        "SELECT MAX(score) FROM pl_sessions WHERE profile_id = ? AND game = ? AND level = ? AND status = 'finished' AND id != ?",
        (profile_id, game, level, session_id),
    ).fetchone()
    return int(row[0]) if row and row[0] is not None else -1


def finish(
    connection: sqlite3.Connection,
    me: dict[str, Any],
    session_id: str,
    payload: dict[str, Any],
    *,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Check the answers on the server and record the score (a score in the request is ignored)."""
    store.ensure(connection)
    row = _mine(connection, me, session_id)
    if row["status"] != "playing":
        raise ValueError("This game is already finished.")
    moment = _now(now)
    seconds = _seconds_between(row["started_at"], moment)
    secret = kit.loads(row["secret"], {})
    points, result = games.score(
        row["game"],
        row["level"],
        row["mode"],
        kit.loads(row["puzzle"], {}),
        secret,
        payload,
        seconds,
    )
    result["seconds"] = int(seconds)
    before = _best_before(connection, me["id"], row["game"], row["level"], session_id)
    best = points > 0 and points > before
    connection.execute(
        "UPDATE pl_sessions SET status = 'finished', score = ?, result = ?, best = ?, finished_at = ?, secret = ? WHERE id = ?",
        (
            points,
            kit.dumps(result),
            1 if best else 0,
            kit.iso(moment),
            kit.dumps(secret),
            session_id,
        ),
    )
    name = (kit.loads(row["puzzle"], {}) or {}).get("title") or game_name(row["game"])
    _settle_challenge(connection, me, row, points, moment)
    if row["daily_date"]:
        kit.log(
            connection,
            APP,
            "daily",
            f"{me['name']} played today's challenge: {points} points",
            actor=me["name"],
        )
    elif (
        best
        and before >= 0
        and promotable(connection, me["id"], row["game"], row["level"])
    ):
        kit.log(
            connection,
            APP,
            "best",
            f"{me['name']} set a new best in {name}: {points}",
            actor=me["name"],
        )
    connection.commit()
    return with_challenge(connection, public_session(_mine(connection, me, session_id)))


def with_challenge(
    connection: sqlite3.Connection, session: dict[str, Any]
) -> dict[str, Any]:
    """A session with the challenge it answers, if any (the score to beat and whose it is)."""
    found = challenge_of_session(connection, session["id"])
    return {**session, "challenge": found} if found else session


# ---------------------------------------------------------------------------
# scores, streaks and leaderboards
# ---------------------------------------------------------------------------


def streak(connection: sqlite3.Connection, profile_id: str, today: date) -> int:
    """Days in a row with at least one finished game (today, or up to yesterday if today is not played yet)."""
    days = {
        r[0]
        for r in connection.execute(
            "SELECT DISTINCT local_date FROM pl_sessions WHERE profile_id = ? AND status = 'finished'",
            (profile_id,),
        )
    }
    cursor = today if today.isoformat() in days else today - timedelta(days=1)
    count = 0
    while cursor.isoformat() in days:
        count += 1
        cursor -= timedelta(days=1)
    return count


def _names(connection: sqlite3.Connection) -> dict[str, dict[str, Any]]:
    return {p["id"]: life.public(p) for p in life.profiles(connection)}


def leaderboard(
    connection: sqlite3.Connection, *, game: str = "", level: str = ""
) -> list[dict[str, Any]]:
    """Each person's best score (names and numbers only), best first. Games of a quiz the player wrote do not
    count: they know the answers."""
    clauses: list[str] = [
        "status = 'finished'",
        "NOT (game = 'quiz' AND level IN (SELECT q.id FROM pl_quizzes q WHERE q.owner_id = pl_sessions.profile_id))",
    ]
    params: list[Any] = []
    if game:
        clauses.append("game = ?")
        params.append(game)
    if level:
        clauses.append("level = ?")
        params.append(level)
    rows = kit.fetch_all(
        connection,
        f"SELECT profile_id, MAX(score) AS best, COUNT(*) AS plays FROM pl_sessions WHERE {' AND '.join(clauses)} GROUP BY profile_id",  # noqa: S608
        params,
    )
    people = _names(connection)
    board = [
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
    board.sort(key=lambda b: (-b["best"], b["name"]))
    for rank, entry in enumerate(board, start=1):
        entry["rank"] = rank
    return board


def daily_board(connection: sqlite3.Connection, day: date) -> list[dict[str, Any]]:
    rows = kit.fetch_all(
        connection,
        "SELECT profile_id, score, finished_at FROM pl_sessions WHERE daily_date = ? AND status = 'finished' ORDER BY score DESC, finished_at",
        (day.isoformat(),),
    )
    people = _names(connection)
    out = []
    for rank, r in enumerate((r for r in rows if r["profile_id"] in people), start=1):
        person = people[r["profile_id"]]
        out.append(
            {
                "rank": rank,
                "profile_id": r["profile_id"],
                "name": person["name"],
                "emoji": person["emoji"],
                "score": int(r["score"]),
            }
        )
    return out


def scores(
    connection: sqlite3.Connection,
    me: dict[str, Any],
    *,
    game: str = "",
    now: datetime | None = None,
) -> dict[str, Any]:
    store.ensure(connection)
    if game and game not in games.GAMES and game != "quiz":
        raise ValueError("Unknown game.")
    today = local_day(connection, now)
    titles = quizzes.titles(connection)
    bests = kit.fetch_all(
        connection,
        """SELECT game, level, MAX(score) AS best, COUNT(*) AS plays, MAX(finished_at) AS last
        FROM pl_sessions WHERE profile_id = ? AND status = 'finished' GROUP BY game, level ORDER BY game, level""",
        (me["id"],),
    )
    recent = kit.fetch_all(
        connection,
        "SELECT * FROM pl_sessions WHERE profile_id = ? AND status = 'finished' ORDER BY finished_at DESC LIMIT 15",
        (me["id"],),
    )
    return {
        "streak": streak(connection, me["id"], today),
        "bests": [
            {
                **b,
                "name": game_name(b["game"], b["level"], titles),
                "level_label": level_label(b["game"], b["level"]),
                "best": int(b["best"] or 0),
            }
            for b in bests
        ],
        "recent": [
            {
                "id": r["id"],
                "game": r["game"],
                "name": game_name(r["game"], r["level"], titles),
                "level": r["level"],
                "level_label": level_label(r["game"], r["level"]),
                "mode": r["mode"],
                "score": int(r["score"]),
                "best": bool(r["best"]),
                "finished_at": r["finished_at"],
                "daily": bool(r["daily_date"]),
            }
            for r in recent
        ],
        "family": leaderboard(connection, game=game),
        "game": game,
    }


def daily(
    connection: sqlite3.Connection, me: dict[str, Any], now: datetime | None = None
) -> dict[str, Any]:
    store.ensure(connection)
    day = local_day(connection, now)
    game, level = daily_game(day)
    mine = kit.fetch_one(
        connection,
        "SELECT * FROM pl_sessions WHERE profile_id = ? AND daily_date = ?",
        (me["id"], day.isoformat()),
    )
    return {
        "date": day.isoformat(),
        "game": game,
        "level": level,
        "name": games.GAMES[game]["name"],
        "tagline": games.GAMES[game]["tagline"],
        "mine": public_session(mine) if mine else None,
        "board": daily_board(connection, day),
        "streak": streak(connection, me["id"], day),
    }


# ---------------------------------------------------------------------------
# live trivia rooms
# ---------------------------------------------------------------------------


def _room(connection: sqlite3.Connection, room_id: str) -> dict[str, Any]:
    row = kit.fetch_one(connection, "SELECT * FROM pl_rooms WHERE id = ?", (room_id,))
    if not row:
        raise LookupError("That game room was not found.")
    return row


def _code(connection: sqlite3.Connection, moment: datetime) -> str:
    for _ in range(50):
        code = "".join(secrets.choice(CODE_LETTERS) for _ in range(4))
        taken = connection.execute(
            "SELECT 1 FROM pl_rooms WHERE code = ? AND status != 'finished' AND expires_at > ?",
            (code, kit.iso(moment)),
        ).fetchone()
        if not taken:
            return code
    raise RuntimeError("Could not find a free room code. Try again.")


def create_room(
    connection: sqlite3.Connection,
    me: dict[str, Any],
    payload: dict[str, Any],
    *,
    now: datetime | None = None,
) -> dict[str, Any]:
    store.ensure(connection)
    moment = _now(now)
    gentle = me.get("age_group") in ("child", "senior")
    count = kit.integer(
        payload.get("questions"),
        "the number of questions",
        default=8,
        minimum=3,
        maximum=games.QUIZ_MAX_QUESTIONS if payload.get("quiz") else 15,
    )
    seconds = kit.integer(
        payload.get("seconds"),
        "the seconds per question",
        default=45 if gentle else 30,
        minimum=10,
        maximum=120,
    )
    category = str(payload.get("category") or "").strip()
    if category and category not in games.TRIVIA_CATEGORIES:
        raise ValueError("Choose a category from the list.")
    quiz = (
        quizzes.visible(connection, me, str(payload.get("quiz")))
        if payload.get("quiz")
        else None
    )
    entry_cents = kit.cents_value(
        payload.get("entry_cents"), "the entry fee", default=0, minimum=0
    )
    if entry_cents > MAX_ENTRY_CENTS:
        raise ValueError("The entry fee is too large for a quiz night.")
    if entry_cents and not life.is_adult(me):
        raise PermissionError("Only a grown-up can charge an entry fee.")
    prize = kit.text(payload.get("prize"), "the prize", max_len=120)
    level = kit.choice(
        payload.get("level"),
        "the level",
        ("kids", "easy", "medium", "hard"),
        default="kids" if me.get("age_group") == "child" else "medium",
    )
    if category == "kids":
        level = "kids"
    rng = random.Random(secrets.randbits(48))
    if quiz:
        questions = games.quiz_round(rng, quizzes.questions_of(quiz), quiz["title"])
        if payload.get("questions") not in (None, ""):  # empty: the whole quiz
            questions = questions[:count]
        category = ""
    else:
        questions = games.trivia_questions(rng, count, level, category)
    room_id = kit.new_id()
    title = kit.text(payload.get("title"), "a title", max_len=60) or (
        quiz["title"] if quiz else f"{me['name']}'s trivia"
    )
    connection.execute(
        """INSERT INTO pl_rooms (id, code, host_id, title, category, level, seconds, auto_advance, questions, status, created_at, updated_at, expires_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'lobby', ?, ?, ?)""",
        (
            room_id,
            _code(connection, moment),
            me["id"],
            title,
            category,
            level,
            seconds,
            0
            if payload.get("auto_advance") is False
            or str(payload.get("auto_advance")).lower() in ("0", "false", "no")
            else 1,
            kit.dumps(questions),
            kit.iso(moment),
            kit.iso(moment),
            kit.iso(moment + timedelta(hours=ROOM_HOURS)),
        ),
    )
    if quiz or entry_cents or prize:
        connection.execute(
            "INSERT INTO pl_room_extras (room_id, quiz_id, entry_cents, currency, prize) VALUES (?, ?, ?, ?, ?)",
            (
                room_id,
                quiz["id"] if quiz else "",
                entry_cents,
                kit.business(connection)["currency"],
                prize,
            ),
        )
    if quiz:
        connection.execute(
            "INSERT INTO pl_room_writers (room_id, writer_id) VALUES (?, ?)",
            (room_id, quiz["owner_id"]),
        )
    _add_player(connection, room_id, profile_id=me["id"], moment=moment)
    kit.log(
        connection,
        APP,
        "room",
        f"{me['name']} opened a quiz night: {title}"
        if entry_cents
        else f"{me['name']} started live trivia",
        ref_type="room",
        ref_id=room_id,
        actor=me["name"],
    )
    connection.commit()
    return room_state(
        connection, _room(connection, room_id), viewer_profile=me["id"], now=moment
    )


def _add_player(
    connection: sqlite3.Connection,
    room_id: str,
    *,
    profile_id: str | None = None,
    guest_name: str = "",
    token_hash: str = "",
    moment: datetime,
) -> str:
    """Household members always get a place; guests from the link are capped so they cannot fill the room."""
    if not profile_id:
        guests = connection.execute(
            "SELECT COUNT(*) FROM pl_room_players WHERE room_id = ? AND profile_id IS NULL",
            (room_id,),
        ).fetchone()[0]
        if guests >= MAX_GUESTS:
            raise ValueError(
                f"This game is full: {MAX_GUESTS} guests have joined. Ask the host about the next one."
            )
    player_id = kit.new_id()
    connection.execute(
        "INSERT INTO pl_room_players (id, room_id, profile_id, guest_name, guest_token_hash, joined_at) VALUES (?, ?, ?, ?, ?, ?)",
        (player_id, room_id, profile_id, guest_name, token_hash, kit.iso(moment)),
    )
    return player_id


def phase(room: dict[str, Any], now: datetime) -> dict[str, Any]:
    """Where the room is at ``now``: lobby, question (with seconds left), reveal, finished or expired."""
    total = len(kit.loads(room["questions"], []))
    if now >= kit.parse_time(room["expires_at"]):
        return {
            "phase": "expired",
            "index": int(room["question_index"]),
            "seconds_left": 0,
            "total": total,
        }
    if room["status"] in ("lobby", "finished"):
        return {
            "phase": room["status"],
            "index": int(room["question_index"]),
            "seconds_left": 0,
            "total": total,
        }
    seconds = int(room["seconds"])
    elapsed = max(
        0.0, (now - kit.parse_time(room["question_started_at"])).total_seconds()
    )
    index = int(room["question_index"])
    if room["auto_advance"]:
        step = seconds + REVEAL_SECONDS
        jumps = int(elapsed // step)
        index += jumps
        elapsed -= jumps * step
        if index >= total:
            return {
                "phase": "finished",
                "index": total - 1,
                "seconds_left": 0,
                "total": total,
            }
    if elapsed < seconds:
        return {
            "phase": "question",
            "index": index,
            "seconds_left": int(seconds - elapsed + 0.999),
            "total": total,
        }
    return {"phase": "reveal", "index": index, "seconds_left": 0, "total": total}


def _sync(
    connection: sqlite3.Connection, room: dict[str, Any], now: datetime
) -> dict[str, Any]:
    """Write down where the clock has taken the room (the next question, the end)."""
    state = phase(room, now)
    changes: dict[str, Any] = {}
    if state["phase"] == "finished" and room["status"] != "finished":
        changes["status"] = "finished"
    elif (
        room["status"] == "running"
        and room["auto_advance"]
        and state["index"] > int(room["question_index"])
    ):
        step = int(room["seconds"]) + REVEAL_SECONDS
        jumps = state["index"] - int(room["question_index"])
        started = kit.parse_time(room["question_started_at"]) + timedelta(
            seconds=jumps * step
        )
        changes.update(
            {"question_index": state["index"], "question_started_at": kit.iso(started)}
        )
    if changes:
        changes["updated_at"] = kit.iso(now)
        kit.update_row(connection, "pl_rooms", room["id"], changes)
        connection.commit()
        room = {**room, **changes}
    return room


def _player_for(
    connection: sqlite3.Connection, room_id: str, profile_id: str
) -> dict[str, Any] | None:
    return kit.fetch_one(
        connection,
        "SELECT * FROM pl_room_players WHERE room_id = ? AND profile_id = ?",
        (room_id, profile_id),
    )


def room_state(
    connection: sqlite3.Connection,
    room: dict[str, Any],
    *,
    viewer_profile: str = "",
    viewer_player: str = "",
    now: datetime | None = None,
) -> dict[str, Any]:
    moment = _now(now)
    room = _sync(connection, room, moment)
    state = phase(room, moment)
    questions = kit.loads(room["questions"], [])
    people = _names(connection)
    players = kit.fetch_all(
        connection,
        "SELECT * FROM pl_room_players WHERE room_id = ? ORDER BY joined_at",
        (room["id"],),
    )
    answered = {
        r["player_id"]: r
        for r in kit.fetch_all(
            connection,
            "SELECT * FROM pl_room_answers WHERE room_id = ? AND question_index = ?",
            (room["id"], state["index"]),
        )
    }
    extras = room_extras(connection, room["id"])
    paid = paid_players(connection, room["id"]) if extras["entry_cents"] else {}
    writer = room_writer(connection, room["id"])
    is_host = bool(viewer_profile and viewer_profile == room["host_id"])
    me = None
    if viewer_profile:
        me = next((p for p in players if p["profile_id"] == viewer_profile), None)
    elif viewer_player:
        me = next((p for p in players if p["id"] == viewer_player), None)

    def name(player: dict[str, Any]) -> str:
        if player["profile_id"]:
            return people.get(player["profile_id"], {}).get("name", "Someone")
        return player["guest_name"] or "Guest"

    board = sorted(
        (
            {
                "id": p["id"],
                "name": name(p),
                "emoji": people.get(p["profile_id"] or "", {}).get("emoji", "🙂"),
                "score": int(p["score"]),
                "answered": p["id"] in answered,
                "guest": not p["profile_id"],
                "quizmaster": bool(writer) and p["profile_id"] == writer,
                # who has paid is for the host (and each guest about themselves)
                **(
                    {"paid": _has_paid(p, paid)}
                    if extras["entry_cents"]
                    and (is_host or (me and me["id"] == p["id"]))
                    else {}
                ),
            }
            for p in players
        ),
        key=lambda p: (-p["score"], p["name"]),
    )
    out: dict[str, Any] = {
        "id": room["id"],
        "code": room["code"],
        "title": room["title"],
        "host": people.get(room["host_id"], {}).get("name", "Someone"),
        "is_host": is_host,
        "entry_cents": extras["entry_cents"],
        "currency": extras["currency"],
        "prize": extras["prize"],
        "from_quiz": bool(extras["quiz_id"]),
        "phase": state["phase"],
        "index": state["index"],
        "total": state["total"],
        "seconds": int(room["seconds"]),
        "seconds_left": state["seconds_left"],
        "auto_advance": bool(room["auto_advance"]),
        "players": board,
        "now": kit.iso(moment),
        "me": None,
    }
    if state["phase"] in ("question", "reveal") and 0 <= state["index"] < len(
        questions
    ):
        question = questions[state["index"]]
        out["question"] = {
            "text": question["text"],
            "choices": question["choices"],
            "category": question.get("category_label")
            or games.TRIVIA_CATEGORIES.get(question["category"], ""),
        }
        if state["phase"] == "reveal":
            out["answer"] = question["answer"]
    if me:
        mine = answered.get(me["id"])
        out["me"] = {
            "player_id": me["id"],
            "score": int(me["score"]),
            "choice": mine["choice"] if mine else None,
            "correct": bool(mine["correct"])
            if mine and state["phase"] == "reveal"
            else None,
            "paid": _has_paid(me, paid) if extras["entry_cents"] else True,
            # whoever wrote the quiz runs it but does not answer
            "can_answer": not (writer and me["profile_id"] == writer),
            "reference": f"{room['code']}-{me['id'][:6].upper()}",
        }
    if is_host and extras["entry_cents"]:
        out["takings_cents"] = sum(paid.values())
        out["paid_count"] = len(paid)
    out["version"] = (
        f"{out['phase']}:{out['index']}:{len(answered)}:{len(players)}:{sum(p['score'] for p in board)}:{len(paid)}"
    )
    return out


def join_room(
    connection: sqlite3.Connection,
    me: dict[str, Any],
    room_id: str,
    *,
    now: datetime | None = None,
) -> dict[str, Any]:
    store.ensure(connection)
    moment = _now(now)
    room = _room(connection, room_id)
    state = phase(room, moment)
    if state["phase"] in ("finished", "expired"):
        raise ValueError("That game has finished.")
    if not _player_for(connection, room_id, me["id"]):
        _add_player(connection, room_id, profile_id=me["id"], moment=moment)
        kit.update_row(connection, "pl_rooms", room_id, {"updated_at": kit.iso(moment)})
        connection.commit()
    return room_state(
        connection, _room(connection, room_id), viewer_profile=me["id"], now=moment
    )


def join_by_code(
    connection: sqlite3.Connection,
    me: dict[str, Any],
    code: str,
    *,
    now: datetime | None = None,
) -> dict[str, Any]:
    store.ensure(connection)
    moment = _now(now)
    clean = str(code or "").strip().upper()
    row = kit.fetch_one(
        connection,
        "SELECT * FROM pl_rooms WHERE code = ? AND status != 'finished' AND expires_at > ? ORDER BY created_at DESC",
        (clean, kit.iso(moment)),
    )
    if not row:
        raise LookupError("No game is open with that code. Check the four letters.")
    return join_room(connection, me, row["id"], now=moment)


def hash_token(token: str) -> str:
    return hashlib.sha256(str(token).encode("utf-8")).hexdigest()


def join_guest(
    connection: sqlite3.Connection,
    room_id: str,
    name: str,
    *,
    client: str = "",
    now: datetime | None = None,
) -> tuple[str, dict[str, Any]]:
    """A relative joins through the public link with just a name; the token they get lets them answer.
    ``client`` is the visitor's rate-limit key: one visitor can add only a few names to a room."""
    store.ensure(connection)
    moment = _now(now)
    room = _room(connection, room_id)
    if phase(room, moment)["phase"] in ("finished", "expired"):
        raise ValueError("That game has finished.")
    clean = kit.text(name, "your name", max_len=30, required=True)
    client_hash = hash_token(f"{room_id}:{client}") if client else ""
    if client_hash:
        joined = connection.execute(
            "SELECT COUNT(*) FROM pl_room_guest_clients WHERE room_id = ? AND client_hash = ?",
            (room_id, client_hash),
        ).fetchone()[0]
        if joined >= GUESTS_PER_VISITOR:
            raise ValueError(
                f"You have already joined {GUESTS_PER_VISITOR} names to this game from here."
            )
    token = secrets.token_urlsafe(18)
    player_id = _add_player(
        connection,
        room_id,
        guest_name=clean,
        token_hash=hash_token(token),
        moment=moment,
    )
    if client_hash:
        connection.execute(
            "INSERT INTO pl_room_guest_clients (player_id, room_id, client_hash) VALUES (?, ?, ?)",
            (player_id, room_id, client_hash),
        )
    kit.log(
        connection,
        APP,
        "room_guest",
        f"{clean} joined live trivia",
        ref_type="room",
        ref_id=room_id,
        public=True,
    )
    kit.update_row(connection, "pl_rooms", room_id, {"updated_at": kit.iso(moment)})
    connection.commit()
    return token, room_state(
        connection, _room(connection, room_id), viewer_player=player_id, now=moment
    )


def guest_player(
    connection: sqlite3.Connection, room_id: str, token: str
) -> dict[str, Any]:
    row = kit.fetch_one(
        connection,
        "SELECT * FROM pl_room_players WHERE room_id = ? AND guest_token_hash = ? AND guest_token_hash != ''",
        (room_id, hash_token(token)),
    )
    if not row:
        raise PermissionError("Join the game with your name first.")
    return row


def _host_room(
    connection: sqlite3.Connection, me: dict[str, Any], room_id: str
) -> dict[str, Any]:
    store.ensure(connection)
    room = _room(connection, room_id)
    if room["host_id"] != me["id"]:
        raise PermissionError("Only the host can do that.")
    return room


def start_room(
    connection: sqlite3.Connection,
    me: dict[str, Any],
    room_id: str,
    *,
    now: datetime | None = None,
) -> dict[str, Any]:
    moment = _now(now)
    room = _host_room(connection, me, room_id)
    if phase(room, moment)["phase"] != "lobby":
        raise ValueError("This game has already started.")
    kit.update_row(
        connection,
        "pl_rooms",
        room_id,
        {
            "status": "running",
            "question_index": 0,
            "question_started_at": kit.iso(moment),
            "updated_at": kit.iso(moment),
        },
    )
    connection.commit()
    return room_state(
        connection, _room(connection, room_id), viewer_profile=me["id"], now=moment
    )


def next_question(
    connection: sqlite3.Connection,
    me: dict[str, Any],
    room_id: str,
    *,
    now: datetime | None = None,
) -> dict[str, Any]:
    """The host moves on now (without waiting for the clock)."""
    moment = _now(now)
    room = _sync(connection, _host_room(connection, me, room_id), moment)
    state = phase(room, moment)
    if state["phase"] not in ("question", "reveal"):
        raise ValueError("The game is not running.")
    following = state["index"] + 1
    if following >= state["total"]:
        kit.update_row(
            connection,
            "pl_rooms",
            room_id,
            {"status": "finished", "updated_at": kit.iso(moment)},
        )
    else:
        kit.update_row(
            connection,
            "pl_rooms",
            room_id,
            {
                "question_index": following,
                "question_started_at": kit.iso(moment),
                "updated_at": kit.iso(moment),
            },
        )
    connection.commit()
    return room_state(
        connection, _room(connection, room_id), viewer_profile=me["id"], now=moment
    )


def end_room(
    connection: sqlite3.Connection,
    me: dict[str, Any],
    room_id: str,
    *,
    now: datetime | None = None,
) -> dict[str, Any]:
    moment = _now(now)
    _host_room(connection, me, room_id)
    kit.update_row(
        connection,
        "pl_rooms",
        room_id,
        {"status": "finished", "updated_at": kit.iso(moment)},
    )
    connection.commit()
    return room_state(
        connection, _room(connection, room_id), viewer_profile=me["id"], now=moment
    )


def answer(
    connection: sqlite3.Connection,
    room_id: str,
    player: dict[str, Any],
    choice: Any,
    *,
    now: datetime | None = None,
) -> dict[str, Any]:
    """An answer counts only while the question is open; faster right answers earn a little more."""
    moment = _now(now)
    room = _sync(connection, _room(connection, room_id), moment)
    state = phase(room, moment)
    if state["phase"] != "question":
        raise ValueError("Time is up for this question.")
    if player["profile_id"] and player["profile_id"] == room_writer(
        connection, room_id
    ):
        raise PermissionError(QUIZMASTER_NOTE)
    extras = room_extras(connection, room_id)
    if (
        extras["entry_cents"]
        and not player["profile_id"]
        and not _has_paid(player, paid_players(connection, room_id))
    ):
        raise ValueError(
            "The host has not marked your entry as paid yet. Pay the entry fee, then you can answer."
        )
    questions = kit.loads(room["questions"], [])
    question = questions[state["index"]]
    try:
        picked = int(choice)
    except (TypeError, ValueError):
        raise ValueError("Choose one of the answers.") from None
    if not 0 <= picked < len(question["choices"]):
        raise ValueError("Choose one of the answers.")
    correct = picked == question["answer"]
    points = (
        100 + round(50 * state["seconds_left"] / max(1, int(room["seconds"])))
        if correct
        else 0
    )
    inserted = connection.execute(
        """INSERT OR IGNORE INTO pl_room_answers (room_id, player_id, question_index, choice, correct, points, answered_at)
        VALUES (?, ?, ?, ?, ?, ?, ?)""",
        (
            room_id,
            player["id"],
            state["index"],
            picked,
            1 if correct else 0,
            points,
            kit.iso(moment),
        ),
    ).rowcount
    if not inserted:
        raise ValueError("You already answered this question.")
    if points:
        connection.execute(
            "UPDATE pl_room_players SET score = score + ? WHERE id = ?",
            (points, player["id"]),
        )
    kit.update_row(connection, "pl_rooms", room_id, {"updated_at": kit.iso(moment)})
    connection.commit()
    return {"accepted": True}


def answer_as(
    connection: sqlite3.Connection,
    me: dict[str, Any],
    room_id: str,
    choice: Any,
    *,
    now: datetime | None = None,
) -> dict[str, Any]:
    store.ensure(connection)
    player = _player_for(connection, room_id, me["id"])
    if not player:
        raise PermissionError("Join the game first.")
    answer(connection, room_id, player, choice, now=now)
    return room_state(
        connection, _room(connection, room_id), viewer_profile=me["id"], now=now
    )


QUIZMASTER_NOTE = "You wrote this quiz, so you know the answers. You are the quizmaster: the others answer."


def room_writer(connection: sqlite3.Connection, room_id: str) -> str:
    """The profile that wrote the quiz this room plays ('' for the quiz book)."""
    row = connection.execute(
        "SELECT writer_id FROM pl_room_writers WHERE room_id = ?", (room_id,)
    ).fetchone()
    return row[0] if row else ""


def room_extras(connection: sqlite3.Connection, room_id: str) -> dict[str, Any]:
    row = kit.fetch_one(
        connection, "SELECT * FROM pl_room_extras WHERE room_id = ?", (room_id,)
    )
    return {
        "quiz_id": (row or {}).get("quiz_id", ""),
        "entry_cents": int((row or {}).get("entry_cents") or 0),
        "currency": (row or {}).get("currency") or "",
        "prize": (row or {}).get("prize", ""),
    }


def paid_players(connection: sqlite3.Connection, room_id: str) -> dict[str, int]:
    return {
        r["player_id"]: int(r["amount_cents"])
        for r in kit.fetch_all(
            connection,
            "SELECT player_id, amount_cents FROM pl_room_entries WHERE room_id = ?",
            (room_id,),
        )
    }


def _has_paid(player: dict[str, Any], paid: dict[str, int]) -> bool:
    """Household members play free; a guest has paid once the host marked it."""
    return bool(player["profile_id"]) or player["id"] in paid


def mark_paid(
    connection: sqlite3.Connection,
    me: dict[str, Any],
    room_id: str,
    player_id: str,
    paid: bool,
    *,
    now: datetime | None = None,
) -> dict[str, Any]:
    """The host received (or did not receive) a guest's entry fee. It is recorded as money the business received,
    once per guest; unmarking takes it back out."""
    moment = _now(now)
    room = _host_room(connection, me, room_id)
    extras = room_extras(connection, room_id)
    if not extras["entry_cents"]:
        raise ValueError("This game has no entry fee.")
    player = kit.fetch_one(
        connection,
        "SELECT * FROM pl_room_players WHERE id = ? AND room_id = ?",
        (player_id, room_id),
    )
    if not player:
        raise LookupError("That player is not in this game.")
    if player["profile_id"]:
        raise ValueError("People in the household play for free.")
    if paid:
        inserted = connection.execute(
            "INSERT OR IGNORE INTO pl_room_entries (player_id, room_id, amount_cents, paid_at) VALUES (?, ?, ?, ?)",
            (player_id, room_id, extras["entry_cents"], kit.iso(moment)),
        ).rowcount
        if inserted:
            kit.record_revenue(
                connection,
                APP,
                source_type="quiz_entry",
                source_id=player_id,
                amount_cents=extras["entry_cents"],
                currency_code=extras["currency"],
                label=f"Entry: {room['title']}",
                customer=player["guest_name"],
                at=kit.iso(moment),
            )
            kit.log(
                connection,
                APP,
                "entry_paid",
                f"{player['guest_name']} paid to play {room['title']}",
                ref_type="room",
                ref_id=room_id,
                amount_cents=extras["entry_cents"],
                actor=me["name"],
            )
    else:
        removed = connection.execute(
            "DELETE FROM pl_room_entries WHERE player_id = ? AND room_id = ?",
            (player_id, room_id),
        ).rowcount
        if removed:
            kit.void_revenue(connection, APP, "quiz_entry", player_id)
            # unpaid guests' answers do not count: what they scored while marked paid goes too
            connection.execute(
                "DELETE FROM pl_room_answers WHERE room_id = ? AND player_id = ?",
                (room_id, player_id),
            )
            connection.execute(
                "UPDATE pl_room_players SET score = 0 WHERE id = ?", (player_id,)
            )
    kit.update_row(connection, "pl_rooms", room_id, {"updated_at": kit.iso(moment)})
    connection.commit()
    return room_state(
        connection, _room(connection, room_id), viewer_profile=me["id"], now=moment
    )


def nights(
    connection: sqlite3.Connection, me: dict[str, Any], *, now: datetime | None = None
) -> dict[str, Any]:
    """The quiz nights this person hosted with an entry fee, and what they took."""
    store.ensure(connection)
    rows = kit.fetch_all(
        connection,
        """SELECT r.*, x.entry_cents, x.currency, x.prize FROM pl_rooms r JOIN pl_room_extras x ON x.room_id = r.id
        WHERE r.host_id = ? AND x.entry_cents > 0 ORDER BY r.created_at DESC LIMIT 100""",
        (me["id"],),
    )
    moment = _now(now)
    out = []
    for row in rows:
        players = kit.count(
            connection,
            "SELECT COUNT(*) FROM pl_room_players WHERE room_id = ? AND profile_id IS NULL",
            (row["id"],),
        )
        paid = paid_players(connection, row["id"])
        out.append(
            {
                "id": row["id"],
                "title": row["title"],
                "code": row["code"],
                "created_at": row["created_at"],
                "phase": phase(row, moment)["phase"],
                "entry_cents": int(row["entry_cents"]),
                "currency": row["currency"],
                "prize": row["prize"],
                "guests": players,
                "paid": len(paid),
                "takings_cents": sum(paid.values()),
            }
        )
    return {
        "nights": out,
        "total_cents": sum(n["takings_cents"] for n in out),
        "currency": kit.business(connection)["currency"],
        "can_charge": life.is_adult(me),
        "payment_ready": bool(
            kit.business(connection).get("payment_link")
            or kit.business(connection).get("payment_instructions")
        ),
    }


ENTRY_COLUMNS = ("name", "guest", "joined_at", "paid", "amount", "score")


def entry_rows(
    connection: sqlite3.Connection, me: dict[str, Any], room_id: str
) -> list[dict[str, Any]]:
    """Everyone who played a game, for the host's spreadsheet."""
    room = _host_room(connection, me, room_id)
    extras = room_extras(connection, room["id"])
    paid = paid_players(connection, room["id"])
    people = _names(connection)
    out = []
    for p in kit.fetch_all(
        connection,
        "SELECT * FROM pl_room_players WHERE room_id = ? ORDER BY joined_at",
        (room["id"],),
    ):
        guest = not p["profile_id"]
        out.append(
            {
                "name": p["guest_name"]
                if guest
                else people.get(p["profile_id"], {}).get("name", "Someone"),
                "guest": "yes" if guest else "",
                "joined_at": p["joined_at"],
                "paid": "yes" if p["id"] in paid else ("free" if not guest else "no"),
                "amount": kit.fmt_money(
                    paid.get(p["id"], 0), extras["currency"] or "USD"
                )
                if p["id"] in paid
                else "",
                "score": int(p["score"]),
            }
        )
    return out


def rooms(
    connection: sqlite3.Connection, me: dict[str, Any], *, now: datetime | None = None
) -> list[dict[str, Any]]:
    """Open rooms in the household (lobby or playing, not expired), newest first."""
    store.ensure(connection)
    moment = _now(now)
    rows = kit.fetch_all(
        connection,
        "SELECT * FROM pl_rooms WHERE status != 'finished' AND expires_at > ? ORDER BY created_at DESC LIMIT 20",
        (kit.iso(moment),),
    )
    people = _names(connection)
    out = []
    for row in rows:
        state = phase(row, moment)
        if state["phase"] in ("finished", "expired"):
            continue
        joined = bool(_player_for(connection, row["id"], me["id"]))
        players = connection.execute(
            "SELECT COUNT(*) FROM pl_room_players WHERE room_id = ?", (row["id"],)
        ).fetchone()[0]
        out.append(
            {
                "id": row["id"],
                "code": row["code"],
                "title": row["title"],
                "host": people.get(row["host_id"], {}).get("name", "Someone"),
                "host_id": row["host_id"],
                "phase": state["phase"],
                "players": int(players),
                "joined": joined,
                "created_at": row["created_at"],
            }
        )
    return out


# ---------------------------------------------------------------------------
# watch and read list
# ---------------------------------------------------------------------------


def _item(connection: sqlite3.Connection, item_id: str) -> dict[str, Any]:
    return kit.require_row(connection, "pl_watch", item_id, "item")


def _can_change(me: dict[str, Any], row: dict[str, Any]) -> bool:
    return row["added_by"] == me["id"] or life.is_adult(me)


def _clean_item(
    connection: sqlite3.Connection,
    payload: dict[str, Any],
    current: dict[str, Any] | None = None,
) -> dict[str, Any]:
    merged = {**(current or {}), **payload}
    year = merged.get("year")
    recommended = str(merged.get("recommended_by") or "")
    if recommended:
        life.get(connection, recommended)  # someone in the household
    return {
        "kind": kit.choice(merged.get("kind"), "the kind", WATCH_KINDS, default="film"),
        "title": kit.text(merged.get("title"), "a title", max_len=120, required=True),
        "creator": kit.text(merged.get("creator"), "who made it", max_len=120),
        "year": kit.integer(year, "the year", minimum=1800, maximum=2100)
        if year not in (None, "")
        else None,
        "link": kit.url(merged.get("link"), "the link"),
        "recommended_by": recommended,
    }


def add_item(
    connection: sqlite3.Connection, me: dict[str, Any], payload: dict[str, Any]
) -> dict[str, Any]:
    store.ensure(connection)
    values = _clean_item(connection, payload)
    item_id = kit.new_id()
    connection.execute(
        """INSERT INTO pl_watch (id, kind, title, creator, year, link, recommended_by, added_by, created_at, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            item_id,
            values["kind"],
            values["title"],
            values["creator"],
            values["year"],
            values["link"],
            values["recommended_by"],
            me["id"],
            kit.now(),
            kit.now(),
        ),
    )
    status = payload.get("status")
    if status:
        set_status(connection, me, item_id, {"status": status})
    kit.log(
        connection,
        APP,
        "watch",
        f"{me['name']} added {values['title']} to the list",
        ref_type="watch",
        ref_id=item_id,
        actor=me["name"],
    )
    connection.commit()
    return item_view(connection, me, _item(connection, item_id))


def update_item(
    connection: sqlite3.Connection,
    me: dict[str, Any],
    item_id: str,
    payload: dict[str, Any],
) -> dict[str, Any]:
    store.ensure(connection)
    row = _item(connection, item_id)
    if not _can_change(me, row):
        raise PermissionError("Ask a grown-up, or whoever added it, to change it.")
    kit.update_row(
        connection,
        "pl_watch",
        item_id,
        {**_clean_item(connection, payload, row), "updated_at": kit.now()},
    )
    connection.commit()
    return item_view(connection, me, _item(connection, item_id))


def delete_item(
    connection: sqlite3.Connection, me: dict[str, Any], item_id: str
) -> None:
    store.ensure(connection)
    row = _item(connection, item_id)
    if not _can_change(me, row):
        raise PermissionError("Ask a grown-up, or whoever added it, to remove it.")
    for table in ("pl_watch_status", "pl_watch_plans"):
        connection.execute(f"DELETE FROM {table} WHERE item_id = ?", (item_id,))  # noqa: S608 - fixed names
    connection.execute("DELETE FROM pl_watch WHERE id = ?", (item_id,))
    connection.commit()


def set_status(
    connection: sqlite3.Connection,
    me: dict[str, Any],
    item_id: str,
    payload: dict[str, Any],
) -> dict[str, Any]:
    store.ensure(connection)
    _item(connection, item_id)
    current = (
        kit.fetch_one(
            connection,
            "SELECT * FROM pl_watch_status WHERE item_id = ? AND profile_id = ?",
            (item_id, me["id"]),
        )
        or {}
    )
    status = kit.choice(
        payload.get("status", current.get("status")),
        "the status",
        WATCH_STATUSES,
        default="want",
    )
    rating_raw = payload.get("rating", current.get("rating"))
    rating = (
        kit.integer(rating_raw, "the rating", minimum=1, maximum=5)
        if rating_raw not in (None, "", 0, "0")
        else None
    )
    notes = kit.text(
        payload.get("notes", current.get("notes", "")),
        "notes",
        max_len=500,
        multiline=True,
    )
    connection.execute(
        """INSERT INTO pl_watch_status (item_id, profile_id, status, rating, notes, updated_at) VALUES (?, ?, ?, ?, ?, ?)
        ON CONFLICT(item_id, profile_id) DO UPDATE SET status = excluded.status, rating = excluded.rating, notes = excluded.notes,
        updated_at = excluded.updated_at""",
        (item_id, me["id"], status, rating, notes, kit.now()),
    )
    connection.commit()
    return item_view(connection, me, _item(connection, item_id))


def item_view(
    connection: sqlite3.Connection,
    me: dict[str, Any],
    row: dict[str, Any],
    *,
    now: datetime | None = None,
) -> dict[str, Any]:
    people = _names(connection)
    statuses = kit.fetch_all(
        connection, "SELECT * FROM pl_watch_status WHERE item_id = ?", (row["id"],)
    )
    mine = next((s for s in statuses if s["profile_id"] == me["id"]), None)
    ratings = [int(s["rating"]) for s in statuses if s["rating"]]
    plans = kit.fetch_all(
        connection,
        "SELECT * FROM pl_watch_plans WHERE item_id = ? AND planned_for >= ? ORDER BY planned_for",
        (row["id"], kit.iso(_now(now) - timedelta(hours=6))),
    )
    return {
        "id": row["id"],
        "kind": row["kind"],
        "title": row["title"],
        "creator": row["creator"],
        "year": row["year"],
        "link": row["link"],
        "recommended_by": people.get(row["recommended_by"], {}).get("name", "")
        if row["recommended_by"]
        else "",
        "recommended_by_id": row["recommended_by"],
        "added_by": people.get(row["added_by"], {}).get("name", "Someone"),
        "can_change": _can_change(me, row),
        "mine": {
            "status": mine["status"],
            "rating": mine["rating"],
            "notes": mine["notes"],
        }
        if mine
        else None,
        "others": [
            {
                "name": people[s["profile_id"]]["name"],
                "status": s["status"],
                "rating": s["rating"],
            }
            for s in statuses
            if s["profile_id"] != me["id"] and s["profile_id"] in people
        ],
        "average_rating": round(sum(ratings) / len(ratings), 1) if ratings else None,
        "plans": [_plan_view(connection, p, people) for p in plans],
        "created_at": row["created_at"],
    }


def _plan_view(
    connection: sqlite3.Connection,
    plan: dict[str, Any],
    people: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    who = kit.loads(plan["who"], []) or []
    when = local_time(connection, plan["planned_for"])
    return {
        "id": plan["id"],
        "item_id": plan["item_id"],
        "planned_for": plan["planned_for"],
        "local": when.strftime("%a %d %b, %H:%M"),
        "who": [people[w]["name"] for w in who if w in people],
        "who_ids": who,
        "note": plan["note"],
        "created_by": people.get(plan["created_by"], {}).get("name", "Someone"),
        "created_by_id": plan["created_by"],
    }


def watch_list(
    connection: sqlite3.Connection,
    me: dict[str, Any],
    *,
    kind: str = "",
    status: str = "",
    query: str = "",
) -> list[dict[str, Any]]:
    store.ensure(connection)
    clauses, params = [], []
    if kind:
        clauses.append("kind = ?")
        params.append(kit.choice(kind, "the kind", WATCH_KINDS))
    if query:
        clauses.append("(title LIKE ? ESCAPE '!' OR creator LIKE ? ESCAPE '!')")
        pattern = query.replace("!", "!!").replace("%", "!%").replace("_", "!_")
        params.extend([f"%{pattern}%", f"%{pattern}%"])
    where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    rows = kit.fetch_all(
        connection,
        f"SELECT * FROM pl_watch {where} ORDER BY created_at DESC LIMIT 300",
        params,
    )  # noqa: S608
    items = [item_view(connection, me, row) for row in rows]
    if status:
        wanted = kit.choice(status, "the status", WATCH_STATUSES)
        items = [
            i
            for i in items
            if (i["mine"] or {}).get("status", "want" if wanted == "want" else "")
            == wanted
        ]
    return items


def search(
    connection: sqlite3.Connection,
    me: dict[str, Any],
    query: str,
    *,
    limit: int = 8,
    now: datetime | None = None,
) -> dict[str, Any]:
    """What matches ``query`` in Play, for the search box across the top: games (name, tagline), the quizzes this
    person may play (title, about, who wrote it; a private quiz is never shown to anyone else), the watch list (title,
    maker) and the open live games (title, room code, host). Up to ``limit`` of each kind; case does not matter."""
    store.ensure(connection)
    text = " ".join(str(query or "").split())[:80]
    needle = text.casefold()
    out: dict[str, Any] = {
        "query": text,
        "games": [],
        "quizzes": [],
        "watch": [],
        "rooms": [],
    }
    if not needle:
        return out
    hit = lambda *parts: any(needle in str(p or "").casefold() for p in parts)  # noqa: E731
    out["games"] = [
        {"id": game_id, "name": spec["name"], "tagline": spec["tagline"]}
        for game_id, spec in games.GAMES.items()
        if hit(spec["name"], spec["tagline"])
    ][:limit]
    people = _names(connection)
    rows = kit.fetch_all(
        connection,
        "SELECT * FROM pl_quizzes WHERE owner_id = ? OR shared = 1 ORDER BY updated_at DESC LIMIT 200",
        (me["id"],),
    )
    out["quizzes"] = [
        quizzes.view(connection, me, row, people=people)
        for row in rows
        if hit(
            row["title"],
            row["about"],
            people.get(row["owner_id"], {}).get("name", ""),
        )
    ][:limit]
    pattern = text.replace("!", "!!").replace("%", "!%").replace("_", "!_")
    out["watch"] = [
        {
            "id": row["id"],
            "kind": row["kind"],
            "title": row["title"],
            "creator": row["creator"],
            "year": row["year"],
        }
        for row in kit.fetch_all(
            connection,
            "SELECT id, kind, title, creator, year FROM pl_watch WHERE title LIKE ? ESCAPE '!' OR creator LIKE ? ESCAPE '!' "
            "ORDER BY created_at DESC LIMIT ?",
            (f"%{pattern}%", f"%{pattern}%", limit),
        )
    ]
    out["rooms"] = [
        {
            key: room[key]
            for key in ("id", "code", "title", "host", "phase", "players", "joined")
        }
        for room in rooms(connection, me, now=now)
        if hit(room["title"], room["code"], room["host"])
    ][:limit]
    return out


WATCH_COLUMNS = (
    "kind",
    "title",
    "by",
    "year",
    "link",
    "recommended_by",
    "added_by",
    "my_status",
    "my_rating",
    "family_rating",
    "my_notes",
)


def watch_rows(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    labels = {"want": "Want to", "doing": "In progress", "done": "Finished"}
    return [
        {
            "kind": item["kind"],
            "title": item["title"],
            "by": item["creator"],
            "year": item["year"] or "",
            "link": item["link"],
            "recommended_by": item["recommended_by"],
            "added_by": item["added_by"],
            "my_status": labels.get((item["mine"] or {}).get("status", ""), ""),
            "my_rating": (item["mine"] or {}).get("rating") or "",
            "family_rating": item["average_rating"] or "",
            "my_notes": (item["mine"] or {}).get("notes", ""),
        }
        for item in items
    ]


def pick(
    connection: sqlite3.Connection,
    me: dict[str, Any],
    *,
    kind: str = "",
    together: bool = False,
) -> dict[str, Any] | None:
    """Something for tonight: a random item I want to enjoy (with ``together``, one nobody has finished yet),
    preferring what the family rates highly."""
    items = watch_list(connection, me, kind=kind)
    candidates = [
        i
        for i in items
        if (i["mine"] or {}).get("status", "want") != "done"
        and not (together and any(o["status"] == "done" for o in i["others"]))
    ]
    if not candidates:
        return None
    weights = [1 + (i["average_rating"] or 0) for i in candidates]
    return random.choices(candidates, weights=weights, k=1)[0]


def add_plan(
    connection: sqlite3.Connection,
    me: dict[str, Any],
    item_id: str,
    payload: dict[str, Any],
) -> dict[str, Any]:
    store.ensure(connection)
    _item(connection, item_id)
    when = kit.moment(payload.get("planned_for"), "the date and time", required=True)
    if kit.parse_time(when) < kit.utcnow() - timedelta(minutes=PLAN_GRACE_MINUTES):
        raise ValueError("Choose a time that has not passed yet.")
    who = [str(w) for w in (payload.get("who") or []) if w]
    for person in who:
        life.get(connection, person)
    if me["id"] not in who:
        who.insert(0, me["id"])
    plan_id = kit.new_id()
    connection.execute(
        "INSERT INTO pl_watch_plans (id, item_id, planned_for, who, note, created_by, created_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
        (
            plan_id,
            item_id,
            when,
            kit.dumps(who[:12]),
            kit.text(payload.get("note"), "a note", max_len=200),
            me["id"],
            kit.now(),
        ),
    )
    kit.log(
        connection,
        APP,
        "plan",
        f"{me['name']} planned to watch together",
        ref_type="watch",
        ref_id=item_id,
        actor=me["name"],
    )
    connection.commit()
    return item_view(connection, me, _item(connection, item_id))


def delete_plan(
    connection: sqlite3.Connection, me: dict[str, Any], plan_id: str
) -> None:
    store.ensure(connection)
    plan = kit.require_row(connection, "pl_watch_plans", plan_id, "plan")
    if plan["created_by"] != me["id"] and not life.is_adult(me):
        raise PermissionError("Ask whoever made the plan to change it.")
    connection.execute("DELETE FROM pl_watch_plans WHERE id = ?", (plan_id,))
    connection.commit()


def plans_today(
    connection: sqlite3.Connection, me: dict[str, Any], *, now: datetime | None = None
) -> list[dict[str, Any]]:
    """Watch-together plans today that include this person (or that they made)."""
    store.ensure(connection)
    day = local_day(connection, now)
    zone = _zone(connection)
    start = datetime.combine(day, datetime.min.time(), tzinfo=zone).astimezone(
        timezone.utc
    )
    end = start + timedelta(days=1)
    rows = kit.fetch_all(
        connection,
        """SELECT p.*, w.title FROM pl_watch_plans p JOIN pl_watch w ON w.id = p.item_id
        WHERE p.planned_for >= ? AND p.planned_for < ? ORDER BY p.planned_for""",
        (kit.iso(start), kit.iso(end)),
    )
    people = _names(connection)
    out = []
    for row in rows:
        who = kit.loads(row["who"], []) or []
        if me["id"] not in who and row["created_by"] != me["id"]:
            continue
        out.append({**_plan_view(connection, row, people), "title": row["title"]})
    return out


def recent_best(
    connection: sqlite3.Connection, me: dict[str, Any], *, now: datetime | None = None
) -> dict[str, Any] | None:
    """The newest personal best someone else in the family set in the last two days."""
    since = kit.iso(_now(now) - timedelta(days=2))
    row = kit.fetch_one(
        connection,
        """SELECT s.* FROM pl_sessions s WHERE s.best = 1 AND s.status = 'finished' AND s.profile_id != ?
        AND s.finished_at >= ? AND (s.game != 'quiz' OR EXISTS (SELECT 1 FROM pl_quizzes q
        WHERE q.id = s.level AND q.shared = 1 AND q.owner_id != s.profile_id))
        ORDER BY s.finished_at DESC LIMIT 1""",
        (me["id"], since),
    )
    if not row:
        return None
    return {
        "name": life.name_of(connection, row["profile_id"]),
        "game": game_name(row["game"], row["level"], quizzes.titles(connection)),
        "score": int(row["score"]),
    }


def history_rows(
    connection: sqlite3.Connection, me: dict[str, Any]
) -> list[dict[str, Any]]:
    """Every game this person finished, newest first (for the spreadsheet download)."""
    store.ensure(connection)
    titles = quizzes.titles(connection)
    rows = kit.fetch_all(
        connection,
        "SELECT * FROM pl_sessions WHERE profile_id = ? AND status = 'finished' ORDER BY finished_at DESC LIMIT 5000",
        (me["id"],),
    )
    out = []
    for r in rows:
        result = kit.loads(r["result"], {}) or {}
        out.append(
            {
                "date": r["local_date"],
                "finished_at": r["finished_at"],
                "game": game_name(r["game"], r["level"], titles),
                "level": level_label(r["game"], r["level"]),
                "mode": r["mode"],
                "daily": "yes" if r["daily_date"] else "",
                "score": int(r["score"]),
                "personal_best": "yes" if r["best"] else "",
                "right": f"{result['correct']} of {result['total']}"
                if "total" in result
                else "",
                "seconds": result.get("seconds", ""),
            }
        )
    return out


HISTORY_COLUMNS = (
    "date",
    "finished_at",
    "game",
    "level",
    "mode",
    "daily",
    "score",
    "personal_best",
    "right",
    "seconds",
)


# ---------------------------------------------------------------------------
# challenges: "beat my score" on the very same puzzle
# ---------------------------------------------------------------------------


def challenge(
    connection: sqlite3.Connection,
    me: dict[str, Any],
    payload: dict[str, Any],
    *,
    now: datetime | None = None,
) -> list[dict[str, Any]]:
    """Challenge people in the household to beat my score on the puzzle I just played (same seed, same puzzle)."""
    store.ensure(connection)
    moment = _now(now)
    row = _mine(connection, me, str(payload.get("session_id") or ""))
    if row["status"] != "finished":
        raise ValueError("Finish the game first, then challenge someone.")
    if row["daily_date"] or (row["game"] == "word" and row["level"] == "daily"):
        raise ValueError(
            "Everyone already gets today's puzzle. Challenge someone with another game."
        )
    raw = payload.get("to")
    people = [str(p) for p in (raw if isinstance(raw, list) else [raw]) if p]
    people = list(dict.fromkeys(people))
    if not people:
        raise ValueError("Choose who to challenge.")
    if len(people) > CHALLENGE_MAX_PEOPLE:
        raise ValueError(f"Challenge at most {CHALLENGE_MAX_PEOPLE} people at once.")
    made = []
    if (
        row["game"] == "quiz"
        and not quizzes.visible(connection, me, row["level"])["shared"]
    ):
        raise ValueError(
            "Share this quiz with the family first, then challenge someone."
        )
    if me["id"] in people:
        raise ValueError("You cannot challenge yourself. Choose someone else.")
    for person in people:
        other = life.get(connection, person)
        challenge_id = kit.new_id()
        inserted = connection.execute(
            """INSERT OR IGNORE INTO pl_challenges (id, from_id, to_id, game, level, mode, seed, from_session, from_score, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                challenge_id,
                me["id"],
                other["id"],
                row["game"],
                row["level"],
                row["mode"],
                int(row["seed"]),
                row["id"],
                int(row["score"]),
                kit.iso(moment),
            ),
        ).rowcount
        if inserted:
            made.append(challenge_id)
    if made:
        name = (kit.loads(row["puzzle"], {}) or {}).get("title") or game_name(
            row["game"]
        )
        kit.log(
            connection,
            APP,
            "challenge",
            f"{me['name']} challenged the family to beat {int(row['score'])} in {name}",
            ref_type="challenge",
            ref_id=made[0],
            actor=me["name"],
        )
    connection.commit()
    return challenges(connection, me, now=moment)["outgoing"]


def _challenge_view(
    row: dict[str, Any],
    me: dict[str, Any],
    people: dict[str, dict[str, Any]],
    titles: dict[str, str],
) -> dict[str, Any]:
    incoming = row["to_id"] == me["id"]
    other = people.get(row["from_id"] if incoming else row["to_id"], {})
    done = row["status"] == "done"
    mine = row["to_score"] if incoming else row["from_score"]
    theirs = row["from_score"] if incoming else row["to_score"]
    outcome = ""
    if done:
        outcome = "won" if mine > theirs else "lost" if mine < theirs else "draw"
    return {
        "id": row["id"],
        "incoming": incoming,
        "who": other.get("name", "Someone"),
        "who_emoji": other.get("emoji", ""),
        "game": row["game"],
        "name": game_name(row["game"], row["level"], titles),
        "level_label": level_label(row["game"], row["level"]),
        "mode": row["mode"],
        "target": int(row["from_score"]),
        "score": None if row["to_score"] is None else int(row["to_score"]),
        "status": row["status"],
        "started": bool(row["to_session"]),
        "outcome": outcome,
        "created_at": row["created_at"],
        "done_at": row["done_at"],
    }


def challenges(
    connection: sqlite3.Connection, me: dict[str, Any], *, now: datetime | None = None
) -> dict[str, Any]:
    store.ensure(connection)
    people = _names(connection)
    titles = quizzes.titles(connection)
    open_rows = kit.fetch_all(
        connection,
        "SELECT * FROM pl_challenges WHERE to_id = ? AND status = 'open' ORDER BY created_at DESC LIMIT 30",
        (me["id"],),
    )
    done_rows = kit.fetch_all(
        connection,
        """SELECT * FROM pl_challenges WHERE (to_id = ? OR from_id = ?) AND status = 'done'
        ORDER BY done_at DESC LIMIT 10""",
        (me["id"], me["id"]),
    )
    sent = kit.fetch_all(
        connection,
        "SELECT * FROM pl_challenges WHERE from_id = ? AND status = 'open' ORDER BY created_at DESC LIMIT 30",
        (me["id"],),
    )
    return {
        "incoming": [_challenge_view(r, me, people, titles) for r in open_rows],
        "outgoing": [_challenge_view(r, me, people, titles) for r in sent],
        "results": [_challenge_view(r, me, people, titles) for r in done_rows],
    }


def play_challenge(
    connection: sqlite3.Connection,
    me: dict[str, Any],
    challenge_id: str,
    *,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Start (or carry on) the challenged game: the same puzzle the challenger played."""
    store.ensure(connection)
    row = kit.fetch_one(
        connection,
        "SELECT * FROM pl_challenges WHERE id = ? AND to_id = ?",
        (challenge_id, me["id"]),
    )
    if not row:
        raise LookupError("That challenge was not found.")
    if row["status"] != "open":
        raise ValueError("You have already taken this challenge.")
    if row["to_session"]:
        existing = kit.fetch_one(
            connection,
            "SELECT * FROM pl_sessions WHERE id = ? AND profile_id = ?",
            (row["to_session"], me["id"]),
        )
        if existing and existing["status"] == "playing":
            return public_session(existing)
    session = create_session(
        connection,
        me,
        row["game"],
        row["level"],
        row["mode"],
        int(row["seed"]),
        moment=now,
    )
    kit.update_row(
        connection, "pl_challenges", row["id"], {"to_session": session["id"]}
    )
    connection.commit()
    return {**session, "challenge": {"id": row["id"], "target": int(row["from_score"])}}


def decline_challenge(
    connection: sqlite3.Connection, me: dict[str, Any], challenge_id: str
) -> None:
    store.ensure(connection)
    found = connection.execute(
        "DELETE FROM pl_challenges WHERE id = ? AND (to_id = ? OR from_id = ?) AND status = 'open'",
        (challenge_id, me["id"], me["id"]),
    ).rowcount
    if not found:
        raise LookupError("That challenge was not found.")
    connection.commit()


def _settle_challenge(
    connection: sqlite3.Connection,
    me: dict[str, Any],
    session: dict[str, Any],
    points: int,
    moment: datetime,
) -> None:
    row = kit.fetch_one(
        connection,
        "SELECT * FROM pl_challenges WHERE to_session = ? AND to_id = ? AND status = 'open'",
        (session["id"], me["id"]),
    )
    if not row:
        return
    kit.update_row(
        connection,
        "pl_challenges",
        row["id"],
        {"status": "done", "to_score": points, "done_at": kit.iso(moment)},
    )
    challenger = life.name_of(connection, row["from_id"])
    target = int(row["from_score"])
    name = (kit.loads(session["puzzle"], {}) or {}).get("title") or game_name(
        session["game"]
    )
    verdict = (
        f"{me['name']} beat {challenger}"
        if points > target
        else f"{challenger} held on against {me['name']}"
        if points < target
        else f"{me['name']} drew with {challenger}"
    )
    kit.log(
        connection,
        APP,
        "challenge_done",
        f"{verdict} in {name}: {points} to {target}",
        ref_type="challenge",
        ref_id=row["id"],
        actor=me["name"],
    )


def challenge_of_session(
    connection: sqlite3.Connection, session_id: str
) -> dict[str, Any] | None:
    row = kit.fetch_one(
        connection,
        "SELECT * FROM pl_challenges WHERE to_session = ?",
        (session_id,),
    )
    if not row:
        return None
    return {
        "id": row["id"],
        "target": int(row["from_score"]),
        "who": life.name_of(connection, row["from_id"]),
        "status": row["status"],
    }


def challenges_pulse(
    connection: sqlite3.Connection, me: dict[str, Any], *, now: datetime | None = None
) -> tuple[int, list[dict[str, Any]]]:
    """Open challenges for the badge, and the new ones (and fresh results of mine) as alerts."""
    fresh = kit.iso(_now(now) - timedelta(minutes=30))
    incoming = kit.fetch_all(
        connection,
        "SELECT * FROM pl_challenges WHERE to_id = ? AND status = 'open' ORDER BY created_at DESC LIMIT 30",
        (me["id"],),
    )
    titles = quizzes.titles(connection) if incoming else {}
    alerts = [
        {
            "id": f"play-challenge:{row['id']}",
            "title": f"{life.name_of(connection, row['from_id'])} challenged you",
            "body": f"Beat {row['from_score']} points in {game_name(row['game'], row['level'], titles)}. "
            "Open Play to take it on.",
            "tone": "good",
            "speak": True,
        }
        for row in incoming
        if row["created_at"] >= fresh and not row["to_session"]
    ]
    answered = kit.fetch_all(
        connection,
        "SELECT * FROM pl_challenges WHERE from_id = ? AND status = 'done' AND done_at >= ? ORDER BY done_at DESC LIMIT 5",
        (me["id"], fresh),
    )
    for row in answered:
        theirs, mine = int(row["to_score"] or 0), int(row["from_score"])
        who = life.name_of(connection, row["to_id"])
        alerts.append(
            {
                "id": f"play-challenge-done:{row['id']}",
                "title": f"{who} took your challenge",
                "body": f"{who} scored {theirs}; you scored {mine}. "
                + (
                    "They won this time!"
                    if theirs > mine
                    else "You are still the champion!"
                    if theirs < mine
                    else "It's a draw!"
                ),
                "tone": "good",
            }
        )
    return len(incoming), alerts
