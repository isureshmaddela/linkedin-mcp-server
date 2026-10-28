"""Life (aira/suite/journey), the third round: a private journal with a mood a day and "on this day" memories,
supporters who back a goal from its public page (paid to the owner directly, marked received by the owner), step
ideas for a goal from the writing model (or a template), and the fixes found using the app."""

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
from aira.suite.journey import journey_routes, service, store

env = test_careers.env
api = test_careers.api
stub = test_careers.stub


class _NoModel:
    def __init__(self) -> None:
        self.prompts: list[str] = []

    def model(self, prompt):
        self.prompts.append(prompt)
        return None


class _Model:
    """A writing model that answers with a fixed reply."""

    def __init__(self, reply: str) -> None:
        self.reply = reply
        self.prompts: list[str] = []

    def model(self, prompt):
        self.prompts.append(prompt)
        return self.reply


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
    tom = life.add(db, pat, {"name": "Tom", "age_group": "teen"})
    return {"pat": pat, "sam": sam, "mia": mia, "tom": tom}


def _years_ago(day: date, years: int) -> date:
    try:
        return day.replace(year=day.year - years)
    except ValueError:  # 29 February
        return day.replace(year=day.year - years, day=28)


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
# the journal
# ---------------------------------------------------------------------------


def test_a_journal_day_keeps_a_mood_and_words_and_one_day_is_one_entry(db, people):
    pat = people["pat"]
    now = service.today(db)
    with pytest.raises(ValueError, match="Pick how the day was"):
        service.save_entry(db, pat, {})
    with pytest.raises(ValueError, match="not a day to come"):
        service.save_entry(
            db, pat, {"mood": 4, "day": (now + timedelta(days=1)).isoformat()}
        )
    with pytest.raises(ValueError):
        service.save_entry(db, pat, {"mood": 9})
    entry = service.save_entry(db, pat, {"mood": 4, "body": "A long walk by the lake."})
    assert entry["day"] == now.isoformat() and entry["mood_label"] == "Good"
    # The one-tap mood from Today changes the mood and keeps the words; pressing it again changes nothing.
    again = service.set_mood(db, pat, {"mood": 5})
    assert again["id"] == entry["id"] and again["body"] == "A long walk by the lake."
    assert service.set_mood(db, pat, {"mood": 5})["mood"] == 5
    service.save_entry(db, pat, {"grateful": "Sunshine"})
    assert len(service.list_entries(db, pat)) == 1
    yesterday = (now - timedelta(days=1)).isoformat()
    service.save_entry(db, pat, {"day": yesterday, "mood": 2, "body": "Tired"})
    summary = service.journal_summary(db, pat)
    assert summary["streak"] == 2 and summary["total"] == 2
    assert summary["entry_today"]["grateful"] == "Sunshine"
    assert summary["mood_days"][-1] == {"day": now.isoformat(), "mood": 5}
    assert summary["mood_days"][-2]["mood"] == 2 and len(summary["mood_days"]) == 30
    assert summary["mood_average"] == 3.5 and summary["mood_word"] == "Good"
    # Filters and search.
    assert [e["day"] for e in service.list_entries(db, pat, mood="2")] == [yesterday]
    assert [e["day"] for e in service.list_entries(db, pat, search="lake")] == [
        now.isoformat()
    ]
    with pytest.raises(ValueError, match="2026-10"):
        service.list_entries(db, pat, month="October")
    # Private: nobody else reads or deletes it.
    assert service.list_entries(db, people["sam"]) == []
    with pytest.raises(LookupError):
        service.delete_entry(db, people["sam"], entry["id"])
    csv = service.journal_csv(db, pat).decode()
    assert csv.splitlines()[0] == "day,mood,what_happened,grateful_for"
    assert csv.index(yesterday) < csv.index(now.isoformat())  # oldest first
    service.delete_entry(db, pat, entry["id"])
    assert [e["day"] for e in service.list_entries(db, pat)] == [yesterday]


