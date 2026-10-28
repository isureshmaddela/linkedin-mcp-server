"""Service: paid help (services in the help centre, charges on a case, payments into the revenue ledger), customers
with their history, answers suggested while a customer writes, bulk actions, deleting a case, the team reports, and
regression tests for bugs found using the app in a browser."""

# ruff: noqa: F811
from __future__ import annotations

import json
import urllib.request
from datetime import timedelta

import pytest
from cloud_support import Browser, build_org, sign_in

import test_careers
from aira.suite import kit, public
from aira.suite.service import desk, store
from test_suite_service import (  # noqa: F401
    MONDAY_9,
    S,
    _anonymous,
    db,
    local_index,
    no_live_model,
    web_case,
)

env = test_careers.env
api = test_careers.api
stub = test_careers.stub


def offer(db, **extra):
    return desk.save_offer(
        db,
        {
            "title": "One-hour setup call",
            "description": "We set it up with you.",
            "price_cents": 4500,
            **extra,
        },
    )


def revenue_cents(db):
    return kit.revenue(db, app="service")["total_cents"]


# ---------------------------------------------------------------------------
# paid help
# ---------------------------------------------------------------------------


def test_paid_services_validate_and_list_what_they_earned(db):
    with pytest.raises(ValueError, match="price"):
        desk.save_offer(db, {"title": "Setup"})
    with pytest.raises(ValueError):
        desk.save_offer(db, {"title": "Setup", "price_cents": 0})
    with pytest.raises(ValueError):
        desk.save_offer(db, {"title": "", "price": "10"})
    typed = desk.save_offer(db, {"title": "Repair", "price": "12.50"})
    assert typed["price_cents"] == 1250 and typed["active"] is True
    setup = offer(db)
    hidden = desk.save_offer(db, {"active": False}, typed["id"])
    assert hidden["active"] is False and hidden["price_cents"] == 1250
    assert [o["id"] for o in desk.list_offers(db, active_only=True)] == [setup["id"]]
    case = web_case(db)
    charge = desk.add_charge(db, case["id"], {"offer_id": setup["id"]})
    desk.set_charge_paid(db, charge["id"], True)
    listed = {o["id"]: o for o in desk.list_offers(db)}
    assert (
        listed[setup["id"]]["sold"] == 1 and listed[setup["id"]]["earned_cents"] == 4500
    )
    desk.delete_offer(db, setup["id"])
    assert store.get_charge(db, charge["id"])["label"] == "One-hour setup call"


def test_a_charge_is_paid_once_into_revenue_and_can_be_undone(db):
    kit.save_business(db, {"name": "Glow", "payment_link": "https://pay.example/glow"})
    case = web_case(db)
    with pytest.raises(ValueError, match="for"):
        desk.add_charge(db, case["id"], {"amount_cents": 500})
    with pytest.raises(ValueError, match="amount"):
        desk.add_charge(db, case["id"], {"label": "Repair"})
    charge = desk.add_charge(db, case["id"], {"label": "Repair visit", "amount": "60"})
    assert charge["status"] == "due" and charge["amount_cents"] == 6000
    mail = [m for m in kit.outbox(db) if m["kind"] == "case_charge"]
    assert len(mail) == 1 and "$60.00" in mail[0]["body"]
    assert "https://pay.example/glow" in mail[0]["body"] and "#1001" in mail[0]["body"]
    assert desk.public_charges(db, case["id"])["due_cents"] == 6000

    desk.set_charge_paid(db, charge["id"], True)
    desk.set_charge_paid(db, charge["id"], True)  # twice: still one payment
    assert revenue_cents(db) == 6000
    assert [m["kind"] for m in kit.outbox(db)].count("payment_receipt") == 1
    assert desk.public_charges(db, case["id"])["due_cents"] == 0
    with pytest.raises(ValueError, match="unpaid"):
        desk.delete_charge(db, charge["id"])
    with pytest.raises(ValueError, match="unpaid"):
        desk.delete_case(db, case["id"])

    desk.set_charge_paid(db, charge["id"], False)
    assert revenue_cents(db) == 0
    desk.set_charge_paid(db, charge["id"], True)
    assert revenue_cents(db) == 6000  # the same line comes back, never counted twice
    assert desk.stats(db)["earned_this_month_cents"] == 6000
    cards = {c["label"]: c["value"] for c in desk.summary(db)["cards"]}
    assert cards["Paid help this month"] == "$60.00"
    notes = [m["body"] for m in store.messages(db, case["id"]) if m["internal"]]
    assert any("Charge added" in n for n in notes)
    assert all(
        "Charge" not in m["body"]
        for m in store.messages(db, case["id"], public_only=True)
    )


