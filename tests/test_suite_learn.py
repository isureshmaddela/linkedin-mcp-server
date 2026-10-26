"""Learn (aira/suite/learn): spaced repetition, goals and streaks, XP and badges, server-graded quizzes and maths,
decks on any topic, reading and the family leaderboard, in unit tests, through the single-user server and the
hosted gateway."""

from __future__ import annotations

import json
import random
import sqlite3
import urllib.error
import urllib.request
from datetime import date, datetime, timedelta, timezone
from fractions import Fraction

import pytest
from cloud_support import Browser, build_org, sign_in
from life_support import Household

import auto_apply_assistant as local
import test_careers
from aira import suite
from aira.suite import kit, life, public
from aira.suite.learn import service, starters, store

env = test_careers.env
api = test_careers.api
stub = test_careers.stub

NOON = datetime(2026, 10, 21, 12, 0, tzinfo=timezone.utc)


@pytest.fixture(autouse=True)
def no_model():
    """Templates only: the machine's own writing model must not answer in tests."""
    previous = suite.gateway()
    suite.set_gateway(suite._NoGateway())
    yield
    suite.set_gateway(previous)


@pytest.fixture
def db(tmp_path):
    kit.forget_schema()
    public.set_index(public._MemoryIndex())
    connection = sqlite3.connect(tmp_path / "learn.sqlite3")
    connection.row_factory = sqlite3.Row
    store.ensure(connection)
    yield connection
    connection.close()


class Req:
    def __init__(self, header: str = "") -> None:
        self._header = header
        self.user = {"email": "pat@home.example"}

    def header(self, name: str, default: str = "") -> str:
        return self._header if name == "X-Aira-Profile" else default


def person(db, name="Pat", age_group="adult"):
    owner = life.current(Req(), db) if not life.profiles(db) else life.profiles(db)[0]
    if name == owner["name"]:
        return owner
    return life.add(db, owner, {"name": name, "age_group": age_group})


def small_deck(db, owner, n=5, shared=False):
    deck = store.create_deck(db, owner["id"], {"title": "Colours", "shared": shared})
    for index in range(n):
        store.add_card(
            db, deck["id"], {"front": f"Colour {index}", "back": f"Answer {index}"}
        )
    db.commit()
    return deck


# ---------------------------------------------------------------------------
# spaced repetition
# ---------------------------------------------------------------------------


def test_sm2_schedule_grows_with_success_and_resets_on_a_miss():
    today = date(2026, 10, 21)
    first = service.schedule({}, "good", today)
    assert first == {
        "ease": 2.5,
        "interval_days": 1,
        "reps": 1,
        "lapses": 0,
        "due": "2026-10-22",
    }
    second = service.schedule(first, "good", today)
    assert second["interval_days"] == 6 and second["reps"] == 2
    third = service.schedule(second, "good", today)
    assert third["interval_days"] == 15  # 6 x 2.5
    easy = service.schedule(third, "easy", today)
    assert easy["ease"] == 2.6 and easy["interval_days"] == round(15 * 2.6 * 1.3)
    hard = service.schedule(third, "hard", today)
    assert hard["ease"] == 2.36 and hard["interval_days"] == 18
    missed = service.schedule(third, "again", today)
    assert (
        missed["interval_days"] == 0 and missed["reps"] == 0 and missed["lapses"] == 1
    )
    assert missed["due"] == today.isoformat() and missed["ease"] == 1.96
    worst = {"ease": 1.4}
    for _ in range(5):
        worst = service.schedule(worst, "again", today)
    assert worst["ease"] == 1.3
    assert service.schedule({}, "easy", today)["interval_days"] == 3
    assert service.schedule(first, "hard", today)["interval_days"] == 3
    with pytest.raises(ValueError):
        service.schedule({}, "perfect", today)
    assert service.when_label(1) == "Tomorrow" and service.when_label(0).startswith(
        "Again"
    )


