"""Play's later additions and the bugs found by using it: quizzes people write (with suggested questions), "beat my
score" challenges on the very same puzzle, quiz nights with an entry fee the host marks as paid (money recorded once
in the business ledger), the watch list's edit, search, spreadsheet and "pick for tonight", and the fixes (a
sensible level for grown-ups, the answers given shown next to the right ones, level names, plans in the past)."""

from __future__ import annotations

import csv
import io
import json
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
from aira.suite.play import quizzes, service

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


class FakeModel:
    def __init__(self, reply=None) -> None:
        self.reply = reply
        self.prompts: list[str] = []

    def model(self, prompt):
        self.prompts.append(prompt)
        return self.reply


@pytest.fixture(autouse=True)
def no_live_model():
    previous = suite.gateway()
    suite.set_gateway(FakeModel())
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


QUESTIONS = [
    {
        "text": "Where did we go on holiday in 2024?",
        "choices": ["Goa", "Paris", "Rome"],
        "answer": 0,
    },
    {
        "text": "What is the dog called?",
        "choices": ["Biscuit", "Rex", "", "Max"],
        "answer": 3,
    },
    {
        "text": "Grandma's favourite song?",
        "choices": ["Moon River", "Yesterday"],
        "answer": 1,
    },
]


# ---------------------------------------------------------------------------
# bugs found by playing
# ---------------------------------------------------------------------------


def test_grown_ups_do_not_start_on_the_childrens_level(db, people):
    owner, kid, gran = people
    levels = {
        g["id"]: g["suggested"] for g in service.catalog(db, owner, now=T0)["games"]
    }
    assert levels == {
        "memory": "medium",
        "scramble": "easy",
        "maths": "medium",
        "trivia": "medium",
        "sudoku": "medium",
        "word": "daily",
    }
    kid_levels = {
        g["id"]: g["suggested"] for g in service.catalog(db, kid, now=T0)["games"]
    }
    assert kid_levels["trivia"] == "kids" and kid_levels["sudoku"] == "kids"
    assert (
        kid_levels["memory"] == "easy"
    )  # memory has no children's level: the easiest one
    gran_levels = {
        g["id"]: g["suggested"] for g in service.catalog(db, gran, now=T0)["games"]
    }
    assert gran_levels["trivia"] == "easy" and gran_levels["maths"] == "easy"


def test_a_result_shows_what_was_answered_next_to_the_right_answer(db, people):
    owner = people[0]
    trivia = service.start(
        db, owner, {"game": "trivia", "level": "easy", "mode": "relaxed"}, now=T0
    )
    right = secret_of(db, trivia["id"])["answers"]
    given = [(right[0] + 1) % 4, *right[1:-1], "x"]
    done = service.finish(db, owner, trivia["id"], {"answers": given}, now=T0)
    assert done["result"]["given"][0] == (right[0] + 1) % 4
    assert done["result"]["given"][-1] is None  # not a choice: shown as no answer
    assert done["result"]["answers"] == right and done["result"]["marks"][0] is False

    maths = service.start(
        db, owner, {"game": "maths", "level": "easy", "mode": "relaxed"}, now=T0
    )
    answers = [str(a) for a in secret_of(db, maths["id"])["answers"]]
    answers[2] = " 999 "
    done = service.finish(db, owner, maths["id"], {"answers": answers}, now=T0)
    assert done["result"]["given"][2] == "999" and done["result"]["marks"][2] is False


def test_scores_name_levels_in_words(db, people):
    owner = people[0]
    session = service.start(
        db, owner, {"game": "sudoku", "level": "kids", "mode": "relaxed"}, now=T0
    )
    service.finish(
        db,
        owner,
        session["id"],
        {"grid": secret_of(db, session["id"])["solution"]},
        now=T0,
    )
    data = service.scores(db, owner, now=T0)
    assert data["bests"][0]["level_label"] == "4 by 4"
    assert data["recent"][0]["level_label"] == "4 by 4"
    assert session["level_label"] == "4 by 4"


