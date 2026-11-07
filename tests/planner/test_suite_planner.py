"""Planner (aira/suite/planner): chores and repeats, points and rewards, shopping lists, budget, bills and notes,
on its own, through the single-user server for a household, and in the hosted build."""

from __future__ import annotations

import json
import sqlite3
import urllib.error
import urllib.request
from datetime import date, timedelta

import pytest
from cloud_support import Browser, build_org, sign_in
from life_support import Household

import auto_apply_assistant as local
import test_careers
from aira import suite
from aira.suite import kit, life, public
from aira.suite.planner import service, store

env = test_careers.env
api = test_careers.api
stub = test_careers.stub


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
    connection = sqlite3.connect(tmp_path / "planner.sqlite3")
    connection.row_factory = sqlite3.Row
    store.ensure(connection)
    yield connection
    connection.close()


@pytest.fixture
def family(db):
    parent = life.current(Req(), db)
    kid = life.add(db, parent, {"name": "Mia", "age_group": "child"})
    teen = life.add(db, parent, {"name": "Leo", "age_group": "teen"})
    return {"parent": parent, "kid": kid, "teen": teen}


# ---------------------------------------------------------------------------
# chores, repeats and points
# ---------------------------------------------------------------------------


def test_repeats_cover_weekdays_weeks_and_month_ends():
    fri = date(2026, 10, 23)  # a Friday
    assert service.next_due({"repeat": "daily"}, fri) == date(2026, 10, 24)
    assert service.next_due({"repeat": "weekdays"}, fri) == date(2026, 10, 26)
    assert service.next_due({"repeat": "weekly", "repeat_days": "0,3"}, fri) == date(
        2026, 10, 26
    )
    assert service.next_due(
        {"repeat": "weekly", "repeat_days": "0,3"}, date(2026, 10, 26)
    ) == date(2026, 10, 29)
    assert service.next_due({"repeat": "weekly", "repeat_days": ""}, fri) == date(
        2026, 10, 30
    )
    assert service.next_due(
        {"repeat": "monthly", "repeat_day": 31}, date(2026, 1, 31)
    ) == date(2026, 2, 28)
    assert service.next_due(
        {"repeat": "monthly", "repeat_day": 31}, date(2026, 2, 28)
    ) == date(2026, 3, 31)
    assert service.next_due(
        {"repeat": "monthly", "repeat_day": 15}, date(2026, 12, 15)
    ) == date(2027, 1, 15)
    assert service.next_due({"repeat": "every_n", "repeat_every": 3}, fri) == date(
        2026, 10, 26
    )
    assert service.next_due({"repeat": "none"}, fri) is None
    assert (
        service.repeat_label({"repeat": "weekly", "repeat_days": "0,3"})
        == "Every Mon, Thu"
    )


def test_completing_is_idempotent_awards_points_and_schedules_the_next(db, family):
    parent, kid = family["parent"], family["kid"]
    on = service.today(db).isoformat()
    chore = service.create_task(
        db,
        parent,
        {
            "title": "Feed the cat",
            "assignee": kid["id"],
            "due_date": on,
            "repeat": "daily",
            "points": 10,
        },
    )
    assert chore["repeat_label"] == "Every day"
    done = service.complete(db, kid, chore["id"])
    assert done["done"] and done["done_by"] == ["Mia"]
    service.complete(db, kid, chore["id"])  # pressed twice
    assert service.balance(db, kid["id"]) == 10
    upcoming = [
        t
        for t in service.list_tasks(db, kid, status="open")
        if t["title"] == "Feed the cat"
    ]
    assert (
        len(upcoming) == 1
        and upcoming[0]["due_date"]
        == (date.fromisoformat(on) + timedelta(days=1)).isoformat()
    )
    # undoing takes the points back; doing it again does not make a second "next" task
    service.undo(db, kid, chore["id"])
    assert service.balance(db, kid["id"]) == 0
    service.complete(db, kid, chore["id"])
    assert (
        len(
            [
                t
                for t in service.list_tasks(db, parent, scope="family", status="open")
                if t["title"] == "Feed the cat"
            ]
        )
        == 1
    )
    assert service.balance(db, kid["id"]) == 10


