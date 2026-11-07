"""Planner: regressions found by using it for real, and the features that make it a home's whole planner:
meals with recipes that fill the shopping list, selling what the household no longer needs (money in), and
pocket money for chore points (money out). On their own, through the single-user server, and hosted."""

# ruff: noqa: F811
from __future__ import annotations

import sqlite3
from datetime import date, timedelta

import pytest
from cloud_support import Browser, build_org, sign_in
from life_support import Household

import auto_apply_assistant as local
from aira.suite import kit, life, public
from aira.suite.planner import meals, planner_routes, selling, service, store
from test_careers import api, env, stub  # noqa: F401
from test_suite_planner import Req, _anon, _no_model, db, family  # noqa: F401


# ---------------------------------------------------------------------------
# regressions
# ---------------------------------------------------------------------------


def test_since_sees_a_change_made_in_the_same_second(db, family, monkeypatch):
    """The list said "nothing changed" when someone ticked an item in the same second the page last asked."""
    groceries = service.lists(db)[0]
    monkeypatch.setattr(kit, "now", lambda: "2026-10-27T10:00:00Z")
    milk = service.add_item(
        db, family["parent"]["id"], groceries["id"], {"name": "Milk"}
    )
    monkeypatch.setattr(kit, "now", lambda: "2026-10-27T10:00:05Z")
    mark = service.list_detail(db, groceries["id"])["now"]
    service.update_item(db, family["kid"]["id"], milk["id"], {"checked": True})
    changed = service.list_detail(db, groceries["id"], since=mark)["changed"]
    assert [c["id"] for c in changed] == [milk["id"]] and changed[0]["checked"]


def test_a_child_cannot_undo_a_chore_someone_else_did(db, family):
    """Un-ticking an "anyone" chore a sibling did took the sibling's points away."""
    parent, kid, teen = family["parent"], family["kid"], family["teen"]
    chore = service.create_task(
        db, parent, {"title": "Empty the dishwasher", "assignee": "", "points": 10}
    )
    service.complete(db, teen, chore["id"])
    with pytest.raises(PermissionError):
        service.undo(db, kid, chore["id"])
    assert service.balance(db, teen["id"]) == 10
    service.undo(db, teen, chore["id"])  # the person who did it can
    assert service.balance(db, teen["id"]) == 0
    service.complete(db, teen, chore["id"])
    service.undo(db, parent, chore["id"])  # and so can an adult
    assert service.balance(db, teen["id"]) == 0


def test_moving_a_repeating_task_onto_its_next_day_is_a_clear_error(db, family):
    """Editing a done daily chore onto tomorrow (where its next one already is) was a server error."""
    parent = family["parent"]
    on = service.today(db)
    chore = service.create_task(
        db,
        parent,
        {
            "title": "Feed the fish",
            "assignee": "",
            "due_date": on.isoformat(),
            "repeat": "daily",
        },
    )
    service.complete(db, parent, chore["id"])
    tomorrow = (on + timedelta(days=1)).isoformat()
    with pytest.raises(ValueError, match="already on that day"):
        service.update_task(db, parent, chore["id"], {"due_date": tomorrow})
    moved = service.update_task(
        db, parent, chore["id"], {"due_date": (on - timedelta(days=1)).isoformat()}
    )
    assert moved["due_date"] < on.isoformat()


def test_a_refused_request_closes_its_database_connection(tmp_path):
    """With two people and no profile chosen, every Planner call answered 403 and left its connection open."""
    closed: list[bool] = []

    class Tracked(sqlite3.Connection):
        def close(self) -> None:
            closed.append(True)
            super().close()

    path = tmp_path / "home.sqlite3"
    kit.forget_schema()
    first = sqlite3.connect(path)
    first.row_factory = sqlite3.Row
    store.ensure(first)
    parent = life.current(Req(), first)
    life.add(first, parent, {"name": "Mia", "age_group": "child"})
    first.close()

    class Request(Req):
        def connect(self):
            connection = sqlite3.connect(path, factory=Tracked)
            connection.row_factory = sqlite3.Row
            return connection

    with pytest.raises(life.ProfileRequired):
        planner_routes._open(Request())
    assert closed == [True]


