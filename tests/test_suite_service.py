"""Service (aira/suite/service): cases, SLA, replies through the outbox, CSAT, saved replies, the help centre, live
chat, the background pass, the single-user server and the hosted gateway."""

from __future__ import annotations

import json
import sqlite3
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone

import pytest
from cloud_support import Browser, build_org, sign_in

import auto_apply_assistant as local
import test_careers
from aira import suite
from aira.suite import background, kit, public
from aira.suite.service import desk, store

env = test_careers.env
api = test_careers.api
stub = test_careers.stub

S = "/api/service"
MONDAY_9 = datetime(2026, 10, 26, 9, 0, tzinfo=timezone.utc)  # a Monday


class Model:
    def __init__(self, reply):
        self.reply = reply

    def model(self, prompt):
        return self.reply


@pytest.fixture(autouse=True)
def no_live_model():
    """A real model may run on this machine: tests never call it."""
    previous = suite.gateway()
    suite.set_gateway(Model(None))
    yield
    suite.set_gateway(previous)


@pytest.fixture
def db(tmp_path):
    kit.forget_schema()
    public.set_index(public._MemoryIndex())
    connection = sqlite3.connect(tmp_path / "service.sqlite3")
    connection.row_factory = sqlite3.Row
    store.ensure(connection)
    yield connection
    connection.close()


@pytest.fixture
def local_index():
    public.set_index(local._public_link_index)


def web_case(db, **extra):
    raw = {
        "subject": "My order has not arrived",
        "message": "Order 42 is late.",
        "name": "Ana Silva",
        "email": "ana@example.com",
        **extra,
    }
    return desk.create_case(db, raw, channel="web", now=MONDAY_9)


# ---------------------------------------------------------------------------
# unit: SLA, statuses, replies, CSAT, merge, macros
# ---------------------------------------------------------------------------


def test_due_times_follow_the_priority_and_business_hours(db):
    first, resolve = desk.due_times(db, MONDAY_9, "urgent")
    assert first == "2026-10-26T10:00:00Z" and resolve == "2026-10-26T17:00:00Z"
    first, _ = desk.due_times(db, MONDAY_9, "low")
    assert first == "2026-10-27T09:00:00Z"
    store.save_settings(
        db,
        {
            "business_hours": True,
            "days": [0, 1, 2, 3, 4],
            "start": "09:00",
            "end": "17:00",
        },
    )
    friday_4pm = datetime(2026, 10, 30, 16, 0, tzinfo=timezone.utc)
    first, _ = desk.due_times(
        db, friday_4pm, "high"
    )  # 4 working hours: 1 on Friday, 3 on Monday
    assert first == "2026-11-02T12:00:00Z"
    saturday = datetime(2026, 10, 31, 10, 0, tzinfo=timezone.utc)
    assert desk.due_times(db, saturday, "urgent")[0] == "2026-11-02T10:00:00Z"
    with pytest.raises(ValueError):
        store.save_settings(db, {"sla": {"normal": {"first": 600, "resolve": 60}}})
    with pytest.raises(ValueError):
        store.save_settings(db, {"start": "18:00", "end": "09:00"})


def test_cases_validate_and_get_numbers_and_an_acknowledgement(db):
    with pytest.raises(ValueError, match="email"):
        desk.create_case(db, {"subject": "Hi", "message": "x"}, channel="web")
    with pytest.raises(ValueError):
        desk.create_case(db, {"subject": ""}, channel="manual")
    one = web_case(db)
    two = web_case(db, subject="Second")
    assert one["reference"] == "#1001" and two["reference"] == "#1002"
    assert (
        one["status"] == "new"
        and one["priority"] == "normal"
        and one["channel"] == "web"
    )
    assert [m["kind"] for m in kit.outbox(db)] == ["case_received", "case_received"]
    assert public.link_for(db, "service", "case", one["id"])["path"].startswith("/p/")


def test_status_rules_overdue_and_due_soon(db):
    case = web_case(db, priority="urgent")
    assert not desk.is_overdue(case, MONDAY_9 + timedelta(minutes=30))
    assert desk.due_soon(case, MONDAY_9 + timedelta(minutes=30))
    assert desk.is_overdue(
        case, MONDAY_9 + timedelta(hours=2)
    )  # no first reply within an hour
    desk.update_case(db, case["id"], {"status": "solved"})
    with pytest.raises(ValueError):
        desk.update_case(db, case["id"], {"status": "new"})
    desk.update_case(db, case["id"], {"status": "closed"})
    with pytest.raises(ValueError, match="closed"):
        desk.update_case(db, case["id"], {"status": "open"})
    with pytest.raises(ValueError, match="closed"):
        desk.customer_reply(db, case["id"], {"body": "Hello?"})
    assert not desk.is_overdue(
        desk.get_case(db, case["id"]), MONDAY_9 + timedelta(days=9)
    )


