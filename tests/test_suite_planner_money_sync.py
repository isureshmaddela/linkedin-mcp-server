"""Planner, fourth round: the money of a sale or a side job reads the same everywhere (the budget, Sell, My day,
a savings goal and Aira's earnings across apps) whatever is changed, undone or removed, a savings goal never goes
below nothing, and a take-turns chore keeps working when someone in the turns leaves the household."""

# ruff: noqa: F811
from __future__ import annotations

from datetime import timedelta

import pytest

from aira.suite import kit, life
from aira.suite.planner import goals, selling, service
from test_suite_planner import Req, _no_model, db, family  # noqa: F401


def _live_revenue(db) -> list[dict]:
    return [
        line for line in kit.revenue_lines(db, app="planner") if not line["voided_at"]
    ]


def _goal(db, parent) -> dict:
    return goals.goals(db, parent)["goals"][0]


def _everywhere(db, parent) -> dict[str, int]:
    """This month's money from sales and jobs, as each place shows it."""
    return {
        "budget": service.budget(db, parent)["income_cents"],
        "sell": selling.overview(db, parent)["earned_this_month_cents"],
        "my_day": service.overview(db, parent)["earned_this_month_cents"],
        "aira": sum(int(line["amount_cents"]) for line in _live_revenue(db)),
    }


# ---------------------------------------------------------------------------
# a savings goal never goes below nothing
# ---------------------------------------------------------------------------


def test_undoing_a_job_whose_goal_money_was_taken_out_is_refused(db, family):
    parent = family["parent"]
    goal = goals.save_goal(db, parent, {"title": "Bike", "target_cents": 120000})
    goals.move(db, parent, goal["id"], {"amount_cents": 10000})
    walk = selling.save_item(
        db,
        parent,
        {"title": "Walk", "kind": "service", "price_cents": 1200, "price_unit": "hour"},
    )
    job = selling.record_job(
        db, parent, walk["id"], {"hours": 2, "goal_id": goal["id"]}
    )
    assert _goal(db, parent)["saved_cents"] == 12400
    goals.move(db, parent, goal["id"], {"amount_cents": 12400}, take_out=True)

    with pytest.raises(ValueError, match="taken out"):
        selling.undo_job(db, parent, job["id"])
    db.rollback()
    # Nothing changed: the goal is at zero (not below), the job and its money are still everywhere.
    view = _goal(db, parent)
    assert view["saved_cents"] == 0 and view["percent"] == 0
    assert sum(m["amount_cents"] for m in view["moves"]) == 0
    assert _everywhere(db, parent) == dict.fromkeys(
        ("budget", "sell", "my_day", "aira"), 2400
    )
    # Removing the job's budget entry is refused the same way.
    entry = service.budget(db, parent)["entries"][0]
    with pytest.raises(ValueError, match="taken out"):
        service.delete_entry(db, parent, entry["id"])
    db.rollback()
    assert _goal(db, parent)["saved_cents"] == 0
    # Undo taking it out first, then the job can be undone: back to the 100 put aside by hand.
    take = next(m for m in _goal(db, parent)["moves"] if m["amount_cents"] < 0)
    goals.undo_move(db, parent, take["id"])
    selling.undo_job(db, parent, job["id"])
    assert _goal(db, parent)["saved_cents"] == 10000
    assert _everywhere(db, parent) == dict.fromkeys(
        ("budget", "sell", "my_day", "aira"), 0
    )


def test_not_sold_after_all_whose_goal_money_was_taken_out_is_refused(db, family):
    parent = family["parent"]
    goal = goals.save_goal(db, parent, {"title": "Holiday", "target_cents": 10000})
    pram = selling.save_item(db, parent, {"title": "Pram", "price_cents": 5000})
    selling.mark_sold(db, parent, pram["id"], {"goal_id": goal["id"]})
    goals.move(db, parent, goal["id"], {"amount_cents": 2000}, take_out=True)
    with pytest.raises(ValueError, match="taken out"):
        selling.unsell(db, parent, pram["id"])
    db.rollback()
    assert _goal(db, parent)["saved_cents"] == 3000
    assert selling.overview(db, parent)["items"][0]["status"] == "sold"
    # Lowering the sale's budget entry by more than is left in the goal is refused too; by less is fine.
    entry = service.budget(db, parent)["entries"][0]
    with pytest.raises(ValueError, match="taken out"):
        service.save_entry(db, parent, {"amount_cents": 1500}, entry["id"])
    db.rollback()
    service.save_entry(db, parent, {"amount_cents": 3000}, entry["id"])
    assert _goal(db, parent)["saved_cents"] == 1000
    assert _everywhere(db, parent) == dict.fromkeys(
        ("budget", "sell", "my_day", "aira"), 3000
    )