def test_study_mixes_reviews_and_new_cards_within_daily_limits(db):
    me = person(db)
    store.save_settings(db, me, {"new_per_day": 4, "goal_value": 50})
    deck = small_deck(db, me, n=8)
    queue = service.study_queue(db, me, now=NOON)
    assert [c["is_new"] for c in queue["cards"]] == [True] * 4
    for card in queue["cards"]:
        service.answer(
            db,
            me,
            card["id"],
            "again" if card is queue["cards"][0] else "good",
            seconds=500,
            now=NOON,
        )
    with pytest.raises(ValueError, match="new cards for today"):
        others = [c for c in store.cards(db, deck["id"])]
        service.answer(db, me, others[-1]["id"], "good", now=NOON)
    after = service.study_queue(db, me, now=NOON)
    assert [c["is_new"] for c in after["cards"]] == [
        False
    ]  # the missed card comes back today
    day = store.day_row(db, me["id"], "2026-10-21")
    assert (
        day["cards"] == 4
        and day["new_cards"] == 4
        and day["seconds"] == 4 * service.SECONDS_CAP
    )
    tomorrow = service.study_queue(db, me, now=NOON + timedelta(days=1))
    assert (
        sum(1 for c in tomorrow["cards"] if not c["is_new"]) == 4
        and sum(1 for c in tomorrow["cards"] if c["is_new"]) == 4
    )


def test_goal_streak_and_the_weekly_freeze_follow_local_days(db):
    kit.save_business(db, {"timezone": "Asia/Kolkata"})
    me = person(db)
    store.save_settings(
        db, me, {"goal_kind": "cards", "goal_value": 2, "new_per_day": 100}
    )
    small_deck(db, me, n=40)
    # 19:00 UTC on the 20th is already the 21st in India.
    assert service.today_of(
        db, datetime(2026, 10, 20, 19, 0, tzinfo=timezone.utc)
    ) == date(2026, 10, 21)

    def practise(moment, n=2):
        for card in service.study_queue(db, me, now=moment)["cards"][:n]:
            result = service.answer(db, me, card["id"], "good", now=moment)
        return result

    monday = datetime(2026, 10, 19, 6, 0, tzinfo=timezone.utc)
    result = practise(monday)
    assert (
        result["goal"]["done"] and result["goal"]["just_met"] and result["streak"] == 1
    )
    practise(monday + timedelta(days=1))
    # Wednesday is missed; Thursday's practice uses this week's freeze and the streak holds.
    thursday = monday + timedelta(days=3)
    result = practise(thursday)
    assert result["goal"]["freeze_used"] is True and result["streak"] == 4
    # Friday missed, Saturday practised: the freeze for this week is used up, so the streak restarts.
    result = practise(monday + timedelta(days=5))
    assert result["goal"]["freeze_used"] is False and result["streak"] == 1
    # Partly done: one card of two, no goal yet.
    one = practise(monday + timedelta(days=6), n=1)
    assert one["goal"]["done"] is False and one["goal"]["progress"] == 1
    # The evening warning: after 18:00 local time, a live streak with the goal still open.
    assert service.streak_at_risk(
        db, me, now=datetime(2026, 10, 25, 13, 0, tzinfo=timezone.utc)
    )  # 18:30 IST
    assert not service.streak_at_risk(
        db, me, now=datetime(2026, 10, 25, 10, 0, tzinfo=timezone.utc)
    )


def test_xp_levels_and_badges(db):
    me = person(db)
    assert (
        service.level_of(0)["level"] == 1
        and service.level_of(50)["level"] == 2
        and service.level_of(199)["level"] == 2
    )
    assert service.level_of(200) == {
        "level": 3,
        "xp": 200,
        "next_at": 450,
        "this_at": 200,
    }
    deck = service.add_starter(db, me, "solar-system", now=NOON)
    assert len(store.cards(db, deck["id"])) == len(
        starters.BY_KEY["solar-system"]["cards"]
    )
    assert "first_deck" in store.person(db, me)["badges"]
    card = service.study_queue(db, me, now=NOON)["cards"][0]
    result = service.answer(db, me, card["id"], "good", now=NOON)
    assert result["xp_gained"] == service.XP_FOR["good"] + service.XP_NEW_CARD
    assert [b["key"] for b in result["new_badges"]] == ["first_review"]
    assert store.person(db, me)["xp"] == result["xp_gained"]
    with pytest.raises(LookupError):
        service.add_starter(db, me, "no-such-deck")