def test_no_charges_on_closed_or_merged_cases(db):
    one, two = web_case(db), web_case(db, subject="Again")
    desk.merge(db, two["id"], one["id"])
    with pytest.raises(ValueError, match="merged"):
        desk.add_charge(db, two["id"], {"label": "x", "amount_cents": 100})
    desk.update_case(db, one["id"], {"status": "closed"})
    with pytest.raises(ValueError, match="closed"):
        desk.add_charge(db, one["id"], {"label": "x", "amount_cents": 100})


def test_a_customer_asks_for_a_paid_service(db):
    setup = offer(db)
    with pytest.raises(ValueError, match="email"):
        desk.request_offer(db, {"offer_id": setup["id"], "name": "Ana"})
    case = desk.request_offer(
        db, {"offer_id": setup["id"], "name": "Ana Silva", "email": "ana@example.com"}
    )
    assert case["subject"] == "One-hour setup call" and case["tags"] == ["paid help"]
    assert [c["amount_cents"] for c in store.charges(db, case["id"])] == [4500]
    received = [m for m in kit.outbox(db) if m["kind"] == "case_received"]
    assert len(received) == 1 and "$45.00" in received[0]["body"]
    assert not [m for m in kit.outbox(db) if m["kind"] == "case_charge"]
    desk.save_offer(db, {"active": False}, setup["id"])
    with pytest.raises(LookupError):
        desk.request_offer(db, {"offer_id": setup["id"], "email": "b@example.com"})


# ---------------------------------------------------------------------------
# customers, suggestions, bulk, delete, reports
# ---------------------------------------------------------------------------


def test_customers_group_their_requests_and_money(db):
    first = web_case(db)
    web_case(db, subject="Second", email="ANA@example.com")
    desk.create_case(db, {"subject": "Walk-in", "name": "Joe Bloggs"}, channel="manual")
    desk.create_case(db, {"subject": "Nobody"}, channel="manual")
    charge = desk.add_charge(db, first["id"], {"label": "Fix", "amount_cents": 2000})
    desk.set_charge_paid(db, charge["id"], True)
    desk.update_case(db, first["id"], {"status": "solved"})
    desk.rate(db, first["id"], {"score": 5})
    people = {p["key"]: p for p in desk.customers(db)}
    assert set(people) == {"ana@example.com", "joe bloggs"}
    ana = people["ana@example.com"]
    assert (ana["cases"], ana["open"], ana["paid_cents"], ana["csat_average"]) == (
        2,
        1,
        2000,
        5.0,
    )
    assert [p["key"] for p in desk.customers(db, q="joe")] == ["joe bloggs"]
    assert len(desk.customer_cases(db, "ana@example.com")) == 2
    history = desk.case_detail(db, first["id"])["history"]
    assert [c["subject"] for c in history] == ["Second"]


def test_articles_are_suggested_for_what_a_customer_wrote(db):
    desk.save_article(
        db,
        {
            "title": "When will my order arrive?",
            "body": "Three working days.",
            "status": "published",
        },
    )
    desk.save_article(
        db,
        {
            "title": "Returns",
            "body": "Send the item back within 30 days.",
            "status": "published",
        },
    )
    desk.save_article(db, {"title": "Order secrets", "body": "Draft."})
    found = desk.suggest_articles(db, "Where is my order? It has not arrived")
    assert [a["title"] for a in found] == ["When will my order arrive?"]
    assert desk.suggest_articles(db, "please help thanks") == []
    case = web_case(db, subject="I want to send an item back", message="Within days?")
    assert [a["title"] for a in desk.case_detail(db, case["id"])["answers"]] == [
        "Returns"
    ]


