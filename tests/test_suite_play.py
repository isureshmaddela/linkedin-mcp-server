"""Play (aira/suite/play): fair games checked on the server, the daily challenge, live trivia, the watch list."""

from __future__ import annotations

import json
import random
import sqlite3
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone

import pytest
from cloud_support import build_org, sign_in
from life_support import Household

import auto_apply_assistant as local
import test_careers
from aira import suite
from aira.suite import kit, life, public
from aira.suite.play import games, service
from aira.suite.play import play_routes as routes

env = test_careers.env
api = test_careers.api
stub = test_careers.stub

T0 = datetime(2026, 10, 26, 9, 0, 0, tzinfo=timezone.utc)  # a Monday


class Req:
    def __init__(self, header: str = "", email: str = "pat@home.example") -> None:
        self._header = header
        self.user = {"email": email}

    def header(self, name: str, default: str = "") -> str:
        return self._header if name == "X-Aira-Profile" else default


@pytest.fixture(autouse=True)
def no_live_model():
    previous = suite.gateway()

    class NoModel:
        def model(self, prompt):
            return None

    suite.set_gateway(NoModel())
    yield
    suite.set_gateway(previous)


@pytest.fixture
def db(tmp_path):
    kit.forget_schema()
    public.set_index(public._MemoryIndex())
    connection = sqlite3.connect(tmp_path / "play.sqlite3")
    connection.row_factory = sqlite3.Row
    yield connection
    connection.close()


@pytest.fixture
def people(db):
    owner = life.current(Req(), db)
    kid = life.add(db, owner, {"name": "Mia", "age_group": "child"})
    gran = life.add(db, owner, {"name": "Rosa", "age_group": "senior"})
    return owner, kid, gran


def secret_of(db, session_id):
    return kit.loads(
        db.execute(
            "SELECT secret FROM pl_sessions WHERE id = ?", (session_id,)
        ).fetchone()[0]
    )


# ---------------------------------------------------------------------------
# puzzles
# ---------------------------------------------------------------------------


def test_every_puzzle_is_the_same_for_the_same_seed():
    for game, spec in games.GAMES.items():
        for level in spec["levels"]:
            first = games.make(game, level, 1234)
            assert games.make(game, level, 1234) == first, (game, level)
            if game != "word":
                assert games.make(game, level, 99) != first, (game, level)
    assert len(games.TRIVIA) >= 120
    assert len({q[2] for q in games.TRIVIA}) == len(games.TRIVIA)
    for _cat, _level, _text, right, wrong in games.TRIVIA:
        assert right not in wrong and len(set(wrong)) == 3


@pytest.mark.parametrize("level,size", [("kids", 4), ("easy", 9), ("medium", 9)])
def test_sudoku_puzzles_have_exactly_one_solution(level, size):
    for seed in range(3):
        puzzle, solution = games.make_sudoku(random.Random(seed), level)
        assert len(puzzle) == size * size and games.sudoku_valid(solution, size)
        assert all(v == 0 or v == solution[i] for i, v in enumerate(puzzle))
        assert games.count_solutions(puzzle, size) == 1


def test_word_feedback_counts_repeated_letters_properly():
    assert games.word_feedback("apple", "apple") == ["correct"] * 5
    assert games.word_feedback("allee", "apple") == [
        "correct",
        "present",
        "absent",
        "absent",
        "correct",
    ]
    assert games.word_feedback("speed", "abide") == [
        "absent",
        "absent",
        "present",
        "absent",
        "present",
    ]
    assert games.word_feedback("sheep", "spoon") == [
        "correct",
        "absent",
        "absent",
        "absent",
        "present",
    ]


# ---------------------------------------------------------------------------
# scores are worked out on the server
# ---------------------------------------------------------------------------