def test_the_public_list_polls_with_a_query_that_is_not_counted_as_an_action(
    db, family
):
    groceries = service.lists(db)[0]
    link = public.create_link(
        db, "planner", "shopping_list", groceries["id"], title="Groceries"
    )
    mark = public.view(db, link["token"])["page"]["now"]
    service.add_item(db, family["parent"]["id"], groceries["id"], {"name": "Bread"})
    status, data = public.ask(db, link["token"], "changes", {"since": mark})
    assert status == 200 and [i["name"] for i in data["changed"]] == ["Bread"]
    assert "added_by" not in data["changed"][0]
    assert (
        public.link_for(db, "planner", "shopping_list", groceries["id"])["actions"] == 0
    )


def test_budget_csv_has_signed_amounts_a_spreadsheet_adds_up(db, family):
    parent = family["parent"]
    service.save_entry(
        db, parent, {"kind": "income", "amount_cents": 250000, "category": "Salary"}
    )
    service.save_entry(
        db,
        parent,
        {
            "kind": "expense",
            "amount_cents": 1250,
            "category": "Groceries",
            "note": "=SUM(A1)",
        },
    )
    text = service.budget_csv(db, parent).decode()
    lines = text.strip().splitlines()
    assert lines[0] == "day,type,category,amount,currency,note,who"
    assert any(",Money in,Salary,2500.00,USD," in line for line in lines)
    assert any(",Money spent,Groceries,-12.50,USD,'=SUM(A1)," in line for line in lines)


def test_chores_search_person_filter_and_csv(db, family):
    parent, kid, teen = family["parent"], family["kid"], family["teen"]
    service.create_task(
        db, parent, {"title": "Hoover the stairs", "assignee": kid["id"]}
    )
    service.create_task(
        db,
        parent,
        {"title": "Mow the lawn", "assignee": teen["id"], "notes": "Front garden"},
    )
    service.create_task(db, parent, {"title": "Water plants", "assignee": ""})

    def titles(**kw):
        return {
            t["title"] for t in service.list_tasks(db, parent, scope="family", **kw)
        }

    assert titles(q="garden") == {"Mow the lawn"}
    assert titles(q="STAIRS") == {"Hoover the stairs"}
    assert titles(q="%") == set()  # a wildcard is a plain character
    assert titles(person=kid["id"]) == {"Hoover the stairs", "Water plants"}
    # a child searching sees only what a child may see
    assert {
        t["title"] for t in service.list_tasks(db, kid, scope="family", q="lawn")
    } == set()
    csv_text = service.tasks_csv(db, parent).decode()
    assert (
        csv_text.splitlines()[0]
        == "task,who,takes_turns,day,time,repeats,points,status,done_by,notes"
    )
    assert "Mow the lawn,Leo" in csv_text


# ---------------------------------------------------------------------------
# meals
# ---------------------------------------------------------------------------


def test_ingredients_are_read_from_typed_lines():
    assert meals.parse_ingredient("2 onions") == {"name": "onions", "quantity": "2"}
    assert meals.parse_ingredient("500 g minced beef") == {
        "name": "minced beef",
        "quantity": "500 g",
    }
    assert meals.parse_ingredient("1.5kg rice") == {"name": "rice", "quantity": "1.5kg"}
    assert meals.parse_ingredient("Salt") == {"name": "Salt", "quantity": ""}
    assert meals.parse_ingredient("   ") is None
    assert meals.parse_ingredient({"name": "Eggs", "quantity": "6"}) == {
        "name": "Eggs",
        "quantity": "6",
    }