def test_on_this_day_brings_back_events_and_journal_days_from_earlier_years(db, people):
    pat = people["pat"]
    now = service.today(db)
    three = _years_ago(now, 3).isoformat()
    service.save_event(
        db, pat, {"title": "Moved to Pune", "event_date": three, "kind": "home"}
    )
    service.save_event(
        db,
        pat,
        {"title": "Not today", "event_date": (now - timedelta(days=3)).isoformat()},
    )
    service.save_event(db, pat, {"title": "This year", "event_date": now.isoformat()})
    service.save_event(
        db, people["sam"], {"title": "Sam's day", "event_date": three, "shared": True}
    )
    one = _years_ago(now, 1)
    db.execute(
        "INSERT INTO jr_journal (id, owner_id, day, mood, body, grateful, created_at, updated_at) VALUES (?, ?, ?, 5, ?, '', ?, ?)",
        (
            kit.new_id(),
            pat["id"],
            one.isoformat(),
            "First day at the new job\nNervous",
            kit.now(),
            kit.now(),
        ),
    )
    db.commit()
    found = service.memories(db, pat)
    assert [(m["type"], m["title"], m["years_ago"]) for m in found] == [
        ("journal", "First day at the new job", 1),
        ("event", "Moved to Pune", 3),
    ]
    assert service.memories(db, people["sam"])[0]["title"] == "Sam's day"
    assert service.overview(db, pat)["memories"] == found
    # Today: "Your day" offers three moods in one tap, then shows the memories.
    card = next(c for c in journey_routes._today(db, pat) if c["title"] == "Your day")
    assert [line["text"][2:] for line in card["lines"][:3]] == [
        "A good day",
        "An okay day",
        "A hard day",
    ]
    action = card["lines"][0]["action"]
    assert action["path"] == "/api/journey/journal/mood" and action["body"] == {
        "mood": 5
    }
    assert card["lines"][3]["sub"] == "On this day 1 year ago"
    service.set_mood(db, pat, action["body"])
    card = next(c for c in journey_routes._today(db, pat) if c["title"] == "Your day")
    # Shown as a plain line (Today crosses out "done" lines; a mood is not a task).
    assert card["lines"][0]["text"].endswith("Today: great")
    assert card["lines"][0]["done"] is False
    assert "action" not in card["lines"][0]


def test_today_asks_about_the_day_only_people_who_use_life(db, people):
    sam = people["sam"]
    assert journey_routes._today(db, sam) == []
    service.save_habit(db, sam, {"title": "Stretch"})
    titles = [card["title"] for card in journey_routes._today(db, sam)]
    assert titles == ["Habits today", "Your day"]


def test_a_leap_day_is_remembered_on_the_28th_of_february():
    assert service._leap(2024) and not service._leap(2026) and not service._leap(1900)


# ---------------------------------------------------------------------------
# supporters
# ---------------------------------------------------------------------------