def test_replies_go_through_the_outbox_and_notes_stay_inside(db):
    case = web_case(db)
    desk.reply(
        db,
        case["id"],
        {"body": "Checking our stock.", "internal": True},
        author="sam@biz.example",
    )
    assert (
        desk.get_case(db, case["id"])["status"] == "open"
        and not desk.get_case(db, case["id"])["first_responded_at"]
    )
    result = desk.reply(
        db,
        case["id"],
        {"body": "It ships tomorrow.", "attachments": ["https://biz.example/track/42"]},
        author="sam@biz.example",
    )
    assert (
        result["case"]["status"] == "waiting" and result["case"]["first_responded_at"]
    )
    sent = [m for m in kit.outbox(db) if m["kind"] == "case_reply"]
    assert (
        len(sent) == 1
        and "It ships tomorrow." in sent[0]["body"]
        and "/p/" in sent[0]["body"]
    )
    assert "Checking our stock" not in sent[0]["body"]
    with pytest.raises(ValueError):
        desk.reply(
            db, case["id"], {"body": "x", "attachments": ["http://plain.example/a"]}
        )
    page = desk.customer_reply(db, case["id"], {"body": "Thanks!"})
    assert page["status"] == "open"
    public_thread = store.messages(db, case["id"], public_only=True)
    assert all(not m["internal"] for m in public_thread) and len(
        store.messages(db, case["id"])
    ) > len(public_thread)


def test_solving_asks_for_a_rating_once_and_csat_is_one_time(db):
    case = web_case(db)
    with pytest.raises(ValueError):
        desk.rate(db, case["id"], {"score": 5})
    desk.reply(db, case["id"], {"body": "Done!", "status_after": "solved"})
    assert [m["kind"] for m in kit.outbox(db)].count("csat_request") == 1
    desk.update_case(db, case["id"], {"status": "open"})
    desk.update_case(db, case["id"], {"status": "solved"})
    assert [m["kind"] for m in kit.outbox(db)].count("csat_request") == 1
    with pytest.raises(ValueError):
        desk.rate(db, case["id"], {"score": 9})
    rated = desk.rate(db, case["id"], {"score": 4, "comment": "Quick help"})
    assert rated["csat_score"] == 4
    with pytest.raises(ValueError, match="already"):
        desk.rate(db, case["id"], {"score": 5})
    assert desk.stats(db)["csat_average"] == 4.0


def test_merging_moves_the_conversation(db):
    first = web_case(db)
    duplicate = web_case(db, subject="Order 42 late again")
    merged = desk.merge(db, duplicate["id"], first["id"])
    assert merged["id"] == first["id"]
    source = store.get_case(db, duplicate["id"])
    assert source["status"] == "closed" and source["merged_into"] == first["id"]
    assert any(
        "merged into this case" in m["body"] for m in store.messages(db, first["id"])
    )
    with pytest.raises(ValueError):
        desk.merge(db, duplicate["id"], first["id"])
    with pytest.raises(ValueError):
        desk.merge(db, first["id"], first["id"])
    assert duplicate["id"] not in {c["id"] for c in desk.list_cases(db, view="all")}


def test_views_search_and_saved_replies(db):
    mine = web_case(db, priority="high")
    desk.update_case(
        db, mine["id"], {"assignee": "sam@biz.example", "tags": "vip, shipping"}
    )
    other = web_case(
        db, subject="Refund question", name="Ben Ode", email="ben@example.com"
    )
    assert [
        c["id"] for c in desk.list_cases(db, view="mine", me="sam@biz.example")
    ] == [mine["id"]]
    assert [c["id"] for c in desk.list_cases(db, view="unassigned")] == [other["id"]]
    assert [c["id"] for c in desk.list_cases(db, q="refund")] == [other["id"]]
    assert [c["id"] for c in desk.list_cases(db, q="#1001")] == [mine["id"]]
    assert [c["id"] for c in desk.list_cases(db, tag="vip")] == [mine["id"]]
    macro = desk.save_macro(
        db,
        {"title": "Shipping", "body": "Hi {customer}, your case {case} is on its way."},
    )
    assert (
        desk.use_macro(db, macro["id"], mine["id"])
        == "Hi Ana, your case #1001 is on its way."
    )
    assert desk.list_macros(db)[0]["uses"] == 1
    suggestion = desk.suggest_reply(db, mine["id"])
    assert suggestion["source"] == "template" and "Ana" in suggestion["reply"]
    rows = kit.csv_bytes(
        [{**c, "tags": ", ".join(c["tags"])} for c in desk.list_cases(db, view="all")],
        desk.CSV_COLUMNS,
    )
    assert b"#1001" in rows and b"vip, shipping" in rows