def test_a_score_sent_by_the_browser_is_ignored(db, people):
    owner, _kid, _gran = people
    session = service.start(
        db, owner, {"game": "trivia", "level": "easy", "mode": "relaxed"}, now=T0
    )
    assert "answers" not in json.dumps(session["puzzle"])
    answers = secret_of(db, session["id"])["answers"]
    wrong = [(a + 1) % 4 for a in answers[:3]] + answers[3:]
    with pytest.raises(ValueError):
        service.finish(
            db, owner, session["id"], {"answers": answers[:5], "score": 99999}, now=T0
        )
    done = service.finish(
        db,
        owner,
        session["id"],
        {"answers": wrong, "score": 99999},
        now=T0 + timedelta(seconds=40),
    )
    assert done["score"] == 700 and done["result"]["correct"] == 7
    with pytest.raises(ValueError):
        service.finish(db, owner, session["id"], {"answers": answers}, now=T0)
    # Someone else cannot read or finish my game.
    other = life.get(db, people[1]["id"])
    with pytest.raises(LookupError):
        service.get_session(db, other, session["id"])


def test_memory_is_played_card_by_card_on_the_server(db, people):
    owner = people[0]
    session = service.start(
        db, owner, {"game": "memory", "level": "easy", "mode": "relaxed"}, now=T0
    )
    assert "layout" not in json.dumps(session)
    with pytest.raises(ValueError):
        service.finish(db, owner, session["id"], {}, now=T0)  # cannot claim a finish
    with pytest.raises(ValueError):
        service.move(db, owner, session["id"], {"action": "flip", "index": 99})
    layout = secret_of(db, session["id"])["layout"]
    pairs: dict[str, list[int]] = {}
    for index, card in enumerate(layout):
        pairs.setdefault(card, []).append(index)
    last = None
    for a, b in pairs.values():
        service.move(db, owner, session["id"], {"action": "flip", "index": a})
        last = service.move(db, owner, session["id"], {"action": "flip", "index": b})
        assert last["matched"] is True
    assert last["complete"] is True
    with pytest.raises(ValueError):
        service.move(
            db, owner, session["id"], {"action": "flip", "index": a}
        )  # already found
    done = service.finish(db, owner, session["id"], {}, now=T0 + timedelta(seconds=60))
    assert (
        done["score"] == 1000
        and done["result"]["perfect"] is True
        and done["best"] is True
    )


def test_the_word_game_finishes_itself(db, people):
    owner = people[0]
    session = service.start(db, owner, {"game": "word", "level": "practice"}, now=T0)
    answer = secret_of(db, session["id"])["answer"]
    with pytest.raises(ValueError):
        service.move(db, owner, session["id"], {"action": "guess", "word": "ab1de"})
    wrong = next(w for w in games.WORDS if w != answer)
    first = service.move(db, owner, session["id"], {"action": "guess", "word": wrong})
    assert (
        first["solved"] is False and first["tries_left"] == 5 and "answer" not in first
    )
    solved = service.move(db, owner, session["id"], {"action": "guess", "word": answer})
    assert (
        solved["solved"]
        and solved["session"]["score"] == 500
        and solved["session"]["status"] == "finished"
    )
    # Running out of tries shows the word and scores nothing.
    lost = service.start(db, owner, {"game": "word", "level": "practice"}, now=T0)
    hidden = secret_of(db, lost["id"])["answer"]
    guess = next(w for w in games.WORDS if w != hidden)
    for _ in range(6):
        result = service.move(db, owner, lost["id"], {"action": "guess", "word": guess})
    assert result["answer"] == hidden and result["session"]["score"] == 0


def test_word_of_the_day_is_one_try_and_the_same_for_everyone(db, people):
    owner, kid, _gran = people
    mine = service.start(db, owner, {"game": "word", "level": "daily"}, now=T0)
    theirs = service.start(db, kid, {"game": "word", "level": "daily"}, now=T0)
    assert secret_of(db, mine["id"])["answer"] == secret_of(db, theirs["id"])["answer"]
    assert (
        service.start(db, owner, {"game": "word", "level": "daily"}, now=T0)["id"]
        == mine["id"]
    )


