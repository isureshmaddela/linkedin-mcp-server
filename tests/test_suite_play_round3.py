# ruff: noqa: F811  (pytest fixtures imported by name and used as arguments)
"""Play: the problems an independent tester found after the quizzes, challenges and quiz nights went in. A private
quiz stays private on Today and in the activity feed, a quiz hosted live plays in full, the person who wrote a quiz
runs it but cannot answer it, undoing a guest's payment takes their points too, plans cannot be in the past, a
challenge on a quiz made private goes away, watch-list search takes % and _ literally, and link guests cannot fill a
room so the family is shut out."""

from __future__ import annotations

from datetime import timedelta

import pytest
from cloud_support import build_org, sign_in
from test_suite_play_extras import (  # noqa: F401  (fixtures used by name)
    QUESTIONS,
    T0,
    db,
    no_live_model,
    people,
    secret_of,
)

from aira.suite import kit
from aira.suite.play import play_routes, quizzes, service

TEN = [
    {"text": f"Question number {i + 1}?", "choices": ["Right", "Wrong"], "answer": 0}
    for i in range(10)
]


def _play_quiz(db, who, quiz_id, *, right=True, at=T0):
    session = service.start(db, who, {"quiz": quiz_id, "mode": "relaxed"}, now=at)
    answers = secret_of(db, session["id"])["answers"]
    if not right:
        answers = [answers[0]] + [None] * (len(answers) - 1)
    return service.finish(db, who, session["id"], {"answers": answers}, now=at)


def _today_lines(db, who):
    cards = play_routes._today(db, who)
    return [line["text"] for card in cards for line in card["lines"]]


def test_a_private_quiz_never_reaches_the_rest_of_the_family(db, people):
    owner, kid, gran = people
    secret = quizzes.create(
        db,
        owner,
        {"title": "Surprise party for Mia", "questions": QUESTIONS, "shared": False},
    )
    first = _play_quiz(db, owner, secret["id"], right=False)
    assert first["best"] is True  # a first play is always a personal best
    _play_quiz(db, owner, secret["id"], at=T0 + timedelta(minutes=1))  # improves
    later = T0 + timedelta(minutes=5)
    assert service.recent_best(db, kid, now=later) is None
    assert not any("Surprise" in line for line in _today_lines(db, kid))
    assert not any("Surprise" in line for line in _today_lines(db, gran))
    feed = kit.activity(db, app="play")
    assert not any("Surprise" in item["title"] for item in feed)


def test_a_writers_own_games_are_not_promoted_but_others_are(db, people):
    owner, kid, gran = people
    shared = quizzes.create(
        db, owner, {"title": "Family facts", "questions": QUESTIONS}
    )
    _play_quiz(db, owner, shared["id"], right=False)
    _play_quiz(db, owner, shared["id"], at=T0 + timedelta(minutes=1))
    assert service.recent_best(db, kid, now=T0 + timedelta(minutes=2)) is None
    assert not any("new best" in item["title"] for item in kit.activity(db, app="play"))
    # Someone who did not write it does show up, by the quiz's name.
    _play_quiz(db, gran, shared["id"], at=T0 + timedelta(minutes=3))
    best = service.recent_best(db, kid, now=T0 + timedelta(minutes=4))
    assert best and best["name"] == "Rosa" and best["game"] == "Family facts"


def test_a_quiz_hosted_live_plays_every_question_by_default(db, people):
    owner, kid, _gran = people
    quiz = quizzes.create(db, kid, {"title": "Ten of them", "questions": TEN})
    room = service.create_room(db, owner, {"quiz": quiz["id"]}, now=T0)
    assert room["total"] == 10
    # Asking for fewer still works.
    fewer = service.create_room(db, owner, {"quiz": quiz["id"], "questions": 4}, now=T0)
    assert fewer["total"] == 4
    # The quiz book stops at 15.
    with pytest.raises(ValueError, match="at most 15"):
        service.create_room(db, owner, {"questions": 20}, now=T0)
    assert service.create_room(db, owner, {}, now=T0)["total"] == 8