def test_a_week_of_meals_fills_the_shopping_list_once(db, family):
    parent, kid = family["parent"], family["kid"]
    monday = meals.week_start(db)
    bolognese = meals.save_recipe(
        db,
        parent,
        {
            "title": "Spaghetti bolognese",
            "ingredients": "500 g minced beef\n2 onions\nSpaghetti\nTomatoes",
            "minutes": 40,
        },
    )
    pancakes = meals.save_recipe(
        db, kid, {"title": "Pancakes", "ingredients": ["Flour", "3 eggs", "Milk"]}
    )
    later = max(monday, service.today(db))
    meals.save_meal(
        db,
        parent,
        {"day": later.isoformat(), "slot": "dinner", "recipe_id": bolognese["id"]},
    )
    # a child suggests a meal; planning the same slot again changes it instead of doubling it
    first = meals.save_meal(
        db, kid, {"day": later.isoformat(), "slot": "breakfast", "title": "Toast"}
    )
    again = meals.save_meal(
        db,
        kid,
        {"day": later.isoformat(), "slot": "breakfast", "recipe_id": pancakes["id"]},
    )
    assert again["id"] == first["id"] and again["title"] == "Pancakes"
    with pytest.raises(PermissionError):  # a child cannot change an adult's meal
        meals.save_meal(
            db, kid, {"day": later.isoformat(), "slot": "dinner", "title": "Sweets"}
        )
    with pytest.raises(ValueError):
        meals.save_meal(
            db,
            parent,
            {"day": later.isoformat(), "slot": "dinner"}
            | {"title": "", "recipe_id": ""},
            again["id"],
        )
    with pytest.raises(ValueError):
        meals.save_meal(db, parent, {"day": "someday", "title": "Soup"})
    week = meals.week(db, parent)
    assert (
        week["start"] == monday.isoformat()
        and len(week["days"]) == 7
        and week["planned"] == 2
    )
    day = next(d for d in week["days"] if d["day"] == later.isoformat())
    assert (
        day["meals"]["dinner"]["title"] == "Spaghetti bolognese"
        and day["meals"]["lunch"] is None
    )

    groceries = service.lists(db)[0]
    service.add_item(
        db, parent["id"], groceries["id"], {"name": "Milk"}
    )  # already waiting
    result = meals.shop(
        db, parent, {"list_id": groceries["id"], "start": monday.isoformat()}
    )
    assert set(result["added"]) == {
        "minced beef",
        "onions",
        "Spaghetti",
        "Tomatoes",
        "Flour",
        "eggs",
    }
    assert result["skipped"] == ["Milk"]
    again = meals.shop(
        db, parent, {"list_id": groceries["id"], "start": monday.isoformat()}
    )
    assert again["added"] == []  # pressing it twice adds nothing
    items = {i["name"]: i for i in service.list_detail(db, groceries["id"])["items"]}
    assert (
        items["minced beef"]["quantity"] == "500 g"
        and items["minced beef"]["category"] == "meat & fish"
    )
    # one recipe on its own
    service.clear_checked(db, groceries["id"])
    assert (
        meals.shop(db, kid, {"list_id": groceries["id"], "recipe_id": pancakes["id"]})[
            "added"
        ]
        == []
    )


def test_copying_last_week_fills_only_empty_meals(db, family):
    parent = family["parent"]
    monday = meals.week_start(db)
    last = monday - timedelta(days=7)
    meals.save_meal(
        db, parent, {"day": last.isoformat(), "slot": "dinner", "title": "Curry"}
    )
    meals.save_meal(
        db,
        parent,
        {
            "day": (last + timedelta(days=1)).isoformat(),
            "slot": "dinner",
            "title": "Fish",
        },
    )
    meals.save_meal(
        db, parent, {"day": monday.isoformat(), "slot": "dinner", "title": "Pizza"}
    )
    assert meals.copy_week(db, parent, monday.isoformat()) == 1
    assert meals.copy_week(db, parent, monday.isoformat()) == 0
    titles = {
        (d["day"], s): m["title"]
        for d in meals.week(db, parent)["days"]
        for s, m in d["meals"].items()
        if m
    }
    assert titles[(monday.isoformat(), "dinner")] == "Pizza"
    assert titles[((monday + timedelta(days=1)).isoformat(), "dinner")] == "Fish"


