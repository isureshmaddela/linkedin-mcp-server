"""AI (aira/suite/ai): the master agent's chat, plain commands, agents and their consent levels, approvals and the
activity log, on its own, through the single-user server for a household, and in the hosted build."""

from __future__ import annotations

import json
import sqlite3

import pytest
from cloud_support import build_org, sign_in
from life_support import Household

import test_careers
from aira import suite
from aira.suite import kit, life, public
from aira.suite.ai import agents, ai_routes, service, store
from aira.suite.journey import service as journey
from aira.suite.journey import store as journey_store
from aira.suite.planner import service as planner
from aira.suite.planner import store as planner_store

env = test_careers.env
api = test_careers.api
stub = test_careers.stub


class _NoModel:
    def model(self, prompt):
        return None


class _Model:
    """A writing model that answers with what the test tells it, and remembers the prompts."""

    def __init__(self, answer):
        self.answer = answer
        self.prompts = []

    def model(self, prompt):
        self.prompts.append(prompt)
        return json.dumps(self.answer)


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
    connection = sqlite3.connect(tmp_path / "ai.sqlite3")
    connection.row_factory = sqlite3.Row
    store.ensure(connection)
    journey_store.ensure(connection)
    planner_store.ensure(connection)
    yield connection
    connection.close()


@pytest.fixture
def people(db):
    pat = life.current(Req(), db)
    sam = life.add(db, pat, {"name": "Sam", "age_group": "adult"})
    mia = life.add(db, pat, {"name": "Mia", "age_group": "child"})
    return {"pat": pat, "sam": sam, "mia": mia}


def _shopping(db) -> list[str]:
    lists = planner.lists(db)
    return [item["name"] for item in planner.list_detail(db, lists[0]["id"])["items"]]


# ---------------------------------------------------------------------------
# understanding a message
# ---------------------------------------------------------------------------


def test_plain_commands_are_understood_without_a_model():
    assert service.parse_commands("Add a goal: run a 5K by 2026-12-31") == [
        {
            "agent": "life",
            "action": "create_goal",
            "args": {"title": "run a 5K", "target_date": "2026-12-31"},
        }
    ]
    assert service.parse_commands("add goal Read 12 books in 12 months")[0]["args"] == {
        "title": "Read 12 books",
        "target_date": "in 12 months",
    }
    assert service.parse_commands("Set Run a 5K to 60%")[0] == {
        "agent": "life",
        "action": "log_progress",
        "args": {"goal": "Run a 5K", "progress": 60},
    }
    assert service.parse_commands("Add milk, eggs and bread to the shopping list")[0][
        "args"
    ] == {"items": ["milk", "eggs", "bread"]}
    assert service.parse_commands("we need to buy Rice")[0]["args"] == {
        "items": ["Rice"]
    }
    assert service.parse_commands("Remind me to call Mum tomorrow at 6pm")[0] == {
        "agent": "planner",
        "action": "add_task",
        "args": {"title": "call Mum", "due_date": "tomorrow", "due_time": "18:00"},
    }
    assert (
        service.parse_commands("add a task: water the plants")[0]["args"]["due_date"]
        == "today"
    )
    assert (
        service.parse_commands("Note: the wifi password is on the router")[0]["action"]
        == "add_note"
    )
    assert service.parse_commands("Add an event: moved to Pune on 2023-03-14")[0][
        "args"
    ] == {"title": "moved to Pune", "date": "2023-03-14"}
    assert service.parse_commands("How are my goals going?") == []


def test_questions_go_to_the_agents_whose_words_they_use(db, people):
    everyone = agents.available(people["pat"])
    ids = lambda picked: [a.id for a in picked]  # noqa: E731
    # "today" names a time, not an app: the medicines question goes to HealthAgent only (it used to read every
    # app and answer with the whole day).
    picked, brief = service.route("Which medicines do I take today?", everyone)
    assert ids(picked) == ["health"] and brief is False
    picked, brief = service.route("What do I need to do today?", everyone)
    assert ids(picked) == ["planner"] and brief is False
    # MemoryAgent is not part of a brief (memories go with every question to the model anyway).
    in_brief = [a for a in everyone if a.id != "memory"]
    picked, brief = service.route("Catch me up on my day", everyone)
    assert picked == in_brief and brief is True
    picked, brief = service.route("What do you remember about me?", everyone)
    assert ids(picked) == ["memory"] and brief is False
    picked, _ = service.route("How can I earn more money?", everyone)
    assert ids(picked) == ["earn"]
    picked, brief = service.route("Which medicines should I take?", everyone)
    assert ids(picked) == ["health"] and brief is False
    picked, _ = service.route("How are my goals going?", everyone)
    assert ids(picked) == ["life"]
    picked, _ = service.route("How's my business doing?", everyone)
    assert ids(picked) == ["business"]
    picked, brief = service.route("Tell me something", everyone)
    assert picked == in_brief and brief is True


