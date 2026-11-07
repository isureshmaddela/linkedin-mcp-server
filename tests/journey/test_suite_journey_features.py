"""Life (aira/suite/journey), the features added after the first version: goals measured in a number or money
(savings, and earning goals that count what the account's apps earned), habits with streaks and a Today button,
a goal's public page where friends cheer, the timeline and vault exports, and the fixes found using the app."""

from __future__ import annotations

import base64
import json
import sqlite3
import urllib.error
import urllib.request
from datetime import date, timedelta

import pytest
from cloud_support import Browser, build_org, sign_in
from cryptography.fernet import Fernet
from life_support import Household

import auto_apply_assistant as local
import test_careers
from aira import suite
from aira.airaapi.store import SecretStore
from aira.suite import kit, life, public
from aira.suite.journey import journey_routes, service, store

env = test_careers.env
api = test_careers.api
stub = test_careers.stub

PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 64


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
    def __init__(self) -> None:
        self.user = {"email": "pat@home.example"}

    def header(self, name: str, default: str = "") -> str:
        return default


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


def _anon(env, method, path, body=None):
    data = json.dumps(body).encode() if body is not None else None
    request = urllib.request.Request(env.base + path, data=data, method=method)
    for name, value in {
        "Content-Type": "application/json",
        "X-Requested-With": "aira",
        "Origin": env.base,
    }.items():
        request.add_header(name, value)
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            return response.status, json.loads(response.read() or b"{}")
    except urllib.error.HTTPError as error:
        return error.code, json.loads(error.read() or b"{}")


# ---------------------------------------------------------------------------
# goals measured in a number or money
# ---------------------------------------------------------------------------


def test_a_number_goal_grows_with_amounts_and_finishes_only_at_its_target(db, people):
    pat = people["pat"]
    goal = service.create_goal(
        db,
        pat,
        {"title": "Run 100 km", "measure": "number", "unit": "km", "target": "100"},
    )
    assert goal["measure"] == "number" and goal["target_amount"] == 10000
    assert goal["progress"] == 0 and goal["unit"] == "km"
    goal = service.check_in(db, pat, goal["id"], {"amount": "12.5", "note": "Park"})
    assert goal["current_amount"] == 1250 and goal["progress"] == 12
    assert goal["checkins"][0]["amount"] == 1250
    goal = service.check_in(db, pat, goal["id"], {"amount": "87.4"})
    assert goal["progress"] == 99 and goal["status"] == "active"  # 99.9 km is not 100
    goal = service.check_in(db, pat, goal["id"], {"amount": "0.1"})
    assert goal["status"] == "completed" and goal["progress"] == 100
    assert [e["title"] for e in service.list_events(db, pat)] == [
        "Reached a goal: Run 100 km"
    ]
    # A correction below the target reopens it; the total never goes below zero.
    goal = service.check_in(db, pat, goal["id"], {"amount": "-500"})
    assert goal["status"] == "active" and goal["current_amount"] == 0
    goal = service.check_in(db, pat, goal["id"], {"value": "50"})
    assert goal["current_amount"] == 5000 and goal["progress"] == 50
    # The AI agent says "set it to 80%": a percentage of the target.
    goal = service.check_in(db, pat, goal["id"], {"progress": 80})
    assert goal["current_amount"] == 8000
    with pytest.raises(ValueError, match="how much"):
        service.check_in(db, pat, goal["id"], {"note": "nothing"})
    with pytest.raises(ValueError, match="no steps"):
        service.add_step(db, pat, goal["id"], {"title": "Stretch"})
    # A lower target the total already reaches finishes the goal.
    goal = service.update_goal(db, pat, goal["id"], {"target": "75"})
    assert goal["status"] == "completed" and goal["target_amount"] == 7500
    assert service.format_amount(goal, 4250) == "42.5 km"
    csv = service.goals_csv(db, pat).decode()
    assert "so_far,target" in csv.splitlines()[0] and "80 km,75 km" in csv


