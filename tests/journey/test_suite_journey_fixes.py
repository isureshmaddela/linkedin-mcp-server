"""Life (aira/suite/journey), fixes after the fourth round of testing: odd amounts on the public pledge form,
a journal day that "Write about another day" must not wipe, a percentage goal that keeps its progress when an
amount is taken back, a money goal that keeps the currency its supporters used, step ideas labelled honestly,
Today's "Your day" card, and a goal that stops taking support when its page is turned off."""

from __future__ import annotations

import pytest
from cloud_support import Browser, build_org, sign_in
from life_support import Household

import auto_apply_assistant as local
import test_suite_journey_journal as base
from aira import suite
from aira.suite import kit, public
from aira.suite.journey import journey_routes, service

# The fixtures and helpers of the journal round (a database with a household, the local build, a stub model).
_anon = base._anon
_Model = base._Model
_no_model = base._no_model
api = base.api
db = base.db
env = base.env
people = base.people
stub = base.stub

ODD_AMOUNTS = [
    "Infinity",
    "-Infinity",
    "sNaN",
    "NaN",
    "1e999999",
    "1e3",
    "12.5.0",
    "0x10",
    "--5",
    "$",
]


def _supported_goal(db, pat, **extra):
    goal = service.create_goal(
        db,
        pat,
        {
            "title": "Laptop",
            "measure": "money",
            "target": "100",
            "currency": "USD",
            **extra,
        },
    )
    service.set_support(db, pat, goal["id"], {"support": True})
    return goal


# ---------------------------------------------------------------------------
# amounts
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("odd", ODD_AMOUNTS)
def test_odd_amounts_are_refused_with_a_clear_message(db, people, odd):
    goal = _supported_goal(db, people["pat"])
    with pytest.raises(ValueError, match="must be a number like 12.50"):
        service.add_pledge(db, goal["id"], {"amount": odd}, "1.1.1.1")
    with pytest.raises(ValueError, match="must be a number like 12.50"):
        service.check_in(db, people["pat"], goal["id"], {"amount": odd})
    assert service.get_goal(db, people["pat"], goal["id"])["current_amount"] == 0


def test_amounts_people_really_type_still_work(db, people):
    assert service.money_in("12.50") == 1250
    assert service.money_in("1,200") == 120000
    assert service.money_in(12.5) == 1250
    assert service.money_in(0.1 + 0.2) == 30
    assert service.money_in(7) == 700
    assert service.money_in("$20", currency="USD") == 2000
    assert service.money_in("USD 20", currency="USD") == 2000
    assert service.money_in("20 usd", currency="USD") == 2000
    assert service.money_in("₹50", currency="INR") == 5000
    assert service.money_in("$5", currency="CAD") == 500  # any dollar sign for a dollar
    assert service.money_in("-5", minimum=-1000) == -500
    assert service.money_in("", default=0) == 0
    with pytest.raises(ValueError, match="too large"):
        service.money_in("99999999999999999999")
    with pytest.raises(ValueError, match="must be a number"):
        service.money_in(float("inf"))
    with pytest.raises(ValueError, match="must be a number"):
        service.money_in(True)


def test_a_pledge_in_another_currencys_symbol_is_refused(db, people):
    goal = _supported_goal(db, people["pat"])
    with pytest.raises(ValueError, match="in USD here"):
        service.add_pledge(db, goal["id"], {"amount": "₹50"}, "1.1.1.1")
    with pytest.raises(ValueError, match="in USD here"):
        service.add_pledge(db, goal["id"], {"amount": "50 EUR"}, "1.1.1.1")
    made = service.add_pledge(db, goal["id"], {"amount": "$50"}, "1.1.1.1")
    assert made["amount_label"] == "$50.00"


# ---------------------------------------------------------------------------
# taking an amount back from a goal measured in percent
# ---------------------------------------------------------------------------


def test_not_received_keeps_a_percentage_goals_own_progress(db, people):
    pat = people["pat"]
    goal = _supported_goal(db, pat)
    service.add_pledge(db, goal["id"], {"amount": "30", "name": "Ann"}, "1.1.1.1")
    pledge = service.goal_detail(db, pat, goal["id"])["pledge_list"][0]
    detail = service.set_pledge(db, pat, pledge["id"], {"status": "received"})
    assert detail["progress"] == 30
    service.update_goal(db, pat, goal["id"], {"measure": "percent"})
    service.check_in(db, pat, goal["id"], {"progress": 60})
    detail = service.set_pledge(db, pat, pledge["id"], {"status": "promised"})
    assert detail["progress"] == 60
    service.set_pledge(db, pat, pledge["id"], {"status": "received"})
    service.delete_pledge(db, pat, pledge["id"])
    assert service.get_goal(db, pat, goal["id"])["progress"] == 60