def test_sudoku_checks_the_grid_and_hints_cost_points(db, people):
    owner = people[0]
    session = service.start(
        db, owner, {"game": "sudoku", "level": "kids", "mode": "relaxed"}, now=T0
    )
    grid = list(session["puzzle"]["grid"])
    solution = secret_of(db, session["id"])["solution"]
    with pytest.raises(ValueError, match="to fix"):
        service.finish(db, owner, session["id"], {"grid": grid}, now=T0)
    tampered = list(solution)
    given = next(i for i, v in enumerate(grid) if v)
    tampered[given] = tampered[given] % 4 + 1
    with pytest.raises(ValueError, match="cannot change"):
        service.finish(db, owner, session["id"], {"grid": tampered}, now=T0)
    hint = service.move(db, owner, session["id"], {"action": "hint", "grid": grid})
    assert hint["hint"]["value"] == solution[hint["hint"]["index"]]
    done = service.finish(
        db, owner, session["id"], {"grid": solution}, now=T0 + timedelta(seconds=90)
    )
    assert (
        done["status"] == "finished"
        and done["score"] == 450
        and done["result"]["hints"] == 1
    )


def test_timed_mode_uses_the_servers_clock_and_seniors_default_to_relaxed(db, people):
    owner, kid, gran = people
    assert (
        service.default_mode(gran) == "relaxed"
        and service.default_mode(kid) == "relaxed"
    )
    assert service.default_mode(owner) == "timed"
    quick = service.start(
        db, owner, {"game": "maths", "level": "easy", "mode": "timed"}, now=T0
    )
    answers = secret_of(db, quick["id"])["answers"]
    fast = service.finish(
        db, owner, quick["id"], {"answers": answers}, now=T0 + timedelta(seconds=20)
    )
    slow_session = service.start(
        db, owner, {"game": "maths", "level": "easy", "mode": "timed"}, now=T0
    )
    slow = service.finish(
        db,
        owner,
        slow_session["id"],
        {"answers": secret_of(db, slow_session["id"])["answers"]},
        now=T0 + timedelta(seconds=500),
    )
    relaxed_session = service.start(
        db, gran, {"game": "maths", "level": "easy"}, now=T0
    )
    relaxed = service.finish(
        db,
        gran,
        relaxed_session["id"],
        {"answers": secret_of(db, relaxed_session["id"])["answers"]},
        now=T0 + timedelta(seconds=20),
    )
    assert (
        fast["score"] > slow["score"] == 1000
        and relaxed["score"] == 1000
        and relaxed_session["mode"] == "relaxed"
    )


def test_the_daily_challenge_is_one_try_and_ranks_the_family(db, people):
    owner, kid, gran = people
    first = service.start(db, owner, {"daily": True}, now=T0)
    assert first["game"] == "trivia" and first["daily_date"] == "2026-10-26"  # Monday
    assert (
        service.start(db, owner, {"daily": True}, now=T0)["id"] == first["id"]
    )  # resume, not a new try
    theirs = service.start(db, kid, {"daily": True}, now=T0)
    assert theirs["puzzle"] == first["puzzle"]  # the same puzzle for everyone
    answers = secret_of(db, first["id"])["answers"]
    service.finish(db, owner, first["id"], {"answers": answers}, now=T0)
    service.finish(db, kid, theirs["id"], {"answers": [0] * 10}, now=T0)
    with pytest.raises(ValueError, match="played today's challenge"):
        service.start(db, owner, {"daily": True}, now=T0)
    board = service.daily(db, owner, now=T0)["board"]
    assert [b["name"] for b in board][0] == "Pat" and len(board) == 2
    tomorrow = service.start(db, owner, {"daily": True}, now=T0 + timedelta(days=1))
    assert tomorrow["game"] == "maths"
    assert service.streak(db, owner["id"], (T0 + timedelta(days=1)).date()) == 1


# ---------------------------------------------------------------------------
# live trivia
# ---------------------------------------------------------------------------


