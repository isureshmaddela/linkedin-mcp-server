"""AI (aira/suite/ai): the fixes and features added after the first release.

* Regressions: a second Approve (Today) answers with what happened; the model's odd action shapes (items as text,
  agent names, one action as an object); repeated facts; the CSV export follows the Activity filters.
* Undo of done actions; agents other apps register (the same consent gate, approvals, log and undo); routines
  run once at their time from the pulse and show on Today; Earn (what the apps earned, earning goals, next steps);
  conversation search, rename and export.
* The single-user server and the hosted build for all of it.
"""

from __future__ import annotations

import json
from datetime import datetime
from zoneinfo import ZoneInfo

import pytest
from cloud_support import build_org, sign_in
from life_support import Household

import test_careers
import test_suite_ai
from aira import suite
from aira.suite import kit
from aira.suite.ai import agents, ai_routes, earn, routines, service
from aira.suite.journey import service as journey
from aira.suite.planner import service as planner

_Model = test_suite_ai._Model
_no_model = test_suite_ai._no_model  # the autouse fixture: no writing model
db = test_suite_ai.db
people = test_suite_ai.people
env = test_careers.env
api = test_careers.api
stub = test_careers.stub


def _shopping(db) -> list[str]:
    lists = planner.lists(db)
    return sorted(
        item["name"] for item in planner.list_detail(db, lists[0]["id"])["items"]
    )


def _tasks(db, me) -> list[str]:
    return [task["title"] for task in planner.list_tasks(db, me)]


# ---------------------------------------------------------------------------
# regressions
# ---------------------------------------------------------------------------


def test_a_second_approve_answers_with_what_happened(db, people):
    """Bug: tapping Approve on Today twice (or on Today and in the chat) answered 400 "already decided"."""
    pat = people["pat"]
    service.configure(db, pat, "planner", {"consent": "manual_approval"})
    op = service.chat(db, pat, {"message": "Add eggs to the shopping list"})["answer"][
        "actions"
    ][0]
    first = service.decide(db, pat, op["id"], True)
    again = service.decide(db, pat, op["id"], True)
    assert first["status"] == again["status"] == "done"
    assert _shopping(db) == ["eggs"]  # it ran once
    with pytest.raises(ValueError, match="already decided"):
        service.decide(db, pat, op["id"], False)
    other = service.chat(db, pat, {"message": "Add jam to the shopping list"})[
        "answer"
    ]["actions"][0]
    service.decide(db, pat, other["id"], False)
    assert service.decide(db, pat, other["id"], False)["status"] == "rejected"
    with pytest.raises(ValueError, match="already decided"):
        service.decide(db, pat, other["id"], True)


def test_the_models_odd_action_shapes_still_work(db, people):
    """Bug: a model that wrote the items as one text added "m, i, l, k"; agent names and a lone action were lost."""
    pat = people["pat"]
    suite.set_gateway(
        _Model(
            {
                "reply": "Adding those.",
                "actions": {
                    "agent": "PlannerAgent",
                    "action": "add_shopping_item",
                    "args": {"items": "milk, eggs and bread"},
                },
            }
        )
    )
    result = service.chat(db, pat, {"message": "We are out of breakfast things"})
    action = result["answer"]["actions"][0]
    assert action["status"] == "done", action
    assert action["summary"] == "Add milk, eggs, bread to the shopping list"
    assert _shopping(db) == ["bread", "eggs", "milk"]


def test_an_action_whose_description_breaks_still_runs():
    calls = []

    def describe(args):
        raise TypeError("bad args")

    action = agents.Action(
        "x",
        "Do the thing",
        "minor",
        {},
        lambda c, m, a: calls.append(a) or "Did it.",
        describe,
    )
    agent = agents.Agent(
        "fake", "FakeAgent", "ai", "", "", ("fake",), "auto_minor", (action,)
    )
    # perform only needs the setting row; a connection with the tables is enough.
    import sqlite3

    connection = sqlite3.connect(":memory:")
    connection.row_factory = sqlite3.Row
    kit.forget_schema()
    from aira.suite.ai import store

    store.ensure(connection)
    outcome = service.perform(
        connection, {"id": "p1", "name": "Pat"}, agent, action, {}
    )
    assert outcome["summary"] == "Do the thing" and outcome["status"] == "done"
    connection.close()


def test_repeated_facts_are_said_once(db, people):
    """Bug: News listed the same headline twice and the brief repeated it."""
    assert agents.unique(["A headline", "a  headline", "Other", ""]) == [
        "A headline",
        "Other",
    ]
    fake = agents.Agent(
        "echo",
        "EchoAgent",
        "ai",
        "",
        "",
        ("echo",),
        reader=lambda c, m: ["Same line", "Same line", "Next"],
    )
    assert fake.read(db, people["pat"]) == ["Same line", "Next"]