def test_supporters_back_a_money_goal_and_received_pledges_fill_it(db, people):
    pat = people["pat"]
    fees = service.create_goal(
        db,
        pat,
        {
            "title": "Course fees",
            "measure": "money",
            "target": "1000",
            "currency": "INR",
        },
    )
    with pytest.raises(PermissionError):
        service.add_pledge(db, fees["id"], {"amount": "50"}, "1.1.1.1")
    with pytest.raises(ValueError, match="https"):
        service.set_support(
            db, pat, fees["id"], {"support": True, "pay_link": "paypal.me/pat"}
        )
    detail = service.set_support(
        db,
        pat,
        fees["id"],
        {
            "support": True,
            "pay_link": "https://paypal.me/pat",
            "pay_note": "UPI: pat@bank",
        },
    )
    assert (
        detail["support"]
        and detail["can_support"]
        and detail["pay_note"] == "UPI: pat@bank"
    )
    page = service.public_goal(db, fees["id"])
    assert (
        page["support"]["currency"] == "INR"
        and page["support"]["pay_link"] == "https://paypal.me/pat"
    )
    with pytest.raises(ValueError):
        service.add_pledge(db, fees["id"], {"amount": "abc"}, "1.1.1.1")
    with pytest.raises(ValueError):
        service.add_pledge(db, fees["id"], {"amount": "0"}, "1.1.1.1")
    with pytest.raises(ValueError, match="most one person"):
        service.add_pledge(db, fees["id"], {"amount": "100001"}, "1.1.1.1")
    made = service.add_pledge(
        db,
        fees["id"],
        {"amount": "250", "name": "Grandma", "message": "Study well"},
        "1.1.1.1",
    )
    assert made["amount_label"] == "₹250.00" and made["name"] == "Grandma"
    service.add_pledge(db, fees["id"], {"amount": "100"}, "2.2.2.2")
    # The page shows who backed it, never how much.
    page = service.public_goal(db, fees["id"])
    assert page["support"]["supporters"] == 2
    assert [b["name"] for b in page["support"]["backers"]] == ["A friend", "Grandma"]
    assert "250" not in json.dumps(page) and "25000" not in json.dumps(page)
    # The owner hears about it, once.
    pulse = journey_routes._pulse(db, pat, "")
    assert pulse["badge"] == 2
    assert any(
        a["title"] == "Grandma backed your goal" and "₹250.00" in a["body"]
        for a in pulse["alerts"]
    )
    detail = service.goal_detail(db, pat, fees["id"])
    assert detail["pledges"] == {
        "supporters": 2,
        "promised": 35000,
        "received": 0,
        "unread": 2,
        "other": 0,
        "currency": "INR",
    }
    assert journey_routes._pulse(db, pat, "")["badge"] == 0
    grandma = next(p for p in detail["pledge_list"] if p["name"] == "Grandma")
    # Received: the money is added to the goal as a check-in; marking it not received takes it back.
    detail = service.set_pledge(db, pat, grandma["id"], {"status": "received"})
    assert detail["current_amount"] == 25000 and detail["progress"] == 25
    assert detail["checkins"][0]["note"] == "Support from Grandma"
    assert (
        detail["pledges"]["received"] == 25000
        and detail["pledges"]["promised"] == 10000
    )
    assert (
        service.set_pledge(db, pat, grandma["id"], {"status": "received"})[
            "current_amount"
        ]
        == 25000
    )
    detail = service.set_pledge(db, pat, grandma["id"], {"status": "promised"})
    assert detail["current_amount"] == 0 and detail["checkins"] == []
    detail = service.set_pledge(db, pat, grandma["id"], {"status": "received"})
    other = next(p for p in detail["pledge_list"] if p["name"] == "A friend")
    detail = service.set_pledge(db, pat, other["id"], {"status": "cancelled"})
    assert detail["pledges"]["supporters"] == 1 and detail["pledges"]["promised"] == 0
    assert service.public_goal(db, fees["id"])["support"]["supporters"] == 1
    with pytest.raises(ValueError):
        service.set_pledge(db, pat, other["id"], {"status": "paid"})
    # Nobody else manages Pat's supporters.
    with pytest.raises(LookupError):
        service.set_pledge(db, people["sam"], grandma["id"], {"status": "cancelled"})
    with pytest.raises(LookupError):
        service.delete_pledge(db, people["sam"], grandma["id"])
    # Deleting a received pledge takes its money back out of the goal.
    service.delete_pledge(db, pat, grandma["id"])
    assert service.get_goal(db, pat, fees["id"])["current_amount"] == 0
    summary = service.overview(db, pat)["support"]
    assert summary["supporters"] == 0 and summary["goals"] == 1
    # Turning support off closes the form on the page; the goal's link stays.
    service.set_support(db, pat, fees["id"], {"support": False})
    assert service.public_goal(db, fees["id"])["support"] is None
    with pytest.raises(PermissionError):
        service.add_pledge(db, fees["id"], {"amount": "5"}, "3.3.3.3")