def test_a_live_trivia_room_runs_on_the_servers_clock(db, people):
    owner, kid, gran = people
    room = service.create_room(db, owner, {"questions": 3, "seconds": 20}, now=T0)
    assert room["phase"] == "lobby" and len(room["code"]) == 4 and room["is_host"]
    assert "answer" not in room
    joined = service.join_by_code(db, kid, room["code"].lower(), now=T0)
    assert {p["name"] for p in joined["players"]} == {"Pat", "Mia"}
    token, guest_state = service.join_guest(db, room["id"], "Auntie Jo", now=T0)
    with pytest.raises(PermissionError):
        service.start_room(db, kid, room["id"], now=T0)
    started = service.start_room(db, owner, room["id"], now=T0)
    assert (
        started["phase"] == "question"
        and started["question"]
        and "answer" not in started
    )
    questions = kit.loads(
        db.execute(
            "SELECT questions FROM pl_rooms WHERE id = ?", (room["id"],)
        ).fetchone()[0]
    )
    right = questions[0]["answer"]
    service.answer_as(db, owner, room["id"], right, now=T0 + timedelta(seconds=2))
    with pytest.raises(ValueError, match="already answered"):
        service.answer_as(db, owner, room["id"], right, now=T0 + timedelta(seconds=3))
    service.answer_as(
        db, kid, room["id"], (right + 1) % 4, now=T0 + timedelta(seconds=5)
    )
    guest = service.guest_player(db, room["id"], token)
    with pytest.raises(ValueError, match="Time is up"):
        service.answer(
            db, room["id"], guest, right, now=T0 + timedelta(seconds=21)
        )  # late
    reveal = service.room_state(
        db,
        service._room(db, room["id"]),
        viewer_profile=owner["id"],
        now=T0 + timedelta(seconds=22),
    )
    assert reveal["phase"] == "reveal" and reveal["answer"] == right
    scores = {p["name"]: p["score"] for p in reveal["players"]}
    assert (
        scores["Pat"] == 100 + round(50 * 18 / 20)
        and scores["Mia"] == 0
        and scores["Auntie Jo"] == 0
    )
    # The clock moves on by itself (20 s question + 6 s answer shown).
    later = service.room_state(
        db, service._room(db, room["id"]), now=T0 + timedelta(seconds=27)
    )
    assert later["phase"] == "question" and later["index"] == 1
    service.answer(
        db, room["id"], guest, questions[1]["answer"], now=T0 + timedelta(seconds=28)
    )
    moved = service.next_question(db, owner, room["id"], now=T0 + timedelta(seconds=30))
    assert moved["index"] == 2
    finished = service.next_question(
        db, owner, room["id"], now=T0 + timedelta(seconds=31)
    )
    assert finished["phase"] == "finished" and finished["players"][0]["name"] in (
        "Pat",
        "Auntie Jo",
    )
    with pytest.raises(ValueError):
        service.join_room(db, gran, room["id"], now=T0 + timedelta(seconds=40))


def test_rooms_expire_and_invite_the_household(db, people):
    owner, kid, _gran = people
    room = service.create_room(db, owner, {}, now=kit.utcnow())
    pulse = routes._pulse(db, kid, "")
    assert pulse["badge"] == 1 and pulse["alerts"][0]["id"] == f"play-room:{room['id']}"
    assert (
        routes._pulse(db, owner, "")["alerts"] == []
    )  # the host is not invited to their own room
    card = routes._today(db, kid)[0]
    assert any(
        "live trivia" in line["text"] and line.get("action") for line in card["lines"]
    )
    with pytest.raises(LookupError):
        service.join_by_code(
            db, kid, room["code"], now=kit.utcnow() + timedelta(hours=3, minutes=1)
        )
    assert service.rooms(db, kid, now=kit.utcnow() + timedelta(hours=4)) == []


# ---------------------------------------------------------------------------
# watch and read list
# ---------------------------------------------------------------------------


def test_the_watch_list_is_shared_and_each_person_rates_it(db, people):
    owner, kid, gran = people
    item = service.add_item(
        db,
        owner,
        {
            "kind": "film",
            "title": "Paddington",
            "year": 2014,
            "recommended_by": gran["id"],
        },
    )
    assert item["recommended_by"] == "Rosa"
    service.set_status(db, owner, item["id"], {"status": "done", "rating": 5})
    viewed = service.set_status(db, kid, item["id"], {"status": "want", "rating": 4})
    assert viewed["mine"]["status"] == "want" and viewed["average_rating"] == 4.5
    assert [o["name"] for o in viewed["others"]] == ["Pat"]
    with pytest.raises(PermissionError):
        service.delete_item(
            db, kid, item["id"]
        )  # a child cannot remove an adult's item
    with pytest.raises(ValueError):
        service.add_item(db, kid, {"title": "X", "link": "javascript:alert(1)"})
    # Soon, so the plan is still today whatever the hour the suite runs.
    when = kit.iso(kit.utcnow().replace(microsecond=0) + timedelta(minutes=2))
    service.add_plan(db, owner, item["id"], {"planned_for": when, "who": [kid["id"]]})
    assert [p["title"] for p in service.plans_today(db, kid)] == ["Paddington"]
    assert service.plans_today(db, gran) == []
    lines = routes._today(db, kid)[0]["lines"]
    assert any(line["text"] == "Watch together: Paddington" for line in lines)
    assert [i["title"] for i in service.watch_list(db, kid, status="want")] == [
        "Paddington"
    ]
    assert service.watch_list(db, owner, status="want") == []


