"""Planner, third round: fixes found by using it for real (a new bill overdue at once, a missed repeating chore
that kept coming back in the past, a time without a day, any text accepted as a bill period) and the features
that make it the home's planner and a way to earn: chores the family takes turns on, side jobs offered to
neighbours (each job done is money in), savings goals that money from sales and jobs can go straight into, and
the budget's last six months. On their own, through the single-user server, and hosted."""

# ruff: noqa: F811
from __future__ import annotations

from datetime import date, timedelta

import pytest
from cloud_support import Browser, build_org, sign_in
from life_support import Household

import auto_apply_assistant as local
from aira.suite import kit, life, public
from aira.suite.planner import goals, selling, service, store
from test_careers import api, env, stub  # noqa: F401
from test_suite_planner import Req, _anon, _no_model, db, family  # noqa: F401


# ---------------------------------------------------------------------------
# fixes
# ---------------------------------------------------------------------------


def test_a_bill_added_after_its_day_this_month_is_not_overdue(db, family):
    parent = family["parent"]
    bill = {
        "id": "b1",
        "repeat": "monthly",
        "due_day": 5,
        "due_date": "",
        "created_at": "2026-10-27T10:00:00Z",
    }
    # Added on the 27th, due on the 5th: next due on 5 November, not "overdue since 5 October".
    assert service.next_bill(db, bill, date(2026, 10, 27)) == (
        "2026-11",
        date(2026, 11, 5),
    )
    # Due later this month: this month's.
    assert service.next_bill(db, {**bill, "due_day": 30}, date(2026, 10, 27)) == (
        "2026-10",
        date(2026, 10, 30),
    )
    # Due today, added today: due today (not skipped).
    assert service.next_bill(db, {**bill, "due_day": 27}, date(2026, 10, 27))[1] == (
        date(2026, 10, 27)
    )
    # A bill that has been running since before last month's day and was not paid is still overdue.
    old = {**bill, "created_at": "2026-08-01T10:00:00Z"}
    assert service.next_bill(db, old, date(2026, 10, 27)) == (
        "2026-09",
        date(2026, 9, 5),
    )
    # Yearly, its date already passed this year when it was added: next year's.
    yearly = {
        **bill,
        "repeat": "yearly",
        "due_date": "2026-03-01",
    }
    assert service.next_bill(db, yearly, date(2026, 10, 27)) == (
        "2027",
        date(2027, 3, 1),
    )
    # Through the service: a bill added now with a due day already gone this month is upcoming.
    on = service.today(db)
    if on.day > 1:
        made = service.save_bill(
            db, parent, {"name": "Water", "amount_cents": 3000, "due_day": on.day - 1}
        )
        assert made["status"] == "upcoming" and made["days_until"] > 0


def test_a_payment_must_name_a_real_period(db, family):
    parent = family["parent"]
    bill = service.save_bill(db, parent, {"name": "Rent", "amount_cents": 90000})
    with pytest.raises(ValueError, match="month"):
        service.pay_bill(db, parent, bill["id"], {"period": "whenever"})
    with pytest.raises(ValueError):
        service.pay_bill(db, parent, bill["id"], {"period": "2026-13"})
    assert service.pay_bill(db, parent, bill["id"], {"period": "2026-12"})
    assert (
        kit.count(
            db, "SELECT COUNT(*) FROM pn_bill_payments WHERE bill_id = ?", (bill["id"],)
        )
        == 1
    )


def test_a_repeating_chore_done_late_catches_up_to_today(db, family):
    parent, kid = family["parent"], family["kid"]
    on = service.today(db)
    late = service.create_task(
        db,
        parent,
        {
            "title": "Feed the fish",
            "assignee": kid["id"],
            "due_date": (on - timedelta(days=5)).isoformat(),
            "repeat": "daily",
        },
    )
    service.complete(db, kid, late["id"])
    following = [
        t
        for t in service.list_tasks(db, kid, status="open")
        if t["title"] == "Feed the fish"
    ]
    # One more, today: not four more days already in the past.
    assert [t["due_date"] for t in following] == [on.isoformat()]
    weekly = service.create_task(
        db,
        parent,
        {
            "title": "Bins",
            "assignee": "",
            "due_date": (on - timedelta(days=14)).isoformat(),
            "repeat": "weekly",
            "repeat_days": [(on - timedelta(days=14)).weekday()],
        },
    )
    service.complete(db, parent, weekly["id"])
    nxt = [
        t["due_date"]
        for t in service.list_tasks(db, parent, scope="family", status="open")
        if t["title"] == "Bins"
    ]
    assert nxt == [on.isoformat()]  # the same weekday as two weeks ago is today