def test_the_export_follows_the_activity_filters(db, people):
    """Bug: Export CSV on a filtered Activity tab downloaded everything."""
    pat = people["pat"]
    service.chat(db, pat, {"message": "How are my goals going?"})
    service.chat(db, pat, {"message": "Add rice to the shopping list"})
    actions = service.ops_csv(db, pat, kind="action").decode().splitlines()
    assert len(actions) == 2 and "rice" in actions[1]
    reads = service.ops_csv(db, pat, kind="read").decode()
    assert "rice" not in reads and "Read Life" in reads
    assert [o["summary"] for o in service.list_ops(db, pat, q="RICE")] == [
        "Add rice to the shopping list"
    ]
    assert service.list_ops(db, pat, q="100%_") == []  # LIKE wildcards are plain text


# ---------------------------------------------------------------------------
# undo
# ---------------------------------------------------------------------------


def test_done_actions_can_be_undone(db, people):
    pat, sam = people["pat"], people["sam"]
    goal = service.chat(db, pat, {"message": "Add a goal: run a 5K by 2026-12-31"})
    task = service.chat(db, pat, {"message": "Remind me to call Mum tomorrow at 6pm"})
    shop = service.chat(db, pat, {"message": "Add milk and bread to the shopping list"})
    note = service.chat(
        db, pat, {"message": "Note: the wifi password is on the router"}
    )
    event = service.chat(
        db, pat, {"message": "Add an event: moved to Pune on 2023-03-14"}
    )
    progress = service.chat(db, pat, {"message": "Set run a 5K to 60%"})
    ops = [r["answer"]["actions"][0] for r in (goal, task, shop, note, event, progress)]
    assert all(op["status"] == "done" and op["can_undo"] for op in ops), ops

    # Another person can neither see nor undo them.
    with pytest.raises(LookupError):
        service.undo(db, sam, ops[0]["id"])

    assert "back to 0%" in service.undo(db, pat, ops[5]["id"])["result"]
    assert journey.list_goals(db, pat)[0]["progress"] == 0
    undone = service.undo(db, pat, ops[2]["id"])
    assert (
        undone["status"] == "undone"
        and undone["result"] == "Took 2 items off the list."
    )
    assert _shopping(db) == []
    assert service.undo(db, pat, ops[1]["id"])["result"] == "Removed the task."
    assert "call Mum" not in _tasks(db, pat)
    service.undo(db, pat, ops[3]["id"])
    assert planner.notes(db, pat) == []
    service.undo(db, pat, ops[4]["id"])
    assert "moved to Pune" not in {e["title"] for e in journey.list_events(db, pat)}
    service.undo(db, pat, ops[0]["id"])
    assert journey.list_goals(db, pat) == []
    # Undoing again answers with what happened; the log and the conversation show it.
    assert service.undo(db, pat, ops[0]["id"])["status"] == "undone"
    assert {o["status"] for o in service.list_ops(db, pat, kind="action")} == {"undone"}
    shown = service.get_session(db, pat, shop["session_id"])["messages"][-1]["actions"][
        0
    ]
    assert shown["status"] == "undone" and shown["can_undo"] is False
    assert service.list_ops(db, pat, status="undone")[0]["undone_at"]


def test_what_cannot_be_undone_says_so(db, people):
    pat = people["pat"]
    read = service.chat(db, pat, {"message": "How are my goals going?"})
    read_op = service.list_ops(db, pat, kind="read")[0]
    with pytest.raises(ValueError, match="cannot be undone"):
        service.undo(db, pat, read_op["id"])
    failed = service.chat(db, pat, {"message": "Set learn piano to 20%"})["answer"][
        "actions"
    ][0]
    assert failed["status"] == "failed" and failed["can_undo"] is False
    with pytest.raises(ValueError, match="cannot be undone"):
        service.undo(db, pat, failed["id"])
    # A thing already removed in its app: the undo says so and still counts.
    made = service.chat(db, pat, {"message": "Add a task: water the plants"})["answer"][
        "actions"
    ][0]
    task = next(
        t for t in planner.list_tasks(db, pat) if t["title"] == "water the plants"
    )
    planner.delete_task(db, pat, task["id"])
    assert service.undo(db, pat, made["id"])["result"] == "The task was already gone."
    assert read["session_id"]


def test_an_approved_action_can_be_undone(db, people):
    pat = people["pat"]
    service.configure(db, pat, "planner", {"consent": "manual_approval"})
    op = service.chat(db, pat, {"message": "Add tea to the shopping list"})["answer"][
        "actions"
    ][0]
    assert op["can_undo"] is False  # nothing to undo while it waits
    done = service.decide(db, pat, op["id"], True)
    assert done["can_undo"] is True and _shopping(db) == ["tea"]
    service.undo(db, pat, op["id"])
    assert _shopping(db) == []


