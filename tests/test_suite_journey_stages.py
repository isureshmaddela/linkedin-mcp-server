"""Life stages (aira/suite/journey/lifebook.py, stages.py): a life from before birth to remembrance.

The stage comes from the birth date; each stage has a checklist by area of life (with important open items carried
along), milestones and ideas for each interest; the life book keeps growth, vaccines and check-ups (reminded on Today
and in the pulse), schools, work and homes; one's own wishes; someone you looked after can be remembered. Everything
is the keeper's own: on its own, through the single-user server for a household, and in the hosted build.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import date, timedelta

import pytest
from cloud_support import build_org, sign_in
from life_support import Household

import test_careers
from aira import apps, suite
from aira.suite import kit, life, public
from aira.suite.journey import journey_routes, lifebook, service, stages, store

env = test_careers.env
api = test_careers.api
stub = test_careers.stub


class _NoModel:
    def model(self, prompt):
        return None


class _Coach:
    """A writing model that answers with a plan."""

    def __init__(self) -> None:
        self.prompts: list[str] = []

    def model(self, prompt):
        self.prompts.append(prompt)
        return json.dumps(
            {
                "summary": "A calm, strong start.",
                "steps": [
                    {
                        "title": "Book the six-month check-up",
                        "description": "Ask about vaccines.",
                        "area": "health",
                    },
                    {
                        "title": "Read a picture book each night",
                        "description": "",
                        "area": "mind",
                    },
                    {"title": "Something", "area": "nonsense"},
                ],
            }
        )


@pytest.fixture(autouse=True)
def _no_model():
    previous = suite.gateway()
    suite.set_gateway(_NoModel())
    yield
    suite.set_gateway(previous)


class Req:
    def __init__(self, header: str = "") -> None:
        self._header = header
        self.user = {"email": "pat@home.example"}

    def header(self, name: str, default: str = "") -> str:
        return self._header if name == "X-Aira-Profile" else default


@pytest.fixture
def db(tmp_path):
    kit.forget_schema()
    public.set_index(public._MemoryIndex())
    connection = sqlite3.connect(tmp_path / "stages.sqlite3")
    connection.row_factory = sqlite3.Row
    store.ensure(connection)
    yield connection
    connection.close()


@pytest.fixture
def people(db):
    pat = life.current(Req(), db)
    sam = life.add(db, pat, {"name": "Sam", "age_group": "adult"})
    tia = life.add(db, pat, {"name": "Tia", "age_group": "teen"})
    return {"pat": pat, "sam": sam, "tia": tia}


def _today(db) -> date:
    return service.today(db)


def _ago(db, **delta) -> str:
    now = _today(db)
    if "years" in delta:
        return now.replace(year=now.year - delta["years"]).isoformat()
    return (now - timedelta(**delta)).isoformat()


# ---------------------------------------------------------------------------
# the knowledge: stages, items, interests
# ---------------------------------------------------------------------------


def test_the_stages_cover_a_whole_life_without_gaps():
    living = stages.LIVING_STAGES
    assert living[0]["id"] == "pregnancy" and living[-1]["id"] == "elder"
    for before, after in zip(living, living[1:], strict=False):
        assert before["to_months"] == after["from_months"], (before["id"], after["id"])
    assert stages.stage_for(-3)["id"] == "pregnancy"
    assert stages.stage_for(0)["id"] == "baby"
    assert stages.stage_for(11)["id"] == "baby"
    assert stages.stage_for(12)["id"] == "toddler"
    assert stages.stage_for(13 * 12)["id"] == "teen"
    assert stages.stage_for(18 * 12)["id"] == "young_adult"
    assert stages.stage_for(40 * 12)["id"] == "midlife"
    assert stages.stage_for(65 * 12)["id"] == "retirement"
    assert stages.stage_for(105 * 12)["id"] == "elder"
    assert stages.stage_for(400 * 12)["id"] == "elder"
    assert stages.stage_for(30, passed=True)["id"] == "remembered"


def test_every_item_and_idea_is_well_formed():
    ids = [item["id"] for stage in stages.STAGES for item in stage["items"]]
    assert len(ids) == len(set(ids))
    record_kinds = set(lifebook.RECORD_KINDS)
    for item in stages.ITEMS.values():
        assert item["area"] in stages.AREA_IDS, item
        what, _, kind = item["do"].partition(":")
        assert what in {
            "check",
            "habit",
            "goal",
            "document",
            "record",
            "wishes",
            "interests",
            "app",
        }, item
        if what == "document":
            assert kind in service.DOCUMENT_KINDS, item
        if what == "record":
            assert kind in record_kinds, item
        if what == "goal":
            assert kind in ("", "money"), item
        if what == "app":
            assert kind in apps.BY_ID, item
        assert item["title"] and item["description"][-1] in ".?", item
    for stage in stages.STAGES:
        assert stage["items"], stage["id"]
    groups = {group["id"] for group in stages.GROUPS}
    for interest in stages.INTERESTS:
        assert set(interest["ideas"]) == groups, interest["id"]
        assert interest["goal_area"] in service.GOAL_CATEGORIES
        for ideas in interest["ideas"].values():
            assert len(ideas) == 2
            for idea in ideas:
                assert idea["kind"] in stages.IDEA_KINDS
                assert not idea["app"] or idea["app"] in apps.BY_ID, idea
    # Health suggestions point to a doctor, never to a dose.
    for item in stages.ITEMS.values():
        assert " mg" not in item["description"] and "dose of" not in item["description"]


def test_months_between_counts_whole_months():
    assert lifebook.months_between(date(2026, 1, 15), date(2026, 2, 14)) == 0
    assert lifebook.months_between(date(2026, 1, 15), date(2026, 2, 15)) == 1
    assert lifebook.months_between(date(2026, 1, 31), date(2026, 2, 28)) == 1
    assert lifebook.months_between(date(1990, 5, 4), date(2026, 5, 3)) == 431
    assert lifebook.months_between(date(1990, 5, 4), date(2026, 5, 4)) == 432
    assert lifebook.months_between(date(2027, 3, 10), date(2026, 10, 29)) == -4


# ---------------------------------------------------------------------------
# lives
# ---------------------------------------------------------------------------


def test_your_own_life_shares_the_birth_date_with_your_profile(db, people):
    pat = people["pat"]
    own = lifebook.list_lives(db, pat)[0]
    assert own["self"] and own["name"] == pat["name"] and own["approximate"]
    assert own["stage"]["id"] == "building"  # an adult with no birth date: a guess
    born = _ago(db, years=44)
    changed = lifebook.save_life(
        db, pat, {"birth_date": born, "interests": ["music"]}, own["id"]
    )
    assert changed["stage"]["id"] == "midlife" and not changed["approximate"]
    assert changed["age_months"] in (528, 527)
    assert life.get(db, pat["id"])["birthday"] == born
    # Saving other details does not wipe the birth date it took from the profile.
    lifebook.save_life(db, pat, {"blood_group": "o+"}, own["id"])
    assert life.get(db, pat["id"])["birthday"] == born
    assert lifebook.book(db, pat)["life"]["blood_group"] == "O+"
    with pytest.raises(ValueError, match="cannot be in the future"):
        lifebook.save_life(
            db,
            pat,
            {"birth_date": (_today(db) + timedelta(days=3)).isoformat()},
            own["id"],
        )
    with pytest.raises(ValueError, match="06:45"):
        lifebook.save_life(db, pat, {"birth_time": "25:00"}, own["id"])
    with pytest.raises(ValueError, match="not an interest"):
        lifebook.save_life(db, pat, {"interests": ["juggling"]}, own["id"])
    with pytest.raises(ValueError, match="stays"):
        lifebook.delete_life(db, pat, own["id"])
    # The profile's age group places a teen without a birth date.
    assert lifebook.list_lives(db, people["tia"])[0]["stage"]["id"] == "teen"


def test_adults_keep_the_lives_of_people_they_look_after(db, people):
    pat, tia, sam = people["pat"], people["tia"], people["sam"]
    due = (_today(db) + timedelta(days=90)).isoformat()
    baby = lifebook.save_life(
        db, pat, {"name": "Mira", "relation": "child", "birth_date": due}
    )
    assert baby["expected"] and baby["stage"]["id"] == "pregnancy"
    book = lifebook.book(db, pat, baby["id"])
    assert (
        book["road"][0]["id"] == "pregnancy" and book["road"][0]["state"] == "current"
    )
    assert book["records"]["vaccine"] == [] and book["wishes"] is None
    with pytest.raises(ValueError, match="ten months"):
        lifebook.save_life(
            db,
            pat,
            {
                "name": "Too early",
                "birth_date": (_today(db) + timedelta(days=400)).isoformat(),
            },
        )
    with pytest.raises(ValueError, match="their name"):
        lifebook.save_life(db, pat, {"relation": "parent"})
    mum = lifebook.save_life(
        db,
        pat,
        {
            "name": "Mum",
            "relation": "parent",
            "birth_date": _ago(db, years=82),
            "add_to_timeline": True,
        },
    )
    assert mum["stage"]["id"] == "elder"
    assert [e["title"] for e in service.list_events(db, pat)] == ["Mum was born"]
    names = [person["name"] for person in lifebook.list_lives(db, pat)]
    assert names == [pat["name"], "Mum", "Mira"]  # yourself, then by birth date
    # A teen keeps their own life only; nobody else sees Pat's.
    with pytest.raises(PermissionError):
        lifebook.save_life(db, tia, {"name": "Pet", "relation": "other"})
    with pytest.raises(LookupError):
        lifebook.book(db, sam, mum["id"])
    with pytest.raises(LookupError):
        lifebook.save_record(
            db,
            sam,
            mum["id"],
            {"kind": "checkup", "title": "x", "day": _ago(db, days=1)},
        )
    assert [p["name"] for p in lifebook.list_lives(db, sam)] == ["Sam"]
    lifebook.delete_life(db, pat, baby["id"])
    with pytest.raises(LookupError):
        lifebook.book(db, pat, baby["id"])


def test_a_life_that_ended_is_remembered(db, people):
    pat = people["pat"]
    born = _ago(db, years=80)
    grandpa = lifebook.save_life(
        db, pat, {"name": "Grandpa", "relation": "grandparent", "birth_date": born}
    )
    lifebook.save_record(
        db,
        pat,
        grandpa["id"],
        {
            "kind": "checkup",
            "title": "Heart check",
            "day": _ago(db, days=200),
            "next_due": _ago(db, days=-5),
        },
    )
    assert [d["who"] for d in lifebook.due(db, pat)] == ["Grandpa"]
    with pytest.raises(ValueError, match="future"):
        lifebook.set_passed(
            db,
            pat,
            grandpa["id"],
            {"died_on": (_today(db) + timedelta(days=1)).isoformat()},
        )
    with pytest.raises(ValueError, match="after their birth"):
        lifebook.set_passed(db, pat, grandpa["id"], {"died_on": "1900-01-01"})
    with pytest.raises(LookupError):
        lifebook.set_passed(
            db, people["tia"], grandpa["id"], {"died_on": _ago(db, days=2)}
        )  # not hers
    gone = lifebook.set_passed(
        db,
        pat,
        grandpa["id"],
        {
            "died_on": _ago(db, days=2),
            "died_note": "Loved his garden.",
            "add_to_timeline": True,
        },
    )
    assert (
        gone["passed"]
        and gone["stage"]["id"] == "remembered"
        and gone["next_stage"] is None
    )
    assert gone["age_months"] in (959, 960)  # his age when he died
    book = lifebook.book(db, pat, grandpa["id"])
    assert (
        book["road"][-1]["id"] == "remembered"
        and book["road"][-1]["state"] == "current"
    )
    assert all(stage["state"] == "past" for stage in book["road"][:-1])
    assert (
        "certificate"
        in " ".join(item["title"] for item in book["road"][-1]["items"]).lower()
    )
    assert book["ideas"] == [] and book["current"]["carried"] == []
    assert lifebook.due(db, pat) == []  # no reminders for someone who has died
    assert "In memory of Grandpa" in [e["title"] for e in service.list_events(db, pat)]
    back = lifebook.clear_passed(db, pat, grandpa["id"])
    assert not back["passed"] and back["stage"]["id"] == "elder"
    with pytest.raises(ValueError, match="your own"):
        lifebook.set_passed(
            db,
            pat,
            lifebook.list_lives(db, pat)[0]["id"],
            {"died_on": _ago(db, days=1)},
        )


# ---------------------------------------------------------------------------
# the checklist and the ideas
# ---------------------------------------------------------------------------


def test_items_are_ticked_set_aside_and_finished_by_their_goal(db, people):
    pat = people["pat"]
    own = lifebook.list_lives(db, pat)[0]
    lifebook.save_life(
        db,
        pat,
        {"birth_date": _ago(db, years=30), "interests": ["technology", "cooking"]},
        own["id"],
    )
    book = lifebook.book(db, pat)
    current = next(stage for stage in book["road"] if stage["state"] == "current")
    assert current["id"] == "building" and current["done"] == 0
    carried = [item["id"] for item in book["current"]["carried"]]
    assert (
        "young_adult.emergency_fund" in carried and "young_adult.study" not in carried
    )
    lifebook.set_item(
        db, pat, own["id"], "young_adult.emergency_fund", {"status": "done"}
    )
    lifebook.set_item(db, pat, own["id"], "building.home", {"status": "skipped"})
    goal = service.create_goal(db, pat, {"title": "Make a will", "category": "family"})
    planned = lifebook.set_item(
        db, pat, own["id"], "building.will", {"goal_id": goal["id"]}
    )
    assert planned["status"] == "planned" and planned["goal"]["title"] == "Make a will"
    book = lifebook.book(db, pat)
    current = next(stage for stage in book["road"] if stage["state"] == "current")
    assert "young_adult.emergency_fund" not in [
        i["id"] for i in book["current"]["carried"]
    ]
    assert current["total"] == len(current["items"]) - 1  # set aside: not counted
    assert (current["done"], current["planned"]) == (0, 1)
    service.update_goal(db, pat, goal["id"], {"status": "completed"})
    current = next(
        stage for stage in lifebook.book(db, pat)["road"] if stage["state"] == "current"
    )
    assert current["done"] == 1  # the goal was reached, so the item is done
    lifebook.set_item(db, pat, own["id"], "building.home", {"status": "open"})
    assert (
        next(i for i in current["items"] if i["id"] == "building.home")["status"]
        == "skipped"
    )
    assert (
        next(
            i
            for i in next(
                s for s in lifebook.book(db, pat)["road"] if s["state"] == "current"
            )["items"]
            if i["id"] == "building.home"
        )["status"]
        == "open"
    )
    # Ideas for the interests at this age, ticked the same way; anything else is refused.
    ideas = [idea["id"] for idea in book["ideas"]]
    assert ideas == [
        "technology.adult.0",
        "technology.adult.1",
        "cooking.adult.0",
        "cooking.adult.1",
    ]
    assert book["ideas"][0]["app"] == "sites" and book["ideas"][0]["kind"] == "earn"
    lifebook.set_item(db, pat, own["id"], "technology.adult.0", {"status": "done"})
    assert lifebook.book(db, pat)["ideas"][0]["status"] == "done"
    for bad in ("technology.adult.9", "music.elder.0", "nothing.here"):
        with pytest.raises(LookupError):
            lifebook.set_item(db, pat, own["id"], bad, {"status": "done"})
    with pytest.raises(ValueError):
        lifebook.set_item(db, pat, own["id"], "building.move", {"status": "maybe"})
    sams_goal = service.create_goal(db, people["sam"], {"title": "Sam's"})
    with pytest.raises(LookupError):
        lifebook.set_item(
            db, pat, own["id"], "building.move", {"goal_id": sams_goal["id"]}
        )
    summary = lifebook.summary(db, pat)
    assert summary["life"]["stage"]["id"] == "building" and len(summary["next"]) == 3
    assert summary["wishes"] == 0 and summary["done"] == 1


def test_babies_and_toddlers_get_no_interest_ideas_yet(db, people):
    pat = people["pat"]
    baby = lifebook.save_life(
        db,
        pat,
        {
            "name": "Mira",
            "relation": "child",
            "birth_date": _ago(db, days=60),
            "interests": ["music"],
        },
    )
    assert baby["stage"]["id"] == "baby" and baby["age_months"] in (1, 2)
    assert lifebook.book(db, pat, baby["id"])["ideas"] == []
    child = lifebook.save_life(
        db,
        pat,
        {
            "name": "Ravi",
            "relation": "child",
            "birth_date": _ago(db, years=7),
            "interests": ["music"],
        },
    )
    assert [idea["title"] for idea in lifebook.book(db, pat, child["id"])["ideas"]] == [
        "Learn an instrument",
        "Sing in a choir or group",
    ]


# ---------------------------------------------------------------------------
# the life book
# ---------------------------------------------------------------------------


def test_the_life_book_keeps_growth_vaccines_schools_and_moments(db, people):
    pat = people["pat"]
    baby = lifebook.save_life(
        db, pat, {"name": "Mira", "relation": "child", "birth_date": _ago(db, days=150)}
    )
    growth = lifebook.save_record(
        db,
        pat,
        baby["id"],
        {
            "kind": "growth",
            "day": _ago(db, days=10),
            "height_cm": "64,5",
            "weight_kg": 6.8,
        },
    )
    assert (growth["height_cm"], growth["weight_kg"], growth["title"]) == (
        64.5,
        6.8,
        "Growth",
    )
    changed = lifebook.save_record(db, pat, "", {"head_cm": "41"}, growth["id"])
    assert (changed["height_cm"], changed["head_cm"]) == (64.5, 41.0)
    for bad, message in (
        ({"kind": "growth", "day": _ago(db, days=1)}, "height, the weight"),
        ({"kind": "growth", "day": _ago(db, days=1), "weight_kg": "heavy"}, "number"),
        ({"kind": "growth", "day": _ago(db, days=1), "weight_kg": "900"}, "between"),
        ({"kind": "vaccine", "day": _ago(db, days=1)}, "the vaccine"),
        ({"kind": "vaccine", "title": "Polio", "day": _ago(db, days=-3)}, "future"),
        (
            {
                "kind": "vaccine",
                "title": "Polio",
                "day": _ago(db, days=3),
                "next_due": _ago(db, days=9),
            },
            "after this one",
        ),
        (
            {
                "kind": "education",
                "title": "Nursery",
                "day": _ago(db, days=3),
                "end_day": _ago(db, days=9),
            },
            "end date",
        ),
        ({"kind": "diary", "title": "x"}, "kind of record"),
    ):
        with pytest.raises(ValueError, match=message):
            lifebook.save_record(db, pat, baby["id"], bad)
    first = lifebook.save_record(
        db,
        pat,
        baby["id"],
        {
            "kind": "vaccine",
            "title": "DTaP",
            "detail": "First dose",
            "day": _ago(db, days=60),
            "next_due": _ago(db, days=-5),
        },
    )
    assert [d["title"] for d in lifebook.due(db, pat)] == ["DTaP"]
    assert lifebook.due(db, pat)[0]["days_left"] == 5
    # The next dose answers the reminder; its own next date takes over.
    lifebook.save_record(
        db,
        pat,
        baby["id"],
        {
            "kind": "vaccine",
            "title": "dtap",
            "detail": "Second dose",
            "day": _ago(db, days=1),
            "next_due": _ago(db, days=-200),
        },
    )
    assert lifebook.due(db, pat) == []
    assert lifebook.due(db, pat, days=365)[0]["detail"] == "Second dose"
    lifebook.delete_record(db, pat, first["id"])
    with pytest.raises(LookupError):
        lifebook.delete_record(db, people["sam"], growth["id"])
    # A school keeps its dates; the next-due date belongs only to vaccines and check-ups.
    school = lifebook.save_record(
        db,
        pat,
        baby["id"],
        {
            "kind": "education",
            "title": "Little Stars",
            "day": _ago(db, days=30),
            "next_due": _ago(db, days=-30),
        },
    )
    assert school["next_due"] == "" and school["end_day"] == ""
    # A moment: in the book of someone you look after, or on your own timeline.
    moment = lifebook.add_milestone(
        db, pat, baby["id"], {"title": "First smile", "day": _ago(db, days=100)}
    )
    assert moment["record"]["kind"] == "milestone"
    book = lifebook.book(db, pat, baby["id"])
    baby_stage = next(stage for stage in book["road"] if stage["id"] == "baby")
    assert next(m for m in baby_stage["milestones"] if m["title"] == "First smile")[
        "recorded"
    ]["day"] == _ago(db, days=100)
    assert [m["title"] for m in baby_stage["moments"]] == ["First smile"]
    own = lifebook.list_lives(db, pat)[0]
    lifebook.save_life(db, pat, {"birth_date": "1990-05-04"}, own["id"])
    mine = lifebook.add_milestone(
        db,
        pat,
        own["id"],
        {"title": "Learned to ride a bike", "day": "1997-06-01", "stage": "child"},
    )
    assert mine["event"]["kind"] == "education"
    child_stage = next(
        stage for stage in lifebook.book(db, pat)["road"] if stage["id"] == "child"
    )
    assert next(
        m for m in child_stage["milestones"] if m["title"] == "Learned to ride a bike"
    )["recorded"]
    data, name = lifebook.book_csv(db, pat, baby["id"])
    text = data.decode()
    assert name == "life-book-mira.csv"
    assert (
        "height cm 64.5, weight kg 6.8, head cm 41.0" in text
        and "First smile" in text
        and "Little Stars" in text
    )


def test_today_and_the_pulse_remind_of_what_is_due(db, people):
    pat = people["pat"]
    baby = lifebook.save_life(
        db, pat, {"name": "Mira", "relation": "child", "birth_date": _ago(db, days=150)}
    )
    lifebook.save_record(
        db,
        pat,
        baby["id"],
        {
            "kind": "vaccine",
            "title": "Rotavirus",
            "day": _ago(db, days=50),
            "next_due": _ago(db, days=-7),
        },
    )
    own = lifebook.list_lives(db, pat)[0]
    lifebook.save_record(
        db,
        pat,
        own["id"],
        {
            "kind": "checkup",
            "title": "Eye test",
            "day": _ago(db, days=400),
            "next_due": _ago(db, days=10),
        },
    )
    card = next(
        c
        for c in journey_routes._today(db, pat)
        if c["title"] == "Vaccines and check-ups"
    )
    assert [line["text"] for line in card["lines"]] == ["Eye test", "Mira: Rotavirus"]
    assert card["lines"][0]["sub"] == "Was due: 10 days ago" and card["tone"] == "warn"
    assert card["lines"][1]["link"] == f"#/journey?tab=stages&life={baby['id']}"
    pulse = journey_routes._pulse(db, pat, "")
    assert pulse["badge"] == 2
    assert [a["title"] for a in pulse["alerts"]] == ["Mira: a vaccine is due"]
    assert not [
        c
        for c in journey_routes._today(db, people["sam"])
        if c["title"] == "Vaccines and check-ups"
    ]


# ---------------------------------------------------------------------------
# wishes and a plan
# ---------------------------------------------------------------------------


def test_wishes_are_for_adults_and_download_as_text(db, people):
    pat, tia = people["pat"], people["tia"]
    assert lifebook.book(db, tia)["wishes"] is None
    with pytest.raises(PermissionError):
        lifebook.save_wishes(db, tia, {"care": "x"})
    with pytest.raises(PermissionError):
        lifebook.wishes_text(db, tia)
    saved = lifebook.save_wishes(
        db,
        pat,
        {
            "health_proxy": "Sam, 98765 43210",
            "funeral": "Simple, with music.",
            "organ_donation": "yes",
        },
    )
    assert saved["filled"] == 2 and saved["values"]["organ_donation"] == "yes"
    with pytest.raises(ValueError):
        lifebook.save_wishes(db, pat, {"organ_donation": "maybe"})
    text = lifebook.wishes_text(db, pat).decode()
    assert (
        "Sam, 98765 43210" in text
        and "Simple, with music." in text
        and "Organ donation\n--------------\nYes" in text
    )
    assert "(not written yet)" in text
    # Private: Sam's own book has no sign of Pat's wishes.
    assert lifebook.book(db, people["sam"])["wishes"]["filled"] == 0


def test_a_plan_comes_from_the_model_or_the_checklist(db, people):
    pat = people["pat"]
    baby = lifebook.save_life(
        db, pat, {"name": "Mira", "relation": "child", "birth_date": _ago(db, days=150)}
    )
    plan = lifebook.plan(lifebook.plan_context(db, pat, baby["id"]))
    assert plan["source"] == "template"
    assert [step["title"] for step in plan["steps"]][:2] == [
        "Keep vaccines on schedule",
        "Go to every baby check-up",
    ]
    coach = _Coach()
    suite.set_gateway(coach)
    plan = lifebook.plan(lifebook.plan_context(db, pat, baby["id"]))
    assert plan["source"] == "model" and plan["summary"] == "A calm, strong start."
    assert [step["area"] for step in plan["steps"]] == ["health", "mind", "purpose"]
    assert (
        "a baby of 4 months" in coach.prompts[0]
        or "a baby of 5 months" in coach.prompts[0]
    )
    assert "Mira" not in coach.prompts[0]  # the model is not told the name


# ---------------------------------------------------------------------------
# through the single-user server
# ---------------------------------------------------------------------------


def test_life_stages_through_the_server(env, api):
    kit.forget_schema()
    household = Household(env, api)
    owner = household.owner
    tia = household.add("Tia", age_group="teen")
    mine = owner.get("/api/journey/lives/me")
    own_id = mine["life"]["id"]
    assert (
        mine["life"]["self"] and mine["wishes"] is not None and mine["may_keep_others"]
    )
    owner.ok(
        "PATCH",
        f"/api/journey/lives/{own_id}",
        {"birth_date": "1988-02-10", "interests": ["writing"]},
    )
    mine = owner.get("/api/journey/lives/me")
    assert (
        mine["life"]["stage"]["id"] == "building"
        and mine["ideas"][0]["interest"] == "writing"
    )
    baby = owner.post(
        "/api/journey/lives",
        {
            "name": "Mira",
            "relation": "child",
            "birth_date": (date.today() - timedelta(days=200)).isoformat(),
            "add_to_timeline": True,
        },
        expect=201,
    )["life"]
    assert (
        owner.get(f"/api/journey/lives/{baby['id']}")["life"]["stage"]["id"] == "baby"
    )
    item = owner.ok(
        "PUT",
        f"/api/journey/lives/{baby['id']}/items/baby.safe_sleep",
        {"status": "done"},
    )["item"]
    assert item["status"] == "done"
    assert (
        owner.ok(
            "PUT",
            f"/api/journey/lives/{baby['id']}/items/writing.adult.0",
            {"status": "done"},
        )["item"]["status"]
        == "done"
    )
    status, _ = owner.call(
        "PUT", f"/api/journey/lives/{baby['id']}/items/baby.nothing", {"status": "done"}
    )
    assert status == 404
    record = owner.post(
        f"/api/journey/lives/{baby['id']}/records",
        {
            "kind": "vaccine",
            "title": "Hepatitis B",
            "day": (date.today() - timedelta(days=30)).isoformat(),
            "next_due": (date.today() + timedelta(days=7)).isoformat(),
        },
        expect=201,
    )["record"]
    assert (
        owner.ok(
            "PATCH", f"/api/journey/records/{record['id']}", {"detail": "Second dose"}
        )["record"]["detail"]
        == "Second dose"
    )
    status, error = owner.call(
        "POST",
        f"/api/journey/lives/{baby['id']}/records",
        {"kind": "growth", "day": "2026-01-01"},
    )
    assert status == 400 and "height" in error["error"]
    moment = owner.post(
        f"/api/journey/lives/{own_id}/milestones",
        {"title": "First full-time job", "day": "2010-07-01"},
        expect=201,
    )
    assert moment["event"]["title"] == "First full-time job"
    overview = owner.get("/api/journey/overview")
    assert overview["stage"]["life"]["stage"]["id"] == "building"
    assert (
        overview["stage"]["due"][0]["title"] == "Hepatitis B"
        and overview["stage"]["others"] == 1
    )
    today = owner.get("/api/life/today")
    lines = [
        line["text"]
        for card in today["cards"]
        if card["title"] == "Vaccines and check-ups"
        for line in card["lines"]
    ]
    assert lines == ["Mira: Hepatitis B"]
    plan = owner.post(f"/api/journey/lives/{baby['id']}/plan", {})
    assert plan["source"] in ("template", "model") and plan["steps"]
    assert (
        owner.ok("PUT", "/api/journey/wishes", {"care": "At home, with family."})[
            "wishes"
        ]["filled"]
        == 1
    )
    status, text = owner.call("GET", "/api/journey/export/wishes", raw=True)
    assert status == 200 and b"At home, with family." in text
    status, csv = owner.call("GET", f"/api/journey/export/lives/{baby['id']}", raw=True)
    assert status == 200 and b"Hepatitis B" in csv and b"Safe sleep" in csv
    catalog = owner.get("/api/journey/stages")
    assert [stage["id"] for stage in catalog["stages"]][0] == "pregnancy" and len(
        catalog["interests"]
    ) == len(stages.INTERESTS)
    # The teen: their own life, no wishes, cannot add others or open Pat's.
    teen = tia.get("/api/journey/lives/me")
    assert (
        teen["life"]["stage"]["id"] == "teen"
        and teen["wishes"] is None
        and not teen["may_keep_others"]
    )
    assert tia.call("POST", "/api/journey/lives", {"name": "Pet"})[0] == 403
    assert tia.call("GET", f"/api/journey/lives/{baby['id']}")[0] == 404
    assert tia.call("PUT", "/api/journey/wishes", {"care": "x"})[0] == 403
    assert tia.call("GET", f"/api/journey/export/lives/{baby['id']}")[0] == 404
    passed = owner.post(
        f"/api/journey/lives/{baby['id']}/passed", {"died_on": date.today().isoformat()}
    )
    assert passed["life"]["stage"]["id"] == "remembered"
    assert (
        owner.ok("DELETE", f"/api/journey/lives/{baby['id']}/passed")["life"]["stage"][
            "id"
        ]
        == "baby"
    )
    assert owner.ok("DELETE", f"/api/journey/lives/{baby['id']}")["deleted"]
    assert owner.call("DELETE", f"/api/journey/lives/{own_id}")[0] == 400


# ---------------------------------------------------------------------------
# hosted
# ---------------------------------------------------------------------------


def test_hosted_members_keep_their_own_lives(cloud):
    kit.forget_schema()
    org = build_org(cloud)
    cloud.billing.subscribe_app(org.owner, "journey")
    owner = sign_in(cloud, org.owner)
    candidate = sign_in(cloud, org.candidate)
    auditor = sign_in(cloud, org.auditor)
    status, mine = owner.call("GET", "/api/journey/lives/me")
    assert status == 200 and mine["life"]["self"]
    status, made = owner.call(
        "POST",
        "/api/journey/lives",
        {"name": "Mum", "relation": "parent", "birth_date": "1950-03-01"},
    )
    assert status == 201, made
    mum = made["life"]["id"]
    # Every member keeps their own: a candidate and an auditor too (life.manage is every member's).
    status, theirs = candidate.call(
        "POST",
        "/api/journey/lives",
        {"name": "Dad", "relation": "parent", "birth_date": "1952-01-01"},
    )
    assert status == 201, theirs
    assert [p["name"] for p in candidate.call("GET", "/api/journey/lives")[1]["lives"]][
        1:
    ] == ["Dad"]
    assert candidate.call("GET", f"/api/journey/lives/{mum}")[0] == 404
    assert (
        candidate.call(
            "PUT", f"/api/journey/lives/{mum}/items/elder.joy", {"status": "done"}
        )[0]
        == 404
    )
    assert auditor.call("POST", f"/api/journey/lives/{mum}/plan", {})[0] == 404
    status, plan = owner.call("POST", f"/api/journey/lives/{mum}/plan", {})
    assert status == 200 and plan["steps"], plan
    status, saved = owner.call("PUT", "/api/journey/wishes", {"funeral": "Quiet."})
    assert status == 200 and saved["wishes"]["filled"] == 1
    other = build_org(cloud, "Other Org", slug_email="other.test")
    cloud.billing.subscribe_app(other.owner, "journey")
    stranger = sign_in(cloud, other.owner)
    assert stranger.call("GET", f"/api/journey/lives/{mum}")[0] == 404
    assert [
        p["name"] for p in stranger.call("GET", "/api/journey/lives")[1]["lives"]
    ] == [stranger.call("GET", "/api/journey/lives/me")[1]["life"]["name"]]
