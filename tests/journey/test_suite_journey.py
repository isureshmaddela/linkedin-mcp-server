"""Life (aira/suite/journey): goals with steps and check-ins, the timeline, the encrypted document vault, Today and
pulse, on its own, through the single-user server for a household, and in the hosted build."""

from __future__ import annotations

import base64
import http.client
import json
import sqlite3
from datetime import date, timedelta
from urllib.parse import urlsplit

import pytest
from cloud_support import build_org, sign_in
from cryptography.fernet import Fernet
from life_support import Household

import test_careers
from aira import suite
from aira.airaapi.store import SecretStore
from aira.suite import kit, life, public
from aira.suite.journey import journey_routes, service, store

env = test_careers.env
api = test_careers.api
stub = test_careers.stub

PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 64
PDF = b"%PDF-1.7\n1 0 obj\n<<>>\nendobj\n%%EOF\n"


class _NoModel:
    def model(self, prompt):
        return None


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
    connection = sqlite3.connect(tmp_path / "journey.sqlite3")
    connection.row_factory = sqlite3.Row
    store.ensure(connection)
    yield connection
    connection.close()


@pytest.fixture
def people(db):
    pat = life.current(Req(), db)
    sam = life.add(db, pat, {"name": "Sam", "age_group": "adult"})
    mia = life.add(db, pat, {"name": "Mia", "age_group": "child"})
    return {"pat": pat, "sam": sam, "mia": mia}


@pytest.fixture
def seal():
    return SecretStore(Fernet.generate_key())


def _b64(data: bytes) -> str:
    return base64.b64encode(data).decode()


# ---------------------------------------------------------------------------
# goals
# ---------------------------------------------------------------------------


def test_steps_drive_progress_and_finishing_puts_the_goal_on_the_timeline(db, people):
    pat = people["pat"]
    goal = service.create_goal(
        db,
        pat,
        {
            "title": "Run a 5K",
            "category": "health",
            "steps": ["Buy shoes", "Run 2K", "Run 5K"],
        },
    )
    assert (
        goal["progress"] == 0
        and len(goal["steps"]) == 3
        and goal["state"] == "on_track"
    )
    goal = service.set_step(db, pat, goal["steps"][0]["id"], {"done": True})
    assert goal["progress"] == 33 and goal["steps_done"] == 1
    goal = service.add_step(db, pat, goal["id"], {"title": "Stretch"})
    assert goal["progress"] == 25
    for step in goal["steps"]:
        goal = service.set_step(db, pat, step["id"], {"done": True})
    assert (
        goal["status"] == "completed"
        and goal["progress"] == 100
        and goal["completed_at"]
    )
    events = service.list_events(db, pat)
    assert [e["title"] for e in events] == ["Reached a goal: Run a 5K"]
    assert events[0]["kind"] == "achievement" and events[0]["goal_id"] == goal["id"]
    # Unticking a step reopens it; finishing again does not add a second event.
    goal = service.set_step(db, pat, goal["steps"][0]["id"], {"done": False})
    assert goal["status"] == "active" and goal["progress"] == 75
    goal = service.set_step(db, pat, goal["steps"][0]["id"], {"done": True})
    assert goal["status"] == "completed"
    assert len(service.list_events(db, pat)) == 1


def test_check_ins_set_progress_and_one_hundred_completes(db, people):
    pat = people["pat"]
    goal = service.create_goal(
        db, pat, {"title": "Read 12 books", "category": "learning"}
    )
    goal = service.check_in(
        db, pat, goal["id"], {"progress": 40, "note": "Five so far"}
    )
    assert goal["progress"] == 40 and goal["checkins"][0]["note"] == "Five so far"
    with pytest.raises(ValueError):
        service.check_in(db, pat, goal["id"], {"progress": 140})
    goal = service.check_in(db, pat, goal["id"], {"progress": 100})
    assert goal["status"] == "completed"
    goal = service.update_goal(db, pat, goal["id"], {"status": "active"})
    assert (
        goal["status"] == "active"
        and goal["progress"] == 90
        and goal["completed_at"] is None
    )
    goal = service.update_goal(db, pat, goal["id"], {"status": "dropped"})
    with pytest.raises(ValueError, match="dropped"):
        service.check_in(db, pat, goal["id"], {"progress": 50})