def test_bulk_actions_apply_and_skip_with_a_reason(db):
    one, two, three = web_case(db), web_case(db, subject="B"), web_case(db, subject="C")
    desk.update_case(db, three["id"], {"status": "closed"})
    with pytest.raises(ValueError):
        desk.bulk(db, [], "solve")
    with pytest.raises(ValueError):
        desk.bulk(db, [one["id"]], "explode")
    done = desk.bulk(
        db, [one["id"], two["id"], "not-an-id"], "assign", assignee="sam@biz.example"
    )
    assert done == {"done": 2, "skipped": []}
    assert desk.get_case(db, two["id"])["assignee"] == "sam@biz.example"
    result = desk.bulk(db, [one["id"], three["id"]], "solve")
    assert result["done"] == 1 and result["skipped"][0]["id"] == three["id"]
    paid = desk.add_charge(db, two["id"], {"label": "x", "amount_cents": 100})
    desk.set_charge_paid(db, paid["id"], True)
    result = desk.bulk(db, [two["id"], three["id"]], "delete")
    assert result["done"] == 1 and "unpaid" in result["skipped"][0]["reason"]
    with pytest.raises(LookupError):
        store.get_case(db, three["id"])


def test_deleting_a_case_removes_its_messages_link_and_merges(db):
    keep, duplicate = web_case(db), web_case(db, subject="Again")
    desk.merge(db, duplicate["id"], keep["id"])
    path = public.link_for(db, "service", "case", keep["id"])["path"]
    assert path
    desk.delete_case(db, keep["id"])
    assert public.link_for(db, "service", "case", keep["id"]) is None
    assert store.messages(db, keep["id"]) == []
    # The duplicate's conversation lived in the kept case: the duplicate goes with it (see
    # test_suite_service_fixes.py), never back as an empty closed case.
    with pytest.raises(LookupError):
        store.get_case(db, duplicate["id"])
    assert any(a["kind"] == "deleted" for a in kit.activity(db, app="service"))


def test_reports_show_the_team_due_times_ratings_topics_and_money(db):
    fast = web_case(db, tags="delivery")
    slow = web_case(db, subject="Slow", tags="delivery, refund")
    desk.update_case(db, fast["id"], {"assignee": "sam@biz.example"})
    desk.reply(
        db,
        fast["id"],
        {"body": "On it", "status_after": "solved"},
        now=MONDAY_9 + timedelta(minutes=30),
    )
    desk.reply(db, slow["id"], {"body": "Sorry"}, now=MONDAY_9 + timedelta(days=1))
    desk.rate(db, fast["id"], {"score": 4, "comment": "Quick"})
    report = desk.reports(db, now=MONDAY_9 + timedelta(days=1))
    sam = next(m for m in report["team"] if m["assignee"] == "sam@biz.example")
    assert (sam["solved"], sam["first_response_minutes"], sam["csat_average"]) == (
        1,
        30,
        4.0,
    )
    assert (
        report["sla"]["first_reply_percent"] == 50
        and report["sla"]["solution_percent"] == 100
    )
    assert report["ratings"][0]["comment"] == "Quick"
    assert report["topics"][0] == {"tag": "delivery", "cases": 2}
    assert report["money"]["total_cents"] == 0


# ---------------------------------------------------------------------------
# regressions found in the browser
# ---------------------------------------------------------------------------


def test_chat_visitors_see_the_business_name_never_a_team_email(db):
    kit.save_business(db, {"name": "Glow Studio"})
    started = desk.start_chat(
        db, {"name": "Cam", "email": "cam@example.com", "message": "Open today?"}
    )
    desk.agent_chat_reply(
        db, started["chat_id"], "Yes, until 6.", author="sam@biz.example"
    )
    polled = desk.visitor_poll(
        db, {"chat_id": started["chat_id"], "secret": started["secret"]}
    )
    agent = [m for m in polled["messages"] if m["from"] == "agent"]
    assert agent[0]["name"] == "Glow Studio"
    assert "sam@biz.example" not in json.dumps(polled)