# ---------------------------------------------------------------------------
# agents other apps register
# ---------------------------------------------------------------------------


@pytest.fixture
def registered():
    """A fake app agent registered like Stocks does, removed again afterwards."""
    before = list(agents.REGISTERED)
    before_ids = dict(agents.BY_ID)
    log: list[tuple[str, dict]] = []

    def buy(connection, me, args):
        log.append(("buy", args))
        return agents.done(
            f"Bought {args.get('quantity')} {args.get('symbol')}.", ref=len(log)
        )

    def unbuy(connection, me, undo):
        log.append(("sell", undo))
        return "Sold them again."

    def watch(connection, me, args):
        log.append(("watch", args))
        return f"Watching {args.get('symbol')}."

    def parser(message):
        text = message.strip().lower()
        if text.startswith("watch "):
            return [{"action": "watch", "args": {"symbol": text[6:].upper()}}]
        return []

    agent = agents.register(
        agents.Agent(
            "fakestocks",
            "FakeStocksAgent",
            "ai",
            "",
            "Pretend shares.",
            ("acmeshares", "acmeportfolio"),
            "auto_minor",
            (
                agents.Action(
                    "watch",
                    "Watch a stock",
                    "minor",
                    {"symbol": "the symbol"},
                    watch,
                    lambda a: f"Watch {a.get('symbol')}",
                ),
                agents.Action(
                    "buy",
                    "Buy shares",
                    "major",
                    {"symbol": "the symbol", "quantity": "how many"},
                    buy,
                    lambda a: f"Buy {a.get('quantity')} {a.get('symbol')}",
                    unbuy,
                ),
            ),
            reader=lambda connection, me: ["Portfolio: 3 shares of ACME, up 2% today."],
            parser=parser,
            earner=lambda connection, me: ["Shares earned you 12.00 this month."],
        )
    )
    yield agent, log
    agents.REGISTERED[:] = before
    agents.BY_ID.clear()
    agents.BY_ID.update(before_ids)


def test_a_registered_agent_gets_the_same_consent_approvals_log_and_undo(
    db, people, registered
):
    agent, log = registered
    pat, sam = people["pat"], people["sam"]
    assert agent in agents.available(pat)
    assert "fakestocks" in {a["id"] for a in service.list_agents(db, pat)["agents"]}

    # It is read for questions that use its words, and in the brief.
    answer = service.chat(db, pat, {"message": "How is my acmeportfolio?"})["answer"]
    assert answer["agents"] == ["FakeStocksAgent"] and "ACME" in answer["content"]
    brief = service.chat(db, pat, {"message": "Give me my morning brief"})["answer"]
    assert "FakeStocksAgent" in brief["agents"]

    # Its own plain command runs at once (a minor action under "small actions alone").
    watched = service.chat(db, pat, {"message": "watch tsla"})["answer"]
    assert watched["source"] == "command" and watched["actions"][0]["status"] == "done"
    assert log[-1] == ("watch", {"symbol": "TSLA"})

    # A major action from the model waits for approval, then runs, and can be undone.
    suite.set_gateway(
        _Model(
            {
                "reply": "I will buy them.",
                "actions": [
                    {
                        "agent": "fakestocks",
                        "action": "buy",
                        "args": {"symbol": "ACME", "quantity": 2},
                    }
                ],
            }
        )
    )
    waiting = service.chat(db, pat, {"message": "Buy 2 ACME acmeshares"})["answer"][
        "actions"
    ][0]
    assert waiting["status"] == "pending" and not any(kind == "buy" for kind, _ in log)
    # Bug: "Buy 2 ACME shares" used to go on the shopping list ("buy ..." is a shopping command) when an app's
    # agent knows a word of it, it is that agent's.
    assert _shopping(db) == []
    assert (
        service.parse_commands("Buy 2 ACME shares")[0]["action"] == "add_shopping_item"
    )
    assert service.parse_commands("Buy 2 acmeshares", [agent]) == []
    assert service.parse_commands("buy rice", [agent])[0]["args"] == {"items": ["rice"]}
    assert [op["id"] for op in service.pending(db, pat)] == [waiting["id"]]
    with pytest.raises(LookupError):
        service.decide(db, sam, waiting["id"], True)
    done = service.decide(db, pat, waiting["id"], True)
    assert done["status"] == "done" and done["can_undo"] and log[-1][0] == "buy"
    assert service.undo(db, pat, waiting["id"])["result"] == "Sold them again."
    assert log[-1] == ("sell", {"ref": 2})

    # Read only blocks it; switched off it is neither read nor allowed to act.
    service.configure(db, pat, "fakestocks", {"consent": "read_only"})
    blocked = service.chat(db, pat, {"message": "watch msft"})["answer"]["actions"][0]
    assert blocked["status"] == "blocked" and "may only read" in blocked["result"]
    service.configure(
        db, pat, "fakestocks", {"enabled": False, "consent": "auto_major"}
    )
    suite.set_gateway(_Model({"reply": "Nothing.", "actions": []}))
    off = service.chat(db, pat, {"message": "How is my acmeportfolio?"})["answer"]
    assert "FakeStocksAgent" not in off["agents"]

    activity = service.list_ops(db, pat, agent="fakestocks")
    assert {op["status"] for op in activity} >= {"done", "undone", "blocked"}
    assert {op["agent_name"] for op in activity} == {"FakeStocksAgent"}
    # Its earnings show on Earn.
    assert earn.overview(db, pat)["from_agents"] == [
        {
            "agent": "fakestocks",
            "name": "FakeStocksAgent",
            "app": "ai",
            "lines": ["Shares earned you 12.00 this month."],
        }
    ]