def test_a_watch_together_plan_cannot_be_in_the_past(db, people):
    owner = people[0]
    item = service.add_item(db, owner, {"kind": "film", "title": "Up"})
    with pytest.raises(ValueError, match="has not passed"):
        service.add_plan(
            db,
            owner,
            item["id"],
            {"planned_for": kit.iso(kit.utcnow() - timedelta(days=1))},
        )
    plan = service.add_plan(
        db,
        owner,
        item["id"],
        {"planned_for": kit.iso(kit.utcnow() + timedelta(hours=3))},
    )
    assert len(plan["plans"]) == 1


def test_the_watch_list_can_be_edited_searched_exported_and_picked_from(db, people):
    owner, kid, gran = people
    film = service.add_item(
        db, owner, {"kind": "film", "title": "Paddingtn", "recommended_by": kid["id"]}
    )
    assert film["recommended_by_id"] == kid["id"]
    fixed = service.update_item(
        db, owner, film["id"], {"title": "Paddington", "year": 2014}
    )
    assert fixed["title"] == "Paddington" and fixed["recommended_by"] == "Mia"
    book = service.add_item(db, gran, {"kind": "book", "title": "=HYPERLINK(1)"})
    assert [i["title"] for i in service.watch_list(db, owner, query="padd")] == [
        "Paddington"
    ]
    assert [i["title"] for i in service.watch_list(db, owner, kind="book")] == [
        "=HYPERLINK(1)"
    ]
    rows = service.watch_rows(service.watch_list(db, owner))
    text = kit.csv_bytes(rows, service.WATCH_COLUMNS).decode()
    assert (
        "'=HYPERLINK(1)" in text and "Paddington" in text
    )  # a formula is never run by a spreadsheet
    # Pick for tonight: never something I finished; "together" skips what someone else finished.
    service.set_status(db, owner, film["id"], {"status": "done"})
    for _ in range(10):
        assert service.pick(db, owner)["id"] == book["id"]
    service.set_status(db, kid, book["id"], {"status": "done"})
    assert service.pick(db, owner, together=True) is None
    assert service.pick(db, owner)["id"] == book["id"]


# ---------------------------------------------------------------------------
# quizzes people write
# ---------------------------------------------------------------------------


def test_a_quiz_is_checked_before_it_is_saved(db, people):
    owner = people[0]
    with pytest.raises(ValueError, match="at least 3"):
        quizzes.create(db, owner, {"title": "Short", "questions": QUESTIONS[:2]})
    with pytest.raises(ValueError, match="same answer twice"):
        quizzes.create(
            db,
            owner,
            {
                "title": "Twice",
                "questions": [
                    *QUESTIONS[:2],
                    {"text": "Q", "choices": ["A", "a"], "answer": 0},
                ],
            },
        )
    with pytest.raises(ValueError, match="right answer for question 3"):
        quizzes.create(
            db,
            owner,
            {
                "title": "Blank",
                "questions": [
                    *QUESTIONS[:2],
                    {"text": "Q", "choices": ["A", "", "B"], "answer": 1},
                ],
            },
        )
    with pytest.raises(ValueError, match="name for the quiz"):
        quizzes.create(db, owner, {"title": " ", "questions": QUESTIONS})
    quiz = quizzes.create(
        db,
        owner,
        {
            "title": "Our family",
            "questions": [*QUESTIONS, {"text": "", "choices": ["", ""]}],
        },
    )
    # Blank answers are dropped and the right one moves with them; empty rows are skipped.
    assert quiz["count"] == 3
    assert quiz["questions"][1] == {
        "text": "What is the dog called?",
        "choices": ["Biscuit", "Rex", "Max"],
        "answer": 2,
    }