def test_a_chat_made_into_a_case_keeps_the_conversation_and_the_first_reply(db):
    started = desk.start_chat(
        db, {"name": "Cam", "email": "cam@example.com", "message": "Open today?"}
    )
    desk.agent_chat_reply(
        db, started["chat_id"], "Yes, until 6.", author="sam@biz.example"
    )
    case = desk.chat_to_case(db, started["chat_id"])
    assert (
        case["first_responded_at"] and case["status"] == "open" and not case["overdue"]
    )
    thread = store.messages(db, case["id"], public_only=True)
    assert [(m["author_kind"], m["body"]) for m in thread] == [
        ("customer", "Open today?"),
        ("agent", "Yes, until 6."),
    ]
    assert case["description"] == "Open today?"


def test_the_customer_of_a_merged_duplicate_can_rate_the_kept_case(db):
    keep, duplicate = web_case(db), web_case(db, subject="Again")
    desk.merge(db, duplicate["id"], keep["id"])
    desk.update_case(db, keep["id"], {"status": "solved"})
    rated = desk.rate(db, duplicate["id"], {"score": 5})
    assert rated["id"] == keep["id"] and rated["csat_score"] == 5


def test_search_finds_a_case_by_phone(db):
    case = web_case(db, phone="+44 20 7946 0000")
    assert [c["id"] for c in desk.list_cases(db, q="7946")] == [case["id"]]


# ---------------------------------------------------------------------------
# the single-user server
# ---------------------------------------------------------------------------


def _get_csv(env, api, path):
    request = urllib.request.Request(env.base + path)
    request.add_header("X-Requested-With", "aira")
    with api.opener.open(request, timeout=30) as response:
        return response.read().decode("utf-8")


def test_paid_help_from_the_help_centre_to_revenue_on_the_local_build(
    env, api, local_index
):
    kit.forget_schema()
    api.post(
        "/api/suite/business",
        {"name": "Glow Studio", "payment_link": "https://pay.example/glow"},
    )
    created = api.post(
        f"{S}/offers", {"title": "Setup call", "price_cents": 4500}, expect=201
    )["offer"]
    api.post(
        f"{S}/articles",
        {
            "title": "When will my order arrive?",
            "body": "Three days.",
            "status": "published",
        },
        expect=201,
    )
    token = api.post(f"{S}/help/link")["link"]["token"]
    status, page = _anonymous(env, "GET", f"/api/public/{token}")
    assert page["page"]["offers"][0]["price_cents"] == 4500
    assert sorted(page["queries"]) == ["answers"]

    asked = urllib.request.Request(
        env.base + f"/api/public/{token}/answers?q=where+is+my+order"
    )
    asked.add_header("X-Requested-With", "aira")
    with urllib.request.urlopen(asked, timeout=30) as response:
        assert (
            json.loads(response.read())["articles"][0]["title"]
            == "When will my order arrive?"
        )

    status, bot = _anonymous(
        env,
        "POST",
        f"/api/public/{token}/request",
        {"offer_id": created["id"], "email": "x@y.z", "website": "spam"},
    )
    assert status == 201 and bot["reference"] == ""
    status, _ = _anonymous(
        env,
        "POST",
        f"/api/public/{token}/request",
        {"offer_id": created["id"], "name": "Ana"},
    )
    assert status == 400
    status, sent = _anonymous(
        env,
        "POST",
        f"/api/public/{token}/request",
        {"offer_id": created["id"], "name": "Ana", "email": "ana@example.com"},
    )
    assert status == 201 and sent["reference"] == "#1001"
    case_token = sent["path"].rsplit("/", 1)[1]
    status, case_page = _anonymous(env, "GET", f"/api/public/{case_token}")
    assert case_page["page"]["due_cents"] == 4500
    assert case_page["page"]["charges"] == [
        {"label": "Setup call", "amount_cents": 4500, "status": "due"}
    ]

    listing = api.get(f"{S}/offers")
    assert listing["due"][0]["reference"] == "#1001" and listing["payment_link"]
    case_id = listing["due"][0]["case_id"]
    detail = api.post(f"{S}/charges/{listing['due'][0]['id']}/paid", {"paid": True})
    assert (
        detail["charge"]["status"] == "paid"
        and detail["charges"][0]["status"] == "paid"
    )
    hub = api.get("/api/suite/revenue")
    assert hub["summary"]["by_app"]["service"] == 4500
    assert hub["lines"][0]["source_type"] == "case_charge"
    status, case_page = _anonymous(env, "GET", f"/api/public/{case_token}")
    assert case_page["page"]["due_cents"] == 0

    added = api.post(
        f"{S}/cases/{case_id}/charges",
        {"label": "Extra hour", "amount_cents": 3000},
        expect=201,
    )
    assert len(added["charges"]) == 2
    api.ok("DELETE", f"{S}/charges/{added['charge']['id']}")
    report = api.get(f"{S}/reports")
    assert report["reports"]["money"]["this_month_cents"] == 4500
    assert report["stats"]["earned_this_month_cents"] == 4500

    people = api.get(f"{S}/customers")["customers"]
    assert people[0]["email"] == "ana@example.com" and people[0]["paid_cents"] == 4500
    exported = _get_csv(env, api, f"{S}/customers?format=csv")
    assert "ana@example.com" in exported and "$45.00" in exported
    assert (
        api.get(f"{S}/customers/cases?key=ana@example.com")["cases"][0]["id"] == case_id
    )

    other = api.post(
        f"{S}/cases", {"subject": "Phone call", "priority": "urgent"}, expect=201
    )["case"]
    # The CSV follows the filters on screen, not only the view.
    exported = _get_csv(env, api, f"{S}/cases?view=all&priority=urgent&format=csv")
    assert "Phone call" in exported and "Setup call" not in exported
    result = api.post(
        f"{S}/cases/bulk", {"ids": [other["id"], case_id], "action": "delete"}
    )
    assert result["done"] == 1 and result["skipped"][0]["id"] == case_id
    api.ok("DELETE", f"{S}/offers/{created['id']}")
    assert api.get(f"{S}/offers")["offers"] == []