def test_a_time_without_a_day_is_today(db, family):
    parent = family["parent"]
    task = service.create_task(
        db,
        parent,
        {"title": "Call Gran", "assignee": parent["id"], "due_time": "18:00"},
    )
    assert task["due_date"] == service.today(db).isoformat()
    assert task["due_time"] == "18:00"


# ---------------------------------------------------------------------------
# taking turns
# ---------------------------------------------------------------------------


def test_chores_take_turns_between_people(db, family):
    parent, kid, teen = family["parent"], family["kid"], family["teen"]
    on = service.today(db).isoformat()
    with pytest.raises(ValueError, match="repeats"):
        service.create_task(
            db,
            parent,
            {"title": "Dishes", "assignee": "", "rotation": [kid["id"], teen["id"]]},
        )
    with pytest.raises(ValueError, match="two people"):
        service.create_task(
            db,
            parent,
            {"title": "Dishes", "repeat": "daily", "rotation": [kid["id"]]},
        )
    with pytest.raises(ValueError, match="household"):
        service.create_task(
            db,
            parent,
            {"title": "Dishes", "repeat": "daily", "rotation": [kid["id"], "nobody"]},
        )
    with pytest.raises(PermissionError):
        service.create_task(
            db,
            kid,
            {
                "title": "Dishes",
                "assignee": kid["id"],
                "repeat": "daily",
                "rotation": [kid["id"], teen["id"]],
            },
        )
    dishes = service.create_task(
        db,
        parent,
        {
            "title": "Dishes",
            "assignee": "",  # anyone: the first person in the turn takes it
            "due_date": on,
            "repeat": "daily",
            "points": 5,
            "rotation": [kid["id"], teen["id"]],
        },
    )
    assert dishes["assignee"] == kid["id"]
    assert dishes["rotation_names"] == ["Mia", "Leo"] and dishes["next_turn"] == "Leo"
    # Mia does hers; the next one is Leo's, then Mia's again. Each earns their own points.
    service.complete(db, kid, dishes["id"])
    leos = next(
        t for t in service.list_tasks(db, teen, status="open") if t["title"] == "Dishes"
    )
    assert leos["assignee"] == teen["id"] and leos["next_turn"] == "Mia"
    assert not [
        t for t in service.list_tasks(db, kid, status="open") if t["title"] == "Dishes"
    ]
    service.complete(db, teen, leos["id"])
    assert service.balance(db, kid["id"]) == 5 and service.balance(db, teen["id"]) == 5
    mias = next(
        t for t in service.list_tasks(db, kid, status="open") if t["title"] == "Dishes"
    )
    assert mias["assignee"] == kid["id"]
    # Someone who leaves the household is skipped: the turn goes back to whoever is left.
    third = life.add(db, parent, {"name": "Ana", "age_group": "teen"})
    service.update_task(
        db, parent, mias["id"], {"rotation": [kid["id"], third["id"], teen["id"]]}
    )
    life.remove(db, parent, third["id"])
    service.complete(db, kid, mias["id"])
    upcoming = next(
        t
        for t in service.list_tasks(db, parent, scope="family", status="open")
        if t["title"] == "Dishes"
    )
    assert upcoming["assignee"] == teen["id"] and upcoming["rotation_names"] == [
        "Mia",
        "Leo",
    ]
    # Turning it off: a plain chore again.
    plain = service.update_task(db, parent, upcoming["id"], {"rotation": []})
    assert plain["rotation"] == [] and plain["next_turn"] == ""
    assert b"takes_turns" in service.tasks_csv(db, parent)


def test_a_child_edits_their_own_task_without_touching_turns(db, family):
    kid = family["kid"]
    mine = service.create_task(
        db,
        kid,
        {
            "title": "Tidy my room",
            "assignee": kid["id"],
            "repeat": "weekly",
            "repeat_days": [5],
        },
    )
    changed = service.update_task(db, kid, mine["id"], {"title": "Tidy my room well"})
    assert changed["title"] == "Tidy my room well" and changed["rotation"] == []