def test_a_quiz_is_played_fairly_and_its_answers_stay_with_the_writer(db, people):
    owner, kid, gran = people
    quiz = quizzes.create(
        db,
        owner,
        {"title": "Our family", "about": "Us", "questions": QUESTIONS, "shared": True},
    )
    secret = quizzes.create(
        db, owner, {"title": "Just mine", "questions": QUESTIONS, "shared": False}
    )
    listed = {q["title"]: q for q in quizzes.list_quizzes(db, kid)}
    assert set(listed) == {"Our family"} and "questions" not in listed["Our family"]
    with pytest.raises(LookupError):
        quizzes.visible(db, kid, secret["id"])
    with pytest.raises(PermissionError):
        quizzes.get_quiz(
            db, kid, quiz["id"]
        )  # a child cannot read the answers of someone else's quiz
    # Mia plays it: the browser never sees which answer is right; the server scores it.
    session = service.start(db, kid, {"quiz": quiz["id"], "mode": "relaxed"}, now=T0)
    assert session["game"] == "quiz" and session["name"] == "Our family"
    assert "answer" not in json.dumps(session["puzzle"]["questions"])
    right = secret_of(db, session["id"])["answers"]
    done = service.finish(
        db, kid, session["id"], {"answers": right, "score": 1}, now=T0
    )
    assert done["score"] == 300 and done["result"]["correct"] == 3
    # The writer's own games do not count on the quiz's board.
    mine = service.start(db, owner, {"quiz": quiz["id"]}, now=T0)
    service.finish(
        db, owner, mine["id"], {"answers": secret_of(db, mine["id"])["answers"]}, now=T0
    )
    board = quizzes.board(db, gran, quiz["id"])
    assert [b["name"] for b in board] == ["Mia"] and board[0]["best"] == 300
    assert any(
        b["name"] == "Our family" for b in service.scores(db, kid, now=T0)["bests"]
    )
    assert service.scores(db, kid, game="quiz", now=T0)["family"][0]["name"] == "Mia"
    # Nobody else can start the private one.
    with pytest.raises(LookupError):
        service.start(db, gran, {"quiz": secret["id"]}, now=T0)


def test_who_may_change_a_quiz(db, people):
    owner, kid, gran = people
    kids_quiz = quizzes.create(
        db, kid, {"title": "Mia's animals", "questions": QUESTIONS}
    )
    adults_quiz = quizzes.create(
        db, owner, {"title": "Pat's films", "questions": QUESTIONS}
    )
    with pytest.raises(PermissionError):
        quizzes.update(db, kid, adults_quiz["id"], {"title": "Mine now"})
    with pytest.raises(PermissionError):
        quizzes.delete(db, kid, adults_quiz["id"])
    # A grown-up may tidy a child's shared quiz.
    assert (
        quizzes.update(db, gran, kids_quiz["id"], {"title": "Mia's animal quiz"})[
            "title"
        ]
        == "Mia's animal quiz"
    )
    quizzes.delete(db, owner, kids_quiz["id"])
    assert [q["title"] for q in quizzes.list_quizzes(db, kid)] == ["Pat's films"]


def test_suggested_questions_come_from_the_model_or_the_quiz_book(db, people):
    owner, kid, _gran = people
    model = FakeModel(
        json.dumps(
            {
                "questions": [
                    {
                        "question": "Which planet is red?",
                        "answer": "Mars",
                        "wrong": ["Venus", "Jupiter", "Saturn"],
                    },
                    {
                        "question": "Broken",
                        "answer": "",
                        "wrong": ["x"],
                    },  # no right answer: dropped
                    {
                        "question": "Same",
                        "answer": "A",
                        "wrong": ["a"],
                    },  # nothing wrong left: dropped
                ]
            }
        )
    )
    suite.set_gateway(model)
    out = quizzes.draft(db, kid, {"topic": "space", "count": 3})
    assert out["source"] == "model"
    assert out["questions"] == [
        {
            "text": "Which planet is red?",
            "choices": ["Mars", "Venus", "Jupiter", "Saturn"],
            "answer": 0,
        }
    ]
    assert "child" in model.prompts[0]
    suite.set_gateway(FakeModel("not json"))
    book = quizzes.draft(db, owner, {"topic": "geography", "count": 5})
    assert book["source"] == "template" and len(book["questions"]) == 5
    assert all(len(q["choices"]) == 4 and q["answer"] == 0 for q in book["questions"])
    # What is suggested can be saved as it is.
    assert (
        quizzes.create(db, owner, {"title": "Geo", "questions": book["questions"]})[
            "count"
        ]
        == 5
    )
    with pytest.raises(ValueError):
        quizzes.draft(db, owner, {"topic": ""})


def test_a_quiz_can_be_played_live(db, people):
    owner, kid, _gran = people
    quiz = quizzes.create(db, kid, {"title": "Mia's quiz", "questions": QUESTIONS})
    room = service.create_room(db, owner, {"quiz": quiz["id"], "seconds": 30}, now=T0)
    assert room["title"] == "Mia's quiz" and room["total"] == 3 and room["from_quiz"]
    started = service.start_room(db, owner, room["id"], now=T0 + timedelta(seconds=1))
    assert (
        started["question"]["category"] == ""
    )  # the room is already named after the quiz
    assert started["question"]["text"] == QUESTIONS[0]["text"]