# ---------------------------------------------------------------------------
# the hosted build
# ---------------------------------------------------------------------------


def test_hosted_paid_help_roles_and_isolation(cloud):
    kit.forget_schema()
    org = build_org(cloud)
    owner = sign_in(cloud, org.owner)
    assert owner.call("GET", f"{S}/offers")[0] == 402
    cloud.billing.subscribe_app(org.owner, "service")
    status, made = owner.call(
        "POST", f"{S}/offers", {"title": "Setup", "price_cents": 2000}
    )
    assert status == 201, made
    auditor = sign_in(cloud, org.auditor)
    for path in ("/offers", "/customers", "/reports"):
        assert auditor.call("GET", f"{S}{path}")[0] == 200, path
    assert (
        auditor.call("POST", f"{S}/offers", {"title": "No", "price_cents": 100})[0]
        == 403
    )
    assert (
        auditor.call("POST", f"{S}/cases/bulk", {"ids": [], "action": "close"})[0]
        == 403
    )
    candidate = sign_in(cloud, org.candidate)
    assert candidate.call("GET", f"{S}/customers")[0] == 403
    coach = sign_in(cloud, org.coach)
    status, case = coach.call(
        "POST", f"{S}/cases", {"subject": "Call", "customer_email": "a@b.example"}
    )
    assert status == 201
    status, charged = coach.call(
        "POST",
        f"{S}/cases/{case['case']['id']}/charges",
        {"label": "Fix", "amount_cents": 900},
    )
    assert status == 201, charged
    assert (
        auditor.call(
            "POST", f"{S}/charges/{charged['charge']['id']}/paid", {"paid": True}
        )[0]
        == 403
    )

    status, shared = owner.call("POST", f"{S}/help/link", {})
    token = shared["link"]["token"]
    visitor = Browser(cloud)
    status, page = visitor.call("GET", f"/api/public/{token}")
    assert status == 200 and page["page"]["offers"][0]["title"] == "Setup"
    status, sent = visitor.call(
        "POST",
        f"/api/public/{token}/request",
        {"offer_id": made["offer"]["id"], "email": "ana@example.com"},
    )
    assert status == 201, sent

    other = build_org(cloud, "Other Org", slug_email="other.test")
    cloud.billing.subscribe_app(other.owner, "service")
    theirs = sign_in(cloud, other.owner)
    assert theirs.call("GET", f"{S}/offers")[1]["offers"] == []
    assert theirs.call("GET", f"{S}/customers")[1]["customers"] == []
    assert (
        theirs.call(
            "POST", f"{S}/charges/{charged['charge']['id']}/paid", {"paid": True}
        )[0]
        == 404
    )