def test_agents_follow_the_apps_and_who_may_see_the_business(db, people):
    adult = {a.id for a in agents.available(people["pat"])}
    assert {
        "life",
        "planner",
        "health",
        "family",
        "learn",
        "play",
        "news",
        "business",
    } <= adult
    assert "business" not in {a.id for a in agents.available(people["mia"])}
    previous = suite._app_filter
    try:
        suite.set_app_filter(lambda app_id: app_id in {"ai", "journey"})
        assert [a.id for a in agents.available(people["pat"])] == [
            "life",
            "earn",
            "memory",
        ]
        # Earnings are for adults: a child has no EarnAgent (but keeps a memory of their own).
        assert [a.id for a in agents.available(people["mia"])] == ["life", "memory"]
    finally:
        suite.set_app_filter(previous)


# ---------------------------------------------------------------------------
# the chat
# ---------------------------------------------------------------------------


def test_a_morning_brief_without_a_model_reads_every_agent(db, people):
    pat = people["pat"]
    journey.create_goal(db, pat, {"title": "Run a 5K", "category": "health"})
    planner.create_task(
        db,
        pat,
        {
            "title": "Pay the electricity bill",
            "assignee": pat["id"],
            "due_date": planner.today(db).isoformat(),
        },
    )
    result = service.chat(db, pat, {"message": "Give me my morning brief"}, hour=8)
    answer = result["answer"]
    assert answer["source"] == "template" and answer["tokens"] == 0
    assert answer["content"].startswith(f"Good morning, {pat['name']}.")
    assert (
        "Run a 5K" in answer["content"]
        and "Pay the electricity bill" in answer["content"]
    )
    assert set(answer["agents"]) >= {"LifeAgent", "PlannerAgent"}
    reads = service.list_ops(db, pat, kind="read")
    assert {op["agent"] for op in reads} >= {"life", "planner"} and all(
        op["status"] == "done" for op in reads
    )
    session = service.get_session(db, pat, result["session_id"])
    assert [m["role"] for m in session["messages"]] == ["user", "assistant"]
    assert session["session"]["title"] == "Give me my morning brief"
    assert session["stats"]["answers"] == 1