# ---------------------------------------------------------------------------
# challenges
# ---------------------------------------------------------------------------


def test_a_challenge_is_the_very_same_puzzle_and_settles_itself(db, people):
    owner, kid, gran = people
    mine = service.start(
        db, owner, {"game": "scramble", "level": "easy", "mode": "relaxed"}, now=T0
    )
    words = secret_of(db, mine["id"])["words"]
    with pytest.raises(ValueError, match="Finish the game"):
        service.challenge(
            db, owner, {"session_id": mine["id"], "to": [kid["id"]]}, now=T0
        )
    done = service.finish(
        db, owner, mine["id"], {"answers": words[:5] + ["", "", ""]}, now=T0
    )
    assert done["score"] == 500
    with pytest.raises(ValueError, match="yourself"):
        service.challenge(
            db, owner, {"session_id": mine["id"], "to": [owner["id"]]}, now=T0
        )
    with pytest.raises(LookupError):
        service.challenge(
            db, owner, {"session_id": mine["id"], "to": ["0" * 32]}, now=T0
        )
    sent = service.challenge(
        db, owner, {"session_id": mine["id"], "to": [kid["id"], gran["id"]]}, now=T0
    )
    assert {c["who"] for c in sent} == {"Mia", "Rosa"}
    service.challenge(
        db, owner, {"session_id": mine["id"], "to": [kid["id"]]}, now=T0
    )  # sending again is harmless
    waiting = service.challenges(db, kid)["incoming"]
    assert (
        len(waiting) == 1
        and waiting[0]["target"] == 500
        and waiting[0]["who"] == owner["name"]
    )
    # Someone else's game cannot be used, and nobody else can take Mia's challenge.
    with pytest.raises(LookupError):
        service.challenge(
            db, kid, {"session_id": mine["id"], "to": [gran["id"]]}, now=T0
        )
    with pytest.raises(LookupError):
        service.play_challenge(db, gran, waiting[0]["id"], now=T0)
    # Mia gets the same letters; starting again carries on the same game.
    hers = service.play_challenge(db, kid, waiting[0]["id"], now=T0)
    assert hers["puzzle"] == mine["puzzle"] and hers["challenge"]["target"] == 500
    assert service.play_challenge(db, kid, waiting[0]["id"], now=T0)["id"] == hers["id"]
    result = service.finish(
        db, kid, hers["id"], {"answers": words}, now=T0 + timedelta(minutes=2)
    )
    assert result["score"] == 800 and result["challenge"]["status"] == "done"
    with pytest.raises(ValueError, match="already taken"):
        service.play_challenge(db, kid, waiting[0]["id"], now=T0)
    assert service.challenges(db, kid)["results"][0]["outcome"] == "won"
    assert service.challenges(db, owner)["results"][0]["outcome"] == "lost"
    # The challenger hears about it; Rosa still has hers waiting (badge and alert).
    badge, alerts = service.challenges_pulse(db, owner, now=T0 + timedelta(minutes=3))
    assert (
        badge == 0
        and alerts[0]["id"].startswith("play-challenge-done:")
        and "They won" in alerts[0]["body"]
    )
    badge, alerts = service.challenges_pulse(db, gran, now=T0 + timedelta(minutes=3))
    assert badge == 1 and alerts[0]["id"].startswith("play-challenge:")
    badge, alerts = service.challenges_pulse(db, gran, now=T0 + timedelta(hours=2))
    assert badge == 1 and alerts == []  # an alert is for new challenges only
    rosa = service.challenges(db, gran)["incoming"][0]
    service.decline_challenge(db, gran, rosa["id"])
    assert service.challenges(db, gran)["incoming"] == []


