"""Health's search box (aira.suite.care.search, GET /api/care/search): one person's medicines, appointments, kinds
of reading, diary days, clients and people, and only what the person searching may open."""

# ruff: noqa: F811
from __future__ import annotations

from aira.suite.care import search, service, work
from test_suite_care import Req, db, no_model, people  # noqa: F401

from aira.suite import life


def test_search_finds_each_kind_of_record(db, people):
    pat, rosa = people["pat"], people["rosa"]
    service.create_medicine(
        db, pat, {"name": "Metformin", "dose": "500 mg", "times": "08:00"}
    )
    service.create_medicine(db, pat, {"name": "Paracetamol", "as_needed": True})
    service.create_medicine(db, pat, {"name": "Ibuprofène", "times": "12:00"})
    service.save_appointment(
        db,
        pat,
        {
            "title": "Dr Patel",
            "at": "2030-05-04T10:30:00Z",
            "place": "Riverside Clinic",
        },
    )
    service.save_day(db, pat, {"mood": 2, "note": "Dizzy after the walk"})
    work.save_client(db, pat, {"name": "Mrs Rao", "rate_cents": 2000})
    service.add_caregiver(db, rosa, {"profile_id": pat["id"]})
    service.add_caregiver(db, pat, {"email": "ann@clinic.example", "name": "Nurse Ann"})

    def find(text: str) -> dict:
        return search.search(db, pat, pat, True, text)

    assert [m["name"] for m in find("metf")["medicines"]] == ["Metformin"]
    # Case and accents do not matter, and the place of an appointment counts.
    assert [a["title"] for a in find("RIVERSIDE")["appointments"]] == ["Dr Patel"]
    assert [m["name"] for m in find("ibuprofene")["medicines"]] == ["Ibuprofène"]
    assert [r["kind"] for r in find("blood")["readings"]] == [
        "blood_pressure",
        "glucose",
    ]
    assert [e["note"] for e in find("dizzy")["diary"]] == ["Dizzy after the walk"]
    assert [c["name"] for c in find("rao")["clients"]] == ["Mrs Rao"]
    people_found = find("a")["people"]
    assert {(p["name"], p["role"]) for p in people_found} >= {
        ("Rosa", "caring_for"),
        ("Nurse Ann", "caregiver"),
    }
    assert all(not rows for rows in find("   ").values())
    assert len(search.search(db, pat, pat, True, "a", limit=1)["medicines"]) == 1


def test_a_caregiver_finds_only_what_they_may_see(db, people):
    pat, rosa, mia = people["pat"], people["rosa"], people["mia"]
    service.create_medicine(db, rosa, {"name": "Statin", "times": "20:00"})
    service.save_day(db, rosa, {"mood": 1, "note": "Statin made me tired"})
    service.add_caregiver(db, rosa, {"profile_id": pat["id"]})
    person, can_edit = service.subject(db, pat, rosa["id"])
    seen = search.search(db, pat, person, can_edit, "statin")
    assert [m["name"] for m in seen["medicines"]] == ["Statin"]
    # Never the diary, clients or people of the person looked after.
    assert seen["diary"] == [] and seen["clients"] == [] and seen["people"] == []
    # A child finds no care clients (care work is for adults).
    assert life.is_adult(mia) is False
    assert search.search(db, mia, mia, True, "a")["clients"] == []
