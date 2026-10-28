"""Play's search (``GET /api/play/search``), behind the search box across the top: games, the quizzes this person may
play, the watch list and the open live games that match, case-insensitively, a few of each kind."""

# ruff: noqa: F811
from __future__ import annotations

from life_support import Household

from aira.suite import kit
from aira.suite.play import quizzes, service
from test_suite_play import T0, api, db, env, no_live_model, people, stub  # noqa: F401

QUESTIONS = [
    {"text": f"Question {n}?", "choices": ["Yes", "No"], "answer": 0} for n in range(3)
]


def _quiz(db, who, title, *, shared=True, about=""):
    return quizzes.create(
        db,
        who,
        {"title": title, "about": about, "shared": shared, "questions": QUESTIONS},
    )


def test_search_finds_each_kind_and_keeps_private_quizzes_private(db, people):
    owner, kid, gran = people
    _quiz(db, owner, "Sunday family quiz", about="Birthdays and holidays")
    _quiz(db, owner, "My secret Sunday quiz", shared=False)
    service.add_item(
        db, owner, {"kind": "film", "title": "Paddington", "creator": "Paul King"}
    )
    service.add_item(db, gran, {"kind": "book", "title": "A Sunday book"})
    room = service.create_room(
        db, owner, {"title": "Sunday trivia", "seconds": 30}, now=T0
    )

    mine = service.search(db, owner, "  SUNDAY ", now=T0)
    assert mine["query"] == "SUNDAY"
    assert sorted(q["title"] for q in mine["quizzes"]) == [
        "My secret Sunday quiz",
        "Sunday family quiz",
    ]
    assert [w["title"] for w in mine["watch"]] == ["A Sunday book"]
    assert [r["title"] for r in mine["rooms"]] == ["Sunday trivia"]
    assert set(mine["rooms"][0]) == {
        "id",
        "code",
        "title",
        "host",
        "phase",
        "players",
        "joined",
    }

    # Someone else never sees a private quiz, not even its name.
    theirs = service.search(db, kid, "sunday", now=T0)
    assert [q["title"] for q in theirs["quizzes"]] == ["Sunday family quiz"]
    assert "secret" not in repr(theirs)

    # A maker, a game's name or tagline, a room code, what a quiz is about, who wrote it.
    assert [w["title"] for w in service.search(db, kid, "paul")["watch"]] == [
        "Paddington"
    ]
    assert [g["id"] for g in service.search(db, kid, "sudoku")["games"]] == ["sudoku"]
    assert [
        r["id"] for r in service.search(db, kid, room["code"].lower(), now=T0)["rooms"]
    ] == [room["id"]]
    assert [q["title"] for q in service.search(db, kid, "birthdays")["quizzes"]] == [
        "Sunday family quiz"
    ]
    assert len(service.search(db, kid, owner["name"])["quizzes"]) == 1

    # Nothing typed finds nothing; a LIKE wildcard is a plain character; the limit holds for each kind.
    empty = service.search(db, owner, "   ")
    assert empty["games"] == empty["quizzes"] == empty["watch"] == empty["rooms"] == []
    assert service.search(db, owner, "%")["watch"] == []
    assert len(service.search(db, owner, "o", limit=1)["games"]) == 1


def test_the_search_route_on_the_local_build(env, api):
    kit.forget_schema()
    household = Household(env, api)
    pat = household.owner
    mia = household.add("Mia", age_group="child", pin="2468")
    pat.post("/api/play/watch", {"kind": "show", "title": "Bluey"}, expect=201)
    found = mia.get("/api/play/search?q=blu")
    assert [w["title"] for w in found["watch"]] == ["Bluey"]
    assert found["games"] == [] and found["quizzes"] == [] and found["rooms"] == []
    assert len(mia.get("/api/play/search?q=a&limit=2")["games"]) == 2
    status, body = mia.call("GET", "/api/play/search?q=a&limit=many")
    assert status == 400, body