def test_a_command_runs_when_the_consent_allows_and_waits_or_stops_when_not(db, people):
    pat = people["pat"]
    result = service.chat(
        db, pat, {"message": "Add milk and bread to the shopping list"}
    )
    action = result["answer"]["actions"][0]
    assert action["status"] == "done" and action["agent"] == "planner"
    assert result["answer"]["content"].startswith("✓ Added milk, bread")
    assert _shopping(db) == ["bread", "milk"] or sorted(_shopping(db)) == [
        "bread",
        "milk",
    ]

    service.configure(db, pat, "planner", {"consent": "read_only"})
    blocked = service.chat(
        db,
        pat,
        {
            "message": "Add eggs to the shopping list",
            "session_id": result["session_id"],
        },
    )
    assert blocked["answer"]["actions"][0]["status"] == "blocked"
    assert "may only read" in blocked["answer"]["content"]
    assert "eggs" not in _shopping(db)

    service.configure(db, pat, "planner", {"consent": "manual_approval"})
    waiting = service.chat(db, pat, {"message": "Add eggs to the shopping list"})
    op = waiting["answer"]["actions"][0]
    assert op["status"] == "pending" and op["can_decide"] is True
    assert "eggs" not in _shopping(db)
    assert [o["id"] for o in service.pending(db, pat)] == [op["id"]]
    card = ai_routes._today(db, pat)[0]
    assert (
        card["title"] == "Waiting for your approval"
        and card["lines"][0]["action"]["path"] == f"/api/ai/ops/{op['id']}/approve"
    )
    assert ai_routes._pulse(db, pat, "")["badge"] == 1
    done = service.decide(db, pat, op["id"], True)
    assert done["status"] == "done" and "eggs" in _shopping(db)
    with pytest.raises(ValueError, match="already decided"):
        service.decide(db, pat, op["id"], False)
    # The conversation shows the decision.
    session = service.get_session(db, pat, waiting["session_id"])
    assert (
        session["messages"][-1]["actions"][0]["status"] == "done"
        and session["messages"][-1]["actions"][0]["can_decide"] is False
    )

    again = service.chat(db, pat, {"message": "Add jam to the shopping list"})
    rejected = service.decide(db, pat, again["answer"]["actions"][0]["id"], False)
    assert rejected["status"] == "rejected" and "jam" not in _shopping(db)

    # A switched-off agent is neither read nor allowed to act.
    service.configure(db, pat, "planner", {"enabled": False, "consent": "auto_major"})
    off = service.chat(db, pat, {"message": "Add tea to the shopping list"})
    assert (
        off["answer"]["actions"][0]["status"] == "blocked"
        and "switched off" in off["answer"]["actions"][0]["result"]
    )
    assert "PlannerAgent" not in off["answer"]["agents"]


def test_auto_minor_runs_small_actions_and_holds_big_ones():
    minor = agents.Action("a", "A", "minor", {}, lambda c, m, a: "", lambda a: "")
    major = agents.Action("b", "B", "major", {}, lambda c, m, a: "", lambda a: "")
    assert service.gate({"enabled": True, "consent": "auto_minor"}, minor) == "run"
    assert service.gate({"enabled": True, "consent": "auto_minor"}, major) == "ask"
    assert service.gate({"enabled": True, "consent": "auto_major"}, major) == "run"
    assert service.gate({"enabled": True, "consent": "manual_approval"}, minor) == "ask"
    assert service.gate({"enabled": True, "consent": "read_only"}, minor) == "blocked"
    assert service.gate({"enabled": False, "consent": "auto_major"}, minor) == "blocked"


def test_goals_by_chat_and_a_failed_action_is_logged(db, people):
    pat = people["pat"]
    made = service.chat(db, pat, {"message": "Add a goal: run a 5K by 2026-12-31"})
    assert made["answer"]["actions"][0]["status"] == "done"
    goal = journey.list_goals(db, pat)[0]
    assert goal["title"] == "run a 5K" and goal["target_date"] == "2026-12-31"
    progress = service.chat(db, pat, {"message": "Set run a 5K to 60%"})
    assert progress["answer"]["actions"][0]["result"] == 'Set "run a 5K" to 60%.'
    missing = service.chat(db, pat, {"message": "Set learn piano to 20%"})
    failed = missing["answer"]["actions"][0]
    assert failed["status"] == "failed" and "no goal called" in failed["result"]
    ops = service.list_ops(db, pat, agent="life")
    done = sum(1 for op in ops if op["status"] == "done")
    failed = sum(1 for op in ops if op["status"] == "failed")
    life_agent = next(
        a for a in service.list_agents(db, pat)["agents"] if a["id"] == "life"
    )
    assert failed == 1 and life_agent["ops"] == len(ops)
    assert life_agent["success_rate"] == round(100 * done / (done + failed), 1)