def test_removing_a_recipe_keeps_the_planned_meal(db, family):
    parent, kid = family["parent"], family["kid"]
    soup = meals.save_recipe(db, parent, {"title": "Soup", "ingredients": "Leeks"})
    meal = meals.save_meal(
        db, parent, {"day": service.today(db).isoformat(), "recipe_id": soup["id"]}
    )
    with pytest.raises(PermissionError):
        meals.delete_recipe(db, kid, soup["id"])
    meals.delete_recipe(db, parent, soup["id"])
    week = meals.week(db, parent)
    planned = [m for d in week["days"] for m in d["meals"].values() if m]
    assert [m["title"] for m in planned] == ["Soup"] and planned[0]["recipe_id"] == ""
    assert meals.recipes(db, parent) == []
    meals.delete_meal(db, parent, meal["id"])
    assert meals.week(db, parent)["planned"] == 0


# ---------------------------------------------------------------------------
# selling (money in)
# ---------------------------------------------------------------------------


def test_selling_from_listing_to_money_in_the_budget(db, family):
    parent, kid = family["parent"], family["kid"]
    with pytest.raises(PermissionError):
        selling.save_item(db, kid, {"title": "My bike", "price_cents": 5000})
    with pytest.raises(PermissionError):
        selling.overview(db, kid)
    pram = selling.save_item(
        db,
        parent,
        {
            "title": "Pram",
            "description": "Folds flat",
            "condition": "like_new",
            "price_cents": 12000,
        },
    )
    sofa = selling.save_item(
        db, parent, {"title": "Old sofa", "price_cents": 0, "condition": "fair"}
    )
    page = selling.public_page(db)
    assert [i["title"] for i in page["items"]] == ["Old sofa", "Pram"]
    assert set(page["items"][1]) == {
        "id",
        "kind",
        "price_unit",
        "title",
        "description",
        "condition",
        "price_cents",
        "reserved",
    }

    # neighbours ask; wrong contact details are refused; asking twice changes the first request
    with pytest.raises(ValueError):
        selling.ask(
            db, {"item_id": pram["id"], "name": "Sam", "contact": "not a contact"}
        )
    with pytest.raises(ValueError):
        selling.ask(
            db, {"item_id": pram["id"], "name": "", "contact": "sam@example.com"}
        )
    first = selling.ask(
        db,
        {
            "item_id": pram["id"],
            "name": "Sam",
            "contact": "sam@example.com",
            "offer": "100",
        },
    )
    same = selling.ask(
        db,
        {
            "item_id": pram["id"],
            "name": "Sam",
            "contact": "SAM@example.com",
            "offer": "110",
        },
    )
    assert same["id"] == first["id"]
    other = selling.ask(
        db,
        {
            "item_id": pram["id"],
            "name": "Ana",
            "contact": "+44 7700 900123",
            "message": "Can collect today",
        },
    )
    view = selling.overview(db, parent)
    assert view["new_requests"] == 2 and view["for_sale"] == 2
    pram_view = next(i for i in view["items"] if i["id"] == pram["id"])
    assert [r["offer_cents"] for r in pram_view["requests"]] == [11000, 0]

    # yes to Sam: kept for Sam, nobody else can ask now
    kept = selling.decide(db, parent, first["id"], True)
    assert kept["status"] == "reserved" and kept["buyer"] == "Sam"
    assert selling.public_page(db)["items"][1]["reserved"] is True
    with pytest.raises(ValueError, match="kept for someone"):
        selling.ask(
            db, {"item_id": pram["id"], "name": "Zoe", "contact": "zoe@example.com"}
        )
    assert (
        selling.new_request_count(db) == 0
    )  # Ana waits, but nothing needs an answer now

    # sold for Sam's offer: money in once, in the budget and in the earnings
    sold = selling.mark_sold(db, parent, pram["id"], {})
    selling.mark_sold(
        db, parent, pram["id"], {"sold_cents": 99}
    )  # pressed again: nothing changes
    assert (
        sold["status"] == "sold"
        and sold["sold_cents"] == 11000
        and sold["buyer"] == "Sam"
    )
    income = kit.fetch_all(
        db, "SELECT * FROM pn_entries WHERE kind = 'income' AND category = 'Selling'"
    )
    assert len(income) == 1 and income[0]["amount_cents"] == 11000
    assert (
        kit.count(
            db,
            "SELECT COUNT(*) FROM suite_revenue WHERE app = 'planner' AND voided_at IS NULL",
        )
        == 1
    )
    assert selling.overview(db, parent)["earned_this_month_cents"] == 11000
    assert [i["title"] for i in selling.public_page(db)["items"]] == ["Old sofa"]
    with pytest.raises(LookupError):
        selling.ask(
            db, {"item_id": pram["id"], "name": "Zoe", "contact": "zoe@example.com"}
        )
    assert (
        kit.fetch_one(
            db, "SELECT status FROM pn_sale_requests WHERE id = ?", (other["id"],)
        )["status"]
        == "declined"
    )

    # marked sold by mistake: removing the money from the budget puts it back on sale
    service.delete_entry(db, parent, income[0]["id"])
    back = next(
        i for i in selling.overview(db, parent)["items"] if i["id"] == pram["id"]
    )
    assert back["status"] == "available" and back["sold_cents"] == 0
    assert (
        kit.count(
            db,
            "SELECT COUNT(*) FROM suite_revenue WHERE app = 'planner' AND voided_at IS NULL",
        )
        == 0
    )
    # a free thing sells for nothing and makes no budget entry
    selling.mark_sold(db, parent, sofa["id"], {"buyer": "Neighbour"})
    assert kit.count(db, "SELECT COUNT(*) FROM pn_entries") == 0
    assert b"Old sofa,thing,Fair,0.00,sold,Neighbour" in selling.csv(db, parent)