def test_a_money_goal_uses_the_account_currency_and_needs_a_target(db, people):
    pat = people["pat"]
    kit.save_business(db, {"currency": "INR"})
    goal = service.create_goal(
        db,
        pat,
        {
            "title": "Save for Goa",
            "category": "money",
            "measure": "money",
            "target": "50,000",
            "current": "5000",
        },
    )
    assert goal["currency"] == "INR" and goal["current_amount"] == 500000
    assert goal["progress"] == 10
    assert service.format_amount(goal, goal["target_amount"]) == "₹50,000.00"
    with pytest.raises(ValueError, match="target"):
        service.create_goal(db, pat, {"title": "No target", "measure": "money"})
    with pytest.raises(ValueError):
        service.create_goal(
            db, pat, {"title": "Zero", "measure": "money", "target": "0"}
        )
    with pytest.raises(ValueError, match="measured in money"):
        service.create_goal(
            db, pat, {"title": "Odd", "measure": "number", "source": "earnings"}
        )
    # A goal with steps cannot become a money goal (its progress follows the steps).
    stepped = service.create_goal(db, pat, {"title": "Move", "steps": ["Pack"]})
    with pytest.raises(ValueError, match="follows its steps"):
        service.update_goal(
            db, pat, stepped["id"], {"measure": "money", "target": "100"}
        )
    data = service.overview(db, pat)
    assert data["money_goals"] == [
        {
            "id": goal["id"],
            "title": "Save for Goa",
            "progress": 10,
            "source": "manual",
            "so_far": "₹5,000.00",
            "target": "₹50,000.00",
            "state": "on_track",
        }
    ]


def test_an_earning_goal_counts_what_the_apps_earned_since_it_started(db, people):
    pat = people["pat"]
    start = service.today(db).isoformat()
    kit.record_revenue(
        db,
        "studio",
        source_type="invoice",
        source_id="old",
        amount_cents=99900,
        at="2020-01-01T10:00:00Z",
    )
    goal = service.create_goal(
        db,
        pat,
        {
            "title": "Earn $1,000 from my side business",
            "category": "money",
            "measure": "money",
            "source": "earnings",
            "target": "1000",
            "currency": "USD",
        },
    )
    assert goal["source"] == "earnings" and goal["start_date"] == start
    assert goal["current_amount"] == 0  # what was earned before the goal does not count
    kit.record_revenue(
        db, "studio", source_type="invoice", source_id="a", amount_cents=40000
    )
    kit.record_revenue(
        db, "videos", source_type="payout", source_id="b", amount_cents=25000
    )
    kit.record_revenue(
        db,
        "shop",
        source_type="order",
        source_id="c",
        amount_cents=10000,
        currency_code="EUR",
    )
    kit.record_revenue(
        db, "shop", source_type="order", source_id="d", amount_cents=90000
    )
    kit.void_revenue(db, "shop", "order", "d")  # refunded: does not count
    db.commit()
    detail = service.goal_detail(db, pat, goal["id"])
    assert detail["current_amount"] == 65000 and detail["progress"] == 65
    assert [(a["name"], a["cents"]) for a in detail["earnings"]["by_app"]] == [
        ("Sales", 40000),
        ("Videos", 25000),
    ]
    with pytest.raises(ValueError, match="by itself"):
        service.check_in(db, pat, goal["id"], {"amount": "100"})
    goal = service.check_in(db, pat, goal["id"], {"note": "Two new clients"})
    assert goal["checkins"][0]["note"] == "Two new clients"
    kit.record_revenue(
        db, "academy", source_type="enrolment", source_id="e", amount_cents=35000
    )
    db.commit()
    reached = service.list_goals(db, pat)[0]
    assert reached["status"] == "completed" and reached["current_amount"] == 100000
    assert service.list_events(db, pat)[0]["title"].startswith("Reached a goal: Earn")
    # Children do not see the account's earnings, so they cannot make an earning goal.
    with pytest.raises(PermissionError):
        service.create_goal(
            db,
            people["mia"],
            {"title": "Earn", "measure": "money", "source": "earnings", "target": "5"},
        )
    assert service.may_count_earnings(people["mia"]) is False
    month = service.earnings(db, "USD", service.today(db).replace(day=1).isoformat())
    assert month["cents"] == 100000 and month["payments"] == 3