def test_children_see_their_own_chores_and_cannot_award_points(db, family):
    parent, kid, teen = family["parent"], family["kid"], family["teen"]
    service.create_task(db, parent, {"title": "Leo's homework", "assignee": teen["id"]})
    service.create_task(db, parent, {"title": "Tidy up", "assignee": ""})
    mine = service.create_task(db, kid, {"title": "Read a book", "assignee": kid["id"]})
    titles = {t["title"] for t in service.list_tasks(db, kid, scope="family")}
    assert titles == {"Tidy up", "Read a book"}
    with pytest.raises(PermissionError):
        service.create_task(
            db, kid, {"title": "Leo does dishes", "assignee": teen["id"]}
        )
    with pytest.raises(PermissionError):
        service.create_task(
            db, kid, {"title": "Candy", "assignee": kid["id"], "points": 500}
        )
    with pytest.raises(PermissionError):
        service.update_task(db, kid, mine["id"], {"points": 50})
    leo_task = next(
        t
        for t in service.list_tasks(db, parent, scope="family")
        if t["title"] == "Leo's homework"
    )
    with pytest.raises(LookupError):
        service.complete(db, kid, leo_task["id"])
    with pytest.raises(ValueError):
        service.create_task(db, parent, {"title": "Walk", "repeat": "weekly"})
    with pytest.raises(ValueError):
        service.create_task(db, parent, {"title": "Walk", "due_time": "7pm"})


def test_everyone_tasks_are_done_by_each_person(db, family):
    parent, kid, teen = family["parent"], family["kid"], family["teen"]
    task = service.create_task(
        db,
        parent,
        {
            "title": "Brush teeth",
            "assignee": "everyone",
            "due_date": service.today(db).isoformat(),
            "points": 2,
        },
    )
    service.complete(db, kid, task["id"])
    assert any(t["title"] == "Brush teeth" for t in service.due_today(db, teen))
    assert not any(t["title"] == "Brush teeth" for t in service.due_today(db, kid))
    service.complete(db, teen, task["id"])
    service.complete(db, parent, task["id"])
    assert store.one(db, "pn_tasks", task["id"], "task")["status"] == "done"
    assert service.balance(db, kid["id"]) == 2 and service.balance(db, teen["id"]) == 2


def test_rewards_need_points_and_an_adult(db, family):
    parent, kid = family["parent"], family["kid"]
    with pytest.raises(PermissionError):
        service.save_reward(db, kid, {"title": "Ice cream", "points": 5})
    reward = service.save_reward(
        db, parent, {"title": "30 minutes extra screen time", "points": 20}
    )
    with pytest.raises(ValueError, match="You need 20 points"):
        service.request_reward(db, kid, reward["id"])
    chore = service.create_task(
        db, parent, {"title": "Wash car", "assignee": kid["id"], "points": 25}
    )
    service.complete(db, kid, chore["id"])
    request = service.request_reward(db, kid, reward["id"])
    with pytest.raises(ValueError):  # 25 - 20 pending = 5 left
        service.request_reward(db, kid, reward["id"])
    with pytest.raises(PermissionError):
        service.decide_reward(db, kid, request["id"], True)
    assert (
        service.decide_reward(db, parent, request["id"], True)["status"] == "approved"
    )
    assert service.balance(db, kid["id"]) == 5
    assert (
        service.decide_reward(db, parent, request["id"], True)["status"] == "approved"
    )  # twice: same
    assert service.balance(db, kid["id"]) == 5
    view = service.rewards_view(db, kid)
    assert (
        view["balance"] == 5
        and len(view["balances"]) == 1
        and view["requests"][0]["status"] == "approved"
    )
    assert len(service.rewards_view(db, parent)["balances"]) == 3


# ---------------------------------------------------------------------------
# shopping
# ---------------------------------------------------------------------------


def test_items_find_their_aisle_and_usual_items_come_back(db, family):
    assert service.aisle("Semi-skimmed milk") == "dairy"
    assert service.aisle("Bananas") == "fruit & veg"
    assert service.aisle("Paracetamol 500mg") == "pharmacy"
    assert service.aisle("Washing up liquid") == "other"
    assert service.aisle("Dish soap") == "household"
    groceries = service.lists(db)[0]
    for name in ("Milk", "Bread", "Milk"):
        service.add_item(db, family["parent"]["id"], groceries["id"], {"name": name})
    usual = service.usual(db)
    assert usual[0]["name"] == "Milk" and usual[0]["times"] == 2
    assert (
        service.usual(db, groceries["id"]) == []
    )  # both are waiting on the list already
    detail = service.list_detail(db, groceries["id"])
    assert [i["category"] for i in detail["items"]][:1] == ["bakery"]