def test_unticking_a_habit_keeps_a_percentage_goals_own_progress(db, people):
    pat = people["pat"]
    goal = service.create_goal(
        db,
        pat,
        {"title": "Run 50 km", "measure": "number", "target": "50", "unit": "km"},
    )
    habit = service.save_habit(
        db, pat, {"title": "Run", "goal_id": goal["id"], "amount": "5"}
    )
    service.mark_habit(db, pat, habit["id"], {})
    assert service.get_goal(db, pat, goal["id"])["progress"] == 10
    service.update_goal(db, pat, goal["id"], {"measure": "percent"})
    service.check_in(db, pat, goal["id"], {"progress": 45})
    service.unmark_habit(db, pat, habit["id"], {})
    assert service.get_goal(db, pat, goal["id"])["progress"] == 45


# ---------------------------------------------------------------------------
# currency of a goal supporters backed
# ---------------------------------------------------------------------------


def test_a_goal_backed_in_one_currency_keeps_it(db, people):
    pat = people["pat"]
    goal = _supported_goal(db, pat)
    service.add_pledge(db, goal["id"], {"amount": "50"}, "1.1.1.1")
    pledge = service.goal_detail(db, pat, goal["id"])["pledge_list"][0]
    service.set_pledge(db, pat, pledge["id"], {"status": "received"})
    with pytest.raises(ValueError, match="backed this goal in USD"):
        service.update_goal(db, pat, goal["id"], {"currency": "EUR"})
    assert service.get_goal(db, pat, goal["id"])["currency"] == "USD"
    # Other changes still save; cancelling the pledges frees the currency.
    assert (
        service.update_goal(db, pat, goal["id"], {"title": "New laptop"})["title"]
        == "New laptop"
    )
    service.set_pledge(db, pat, pledge["id"], {"status": "cancelled"})
    assert (
        service.update_goal(db, pat, goal["id"], {"currency": "EUR"})["currency"]
        == "EUR"
    )


def test_pledge_totals_add_up_one_currency_only(db, people):
    pat = people["pat"]
    goal = service.create_goal(db, pat, {"title": "Charity run"})
    service.set_support(db, pat, goal["id"], {"support": True})
    service.add_pledge(db, goal["id"], {"amount": "10"}, "1.1.1.1")
    service.add_pledge(db, goal["id"], {"amount": "20"}, "2.2.2.2")
    db.execute(
        "INSERT INTO jr_pledges (id, goal_id, owner_id, name, message, amount, currency, status, client, at) "
        "VALUES ('old', ?, ?, 'Old', '', 99900, 'INR', 'promised', '', ?)",
        (goal["id"], pat["id"], kit.now()),
    )
    db.commit()
    totals = service.pledge_totals(db, goal["id"])
    assert totals["currency"] == "USD" and totals["promised"] == 3000
    assert totals["supporters"] == 3 and totals["other"] == 1


# ---------------------------------------------------------------------------
# journal: another day already written
# ---------------------------------------------------------------------------


def test_writing_about_a_day_already_written_never_wipes_it(db, people):
    pat = people["pat"]
    day = (service.today(db).replace(day=1)).isoformat()
    service.save_entry(
        db,
        pat,
        {"day": day, "mood": 4, "body": "Grandma's visit.", "grateful": "Grandma"},
    )
    with pytest.raises(ValueError, match="already wrote about"):
        service.save_entry(
            db, pat, {"day": day, "mood": 1, "body": "", "grateful": "", "new": True}
        )
    entry = service.list_entries(db, pat)[0]
    assert (entry["mood"], entry["body"], entry["grateful"]) == (
        4,
        "Grandma's visit.",
        "Grandma",
    )
    # A day not written yet saves from the same form; editing a day (no "new") still changes it.
    service.save_entry(db, pat, {"day": "2020-01-02", "mood": 3, "new": True})
    edited = service.save_entry(
        db, pat, {"day": day, "mood": 1, "body": "Changed", "grateful": ""}
    )
    assert (edited["mood"], edited["body"], edited["grateful"]) == (1, "Changed", "")


# ---------------------------------------------------------------------------
# Today, step ideas, turning a page off
# ---------------------------------------------------------------------------