# ---------------------------------------------------------------------------
# side jobs
# ---------------------------------------------------------------------------


def test_side_jobs_earn_money_once_per_job(db, family):
    parent, kid = family["parent"], family["kid"]
    with pytest.raises(PermissionError):
        selling.save_item(db, kid, {"title": "Babysitting", "kind": "service"})
    walking = selling.save_item(
        db,
        parent,
        {
            "title": "Dog walking",
            "kind": "service",
            "price_cents": 1200,
            "price_unit": "hour",
            "description": "Weekday mornings",
        },
    )
    assert walking["kind"] == "service" and walking["price_unit"] == "hour"
    with pytest.raises(ValueError, match="cannot become"):
        selling.save_item(db, parent, {"kind": "thing"}, walking["id"])
    with pytest.raises(ValueError, match="record each job"):
        selling.mark_sold(db, parent, walking["id"], {})
    page = selling.public_page(db)
    assert page["items"][0]["kind"] == "service" and page["items"][0]["condition"] == ""

    # A neighbour asks (an offer is ignored for a job); saying yes books them and the job stays on offer.
    asked = selling.ask(
        db,
        {
            "item_id": walking["id"],
            "name": "Mrs Patel",
            "contact": "07700 900123",
            "offer": "5",
            "message": "Tuesdays?",
        },
    )
    row = kit.fetch_one(
        db, "SELECT * FROM pn_sale_requests WHERE id = ?", (asked["id"],)
    )
    assert row["offer_cents"] == 0
    assert selling.new_request_count(db) == 1
    booked = selling.decide(db, parent, asked["id"], True)
    assert (
        booked["status"] == "available"
        and booked["requests"][0]["status"] == "accepted"
    )
    assert selling.new_request_count(db) == 0
    second = selling.ask(
        db, {"item_id": walking["id"], "name": "Tom", "contact": "tom@example.com"}
    )
    assert second["status"] == "new"  # others can still ask

    # Done and paid: per hour, so 2 hours is 24.00, once in the budget and once in the earnings.
    with pytest.raises(ValueError, match="hours"):
        selling.record_job(db, parent, walking["id"], {"request_id": asked["id"]})
    job = selling.record_job(
        db, parent, walking["id"], {"request_id": asked["id"], "hours": 2}
    )
    assert job["amount_cents"] == 2400 and job["customer"] == "Mrs Patel"
    with pytest.raises(ValueError, match="already recorded"):
        selling.record_job(
            db, parent, walking["id"], {"request_id": asked["id"], "hours": 2}
        )
    entries = kit.fetch_all(db, "SELECT * FROM pn_entries WHERE kind = 'income'")
    assert len(entries) == 1 and entries[0]["category"] == "Side jobs"
    revenue = kit.fetch_all(
        db,
        "SELECT * FROM suite_revenue WHERE app = 'planner' AND source_type = 'job' AND voided_at IS NULL",
    )
    assert len(revenue) == 1 and revenue[0]["amount_cents"] == 2400
    view = selling.overview(db, parent)
    assert view["earned_this_month_cents"] == 2400 and view["jobs_done"] == 1
    item = next(i for i in view["items"] if i["id"] == walking["id"])
    assert item["jobs_count"] == 1 and item["earned_cents"] == 2400
    assert [r["name"] for r in item["requests"]] == [
        "Tom"
    ]  # the done request left the list
    assert b"Dog walking,side job" in selling.csv(db, parent)
    # A job without a request, paid a set amount.
    extra = selling.record_job(
        db, parent, walking["id"], {"amount_cents": 1500, "customer": "Tom"}
    )
    assert selling.overview(db, parent)["earned_total_cents"] == 3900

    # Undo: the money leaves the budget and the earnings; the request is booked again.
    selling.undo_job(db, parent, job["id"])
    assert (
        kit.fetch_one(
            db, "SELECT status FROM pn_sale_requests WHERE id = ?", (asked["id"],)
        )["status"]
        == "accepted"
    )
    assert selling.overview(db, parent)["earned_total_cents"] == 1500
    # Removing the job's money from the budget undoes the job too.
    entry = kit.fetch_one(
        db, "SELECT entry_id FROM pn_jobs WHERE id = ?", (extra["id"],)
    )
    service.delete_entry(db, parent, entry["entry_id"])
    assert kit.count(db, "SELECT COUNT(*) FROM pn_jobs") == 0
    assert (
        kit.count(
            db,
            "SELECT COUNT(*) FROM suite_revenue WHERE source_type = 'job' AND voided_at IS NULL",
        )
        == 0
    )

    # Pausing takes it off the page and stops new requests.
    selling.pause(db, parent, walking["id"])
    assert selling.public_page(db)["items"] == []
    with pytest.raises(ValueError, match="not taking"):
        selling.ask(
            db, {"item_id": walking["id"], "name": "Kim", "contact": "kim@example.com"}
        )
    selling.put_back(db, parent, walking["id"])
    assert len(selling.public_page(db)["items"]) == 1
    with pytest.raises(PermissionError):
        selling.record_job(db, kid, walking["id"], {"amount_cents": 100})
    with pytest.raises(ValueError):
        selling.pause(
            db, parent, selling.save_item(db, parent, {"title": "Lamp"})["id"]
        )