def test_a_goal_that_was_removed_does_not_block_undoing(db, family):
    parent = family["parent"]
    goal = goals.save_goal(db, parent, {"title": "Gone", "target_cents": 10000})
    pram = selling.save_item(db, parent, {"title": "Pram", "price_cents": 5000})
    selling.mark_sold(db, parent, pram["id"], {"goal_id": goal["id"]})
    goals.move(db, parent, goal["id"], {"amount_cents": 5000}, take_out=True)
    goals.delete_goal(db, parent, goal["id"])
    selling.unsell(db, parent, pram["id"])
    assert _everywhere(db, parent)["aira"] == 0


# ---------------------------------------------------------------------------
# changing or removing a sale's or a job's budget entry
# ---------------------------------------------------------------------------


def test_changing_a_side_job_entry_changes_the_job_and_the_earnings(db, family):
    parent = family["parent"]
    goal = goals.save_goal(db, parent, {"title": "Bike", "target_cents": 50000})
    walk = selling.save_item(
        db, parent, {"title": "Walk", "kind": "service", "price_cents": 2000}
    )
    selling.record_job(db, parent, walk["id"], {"goal_id": goal["id"]})
    entry = service.budget(db, parent)["entries"][0]
    service.save_entry(db, parent, {"amount_cents": 9000}, entry["id"])
    assert _everywhere(db, parent) == dict.fromkeys(
        ("budget", "sell", "my_day", "aira"), 9000
    )
    item = selling.overview(db, parent)["items"][0]
    assert item["earned_cents"] == 9000 and item["jobs"][0]["amount_cents"] == 9000
    assert _goal(db, parent)["saved_cents"] == 9000
    # Moved to last month: out of this month everywhere, and Aira's earnings line moves with it.
    last = (service.today(db).replace(day=1) - timedelta(days=2)).isoformat()
    service.save_entry(db, parent, {"day": last}, entry["id"])
    assert selling.overview(db, parent)["earned_this_month_cents"] == 0
    assert service.budget(db, parent)["income_cents"] == 0
    assert _live_revenue(db)[0]["at"][:7] == last[:7]
    # Money in cannot become money spent.
    with pytest.raises(ValueError, match="Undo on the job"):
        service.save_entry(db, parent, {"kind": "expense"}, entry["id"])
    db.rollback()


def test_changing_a_sale_entry_changes_the_sale_and_the_earnings(db, family):
    parent = family["parent"]
    pram = selling.save_item(db, parent, {"title": "Pram", "price_cents": 5000})
    selling.mark_sold(db, parent, pram["id"], {})
    entry = service.budget(db, parent)["entries"][0]
    service.save_entry(db, parent, {"amount_cents": 4500}, entry["id"])
    assert _everywhere(db, parent) == dict.fromkeys(
        ("budget", "sell", "my_day", "aira"), 4500
    )
    assert selling.overview(db, parent)["items"][0]["sold_cents"] == 4500


def test_a_removed_sold_thing_still_counts_and_its_entry_still_undoes_it(db, family):
    parent = family["parent"]
    pram = selling.save_item(db, parent, {"title": "Pram", "price_cents": 5000})
    selling.mark_sold(db, parent, pram["id"], {})
    selling.delete_item(db, parent, pram["id"])
    # The money was earned: it still counts in Sell and My day, as it does in the budget and Aira's earnings.
    assert _everywhere(db, parent) == dict.fromkeys(
        ("budget", "sell", "my_day", "aira"), 5000
    )
    over = selling.overview(db, parent)
    assert over["earned_total_cents"] == 5000 and over["sold"] == 1
    assert over["items"] == []
    # Its budget entry can still be changed, and removing it takes the money out everywhere.
    entry = service.budget(db, parent)["entries"][0]
    service.save_entry(db, parent, {"amount_cents": 4000}, entry["id"])
    assert _everywhere(db, parent)["aira"] == 4000
    service.delete_entry(db, parent, entry["id"])
    assert _everywhere(db, parent) == dict.fromkeys(
        ("budget", "sell", "my_day", "aira"), 0
    )
    assert not _live_revenue(db)


# ---------------------------------------------------------------------------
# a side job's hours and day
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("unit", ["job", "hour"])
@pytest.mark.parametrize("hours", ["nan", "NaN", "inf", "-1", "1e400", "lots"])
def test_hours_that_are_not_a_number_are_refused_plainly(db, family, unit, hours):
    parent = family["parent"]
    item = selling.save_item(
        db,
        parent,
        {"title": "Sit", "kind": "service", "price_cents": 1000, "price_unit": unit},
    )
    with pytest.raises(ValueError, match="hours as a number"):
        selling.record_job(db, parent, item["id"], {"hours": hours})
    db.rollback()
    assert service.budget(db, parent)["income_cents"] == 0


