"""Learn's search (GET /api/learn/search, the search box across the top of the app): decks, cards and books holding
every word, in any case, only what the person may see, and through the single-user server."""

# ruff: noqa: F811
from __future__ import annotations

from test_suite_learn import api, db, env, no_model, person, stub  # noqa: F401

from aira.suite.learn import store


def test_search_finds_decks_cards_and_books_the_person_can_see(db):
    pat = person(db)
    sam = person(db, "Sam", "child")
    spanish = store.create_deck(
        db, pat["id"], {"title": "Spanish words", "description": "For the trip"}
    )
    store.add_card(db, spanish["id"], {"front": "Gracias", "back": "Thank you"})
    store.add_card(
        db, spanish["id"], {"front": "Thank you very much", "back": "Muchas gracias"}
    )
    store.add_card(
        db, spanish["id"], {"front": "Hola", "back": "Hello", "hint": "A greeting"}
    )
    shared = store.create_deck(db, sam["id"], {"title": "Dinosaurs", "shared": True})
    store.add_card(db, shared["id"], {"front": "T. rex", "back": "A big meat eater"})
    private = store.create_deck(db, sam["id"], {"title": "Sam's secret Spanish"})
    store.add_card(
        db, private["id"], {"front": "Gracias amigo", "back": "Thanks friend"}
    )
    db.execute(
        "INSERT INTO ln_books (id, profile_id, title, author, status, created_at) VALUES ('b1', ?, 'Matilda', 'Roald Dahl', 'reading', '2026-10-01')",
        (pat["id"],),
    )
    db.commit()

    found = store.search(db, pat, "GRACIAS")
    assert [c["front"] for c in found["cards"]] == [
        "Gracias",
        "Thank you very much",
    ]  # the question first
    assert found["cards"][0]["deck_title"] == "Spanish words"
    assert found["decks"] == [] and found["books"] == []

    # Every word must be there, on any field; a shared deck counts, someone's private deck does not.
    assert [c["front"] for c in store.search(db, pat, "greeting hello")["cards"]] == [
        "Hola"
    ]
    assert [d["title"] for d in store.search(db, pat, "spanish")["decks"]] == [
        "Spanish words"
    ]
    dinos = store.search(db, pat, "dinosaurs")
    assert (
        dinos["decks"][0]["owner_name"] == "Sam"
        and dinos["decks"][0]["card_count"] == 1
    )
    assert not dinos["decks"][0]["mine"]
    assert [d["title"] for d in store.search(db, pat, "trip")["decks"]] == [
        "Spanish words"
    ]
    assert [b["title"] for b in store.search(db, pat, "dahl")["books"]] == ["Matilda"]
    # Sam's books are Sam's.
    assert store.search(db, sam, "matilda")["books"] == []
    assert store.search(db, pat, "  ") == {"decks": [], "cards": [], "books": []}
    assert len(store.search(db, pat, "a", limit=1)["cards"]) == 1


def test_search_through_the_server(env, api):
    made = api.post(
        "/api/learn/decks",
        {
            "title": "Capitals",
            "cards": [
                {"front": "France", "back": "Paris"},
                {"front": "Italy", "back": "Rome"},
            ],
        },
        expect=201,
    )
    found = api.get("/api/learn/search?q=paris")
    assert [c["front"] for c in found["cards"]] == ["France"]
    assert found["cards"][0]["deck_id"] == made["deck"]["id"]
    assert (
        api.get("/api/learn/search?q=capitals&limit=1")["decks"][0]["card_count"] == 2
    )
