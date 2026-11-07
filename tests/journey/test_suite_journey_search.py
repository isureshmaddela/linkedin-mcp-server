"""Life's search (GET /api/journey/search): the search box at the top of the Aira frame finds a person's own goals,
habits, journal days, timeline events and documents, never another person's, and never by a document's number."""

# ruff: noqa: F811
from __future__ import annotations

import pytest
from life_support import Household
from test_suite_journey import api, db, env, people, seal, stub  # noqa: F401

from aira.suite import kit
from aira.suite.journey import service


def _fill(db, me, seal) -> None:
    service.create_goal(
        db, me, {"title": "Run a 5K race", "description": "Get fit", "steps": []}
    )
    service.create_goal(
        db, me, {"title": "Learn to swim", "description": "Run less, swim more"}
    )
    service.save_habit(db, me, {"title": "Run 20 minutes", "per_week": 3})
    service.save_entry(
        db, me, {"mood": 4, "body": "Long day. Then a good run by the river."}
    )
    service.save_event(
        db,
        me,
        {"title": "First marathon run", "event_date": "2019-05-04", "kind": "life"},
    )
    service.save_document(
        db,
        me,
        {"title": "Passport", "kind": "passport", "number": "RUN123", "tags": []},
        seal,
    )


def test_search_finds_each_kind_names_first_and_keeps_others_out(db, people, seal):
    pat, sam = people["pat"], people["sam"]
    _fill(db, pat, seal)
    service.create_goal(db, sam, {"title": "Run with Sam"})
    found = service.search(db, pat, "run")
    assert [g["title"] for g in found["goals"]] == ["Run a 5K race", "Learn to swim"]
    assert [h["title"] for h in found["habits"]] == ["Run 20 minutes"]
    assert [e["title"] for e in found["events"]] == ["First marathon run"]
    (entry,) = found["entries"]
    assert entry["mood_label"] == "Good" and "good run by the river" in entry["snippet"]
    assert "body" not in entry
    # A document's number is never searched (nor sent back).
    assert found["documents"] == []
    assert found["total"] == 5 and found["query"] == "run"
    # Another person's records stay theirs.
    assert "Run with Sam" not in str(found)
    assert service.search(db, sam, "run")["total"] == 1
    # The limit applies to each kind; an empty search finds nothing.
    assert len(service.search(db, pat, "run", limit=1)["goals"]) == 1
    assert service.search(db, pat, "  ")["total"] == 0
    with pytest.raises(ValueError):
        service.search(db, pat, "run", limit=0)


def test_search_takes_percent_and_underscore_as_plain_characters(db, people, seal):
    pat = people["pat"]
    service.create_goal(db, pat, {"title": "Save 50% more"})
    service.create_goal(db, pat, {"title": "Save 500 more"})
    service.save_document(
        db, pat, {"title": "tax_2025", "kind": "tax", "tags": []}, seal
    )
    service.save_document(
        db, pat, {"title": "tax 2025", "kind": "tax", "tags": []}, seal
    )
    assert [g["title"] for g in service.search(db, pat, "50%")["goals"]] == [
        "Save 50% more"
    ]
    assert [d["title"] for d in service.search(db, pat, "tax_")["documents"]] == [
        "tax_2025"
    ]


def test_search_on_the_local_build(env, api):
    kit.forget_schema()
    home = Household(env, api)
    pat = home.owner
    pat.post("/api/journey/goals", {"title": "Run a 5K race"}, expect=201)
    pat.post(
        "/api/journey/documents",
        {"title": "My passport", "kind": "passport", "issuer": "Passport Office"},
        expect=201,
    )
    body = pat.get("/api/journey/search?q=passport&limit=3")
    assert [d["title"] for d in body["documents"]] == ["My passport"]
    assert body["documents"][0]["state"] == "no_expiry"
    assert pat.get("/api/journey/search?q=5k")["goals"][0]["title"] == "Run a 5K race"
    status, error = pat.call("GET", "/api/journey/search?q=run&limit=99")
    assert status == 400 and "at most 50" in error["error"]