def test_registering_checks_the_agent(registered):
    agent, _ = registered
    with pytest.raises(ValueError, match="taken"):
        agents.register(agents.Agent("life", "Mine", "ai", "", "", ()))
    with pytest.raises(ValueError, match="lowercase"):
        agents.register(agents.Agent("Bad-Id", "Mine", "ai", "", "", ()))
    with pytest.raises(ValueError, match="consent"):
        agents.register(agents.Agent("odd", "Mine", "ai", "", "", (), "whenever"))
    with pytest.raises(ValueError, match="risk"):
        agents.register(
            agents.Agent(
                "odd",
                "Mine",
                "ai",
                "",
                "",
                (),
                actions=(agents.Action("a", "A", "huge", {}, lambda c, m, a: "", str),),
            )
        )
    # Registering the same id again replaces it.
    again = agents.register(agents.Agent("fakestocks", "Renamed", "ai", "", "", ()))
    assert agents.BY_ID["fakestocks"] is again
    assert [a.id for a in agents.REGISTERED].count("fakestocks") == 1


# ---------------------------------------------------------------------------
# routines
# ---------------------------------------------------------------------------


@pytest.fixture
def clock(monkeypatch):
    """Set the account's clock: clock.set(2026, 10, 27, 7, 5) (a Tuesday)."""

    class Clock:
        now = datetime(2026, 10, 27, 6, 0, tzinfo=ZoneInfo("UTC"))

        def set(self, *parts):
            self.now = datetime(*parts, tzinfo=ZoneInfo("UTC"))

    current = Clock()
    monkeypatch.setattr(routines, "local_now", lambda connection: current.now)
    return current


def test_routines_check_what_they_are_given(db, people, clock):
    pat = people["pat"]
    with pytest.raises(ValueError, match="HH:MM"):
        routines.save(db, pat, {"prompt": "Brief", "at": "7am"})
    with pytest.raises(ValueError, match="HH:MM"):
        routines.save(db, pat, {"prompt": "Brief", "at": "24:10"})
    with pytest.raises(ValueError, match="days"):
        routines.save(db, pat, {"prompt": "Brief", "at": "07:00", "days": ["someday"]})
    with pytest.raises(ValueError, match="at least one day"):
        routines.save(db, pat, {"prompt": "Brief", "at": "07:00", "days": []})
    with pytest.raises(ValueError):
        routines.save(db, pat, {"prompt": "", "at": "07:00"})
    made = routines.save(
        db,
        pat,
        {
            "prompt": "Give me my morning brief",
            "at": "7:5",
            "days": ["mon", "tue", "wed", "thu", "fri"],
        },
    )
    assert made["title"] == "Give me my morning brief" and made["at"] == "07:05"
    assert made["days"] == [0, 1, 2, 3, 4] and made["days_label"] == "Weekdays"
    changed = routines.save(db, pat, {"title": "Brief", "days": "sat,sun"}, made["id"])
    assert changed["title"] == "Brief" and changed["days_label"] == "Weekends"
    assert changed["prompt"] == "Give me my morning brief"
    with pytest.raises(LookupError):
        routines.save(db, people["sam"], {"title": "Mine"}, made["id"])