def test_a_job_on_another_day_counts_in_that_month_everywhere(db, family):
    parent = family["parent"]
    walk = selling.save_item(
        db, parent, {"title": "Walk", "kind": "service", "price_cents": 2000}
    )
    last = (service.today(db).replace(day=1) - timedelta(days=3)).isoformat()
    selling.record_job(db, parent, walk["id"], {"day": last})
    assert _live_revenue(db)[0]["at"][:7] == last[:7]
    assert kit.revenue(db, app="planner")["this_month_cents"] == 0
    assert selling.overview(db, parent)["earned_this_month_cents"] == 0
    # Today's job is dated now, so it is this month in Aira's earnings.
    selling.record_job(db, parent, walk["id"], {})
    assert kit.revenue(db, app="planner")["this_month_cents"] == 2000
    # A day that has not come yet is refused.
    ahead = (service.today(db) + timedelta(days=1)).isoformat()
    with pytest.raises(ValueError, match="today or a day before"):
        selling.record_job(db, parent, walk["id"], {"day": ahead})
    db.rollback()


# ---------------------------------------------------------------------------
# take turns when someone leaves
# ---------------------------------------------------------------------------


def test_a_take_turns_chore_still_saves_after_someone_leaves(db, family):
    parent, kid, teen = family["parent"], family["kid"], family["teen"]
    task = service.create_task(
        db,
        parent,
        {"title": "Wash up", "repeat": "daily", "rotation": [kid["id"], teen["id"]]},
    )
    other = service.create_task(
        db,
        parent,
        {"title": "Bins", "repeat": "daily", "rotation": [kid["id"], teen["id"]]},
    )
    life.remove(db, parent, teen["id"])
    # Only the title changes (the AI app, the API): it saves, and with one person left it is simply theirs.
    saved = service.update_task(db, parent, task["id"], {"title": "Dry up"})
    assert saved["title"] == "Dry up"
    assert saved["rotation"] == [] and saved["next_turn"] == ""
    assert saved["assignee"] == kid["id"]
    # The Edit form sends the one person still ticked: that saves too.
    form = service.update_task(
        db, parent, other["id"], {"title": "Bins out", "rotation": [kid["id"]]}
    )
    assert form["rotation"] == [] and form["assignee"] == kid["id"]
    service.update_task(db, parent, task["id"], {"title": "Wash up"})
    # And the next one in the series has no one-person turns.
    service.complete(db, kid, task["id"])
    nxt = [
        t
        for t in service.list_tasks(db, parent, scope="family")
        if t["title"] == "Wash up"
    ]
    assert nxt and nxt[0]["rotation"] == [] and nxt[0]["assignee"] == kid["id"]


def test_a_rotation_with_three_keeps_turning_when_one_leaves(db, family):
    parent, kid, teen = family["parent"], family["kid"], family["teen"]
    task = service.create_task(
        db,
        parent,
        {
            "title": "Hoover",
            "repeat": "daily",
            "rotation": [parent["id"], kid["id"], teen["id"]],
        },
    )
    life.remove(db, parent, teen["id"])
    saved = service.update_task(db, parent, task["id"], {"title": "Hoover the stairs"})
    assert saved["rotation"] == [parent["id"], kid["id"]]


def test_choosing_someone_outside_the_turns_says_so(db, family):
    parent, kid, teen = family["parent"], family["kid"], family["teen"]
    task = service.create_task(
        db,
        parent,
        {"title": "Dishes", "repeat": "daily", "rotation": [kid["id"], teen["id"]]},
    )
    with pytest.raises(ValueError, match="not one of the people taking turns"):
        service.update_task(db, parent, task["id"], {"assignee": parent["id"]})
    db.rollback()
    # Choosing one of them is fine, and so is ticking the new person under Take turns too.
    assert (
        service.update_task(db, parent, task["id"], {"assignee": teen["id"]})[
            "assignee"
        ]
        == teen["id"]
    )
    both = service.update_task(
        db,
        parent,
        task["id"],
        {"assignee": parent["id"], "rotation": [kid["id"], teen["id"], parent["id"]]},
    )
    assert both["assignee"] == parent["id"] and len(both["rotation"]) == 3


# ---------------------------------------------------------------------------
# the public page
# ---------------------------------------------------------------------------


def test_the_public_page_title_says_what_is_on_it(db, family):
    parent = family["parent"]
    walk = selling.save_item(
        db, parent, {"title": "Walk", "kind": "service", "price_cents": 2000}
    )
    assert selling.public_page(db)["title"] == "Jobs we can do"
    selling.save_item(db, parent, {"title": "Pram", "price_cents": 5000})
    assert selling.public_page(db)["title"] == "Things for sale and jobs we can do"
    selling.pause(db, parent, walk["id"])
    assert selling.public_page(db)["title"] == "Things for sale"
    selling.save_settings(db, parent, {"title": "The Patels' table"})
    assert selling.public_page(db)["title"] == "The Patels' table"