# ---------------------------------------------------------------------------
# the single-user build
# ---------------------------------------------------------------------------


@pytest.fixture
def local_index():
    public.set_index(local._public_link_index)


def _anonymous(env, method, path, body=None):
    data = json.dumps(body).encode() if body is not None else None
    request = urllib.request.Request(env.base + path, data=data, method=method)
    for name, value in {
        "Content-Type": "application/json",
        "X-Requested-With": "aira",
        "Origin": env.base,
    }.items():
        request.add_header(name, value)
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            return response.status, json.loads(response.read() or b"{}")
    except urllib.error.HTTPError as error:
        return error.code, json.loads(error.read() or b"{}")


def test_a_family_plays_together_on_the_local_build(env, api, local_index):
    kit.forget_schema()
    household = Household(env, api)
    pat = household.owner
    mia = household.add("Mia", age_group="child", pin="2468")
    catalog = mia.get("/api/play/games")
    assert catalog["default_mode"] == "relaxed" and catalog["suggested_level"] == "kids"
    assert {g["id"] for g in catalog["games"]} == set(games.GAME_IDS)

    # Both play today's challenge; the board ranks them; a second try is refused.
    for person, good in ((pat, True), (mia, False)):
        session = person.post("/api/play/sessions", {"daily": True}, expect=201)[
            "session"
        ]
        game = session["game"]
        secret = _secret(env, session["id"])
        payload = _solve(game, secret, session, good)
        if game == "memory":
            for move in payload.pop("moves"):
                person.post(f"/api/play/sessions/{session['id']}/move", move)
        person.post(f"/api/play/sessions/{session['id']}/finish", payload)
        status, body = person.call("POST", "/api/play/sessions", {"daily": True})
        assert status == 400 and "today's challenge" in body["error"]
    board = pat.get("/api/play/daily")["board"]
    assert [b["name"] for b in board] == ["Careers", "Mia"] and board[0][
        "score"
    ] > board[1]["score"]

    # Live trivia: Pat hosts, Mia joins with the code, Auntie Jo joins through the public link.
    room = pat.post("/api/play/rooms", {"questions": 3, "seconds": 60}, expect=201)[
        "room"
    ]
    status, body = mia.call("POST", f"/api/play/rooms/{room['id']}/link", {})
    assert status == 403, body  # a child cannot share outside the family
    link = pat.post(f"/api/play/rooms/{room['id']}/link")["link"]
    mia.post("/api/play/rooms/join", {"code": room["code"]})
    status, page = _anonymous(env, "GET", f"/api/public/{link['token']}")
    assert (
        status == 200
        and page["page"]["room"]["joinable"]
        and '"answer":' not in json.dumps(page["page"])
    )
    status, joined = _anonymous(
        env, "POST", f"/api/public/{link['token']}/join", {"name": "Auntie Jo"}
    )
    assert status == 201, joined
    token = joined["token"]
    started = pat.post(f"/api/play/rooms/{room['id']}/start")["room"]
    assert started["phase"] == "question"
    status, answered = _anonymous(
        env,
        "POST",
        f"/api/public/{link['token']}/answer",
        {"token": token, "choice": 0},
    )
    assert status == 200 and answered["room"]["me"]["choice"] == 0
    status, again = _anonymous(
        env,
        "POST",
        f"/api/public/{link['token']}/answer",
        {"token": token, "choice": 1},
    )
    assert status == 400
    assert (
        _anonymous(
            env,
            "POST",
            f"/api/public/{link['token']}/answer",
            {"token": "nope", "choice": 1},
        )[0]
        == 403
    )
    mia.post(f"/api/play/rooms/{room['id']}/answer", {"choice": 1})
    state = mia.get(f"/api/play/rooms/{room['id']}?since=x")["room"]
    assert state["changed"] and {p["name"] for p in state["players"]} == {
        "Careers",
        "Mia",
        "Auntie Jo",
    }
    assert state["link"] is None  # only the host sees the share link
    assert (
        pat.get(f"/api/play/rooms/{room['id']}")["room"]["link"]["token"]
        == link["token"]
    )

    # A watch-together plan shows on Mia's Today.
    item = pat.post(
        "/api/play/watch", {"kind": "film", "title": "Paddington"}, expect=201
    )["item"]
    # Soon, so the plan is still today whatever the hour the suite runs.
    when = kit.iso(kit.utcnow().replace(microsecond=0) + timedelta(minutes=2))
    pat.post(
        f"/api/play/watch/{item['id']}/plans",
        {"planned_for": when, "who": [mia.id]},
        expect=201,
    )
    today = mia.get("/api/life/today")
    play_card = next(c for c in today["cards"] if c["app"] == "play")
    assert any(
        line["text"] == "Watch together: Paddington" for line in play_card["lines"]
    )


