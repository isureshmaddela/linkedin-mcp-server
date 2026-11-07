"""AI (aira/suite/ai): the third round.

* Memory: "Remember that ..." / "Forget that ..." in the chat (MemoryAgent: consent gate, Activity, undo), the
  Memory tab's routes, memories in every prompt to the writing model, each person's own, hosted isolation.
* A daily limit per agent: after N actions alone in 24 hours the next ones wait in Approvals (registered agents
  such as StocksAgent can bring their own default).
* Answers marked helpful or not; an answer's words kept apart from the lines about its actions.
* Earning goals with their pace.
* Regressions: an approval claimed by one tap is not run again by a second; reads in Activity name the question;
  "remember to ..." is a task.
"""

from __future__ import annotations

import json
from datetime import timedelta

import pytest
from cloud_support import build_org, sign_in
from life_support import Household

import test_careers
import test_suite_ai
from aira import suite
from aira.suite import kit
from aira.suite.ai import agents, earn, memory, service
from aira.suite.journey import service as journey
from aira.suite.planner import service as planner

_Model = test_suite_ai._Model
_no_model = test_suite_ai._no_model  # the autouse fixture: no writing model
db = test_suite_ai.db
people = test_suite_ai.people
env = test_careers.env
api = test_careers.api
stub = test_careers.stub


def _tasks(db, me) -> list[str]:
    return [task["title"] for task in planner.list_tasks(db, me)]


# ---------------------------------------------------------------------------
# memory
# ---------------------------------------------------------------------------


def test_memory_commands_are_understood():
    parse = service.parse_commands
    assert parse("Remember that I am vegetarian.") == [
        {"agent": "memory", "action": "remember", "args": {"text": "I am vegetarian"}}
    ]
    assert parse("remember: Our wifi is Home-5G")[0]["args"] == {
        "text": "Our wifi is Home-5G"
    }
    assert parse("Please keep in mind that I like short answers")[0]["args"] == {
        "text": "I like short answers"
    }
    assert parse("Don't forget my anniversary is on 12 May")[0]["action"] == "remember"
    assert parse("Forget that I am vegetarian") == [
        {"agent": "memory", "action": "forget", "args": {"text": "I am vegetarian"}}
    ]
    assert parse("forget about the wifi")[0]["args"] == {"text": "the wifi"}
    # "Remember to ..." asks for a reminder: a task, not a memory.
    assert parse("Remember to call Mum tomorrow") == [
        {
            "agent": "planner",
            "action": "add_task",
            "args": {"title": "call Mum", "due_date": "tomorrow", "due_time": ""},
        }
    ]
    assert parse("Don't forget to water the plants")[0]["action"] == "add_task"
    # A question is not something to remember; "forget it" is not a command.
    assert parse("Remember when we went to Goa?") == []
    assert parse("Forget it") == []


def test_remembering_by_chat_reaches_every_answer_and_can_be_undone(db, people):
    pat, sam = people["pat"], people["sam"]
    said = service.chat(db, pat, {"message": "Remember that I am vegetarian"})
    op = said["answer"]["actions"][0]
    assert op["agent"] == "memory" and op["status"] == "done" and op["can_undo"]
    assert said["answer"]["source"] == "command" and said["answer"]["reply"] == ""
    assert [m["text"] for m in memory.list_memories(db, pat)] == ["I am vegetarian"]
    # The same words twice are kept once; another person's memory is their own.
    service.chat(db, pat, {"message": "remember that i am vegetarian"})
    assert len(memory.list_memories(db, pat)) == 1
    assert memory.list_memories(db, sam) == []

    # Every question to the writing model carries the memories.
    model = _Model({"reply": "Try the lentil soup.", "actions": []})
    suite.set_gateway(model)
    answer = service.chat(db, pat, {"message": "What should I cook tonight?"})["answer"]
    assert answer["content"] == "Try the lentil soup."
    assert "asked you to remember" in model.prompts[-1]
    assert "- I am vegetarian" in model.prompts[-1]
    service.chat(db, sam, {"message": "What should I cook tonight?"})
    assert "I am vegetarian" not in model.prompts[-1]

    # Without a model, MemoryAgent reads them out when asked (and only then: not in a brief).
    suite.set_gateway(test_suite_ai._NoModel())
    recall = service.chat(db, pat, {"message": "What do you remember about me?"})[
        "answer"
    ]
    assert recall["agents"] == ["MemoryAgent"]
    assert "You asked me to remember: I am vegetarian" in recall["content"]
    brief = service.chat(db, pat, {"message": "Give me my morning brief"})["answer"]
    assert "MemoryAgent" not in brief["agents"]

    # Saying it again is not a second memory (and undoing that must not forget the first).
    twice = service.chat(db, pat, {"message": "Remember that I am vegetarian"})
    assert twice["answer"]["actions"][0]["result"].startswith("I already remember")
    assert twice["answer"]["actions"][0]["can_undo"] is False

    # Forget (in other words), and take the forgetting back; then undo the first remember.
    missing = service.chat(db, pat, {"message": "Forget my passport number"})
    assert missing["answer"]["actions"][0]["status"] == "failed"
    forgot = service.chat(db, pat, {"message": "Forget that I'm vegetarian"})["answer"][
        "actions"
    ][0]
    assert forgot["status"] == "done" and memory.list_memories(db, pat) == []
    service.undo(db, pat, forgot["id"])
    assert [m["text"] for m in memory.list_memories(db, pat)] == ["I am vegetarian"]
    again = memory.list_memories(db, pat)[0]
    memory.delete(db, pat, again["id"])
    assert service.undo(db, pat, op["id"])["result"] == "The memory was already gone."

    # Read only: MemoryAgent may not remember on its own.
    service.configure(db, pat, "memory", {"consent": "read_only"})
    blocked = service.chat(db, pat, {"message": "Remember that I like tea"})["answer"]
    assert blocked["actions"][0]["status"] == "blocked"
    assert memory.list_memories(db, pat) == []
    activity = service.list_ops(db, pat, agent="memory", kind="action")
    assert {o["status"] for o in activity} >= {"done", "undone", "blocked", "failed"}


