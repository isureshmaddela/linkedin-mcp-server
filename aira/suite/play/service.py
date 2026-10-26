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
from aira.suite.play import games, store
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
MAX_PLAYERS = 30
CODE_LETTERS = "ABCDEFGHJKLMNPQRSTUVWXYZ"  # no I or O: easy to read aloud
WATCH_KINDS = ("film", "show", "book", "podcast", "game")
WATCH_STATUSES = ("want", "doing", "done")


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
    return {
        "id": row["id"],
        "game": row["game"],
        "name": games.GAMES[row["game"]]["name"],
        "level": row["level"],
        "mode": row["mode"],
        "daily_date": row["daily_date"],
        "puzzle": kit.loads(row["puzzle"], {}),
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
            day.isoformat() if daily else "",
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
    return public_session(_mine(connection, me, session_id))


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
    name = games.GAMES[row["game"]]["name"]
    if row["daily_date"]:
        kit.log(
            connection,
            APP,
            "daily",
            f"{me['name']} played today's challenge: {points} points",
            actor=me["name"],
        )
    elif best and before >= 0:
        kit.log(
            connection,
            APP,
            "best",
            f"{me['name']} set a new best in {name}: {points}",
            actor=me["name"],
        )
    connection.commit()
    return public_session(_mine(connection, me, session_id))


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
    """Each person's best score (names and numbers only), best first."""
    clauses, params = ["status = 'finished'"], []
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
    if game and game not in games.GAMES:
        raise ValueError("Unknown game.")
    today = local_day(connection, now)
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
            {**b, "name": games.GAMES[b["game"]]["name"], "best": int(b["best"] or 0)}
            for b in bests
        ],
        "recent": [
            {
                "id": r["id"],
                "game": r["game"],
                "name": games.GAMES[r["game"]]["name"],
                "level": r["level"],
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
        maximum=15,
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
    level = kit.choice(
        payload.get("level"),
        "the level",
        ("kids", "easy", "medium", "hard"),
        default="kids" if me.get("age_group") == "child" else "medium",
    )
    if category == "kids":
        level = "kids"
    questions = games.trivia_questions(
        random.Random(secrets.randbits(48)), count, level, category
    )
    room_id = kit.new_id()
    title = (
        kit.text(payload.get("title"), "a title", max_len=60)
        or f"{me['name']}'s trivia"
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
    _add_player(connection, room_id, profile_id=me["id"], moment=moment)
    kit.log(
        connection,
        APP,
        "room",
        f"{me['name']} started live trivia",
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
    players = connection.execute(
        "SELECT COUNT(*) FROM pl_room_players WHERE room_id = ?", (room_id,)
    ).fetchone()[0]
    if players >= MAX_PLAYERS:
        raise ValueError(f"This room is full ({MAX_PLAYERS} players).")
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
        "is_host": bool(viewer_profile and viewer_profile == room["host_id"]),
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
            "category": games.TRIVIA_CATEGORIES.get(question["category"], ""),
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
        }
    out["version"] = (
        f"{out['phase']}:{out['index']}:{len(answered)}:{len(players)}:{sum(p['score'] for p in board)}"
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
    now: datetime | None = None,
) -> tuple[str, dict[str, Any]]:
    """A relative joins through the public link with just a name; the token they get lets them answer."""
    moment = _now(now)
    room = _room(connection, room_id)
    if phase(room, moment)["phase"] in ("finished", "expired"):
        raise ValueError("That game has finished.")
    clean = kit.text(name, "your name", max_len=30, required=True)
    token = secrets.token_urlsafe(18)
    player_id = _add_player(
        connection,
        room_id,
        guest_name=clean,
        token_hash=hash_token(token),
        moment=moment,
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
        clauses.append("(title LIKE ? OR creator LIKE ?)")
        params.extend([f"%{query}%", f"%{query}%"])
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


def add_plan(
    connection: sqlite3.Connection,
    me: dict[str, Any],
    item_id: str,
    payload: dict[str, Any],
) -> dict[str, Any]:
    store.ensure(connection)
    _item(connection, item_id)
    when = kit.moment(payload.get("planned_for"), "the date and time", required=True)
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
        """SELECT * FROM pl_sessions WHERE best = 1 AND status = 'finished' AND profile_id != ? AND finished_at >= ?
        ORDER BY finished_at DESC LIMIT 1""",
        (me["id"], since),
    )
    if not row:
        return None
    return {
        "name": life.name_of(connection, row["profile_id"]),
        "game": games.GAMES[row["game"]]["name"],
        "score": int(row["score"]),
    }