# ---------------------------------------------------------------------------
# habits
# ---------------------------------------------------------------------------


def test_streaks_count_days_in_a_row_and_weeks_that_met_their_target():
    now = date(2026, 10, 28)  # a Wednesday
    days = {now - timedelta(days=n) for n in (1, 2, 3, 6, 7)}
    # Today is not done yet: the streak runs to yesterday and is not broken.
    assert service.streaks(days, 7, now) == {"current": 3, "best": 3}
    assert service.streaks(days | {now}, 7, now) == {"current": 4, "best": 4}
    assert service.streaks(set(), 7, now) == {"current": 0, "best": 0}
    # Three times a week: last week had Thu, Fri and Sun? Count weeks with at least 3 days.
    week = date(2026, 10, 26)  # this Monday
    weekly = {week - timedelta(days=n) for n in (1, 3, 5)}  # last week: Sun, Fri, Wed
    weekly |= {
        week - timedelta(days=n) for n in (8, 9, 10)
    }  # the week before: Sun, Sat, Fri
    weekly |= {week - timedelta(days=n) for n in (15,)}  # three weeks ago: once only
    assert service.streaks(weekly, 3, now) == {"current": 2, "best": 2}
    weekly |= {week, week + timedelta(days=1), now}  # this week is met too
    assert service.streaks(weekly, 3, now)["current"] == 3


def test_habits_tick_once_a_day_feed_a_savings_goal_and_stay_private(db, people):
    pat, sam = people["pat"], people["sam"]
    kit.save_business(db, {"currency": "USD"})
    savings = service.create_goal(
        db,
        pat,
        {"title": "Emergency fund", "measure": "money", "target": "30"},
    )
    habit = service.save_habit(
        db,
        pat,
        {"title": "Put $5 aside", "goal_id": savings["id"], "amount": "5"},
    )
    assert habit["every_day"] and habit["amount_label"] == "$5.00"
    today = service.today(db)
    habit = service.mark_habit(db, pat, habit["id"], {})
    habit = service.mark_habit(db, pat, habit["id"], {})  # twice: still once
    assert habit["done_today"] and habit["streak"] == 1 and habit["total"] == 1
    # Started today and done today: 100% of the days it has existed, not 1 day in 30.
    assert habit["rate"] == 100 and habit["started_on"] == today.isoformat()
    assert service.get_goal(db, pat, savings["id"])["current_amount"] == 500
    yesterday = (today - timedelta(days=1)).isoformat()
    habit = service.mark_habit(db, pat, habit["id"], {"day": yesterday})
    assert habit["streak"] == 2 and habit["days"][-2]["done"] is True
    assert (
        habit["rate"] == 100
    )  # a day ticked before the habit was made counts from that day
    goal = service.get_goal(db, pat, savings["id"])
    assert goal["current_amount"] == 1000 and goal["checkins"][0]["note"].startswith(
        "Habit:"
    )
    # Undo takes the money back off the goal.
    habit = service.unmark_habit(db, pat, habit["id"], {"day": yesterday})
    assert habit["streak"] == 1
    assert service.get_goal(db, pat, savings["id"])["current_amount"] == 500
    with pytest.raises(ValueError, match="not later"):
        service.mark_habit(
            db, pat, habit["id"], {"day": (today + timedelta(days=1)).isoformat()}
        )
    with pytest.raises(ValueError, match="last 30 days"):
        service.mark_habit(
            db, pat, habit["id"], {"day": (today - timedelta(days=40)).isoformat()}
        )
    with pytest.raises(LookupError):
        service.mark_habit(db, sam, habit["id"], {})
    with pytest.raises(LookupError):
        service.save_habit(db, sam, {"title": "Spy", "goal_id": savings["id"]})
    assert service.list_habits(db, sam) == []
    # A percent goal cannot take an amount; a stopped habit cannot be ticked.
    plain = service.create_goal(db, pat, {"title": "Be kinder"})
    with pytest.raises(ValueError, match="number or money"):
        service.save_habit(
            db, pat, {"title": "Smile", "goal_id": plain["id"], "amount": "1"}
        )
    stopped = service.save_habit(db, pat, {"archived": True}, habit["id"])
    assert stopped["archived"] and service.list_habits(db, pat) == []
    with pytest.raises(ValueError, match="stopped"):
        service.mark_habit(db, pat, habit["id"], {})
    # Five ticks reach the $30 goal... once it is going again.
    service.save_habit(db, pat, {"archived": False}, habit["id"])
    for n in range(2, 7):
        service.mark_habit(
            db, pat, habit["id"], {"day": (today - timedelta(days=n)).isoformat()}
        )
    assert service.get_goal(db, pat, savings["id"])["status"] == "completed"
    # Deleting the goal unlinks the habit; deleting the habit keeps what was saved.
    service.delete_goal(db, pat, savings["id"])
    assert service.list_habits(db, pat)[0]["goal_id"] == ""
    service.delete_habit(db, pat, habit["id"])
    assert service.list_habits(db, pat) == []