def test_a_flood_of_requests_is_capped(db, family):
    lamp = selling.save_item(
        db, family["parent"], {"title": "Lamp", "price_cents": 500}
    )
    for n in range(selling.MAX_NEW_REQUESTS):
        selling.ask(
            db,
            {
                "item_id": lamp["id"],
                "name": f"Buyer {n}",
                "contact": f"b{n}@example.com",
            },
        )
    with pytest.raises(ValueError, match="Lots of people"):
        selling.ask(
            db,
            {"item_id": lamp["id"], "name": "One more", "contact": "late@example.com"},
        )


# ---------------------------------------------------------------------------
# pocket money (money out)
# ---------------------------------------------------------------------------


def test_pocket_money_turns_points_into_money_once(db, family):
    parent, kid = family["parent"], family["kid"]
    chore = service.create_task(
        db, parent, {"title": "Wash the car", "assignee": kid["id"], "points": 150}
    )
    service.complete(db, kid, chore["id"])
    with pytest.raises(PermissionError):
        service.pocket(db, kid)
    with pytest.raises(PermissionError):
        service.pay_out(db, kid, kid["id"], {})
    with pytest.raises(ValueError, match="First set"):
        service.pay_out(db, parent, kid["id"], {})
    with pytest.raises(PermissionError):
        service.set_pocket_rate(db, kid, {"per_100_cents": 100000})
    service.set_pocket_rate(db, parent, {"per_100_cents": 250})  # 100 points = 2.50
    reward = service.save_reward(db, parent, {"title": "Cinema", "points": 50})
    service.request_reward(db, kid, reward["id"])  # 50 points promised to a reward
    view = service.pocket(db, parent)
    mia = next(p for p in view["people"] if p["profile_id"] == kid["id"])
    assert (
        mia["points"] == 150 and mia["available"] == 100 and mia["worth_cents"] == 250
    )
    with pytest.raises(ValueError, match="has 100 points"):
        service.pay_out(db, parent, kid["id"], {"points": 120})
    payout = service.pay_out(db, parent, kid["id"], {"points": 30})
    assert payout["amount_cents"] == 75 and service.balance(db, kid["id"]) == 120
    spent = kit.fetch_all(db, "SELECT * FROM pn_entries WHERE category = 'Children'")
    assert (
        len(spent) == 1
        and spent[0]["amount_cents"] == 75
        and spent[0]["note"] == "Pocket money: Mia"
    )
    # the child sees their points history (no money), the adult sees the money
    history = service.rewards_view(db, kid)["history"]
    assert history[0]["delta"] == -30 and history[0]["reason"] == "Pocket money"
    assert service.pocket(db, parent)["paid_this_month_cents"] == 75
    service.undo_payout(db, parent, payout["id"])
    assert service.balance(db, kid["id"]) == 150
    assert kit.count(db, "SELECT COUNT(*) FROM pn_entries") == 0
    # the rest of the available points by default; removing it from the budget gives the points back
    whole = service.pay_out(db, parent, kid["id"], {})
    assert whole["points"] == 100 and whole["amount_cents"] == 250
    entry = kit.fetch_one(db, "SELECT id FROM pn_entries WHERE category = 'Children'")
    service.delete_entry(db, parent, entry["id"])
    assert (
        service.balance(db, kid["id"]) == 150
        and service.pocket(db, parent)["payouts"] == []
    )
    with pytest.raises(ValueError, match="less than 0.01"):
        service.set_pocket_rate(db, parent, {"per_100_cents": 1})
        service.pay_out(db, parent, kid["id"], {"points": 1})