def test_goal_states_say_behind_and_overdue():
    today = date(2026, 10, 26)
    base = {"status": "active", "progress": 10, "created_at": "2026-01-01T00:00:00Z"}
    assert service.goal_state({**base, "target_date": "2026-12-31"}, today) == "behind"
    assert (
        service.goal_state({**base, "progress": 85, "target_date": "2026-12-31"}, today)
        == "on_track"
    )
    assert service.goal_state({**base, "target_date": "2026-10-01"}, today) == "overdue"
    assert service.goal_state({**base, "target_date": ""}, today) == "on_track"
    assert (
        service.goal_state(
            {**base, "status": "paused", "target_date": "2026-10-01"}, today
        )
        == "paused"
    )


def test_goals_are_private_and_filters_work(db, people):
    pat, sam = people["pat"], people["sam"]
    mine = service.create_goal(
        db, pat, {"title": "Save for a trip", "category": "money"}
    )
    service.create_goal(db, pat, {"title": "Learn Telugu", "category": "learning"})
    with pytest.raises(LookupError):
        service.get_goal(db, sam, mine["id"])
    with pytest.raises(LookupError):
        service.check_in(db, sam, mine["id"], {"progress": 50})
    with pytest.raises(LookupError):
        service.delete_goal(db, sam, mine["id"])
    assert service.list_goals(db, sam) == []
    assert [g["title"] for g in service.list_goals(db, pat, category="money")] == [
        "Save for a trip"
    ]
    assert [g["title"] for g in service.list_goals(db, pat, search="telugu")] == [
        "Learn Telugu"
    ]
    with pytest.raises(ValueError):
        service.list_goals(db, pat, status="secret")
    csv = service.goals_csv(db, pat).decode()
    assert (
        csv.splitlines()[0].startswith("title,category,status,progress")
        and "Save for a trip" in csv
    )


# ---------------------------------------------------------------------------
# timeline
# ---------------------------------------------------------------------------


def test_shared_events_reach_the_household_and_only_their_owner_changes_them(
    db, people
):
    pat, sam = people["pat"], people["sam"]
    private = service.save_event(
        db, pat, {"title": "First job", "event_date": "2020-07-31", "kind": "career"}
    )
    shared = service.save_event(
        db,
        pat,
        {
            "title": "Moved to Hyderabad",
            "event_date": "2023-03-14",
            "kind": "home",
            "shared": True,
        },
    )
    assert [e["title"] for e in service.list_events(db, pat)] == [
        "Moved to Hyderabad",
        "First job",
    ]
    assert service.list_events(db, sam) == []
    family = service.list_events(db, sam, family=True)
    assert [e["title"] for e in family] == ["Moved to Hyderabad"]
    assert family[0]["mine"] is False and family[0]["owner_name"] == pat["name"]
    with pytest.raises(PermissionError):
        service.save_event(db, sam, {"title": "Changed"}, shared["id"])
    with pytest.raises(LookupError):
        service.delete_event(db, sam, private["id"])
    assert [e["title"] for e in service.list_events(db, pat, year="2020")] == [
        "First job"
    ]
    assert [e["title"] for e in service.list_events(db, pat, kind="home")] == [
        "Moved to Hyderabad"
    ]
    with pytest.raises(ValueError):
        service.save_event(db, pat, {"title": "No date"})
    with pytest.raises(ValueError):
        service.list_events(db, pat, year="20x0")


# ---------------------------------------------------------------------------
# document vault
# ---------------------------------------------------------------------------