def test_today_shows_habits_with_a_done_button_and_the_overview_counts_them(
    db, people, monkeypatch
):
    pat = people["pat"]
    wednesday = date(2026, 10, 28)
    monkeypatch.setattr(service, "today", lambda connection: wednesday)
    walk = service.save_habit(db, pat, {"title": "Walk 30 minutes"})
    gym = service.save_habit(db, pat, {"title": "Gym", "per_week": 3})
    cards = journey_routes._today(db, pat)
    habits = next(c for c in cards if c["title"] == "Habits today")
    assert [line["text"] for line in habits["lines"]] == ["Walk 30 minutes", "Gym"]
    assert (
        habits["lines"][0]["action"]["path"] == f"/api/journey/habits/{walk['id']}/done"
    )
    assert habits["lines"][1]["sub"] == "0 of 3 this week"
    service.mark_habit(db, pat, walk["id"], {})
    habits = next(
        c for c in journey_routes._today(db, pat) if c["title"] == "Habits today"
    )
    assert habits["lines"][0]["done"] is True and "action" not in habits["lines"][0]
    assert service.overview(db, pat)["habits"]["done_today"] == 1
    # A weekly habit whose week is done (Monday, Tuesday and last Sunday do not count) leaves Today.
    for day in ("2026-10-25", "2026-10-26", "2026-10-27"):
        service.mark_habit(db, pat, gym["id"], {"day": day})
    gym_view = service.list_habits(db, pat)[1]
    assert gym_view["this_week"] == 2 and gym_view["streak"] == 0
    lines = next(
        c for c in journey_routes._today(db, pat) if c["title"] == "Habits today"
    )["lines"]
    assert lines[1]["sub"] == "2 of 3 this week"
    service.mark_habit(db, pat, gym["id"], {"day": "2026-10-28"})
    lines = next(
        c for c in journey_routes._today(db, pat) if c["title"] == "Habits today"
    )["lines"]
    assert lines[1]["done"] is True  # done today: still shown, ticked
    monkeypatch.setattr(service, "today", lambda connection: date(2026, 10, 29))
    lines = next(
        c for c in journey_routes._today(db, pat) if c["title"] == "Habits today"
    )["lines"]
    assert [line["text"] for line in lines] == ["Walk 30 minutes"]
    assert service.list_habits(db, pat)[1]["streak"] == 1
    assert journey_routes._today(db, people["sam"]) == []


# ---------------------------------------------------------------------------
# a goal's public page
# ---------------------------------------------------------------------------


