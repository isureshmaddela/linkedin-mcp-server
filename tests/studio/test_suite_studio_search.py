"""Search across Sales (aira/suite/studio/search.py, GET /api/studio/search): the box across the top of the app
finds clients, the people at them, deals, proposals, invoices and projects, a few of each, newest first."""

from __future__ import annotations

import sqlite3

import pytest

import test_careers
from aira.suite import kit, public
from aira.suite.studio import sales, search, service, store

env = test_careers.env
api = test_careers.api
stub = test_careers.stub

TODAY = "2026-10-25"


@pytest.fixture
def db(tmp_path):
    kit.forget_schema()
    public.set_index(public._MemoryIndex())
    connection = sqlite3.connect(tmp_path / "search.sqlite3")
    connection.row_factory = sqlite3.Row
    store.ensure(connection)
    yield connection
    connection.close()


def _book(db):
    bloom = service.create_client(
        db,
        {
            "name": "Maya Patel",
            "company": "Bloom Bakery",
            "email": "maya@bloom.example",
        },
    )
    rao = service.create_client(db, {"name": "Arjun Rao", "company": "Rao Logistics"})
    sales.add_contact(
        db, rao["id"], {"name": "Priya Shah", "email": "priya@rao.example"}
    )
    sales.create_deal(
        db,
        {"title": "Website refresh", "client_id": bloom["id"], "value_cents": 450000},
    )
    service.create_invoice(
        db,
        {
            "client_id": bloom["id"],
            "items": [{"description": "Logo", "unit_cents": 150000}],
        },
        today=TODAY,
    )
    service.create_proposal(
        db,
        {
            "client_id": rao["id"],
            "title": "Fleet dashboard",
            "items": [{"description": "Build", "unit_cents": 900000}],
        },
        today=TODAY,
    )
    service.create_project(db, {"client_id": bloom["id"], "name": "Bloom website"})
    db.commit()
    return bloom, rao


def test_a_client_name_finds_everything_of_that_client(db):
    bloom, _ = _book(db)
    found = search.search(db, "bloom", today=TODAY)
    assert [c["id"] for c in found["clients"]] == [bloom["id"]]
    assert [d["title"] for d in found["deals"]] == ["Website refresh"]
    assert found["deals"][0]["client_company"] == "Bloom Bakery"
    assert [i["number"] for i in found["invoices"]] == ["INV-0001"]
    assert (
        found["invoices"][0]["status"] == "draft"
        and found["invoices"][0]["balance_cents"] == 150000
    )
    assert [p["name"] for p in found["projects"]] == ["Bloom website"]
    assert found["proposals"] == []
    assert "client_email" not in found["projects"][0]


def test_numbers_titles_and_the_people_at_a_client(db):
    _, rao = _book(db)
    assert [
        p["title"] for p in search.search(db, "PRO-0001", today=TODAY)["proposals"]
    ] == ["Fleet dashboard"]
    assert [
        i["number"] for i in search.search(db, "inv-0001", today=TODAY)["invoices"]
    ] == ["INV-0001"]
    through_contact = search.search(db, "priya", today=TODAY)["clients"]
    assert [(c["id"], c["contact"]) for c in through_contact] == [
        (rao["id"], "Priya Shah")
    ]


def test_wildcards_are_text_and_each_kind_is_capped(db):
    _book(db)
    assert search.search(db, "%", today=TODAY)["clients"] == []
    assert search.search(db, "_", today=TODAY)["clients"] == []
    for n in range(8):
        service.create_client(db, {"name": f"Acme {n}"})
    db.commit()
    assert len(search.search(db, "acme", today=TODAY, limit=3)["clients"]) == 3
    assert len(search.search(db, "acme", today=TODAY, limit=500)["clients"]) == 8
    empty = search.search(db, "   ", today=TODAY)
    assert empty["q"] == "" and all(empty[kind] == [] for kind in search.KINDS)


def test_local_search_route(env, api):
    kit.forget_schema()
    client = api.post(
        "/api/studio/clients",
        {"name": "Lee Chen", "company": "Chen Studio"},
        expect=201,
    )["client"]
    api.post(
        "/api/studio/deals",
        {"title": "Rebrand", "client_id": client["id"], "value_cents": 1000},
        expect=201,
    )
    found = api.get("/api/studio/search?q=chen&limit=2")
    assert found["q"] == "chen"
    assert [c["name"] for c in found["clients"]] == ["Lee Chen"]
    assert [d["title"] for d in found["deals"]] == ["Rebrand"]
    assert api.get("/api/studio/search?q=nobody&limit=x")["clients"] == []