def test_your_day_asks_for_words_only_when_none_are_written(db, people):
    pat = people["pat"]
    service.set_mood(db, pat, {"mood": 5})
    card = journey_routes._your_day(db, pat)
    assert (
        card is not None
        and card["lines"][0]["sub"] == "In your journal. Add a few words in Life."
    )
    service.save_entry(db, pat, {"mood": 5, "body": "A good long walk"})
    card = journey_routes._your_day(db, pat)
    assert card is not None and card["lines"][0]["sub"] == "In your journal."


def test_template_steps_are_never_labelled_as_the_models():
    previous = suite.gateway()
    try:
        suite.set_gateway(_Model('{"ideas": ["x", "y"]}'))
        answer = service.suggest_steps({"title": "Run a 5k", "category": "health"})
        assert answer["source"] == "template" and len(answer["steps"]) >= 4
        suite.set_gateway(_Model('{"steps": "not a list"}'))
        assert service.suggest_steps({"title": "Run a 5k"})["source"] == "template"
        suite.set_gateway(_Model('{"steps": ["Buy shoes", "Run 1 km", "Run 3 km"]}'))
        answer = service.suggest_steps({"title": "Run a 5k"})
        assert answer == {
            "steps": ["Buy shoes", "Run 1 km", "Run 3 km"],
            "source": "model",
        }
    finally:
        suite.set_gateway(previous)


def test_fixes_on_the_local_build(env, api):
    kit.forget_schema()
    public.set_index(local._public_link_index)
    home = Household(env, api)
    pat = home.owner
    goal = pat.post(
        "/api/journey/goals",
        {"title": "Laptop for college", "measure": "money", "target": "800"},
        expect=201,
    )["goal"]
    detail = pat.call(
        "PATCH", f"/api/journey/goals/{goal['id']}/support", {"support": True}
    )[1]["goal"]
    token = detail["link"]["token"]
    for odd in ("Infinity", "-Infinity", "sNaN", "NaN", "1e999999"):
        status, body = _anon(
            env, "POST", f"/api/public/{token}/pledge", {"amount": odd}
        )
        assert status == 400 and "must be a number" in str(body), (odd, body)
    status, body = _anon(env, "POST", f"/api/public/{token}/pledge", {"amount": "₹50"})
    assert status == 400 and "in USD here" in str(body)
    assert _anon(env, "POST", f"/api/public/{token}/pledge", {"amount": "$5"})[0] == 201
    # Turning the link off stops support too, and says so.
    status, body = pat.call("DELETE", f"/api/journey/goals/{goal['id']}/link")
    assert status == 200 and body["support_stopped"] is True
    assert body["goal"]["support"] is False and body["goal"]["link"] is None
    assert _anon(env, "POST", f"/api/public/{token}/pledge", {"amount": "5"})[0] == 404
    # Journal: the new-day form refuses a written day instead of wiping it.
    pat.post("/api/journey/journal", {"day": "2026-01-05", "mood": 4, "body": "Kept"})
    status, body = pat.call(
        "POST",
        "/api/journey/journal",
        {"day": "2026-01-05", "mood": 1, "body": "", "grateful": "", "new": True},
    )
    assert status == 400 and "already wrote" in str(body)
    entry = pat.get("/api/journey/journal?month=2026-01")["entries"][0]
    assert entry["body"] == "Kept" and entry["mood"] == 4


def test_hosted_pledge_with_an_odd_amount_is_a_clear_400(cloud):
    kit.forget_schema()
    org = build_org(cloud)
    cloud.billing.subscribe_app(org.owner, "journey")
    owner = sign_in(cloud, org.owner)
    status, made = owner.call(
        "POST",
        "/api/journey/goals",
        {"title": "Fees", "measure": "money", "target": "500"},
    )
    assert status == 201, made
    status, detail = owner.call(
        "PATCH", f"/api/journey/goals/{made['goal']['id']}/support", {"support": True}
    )
    token = detail["goal"]["link"]["token"]
    visitor = Browser(cloud)
    for odd in ("Infinity", "sNaN", "1e999999"):
        status, body = visitor.call(
            "POST", f"/api/public/{token}/pledge", {"amount": odd}
        )
        assert status == 400 and "must be a number" in str(body), (odd, body)
    status, body = owner.call("DELETE", f"/api/journey/goals/{made['goal']['id']}/link")
    assert status == 200 and body["support_stopped"] is True