def test_the_help_centre_searches_counts_and_votes(db):
    category = desk.save_category(db, {"name": "Orders"})
    with pytest.raises(ValueError):
        desk.save_article(db, {"title": "Empty", "status": "published"})
    article = desk.save_article(
        db,
        {
            "title": "Where is my order?",
            "body": "Track it from the link in your email.",
            "status": "published",
            "category_id": category["id"],
        },
    )
    draft = desk.save_article(db, {"title": "Secret draft", "body": "Not yet."})
    assert [a["id"] for a in desk.list_articles(db, published_only=True)] == [
        article["id"]
    ]
    assert [
        a["id"] for a in desk.list_articles(db, published_only=True, q="track")
    ] == [article["id"]]
    with pytest.raises(LookupError):
        desk.read_article(db, draft["id"])
    assert desk.read_article(db, article["id"])["body"].startswith("Track")
    assert desk.vote(db, article["id"], "1.2.3.4", True) == {"recorded": True}
    assert desk.vote(db, article["id"], "1.2.3.4", True) == {"recorded": False}
    desk.vote(db, article["id"], "1.2.3.4", False)
    row = store.get_article(db, article["id"])
    assert (row["views"], row["helpful_yes"], row["helpful_no"]) == (1, 0, 1)


def test_live_chat_polls_and_turns_into_a_case(db):
    started = desk.start_chat(
        db,
        {"name": "Cam", "email": "cam@example.com", "message": "Are you open today?"},
    )
    chat_id, secret = started["chat_id"], started["secret"]
    with pytest.raises(LookupError):
        desk.visitor_poll(db, {"chat_id": chat_id, "secret": "wrong"})
    first = desk.visitor_poll(db, {"chat_id": chat_id, "secret": secret})
    assert [m["from"] for m in first["messages"]] == ["you"]
    desk.agent_chat_reply(db, chat_id, "Yes, until 6 pm.", author="sam@biz.example")
    newer = desk.visitor_poll(
        db, {"chat_id": chat_id, "secret": secret, "after": first["cursor"]}
    )
    assert [m["body"] for m in newer["messages"]] == ["Yes, until 6 pm."] and newer[
        "status"
    ] == "active"
    case = desk.chat_to_case(db, chat_id)
    assert case["channel"] == "chat" and "Are you open today?" in case["description"]
    assert desk.chat_to_case(db, chat_id)["id"] == case["id"]
    desk.end_chat(db, chat_id)
    with pytest.raises(ValueError):
        desk.visitor_send(
            db, {"chat_id": chat_id, "secret": secret, "body": "One more"}
        )
    store.save_settings(db, {"chat_enabled": False})
    assert not desk.chat_online(db)
    with pytest.raises(ValueError, match="closed"):
        desk.start_chat(db, {"name": "Dee", "message": "Hello"})


def test_the_background_pass_flags_overdue_and_closes_solved_cases(db):
    case = web_case(db, priority="urgent")
    result = desk.tick(db, MONDAY_9 + timedelta(hours=3))
    assert result["overdue"] == 1 and store.get_case(db, case["id"])["overdue"]
    solved = web_case(db, subject="Old one")
    desk.update_case(db, solved["id"], {"status": "solved"}, now=MONDAY_9)
    db.execute(
        "UPDATE sv_cases SET solved_at = ? WHERE id = ?",
        ("2026-10-26T09:00:00Z", solved["id"]),
    )
    result = desk.tick(db, MONDAY_9 + timedelta(days=8))
    assert (
        result["closed"] == 1 and store.get_case(db, solved["id"])["status"] == "closed"
    )
    assert [m["kind"] for m in kit.outbox(db)].count("csat_request") == 1
    assert "service" in background.TASKS