def test_the_writer_runs_their_quiz_live_but_cannot_answer_it(db, people):
    owner, kid, gran = people
    quiz = quizzes.create(db, owner, {"title": "Pat's quiz", "questions": QUESTIONS})
    room = service.create_room(
        db,
        owner,
        {"quiz": quiz["id"], "entry_cents": 450, "prize": "A cake", "seconds": 60},
        now=T0,
    )
    assert room["me"]["can_answer"] is False
    service.join_room(db, gran, room["id"], now=T0)
    started = service.start_room(db, owner, room["id"], now=T0 + timedelta(seconds=1))
    assert started["question"]
    right = started_answer(db, room["id"])
    with pytest.raises(PermissionError, match="quizmaster"):
        service.answer_as(db, owner, room["id"], right, now=T0 + timedelta(seconds=2))
    state = service.answer_as(
        db, gran, room["id"], right, now=T0 + timedelta(seconds=2)
    )
    assert state["me"]["can_answer"] is True
    board = {p["name"]: p for p in state["players"]}
    assert (
        board[owner["name"]]["quizmaster"] is True
        and board[owner["name"]]["score"] == 0
    )
    assert board["Rosa"]["quizmaster"] is False and board["Rosa"]["score"] > 0
    # Someone else hosting Mia's quiz: Mia, the writer, cannot answer either; the host can.
    hers = quizzes.create(db, kid, {"title": "Mia's quiz", "questions": QUESTIONS})
    other = service.create_room(db, owner, {"quiz": hers["id"]}, now=T0)
    assert other["me"]["can_answer"] is True
    joined = service.join_room(db, kid, other["id"], now=T0)
    assert joined["me"]["can_answer"] is False
    service.start_room(db, owner, other["id"], now=T0 + timedelta(seconds=1))
    with pytest.raises(PermissionError):
        service.answer_as(db, kid, other["id"], 0, now=T0 + timedelta(seconds=2))
    service.answer_as(db, owner, other["id"], 0, now=T0 + timedelta(seconds=2))
    # Quiz-book rooms have no quizmaster.
    book = service.create_room(db, owner, {}, now=T0)
    assert book["me"]["can_answer"] is True
    assert not any(p["quizmaster"] for p in book["players"])


def test_undoing_a_payment_takes_the_points_scored_while_paid(db, people):
    owner, _kid, _gran = people
    room = service.create_room(db, owner, {"entry_cents": 300, "seconds": 60}, now=T0)
    token, _ = service.join_guest(db, room["id"], "Sam", now=T0)
    sam = service.guest_player(db, room["id"], token)
    service.mark_paid(db, owner, room["id"], sam["id"], True, now=T0)
    service.start_room(db, owner, room["id"], now=T0 + timedelta(seconds=1))
    service.answer(
        db,
        room["id"],
        sam,
        started_answer(db, room["id"]),
        now=T0 + timedelta(seconds=2),
    )
    scored = service.room_state(
        db,
        service._room(db, room["id"]),
        viewer_profile=owner["id"],
        now=T0 + timedelta(seconds=3),
    )
    assert next(p for p in scored["players"] if p["name"] == "Sam")["score"] > 0
    undone = service.mark_paid(
        db, owner, room["id"], sam["id"], False, now=T0 + timedelta(seconds=4)
    )
    entry = next(p for p in undone["players"] if p["name"] == "Sam")
    assert entry["score"] == 0 and entry["paid"] is False and entry["answered"] is False
    assert undone["takings_cents"] == 0
    assert kit.revenue(db, app="play")["total_cents"] == 0


def started_answer(db, room_id):
    questions = kit.loads(
        db.execute(
            "SELECT questions FROM pl_rooms WHERE id = ?", (room_id,)
        ).fetchone()[0],
        [],
    )
    return questions[0]["answer"]


def test_a_plan_cannot_be_even_an_hour_in_the_past(db, people):
    owner = people[0]
    item = service.add_item(db, owner, {"kind": "film", "title": "Up"})
    with pytest.raises(ValueError, match="has not passed"):
        service.add_plan(
            db,
            owner,
            item["id"],
            {"planned_for": kit.iso(kit.utcnow() - timedelta(minutes=50))},
        )
    with pytest.raises(ValueError, match="has not passed"):
        service.add_plan(
            db,
            owner,
            item["id"],
            {"planned_for": kit.iso(kit.utcnow() - timedelta(minutes=10))},
        )
    # "Right now" typed into the form a moment ago is still fine.
    made = service.add_plan(
        db,
        owner,
        item["id"],
        {"planned_for": kit.iso(kit.utcnow() - timedelta(seconds=30))},
    )
    assert len(made["plans"]) == 1