def test_quizzes_are_graded_on_the_server(db):
    me = person(db)
    deck = small_deck(db, me, n=6)
    quiz = service.create_quiz(db, me, deck["id"], 4, rng=random.Random(7))
    assert len(quiz["questions"]) == 4 and all(
        len(q["options"]) == 4 for q in quiz["questions"]
    )
    assert all("correct" not in q for q in quiz["questions"])
    stored = json.loads(
        db.execute(
            "SELECT questions FROM ln_quizzes WHERE id = ?", (quiz["id"],)
        ).fetchone()[0]
    )
    right = [q["correct"] for q in stored]
    # The client sends junk for one answer and a wrong one for another: only real right answers count.
    answers = [right[0], "1", (right[2] + 1) % 4, right[3]]
    result = service.submit_quiz(db, me, quiz["id"], answers, now=NOON)
    assert result["score"] == 2 and result["total"] == 4
    assert (
        result["results"][1]["your"] is None and result["results"][0]["right"] is True
    )
    with pytest.raises(ValueError, match="already handed in"):
        service.submit_quiz(db, me, quiz["id"], right, now=NOON)
    perfect = service.create_quiz(db, me, deck["id"], 3, rng=random.Random(1))
    stored = json.loads(
        db.execute(
            "SELECT questions FROM ln_quizzes WHERE id = ?", (perfect["id"],)
        ).fetchone()[0]
    )
    result = service.submit_quiz(
        db, me, perfect["id"], [q["correct"] for q in stored], now=NOON
    )
    assert result["score"] == 3 and "quiz_perfect" in [
        b["key"] for b in result["new_badges"]
    ]
    with pytest.raises(ValueError, match="two different answers"):
        service.create_quiz(db, me, small_deck(db, me, n=1)["id"])
    other = person(db, "Mia", "child")
    with pytest.raises(LookupError):
        service.submit_quiz(db, other, quiz["id"], right)


def test_maths_practice_is_generated_and_graded_on_the_server(db):
    me = person(db)
    for level in service.MATHS_LEVELS:
        practice = service.create_maths(
            db, me, {"level": level, "count": 5}, rng=random.Random(3)
        )
        stored = json.loads(
            db.execute(
                "SELECT questions FROM ln_quizzes WHERE id = ?", (practice["id"],)
            ).fetchone()[0]
        )
        assert all("answer" not in q for q in practice["questions"])
        result = service.submit_maths(
            db, me, practice["id"], [q["answer"] for q in stored], now=kit.utcnow()
        )
        assert result["score"] == 5, (level, result["results"])
    assert (
        service.parse_number("3/4")
        == Fraction(3, 4)
        == service.parse_number("0.75")
        == service.parse_number("0,75")
    )
    assert service.parse_number("1 1/4") == Fraction(5, 4)
    assert service.parse_number("12%") == 12 and service.parse_number("1,000") == 1000
    assert (
        service.parse_number("abc") is None
        and service.parse_number("") is None
        and service.parse_number("1/0") is None
    )
    timed = service.create_maths(
        db, me, {"level": "times", "mode": "timed", "count": 3}, rng=random.Random(5)
    )
    stored = json.loads(
        db.execute(
            "SELECT questions FROM ln_quizzes WHERE id = ?", (timed["id"],)
        ).fetchone()[0]
    )
    late = service.submit_maths(
        db,
        me,
        timed["id"],
        [q["answer"] for q in stored],
        now=kit.utcnow() + timedelta(minutes=10),
    )
    assert (
        late["late"] is True
        and late["xp_gained"] == 0
        and "Time ran out" in late["message"]
    )
    with pytest.raises(ValueError):
        service.create_maths(db, me, {"level": "calculus"})


def test_starter_decks_are_complete():
    keys = [deck["key"] for deck in starters.STARTERS]
    assert len(keys) == len(set(keys)) >= 10
    for deck in starters.STARTERS:
        assert 15 <= len(deck["cards"]) <= 30, deck["key"]
        assert all(front and back for front, back, _hint in deck["cards"])
    assert starters.match("the planets in space")["key"] == "solar-system"
    assert starters.match("how to spot a scam")["key"] == "safe-online"
    assert starters.match("pottery glazes") is None


def test_decks_on_any_topic_with_and_without_a_model(db):
    me = person(db)
    matched = service.generate_deck(
        db, me, {"topic": "Planets and space", "count": 8}, now=NOON
    )
    assert matched["cards_added"] == 8 and "starter deck" in matched["note"]
    assert store.deck(db, matched["id"])["source"] == "template"
    plain = service.generate_deck(db, me, {"topic": "Pottery", "count": 6}, now=NOON)
    assert plain["cards_added"] == 6 and "template" in plain["note"]
    assert "Pottery" in store.cards(db, plain["id"])[0]["front"]

    class Model:
        def __init__(self, reply):
            self.reply = reply

        def model(self, prompt):
            return self.reply

    cards = [{"front": f"Q{i}", "back": f"A{i}"} for i in range(6)]
    suite.set_gateway(Model(json.dumps({"title": "Birds", "cards": cards})))
    written = service.generate_deck(
        db, me, {"topic": "Birds", "count": 5, "age_group": "child"}, now=NOON
    )
    assert (
        written["cards_added"] == 5 and store.deck(db, written["id"])["source"] == "ai"
    )
    suite.set_gateway(
        Model(json.dumps({"title": "Birds", "cards": [{"front": "only one"}]}))
    )
    fallback = service.generate_deck(db, me, {"topic": "Birds", "count": 5}, now=NOON)
    assert (
        store.deck(db, fallback["id"])["source"] == "template"
        and fallback["cards_added"] >= 5
    )
    with pytest.raises(ValueError):
        service.generate_deck(db, me, {"topic": ""})


