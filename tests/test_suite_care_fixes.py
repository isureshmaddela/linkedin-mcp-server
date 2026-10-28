"""Health (app id ``care``): regressions for the problems an independent tester found after the care work round.

The hosted public links in the real start-up order, a stopped medicine kept in the doctor's numbers, a changed
time (or a pause) never rewriting the past, who may move everyone's time zone, the client's "I have paid" note,
"Email it" (sent only once it went, never twice), visits, diary days and readings in the future, deleting a past
client, and the wording of counts."""

from __future__ import annotations

import json
import sqlite3
import urllib.error
import urllib.request
from datetime import date, datetime, timedelta, timezone

import pytest
from cloud_support import Browser, build_org, sign_in
from life_support import Household

import auto_apply_assistant as local
import test_careers
from aira.suite import kit, life, public
from aira.suite.care import report, service, store, work

env = test_careers.env
api = test_careers.api
stub = test_careers.stub


class _NoModel:
    def model(self, prompt):
        return None


@pytest.fixture(autouse=True)
def no_model():
    """Never call the real writing model (a live Ollama may run on this machine)."""
    from aira import suite

    previous = suite.gateway()
    suite.set_gateway(_NoModel())
    yield
    suite.set_gateway(previous)


class Req:
    def __init__(self) -> None:
        self.user = {"email": "pat@home.example"}

    def header(self, name: str, default: str = "") -> str:
        return default


@pytest.fixture
def db(tmp_path):
    kit.forget_schema()
    public.set_index(public._MemoryIndex())
    connection = sqlite3.connect(tmp_path / "care.sqlite3")
    connection.row_factory = sqlite3.Row
    store.ensure(connection)
    yield connection
    connection.close()


@pytest.fixture
def pat(db):
    return life.current(Req(), db)


@pytest.fixture
def local_index():
    public.set_index(local._public_link_index)


def utc(day: str, clock: str) -> datetime:
    hour, minute = (int(part) for part in clock.split(":"))
    return datetime.fromisoformat(day).replace(
        hour=hour, minute=minute, tzinfo=timezone.utc
    )


def created(db, medicine_id: str, at: str) -> None:
    db.execute("UPDATE cr_medicines SET created_at = ? WHERE id = ?", (at, medicine_id))
    db.commit()


def anonymous(env, method, path, body=None):
    data = json.dumps(body).encode() if body is not None else None
    request = urllib.request.Request(env.base + path, data=data, method=method)
    request.add_header("Content-Type", "application/json")
    request.add_header("X-Requested-With", "aira")
    request.add_header("Origin", env.base)
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            return response.status, json.loads(response.read() or b"{}")
    except urllib.error.HTTPError as error:
        return error.code, json.loads(error.read() or b"{}")


# ---------------------------------------------------------------------------
# high: hosted public links in the real start-up order
# ---------------------------------------------------------------------------


def test_hosted_health_links_open_when_the_single_user_index_was_installed_last(
    cloud,
):
    """The real server builds the CloudApp (which installs the organization index) and then the gateway imports
    the single-user module, whose ``set_default_index`` made its own index the current one again. Every link a
    member made then answered 404. Health's links are now also put in the organization's index."""
    kit.forget_schema()
    # The production order without any other app's workaround: the single-user index made current again over the
    # organization index (set directly, so a wrapped ``set_default_index`` cannot hide the bug).
    public._index = local._public_link_index
    assert type(public._index).__module__ != "aira_cloud.public_links"
    org = build_org(cloud)
    cloud.billing.subscribe_app(org.owner, "care")
    visitor = Browser(cloud)
    for who in ("owner", "admin", "coach", "candidate", "billing", "auditor"):
        member = sign_in(cloud, getattr(org, who))
        status, client = member.call(
            "POST",
            "/api/care/work/clients",
            {"name": f"Client of {who}", "rate_cents": 2000},
        )
        assert status == 201, (who, client)
        status, visit = member.call(
            "POST",
            "/api/care/work/visits",
            {"client_id": client["client"]["id"], "minutes": 90},
        )
        assert status == 201, (who, visit)
        status, statement = member.call(
            "POST", "/api/care/work/statements", {"client_id": client["client"]["id"]}
        )
        assert status == 201, (who, statement)
        statement_id = statement["statement"]["id"]
        status, made = member.call(
            "POST", f"/api/care/work/statements/{statement_id}/link", {}
        )
        assert status == 200, (who, made)
        token = made["link"]["token"]
        status, page = visitor.call("GET", f"/api/public/{token}")
        assert status == 200, (who, page)
        assert page["page"]["statement"]["total_cents"] == 3000
        status, told = visitor.call(
            "POST", f"/api/public/{token}/paid", {"note": "UPI ref 12345"}
        )
        assert status == 200 and told == {"status": "told"}, (who, told)
        # The emergency card and the doctor's report.
        status, card = member.call("POST", "/api/care/card/link", {})
        assert status == 200, (who, card)
        assert visitor.call("GET", f"/api/public/{card['link']['token']}")[0] == 200
        status, shared = member.call("POST", "/api/care/report/link", {"days": 30})
        assert status == 200, (who, shared)
        assert visitor.call("GET", f"/api/public/{shared['link']['token']}")[0] == 200
        # Turning a link off works in the organization's index too.
        assert member.call("DELETE", "/api/care/card/link")[0] == 200
        assert visitor.call("GET", f"/api/public/{card['link']['token']}")[0] == 404
        assert (
            member.call("DELETE", f"/api/care/work/statements/{statement_id}")[0] == 200
        )
        assert visitor.call("GET", f"/api/public/{token}")[0] == 404
    public.set_index(cloud.public_index)