def test_a_friendly_message_for_a_contact_that_is_not_one(db, family):
    lamp = selling.save_item(
        db, family["parent"], {"title": "Lamp", "price_cents": 500}
    )
    with pytest.raises(ValueError, match="phone number"):
        selling.ask(
            db, {"item_id": lamp["id"], "name": "Sam", "contact": "not-a-phone"}
        )


# ---------------------------------------------------------------------------
# savings goals
# ---------------------------------------------------------------------------


def test_savings_goals_and_money_put_toward_them(db, family):
    parent, kid = family["parent"], family["kid"]
    with pytest.raises(PermissionError):
        goals.goals(db, kid)
    with pytest.raises(PermissionError):
        goals.save_goal(db, kid, {"title": "Bike", "target_cents": 100})
    with pytest.raises(ValueError):
        goals.save_goal(db, parent, {"title": "Bike", "target_cents": 0})
    on = service.today(db)
    holiday = goals.save_goal(
        db,
        parent,
        {
            "title": "Holiday",
            "target_cents": 120000,
            "target_day": (on + timedelta(days=183)).isoformat(),
        },
    )
    assert holiday["saved_cents"] == 0 and holiday["status"] == "saving"
    assert holiday["months_left"] == 7 or holiday["months_left"] == 6
    assert holiday["per_month_cents"] * holiday["months_left"] >= 120000
    after = goals.move(
        db, parent, holiday["id"], {"amount_cents": 20000, "note": "Salary"}
    )
    assert after["saved_cents"] == 20000 and after["percent"] == 16
    with pytest.raises(ValueError, match="put aside"):
        goals.move(db, parent, holiday["id"], {"amount_cents": 30000}, take_out=True)
    taken = goals.move(db, parent, holiday["id"], {"amount_cents": 5000}, take_out=True)
    assert taken["saved_cents"] == 15000
    # Undoing money put in that was partly taken out again would go below zero: refused.
    put_in = next(m for m in taken["moves"] if m["amount_cents"] == 20000)
    with pytest.raises(ValueError, match="taken out"):
        goals.undo_move(db, parent, put_in["id"])

    # A sale's money straight into the goal; "not sold after all" takes it back out.
    pram = selling.save_item(db, parent, {"title": "Pram", "price_cents": 8000})
    selling.mark_sold(db, parent, pram["id"], {"goal_id": holiday["id"]})
    assert goals.goals(db, parent)["goals"][0]["saved_cents"] == 23000
    selling.unsell(db, parent, pram["id"])
    assert goals.goals(db, parent)["goals"][0]["saved_cents"] == 15000
    # A side job's money too.
    ironing = selling.save_item(
        db, parent, {"title": "Ironing", "kind": "service", "price_cents": 1000}
    )
    job = selling.record_job(db, parent, ironing["id"], {"goal_id": holiday["id"]})
    assert goals.goals(db, parent)["goals"][0]["saved_cents"] == 16000
    selling.undo_job(db, parent, job["id"])
    assert goals.goals(db, parent)["goals"][0]["saved_cents"] == 15000
    with pytest.raises(LookupError):
        selling.record_job(db, parent, ironing["id"], {"goal_id": "f" * 32})

    # Reached, a date that passed, and the totals.
    small = goals.save_goal(db, parent, {"title": "Lamp", "target_cents": 1000})
    reached = goals.move(db, parent, small["id"], {"amount_cents": 1500})
    assert reached["status"] == "reached" and reached["percent"] == 100
    assert [g["title"] for g in selling.overview(db, parent)["goals"]] == ["Holiday"]
    late = goals.save_goal(
        db,
        parent,
        {
            "title": "Boiler",
            "target_cents": 5000,
            "target_day": (on - timedelta(days=1)).isoformat(),
        },
    )
    assert late["status"] == "late" and late["per_month_cents"] == 5000
    summary = goals.goals(db, parent)
    assert summary["saved_cents"] == 16500 and summary["this_month_cents"] == 16500
    goals.delete_goal(db, parent, small["id"])
    # The soonest date first.
    assert [g["title"] for g in goals.goals(db, parent)["goals"]] == [
        "Boiler",
        "Holiday",
    ]