def _secret(env, session_id):
    user = env.user()
    with sqlite3.connect(user["data_path"]) as connection:
        return kit.loads(
            connection.execute(
                "SELECT secret FROM pl_sessions WHERE id = ?", (session_id,)
            ).fetchone()[0]
        )


def _solve(game, secret, session, good):
    if game in ("trivia", "maths"):
        answers = secret["answers"]
        return {"answers": answers if good else [None] * len(answers)}
    if game == "scramble":
        return {"answers": secret["words"] if good else [""] * len(secret["words"])}
    if game == "sudoku":
        return {"grid": secret["solution"]}
    if game == "memory":
        layout = secret["layout"]
        pairs: dict[str, list[int]] = {}
        for index, card in enumerate(layout):
            pairs.setdefault(card, []).append(index)
        moves = []
        if not good:
            moves += [
                {"action": "flip", "index": i}
                for i in (0, next(j for j, c in enumerate(layout) if c != layout[0]))
            ]
        for a, b in pairs.values():
            moves += [{"action": "flip", "index": a}, {"action": "flip", "index": b}]
        return {"moves": moves}
    raise AssertionError(game)


# ---------------------------------------------------------------------------
# the hosted build
# ---------------------------------------------------------------------------


def test_hosted_play_needs_the_app_and_every_member_can_play(cloud):
    kit.forget_schema()
    org = build_org(cloud)
    owner = sign_in(cloud, org.owner)
    status, body = owner.call("GET", "/api/play/games")
    assert status == 402 and body["code"] == "app_required"
    cloud.billing.subscribe_app(org.owner, "play")
    for who in ("owner", "admin", "coach", "candidate", "billing", "auditor"):
        browser = sign_in(cloud, getattr(org, who))
        status, data = browser.call("GET", "/api/play/games")
        assert status == 200, (who, data)
    candidate = sign_in(cloud, org.candidate)
    status, made = candidate.call("POST", "/api/play/rooms", {"questions": 3})
    assert status == 201, made
    status, joined = sign_in(cloud, org.coach).call(
        "POST", "/api/play/rooms/join", {"code": made["room"]["code"]}
    )
    assert status == 200 and len(joined["room"]["players"]) == 2
    status, session = owner.call(
        "POST", "/api/play/sessions", {"game": "maths", "level": "easy"}
    )
    assert status == 201
    # Another member cannot open my game; another organization sees none of this.
    assert (
        sign_in(cloud, org.admin).call(
            "GET", f"/api/play/sessions/{session['session']['id']}"
        )[0]
        == 404
    )
    other = build_org(cloud, "Other Org", slug_email="other.test")
    cloud.billing.subscribe_app(other.owner, "play")
    status, rooms = sign_in(cloud, other.owner).call("GET", "/api/play/rooms")
    assert status == 200 and rooms["rooms"] == []