def test_numbers_and_scans_are_encrypted_and_only_their_owner_opens_them(
    db, people, seal
):
    pat, sam = people["pat"], people["sam"]
    doc = service.save_document(
        db,
        pat,
        {
            "title": "Passport",
            "kind": "passport",
            "number": "Z1234567",
            "expires_on": "2030-01-14",
            "tags": "travel, identity",
            "file": {"name": "scan.png", "data": _b64(PNG)},
        },
        seal,
    )
    assert doc["number"] == "Z1234567" and doc["number_hint"] == "•••• 4567"
    assert doc["file"] == {"name": "scan.png", "type": "image/png", "size": len(PNG)}
    assert doc["tags"] == ["travel", "identity"]
    row = dict(db.execute("SELECT * FROM jr_documents").fetchone())
    assert b"Z1234567" not in bytes(row["number_cipher"]) and PNG not in bytes(
        row["file_cipher"]
    )
    assert (
        b"Z1234567"
        not in (
            db.execute("SELECT group_concat(number_hint) FROM jr_documents").fetchone()[
                0
            ]
            or ""
        ).encode()
    )
    data, kind, name = service.document_file(db, pat, doc["id"], seal)
    assert (data, kind, name) == (PNG, "image/png", "scan.png")
    listed = service.list_documents(db, pat)
    assert "number" not in listed[0]  # the list never decrypts
    for attempt in (
        lambda: service.get_document(db, sam, doc["id"], seal),
        lambda: service.document_file(db, sam, doc["id"], seal),
        lambda: service.delete_document(db, sam, doc["id"]),
    ):
        with pytest.raises(LookupError):
            attempt()
    # Editing without a number or file keeps both; remove_file drops the scan.
    edited = service.save_document(db, pat, {"title": "My passport"}, seal, doc["id"])
    assert edited["number"] == "Z1234567" and edited["file"]["size"] == len(PNG)
    edited = service.save_document(db, pat, {"remove_file": True}, seal, doc["id"])
    assert edited["file"] is None
    with pytest.raises(LookupError):
        service.document_file(db, pat, doc["id"], seal)


def test_only_scans_photos_and_text_up_to_ten_megabytes(db, people, seal):
    pat = people["pat"]
    pdf = service.save_document(
        db,
        pat,
        {"title": "Degree", "file": {"name": "degree.exe.pdf", "data": _b64(PDF)}},
        seal,
    )
    assert (
        pdf["file"]["type"] == "application/pdf"
        and pdf["file"]["name"] == "degree.exe.pdf"
    )
    with pytest.raises(ValueError, match="PDF, a photo"):
        service.save_document(
            db,
            pat,
            {
                "title": "Bad",
                "file": {"name": "x.pdf", "data": _b64(b"MZ\x90\x00program")},
            },
            seal,
        )
    with pytest.raises(ValueError, match="10 MB"):
        service.save_document(
            db,
            pat,
            {
                "title": "Big",
                "file": {
                    "name": "big.pdf",
                    "data": _b64(b"%PDF-" + b"0" * (10 * 1024 * 1024)),
                },
            },
            seal,
        )
    with pytest.raises(ValueError, match="could not be read"):
        service.save_document(
            db, pat, {"title": "Broken", "file": {"name": "a.png", "data": "***"}}, seal
        )
    text = service.save_document(
        db,
        pat,
        {
            "title": "Notes",
            "file": {"name": "notes.txt", "data": _b64("ఆధార్ notes".encode())},
        },
        seal,
    )
    assert text["file"]["type"] == "text/plain"
    with pytest.raises(ValueError, match="before the issue date"):
        service.save_document(
            db,
            pat,
            {"title": "Odd", "issued_on": "2025-01-01", "expires_on": "2024-01-01"},
            seal,
        )