# ---------------------------------------------------------------------------
# medium: a stopped medicine stays in the numbers and the doctor's report
# ---------------------------------------------------------------------------


def test_a_stopped_medicine_stays_in_the_report_and_the_numbers(db, pat):
    now = utc("2026-10-27", "12:00")
    pills = service.create_medicine(
        db, pat, {"name": "Metformin", "dose": "500 mg", "times": "08:00"}
    )
    created(db, pills["id"], "2026-10-20T00:00:00Z")
    for day in ("2026-10-25", "2026-10-26"):
        service.take(
            db,
            pat,
            {"medicine_id": pills["id"], "date": day, "time": "08:00"},
            now=utc(day, "08:10"),
        )
    before = report.build(db, pat, 30, now=now)
    row = before["medicines"][0]
    assert (row["taken"], row["missed"], row["active"]) == (2, 6, True)
    # Stopped because of side effects: exactly what the doctor needs to see.
    assert service.delete_medicine(db, pat, pills["id"], now=now) == "paused"
    after = report.build(db, pat, 30, now=now + timedelta(days=2))
    assert [m["name"] for m in after["medicines"]] == ["Metformin"]
    row = after["medicines"][0]
    assert (row["taken"], row["missed"], row["active"]) == (2, 6, False)
    overall = service.adherence(db, pat["id"], 30, now + timedelta(days=2))
    assert (overall["taken"], overall["missed"], overall["counted"]) == (2, 6, 8)
    # Today and the reminders show only medicines still taken.
    assert (
        service.day_slots(db, pat["id"], date(2026, 10, 29), now + timedelta(days=2))
        == []
    )
    # A stopped medicine deleted for good leaves nothing behind.
    assert service.delete_medicine(db, pat, pills["id"]) == "deleted"
    assert kit.count(db, "SELECT COUNT(*) FROM cr_schedules", ()) == 0


# ---------------------------------------------------------------------------
# medium: changing a time, or a pause, never rewrites the past
# ---------------------------------------------------------------------------


def _take_daily(db, pat, medicine_id: str, first: int, last: int) -> None:
    for day in range(first, last + 1):
        stamp = f"2026-10-{day:02d}"
        service.take(
            db,
            pat,
            {"medicine_id": medicine_id, "date": stamp, "time": "08:00"},
            now=utc(stamp, "08:05"),
        )