def test_todays_puzzle_and_private_quizzes_cannot_be_challenged(db, people):
    owner, kid, _gran = people
    daily = service.start(db, owner, {"daily": True}, now=T0)
    game = daily["game"]
    secret = secret_of(db, daily["id"])
    payload = (
        {"answers": secret.get("answers") or secret.get("words")}
        if game != "memory"
        else {}
    )
    if game == "memory":
        layout = secret["layout"]
        for index in range(len(layout)):
            for other in range(index + 1, len(layout)):
                if layout[index] == layout[other]:
                    service.move(
                        db, owner, daily["id"], {"action": "flip", "index": index}
                    )
                    service.move(
                        db, owner, daily["id"], {"action": "flip", "index": other}
                    )
    service.finish(db, owner, daily["id"], payload, now=T0)
    with pytest.raises(ValueError, match="today's puzzle"):
        service.challenge(
            db, owner, {"session_id": daily["id"], "to": [kid["id"]]}, now=T0
        )
    private = quizzes.create(
        db, owner, {"title": "Secret", "questions": QUESTIONS, "shared": False}
    )
    played = service.start(db, owner, {"quiz": private["id"]}, now=T0)
    service.finish(db, owner, played["id"], {"answers": [0, 0, 0]}, now=T0)
    with pytest.raises(ValueError, match="Share this quiz"):
        service.challenge(
            db, owner, {"session_id": played["id"], "to": [kid["id"]]}, now=T0
        )


# ---------------------------------------------------------------------------
# quiz nights: an entry fee, marked as paid by the host
# ---------------------------------------------------------------------------


def test_a_quiz_night_takes_entry_fees_once(db, people):
    owner, kid, gran = people
    with pytest.raises(PermissionError):
        service.create_room(db, kid, {"entry_cents": 500}, now=T0)
    with pytest.raises(ValueError):
        service.create_room(db, owner, {"entry_cents": -5}, now=T0)
    with pytest.raises(ValueError, match="too large"):
        service.create_room(db, owner, {"entry_cents": 10_000_000}, now=T0)
    room = service.create_room(
        db,
        owner,
        {"title": "Cafe quiz", "entry_cents": 500, "prize": "A cake", "seconds": 60},
        now=T0,
    )
    assert (
        room["entry_cents"] == 500
        and room["prize"] == "A cake"
        and room["takings_cents"] == 0
    )
    token, guest_view = service.join_guest(db, room["id"], "Sam", now=T0)
    assert guest_view["me"]["paid"] is False and guest_view["me"][
        "reference"
    ].startswith(room["code"])
    # A guest sees whether they have paid, never whether anyone else has.
    assert [p["name"] for p in guest_view["players"] if "paid" in p] == ["Sam"]
    sam = service.guest_player(db, room["id"], token)
    service.join_room(db, kid, room["id"], now=T0)
    service.start_room(db, owner, room["id"], now=T0 + timedelta(seconds=1))
    with pytest.raises(ValueError, match="marked your entry as paid"):
        service.answer(db, room["id"], sam, 0, now=T0 + timedelta(seconds=2))
    service.answer_as(
        db, kid, room["id"], 0, now=T0 + timedelta(seconds=2)
    )  # family plays free
    # Only the host marks payments, and only for guests.
    with pytest.raises(PermissionError):
        service.mark_paid(db, gran, room["id"], sam["id"], True, now=T0)
    kid_player = service._player_for(db, room["id"], kid["id"])
    with pytest.raises(ValueError, match="free"):
        service.mark_paid(db, owner, room["id"], kid_player["id"], True, now=T0)
    state = service.mark_paid(
        db, owner, room["id"], sam["id"], True, now=T0 + timedelta(seconds=3)
    )
    service.mark_paid(
        db, owner, room["id"], sam["id"], True, now=T0 + timedelta(seconds=3)
    )  # twice: counted once
    assert state["takings_cents"] == 500 and state["paid_count"] == 1
    assert next(p for p in state["players"] if p["name"] == "Sam")["paid"] is True
    assert kit.revenue(db, app="play")["total_cents"] == 500
    service.answer(db, room["id"], sam, 1, now=T0 + timedelta(seconds=4))
    # Taking the mark back takes the money back out of the ledger.
    service.mark_paid(
        db, owner, room["id"], sam["id"], False, now=T0 + timedelta(seconds=5)
    )
    assert kit.revenue(db, app="play")["total_cents"] == 0
    service.mark_paid(
        db, owner, room["id"], sam["id"], True, now=T0 + timedelta(seconds=6)
    )
    summary = service.nights(db, owner)
    assert (
        summary["total_cents"] == 500
        and summary["nights"][0]["paid"] == 1
        and summary["nights"][0]["guests"] == 1
    )
    assert (
        service.nights(db, kid)["can_charge"] is False
        and service.nights(db, kid)["nights"] == []
    )
    rows = service.entry_rows(db, owner, room["id"])
    assert {r["name"]: r["paid"] for r in rows} == {
        owner["name"]: "free",
        "Mia": "free",
        "Sam": "yes",
    }
    with pytest.raises(PermissionError):
        service.entry_rows(db, kid, room["id"])
    with pytest.raises(ValueError, match="no entry fee"):
        free = service.create_room(db, owner, {}, now=T0)
        token2, _ = service.join_guest(db, free["id"], "Jo", now=T0)
        service.mark_paid(
            db,
            owner,
            free["id"],
            service.guest_player(db, free["id"], token2)["id"],
            True,
        )