def test_memory_checks_what_it_is_given(db, people, monkeypatch):
    pat, sam = people["pat"], people["sam"]
    with pytest.raises(ValueError, match="Enter what to remember"):
        memory.add(db, pat, "   ")
    with pytest.raises(ValueError, match="too long"):
        memory.add(db, pat, "x" * 301)
    saved = memory.add(db, pat, "my sister is Anna")
    assert saved["text"] == "My sister is Anna"
    with pytest.raises(LookupError):
        memory.update(db, sam, saved["id"], {"text": "Mine now"})
    with pytest.raises(LookupError):
        memory.delete(db, sam, saved["id"])
    assert (
        memory.update(db, pat, saved["id"], {"text": "My sister is Anna Rose"})["text"]
        == "My sister is Anna Rose"
    )
    memory.add(db, pat, "My brother is Ravi")
    assert [m["text"] for m in memory.list_memories(db, pat, q="brother")] == [
        "My brother is Ravi"
    ]
    assert [m["text"] for m in memory.list_memories(db, pat, q="%")] == []
    with pytest.raises(ValueError, match="Which one"):
        memory.find(db, pat, "my")
    monkeypatch.setattr(memory, "MAX_MEMORIES", 2)
    with pytest.raises(ValueError, match="keeps 2 memories"):
        memory.add(db, pat, "A third thing")
    assert memory.clear(db, sam) == 0
    assert memory.clear(db, pat) == 2 and memory.list_memories(db, pat) == []


def test_memory_routes_on_the_local_build(env, api):
    kit.forget_schema()
    home = Household(env, api)
    pat = home.owner
    mia = home.add("Mia", age_group="child", pin="2468")
    assert pat.get("/api/ai/memories") == {"memories": [], "limit": 100}
    assert pat.call("POST", "/api/ai/memories", {"text": ""})[0] == 400
    assert pat.call("POST", "/api/ai/memories", {"text": "x" * 400})[0] == 400
    saved = pat.post("/api/ai/memories", {"text": "I am vegetarian"}, expect=201)[
        "memory"
    ]
    assert mia.get("/api/ai/memories")["memories"] == []
    assert (
        mia.call("PATCH", f"/api/ai/memories/{saved['id']}", {"text": "no"})[0] == 404
    )
    assert mia.call("DELETE", f"/api/ai/memories/{saved['id']}")[0] == 404
    # A child keeps memories of their own, through the chat too.
    made = mia.post("/api/ai/chat", {"message": "Remember that my teacher is Mr Lee"})
    assert made["answer"]["actions"][0]["status"] == "done"
    assert [m["text"] for m in mia.get("/api/ai/memories")["memories"]] == [
        "My teacher is Mr Lee"
    ]
    changed = pat.ok(
        "PATCH", f"/api/ai/memories/{saved['id']}", {"text": "I eat no meat"}
    )
    assert changed["memory"]["text"] == "I eat no meat"
    assert pat.get("/api/ai/memories?q=meat")["memories"][0]["id"] == saved["id"]
    assert "memory" in {a["id"] for a in pat.get("/api/ai/agents")["agents"]}
    assert pat.ok("DELETE", "/api/ai/memories")["deleted"] == 1
    assert pat.get("/api/ai/memories")["memories"] == []
    assert len(mia.get("/api/ai/memories")["memories"]) == 1