def test_hub_checklist_and_summary(db):
    steps = {s["id"]: s["done"] for s in desk.checklist(db)}
    assert not any(steps.values())
    kit.save_business(db, {"name": "Glow Studio"})
    desk.save_article(db, {"title": "Hours", "body": "9 to 6.", "status": "published"})
    public.create_link(db, "service", "help_center", "help", title="Help centre")
    case = web_case(db)
    desk.update_case(db, case["id"], {"status": "solved"})
    assert all(s["done"] for s in desk.checklist(db))
    cards = {c["label"]: c["value"] for c in desk.summary(db)["cards"]}
    assert cards["Open cases"] == 0


# ---------------------------------------------------------------------------
# the single-user server
# ---------------------------------------------------------------------------


def _anonymous(env, method, path, body=None):
    data = json.dumps(body).encode() if body is not None else None
    request = urllib.request.Request(env.base + path, data=data, method=method)
    request.add_header("Content-Type", "application/json")
    request.add_header("X-Requested-With", "aira")
    request.add_header("Origin", env.base)
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            return response.status, json.loads(response.read() or b"{}")
    except urllib.error.HTTPError as error:
        return error.code, json.loads(error.read() or b"{}")


def test_a_customer_gets_help_from_the_help_centre_to_a_rating(env, api, local_index):
    kit.forget_schema()
    api.post("/api/suite/business", {"name": "Glow Studio", "email": "hi@glow.example"})
    api.post(
        f"{S}/articles",
        {
            "title": "Opening hours",
            "body": "We are open 9 to 6.",
            "status": "published",
        },
        expect=201,
    )
    internal = api.post(
        f"{S}/articles", {"title": "Staff only", "body": "Draft."}, expect=201
    )["article"]
    token = api.post(f"{S}/help/link")["link"]["token"]

    status, page = _anonymous(env, "GET", f"/api/public/{token}")
    assert status == 200 and [a["title"] for a in page["page"]["articles"]] == [
        "Opening hours"
    ]
    status, found = _anonymous(
        env, "POST", f"/api/public/{token}/search", {"q": "open"}
    )
    assert status == 200 and len(found["articles"]) == 1
    status, _ = _anonymous(
        env, "POST", f"/api/public/{token}/article", {"id": internal["id"]}
    )
    assert status == 404
    status, created = _anonymous(
        env,
        "POST",
        f"/api/public/{token}/contact",
        {
            "name": "Ana Silva",
            "email": "ana@example.com",
            "subject": "Booking",
            "message": "Can I come at 7?",
        },
    )
    assert (
        status == 201
        and created["reference"] == "#1001"
        and created["path"].startswith("/p/")
    )
    case_token = created["path"].rsplit("/", 1)[1]

    listing = api.get(f"{S}/cases")
    case_id = listing["cases"][0]["id"]
    assert listing["views"]["unassigned"] == 1
    api.post(
        f"{S}/cases/{case_id}/reply",
        {"body": "Internal: check with Sam.", "internal": True},
    )
    api.post(f"{S}/cases/{case_id}/reply", {"body": "We close at 6, but 5:30 works."})
    outbox = api.get("/api/suite/outbox")["messages"]
    reply_mail = next(m for m in outbox if m["kind"] == "case_reply")
    assert f"{env.base}/p/{case_token}" in reply_mail["body"]

    status, case_page = _anonymous(env, "GET", f"/api/public/{case_token}")
    thread = case_page["page"]["thread"]
    assert [t["from"] for t in thread] == ["you", "us"] and all(
        "Internal" not in t["body"] for t in thread
    )
    status, after = _anonymous(
        env, "POST", f"/api/public/{case_token}/reply", {"body": "5:30 is perfect."}
    )
    assert status == 200 and after["status"] == "open"
    api.ok("PATCH", f"{S}/cases/{case_id}", {"status": "solved"})
    status, rated = _anonymous(
        env, "POST", f"/api/public/{case_token}/rate", {"score": 5, "comment": "Lovely"}
    )
    assert status == 200 and rated["rated"] == 5 and not rated["can_rate"]
    assert (
        _anonymous(env, "POST", f"/api/public/{case_token}/rate", {"score": 1})[0]
        == 400
    )
    overview = api.get(f"{S}/overview")
    assert (
        overview["stats"]["csat_average"] == 5.0
        and overview["stats"]["first_response_minutes"] is not None
    )
    home = api.get("/api/suite/home")
    assert any(a["id"] == "service" and a["cards"] for a in home["apps"])


