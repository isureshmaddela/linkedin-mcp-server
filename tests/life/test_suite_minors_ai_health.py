"""Young people and the writing model (AI chat, Learn decks, Play quizzes), and a parent looking after a young
child's medicines."""

from __future__ import annotations

import json
import sqlite3

import pytest

from aira import suite
from aira.suite import kit, life, public
from aira.suite.ai import service as ai
from aira.suite.ai import store as ai_store
from aira.suite.care import service as care
from aira.suite.care import store as care_store
from aira.suite.learn import service as learn
from aira.suite.learn import store as learn_store
from aira.suite.play import quizzes
from aira.suite.play import store as play_store


class Model:
    """A writing model that answers what the test says and remembers the prompts."""

    def __init__(self, answer):
        self.answer = answer
        self.prompts = []

    def model(self, prompt):
        self.prompts.append(prompt)
        return json.dumps(self.answer)


class Req:
    def __init__(self, header: str = "") -> None:
        self._header = header
        self.user = {"email": "pat@home.example"}

    def header(self, name: str, default: str = "") -> str:
        return self._header if name == "X-Aira-Profile" else default


@pytest.fixture(autouse=True)
def _gateway():
    previous = suite.gateway()
    yield
    suite.set_gateway(previous)


@pytest.fixture
def db(tmp_path):
    kit.forget_schema()
    public.set_index(public._MemoryIndex())
    connection = sqlite3.connect(tmp_path / "minors.sqlite3")
    connection.row_factory = sqlite3.Row
    for store in (ai_store, care_store, learn_store, play_store):
        store.ensure(connection)
    kit.save_business(connection, {"name": "Home", "timezone": "UTC"})
    yield connection
    connection.close()


@pytest.fixture
def people(db):
    pat = life.current(Req(), db)
    teo = life.add(db, pat, {"name": "Teo", "age_group": "teen"})
    mia = life.add(db, pat, {"name": "Mia", "age_group": "child"})
    return {"pat": pat, "teo": teo, "mia": mia}


def test_every_prompt_has_crisis_rules_and_young_people_get_stricter_ones(db, people):
    model = Model({"reply": "Here is your day.", "actions": []})
    suite.set_gateway(model)
    ai.chat(db, people["pat"], {"message": "What is on today?"})
    ai.chat(db, people["teo"], {"message": "What is on today?"})
    adult, teen = model.prompts
    for prompt in (adult, teen):
        assert "Safety rules" in prompt and "14416" in prompt and "self" in prompt
    assert "child or a teenager" in teen and "child or a teenager" not in adult


def test_self_harm_gets_a_fixed_answer_with_helplines(db, people):
    model = Model({"reply": "anything", "actions": []})
    suite.set_gateway(model)
    for who in ("pat", "teo"):
        answer = ai.chat(db, people[who], {"message": "I want to kill myself"})[
            "answer"
        ]
        assert "14416" in answer["content"] and "1-800-891-4416" in answer["content"]
        assert "emergency services" in answer["content"]
    assert model.prompts == []  # the model never sees it and never answers it
    young = ai.chat(db, people["mia"], {"message": "i wanna die"})["answer"]["content"]
    assert "grown-up you trust" in young


def test_a_young_persons_answer_is_filtered(db, people):
    suite.set_gateway(Model({"reply": "You could try some porn sites.", "actions": []}))
    teen = ai.chat(db, people["teo"], {"message": "What can I do tonight?"})["answer"]
    assert (
        "porn" not in teen["content"] and "parent, carer or teacher" in teen["content"]
    )
    adult = ai.chat(db, people["pat"], {"message": "What can I do tonight?"})["answer"]
    assert "porn" in adult["content"]  # grown-ups' answers are theirs


def test_learn_decks_and_play_quizzes_for_young_people(db, people):
    teo, mia = people["teo"], people["mia"]
    cards = [{"front": f"Bird {i}?", "back": f"Answer {i}"} for i in range(5)]
    cards.append({"front": "Nude birds?", "back": "send nudes"})
    model = Model({"title": "Birds", "cards": cards})
    suite.set_gateway(model)
    deck = learn.generate_deck(db, teo, {"topic": "Birds", "count": 6})
    assert deck["cards_added"] == 5
    assert "suitable for a young learner" in model.prompts[0]
    with pytest.raises(ValueError, match="another topic"):
        learn.generate_deck(db, mia, {"topic": "suicide methods"})
    quiz_model = Model(
        {
            "questions": [
                {
                    "question": "Which planet is red?",
                    "answer": "Mars",
                    "wrong": ["Venus", "Jupiter", "Saturn"],
                },
                {
                    "question": "Who sells porn?",
                    "answer": "x",
                    "wrong": ["y", "z", "w"],
                },
            ]
        }
    )
    suite.set_gateway(quiz_model)
    drafted = quizzes.draft(db, teo, {"topic": "space", "count": 2})
    assert [q["text"] for q in drafted["questions"]] == ["Which planet is red?"]
    assert "young player" in quiz_model.prompts[0]
    # a guardian turned AI off: the quiz book and the template deck still work, without the model
    life.set_controls(db, people["pat"], teo["id"], {"apps": {"ai": False}})
    quiz_model.prompts.clear()
    book = quizzes.draft(
        db, life.get(db, teo["id"]), {"topic": "geography", "count": 3}
    )
    assert book["source"] == "template" and quiz_model.prompts == []


def test_a_parent_manages_a_young_childs_medicines(db, people):
    pat, teo, mia = people["pat"], people["teo"], people["mia"]
    person, can_edit = care.subject(db, pat, mia["id"])
    assert person["id"] == mia["id"] and can_edit is True
    medicine = care.create_medicine(
        db, person, {"name": "Amoxicillin", "times": "08:00"}
    )
    assert medicine["owner_id"] == mia["id"]
    assert {p["name"]: p["manages"] for p in care.caring_for(db, pat)} == {"Mia": True}
    # a teen's health is their own; a child cannot manage anyone's
    with pytest.raises(PermissionError):
        care.subject(db, pat, teo["id"])
    with pytest.raises(PermissionError):
        care.subject(db, mia, pat["id"])