def test_hosted_memory_needs_the_app_and_stays_with_each_member(cloud):
    kit.forget_schema()
    org = build_org(cloud)
    owner = sign_in(cloud, org.owner)
    status, body = owner.call("GET", "/api/ai/memories")
    assert status == 402 and body["code"] == "app_required"
    cloud.billing.subscribe_app(org.owner, "ai")
    status, saved = owner.call("POST", "/api/ai/memories", {"text": "I am the owner"})
    assert status == 201, saved
    for who in ("admin", "coach", "candidate", "billing"):
        browser = sign_in(cloud, getattr(org, who))
        status, theirs = browser.call("GET", "/api/ai/memories")
        assert status == 200 and theirs["memories"] == [], (who, theirs)
        status, _ = browser.call("DELETE", f"/api/ai/memories/{saved['memory']['id']}")
        assert status in (403, 404), who
    candidate = sign_in(cloud, org.candidate)
    status, mine = candidate.call("POST", "/api/ai/memories", {"text": "I am new"})
    assert status == 201, mine
    status, listed = owner.call("GET", "/api/ai/memories")
    assert [m["text"] for m in listed["memories"]] == ["I am the owner"]
    other = build_org(cloud, "Other Org", slug_email="other.test")
    cloud.billing.subscribe_app(other.owner, "ai")
    stranger = sign_in(cloud, other.owner)
    status, theirs = stranger.call("GET", "/api/ai/memories")
    assert status == 200 and theirs["memories"] == []
    assert (
        stranger.call("DELETE", f"/api/ai/memories/{saved['memory']['id']}")[0] == 404
    )
    assert "I am the owner" not in json.dumps(theirs)


# ---------------------------------------------------------------------------
# a daily limit per agent
# ---------------------------------------------------------------------------


@pytest.fixture
def trader():
    """A fake trading agent registered like Stocks, with a default limit of 2 actions alone a day."""
    before = list(agents.REGISTERED)
    before_ids = dict(agents.BY_ID)
    trades: list[dict] = []

    def trade(connection, me, args):
        trades.append(args)
        return f"Bought {args.get('symbol')}."

    agent = agents.register(
        agents.Agent(
            "faketrader",
            "FakeTraderAgent",
            "ai",
            "",
            "Pretend trades.",
            ("acmetrade",),
            "auto_major",
            (
                agents.Action(
                    "trade",
                    "Trade",
                    "major",
                    {"symbol": "the symbol"},
                    trade,
                    lambda a: f"Buy {a.get('symbol')}",
                ),
            ),
            reader=lambda connection, me: ["Nothing traded yet."],
            default_limit=2,
        )
    )
    yield agent, trades
    agents.REGISTERED[:] = before
    agents.BY_ID.clear()
    agents.BY_ID.update(before_ids)