def test_since_returns_ticks_and_removals_in_order(db, family):
    groceries = service.lists(db)[0]
    milk = service.add_item(
        db, family["parent"]["id"], groceries["id"], {"name": "Milk", "quantity": "2"}
    )
    eggs = service.add_item(
        db, family["parent"]["id"], groceries["id"], {"name": "Eggs"}
    )
    mark = service.list_detail(db, groceries["id"])["now"]
    service.update_item(db, family["kid"]["id"], milk["id"], {"checked": True})
    service.delete_item(db, eggs["id"])
    changed = service.list_detail(db, groceries["id"], since=mark)["changed"]
    by_id = {c["id"]: c for c in changed}
    assert by_id[milk["id"]]["checked"] and by_id[milk["id"]]["checked_by"] == "Mia"
    assert by_id[eggs["id"]]["deleted"] is True
    assert service.clear_checked(db, groceries["id"]) == 1
    assert service.list_detail(db, groceries["id"])["items"] == []


# ---------------------------------------------------------------------------
# budget and bills
# ---------------------------------------------------------------------------


def test_budget_adds_up_and_is_for_adults(db, family):
    parent, kid = family["parent"], family["kid"]
    with pytest.raises(PermissionError):
        service.budget(db, kid)
    cats = {c["name"]: c for c in service.categories(db)}
    service.save_category(
        db,
        parent,
        {"name": "Groceries", "monthly_cents": 40000},
        cats["Groceries"]["id"],
    )
    month = service.today(db).strftime("%Y-%m")
    service.save_entry(
        db, parent, {"kind": "income", "amount_cents": 300000, "category": "Salary"}
    )
    service.save_entry(
        db, parent, {"kind": "expense", "amount_cents": 25000, "category": "Groceries"}
    )
    service.save_entry(
        db, parent, {"kind": "expense", "amount_cents": 20000, "category": "Groceries"}
    )
    service.save_entry(
        db, parent, {"kind": "expense", "amount_cents": 5000, "category": "Pets"}
    )
    data = service.budget(db, parent, month)
    groceries = next(c for c in data["categories"] if c["name"] == "Groceries")
    assert (
        groceries["spent_cents"] == 45000
        and groceries["over"]
        and groceries["left_cents"] == -5000
        and groceries["percent"] == 112
    )
    assert any(
        c["name"] == "Pets" and c["spent_cents"] == 5000 for c in data["categories"]
    )
    assert (
        data["income_cents"] == 300000
        and data["expense_cents"] == 50000
        and data["net_cents"] == 250000
    )
    assert b"Groceries" in service.budget_csv(db, parent, month)
    with pytest.raises(ValueError):
        service.save_entry(db, parent, {"kind": "expense", "amount_cents": 0})
    with pytest.raises(ValueError):
        service.budget(db, parent, "2026-13")


def test_paying_a_bill_makes_one_spending_entry(db, family):
    parent, kid = family["parent"], family["kid"]
    with pytest.raises(PermissionError):
        service.bills(db, kid)
    on = service.today(db)
    bill = service.save_bill(
        db, parent, {"name": "Electricity", "amount_cents": 8500, "due_day": on.day}
    )
    assert bill["status"] == "due" and bill["days_until"] == 0
    paid = service.pay_bill(db, parent, bill["id"], {})
    service.pay_bill(db, parent, bill["id"], {"period": bill["next_period"]})  # again
    entries = kit.fetch_all(
        db, "SELECT * FROM pn_entries WHERE bill_id = ?", (bill["id"],)
    )
    assert len(entries) == 1 and entries[0]["amount_cents"] == 8500
    assert paid["next_period"] != bill["next_period"] and paid["status"] == "upcoming"
    service.unpay_bill(db, parent, bill["id"], bill["next_period"])
    assert (
        kit.count(
            db, "SELECT COUNT(*) FROM pn_entries WHERE bill_id = ?", (bill["id"],)
        )
        == 0
    )
    once = service.save_bill(
        db,
        parent,
        {
            "name": "Car tax",
            "amount_cents": 15000,
            "repeat": "once",
            "due_date": (on + timedelta(days=2)).isoformat(),
        },
    )
    assert once["days_until"] == 2
    assert service.pay_bill(db, parent, once["id"], {})["status"] == "settled"
    with pytest.raises(ValueError):
        service.save_bill(db, parent, {"name": "Insurance", "repeat": "yearly"})


def test_month_end_bills_and_an_unpaid_bill_from_last_month():
    bill = {
        "id": "b",
        "repeat": "monthly",
        "due_day": 31,
        "due_date": "",
        "created_at": "2026-01-01T00:00:00Z",
    }
    assert service._bill_due(bill, "2026-02") == date(2026, 2, 28)
    assert service._bill_due(bill, "2026-04") == date(2026, 4, 30)