@pytest.mark.parametrize("older_database", [False, True])
def test_changing_a_time_keeps_the_doses_taken_at_the_old_time(db, pat, older_database):
    now = utc("2026-10-27", "12:00")
    pills = service.create_medicine(db, pat, {"name": "Metformin", "times": "08:00"})
    created(db, pills["id"], "2026-10-10T00:00:00Z")
    if older_database:  # a medicine added before Health kept schedules
        db.execute("DELETE FROM cr_schedules")
        db.commit()
    _take_daily(db, pat, pills["id"], 17, 26)
    week = service.adherence(db, pat["id"], 7, now)
    assert (week["taken"], week["missed"], week["percent"]) == (6, 1, 86)
    service.update_medicine(db, pat, pills["id"], {"times": "09:00"}, now=now)
    week = service.adherence(db, pat["id"], 7, now)
    assert (week["taken"], week["missed"], week["percent"]) == (6, 1, 86)
    per_medicine = service.medicine_adherence(db, pat["id"], 30, now)[pills["id"]]
    assert per_medicine["taken"] == 10
    # From the next day the new time counts.
    tomorrow = utc("2026-10-28", "10:30")
    slots = service.day_slots(db, pat["id"], date(2026, 10, 28), tomorrow)
    assert [(s["time"], s["status"]) for s in slots] == [("09:00", "missed")]
    service.take(
        db,
        pat,
        {"medicine_id": pills["id"], "date": "2026-10-28", "time": "09:00"},
        now=tomorrow,
    )
    week = service.adherence(db, pat["id"], 7, tomorrow)
    assert (week["taken"], week["missed"]) == (
        6,
        1,
    )  # the 22nd fell out, the 28th came in
    # A dose missed under the old time can still be marked from today's list.
    today = service.day_slots(db, pat["id"], date(2026, 10, 27), now)
    assert [(s["time"], s["status"]) for s in today] == [("08:00", "missed")]
    service.take(
        db,
        pat,
        {"medicine_id": pills["id"], "date": "2026-10-27", "time": "08:00"},
        now=now,
    )


def test_a_pause_is_not_counted_as_missed_days(db, pat):
    pills = service.create_medicine(db, pat, {"name": "Metformin", "times": "08:00"})
    created(db, pills["id"], "2026-10-10T00:00:00Z")
    _take_daily(db, pat, pills["id"], 17, 26)
    assert (
        service.delete_medicine(db, pat, pills["id"], now=utc("2026-10-27", "12:00"))
        == "paused"
    )
    service.update_medicine(
        db, pat, pills["id"], {"active": True}, now=utc("2026-10-30", "12:00")
    )
    week = service.adherence(db, pat["id"], 7, utc("2026-10-31", "12:00"))
    # 25 and 26 taken, 27 and 31 missed; 28 and 29 paused and 30's 08:00 came before the restart.
    assert (week["taken"], week["missed"]) == (2, 2)
    # Changing only the name or the dose changes nothing about the schedule.
    service.update_medicine(
        db, pat, pills["id"], {"dose": "850 mg"}, now=utc("2026-10-31", "12:00")
    )
    assert kit.count(db, "SELECT COUNT(*) FROM cr_schedules", ()) == 2


# ---------------------------------------------------------------------------
# medium: who may change the one time zone everyone's reminders use
# ---------------------------------------------------------------------------


def test_a_child_cannot_move_the_households_time_zone(env, api, local_index):
    kit.forget_schema()
    household = Household(env, api)
    pat = household.owner
    rosa = household.add("Rosa", age_group="senior")
    mia = household.add("Mia", age_group="child", pin="2468")
    pat.post("/api/care/settings", {"timezone": "Europe/London"})
    assert mia.get("/api/care/medicines")["timezone"]["can_change"] is False
    status, refused = mia.call(
        "POST", "/api/care/settings", {"timezone": "America/New_York"}
    )
    assert status == 403 and "adult" in refused["error"]
    assert rosa.get("/api/care/medicines")["timezone"]["name"] == "Europe/London"
    # Another adult may change it.
    assert rosa.get("/api/care/overview")["timezone"]["can_change"] is True
    rosa.post("/api/care/settings", {"timezone": "Asia/Kolkata"})
    assert pat.get("/api/care/medicines")["timezone"]["name"] == "Asia/Kolkata"
    pat.post("/api/care/settings", {"timezone": "UTC"})


def test_hosted_only_the_owner_or_an_admin_change_the_time_zone(cloud):
    kit.forget_schema()
    org = build_org(cloud)
    cloud.billing.subscribe_app(org.owner, "care")
    owner = sign_in(cloud, org.owner)
    # The first time anyone's browser may fill it in.
    candidate = sign_in(cloud, org.candidate)
    status, first = candidate.call(
        "POST", "/api/care/settings", {"timezone": "Europe/London"}
    )
    assert status == 200 and first["timezone"]["can_change"] is False
    for who in ("candidate", "auditor", "coach", "billing"):
        member = sign_in(cloud, getattr(org, who))
        status, refused = member.call(
            "POST", "/api/care/settings", {"timezone": "Pacific/Auckland"}
        )
        assert status == 403, (who, refused)
    assert owner.call("GET", "/api/care/medicines")[1]["timezone"] == {
        "name": "Europe/London",
        "source": "health",
        "can_change": True,
    }
    admin = sign_in(cloud, org.admin)
    assert (
        admin.call("POST", "/api/care/settings", {"timezone": "Asia/Kolkata"})[0] == 200
    )
    assert (
        owner.call("GET", "/api/care/overview")[1]["timezone"]["name"] == "Asia/Kolkata"
    )