def test_the_budget_shows_the_last_six_months(db, family):
    parent = family["parent"]
    service.save_entry(
        db, parent, {"kind": "income", "amount_cents": 200000, "day": "2026-10-01"}
    )
    service.save_entry(
        db, parent, {"kind": "expense", "amount_cents": 5000, "day": "2026-10-03"}
    )
    service.save_entry(
        db, parent, {"kind": "expense", "amount_cents": 7000, "day": "2026-08-15"}
    )
    service.save_entry(
        db, parent, {"kind": "expense", "amount_cents": 999, "day": "2026-04-30"}
    )  # seven months back: not shown
    trend = service.budget(db, parent, "2026-10")["trend"]
    assert [t["month"] for t in trend] == [
        "2026-05",
        "2026-06",
        "2026-07",
        "2026-08",
        "2026-09",
        "2026-10",
    ]
    assert trend[-1] == {
        "month": "2026-10",
        "income_cents": 200000,
        "expense_cents": 5000,
        "net_cents": 195000,
    }
    assert trend[3]["net_cents"] == -7000
    assert [t["month"] for t in service.budget(db, parent, "2026-02")["trend"]][
        0
    ] == "2025-09"


def test_an_older_database_gains_the_new_columns(tmp_path):
    import sqlite3

    kit.forget_schema()
    connection = sqlite3.connect(tmp_path / "old.sqlite3")
    connection.row_factory = sqlite3.Row
    life.ensure(connection)
    kit.ensure(connection, "planner.v1", store.SCHEMA)
    kit.ensure(connection, "planner.v2", store.SCHEMA_V2)
    store.ensure(connection)
    store.ensure(connection)  # twice: nothing is added again
    columns = {row[1] for row in connection.execute("PRAGMA table_info(pn_tasks)")}
    assert "rotation" in columns
    columns = {row[1] for row in connection.execute("PRAGMA table_info(pn_sale_items)")}
    assert {"kind", "price_unit"} <= columns
    connection.close()


# ---------------------------------------------------------------------------
# through the single-user server, and hosted
# ---------------------------------------------------------------------------