def test_reading_counts_toward_the_goal_with_capped_xp(db):
    me = person(db)
    store.save_settings(db, me, {"goal_kind": "minutes", "goal_value": 20})
    book = service.add_book(db, me, {"title": "The Hobbit", "author": "Tolkien"})
    first = service.log_reading(
        db, me, {"minutes": 25, "book_id": book["id"]}, now=NOON
    )
    assert first["goal"]["done"] and first["xp_gained"] == 25 + service.XP_GOAL
    second = service.log_reading(db, me, {"minutes": 40}, now=NOON)
    assert second["xp_gained"] == 5  # 30 XP a day from reading at most
    assert service.finish_book(db, me, book["id"])["status"] == "finished"
    assert service.books(db, me)[0]["minutes"] == 25
    store.save_settings(db, me, {"reading_counts": False})
    assert service.goal_state(db, me, date(2026, 10, 21))["minutes_today"] == 0
    with pytest.raises(ValueError):
        service.log_reading(db, me, {"minutes": 0})


# ---------------------------------------------------------------------------
# the single-user build
# ---------------------------------------------------------------------------


@pytest.fixture
def local_index():
    public.set_index(local._public_link_index)


def test_a_household_learns_together_on_the_local_build(env, api, local_index):
    kit.forget_schema()
    home = Household(env, api)
    parent = home.owner
    mia = home.add("Mia", age_group="child")
    # Mia adds the times tables and studies until her goal (10 cards for a child) is met.
    starters_listed = mia.get("/api/learn/decks")["starters"]
    assert starters_listed[0]["age_group"] == "child"
    deck = mia.post("/api/learn/starters", {"key": "times-2-6"}, expect=201)["deck"]
    assert deck["progress"]["total"] == 30 and deck["mine"]
    overview = mia.get("/api/learn/overview")
    assert (
        overview["settings"]["goal_value"] == 10 and overview["counts"]["new"] == 8
    )  # a child's new-card limit
    for _ in range(3):
        for card in mia.get(f"/api/learn/study?deck_id={deck['id']}")["cards"]:
            last = mia.post(
                "/api/learn/study/answer",
                {"card_id": card["id"], "grade": "good", "seconds": 8},
            )
    assert last["goal"]["progress"] == 8
    status, refused = mia.call(
        "POST", "/api/learn/study/answer", {"card_id": card["id"], "grade": "good"}
    )
    assert status == 200  # a review of a card already seen is still allowed
    quiz = mia.post("/api/learn/maths", {"level": "times", "count": 3}, expect=201)[
        "quiz"
    ]
    result = mia.post(
        f"/api/learn/maths/{quiz['id']}/submit", {"answers": ["1", "2", "3"]}
    )
    assert result["total"] == 3 and "message" in result
    # The parent sees the family leaderboard: names and numbers, nothing else.
    board = parent.get("/api/learn/leaderboard")["people"]
    assert board[0]["name"] == "Mia" and board[0]["cards_week"] >= 9
    assert set(board[0]) == {
        "profile_id",
        "name",
        "emoji",
        "color",
        "streak",
        "xp_week",
        "cards_week",
        "level",
    }
    # A private deck stays private; a shared deck is studied with progress per person.
    private = parent.post(
        "/api/learn/decks",
        {"title": "Tax words", "cards": [{"front": "VAT", "back": "Value added tax"}]},
        expect=201,
    )["deck"]
    assert mia.call("GET", f"/api/learn/decks/{private['id']}")[0] == 404
    shared = parent.post(
        "/api/learn/decks",
        {
            "title": "Family words",
            "shared": True,
            "cards": [
                {"front": "Abuela", "back": "Grandma"},
                {"front": "Tío", "back": "Uncle"},
            ],
        },
        expect=201,
    )["deck"]
    seen = mia.get(f"/api/learn/study?deck_id={shared['id']}")["cards"]
    assert [
        c["front"] for c in seen
    ] == []  # Mia used today's new cards on the times tables
    first = parent.get(f"/api/learn/study?deck_id={shared['id']}")["cards"][0]
    parent.post("/api/learn/study/answer", {"card_id": first["id"], "grade": "good"})
    assert (
        parent.get(f"/api/learn/decks/{shared['id']}")["deck"]["progress"]["seen"] == 1
    )
    assert mia.get(f"/api/learn/decks/{shared['id']}")["deck"]["progress"]["seen"] == 0
    assert (
        mia.call("PATCH", f"/api/learn/decks/{shared['id']}", {"title": "Mine now"})[0]
        == 403
    )
    assert (
        mia.call("POST", f"/api/learn/decks/{deck['id']}/link", {})[0] == 403
    )  # a child cannot share publicly
    # The parent shares a deck as a page a relative studies without signing in.
    link = parent.post(f"/api/learn/decks/{shared['id']}/link")["link"]
    request = urllib.request.Request(
        env.base + f"/api/public/{link['token']}", headers={"X-Requested-With": "aira"}
    )
    with urllib.request.urlopen(request, timeout=30) as response:
        page = json.loads(response.read())
    assert page["kind"] == "deck" and [c["front"] for c in page["page"]["cards"]] == [
        "Abuela",
        "Tío",
    ]
    parent.ok("DELETE", f"/api/learn/decks/{shared['id']}/link")
    with pytest.raises(urllib.error.HTTPError):
        urllib.request.urlopen(request, timeout=30)
    # Today and the pulse speak for the person asking.
    day = mia.get("/api/life/today")
    card = next(c for c in day["cards"] if c["app"] == "learn")
    assert card["title"] == "Practise today" and card["link"] == "#/learn?tab=study"
    assert mia.get("/api/life/pulse")["apps"]["learn"]["badge"] >= 0
    # Reading and the hub.
    book = parent.post("/api/learn/books", {"title": "Matilda"}, expect=201)["book"]
    parent.post("/api/learn/reading", {"minutes": 15, "book_id": book["id"]})
    assert parent.get("/api/learn/books")["minutes_today"] == 15
    user = env.user()
    local._bind_user(user)
    try:
        with local._connect() as connection:
            steps = {
                step["id"]: step["done"] for step in kit.CHECKLISTS["learn"](connection)
            }
            summary = kit.SUMMARIES["learn"](connection)
    finally:
        local._unbind_user()
    assert steps == {"deck": True, "study": True, "goal": False, "quiz": True}
    assert summary["cards"][0]["value"] >= 9