def test_an_agent_stops_at_its_daily_limit_and_asks(db, people, trader):
    agent, trades = trader
    pat, sam = people["pat"], people["sam"]
    action = agent.action("trade")
    assert action is not None
    view = next(
        a for a in service.list_agents(db, pat)["agents"] if a["id"] == "faketrader"
    )
    assert view["daily_limit"] == 2 and view["consent"] == "auto_major"

    first = service.perform(db, pat, agent, action, {"symbol": "A"})
    second = service.perform(db, pat, agent, action, {"symbol": "B"})
    third = service.perform(db, pat, agent, action, {"symbol": "C"})
    assert [first["status"], second["status"]] == ["done", "done"]
    assert third["status"] == "pending" and "its limit" in third["result"]
    assert [t["symbol"] for t in trades] == ["A", "B"]
    # Approving it runs it; an approved action does not use up the limit.
    assert service.decide(db, pat, third["id"], True)["status"] == "done"
    assert service.perform(db, pat, agent, action, {"symbol": "D"})["status"] == (
        "pending"
    )
    # Another person has their own count; actions older than 24 hours no longer count.
    assert service.perform(db, sam, agent, action, {"symbol": "E"})["status"] == "done"
    old = kit.iso(kit.utcnow() - timedelta(hours=25))
    db.execute(
        "UPDATE ai_ops SET created_at = ?, decided_at = ? WHERE status = 'done' AND decided_at = created_at "
        "AND owner_id = ?",
        (old, old, pat["id"]),
    )
    db.commit()
    assert service.perform(db, pat, agent, action, {"symbol": "F"})["status"] == "done"

    # The person sets their own limit (0: none); the limit is checked.
    assert (
        service.configure(db, pat, "faketrader", {"daily_limit": 0})["daily_limit"] == 0
    )
    assert all(
        service.perform(db, pat, agent, action, {"symbol": s})["status"] == "done"
        for s in "GHI"
    )
    for wrong in (-1, 1001, "lots"):
        with pytest.raises(ValueError):
            service.configure(db, pat, "faketrader", {"daily_limit": wrong})
    kept = service.configure(db, pat, "faketrader", {"consent": "auto_minor"})
    assert kept["daily_limit"] == 0  # changing the consent keeps the limit
    # A built-in agent with a limit set by the person.
    service.configure(db, pat, "planner", {"daily_limit": 1})
    service.chat(db, pat, {"message": "Add a task: one"})
    waits = service.chat(db, pat, {"message": "Add a task: two"})["answer"]["actions"][
        0
    ]
    assert (
        waits["status"] == "pending"
        and "PlannerAgent already took 1 action" in (waits["result"])
    )
    assert _tasks(db, pat) == ["one"]
    with pytest.raises(ValueError, match="1000"):
        agents.register(
            agents.Agent("toomany", "Too", "ai", "", "", (), default_limit=5000)
        )


def test_the_agents_route_saves_the_daily_limit(env, api):
    kit.forget_schema()
    home = Household(env, api)
    pat = home.owner
    assert pat.call("PATCH", "/api/ai/agents/planner", {"daily_limit": -3})[0] == 400
    saved = pat.ok("PATCH", "/api/ai/agents/planner", {"daily_limit": "5"})["agent"]
    assert saved["daily_limit"] == 5
    listed = pat.get("/api/ai/agents")["agents"]
    assert next(a for a in listed if a["id"] == "planner")["daily_limit"] == 5


# ---------------------------------------------------------------------------
# answers: helpful or not; their own words
# ---------------------------------------------------------------------------


def test_answers_can_be_marked_helpful_and_keep_their_own_words(db, people):
    pat, sam = people["pat"], people["sam"]
    suite.set_gateway(
        _Model(
            {
                "reply": "Done, I added it.",
                "actions": [
                    {
                        "agent": "planner",
                        "action": "add_task",
                        "args": {"title": "Pay rent"},
                    }
                ],
            }
        )
    )
    result = service.chat(db, pat, {"message": "I must pay the rent"})
    answer = result["answer"]
    # The words shown above the action cards leave out the lines about the actions (they go stale when the
    # person approves or undoes); the saved conversation keeps both.
    assert answer["reply"] == "Done, I added it."
    assert (
        answer["content"].startswith("Done, I added it.") and "✓" in answer["content"]
    )
    again = service.get_session(db, pat, result["session_id"])["messages"][-1]
    assert again["reply"] == "Done, I added it." and again["rating"] == 0

    assert service.list_agents(db, pat)["kpis"]["helpful_percent"] is None
    assert service.rate(db, pat, answer["id"], {"rating": 1})["rating"] == 1
    kpis = service.list_agents(db, pat)["kpis"]
    assert kpis["rated"] == 1 and kpis["helpful_percent"] == 100
    second = service.chat(db, pat, {"message": "And again?"})["answer"]
    service.rate(db, pat, second["id"], {"rating": -1})
    assert service.list_agents(db, pat)["kpis"]["helpful_percent"] == 50
    service.rate(db, pat, second["id"], {"rating": 0})
    assert service.list_agents(db, pat)["kpis"]["rated"] == 1
    with pytest.raises(LookupError):
        service.rate(db, sam, answer["id"], {"rating": 1})
    with pytest.raises(LookupError):
        service.rate(db, pat, result["question"]["id"], {"rating": 1})
    with pytest.raises(ValueError):
        service.rate(db, pat, answer["id"], {"rating": 5})
    assert service.list_agents(db, sam)["kpis"]["rated"] == 0


def test_the_rate_route(env, api):
    kit.forget_schema()
    home = Household(env, api)
    pat = home.owner
    mia = home.add("Mia", age_group="child", pin="2468")
    answer = pat.post("/api/ai/chat", {"message": "How are my goals going?"})["answer"]
    assert (
        pat.ok("POST", f"/api/ai/messages/{answer['id']}/rate", {"rating": 1})[
            "message"
        ]["rating"]
        == 1
    )
    assert (
        mia.call("POST", f"/api/ai/messages/{answer['id']}/rate", {"rating": -1})[0]
        == 404
    )
    assert (
        pat.call("POST", f"/api/ai/messages/{answer['id']}/rate", {"rating": "x"})[0]
        == 400
    )