# ---------------------------------------------------------------------------
# notes
# ---------------------------------------------------------------------------


def test_private_notes_stay_private(db, family):
    parent, kid = family["parent"], family["kid"]
    secret = service.save_note(
        db, parent, {"title": "Gift ideas for Mia", "body": "Bike", "shared": False}
    )
    shared = service.save_note(
        db,
        kid,
        {
            "title": "Wifi password",
            "body": "on the router",
            "pinned": True,
            "checklist": ["Restart router", {"text": "Call ISP", "done": True}],
        },
    )
    assert [n["title"] for n in service.notes(db, kid)] == ["Wifi password"]
    assert {n["title"] for n in service.notes(db, parent)} == {
        "Gift ideas for Mia",
        "Wifi password",
    }
    with pytest.raises(LookupError):
        service.save_note(db, kid, {"body": "peek"}, secret["id"])
    with pytest.raises(PermissionError):
        service.save_note(db, parent, {"shared": False}, shared["id"])
    assert shared["checklist"] == [
        {"text": "Restart router", "done": False},
        {"text": "Call ISP", "done": True},
    ]
    with pytest.raises(ValueError):
        service.save_note(db, kid, {"title": "", "body": ""})


# ---------------------------------------------------------------------------
# the single-user server: a household
# ---------------------------------------------------------------------------


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


def test_a_household_runs_the_home_on_the_local_build(env, api):
    kit.forget_schema()
    public.set_index(local._public_link_index)
    home = Household(env, api)
    parent = home.owner
    mia = home.add("Mia", age_group="child", pin="2468")
    today = parent.get("/api/planner/overview")["today"]
    chore = parent.post(
        "/api/planner/tasks",
        {
            "title": "Tidy your room",
            "assignee": mia.id,
            "due_date": today,
            "points": 30,
        },
        expect=201,
    )["task"]
    reward = parent.post(
        "/api/planner/rewards", {"title": "Movie night pick", "points": 25}, expect=201
    )["reward"]

    # Mia sees the chore on Today, finishes it from there, and asks for the reward.
    day = mia.get("/api/life/today")
    card = next(c for c in day["cards"] if c["title"] == "Your chores today")
    action = card["lines"][0]["action"]
    assert (
        card["lines"][0]["text"] == "Tidy your room"
        and action["path"] == f"/api/planner/tasks/{chore['id']}/done"
    )
    assert mia.get("/api/life/pulse")["apps"]["planner"]["badge"] == 1
    mia.post(action["path"])
    mia.post(action["path"])  # tapped twice
    assert mia.get("/api/planner/rewards")["balance"] == 30
    request = mia.post(f"/api/planner/rewards/{reward['id']}/request", expect=201)[
        "request"
    ]
    assert (
        mia.call("POST", f"/api/planner/requests/{request['id']}/approve", {})[0] == 403
    )
    parent.post(f"/api/planner/requests/{request['id']}/approve")
    assert mia.get("/api/planner/rewards")["balance"] == 5
    # Money is for adults.
    assert mia.call("GET", "/api/planner/budget")[0] == 403
    assert mia.call("GET", "/api/planner/bills")[0] == 403
    assert "bills_soon" in parent.get("/api/planner/overview")

    # Two people tick the same list; each sees the other's ticks with ``since``.
    groceries = parent.get("/api/planner/lists")["lists"][0]
    path = f"/api/planner/lists/{groceries['id']}"
    milk = parent.post(f"{path}/items", {"name": "Milk", "quantity": "2"}, expect=201)[
        "item"
    ]
    mark = mia.get(path)["now"]
    parent.ok("PATCH", f"/api/planner/items/{milk['id']}", {"checked": True})
    changed = mia.get(f"{path}?since={mark}")["changed"]
    assert (
        changed[0]["id"] == milk["id"]
        and changed[0]["checked"]
        and changed[0]["checked_by"] == parent.name
    )

    # A bill due today shows on the parent's Today with a Paid button, and its pulse alert.
    bill = parent.post(
        "/api/planner/bills",
        {"name": "Water", "amount_cents": 4200, "due_day": int(today[8:10])},
        expect=201,
    )["bill"]
    cards = parent.get("/api/life/today")["cards"]
    bills = next(c for c in cards if c["title"] == "Bills due soon")
    assert (
        bills["lines"][0]["action"]["path"] == f"/api/planner/bills/{bill['id']}/paid"
    )
    alerts = parent.get("/api/life/pulse")["alerts"]
    assert any(
        a["id"] == f"planner:bill:{bill['id']}:{bill['next_period']}" for a in alerts
    )
    assert not any(
        c["title"] == "Bills due soon" for c in mia.get("/api/life/today")["cards"]
    )

    # A relative ticks and adds through the list's link; a child cannot share it.
    assert mia.call("POST", f"{path}/link", {})[0] == 403
    link = parent.post(f"{path}/link")["link"]
    eggs = parent.post(f"{path}/items", {"name": "Eggs"}, expect=201)["item"]
    status, page = _anon(env, "GET", f"/api/public/{link['token']}")
    assert status == 200 and {i["name"] for i in page["page"]["items"]} >= {
        "Milk",
        "Eggs",
    }
    assert "checked_by" not in page["page"]["items"][0]
    status, _ = _anon(
        env,
        "POST",
        f"/api/public/{link['token']}/tick",
        {"item_id": eggs["id"], "checked": True, "by": "Grandma"},
    )
    assert status == 200
    status, added = _anon(
        env,
        "POST",
        f"/api/public/{link['token']}/add",
        {"name": "Biscuits", "by": "Grandma"},
    )
    assert status == 201
    items = {i["name"]: i for i in parent.get(path)["items"]}
    assert (
        items["Eggs"]["checked_by"] == "Grandma"
        and items["Biscuits"]["added_by"] == "Grandma"
    )
    # A tick for an item on another list is refused.
    other = parent.post("/api/planner/lists", {"name": "Pharmacy"}, expect=201)["list"]
    pills = parent.post(
        f"/api/planner/lists/{other['id']}/items", {"name": "Plasters"}, expect=201
    )["item"]
    assert (
        _anon(
            env, "POST", f"/api/public/{link['token']}/tick", {"item_id": pills["id"]}
        )[0]
        == 404
    )
    parent.ok("DELETE", f"{path}/link")
    assert _anon(env, "GET", f"/api/public/{link['token']}")[0] == 404