def test_a_sponsored_goal_tracks_pledges_without_changing_its_progress(db, people):
    pat = people["pat"]
    run = service.create_goal(
        db, pat, {"title": "Charity 10K", "steps": ["Train", "Run"]}
    )
    service.set_support(db, pat, run["id"], {"support": True})
    service.add_pledge(db, run["id"], {"amount": "20", "name": "Ravi"}, "1.1.1.1")
    detail = service.goal_detail(db, pat, run["id"])
    pledge_id = detail["pledge_list"][0]["id"]
    assert detail["pledge_list"][0]["currency"] == "USD"  # the account's currency
    detail = service.set_pledge(db, pat, pledge_id, {"status": "received"})
    assert detail["progress"] == 0 and detail["pledges"]["received"] == 2000
    for _ in range(service.PLEDGES_PER_VISITOR):
        service.add_pledge(db, run["id"], {"amount": "1"}, "9.9.9.9")
    with pytest.raises(ValueError, match="tomorrow"):
        service.add_pledge(db, run["id"], {"amount": "1"}, "9.9.9.9")
    # Deleting the goal removes its pledges.
    service.delete_goal(db, pat, run["id"])
    assert kit.count(db, "SELECT COUNT(*) FROM jr_pledges") == 0


def test_only_adults_ask_for_support(db, people):
    for who in ("mia", "tom"):
        goal = service.create_goal(db, people[who], {"title": "Save for a bike"})
        assert service.goal_detail(db, people[who], goal["id"])["can_support"] is False
        with pytest.raises(PermissionError):
            service.set_support(db, people[who], goal["id"], {"support": True})
    dropped = service.create_goal(db, people["pat"], {"title": "Old plan"})
    service.update_goal(db, people["pat"], dropped["id"], {"status": "dropped"})
    with pytest.raises(PermissionError):
        service.set_support(db, people["pat"], dropped["id"], {"support": True})


def test_a_habit_still_adds_to_its_goal_and_untick_takes_it_back(db, people):
    pat = people["pat"]
    save = service.create_goal(
        db, pat, {"title": "Save", "measure": "money", "target": "100"}
    )
    habit = service.save_habit(
        db, pat, {"title": "Put 5 aside", "goal_id": save["id"], "amount": "5"}
    )
    service.mark_habit(db, pat, habit["id"], {})
    assert service.get_goal(db, pat, save["id"])["current_amount"] == 500
    service.unmark_habit(db, pat, habit["id"], {})
    goal = service.get_goal(db, pat, save["id"])
    assert goal["current_amount"] == 0 and goal["checkins"] == []


# ---------------------------------------------------------------------------
# step ideas
# ---------------------------------------------------------------------------


def test_step_ideas_come_from_the_model_or_a_template_for_the_area():
    template = service.suggest_steps({"title": "Run a marathon", "category": "health"})
    assert template["source"] == "template" and template["steps"][0].startswith(
        "Talk to a doctor"
    )
    assert service.suggest_steps({"title": "Paint"})["steps"] == service._DEFAULT_STEPS
    with pytest.raises(ValueError):
        service.suggest_steps({"title": ""})
    model = _Model(
        json.dumps(
            {
                "steps": [
                    "1. Buy shoes",
                    "- Run 2 km",
                    "Run 2 km",
                    "  ",
                    "Run 5 km" + "!" * 200,
                ]
            }
        )
    )
    suite.set_gateway(model)
    answer = service.suggest_steps(
        {"title": "Run a 5K", "category": "health", "description": "For my heart"}
    )
    assert answer["source"] == "model"
    assert (
        answer["steps"][:2] == ["Buy shoes", "Run 2 km"] and len(answer["steps"]) == 3
    )
    assert len(answer["steps"][2]) == 160
    assert "Run a 5K" in model.prompts[0] and "For my heart" in model.prompts[0]
    # A reply that is not usable falls back to the template.
    suite.set_gateway(_Model('{"steps": ["Only one"]}'))
    assert (
        service.suggest_steps({"title": "x", "category": "money"})["source"]
        == "template"
    )
    suite.set_gateway(_Model("not json"))
    assert (
        service.suggest_steps({"title": "x", "category": "travel"})["steps"][0]
        == "Choose where and when"
    )