# ---------------------------------------------------------------------------
# the hosted build
# ---------------------------------------------------------------------------


def test_hosted_learn_needs_the_app_and_keeps_members_apart(cloud):
    kit.forget_schema()
    org = build_org(cloud)
    owner = sign_in(cloud, org.owner)
    assert owner.call("GET", "/api/learn/overview")[0] == 402
    cloud.billing.subscribe_app(org.owner, "learn")
    decks = {}
    for who in ("owner", "candidate", "coach", "auditor", "billing"):
        browser = sign_in(cloud, getattr(org, who))
        assert browser.call("GET", "/api/learn/overview")[0] == 200, who
        status, created = browser.call(
            "POST", "/api/learn/starters", {"key": "world-capitals"}
        )
        assert status == 201, (who, created)
        decks[who] = created["deck"]["id"]
    candidate = sign_in(cloud, org.candidate)
    coach = sign_in(cloud, org.coach)
    # A member's own deck is theirs: another member cannot open it or study it.
    assert coach.call("GET", f"/api/learn/decks/{decks['candidate']}")[0] == 404
    status, queue = candidate.call("GET", "/api/learn/study")
    card = queue["cards"][0]
    assert (
        candidate.call(
            "POST", "/api/learn/study/answer", {"card_id": card["id"], "grade": "good"}
        )[0]
        == 200
    )
    assert (
        coach.call(
            "POST", "/api/learn/study/answer", {"card_id": card["id"], "grade": "good"}
        )[0]
        == 404
    )
    status, board = owner.call("GET", "/api/learn/leaderboard")
    assert status == 200 and len(board["people"]) == 5
    # Another organization sees nothing of this one.
    other = build_org(cloud, "Other Org", slug_email="other.test")
    cloud.billing.subscribe_app(other.owner, "learn")
    theirs = sign_in(cloud, other.owner)
    status, listed = theirs.call("GET", "/api/learn/decks")
    assert status == 200 and listed["decks"] == []
    assert theirs.call("GET", f"/api/learn/decks/{decks['owner']}")[0] == 404
    assert Browser(cloud).call("GET", "/api/learn/overview")[0] == 401