def test_side_jobs_goals_and_turns_on_the_local_build(env, api):
    kit.forget_schema()
    public.set_index(local._public_link_index)
    home = Household(env, api)
    parent = home.owner
    mia = home.add("Mia", age_group="child")
    leo = home.add("Leo", age_group="teen")
    today = parent.get("/api/planner/overview")["today"]

    # take turns, through the routes
    task = parent.post(
        "/api/planner/tasks",
        {
            "title": "Wash up",
            "repeat": "daily",
            "due_date": today,
            "rotation": [mia.id, leo.id],
        },
        expect=201,
    )["task"]
    assert task["assignee"] == mia.id
    assert (
        mia.call("PATCH", f"/api/planner/tasks/{task['id']}", {"rotation": [mia.id]})[0]
        == 403
    )
    mia.post(f"/api/planner/tasks/{task['id']}/done")
    assert [t["title"] for t in leo.get("/api/planner/tasks?status=open")["tasks"]] == [
        "Wash up"
    ]

    # goals: adults only
    assert mia.call("GET", "/api/planner/goals")[0] == 403
    goal = parent.post(
        "/api/planner/goals", {"title": "New bikes", "target_cents": 30000}, expect=201
    )["goal"]
    parent.post(f"/api/planner/goals/{goal['id']}/add", {"amount_cents": 5000})
    assert (
        parent.call(
            "POST", f"/api/planner/goals/{goal['id']}/take", {"amount_cents": 9000}
        )[0]
        == 400
    )

    # a side job: a neighbour asks on the public page; Today and the pulse show it with "Say yes"
    job_item = parent.post(
        "/api/planner/sell/items",
        {
            "title": "Babysitting",
            "kind": "service",
            "price_cents": 1000,
            "price_unit": "hour",
        },
        expect=201,
    )["item"]
    link = parent.post("/api/planner/sell/link")["link"]
    status, page = _anon(env, "GET", f"/api/public/{link['token']}")
    assert status == 200 and page["page"]["items"][0]["price_unit"] == "hour"
    status, asked = _anon(
        env,
        "POST",
        f"/api/public/{link['token']}/ask",
        {
            "item_id": job_item["id"],
            "name": "Jo",
            "contact": "jo@example.com",
            "message": "Friday night",
        },
    )
    assert status == 201, asked
    card = next(
        c
        for c in parent.get("/api/life/today")["cards"]
        if c["title"] == "People are asking"
    )
    assert card["lines"][0]["action"]["label"] == "Say yes"
    alert = next(
        a
        for a in parent.get("/api/life/pulse")["alerts"]
        if a["id"] == f"planner:sale:{asked['request']['id']}"
    )
    assert alert["title"] == "Someone asks for Babysitting"
    parent.post(card["lines"][0]["action"]["path"])  # Say yes, from Today
    job = parent.post(
        f"/api/planner/sell/items/{job_item['id']}/jobs",
        {"request_id": asked["request"]["id"], "hours": 3, "goal_id": goal["id"]},
        expect=201,
    )["job"]
    assert job["amount_cents"] == 3000
    assert parent.get("/api/planner/goals")["goals"][0]["saved_cents"] == 8000
    assert parent.get("/api/planner/budget")["income_cents"] == 3000
    # My day shows adults what they earned this month; children never see money.
    assert parent.get("/api/planner/overview")["earned_this_month_cents"] == 3000
    assert mia.get("/api/planner/overview")["earned_this_month_cents"] == 0
    assert (
        mia.call(
            "POST", f"/api/planner/sell/items/{job_item['id']}/jobs", {"hours": 1}
        )[0]
        == 403
    )
    parent.ok("DELETE", f"/api/planner/sell/jobs/{job['id']}")
    assert parent.get("/api/planner/budget")["income_cents"] == 0
    assert parent.get("/api/planner/goals")["goals"][0]["saved_cents"] == 5000
    parent.post(f"/api/planner/sell/items/{job_item['id']}/pause")
    status, page = _anon(env, "GET", f"/api/public/{link['token']}")
    assert page["page"]["items"] == []
    assert "trend" in parent.get("/api/planner/budget")


def test_hosted_goals_and_side_jobs_stay_in_their_organization(cloud):
    kit.forget_schema()
    org = build_org(cloud)
    owner = sign_in(cloud, org.owner)
    assert owner.call("GET", "/api/planner/goals")[0] == 402
    cloud.billing.subscribe_app(org.owner, "planner")
    status, goal = owner.call(
        "POST", "/api/planner/goals", {"title": "Team trip", "target_cents": 50000}
    )
    assert status == 201, goal
    status, job = owner.call(
        "POST",
        "/api/planner/sell/items",
        {"title": "Tutoring", "kind": "service", "price_cents": 2500},
    )
    assert status == 201, job
    status, done = owner.call(
        "POST",
        f"/api/planner/sell/items/{job['item']['id']}/jobs",
        {"customer": "Kim", "goal_id": goal["goal"]["id"]},
    )
    assert status == 201 and done["job"]["amount_cents"] == 2500
    assert owner.call("GET", "/api/planner/goals")[1]["saved_cents"] == 2500
    status, link = owner.call("POST", "/api/planner/sell/link", {})
    visitor = Browser(cloud)
    status, page = visitor.call("GET", f"/api/public/{link['link']['token']}")
    assert status == 200 and page["page"]["items"][0]["kind"] == "service"
    other = build_org(cloud, "Other Org", slug_email="other.test")
    cloud.billing.subscribe_app(other.owner, "planner")
    theirs = sign_in(cloud, other.owner)
    assert theirs.call("GET", "/api/planner/goals")[1]["goals"] == []
    assert theirs.call("GET", "/api/planner/sell")[1]["earned_total_cents"] == 0
    assert (
        theirs.call("DELETE", f"/api/planner/sell/jobs/{done['job']['id']}")[0] == 404
    )