def test_a_goal_page_shows_progress_but_not_notes_or_money_and_takes_cheers(db, people):
    pat = people["pat"]
    run = service.create_goal(
        db,
        pat,
        {
            "title": "Run a 5K",
            "description": "For my health",
            "steps": ["Shoes", "Run 5K"],
        },
    )
    service.set_step(db, pat, run["steps"][0]["id"], {"done": True})
    service.check_in(db, pat, run["id"], {"note": "My knee hurts (private)"})
    page = service.public_goal(db, run["id"])
    assert (
        page["title"] == "Run a 5K" and page["name"] == "Pat" and page["progress"] == 50
    )
    assert page["steps"] == [
        {"title": "Shoes", "done": True},
        {"title": "Run 5K", "done": False},
    ]
    assert "knee" not in json.dumps(page)
    money = service.create_goal(
        db,
        pat,
        {"title": "Save", "measure": "money", "target": "1000", "current": "250"},
    )
    shown = service.public_goal(db, money["id"])
    assert shown["progress"] == 25 and shown["so_far"] == "" and shown["target"] == ""
    assert "250" not in json.dumps(shown)
    cheer = service.add_cheer(
        db, run["id"], {"name": "Grandma", "message": "So proud of you!"}, "1.2.3.4"
    )
    assert cheer["name"] == "Grandma"
    assert (
        service.add_cheer(db, run["id"], {"message": "Go!"}, "1.2.3.4")["name"]
        == "A friend"
    )
    with pytest.raises(ValueError):
        service.add_cheer(db, run["id"], {"name": "x"}, "1.2.3.4")
    for _ in range(3):
        service.add_cheer(db, run["id"], {"message": "Again"}, "1.2.3.4")
    with pytest.raises(ValueError, match="tomorrow"):
        service.add_cheer(db, run["id"], {"message": "Too many"}, "1.2.3.4")
    assert len(service.public_goal(db, run["id"])["cheers"]) == 5
    # The owner hears about it once: a pulse alert, then reading the goal marks the cheers read.
    pulse = journey_routes._pulse(db, pat, "")
    assert pulse["badge"] == 5 and pulse["alerts"][0]["id"].startswith("journey:cheer:")
    detail = service.goal_detail(db, pat, run["id"])
    assert len(detail["cheers"]) == 5 and detail["cheers_unread"] == 5
    assert service.goal_detail(db, pat, run["id"])["cheers_unread"] == 0
    assert journey_routes._pulse(db, pat, "")["badge"] == 0
    with pytest.raises(LookupError):
        service.delete_cheer(db, people["sam"], detail["cheers"][0]["id"])
    service.delete_cheer(db, pat, detail["cheers"][0]["id"])
    assert len(service.goal_detail(db, pat, run["id"])["cheers"]) == 4
    assert service.may_share(people["mia"]) is False and service.may_share(pat)


# ---------------------------------------------------------------------------
# fixes found using the app
# ---------------------------------------------------------------------------


def test_marking_a_goal_with_steps_done_ticks_them_and_rounding_never_finishes_early(
    db, people
):
    pat = people["pat"]
    goal = service.create_goal(
        db, pat, {"title": "Move house", "steps": ["Pack", "Van"]}
    )
    goal = service.update_goal(db, pat, goal["id"], {"status": "completed"})
    assert goal["steps_done"] == 2 and goal["progress"] == 100
    big = service.create_goal(
        db, pat, {"title": "Big", "steps": [f"Step {n}" for n in range(50)]}
    )
    for step in big["steps"][:49]:
        db.execute("UPDATE jr_steps SET done = 1 WHERE id = ?", (step["id"],))
    big = service.set_step(db, pat, big["steps"][0]["id"], {"done": True})
    assert big["progress"] == 98 and big["status"] == "active"


def test_the_document_list_never_reads_the_encrypted_scans(db, people, seal):
    pat = people["pat"]
    service.save_document(
        db,
        pat,
        {"title": "Passport", "file": {"name": "p.png", "data": _b64(PNG)}},
        seal,
    )
    statements: list[str] = []
    db.set_trace_callback(statements.append)
    try:
        listed = service.list_documents(db, pat)
        service.overview(db, pat)
    finally:
        db.set_trace_callback(None)
    assert listed[0]["file"]["size"] == len(PNG)
    reads = [s for s in statements if "FROM jr_documents" in s]
    assert reads and not [s for s in reads if "file_cipher" in s or "SELECT *" in s]