def test_a_challenge_on_a_quiz_made_private_goes_away(db, people):
    owner, kid, gran = people
    quiz = quizzes.create(db, kid, {"title": "Mia's quiz", "questions": QUESTIONS})
    played = _play_quiz(db, owner, quiz["id"])
    service.challenge(
        db, owner, {"session_id": played["id"], "to": [gran["id"]]}, now=T0
    )
    assert len(service.challenges(db, gran)["incoming"]) == 1
    quizzes.update(db, kid, quiz["id"], {"shared": False})
    assert service.challenges(db, gran)["incoming"] == []
    assert service.challenges_pulse(db, gran, now=T0)[0] == 0
    assert not any("challenged you" in line for line in _today_lines(db, gran))


def test_watch_search_takes_percent_and_underscore_literally(db, people):
    owner = people[0]
    service.add_item(db, owner, {"kind": "film", "title": "Film one"})
    service.add_item(db, owner, {"kind": "film", "title": "100% Wolf"})
    service.add_item(db, owner, {"kind": "book", "title": "snake_case notes"})
    assert [i["title"] for i in service.watch_list(db, owner, query="%")] == [
        "100% Wolf"
    ]
    assert [i["title"] for i in service.watch_list(db, owner, query="_")] == [
        "snake_case notes"
    ]
    assert [i["title"] for i in service.watch_list(db, owner, query="!")] == []
    assert len(service.watch_list(db, owner, query="o")) == 3


def test_link_guests_cannot_fill_a_room_and_shut_the_family_out(db, people):
    owner, kid, gran = people
    room = service.create_room(db, owner, {}, now=T0)
    # One visitor can add a few names (a family on one phone), not a crowd.
    for n in range(service.GUESTS_PER_VISITOR):
        service.join_guest(db, room["id"], f"Cousin {n}", client="1.2.3.4", now=T0)
    with pytest.raises(ValueError, match="already joined"):
        service.join_guest(db, room["id"], "Cousin X", client="1.2.3.4", now=T0)
    # Many visitors: the guests stop at the cap...
    n = service.GUESTS_PER_VISITOR
    visitor = 0
    while n < service.MAX_GUESTS:
        service.join_guest(db, room["id"], f"Guest {n}", client=f"v{visitor}", now=T0)
        n += 1
        visitor += n % 2
    with pytest.raises(ValueError, match="full"):
        service.join_guest(db, room["id"], "One more", client="new", now=T0)
    # ...and the family still gets in.
    assert service.join_room(db, kid, room["id"], now=T0)["me"]
    assert service.join_room(db, gran, room["id"], now=T0)["me"]


def test_hosted_private_quiz_stays_off_a_colleagues_today(cloud):
    kit.forget_schema()
    org = build_org(cloud)
    cloud.billing.subscribe_app(org.owner, "play")
    owner = sign_in(cloud, org.owner)
    coach = sign_in(cloud, org.coach)
    status, made = owner.call(
        "POST",
        "/api/play/quizzes",
        {"title": "Secret plan", "questions": QUESTIONS, "shared": False},
    )
    assert status == 201
    status, session = owner.call(
        "POST", "/api/play/sessions", {"quiz": made["quiz"]["id"]}
    )
    assert status == 201
    status, _done = owner.call(
        "POST",
        f"/api/play/sessions/{session['session']['id']}/finish",
        {"answers": [0, 0, 0]},
    )
    assert status == 200
    status, today = coach.call("GET", "/api/life/today")
    assert status == 200
    assert "Secret plan" not in str(today)
    # The owner, as writer and host of a shared quiz, runs it but cannot answer.
    status, shared = owner.call(
        "POST", "/api/play/quizzes", {"title": "Team quiz", "questions": QUESTIONS}
    )
    status, room = owner.call("POST", "/api/play/rooms", {"quiz": shared["quiz"]["id"]})
    assert status == 201 and room["room"]["total"] == 3
    assert room["room"]["me"]["can_answer"] is False
    room_id = room["room"]["id"]
    status, joined = coach.call("GET", f"/api/play/rooms/{room_id}")
    assert status == 200 and joined["room"]["me"] is None
    status, joined = coach.call("POST", f"/api/play/rooms/{room_id}/join")
    assert status == 200 and joined["room"]["me"]["can_answer"] is True
    owner.call("POST", f"/api/play/rooms/{room_id}/start")
    assert (
        owner.call("POST", f"/api/play/rooms/{room_id}/answer", {"choice": 0})[0] == 403
    )
    assert (
        coach.call("POST", f"/api/play/rooms/{room_id}/answer", {"choice": 0})[0] == 200
    )