def test_the_model_answers_from_the_facts_and_its_actions_pass_the_gate(db, people):
    pat = people["pat"]
    journey.create_goal(db, pat, {"title": "Save for Goa", "category": "money"})
    model = _Model(
        {
            "reply": "You have one goal: Save for Goa. I will add a goal for swimming.",
            "actions": [
                {
                    "agent": "life",
                    "action": "create_goal",
                    "args": {"title": "Learn to swim", "category": "health"},
                },
                {"agent": "life", "action": "delete_everything", "args": {}},
                {"agent": "nobody", "action": "create_goal", "args": {}},
            ],
        }
    )
    suite.set_gateway(model)
    result = service.chat(
        db, pat, {"message": "What are my goals? Also add learning to swim."}
    )
    answer = result["answer"]
    assert answer["source"] == "model" and answer["tokens"] > 0
    assert answer["content"].startswith("You have one goal: Save for Goa.")
    assert [a["status"] for a in answer["actions"]] == [
        "done"
    ]  # unknown agents and actions are ignored
    assert {g["title"] for g in journey.list_goals(db, pat)} == {
        "Save for Goa",
        "Learn to swim",
    }
    prompt = model.prompts[0]
    assert (
        'Goal "Save for Goa"' in prompt
        and 'action "create_goal"' in prompt
        and "Never invent data" in prompt
    )
    # The next question carries the conversation so far.
    service.chat(db, pat, {"message": "Thanks", "session_id": result["session_id"]})
    assert "Person: What are my goals?" in model.prompts[1]
    # A plain command goes straight to its agent without waiting for the model.
    command = service.chat(db, pat, {"message": "Add rice to the shopping list"})
    assert len(model.prompts) == 2 and command["answer"]["source"] == "command"
    assert command["answer"]["content"] == '✓ Added rice to "Groceries".'
    # A model that answers nonsense falls back to the built-in answer.
    suite.set_gateway(_Model(["not", "an", "object"]))
    fallback = service.chat(db, pat, {"message": "How are my goals going?"})
    assert (
        fallback["answer"]["source"] == "template"
        and "Save for Goa" in fallback["answer"]["content"]
    )


def test_conversations_and_actions_belong_to_one_person(db, people):
    pat, sam = people["pat"], people["sam"]
    service.configure(db, pat, "planner", {"consent": "manual_approval"})
    result = service.chat(db, pat, {"message": "Add coffee to the shopping list"})
    op_id = result["answer"]["actions"][0]["id"]
    with pytest.raises(LookupError):
        service.get_session(db, sam, result["session_id"])
    with pytest.raises(LookupError):
        service.chat(db, sam, {"message": "hello", "session_id": result["session_id"]})
    with pytest.raises(LookupError):
        service.decide(db, sam, op_id, True)
    assert service.list_sessions(db, sam) == [] and service.list_ops(db, sam) == []
    assert service.setting(db, sam, agents.BY_ID["planner"])["consent"] == "auto_minor"
    with pytest.raises(LookupError):
        service.configure(db, people["mia"], "business", {"enabled": True})
    with pytest.raises(ValueError):
        service.configure(db, pat, "planner", {"consent": "anything"})
    service.delete_session(db, pat, result["session_id"])
    assert service.list_sessions(db, pat) == []
    csv = service.ops_csv(db, pat).decode()
    assert (
        csv.splitlines()[0]
        == "created_at,agent_name,kind,action,risk,summary,status,result,decided_at"
    )
    assert "coffee" in csv


def test_the_agents_page_adds_up_operations(db, people):
    pat = people["pat"]
    service.chat(db, pat, {"message": "How are my goals going?"})
    service.configure(db, pat, "news", {"enabled": False})
    data = service.list_agents(db, pat)
    kpis = data["kpis"]
    assert kpis["total"] == len(data["agents"]) and kpis["active"] == kpis["total"] - 1
    assert kpis["ops"] == 1 and kpis["ops_week"] == 1 and kpis["success_rate"] == 100.0
    assert kpis["common_consent"] in agents.CONSENT_LEVELS
    assert [level["id"] for level in data["consent_levels"]] == list(
        agents.CONSENT_LEVELS
    )
    assert (
        data["recent"][0]["agent_name"] == "LifeAgent"
        and data["recent"][0]["kind"] == "read"
    )


# ---------------------------------------------------------------------------
# the single-user server
# ---------------------------------------------------------------------------