# ---------------------------------------------------------------------------
# through the single-user server
# ---------------------------------------------------------------------------


def test_meals_selling_and_pocket_money_on_the_local_build(env, api):
    kit.forget_schema()
    public.set_index(local._public_link_index)
    home = Household(env, api)
    parent = home.owner
    mia = home.add("Mia", age_group="child")
    today = parent.get("/api/planner/overview")["today"]

    # meals: plan tonight from a recipe, see it on Today, shop for it
    recipe = parent.post(
        "/api/planner/recipes",
        {"title": "Fish pie", "ingredients": "4 potatoes\nFish\nPeas"},
        expect=201,
    )["recipe"]
    parent.post(
        "/api/planner/meals",
        {"day": today, "slot": "dinner", "recipe_id": recipe["id"], "cook": mia.id},
        expect=201,
    )
    week = mia.get(f"/api/planner/meals?start={today}")
    assert any(
        d["meals"]["dinner"] and d["meals"]["dinner"]["cook_name"] == "Mia"
        for d in week["days"]
    )
    card = next(
        c for c in mia.get("/api/life/today")["cards"] if c["title"] == "Meals today"
    )
    assert (
        card["lines"][0]["text"] == "Dinner: Fish pie"
        and card["lines"][0]["sub"] == "Mia is cooking"
    )
    shopped = mia.post("/api/planner/meals/shop", {"start": today})
    assert set(shopped["added"]) == {"potatoes", "Fish", "Peas"}
    assert mia.call("GET", "/api/planner/meals?start=tuesday")[0] == 400

    # selling: a child cannot; a neighbour asks on the public page; it shows on Today and as an alert
    assert mia.call("GET", "/api/planner/sell")[0] == 403
    assert mia.call("POST", "/api/planner/sell/link", {})[0] == 403
    bike = parent.post(
        "/api/planner/sell/items",
        {"title": "Kids bike", "price_cents": 4000},
        expect=201,
    )["item"]
    link = parent.post("/api/planner/sell/link")["link"]
    status, page = _anon(env, "GET", f"/api/public/{link['token']}")
    assert status == 200 and page["page"]["items"][0]["title"] == "Kids bike"
    assert "requests" not in page["page"]["items"][0]
    status, asked = _anon(
        env,
        "POST",
        f"/api/public/{link['token']}/ask",
        {
            "item_id": bike["id"],
            "name": "Sam",
            "contact": "sam@example.com",
            "offer": "35",
        },
    )
    assert status == 201, asked
    assert (
        _anon(
            env,
            "POST",
            f"/api/public/{link['token']}/ask",
            {"item_id": bike["id"], "name": "Sam", "contact": "nope"},
        )[0]
        == 400
    )
    alerts = parent.get("/api/life/pulse")["alerts"]
    alert = next(
        a for a in alerts if a["id"] == f"planner:sale:{asked['request']['id']}"
    )
    assert "Sam" in alert["body"] and "$35.00" in alert["body"]
    assert not any(
        a["id"].startswith("planner:sale:")
        for a in mia.get("/api/life/pulse")["alerts"]
    )
    card = next(
        c
        for c in parent.get("/api/life/today")["cards"]
        if c["title"] == "People want to buy"
    )
    parent.post(card["lines"][0]["action"]["path"])  # Keep it for them, from Today
    sold = parent.post(f"/api/planner/sell/items/{bike['id']}/sold")["item"]
    assert sold["sold_cents"] == 3500
    budget = parent.get("/api/planner/budget")
    assert budget["income_cents"] == 3500 and any(
        e["note"] == "Sold: Kids bike" for e in budget["entries"]
    )
    status, csv_body = parent.call("GET", "/api/planner/sell?format=csv", raw=True)
    assert status == 200 and b"Kids bike" in csv_body
    parent.ok("DELETE", "/api/planner/sell/link")
    assert _anon(env, "GET", f"/api/public/{link['token']}")[0] == 404

    # pocket money: Mia's chore points become money the adult pays out
    chore = parent.post(
        "/api/planner/tasks",
        {"title": "Hoover", "assignee": mia.id, "points": 200},
        expect=201,
    )["task"]
    mia.post(f"/api/planner/tasks/{chore['id']}/done")
    assert mia.call("GET", "/api/planner/pocket")[0] == 403
    parent.post("/api/planner/pocket/rate", {"per_100_cents": 100})
    payout = parent.post(
        f"/api/planner/pocket/{mia.id}/pay", {"points": 150}, expect=201
    )["payout"]
    assert payout["amount_cents"] == 150
    assert mia.get("/api/planner/rewards")["balance"] == 50
    parent.ok("DELETE", f"/api/planner/pocket/payouts/{payout['id']}")
    assert mia.get("/api/planner/rewards")["balance"] == 200

    # chores search and CSV through the server
    found = parent.get("/api/planner/tasks?scope=family&status=all&q=hoov")["tasks"]
    assert [t["title"] for t in found] == ["Hoover"]
    status, body = parent.call("GET", "/api/planner/tasks?format=csv", raw=True)
    assert status == 200 and b"Hoover,Mia" in body