# ---------------------------------------------------------------------------
# medium: the client's note, and "Email it"
# ---------------------------------------------------------------------------


def _billed(db, pat, email: str = "lee@example.com") -> dict:
    client = work.save_client(
        db, pat, {"name": "Mr Lee", "email": email, "rate_cents": 2000}
    )
    work.save_visit(
        db, pat, {"client_id": client["id"], "day": "2026-10-01", "minutes": 150}
    )
    return work.create_statement(db, pat, {"client_id": client["id"]})


def test_the_clients_paid_note_reaches_the_carer(db, pat):
    statement = _billed(db, pat)
    made = work.link(db, pat, statement["id"])
    status, _ = public.act(
        db, made["token"], "paid", {"note": "Paid by UPI ref 12345"}, {}
    )
    assert status == 200
    shown = work.get_statement(db, pat["id"], statement["id"])
    assert shown["client_paid_note"] == "Paid by UPI ref 12345"
    assert (
        work.statements(db, pat["id"])[0]["client_paid_note"] == "Paid by UPI ref 12345"
    )
    (alert,) = work.alerts(db, pat)
    assert "UPI ref 12345" in alert["body"]
    # A reference sent afterwards replaces the first note; an empty press keeps it.
    public.act(db, made["token"], "paid", {"note": "ref 99"}, {})
    public.act(db, made["token"], "paid", {}, {})
    assert (
        work.get_statement(db, pat["id"], statement["id"])["client_paid_note"]
        == "ref 99"
    )


def test_email_it_waits_for_the_email_and_never_queues_two(db, pat):
    statement = _billed(db, pat)
    first = work.send(db, pat, statement["id"])
    assert first["statement"]["status"] == "open"
    assert first["statement"]["email"]["status"] == "pending"
    assert "1 visit, 2 hours 30 minutes, $50.00" in first["message"]["body"]
    second = work.send(db, pat, statement["id"])
    assert second["already"] is True
    assert len([m for m in kit.outbox(db) if m["kind"] == "care_statement"]) == 1
    # Cancelled in the outbox: it can be emailed again, still not "sent".
    kit.set_outbox_status(db, [first["message"]["id"]], "cancelled")
    third = work.send(db, pat, statement["id"])
    assert third["already"] is False and third["statement"]["status"] == "open"
    # Approved and gone: now it is sent.
    kit.save_email_settings(
        db, {"from_email": "pat@home.example", "smtp_host": "smtp.example"}
    )
    kit.set_outbox_status(db, [third["message"]["id"]], "approved")
    assert (
        work.send(db, pat, statement["id"])["already"] is True
    )  # approved, about to go
    kit.send_outbox(db, "pw", sender=lambda *args: None)
    listed = work.statements(db, pat["id"])[0]
    assert listed["status"] == "sent" and listed["sent_at"]
    assert listed["email"]["status"] == "sent"


# ---------------------------------------------------------------------------
# low: the future, deleting a past client
# ---------------------------------------------------------------------------


def test_nothing_can_be_dated_in_the_future(db, pat):
    client = work.save_client(db, pat, {"name": "Mrs Rao", "rate_cents": 1500})
    tomorrow = (service.local_today(db) + timedelta(days=1)).isoformat()
    with pytest.raises(ValueError, match="future"):
        work.save_visit(
            db, pat, {"client_id": client["id"], "day": tomorrow, "minutes": 60}
        )
    today = work.save_visit(db, pat, {"client_id": client["id"], "minutes": 60})
    assert today["day"] == service.local_today(db).isoformat()
    with pytest.raises(ValueError):
        service.save_day(db, pat, {"mood": 3, "day": tomorrow})
    with pytest.raises(ValueError, match="future"):
        service.add_reading(
            db,
            pat,
            {
                "kind": "weight",
                "value1": 70,
                "at": kit.iso(kit.utcnow() + timedelta(days=30)),
            },
        )
    service.add_reading(db, pat, {"kind": "weight", "value1": 71})
    # A future reading an older Health stored is left out of the lists and the report.
    db.execute(
        "INSERT INTO cr_readings (id, owner_id, kind, value1, unit, at, created_at) VALUES (?, ?, 'weight', 99, 'kg', ?, ?)",
        (
            kit.new_id(),
            pat["id"],
            kit.iso(kit.utcnow() + timedelta(days=30)),
            kit.now(),
        ),
    )
    db.commit()
    assert [r["value1"] for r in service.readings(db, pat["id"])] == [71]
    assert service.latest_readings(db, pat["id"])["weight"]["value1"] == 71
    weight = next(
        r for r in report.build(db, pat, 30)["readings"] if r["kind"] == "weight"
    )
    assert weight["count"] == 1