# ---------------------------------------------------------------------------
# the single-user server and the hosted build
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


def _secret(env, session_id):
    with sqlite3.connect(env.user()["data_path"]) as connection:
        return kit.loads(
            connection.execute(
                "SELECT secret FROM pl_sessions WHERE id = ?", (session_id,)
            ).fetchone()[0]
        )


def test_quizzes_challenges_and_a_quiz_night_on_the_local_build(env, api, local_index):
    kit.forget_schema()
    household = Household(env, api)
    pat = household.owner
    mia = household.add("Mia", age_group="child", pin="2468")

    # Mia writes a quiz; Pat plays it and challenges Mia back on the same questions.
    status, body = mia.call(
        "POST", "/api/play/quizzes", {"title": "Mia's quiz", "questions": QUESTIONS[:2]}
    )
    assert status == 400 and "at least 3" in body["error"]
    quiz = mia.post(
        "/api/play/quizzes", {"title": "Mia's quiz", "questions": QUESTIONS}, expect=201
    )["quiz"]
    assert (
        pat.call("GET", f"/api/play/quizzes/{quiz['id']}")[0] == 200
    )  # a grown-up may check a child's quiz
    session = pat.post(
        "/api/play/sessions", {"quiz": quiz["id"], "mode": "relaxed"}, expect=201
    )["session"]
    done = pat.post(
        f"/api/play/sessions/{session['id']}/finish",
        {"answers": _secret(env, session["id"])["answers"]},
    )["session"]
    assert done["score"] == 300
    pat.post(
        "/api/play/challenges",
        {"session_id": session["id"], "to": [mia.id]},
        expect=201,
    )
    today = mia.get("/api/life/today")
    card = next(c for c in today["cards"] if c["app"] == "play")
    assert (
        any("challenged you" in line["text"] for line in card["lines"])
        and card["link"] == "#/play"
    )
    pulse = mia.get("/api/life/pulse")
    assert any(a["id"].startswith("play-challenge:") for a in pulse["alerts"])
    waiting = mia.get("/api/play/challenges")["incoming"][0]
    hers = mia.post(f"/api/play/challenges/{waiting['id']}/play")["session"]
    assert hers["puzzle"] == session["puzzle"]
    result = mia.post(
        f"/api/play/sessions/{hers['id']}/finish", {"answers": [0, 0, 0]}
    )["session"]
    assert result["challenge"]["status"] == "done"

    # The scores download is a spreadsheet of every game.
    status, raw = pat.call("GET", "/api/play/scores?format=csv", raw=True)
    rows = list(csv.DictReader(io.StringIO(raw.decode())))
    assert (
        status == 200 and rows[0]["game"] == "Mia's quiz" and rows[0]["score"] == "300"
    )

    # A quiz night: a guest sees the fee, pays the host, and the money reaches the business ledger.
    status, body = mia.call("POST", "/api/play/rooms", {"entry_cents": 300})
    assert status == 403
    room = pat.post(
        "/api/play/rooms",
        {"title": "Club quiz", "entry_cents": 300, "prize": "Tea", "seconds": 60},
        expect=201,
    )["room"]
    link = pat.post(f"/api/play/rooms/{room['id']}/link")["link"]
    status, page = _anonymous(env, "GET", f"/api/public/{link['token']}")
    assert (
        status == 200
        and page["page"]["room"]["entry_cents"] == 300
        and page["page"]["room"]["prize"] == "Tea"
    )
    status, joined = _anonymous(
        env, "POST", f"/api/public/{link['token']}/join", {"name": "Sam"}
    )
    assert status == 201 and joined["room"]["me"]["paid"] is False
    pat.post(f"/api/play/rooms/{room['id']}/start")
    status, body = _anonymous(
        env,
        "POST",
        f"/api/public/{link['token']}/answer",
        {"token": joined["token"], "choice": 0},
    )
    assert status == 400 and "paid" in body["error"]
    sam = next(
        p
        for p in pat.get(f"/api/play/rooms/{room['id']}")["room"]["players"]
        if p["name"] == "Sam"
    )
    status, body = mia.call(
        "POST", f"/api/play/rooms/{room['id']}/players/{sam['id']}/paid", {"paid": True}
    )
    assert status == 403
    marked = pat.post(
        f"/api/play/rooms/{room['id']}/players/{sam['id']}/paid", {"paid": True}
    )["room"]
    assert marked["takings_cents"] == 300
    status, answered = _anonymous(
        env,
        "POST",
        f"/api/public/{link['token']}/answer",
        {"token": joined["token"], "choice": 0},
    )
    assert status == 200 and answered["room"]["me"]["paid"] is True
    assert pat.get("/api/play/nights")["total_cents"] == 300
    status, raw = pat.call(
        "GET", f"/api/play/rooms/{room['id']}/entries?format=csv", raw=True
    )
    assert status == 200 and "Sam,yes" in raw.decode().replace("\r", "")
    assert mia.call("GET", f"/api/play/rooms/{room['id']}/entries")[0] == 403

    # The watch list: edit, then download.
    item = pat.post(
        "/api/play/watch", {"kind": "film", "title": "Paddingtn"}, expect=201
    )["item"]
    status, edited = pat.call(
        "PATCH", f"/api/play/watch/{item['id']}", {"title": "Paddington"}
    )
    assert status == 200 and edited["item"]["title"] == "Paddington"
    status, raw = pat.call("GET", "/api/play/watch?format=csv", raw=True)
    assert status == 200 and "Paddington" in raw.decode()
    assert pat.get("/api/play/watch/pick")["item"]["title"] == "Paddington"