# ---------------------------------------------------------------------------
# earning goals and their pace
# ---------------------------------------------------------------------------


def test_an_earning_goal_says_whether_it_is_on_track(db, people):
    pat = people["pat"]
    date = (journey.today(db) + timedelta(days=70)).isoformat()
    made = service.chat(db, pat, {"message": f"Add an earning goal: 10000 by {date}"})[
        "answer"
    ]["actions"][0]
    assert made["status"] == "done", made
    goal = earn.overview(db, pat)["goals"][0]
    pace = goal["pace"]
    assert pace["state"] == "behind" and pace["recent_week_cents"] == 0
    assert pace["need_week_cents"] == 100_000  # 10,000.00 over 10 weeks
    assert pace["projected_cents"] == 0

    kit.record_revenue(
        db, "studio", source_type="invoice", source_id="p1", amount_cents=280_000
    )
    db.commit()
    pace = earn.overview(db, pat)["goals"][0]["pace"]
    # 2,800.00 in the last four weeks is 700.00 a week; 7,200.00 is left over 10 weeks: 720.00 a week.
    assert pace["recent_week_cents"] == 70_000 and pace["need_week_cents"] == 72_000
    assert pace["state"] == "behind" and pace["projected_cents"] == 280_000 + 700_000
    kit.record_revenue(
        db, "shop", source_type="order", source_id="p2", amount_cents=40_000
    )
    db.commit()
    data = earn.overview(db, pat)
    assert data["goals"][0]["pace"]["state"] == "on_track"
    line = next(f for f in earn.facts(db, pat) if f.startswith("Earning goal"))
    assert "a week" in line and "on track" in line

    # Goals without a date have no pace; a goal past its date says so.
    service.chat(db, pat, {"message": "Add an earning goal: 500"})
    undated = next(g for g in earn.overview(db, pat)["goals"] if not g["target_date"])
    assert undated["pace"] is None
    late = earn.pace(
        db,
        {
            "status": "active",
            "target_date": "2020-01-01",
            "target_amount": 1000,
            "current_amount": 400,
            "currency": "USD",
            "days_left": -5,
        },
    )
    assert late is not None and late["state"] == "late"
    assert late["remaining_cents"] == 600
    assert "past its date" in earn.pace_text(late)


# ---------------------------------------------------------------------------
# regressions
# ---------------------------------------------------------------------------


def test_an_approval_claimed_by_one_tap_is_not_run_by_another(db, people):
    """Bug: Approve on Today and in Approvals at the same moment ran the action twice."""
    pat = people["pat"]
    service.configure(db, pat, "planner", {"consent": "manual_approval"})
    op = service.chat(db, pat, {"message": "Add a task: call the plumber"})["answer"][
        "actions"
    ][0]
    assert op["status"] == "pending"
    # Another request has just claimed it (it is running the action now).
    db.execute("UPDATE ai_ops SET decided_at = ? WHERE id = ?", (kit.now(), op["id"]))
    db.commit()
    assert service.decide(db, pat, op["id"], True)["status"] == "pending"
    assert _tasks(db, pat) == []
    # A claim that never finished (the server stopped) may be tried again after a few minutes.
    stale = kit.iso(kit.utcnow() - timedelta(minutes=10))
    db.execute("UPDATE ai_ops SET decided_at = ? WHERE id = ?", (stale, op["id"]))
    db.commit()
    assert service.decide(db, pat, op["id"], True)["status"] == "done"
    assert service.decide(db, pat, op["id"], True)["status"] == "done"
    assert _tasks(db, pat) == ["call the plumber"]


def test_reads_in_activity_name_the_question(db, people):
    """Bug: every read in Activity said "Read Health for a question", so nobody could tell which."""
    pat = people["pat"]
    service.chat(db, pat, {"message": "Which medicines do I take today?"})
    read = service.list_ops(db, pat, kind="read")[0]
    assert read["summary"] == 'Read Health for "Which medicines do I take today?"'
    assert service.list_ops(db, pat, q="medicines")[0]["id"] == read["id"]
    long = "How are my goals going " + "and what next " * 20
    service.chat(db, pat, {"message": long})
    summary = service.list_ops(db, pat, kind="read")[0]["summary"]
    assert summary.endswith('…"') and len(summary) < 110