# ---------------------------------------------------------------------------
# the hosted build
# ---------------------------------------------------------------------------


def test_hosted_planner_needs_the_app_and_keeps_notes_private(cloud):
    kit.forget_schema()
    org = build_org(cloud)
    owner = sign_in(cloud, org.owner)
    status, body = owner.call("GET", "/api/planner/overview")
    assert status == 402 and body["code"] == "app_required"
    cloud.billing.subscribe_app(org.owner, "planner")
    for who in ("owner", "admin", "coach", "candidate", "billing", "auditor"):
        browser = sign_in(cloud, getattr(org, who))
        assert browser.call("GET", "/api/planner/overview")[0] == 200, who
        status, note = browser.call(
            "POST", "/api/planner/notes", {"title": f"{who} private", "shared": False}
        )
        assert status == 201, (who, note)
    candidate = sign_in(cloud, org.candidate)
    status, listed = candidate.call("GET", "/api/planner/notes")
    assert [n["title"] for n in listed["notes"]] == ["candidate private"]
    status, shared = owner.call(
        "POST", "/api/planner/notes", {"title": "Office wifi", "shared": True}
    )
    assert {
        n["title"] for n in candidate.call("GET", "/api/planner/notes")[1]["notes"]
    } == {"candidate private", "Office wifi"}
    # Members are adults by default: budgets work for everyone, separately from other organizations.
    assert (
        candidate.call(
            "POST", "/api/planner/entries", {"amount_cents": 1200, "category": "Lunch"}
        )[0]
        == 201
    )
    other = build_org(cloud, "Other Org", slug_email="other.test")
    cloud.billing.subscribe_app(other.owner, "planner")
    status, notes = sign_in(cloud, other.owner).call("GET", "/api/planner/notes")
    assert status == 200 and notes["notes"] == []
    # The shopping list link works for a visitor once the organization has the app.
    lists = owner.call("GET", "/api/planner/lists")[1]["lists"]
    link = owner.call("POST", f"/api/planner/lists/{lists[0]['id']}/link", {})[1][
        "link"
    ]
    visitor = Browser(cloud)
    assert visitor.call("GET", f"/api/public/{link['token']}")[0] == 200
    assert (
        visitor.call(
            "POST", f"/api/public/{link['token']}/add", {"name": "Coffee", "by": "Sam"}
        )[0]
        == 201
    )