def test_a_routine_runs_once_at_its_time_and_shows_on_today(db, people, clock):
    pat, sam = people["pat"], people["sam"]
    journey.create_goal(db, pat, {"title": "Run a 5K"})
    brief = routines.save(
        db,
        pat,
        {"title": "Morning brief", "prompt": "Give me my morning brief", "at": "07:00"},
    )
    weekend = routines.save(
        db,
        pat,
        {"prompt": "Add milk to the shopping list", "at": "06:30", "days": ["sat"]},
    )

    # Before the time: nothing runs; Today shows it is still to come, with Run now.
    clock.set(2026, 10, 27, 6, 59)
    pulse = ai_routes._pulse(db, pat, "")
    assert pulse["alerts"] == []
    card = next(
        c for c in ai_routes._today(db, pat) if c["title"] == "Monosage's routines today"
    )
    assert [line["text"] for line in card["lines"]] == [
        "Morning brief"
    ]  # not the Saturday one
    assert card["lines"][0]["sub"] == "At 07:00" and card["lines"][0]["action"][
        "body"
    ] == {"quick": True}

    # At the time: the pulse runs it once, from the facts (no model), and shows the answer.
    clock.set(2026, 10, 27, 7, 2)
    model = _Model({"reply": "From the model", "actions": []})
    suite.set_gateway(model)
    pulse = ai_routes._pulse(db, pat, "")
    assert model.prompts == []  # the pulse never waits for the model
    alert = pulse["alerts"][0]
    assert (
        alert["id"] == f"ai-routine:{brief['id']}:2026-10-27"
        and alert["title"] == "Morning brief"
    )
    assert "Run a 5K" in alert["body"] and alert["speak"] is True
    assert ai_routes._pulse(db, pat, "")["alerts"] == [alert]  # still unseen
    ran = routines.get(db, pat, brief["id"])
    assert ran["ran_today"] and ran["session_id"]
    session = service.get_session(db, pat, ran["session_id"])
    assert (
        session["session"]["title"] == "Morning brief" and len(session["messages"]) == 2
    )
    card = next(
        c for c in ai_routes._today(db, pat) if c["title"] == "Monosage's routines today"
    )
    assert (
        card["lines"][0]["done"] is True
        and card["link"] == f"#/ai?tab=chat&session={ran['session_id']}"
    )

    # Seen: the alert goes; later pulses that day do not run it again; another person has nothing.
    routines.seen(db, pat, brief["id"])
    assert ai_routes._pulse(db, pat, "")["alerts"] == []
    assert len(service.get_session(db, pat, ran["session_id"])["messages"]) == 2
    assert (
        ai_routes._pulse(db, sam, "")["alerts"] == []
        and routines.list_routines(db, sam) == []
    )

    # The next day it runs again into the same conversation.
    clock.set(2026, 10, 28, 7, 30)
    ai_routes._pulse(db, pat, "")
    assert len(service.get_session(db, pat, ran["session_id"])["messages"]) == 4

    # On Saturday the command routine adds milk (through the consent gate).
    clock.set(2026, 10, 31, 6, 31)
    ai_routes._pulse(db, pat, "")
    assert _shopping(db) == ["milk"]
    assert routines.get(db, pat, weekend["id"])["ran_today"]


def test_run_now_asks_the_model_and_a_switched_off_routine_waits(db, people, clock):
    pat = people["pat"]
    routine = routines.save(
        db, pat, {"prompt": "How are my goals going?", "at": "05:00", "enabled": False}
    )
    clock.set(2026, 10, 27, 9, 0)
    assert ai_routes._pulse(db, pat, "")["alerts"] == []
    assert not routines.get(db, pat, routine["id"])["ran_today"]
    suite.set_gateway(_Model({"reply": "Your goals are fine.", "actions": []}))
    result = routines.run(db, pat, routine["id"])
    assert (
        result["answer"]["content"] == "Your goals are fine."
        and result["answer"]["source"] == "model"
    )
    assert result["routine"]["last_answer"] == "Your goals are fine."
    # A deleted conversation starts a new one; deleting the routine keeps its conversation.
    service.delete_session(db, pat, result["session_id"])
    again = routines.run(db, pat, routine["id"], use_model=False)
    assert again["session_id"] != result["session_id"]
    routines.delete(db, pat, routine["id"])
    assert routines.list_routines(db, pat) == []
    assert service.get_session(db, pat, again["session_id"])["messages"]


def test_moving_a_routine_later_today_runs_it_again(db, people, clock):
    pat = people["pat"]
    routine = routines.save(
        db, pat, {"prompt": "Give me my morning brief", "at": "07:00"}
    )
    clock.set(2026, 10, 27, 8, 0)
    ai_routes._pulse(db, pat, "")
    assert routines.get(db, pat, routine["id"])["ran_today"]
    moved = routines.save(db, pat, {"at": "09:00"}, routine["id"])
    assert moved["ran_today"] is False
    clock.set(2026, 10, 27, 9, 1)
    ai_routes._pulse(db, pat, "")
    assert routines.get(db, pat, routine["id"])["ran_today"]