def test_hosted_selling_and_meals_stay_in_their_organization(cloud):
    kit.forget_schema()
    org = build_org(cloud)
    owner = sign_in(cloud, org.owner)
    assert owner.call("GET", "/api/planner/sell")[0] == 402
    cloud.billing.subscribe_app(org.owner, "planner")
    status, made = owner.call(
        "POST", "/api/planner/sell/items", {"title": "Desk", "price_cents": 2000}
    )
    assert status == 201, made
    for who in ("admin", "coach", "candidate", "auditor"):
        browser = sign_in(cloud, getattr(org, who))
        assert browser.call("GET", "/api/planner/meals")[0] == 200, who
    status, link = owner.call("POST", "/api/planner/sell/link", {})
    visitor = Browser(cloud)
    status, page = visitor.call("GET", f"/api/public/{link['link']['token']}")
    assert status == 200 and [i["title"] for i in page["page"]["items"]] == ["Desk"]
    status, asked = visitor.call(
        "POST",
        f"/api/public/{link['link']['token']}/ask",
        {"item_id": made["item"]["id"], "name": "Kim", "contact": "kim@example.com"},
    )
    assert status == 201, asked
    other = build_org(cloud, "Other Org", slug_email="other.test")
    cloud.billing.subscribe_app(other.owner, "planner")
    status, theirs = sign_in(cloud, other.owner).call("GET", "/api/planner/sell")
    assert status == 200 and theirs["items"] == [] and theirs["new_requests"] == 0
    assert owner.call("GET", "/api/planner/sell")[1]["new_requests"] == 1


def test_week_start_is_monday():
    assert meals.week_start(None, "2026-10-29") == date(2026, 10, 26)  # a Thursday
    assert meals.week_start(None, "2026-10-26") == date(2026, 10, 26)