# ---------------------------------------------------------------------------
# fixes found using the app
# ---------------------------------------------------------------------------


def test_a_document_that_ran_out_long_ago_stops_reminding(db, people, tmp_path):
    from cryptography.fernet import Fernet

    from aira.airaapi.store import SecretStore

    pat = people["pat"]
    seal = SecretStore(Fernet.generate_key())
    now = service.today(db)
    service.save_document(
        db,
        pat,
        {
            "title": "Old passport",
            "expires_on": (now - timedelta(days=900)).isoformat(),
        },
        seal,
    )
    service.save_document(
        db,
        pat,
        {"title": "Visa", "expires_on": (now - timedelta(days=10)).isoformat()},
        seal,
    )
    service.save_document(
        db,
        pat,
        {"title": "Card", "expires_on": (now + timedelta(days=20)).isoformat()},
        seal,
    )
    due = service.reminders(db, pat)
    assert [d["title"] for d in due["documents"]] == ["Visa", "Card"]
    assert journey_routes._pulse(db, pat, "")["badge"] == 2
    # It is still listed as expired in the vault.
    assert [d["title"] for d in service.list_documents(db, pat, state="expired")] == [
        "Old passport",
        "Visa",
    ]


# ---------------------------------------------------------------------------
# the single-user server and the hosted build
# ---------------------------------------------------------------------------


def test_journal_supporters_and_step_ideas_on_the_local_build(env, api):
    kit.forget_schema()
    public.set_index(local._public_link_index)
    home = Household(env, api)
    pat = home.owner
    mia = home.add("Mia", age_group="child", pin="2468")
    # Journal: write, the one-tap mood, read back, private, export.
    assert pat.call("POST", "/api/journey/journal", {})[0] == 400
    pat.post("/api/journey/journal", {"mood": 4, "body": "Good day"})
    pat.post("/api/journey/journal/mood", {"mood": 5})
    data = pat.get("/api/journey/journal")
    assert data["entries"][0]["mood"] == 5 and data["entries"][0]["body"] == "Good day"
    assert data["summary"]["streak"] == 1 and len(data["moods"]) == 5
    assert mia.get("/api/journey/journal")["entries"] == []
    assert (
        mia.call("DELETE", f"/api/journey/journal/{data['entries'][0]['id']}")[0] == 404
    )
    status, csv = pat.call("GET", "/api/journey/export/journal", raw=True)
    assert status == 200 and csv.startswith(b"day,mood")
    cards = pat.get("/api/life/today")["cards"]
    assert any(c["title"] == "Your day" for c in cards)
    # Step ideas.
    ideas = pat.post(
        "/api/journey/goals/suggest", {"title": "Learn Spanish", "category": "learning"}
    )
    assert ideas["source"] == "template" and len(ideas["steps"]) >= 4
    # Supporters: turning it on makes the page; a visitor backs the goal; the owner marks it received.
    goal = pat.post(
        "/api/journey/goals",
        {"title": "Laptop for college", "measure": "money", "target": "800"},
        expect=201,
    )["goal"]
    detail = pat.call(
        "PATCH",
        f"/api/journey/goals/{goal['id']}/support",
        {"support": True, "pay_note": "Cash is fine"},
    )
    assert detail[0] == 200, detail
    detail = detail[1]["goal"]
    token = detail["link"]["token"]
    status, page = _anon(env, "GET", f"/api/public/{token}")
    assert status == 200 and page["actions"] == ["cheer", "pledge"]
    assert page["page"]["support"]["pay_note"] == "Cash is fine"
    status, body = _anon(
        env,
        "POST",
        f"/api/public/{token}/pledge",
        {"amount": "40", "name": "Aunt Lata"},
    )
    assert status == 201 and body["pledge"]["amount_label"] == "$40.00", body
    assert _anon(env, "POST", f"/api/public/{token}/pledge", {"amount": "-5"})[0] == 400
    assert pat.get("/api/life/pulse")["apps"]["journey"]["badge"] == 1
    pledge = pat.get(f"/api/journey/goals/{goal['id']}")["goal"]["pledge_list"][0]
    assert (
        mia.call(
            "PATCH", f"/api/journey/pledges/{pledge['id']}", {"status": "received"}
        )[0]
        == 404
    )
    goal = pat.call(
        "PATCH", f"/api/journey/pledges/{pledge['id']}", {"status": "received"}
    )[1]["goal"]
    assert goal["current_amount"] == 4000 and goal["progress"] == 5
    assert pat.call("DELETE", f"/api/journey/pledges/{pledge['id']}")[0] == 200
    # A child cannot ask for support.
    kid = mia.post("/api/journey/goals", {"title": "Bike"}, expect=201)["goal"]
    assert (
        mia.call("PATCH", f"/api/journey/goals/{kid['id']}/support", {"support": True})[
            0
        ]
        == 403
    )