def test_the_vault_has_a_size_limit_and_a_replaced_scan_does_not_count_twice(
    db, people, seal, monkeypatch
):
    pat = people["pat"]
    monkeypatch.setattr(service, "MAX_VAULT_BYTES", len(PNG) * 2)
    first = service.save_document(
        db, pat, {"title": "A", "file": {"name": "a.png", "data": _b64(PNG)}}, seal
    )
    service.save_document(
        db, pat, {"title": "B", "file": {"name": "b.png", "data": _b64(PNG)}}, seal
    )
    with pytest.raises(ValueError, match="vault is full"):
        service.save_document(
            db, pat, {"title": "C", "file": {"name": "c.png", "data": _b64(PNG)}}, seal
        )
    replaced = service.save_document(
        db, pat, {"file": {"name": "a2.png", "data": _b64(PNG)}}, seal, first["id"]
    )
    assert replaced["file"]["name"] == "a2.png"
    # Clearing the number: an empty number removes it.
    numbered = service.save_document(db, pat, {"title": "D", "number": "X1"}, seal)
    cleared = service.save_document(db, pat, {"number": ""}, seal, numbered["id"])
    assert cleared["number"] == "" and cleared["number_hint"] == ""


def test_timeline_years_and_exports_leave_out_numbers(db, people, seal):
    pat, sam = people["pat"], people["sam"]
    service.save_event(
        db, pat, {"title": "Born", "event_date": "1990-05-01", "kind": "birth"}
    )
    service.save_event(
        db,
        pat,
        {"title": "=HYPERLINK(1)", "event_date": "2015-06-01", "location": "Pune"},
    )
    service.save_event(
        db, sam, {"title": "Sam's wedding", "event_date": "2018-02-01", "shared": True}
    )
    assert service.event_years(db, pat) == ["2018", "2015", "1990"]
    csv = service.events_csv(db, pat).decode().splitlines()
    assert csv[0] == "date,title,kind,location,shared,story"
    assert csv[1].startswith("1990-05-01,Born,birth") and "'=HYPERLINK(1)" in csv[2]
    assert len(csv) == 3  # only my own events
    service.save_document(
        db, pat, {"title": "Passport", "number": "Z9876543", "tags": "travel"}, seal
    )
    docs = service.documents_csv(db, pat).decode()
    assert "Passport" in docs and "Z9876543" not in docs and "6543" not in docs


# ---------------------------------------------------------------------------
# the single-user server and the hosted build
# ---------------------------------------------------------------------------


def test_habits_cheers_and_earning_goals_on_the_local_build(env, api):
    kit.forget_schema()
    public.set_index(local._public_link_index)
    home = Household(env, api)
    pat = home.owner
    mia = home.add("Mia", age_group="child", pin="2468")
    habit = pat.post("/api/journey/habits", {"title": "Read 20 pages"}, expect=201)[
        "habit"
    ]
    cards = pat.get("/api/life/today")["cards"]
    line = next(c for c in cards if c["title"] == "Habits today")["lines"][0]
    pat.post(line["action"]["path"], line["action"]["body"])
    pat.post(line["action"]["path"], line["action"]["body"])  # pressed again
    habits = pat.get("/api/journey/habits")
    assert habits["habits"][0]["done_today"] and habits["habits"][0]["total"] == 1
    assert mia.get("/api/journey/habits")["habits"] == []
    assert mia.call("POST", f"/api/journey/habits/{habit['id']}/done", {})[0] == 404

    goal = pat.post(
        "/api/journey/goals",
        {"title": "Learn to swim", "steps": ["Lessons"]},
        expect=201,
    )["goal"]
    link = pat.post(f"/api/journey/goals/{goal['id']}/link")["link"]
    status, page = _anon(env, "GET", f"/api/public/{link['token']}")
    assert status == 200 and page["app"] == "journey" and page["kind"] == "goal_page"
    assert page["page"]["title"] == "Learn to swim" and page["actions"] == [
        "cheer",
        "pledge",
    ]
    status, body = _anon(
        env,
        "POST",
        f"/api/public/{link['token']}/cheer",
        {"name": "Uncle Ravi", "message": "You can do it!"},
    )
    assert status == 201, body
    assert (
        _anon(env, "POST", f"/api/public/{link['token']}/cheer", {"message": ""})[0]
        == 400
    )
    assert pat.get("/api/life/pulse")["apps"]["journey"]["badge"] == 1
    assert mia.get("/api/life/pulse")["apps"]["journey"] == {"badge": 0}
    detail = pat.get(f"/api/journey/goals/{goal['id']}")["goal"]
    assert (
        detail["link"]["token"] == link["token"]
        and detail["cheers"][0]["name"] == "Uncle Ravi"
    )
    pat.ok("DELETE", f"/api/journey/goals/{goal['id']}/link")
    assert _anon(env, "GET", f"/api/public/{link['token']}")[0] == 404
    # A child keeps goals but does not make public pages, or earning goals.
    kid_goal = mia.post("/api/journey/goals", {"title": "Ride a bike"}, expect=201)[
        "goal"
    ]
    assert mia.call("POST", f"/api/journey/goals/{kid_goal['id']}/link", {})[0] == 403
    assert mia.get("/api/journey/earnings") == {"allowed": False}
    status, _ = mia.call(
        "POST",
        "/api/journey/goals",
        {"title": "Earn", "measure": "money", "source": "earnings", "target": "10"},
    )
    assert status == 403
    earning = pat.get("/api/journey/earnings")
    assert earning["allowed"] is True and earning["this_month"]["cents"] == 0
    made = pat.post(
        "/api/journey/goals",
        {
            "title": "Earn 500",
            "measure": "money",
            "source": "earnings",
            "target": "500",
        },
        expect=201,
    )["goal"]
    assert made["source"] == "earnings"
    status, csv = pat.call("GET", "/api/journey/export/events", raw=True)
    assert status == 200 and csv.startswith(b"date,title")
    status, csv = pat.call("GET", "/api/journey/export/documents", raw=True)
    assert status == 200 and csv.startswith(b"name,kind")
    assert "years" in pat.get("/api/journey/events")