def test_hosted_quizzes_stay_in_their_organization(cloud):
    kit.forget_schema()
    org = build_org(cloud)
    cloud.billing.subscribe_app(org.owner, "play")
    owner = sign_in(cloud, org.owner)
    coach = sign_in(cloud, org.coach)
    status, made = owner.call(
        "POST", "/api/play/quizzes", {"title": "Team quiz", "questions": QUESTIONS}
    )
    assert status == 201, made
    status, private = owner.call(
        "POST",
        "/api/play/quizzes",
        {"title": "Draft", "questions": QUESTIONS, "shared": False},
    )
    assert status == 201
    status, listed = coach.call("GET", "/api/play/quizzes")
    assert status == 200 and [q["title"] for q in listed["quizzes"]] == ["Team quiz"]
    assert coach.call("GET", f"/api/play/quizzes/{private['quiz']['id']}")[0] == 404
    for who in ("candidate", "auditor", "billing"):
        status, data = sign_in(cloud, getattr(org, who)).call(
            "POST", "/api/play/sessions", {"quiz": made["quiz"]["id"]}
        )
        assert status == 201, (who, data)
    status, room = owner.call(
        "POST", "/api/play/rooms", {"entry_cents": 250, "quiz": made["quiz"]["id"]}
    )
    assert status == 201 and room["room"]["entry_cents"] == 250
    other = build_org(cloud, "Other Org", slug_email="other.test")
    cloud.billing.subscribe_app(other.owner, "play")
    stranger = sign_in(cloud, other.owner)
    status, theirs = stranger.call("GET", "/api/play/quizzes")
    assert status == 200 and theirs["quizzes"] == []
    assert (
        stranger.call("POST", "/api/play/sessions", {"quiz": made["quiz"]["id"]})[0]
        == 404
    )
    assert stranger.call("GET", "/api/play/nights")[1]["nights"] == []