def test_a_routine_added_after_its_time_waits_for_its_next_day(db, people, clock):
    pat = people["pat"]
    clock.set(2026, 10, 27, 14, 0)
    routine = routines.save(
        db, pat, {"prompt": "Give me my morning brief", "at": "07:00"}
    )
    assert ai_routes._pulse(db, pat, "")["alerts"] == []
    assert not routines.get(db, pat, routine["id"])["ran_today"]
    assert not any(
        c["title"] == "Monosage's routines today" for c in ai_routes._today(db, pat)
    )
    # Moved to later today, it runs today after all; and the next morning as usual.
    routines.save(db, pat, {"at": "15:00"}, routine["id"])
    clock.set(2026, 10, 27, 15, 1)
    ai_routes._pulse(db, pat, "")
    assert routines.get(db, pat, routine["id"])["ran_today"]
    clock.set(2026, 10, 28, 15, 2)
    ai_routes._pulse(db, pat, "")
    assert routines.get(db, pat, routine["id"])["last_run_on"] == "2026-10-28"


# ---------------------------------------------------------------------------
# earn
# ---------------------------------------------------------------------------


def test_earn_shows_money_goals_and_next_steps_to_adults_only(db, people, monkeypatch):
    pat, mia = people["pat"], people["mia"]
    # Only Sales has setup steps here (the other apps' real lists are left out).
    monkeypatch.setattr(
        kit,
        "CHECKLISTS",
        {
            "studio": lambda connection: [
                kit.step("client", "Add a client", True),
                kit.step("brand", "Add your logo", False),
                kit.step("payment", "Add a payment link", False, "Settings > Payments"),
            ],
        },
    )
    kit.record_revenue(
        db,
        "studio",
        source_type="invoice",
        source_id="i1",
        amount_cents=150000,
        label="Invoice 1",
    )
    db.commit()

    data = earn.overview(db, pat)
    assert data["may_see"] is True and data["this_month_cents"] == 150000
    assert data["by_app"] == [{"app": "studio", "name": "Sales", "cents": 150000}]
    assert [s["label"] for s in data["steps"]] == [
        "Add a payment link",
        "Add your logo",
    ]  # money first
    assert data["steps"][0]["link"] == "#/studio" and data["goals"] == []
    assert {w["app"] for w in data["ways"]} >= {"videos", "stocks", "journey", "studio"}

    # An earning goal by plain command; it counts what the apps earn from today; it can be undone.
    made = service.chat(db, pat, {"message": "Add an earning goal: 50k by 2026-12-31"})
    action = made["answer"]["actions"][0]
    assert action["agent"] == "earn" and action["status"] == "done", action
    goal = earn.overview(db, pat)["goals"][0]
    assert goal["target_cents"] == 5_000_000 and goal["target_date"] == "2026-12-31"
    kit.record_revenue(
        db, "shop", source_type="order", source_id="o1", amount_cents=500000
    )
    db.commit()
    assert earn.overview(db, pat)["goals"][0]["current_cents"] >= 500000

    answer = service.chat(db, pat, {"message": "How can I earn more?"})["answer"]
    assert answer["agents"] == ["EarnAgent"]
    assert (
        "Earned this month" in answer["content"]
        and "Add a payment link" in answer["content"]
    )

    service.undo(db, pat, action["id"])
    assert earn.overview(db, pat)["goals"] == []

    # A child sees the ways to earn, never the money; and has no EarnAgent to ask.
    child = earn.overview(db, mia)
    assert (
        child["may_see"] is False and "this_month_cents" not in child and child["ways"]
    )
    assert "earn" not in {a.id for a in agents.available(mia)}
    with pytest.raises(LookupError):
        service.configure(db, mia, "earn", {"enabled": True})


def test_an_earning_goal_needs_the_life_app(db, people):
    pat = people["pat"]
    previous = suite._app_filter
    try:
        suite.set_app_filter(lambda app_id: app_id == "ai")
        result = service.chat(db, pat, {"message": "Set an earning goal of 1000"})
        action = result["answer"]["actions"][0]
        assert action["status"] == "failed" and "Life app" in action["result"]
        assert earn.overview(db, pat)["goals"] is None
    finally:
        suite.set_app_filter(previous)


def test_earning_goal_commands_are_understood():
    parse = service.parse_commands
    assert parse("Add an earning goal: 50000 by 2026-12-31") == [
        {
            "agent": "earn",
            "action": "create_earning_goal",
            "args": {"target": "50000", "target_date": "2026-12-31"},
        }
    ]
    assert parse("set an earnings goal of ₹1,20,000 in 6 months")[0]["args"] == {
        "target": "120000",
        "target_date": "in 6 months",
    }
    assert parse("new income goal 2.5k a month")[0]["args"] == {
        "target": "2500",
        "target_date": "",
    }
    assert parse("Add a goal: run a 5K")[0]["action"] == "create_goal"


# ---------------------------------------------------------------------------
# conversations
# ---------------------------------------------------------------------------