def test_a_past_client_can_be_deleted_for_good(db, pat):
    statement = _billed(db, pat)
    client_id = statement["client_id"]
    made = work.link(db, pat, statement["id"])
    assert work.delete_client(db, pat, client_id) == "archived"
    with pytest.raises(ValueError, match="waiting to be paid"):
        work.delete_client(db, pat, client_id)
    work.set_paid(db, pat, statement["id"], True)
    assert work.delete_client(db, pat, client_id) == "deleted"
    assert work.clients(db, pat["id"]) == [] and work.statements(db, pat["id"]) == []
    assert work.visits(db, pat["id"]) == []
    with pytest.raises(LookupError):
        public.view(db, made["token"])
    assert (
        kit.revenue(db, app="care")["total_cents"] == 5000
    )  # the money received stays


# ---------------------------------------------------------------------------
# the fixed journeys over HTTP on the local build
# ---------------------------------------------------------------------------


def test_the_fixed_journeys_on_the_local_build(env, api, local_index):
    kit.forget_schema()
    household = Household(env, api)
    pat = household.owner
    pat.post("/api/care/settings", {"timezone": "UTC"})
    pills = pat.post(
        "/api/care/medicines", {"name": "Metformin", "times": "08:00"}, expect=201
    )["medicine"]
    assert (
        pat.ok("DELETE", f"/api/care/medicines/{pills['id']}")["outcome"] == "deleted"
    )
    client = pat.post(
        "/api/care/work/clients",
        {"name": "Mr Lee", "email": "lee.family@example.com", "rate_cents": 2000},
        expect=201,
    )["client"]
    tomorrow = (kit.utcnow().date() + timedelta(days=1)).isoformat()
    assert (
        pat.call(
            "POST",
            "/api/care/work/visits",
            {"client_id": client["id"], "day": tomorrow, "minutes": 60},
        )[0]
        == 400
    )
    pat.post(
        "/api/care/work/visits", {"client_id": client["id"], "minutes": 60}, expect=201
    )
    statement = pat.post(
        "/api/care/work/statements", {"client_id": client["id"]}, expect=201
    )["statement"]
    first = pat.post(f"/api/care/work/statements/{statement['id']}/send")
    again = pat.post(f"/api/care/work/statements/{statement['id']}/send")
    assert first["already"] is False and again["already"] is True
    assert first["statement"]["status"] == "open"
    queued = [
        m
        for m in api.get("/api/suite/outbox")["messages"]
        if m["kind"] == "care_statement" and m["ref_id"] == statement["id"]
    ]
    assert len(queued) == 1 and "1 visit, 1 hour, $20.00" in queued[0]["body"]
    token = first["statement"]["link"]["token"]
    assert (
        anonymous(env, "POST", f"/api/public/{token}/paid", {"note": "UPI ref 12345"})[
            0
        ]
        == 200
    )
    shown = pat.get("/api/care/work")["statements"][0]
    assert shown["client_paid_note"] == "UPI ref 12345"
    assert any(
        "UPI ref 12345" in a["body"]
        for a in pat.get("/api/life/pulse")["alerts"]
        if a["id"].startswith("carepaid:")
    )
    assert any(
        "UPI ref 12345" in line["sub"]
        for card in pat.get("/api/life/today")["cards"]
        if card["title"] == "Care work"
        for line in card["lines"]
    )
    # Paid, then the past client deleted for good.
    pat.post(f"/api/care/work/statements/{statement['id']}/paid", {"paid": True})
    assert (
        pat.ok("DELETE", f"/api/care/work/clients/{client['id']}")["outcome"]
        == "archived"
    )
    assert (
        pat.ok("DELETE", f"/api/care/work/clients/{client['id']}")["outcome"]
        == "deleted"
    )
    assert pat.get("/api/care/work")["clients"] == []
    assert anonymous(env, "GET", f"/api/public/{token}")[0] == 404
