"""Search across Courses & Events (``/api/academy/search``, the box at the top of the app): one request finds the
courses, events, students, ticket orders and partners whose words match, newest first, with totals when there
are more than the limit; % and _ are taken literally; a child's household profile is refused like the rest of the
app."""

from __future__ import annotations

import json
import sqlite3
import urllib.error
import urllib.request
from datetime import timedelta

import pytest

import auto_apply_assistant as local
import test_careers
from aira.suite import kit, public
from aira.suite.academy import household, partners, search
from aira.suite.academy import service as academy
from aira.suite.academy import store as academy_store
from aira.suite.events import service as events
from aira.suite.events import store as events_store

env = test_careers.env
api = test_careers.api
stub = test_careers.stub


@pytest.fixture
def db(tmp_path):
    kit.forget_schema()
    public.set_index(public._MemoryIndex())
    connection = sqlite3.connect(tmp_path / "experiences.sqlite3", timeout=10)
    connection.row_factory = sqlite3.Row
    academy_store.ensure(connection)
    events_store.ensure(connection)
    yield connection
    connection.close()


def later(**delta) -> str:
    return kit.iso(kit.utcnow() + timedelta(**delta))


def test_one_search_finds_every_kind_and_counts_the_rest(db):
    guitar = academy.create_course(
        db, {"title": "Guitar basics", "subtitle": "Chords", "price_cents": 4900}
    )
    academy.create_course(db, {"title": "Python 101", "subtitle": "Guitar-free"})
    for n in range(7):
        academy.enrol(
            db,
            guitar["id"],
            {"name": f"Ana {n}", "email": f"ana{n}@example.com"},
            source="manual",
        )
    jazz = events.create_event(
        db,
        {
            "title": "Jazz Night",
            "venue_name": "Ana's Bar",
            "starts_at": later(days=4),
        },
    )
    seat = events.add_ticket_type(
        db, jazz["id"], {"name": "Seat", "price_cents": 1500, "quantity": 20}
    )
    events.set_status(db, jazz["id"], "published")
    placed = events.place_order(
        db,
        jazz["id"],
        {
            "name": "Ben Okafor",
            "email": "ben@example.com",
            "items": [{"ticket_type_id": seat["id"], "quantity": 2}],
        },
    )["order"]
    partners.create_partner(db, {"name": "Priya", "code": "ANAPAL"})

    found = search.search(db, "ana", limit=5)
    assert found["q"] == "ana"
    # Students: 7 match, 5 shown, newest first, with their course.
    assert found["totals"]["students"] == 7 and len(found["students"]) == 5
    assert found["students"][0]["name"] == "Ana 6"
    assert found["students"][0]["course_title"] == "Guitar basics"
    # The venue, a partner's code: every field the tabs' own searches look at.
    assert [e["title"] for e in found["events"]] == ["Jazz Night"]
    assert found["events"][0]["phase"] == "published"
    assert [p["code"] for p in found["partners"]] == ["ANAPAL"]
    assert found["partners"][0]["active"] is True
    assert found["courses"] == [] and found["totals"]["courses"] == 0

    both = search.search(db, "guitar")
    assert [c["title"] for c in both["courses"]] == ["Python 101", "Guitar basics"]
    by_reference = search.search(db, placed["reference"].lower())
    assert [o["id"] for o in by_reference["orders"]] == [placed["id"]]
    assert by_reference["orders"][0]["event_title"] == "Jazz Night"
    assert search.search(db, "ben@example")["orders"][0]["buyer_name"] == "Ben Okafor"

    # % and _ are letters here, not wildcards; nothing typed finds nothing.
    assert sum(search.search(db, "%")["totals"].values()) == 0
    assert sum(search.search(db, "_")["totals"].values()) == 0
    empty = search.search(db, "   ")
    assert (
        empty["q"] == "" and empty["students"] == [] and empty["totals"]["orders"] == 0
    )


def call(api, method, path, body=None, headers=None):
    data = json.dumps(body).encode() if body is not None else None
    request = urllib.request.Request(api.base + path, data=data, method=method)
    request.add_header("Content-Type", "application/json")
    request.add_header("X-Requested-With", "aira")
    request.add_header("Origin", api.base)
    for key, value in (headers or {}).items():
        request.add_header(key, value)
    try:
        with api.opener.open(request, timeout=60) as response:
            return response.status, json.loads(response.read())
    except urllib.error.HTTPError as error:
        return error.code, json.loads(error.read())


def test_the_search_route_answers_the_box_and_keeps_children_out(env, api):
    kit.forget_schema()
    public.set_index(local._public_link_index)
    course = api.post("/api/academy/courses", {"title": "Guitar 101"}, expect=201)[
        "course"
    ]
    api.post(
        "/api/academy/students",
        {"course_id": course["id"], "name": "Ana Lopez", "email": "ana@example.com"},
        expect=201,
    )
    found = api.get("/api/academy/search?q=ana&limit=4")
    assert [s["email"] for s in found["students"]] == ["ana@example.com"]
    assert set(found["totals"]) == {
        "courses",
        "events",
        "students",
        "orders",
        "partners",
    }
    assert api.get("/api/academy/search?q=guitar")["courses"][0]["id"] == course["id"]
    status, refused = api.call("GET", "/api/academy/search?q=ana&limit=500")
    assert status == 400 and "limit" in refused["error"]

    owner = api.get("/api/life/profiles")["profiles"][0]
    status, added = call(
        api,
        "POST",
        "/api/life/profiles",
        {"name": "Kid", "age_group": "child"},
        {"X-Aira-Profile": owner["id"]},
    )
    assert status == 201, added
    status, refused = call(
        api,
        "GET",
        "/api/academy/search?q=ana",
        headers={"X-Aira-Profile": added["profile"]["id"]},
    )
    assert status == 403 and refused["error"] == household.REFUSED