def test_conversations_can_be_found_renamed_and_exported(db, people):
    pat, sam = people["pat"], people["sam"]
    first = service.chat(db, pat, {"message": "How are my goals going?"})
    service.chat(db, pat, {"message": "Add saffron to the shopping list"})
    assert [s["id"] for s in service.list_sessions(db, pat, q="goals")] == [
        first["session_id"]
    ]
    assert (
        len(service.list_sessions(db, pat, q="SAFFRON")) == 1
    )  # found in the messages
    assert service.list_sessions(db, pat, q="nothing like this") == []
    renamed = service.rename_session(
        db, pat, first["session_id"], {"title": "  Goals chat  "}
    )
    assert renamed["title"] == "Goals chat"
    with pytest.raises(ValueError):
        service.rename_session(db, pat, first["session_id"], {"title": ""})
    with pytest.raises(LookupError):
        service.rename_session(db, sam, first["session_id"], {"title": "Mine"})
    name, body = service.session_text(db, pat, first["session_id"])
    text = body.decode()
    assert name == "aira-goals-chat.txt"
    assert (
        text.startswith("Goals chat\n")
        and "You (" in text
        and "How are my goals going?" in text
    )
    with pytest.raises(LookupError):
        service.session_text(db, sam, first["session_id"])


# ---------------------------------------------------------------------------
# the single-user server
# ---------------------------------------------------------------------------


def test_the_new_routes_on_the_local_build(env, api):
    kit.forget_schema()
    home = Household(env, api)
    pat = home.owner
    mia = home.add("Mia", age_group="child", pin="2468")

    # Undo from the chat.
    made = pat.post("/api/ai/chat", {"message": "Add a task: water the plants"})
    op = made["answer"]["actions"][0]
    assert op["can_undo"] is True
    assert mia.call("POST", f"/api/ai/ops/{op['id']}/undo", {})[0] == 404
    undone = pat.post(f"/api/ai/ops/{op['id']}/undo")["op"]
    assert undone["status"] == "undone"
    assert pat.get("/api/ai/ops?status=undone")["ops"][0]["id"] == op["id"]
    read = pat.get("/api/ai/ops?kind=read")["ops"]
    assert (
        read == []
        or pat.call("POST", f"/api/ai/ops/{read[0]['id']}/undo", {})[0] == 400
    )

    # Conversations: search, rename, export.
    session_id = made["session_id"]
    assert [s["id"] for s in pat.get("/api/ai/sessions?q=plants")["sessions"]] == [
        session_id
    ]
    assert (
        pat.ok("PATCH", f"/api/ai/sessions/{session_id}", {"title": "Garden"})[
            "session"
        ]["title"]
        == "Garden"
    )
    status, body = pat.call("GET", f"/api/ai/sessions/{session_id}/export", raw=True)
    assert status == 200 and body.decode().startswith("Garden")
    assert mia.call("GET", f"/api/ai/sessions/{session_id}/export")[0] == 404

    # Activity search and the filtered export.
    assert pat.get("/api/ai/ops?q=plants")["ops"][0]["summary"].startswith(
        "Add the task"
    )
    status, csv = pat.call("GET", "/api/ai/export/ops?kind=read", raw=True)
    assert status == 200 and b"water the plants" not in csv

    # Routines.
    listed = pat.get("/api/ai/routines")
    assert (
        listed["routines"] == [] and listed["suggested"][0]["title"] == "Morning brief"
    )
    assert (
        pat.call("POST", "/api/ai/routines", {"prompt": "Brief", "at": "nope"})[0]
        == 400
    )
    routine = pat.post(
        "/api/ai/routines",
        {"title": "Morning brief", "prompt": "Give me my morning brief", "at": "23:59"},
        expect=201,
    )["routine"]
    assert mia.get("/api/ai/routines")["routines"] == []
    assert (
        mia.call("PATCH", f"/api/ai/routines/{routine['id']}", {"title": "x"})[0] == 404
    )
    ran = pat.post(f"/api/ai/routines/{routine['id']}/run", {"quick": True})
    assert ran["answer"]["source"] == "template" and ran["routine"]["ran_today"]
    card = next(
        c
        for c in pat.get("/api/life/today")["cards"]
        if c["title"] == "Monosage's routines today"
    )
    assert card["lines"][0]["done"] is True
    alerts = [a for a in pat.get("/api/life/pulse")["alerts"] if a["app"] == "ai"]
    assert alerts and alerts[0]["title"] == "Morning brief"
    pat.post(alerts[0]["actions"][0]["path"])
    assert [a for a in pat.get("/api/life/pulse")["alerts"] if a["app"] == "ai"] == []
    assert (
        pat.ok("PATCH", f"/api/ai/routines/{routine['id']}", {"enabled": False})[
            "routine"
        ]["enabled"]
        is False
    )
    pat.ok("DELETE", f"/api/ai/routines/{routine['id']}")
    assert pat.get("/api/ai/routines")["routines"] == []

    # Earn: adults see the money; a child sees ways to earn only.
    adult = pat.get("/api/ai/earn")
    assert adult["may_see"] is True and "this_month_cents" in adult
    child = mia.get("/api/ai/earn")
    assert child["may_see"] is False and "this_month_cents" not in child
    assert "earn" not in {a["id"] for a in mia.get("/api/ai/agents")["agents"]}