def test_expiry_states_overview_and_reminders(db, people, seal):
    pat = people["pat"]
    today = service.today(db)
    soon = (today + timedelta(days=7)).isoformat()
    service.save_document(
        db,
        pat,
        {"title": "Insurance card", "kind": "insurance", "expires_on": soon},
        seal,
    )
    service.save_document(
        db,
        pat,
        {
            "title": "Old visa",
            "kind": "visa",
            "expires_on": (today - timedelta(days=3)).isoformat(),
        },
        seal,
    )
    service.save_document(
        db, pat, {"title": "Birth certificate", "kind": "birth_certificate"}, seal
    )
    service.save_document(
        db,
        pat,
        {"title": "Licence", "expires_on": (today + timedelta(days=400)).isoformat()},
        seal,
    )
    states = {d["title"]: d["state"] for d in service.list_documents(db, pat)}
    assert states == {
        "Insurance card": "expires_soon",
        "Old visa": "expired",
        "Birth certificate": "no_expiry",
        "Licence": "valid",
    }
    assert [d["title"] for d in service.list_documents(db, pat, state="expired")] == [
        "Old visa"
    ]
    goal = service.create_goal(
        db,
        pat,
        {
            "title": "Pass the exam",
            "target_date": (today + timedelta(days=3)).isoformat(),
        },
    )
    service.check_in(db, pat, goal["id"], {"progress": 50})
    service.create_goal(db, pat, {"title": "Someday", "category": "travel"})
    service.save_event(
        db,
        pat,
        {"title": "Wedding", "event_date": (today + timedelta(days=20)).isoformat()},
    )
    data = service.overview(db, pat)
    assert (
        data["goals"]["active"] == 2
        and data["life_score"] == 25
        and data["goals"]["in_progress"] == 1
    )
    assert data["documents"] == {
        "total": 4,
        "expires_soon": 1,
        "expired": 1,
        "bytes": 0,
    }
    # The licence expires in 400 days: past the year "coming up" covers.
    assert [m["title"] for m in data["milestones"]] == [
        "Pass the exam",
        "Insurance card",
        "Wedding",
    ]
    due = service.reminders(db, pat)
    assert [g["title"] for g in due["goals"]] == ["Pass the exam"]
    assert [d["title"] for d in due["documents"]] == ["Old visa", "Insurance card"]
    card = journey_routes._today(db, pat)[0]
    assert card["title"] == "Goals and documents" and card["tone"] == "warn"
    assert [line["text"] for line in card["lines"]] == [
        "Pass the exam",
        "Old visa",
        "Insurance card",
    ]
    pulse = journey_routes._pulse(db, pat, "")
    assert pulse["badge"] == 3
    assert [a["id"] for a in pulse["alerts"]] == [
        f"journey:doc:{d['id']}:{d['expires_on']}"
        for d in due["documents"]
        if d["days_left"] == 7
    ]
    # Nothing due, no card.
    assert journey_routes._today(db, people["sam"]) == []


# ---------------------------------------------------------------------------
# the single-user server
# ---------------------------------------------------------------------------


def test_a_household_keeps_goals_a_timeline_and_a_vault_on_the_local_build(env, api):
    kit.forget_schema()
    home = Household(env, api)
    pat = home.owner
    mia = home.add("Mia", age_group="child", pin="2468")
    goal = pat.post(
        "/api/journey/goals",
        {"title": "Run a 5K", "category": "health", "steps": ["Buy shoes", "Run 5K"]},
        expect=201,
    )["goal"]
    pat.ok("PATCH", f"/api/journey/steps/{goal['steps'][0]['id']}", {"done": True})
    assert pat.get(f"/api/journey/goals/{goal['id']}")["goal"]["progress"] == 50
    # A goal with steps follows its steps: a hand-set progress is refused, a note is kept.
    status, body = pat.call(
        "POST", f"/api/journey/goals/{goal['id']}/checkin", {"progress": 100}
    )
    assert status == 400 and "Tick a step" in body["error"]
    pat.post(
        f"/api/journey/goals/{goal['id']}/checkin", {"note": "Shoes bought, legs sore"}
    )
    done = pat.ok(
        "PATCH", f"/api/journey/steps/{goal['steps'][1]['id']}", {"done": True}
    )["goal"]
    assert done["status"] == "completed" and done["checkins"][0]["note"]
    assert mia.get("/api/journey/goals")["goals"] == []
    assert mia.call("GET", f"/api/journey/goals/{goal['id']}")[0] == 404

    pat.post(
        "/api/journey/events",
        {
            "title": "Family trip to Goa",
            "event_date": "2025-12-20",
            "kind": "travel",
            "shared": True,
        },
        expect=201,
    )
    shared = mia.get("/api/journey/events?family=1")["events"]
    assert [e["title"] for e in shared] == ["Family trip to Goa"]
    assert [e["title"] for e in pat.get("/api/journey/events")["events"]] == [
        "Reached a goal: Run a 5K",
        "Family trip to Goa",
    ]

    expires = (date.today() + timedelta(days=30)).isoformat()
    doc = pat.post(
        "/api/journey/documents",
        {
            "title": "Passport",
            "kind": "passport",
            "number": "Z1234567",
            "expires_on": expires,
            "file": {"name": "passport.pdf", "data": _b64(PDF)},
        },
        expect=201,
    )["document"]
    assert doc["number"] == "Z1234567" and doc["state"] == "expires_soon"
    status, raw = pat.call("GET", f"/api/journey/documents/{doc['id']}/file", raw=True)
    assert status == 200 and raw == PDF
    assert mia.call("GET", f"/api/journey/documents/{doc['id']}")[0] == 404
    assert (
        mia.call("GET", f"/api/journey/documents/{doc['id']}/file", raw=True)[0] == 404
    )
    status, body = pat.call(
        "POST",
        "/api/journey/documents",
        {"title": "Program", "file": {"name": "a.pdf", "data": _b64(b"MZprogram")}},
    )
    assert status == 400 and "PDF" in body["error"]

    overview = pat.get("/api/journey/overview")
    assert overview["documents"]["expires_soon"] == 1 and overview["events"] == 2
    cards = pat.get("/api/life/today")["cards"]
    assert any(
        c["app"] == "journey" and c["lines"][0]["text"] == "Passport" for c in cards
    )
    assert pat.get("/api/life/pulse")["apps"]["journey"]["badge"] == 1
    status, csv = pat.call("GET", "/api/journey/export/goals", raw=True)
    assert status == 200 and b"Run a 5K" in csv
    pat.ok("DELETE", f"/api/journey/documents/{doc['id']}")
    assert pat.get("/api/journey/documents")["documents"] == []