def test_hosted_journal_and_supporters_stay_with_their_member(cloud):
    kit.forget_schema()
    org = build_org(cloud)
    owner = sign_in(cloud, org.owner)
    assert owner.call("GET", "/api/journey/journal")[0] == 402
    assert owner.call("POST", "/api/journey/goals/suggest", {"title": "x"})[0] == 402
    cloud.billing.subscribe_app(org.owner, "journey")
    assert (
        owner.call(
            "POST", "/api/journey/journal", {"mood": 3, "body": "Board meeting"}
        )[0]
        == 200
    )
    candidate = sign_in(cloud, org.candidate)
    assert candidate.call("GET", "/api/journey/journal")[1]["entries"] == []
    status, ideas = candidate.call(
        "POST",
        "/api/journey/goals/suggest",
        {"title": "Get a job", "category": "career"},
    )
    assert status == 200 and ideas["steps"][0] == "Write down the job or role you want"
    status, made = candidate.call(
        "POST",
        "/api/journey/goals",
        {"title": "Bootcamp fees", "measure": "money", "target": "500"},
    )
    assert status == 201, made
    goal_id = made["goal"]["id"]
    status, detail = candidate.call(
        "PATCH", f"/api/journey/goals/{goal_id}/support", {"support": True}
    )
    assert status == 200, detail
    token = detail["goal"]["link"]["token"]
    visitor = Browser(cloud)
    status, pledged = visitor.call(
        "POST", f"/api/public/{token}/pledge", {"amount": "25", "name": "Mentor"}
    )
    assert status == 201, pledged
    # The owner of the organization does not see or change a member's supporters.
    pledge_id = candidate.call("GET", f"/api/journey/goals/{goal_id}")[1]["goal"][
        "pledge_list"
    ][0]["id"]
    assert (
        owner.call(
            "PATCH", f"/api/journey/pledges/{pledge_id}", {"status": "received"}
        )[0]
        == 404
    )
    status, body = candidate.call(
        "PATCH", f"/api/journey/pledges/{pledge_id}", {"status": "received"}
    )
    assert status == 200 and body["goal"]["current_amount"] == 2500
    other = build_org(cloud, "Other Org", slug_email="other.test")
    cloud.billing.subscribe_app(other.owner, "journey")
    stranger = sign_in(cloud, other.owner)
    assert stranger.call("GET", "/api/journey/journal")[1]["entries"] == []
    assert stranger.call("DELETE", f"/api/journey/pledges/{pledge_id}")[0] == 404