# ---------------------------------------------------------------------------
# the hosted build
# ---------------------------------------------------------------------------


def test_hosted_routines_and_earn_follow_the_app_and_the_roles(cloud):
    kit.forget_schema()
    org = build_org(cloud)
    owner = sign_in(cloud, org.owner)
    for method, path in (
        ("GET", "/api/ai/earn"),
        ("GET", "/api/ai/routines"),
        ("POST", "/api/ai/routines"),
    ):
        status, body = owner.call(
            method,
            path,
            {"prompt": "Brief", "at": "07:00"} if method == "POST" else None,
        )
        assert status == 402 and body["code"] == "app_required", (path, body)
    cloud.billing.subscribe_app(org.owner, "ai")
    cloud.billing.subscribe_app(org.owner, "planner")

    status, mine = owner.call(
        "POST",
        "/api/ai/routines",
        {"prompt": "Give me my morning brief", "at": "07:00"},
    )
    assert status == 201, mine
    candidate = sign_in(cloud, org.candidate)
    status, theirs = candidate.call("GET", "/api/ai/routines")
    assert status == 200 and theirs["routines"] == []
    assert (
        candidate.call(
            "POST", f"/api/ai/routines/{mine['routine']['id']}/run", {"quick": True}
        )[0]
        == 404
    )

    for who in ("owner", "admin", "coach", "candidate", "billing", "auditor"):
        browser = sign_in(cloud, getattr(org, who))
        status, data = browser.call("GET", "/api/ai/earn")
        assert status == 200, (who, data)
        assert data["may_see"] == (who in {"owner", "admin", "coach", "auditor"}), who
        assert ("this_month_cents" in data) == data["may_see"]

    # Undo works for the member who acted, never across members or organizations.
    status, chat = owner.call(
        "POST", "/api/ai/chat", {"message": "Add a task: call the bank"}
    )
    assert status == 200, chat
    op = chat["answer"]["actions"][0]
    if op["status"] == "done":
        assert candidate.call("POST", f"/api/ai/ops/{op['id']}/undo", {})[0] == 404
        status, undone = owner.call("POST", f"/api/ai/ops/{op['id']}/undo", {})
        assert status == 200 and undone["op"]["status"] == "undone"
    other = build_org(cloud, "Other Org", slug_email="other.test")
    cloud.billing.subscribe_app(other.owner, "ai")
    stranger = sign_in(cloud, other.owner)
    status, theirs = stranger.call("GET", "/api/ai/routines")
    assert status == 200 and theirs["routines"] == []
    assert stranger.call("POST", f"/api/ai/ops/{op['id']}/undo", {})[0] == 404
    assert json.dumps(stranger.call("GET", "/api/ai/earn")[1]).count("routine") == 0


def test_run_now_from_the_routines_tab_does_not_pop_up(db, people, clock):
    """Bug: pressing Run now opened the answer and then showed it again as an alert."""
    pat = people["pat"]
    routine = routines.save(
        db, pat, {"prompt": "How are my goals going?", "at": "05:00"}
    )
    clock.set(2026, 10, 27, 9, 0)
    routines.run(db, pat, routine["id"], use_model=False, seen=True)
    assert routines.alerts(db, pat) == []
    routines.run(
        db, pat, routine["id"], use_model=False
    )  # Today's Run now: the answer pops up
    assert len(routines.alerts(db, pat)) == 1


def test_the_earn_tab_sets_an_earning_goal_through_the_consent_gate(env, api):
    kit.forget_schema()
    home = Household(env, api)
    pat = home.owner
    mia = home.add("Mia", age_group="child", pin="2468")
    assert pat.call("POST", "/api/ai/earn/goals", {"target": ""})[0] == 400
    made = pat.post(
        "/api/ai/earn/goals",
        {"target": "2500", "target_date": "2026-12-31"},
        expect=201,
    )
    assert made["outcome"]["status"] == "done" and made["outcome"]["can_undo"]
    goals = pat.get("/api/ai/earn")["goals"]
    assert [g["target_cents"] for g in goals] == [250000]
    assert mia.call("POST", "/api/ai/earn/goals", {"target": "10"})[0] == 404
    pat.ok("PATCH", "/api/ai/agents/earn", {"consent": "manual_approval"})
    waiting = pat.post("/api/ai/earn/goals", {"target": "100"})
    assert waiting["outcome"]["status"] == "pending"
    assert len(pat.get("/api/ai/earn")["goals"]) == 1