def test_hosted_habits_goal_pages_and_earning_goals(cloud):
    kit.forget_schema()
    org = build_org(cloud)
    owner = sign_in(cloud, org.owner)
    assert owner.call("GET", "/api/journey/habits")[0] == 402
    cloud.billing.subscribe_app(org.owner, "journey")
    status, made = owner.call("POST", "/api/journey/habits", {"title": "Stretch"})
    assert status == 201, made
    assert (
        owner.call("POST", f"/api/journey/habits/{made['habit']['id']}/done", {})[0]
        == 200
    )
    candidate = sign_in(cloud, org.candidate)
    assert candidate.call("GET", "/api/journey/habits")[1]["habits"] == []
    assert (
        candidate.call("POST", f"/api/journey/habits/{made['habit']['id']}/done", {})[0]
        == 404
    )
    # Earnings are the organization's: the owner may count them, a candidate may not.
    assert owner.call("GET", "/api/journey/earnings")[1]["allowed"] is True
    assert candidate.call("GET", "/api/journey/earnings")[1] == {"allowed": False}
    goal = {"title": "Earn", "measure": "money", "source": "earnings", "target": "100"}
    assert owner.call("POST", "/api/journey/goals", goal)[0] == 201
    assert candidate.call("POST", "/api/journey/goals", goal)[0] == 403
    # A goal page works for an anonymous visitor once the organization has the app.
    status, created = candidate.call(
        "POST", "/api/journey/goals", {"title": "Get a job"}
    )
    assert status == 201
    status, link = candidate.call(
        "POST", f"/api/journey/goals/{created['goal']['id']}/link", {}
    )
    assert status == 200, link
    visitor = Browser(cloud)
    token = link["link"]["token"]
    status, page = visitor.call("GET", f"/api/public/{token}")
    assert status == 200 and page["page"]["title"] == "Get a job"
    status, cheer = visitor.call(
        "POST", f"/api/public/{token}/cheer", {"name": "Coach", "message": "Keep going"}
    )
    assert status == 201, cheer
    assert (
        owner.call("POST", f"/api/journey/goals/{created['goal']['id']}/link", {})[0]
        == 404
    )
    other = build_org(cloud, "Other Org", slug_email="other.test")
    cloud.billing.subscribe_app(other.owner, "journey")
    assert (
        sign_in(cloud, other.owner).call("GET", "/api/journey/habits")[1]["habits"]
        == []
    )