def test_chatting_with_aira_on_the_local_build(env, api):
    kit.forget_schema()
    home = Household(env, api)
    pat = home.owner
    mia = home.add("Mia", age_group="child", pin="2468")
    pat.post("/api/journey/goals", {"title": "Run a 5K"}, expect=201)
    result = pat.post("/api/ai/chat", {"message": "How are my goals going?"})
    assert (
        "Run a 5K" in result["answer"]["content"]
        and result["answer"]["agent"] == "LifeAgent"
    )
    session_id = result["session_id"]
    shopping = pat.post(
        "/api/ai/chat",
        {"message": "Add milk to the shopping list", "session_id": session_id},
    )
    assert shopping["answer"]["actions"][0]["status"] == "done"
    assert [s["id"] for s in pat.get("/api/ai/sessions")["sessions"]] == [session_id]
    assert len(pat.get(f"/api/ai/sessions/{session_id}")["messages"]) == 4
    assert mia.call("GET", f"/api/ai/sessions/{session_id}")[0] == 404
    assert mia.get("/api/ai/sessions")["sessions"] == []

    agents_page = pat.get("/api/ai/agents")
    assert "business" in {a["id"] for a in agents_page["agents"]}
    assert "business" not in {a["id"] for a in mia.get("/api/ai/agents")["agents"]}
    changed = pat.ok("PATCH", "/api/ai/agents/planner", {"consent": "manual_approval"})[
        "agent"
    ]
    assert changed["consent"] == "manual_approval"
    assert pat.call("PATCH", "/api/ai/agents/nobody", {"enabled": False})[0] == 404
    waiting = pat.post("/api/ai/chat", {"message": "Add eggs to the shopping list"})[
        "answer"
    ]["actions"][0]
    assert waiting["status"] == "pending"
    assert [o["id"] for o in pat.get("/api/ai/approvals")["ops"]] == [waiting["id"]]
    assert pat.get("/api/life/pulse")["apps"]["ai"]["badge"] == 1
    card = next(c for c in pat.get("/api/life/today")["cards"] if c["app"] == "ai")
    pat.post(card["lines"][0]["action"]["path"])
    assert pat.get("/api/ai/approvals")["ops"] == []
    assert (
        pat.get("/api/ai/ops?status=done&kind=action")["ops"][0]["summary"]
        == "Add eggs to the shopping list"
    )
    assert mia.call("POST", f"/api/ai/ops/{waiting['id']}/approve", {})[0] == 404
    status, csv = pat.call("GET", "/api/ai/export/ops", raw=True)
    assert status == 200 and b"Add eggs" in csv
    assert pat.call("POST", "/api/ai/chat", {"message": ""})[0] == 400
    pat.ok("DELETE", f"/api/ai/sessions/{session_id}")
    assert session_id not in {s["id"] for s in pat.get("/api/ai/sessions")["sessions"]}


# ---------------------------------------------------------------------------
# the hosted build
# ---------------------------------------------------------------------------


def test_hosted_ai_needs_the_app_and_only_staff_see_the_business(cloud):
    kit.forget_schema()
    org = build_org(cloud)
    owner = sign_in(cloud, org.owner)
    status, body = owner.call("POST", "/api/ai/chat", {"message": "hello"})
    assert status == 402 and body["code"] == "app_required"
    cloud.billing.subscribe_app(org.owner, "ai")
    # Without a business app there is no business to ask about.
    assert "business" not in {
        a["id"] for a in owner.call("GET", "/api/ai/agents")[1]["agents"]
    }
    cloud.billing.subscribe_app(org.owner, "studio")
    for who in ("owner", "admin", "coach", "candidate", "billing", "auditor"):
        browser = sign_in(cloud, getattr(org, who))
        status, data = browser.call("GET", "/api/ai/agents")
        assert status == 200, (who, data)
        has_business = "business" in {a["id"] for a in data["agents"]}
        assert has_business == (who in {"owner", "admin", "coach", "auditor"}), who
    candidate = sign_in(cloud, org.candidate)
    status, chat = candidate.call(
        "POST", "/api/ai/chat", {"message": "How is my business doing?"}
    )
    assert status == 200 and "Revenue" not in chat["answer"]["content"]
    status, mine = owner.call(
        "POST", "/api/ai/chat", {"message": "Give me my morning brief"}
    )
    assert status == 200
    assert candidate.call("GET", f"/api/ai/sessions/{mine['session_id']}")[0] == 404
    other = build_org(cloud, "Other Org", slug_email="other.test")
    cloud.billing.subscribe_app(other.owner, "ai")
    status, sessions = sign_in(cloud, other.owner).call("GET", "/api/ai/sessions")
    assert status == 200 and sessions["sessions"] == []