def test_a_live_chat_round_trip_on_the_local_build(env, api, local_index):
    kit.forget_schema()
    token = api.post(f"{S}/chat/link")["link"]["token"]
    status, page = _anonymous(env, "GET", f"/api/public/{token}")
    assert status == 200 and page["page"]["online"] is True
    status, started = _anonymous(
        env,
        "POST",
        f"/api/public/{token}/start",
        {"name": "Cam", "email": "cam@example.com", "message": "Hello?"},
    )
    assert status == 201
    chats = api.get(f"{S}/chats")["chats"]
    assert chats[0]["waiting_for_us"] is True
    api.post(f"{S}/chats/{chats[0]['id']}/reply", {"body": "Hi Cam, how can we help?"})
    status, polled = _anonymous(
        env,
        "POST",
        f"/api/public/{token}/poll",
        {
            "chat_id": started["chat_id"],
            "secret": started["secret"],
            "after": started["cursor"],
        },
    )
    assert [m["body"] for m in polled["messages"]] == ["Hi Cam, how can we help?"]
    assert (
        _anonymous(
            env,
            "POST",
            f"/api/public/{token}/poll",
            {"chat_id": started["chat_id"], "secret": "nope"},
        )[0]
        == 404
    )
    # The window polls with a read-only GET query; the secret rides in a header, never the address.
    asked = urllib.request.Request(
        env.base + f"/api/public/{token}/messages?chat_id={started['chat_id']}&after=0"
    )
    asked.add_header("X-Requested-With", "aira")
    asked.add_header("Aira-Secret", started["secret"])
    with urllib.request.urlopen(asked, timeout=30) as response:
        queried = json.loads(response.read())
    assert len(queried["messages"]) == 2 and queried["status"] == "active"
    assert (
        _anonymous(
            env,
            "GET",
            f"/api/public/{token}/messages?chat_id={started['chat_id']}&secret={started['secret']}",
        )[0]
        == 404
    )
    assert _anonymous(env, "GET", f"/api/public/{token}/nothing")[0] == 404
    assert page["queries"] == ["messages"]
    case = api.post(f"{S}/chats/{chats[0]['id']}/case", expect=201)["case"]
    assert case["channel"] == "chat"
    request = urllib.request.Request(env.base + f"{S}/cases?format=csv&view=all")
    request.add_header("X-Requested-With", "aira")
    with api.opener.open(request, timeout=30) as response:
        exported = response.read().decode("utf-8")
    assert (
        response.headers["Content-Type"].startswith("text/csv") and "#1001" in exported
    )


def test_service_routes_need_a_session(env):
    assert _anonymous(env, "GET", f"{S}/overview")[0] == 401


# ---------------------------------------------------------------------------
# the hosted build
# ---------------------------------------------------------------------------


def test_hosted_service_needs_the_app_and_the_right_role(cloud):
    kit.forget_schema()
    org = build_org(cloud)
    owner = sign_in(cloud, org.owner)
    status, data = owner.call("GET", f"{S}/overview")
    assert status == 402 and data["code"] == "app_required"
    cloud.billing.subscribe_app(org.owner, "service")
    assert owner.call("GET", f"{S}/overview")[0] == 200
    for who in ("admin", "coach"):
        status, _ = sign_in(cloud, getattr(org, who)).call(
            "POST", f"{S}/cases", {"subject": f"From {who}"}
        )
        assert status == 201, who
    auditor = sign_in(cloud, org.auditor)
    assert auditor.call("GET", f"{S}/cases")[0] == 200
    assert auditor.call("POST", f"{S}/cases", {"subject": "Nope"})[0] == 403
    assert sign_in(cloud, org.candidate).call("GET", f"{S}/overview")[0] == 403

    owner.call(
        "POST",
        f"{S}/articles",
        {"title": "Returns", "body": "30 days.", "status": "published"},
    )
    status, shared = owner.call("POST", f"{S}/help/link", {})
    token = shared["link"]["token"]
    visitor = Browser(cloud)
    status, page = visitor.call("GET", f"/api/public/{token}")
    assert status == 200 and page["page"]["articles"][0]["title"] == "Returns"
    status, created = visitor.call(
        "POST",
        f"/api/public/{token}/contact",
        {"name": "Ana", "email": "ana@example.com", "message": "Hi"},
    )
    assert status == 201, created
    status, listing = owner.call("GET", f"{S}/cases?view=all")
    assert {c["subject"] for c in listing["cases"]} == {
        "From admin",
        "From coach",
        "A question",
    }

    other = build_org(cloud, "Other Org", slug_email="other.test")
    cloud.billing.subscribe_app(other.owner, "service")
    status, theirs = sign_in(cloud, other.owner).call("GET", f"{S}/cases?view=all")
    assert status == 200 and theirs["cases"] == []