# ---------------------------------------------------------------------------
# the hosted build
# ---------------------------------------------------------------------------


def test_hosted_life_needs_the_app_and_keeps_each_member_private(cloud):
    kit.forget_schema()
    org = build_org(cloud)
    owner = sign_in(cloud, org.owner)
    status, body = owner.call("GET", "/api/journey/overview")
    assert status == 402 and body["code"] == "app_required"
    cloud.billing.subscribe_app(org.owner, "journey")
    made = {}
    for who in ("owner", "admin", "coach", "candidate", "billing", "auditor"):
        browser = sign_in(cloud, getattr(org, who))
        assert browser.call("GET", "/api/journey/overview")[0] == 200, who
        status, created = browser.call(
            "POST", "/api/journey/goals", {"title": f"{who} goal"}
        )
        assert status == 201, (who, created)
        made[who] = created["goal"]["id"]
    candidate = sign_in(cloud, org.candidate)
    assert [
        g["title"] for g in candidate.call("GET", "/api/journey/goals")[1]["goals"]
    ] == ["candidate goal"]
    assert candidate.call("GET", f"/api/journey/goals/{made['owner']}")[0] == 404
    status, doc = owner.call(
        "POST",
        "/api/journey/documents",
        {
            "title": "Company ID",
            "number": "E-1001",
            "file": {"name": "id.png", "data": _b64(PNG)},
        },
    )
    assert status == 201, doc
    status, raw = owner.call(
        "GET", f"/api/journey/documents/{doc['document']['id']}/file", raw=True
    )
    assert status == 200 and raw == PNG
    assert (
        candidate.call("GET", f"/api/journey/documents/{doc['document']['id']}")[0]
        == 404
    )
    other = build_org(cloud, "Other Org", slug_email="other.test")
    cloud.billing.subscribe_app(other.owner, "journey")
    status, goals = sign_in(cloud, other.owner).call("GET", "/api/journey/goals")
    assert status == 200 and goals["goals"] == []


def test_a_delete_with_a_body_does_not_break_the_next_request_on_the_connection(
    env, api
):
    """A client may send a body with DELETE; the server must read it, or the bytes start the next request."""
    kit.forget_schema()
    pat = Household(env, api).owner
    goal = pat.post("/api/journey/goals", {"title": "Keep alive"}, expect=201)["goal"]
    jar = next(h.cookiejar for h in api.opener.handlers if hasattr(h, "cookiejar"))
    cookie = "; ".join(f"{c.name}={c.value}" for c in jar)
    parts = urlsplit(env.base)
    connection = http.client.HTTPConnection(parts.hostname, parts.port, timeout=30)
    headers = {
        "Cookie": cookie,
        "Content-Type": "application/json",
        "X-Requested-With": "aira",
        "Origin": env.base,
        "X-Aira-Profile": pat.header,
    }
    connection.request(
        "DELETE", f"/api/journey/goals/{goal['id']}", body=b"{}", headers=headers
    )
    first = connection.getresponse()
    assert first.status == 200 and first.read()
    connection.request("GET", "/api/journey/goals", headers=headers)
    second = connection.getresponse()
    assert second.status == 200 and json.loads(second.read())["goals"] == []
    connection.close()
