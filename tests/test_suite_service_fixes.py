"""Service: regressions for the problems an independent tester found after paid help was added.

* Merging cases from two customers showed each customer the other's conversation, and the duplicate's charge
  vanished from Paid help, the customer's page and the Customers totals. Merges now need the same customer, and
  the charges move with the messages (older merges are repaired when the database opens).
* Deleting a kept case wiped the merged duplicate's conversation and got around the "payment received" guard. The
  duplicates go with the kept case, and a paid charge on any of them stops the delete.
* A child (or teen) household profile read customers' details and marked payments: refused (403).
* The Business hub shows three numbers per app, so "Paid help this month" never appeared: it is one of the three
  once the business offers paid help.
* A customer who left only a phone number was missing from Customers; searching 5551234567 did not find
  +1 (555) 123-4567; suggested articles matched parts of words ("set" in "reset").

Unit tests on a scratch database, then the single-user server.
"""

# ruff: noqa: F811
from __future__ import annotations

import json

import pytest
from life_support import Household

import test_careers
from aira.suite import kit, public
from aira.suite.service import desk, store
from test_suite_service import (  # noqa: F401 - fixtures
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


def _cy(db):
    return desk.create_case(
        db,
        {
            "subject": "Password reset broken",
            "message": "I cannot reset my password",
            "name": "Cy",
            "email": "cy@example.com",
        },
        channel="web",
    )


def _page(db, case_id):
    from aira.suite.service import service_routes

    return service_routes._case_page(db, case_id)


# ---------------------------------------------------------------------------
# merging
# ---------------------------------------------------------------------------


def test_cases_from_two_customers_do_not_merge(db):
    ana = web_case(db)
    charge = desk.add_charge(
        db, ana["id"], {"label": "Extra hour", "amount_cents": 4500}
    )
    cy = _cy(db)
    with pytest.raises(ValueError, match="another customer"):
        desk.merge(db, ana["id"], cy["id"])
    # Nothing moved: each customer still sees only their own request, and the money still counts.
    assert _page(db, ana["id"])["subject"] == "My order has not arrived"
    assert [m["body"] for m in _page(db, cy["id"])["thread"]] == [
        "I cannot reset my password"
    ]
    assert [c["id"] for c in desk.charge_lists(db)["due"]] == [charge["id"]]


def test_same_customer_rules():
    same = desk.same_customer
    ana = {"customer_email": "ana@example.com", "customer_name": "Ana"}
    assert same(ana, {"customer_email": "ANA@example.com "})
    assert not same(ana, {"customer_email": "cy@example.com", "customer_name": "Ana"})
    # The first detail both have decides: here the name.
    assert same(ana, {"customer_name": " ana ", "customer_phone": "+1 555 222 3333"})
    assert same(
        {"customer_phone": "+1 (555) 222-3333"}, {"customer_phone": "555 222 3333"}
    )
    assert not same(
        {"customer_phone": "555 222 3333"}, {"customer_phone": "555 999 3333"}
    )
    # No shared detail at all: not provably the same person.
    assert not same(
        {"customer_email": "a@b.example"}, {"customer_phone": "555 222 3333"}
    )
    # A case without any customer details goes with any case.
    assert same({}, ana) and same(ana, {"customer_name": ""})


def test_a_merge_moves_the_charges_so_the_money_still_counts(db):
    kit.save_business(db, {"name": "Glow", "payment_link": "https://pay.example/glow"})
    duplicate = web_case(db, subject="Setup call please")
    charge = desk.add_charge(
        db, duplicate["id"], {"label": "Extra hour", "amount_cents": 4500}
    )
    keep = web_case(db)
    desk.merge(db, duplicate["id"], keep["id"])
    assert store.get_charge(db, charge["id"])["case_id"] == keep["id"]
    # Paid help > Waiting to be paid, the customer's page (from either link), Customers and the reports.
    assert [c["id"] for c in desk.charge_lists(db)["due"]] == [charge["id"]]
    for link_case in (duplicate["id"], keep["id"]):
        page = _page(db, link_case)
        assert page["due_cents"] == 4500 and page["charges"][0]["label"] == "Extra hour"
    ana = desk.customers(db)[0]
    assert ana["due_cents"] == 4500 and ana["cases"] == 1
    assert desk.stats(db)["due_cents"] == 4500
    assert desk.reports(db)["money"]["due_cents"] == 4500
    desk.set_charge_paid(db, charge["id"], True)
    assert [c["id"] for c in desk.charge_lists(db)["paid"]] == [charge["id"]]
    assert desk.customers(db)[0]["paid_cents"] == 4500


def test_a_kept_case_without_customer_details_takes_the_duplicates(db):
    keep = desk.create_case(db, {"subject": "Call about the boiler"}, channel="phone")
    duplicate = web_case(db)
    desk.merge(db, duplicate["id"], keep["id"])
    kept = store.get_case(db, keep["id"])
    assert kept["customer_email"] == "ana@example.com"
    desk.reply(db, keep["id"], {"body": "On our way."})
    sent = [m for m in kit.outbox(db) if m["kind"] == "case_reply"]
    assert [m["recipient"] for m in sent] == ["ana@example.com"]


def test_a_duplicate_of_a_duplicate_reaches_the_case_kept_in_the_end(db):
    first, second, third = (
        web_case(db, subject="One"),
        web_case(db, subject="Two"),
        web_case(db, subject="Three"),
    )
    desk.merge(db, first["id"], second["id"])
    desk.merge(db, second["id"], third["id"])
    assert store.get_case(db, first["id"])["merged_into"] == third["id"]
    desk.customer_reply(db, first["id"], {"body": "Any news?"})
    assert store.messages(db, third["id"])[-1]["body"] == "Any news?"
    assert _page(db, first["id"])["subject"] == "Three"


def test_older_merges_are_repaired_when_the_database_opens(db):
    """Merges made before this fix left the charges on the duplicate and could chain."""
    first, second, third = (
        web_case(db, subject="One"),
        web_case(db, subject="Two"),
        web_case(db, subject="Three"),
    )
    charge = desk.add_charge(db, first["id"], {"label": "Fix", "amount_cents": 900})
    db.execute(
        "UPDATE sv_cases SET merged_into = ?, status = 'closed' WHERE id = ?",
        (second["id"], first["id"]),
    )
    db.execute(
        "UPDATE sv_cases SET merged_into = ?, status = 'closed' WHERE id = ?",
        (third["id"], second["id"]),
    )
    db.commit()
    kit.forget_schema()
    store.ensure(db)
    assert store.get_case(db, first["id"])["merged_into"] == third["id"]
    assert store.get_charge(db, charge["id"])["case_id"] == third["id"]
    assert desk.charge_lists(db)["due"][0]["id"] == charge["id"]


def test_an_old_cross_customer_merge_never_shows_the_other_conversation(db):
    ana, cy = web_case(db), _cy(db)
    # As an older version did it: messages moved, no customer check.
    db.execute(
        "UPDATE sv_messages SET case_id = ? WHERE case_id = ?", (cy["id"], ana["id"])
    )
    db.execute(
        "UPDATE sv_cases SET merged_into = ?, status = 'closed' WHERE id = ?",
        (cy["id"], ana["id"]),
    )
    db.commit()
    page = _page(db, ana["id"])
    assert page["subject"] == "My order has not arrived"
    assert page["thread"] == [] and page["can_reply"] is False and page["note"]
    assert "Password" not in json.dumps(page)
    with pytest.raises(ValueError, match="combined"):
        desk.customer_reply(db, ana["id"], {"body": "Hello?"})
    desk.update_case(db, cy["id"], {"status": "solved"})
    with pytest.raises(ValueError, match="combined"):
        desk.rate(db, ana["id"], {"score": 1})


# ---------------------------------------------------------------------------
# deleting a kept case
# ---------------------------------------------------------------------------


def test_a_kept_case_with_a_paid_duplicate_charge_cannot_be_deleted(db):
    duplicate = web_case(db, subject="Setup")
    charge = desk.add_charge(
        db, duplicate["id"], {"label": "Setup", "amount_cents": 4500}
    )
    keep = web_case(db)
    desk.merge(db, duplicate["id"], keep["id"])
    desk.set_charge_paid(db, charge["id"], True)
    with pytest.raises(ValueError, match="unpaid"):
        desk.delete_case(db, keep["id"])
    result = desk.bulk(db, [keep["id"]], "delete")
    assert result["done"] == 0 and "unpaid" in result["skipped"][0]["reason"]
    assert kit.revenue(db, app="service")["total_cents"] == 4500
    # A charge an older version left on the duplicate is checked too.
    db.execute(
        "UPDATE sv_charges SET case_id = ? WHERE id = ?",
        (duplicate["id"], charge["id"]),
    )
    db.commit()
    with pytest.raises(ValueError, match="unpaid"):
        desk.delete_case(db, keep["id"])


def test_deleting_a_kept_case_takes_its_duplicates_and_their_links(db):
    duplicate = web_case(db, subject="Again")
    duplicate_link = desk.case_link(db, duplicate)
    keep = web_case(db)
    desk.merge(db, duplicate["id"], keep["id"])
    assert desk.delete_case(db, keep["id"]) == 2
    for gone in (keep["id"], duplicate["id"]):
        with pytest.raises(LookupError):
            store.get_case(db, gone)
        assert public.link_for(db, "service", "case", gone) is None
    with pytest.raises(LookupError):
        public.resolve(db, duplicate_link["token"])
    assert kit.count(db, "SELECT COUNT(*) FROM sv_messages") == 0
    assert desk.list_cases(db, view="all") == []


# ---------------------------------------------------------------------------
# customers, search, suggestions, the hub card
# ---------------------------------------------------------------------------


def test_a_customer_who_left_only_a_phone_number_is_listed(db):
    called = desk.create_case(
        db,
        {"subject": "Called about the boiler", "customer_phone": "+1 555 222 3333"},
        channel="phone",
    )
    desk.create_case(
        db,
        {"subject": "Called again", "customer_phone": "+1 (555) 222-3333"},
        channel="phone",
    )
    people = desk.customers(db)
    assert len(people) == 1
    person = people[0]
    assert person["key"] == "tel:15552223333" and person["cases"] == 2
    assert person["phone"]
    assert len(desk.customer_cases(db, person["key"])) == 2
    history = desk.case_detail(db, called["id"])["history"]
    assert [c["subject"] for c in history] == ["Called again"]
    assert [p["key"] for p in desk.customers(db, q="555")] == ["tel:15552223333"]


def test_search_finds_a_phone_number_typed_without_its_formatting(db):
    desk.create_case(
        db,
        {"subject": "Call back", "customer_phone": "+1 (555) 123-4567"},
        channel="phone",
    )
    for typed in ("5551234567", "555 123 4567", "(555) 123-4567", "123-4567"):
        assert len(desk.list_cases(db, view="all", q=typed)) == 1, typed
    assert desk.list_cases(db, view="all", q="5559999") == []


def test_suggested_articles_match_whole_words(db):
    desk.save_article(
        db,
        {
            "title": "How do I reset my password?",
            "body": "Use the link on the sign-in page.",
            "status": "published",
        },
    )
    desk.save_article(
        db,
        {
            "title": "Changing your delivery address",
            "body": "Open your orders and choose Change address.",
            "status": "published",
        },
    )
    found = desk.suggest_articles(db, "I want to set up my delivery address")
    assert [a["title"] for a in found] == ["Changing your delivery address"]
    # Plain plurals still match.
    assert [a["title"] for a in desk.suggest_articles(db, "Passwords reset")] == [
        "How do I reset my password?"
    ]


def test_the_hub_card_shows_paid_help_once_it_is_offered(db):
    cards = desk.summary(db)["cards"]
    assert [c["label"] for c in cards] == ["Open cases", "Overdue", "Satisfaction"]
    desk.save_offer(db, {"title": "Setup", "price_cents": 4500})
    case = web_case(db)
    charge = desk.add_charge(db, case["id"], {"label": "Setup", "amount_cents": 4500})
    desk.set_charge_paid(db, charge["id"], True)
    cards = desk.summary(db)["cards"]
    # The hub shows the first three numbers of each app.
    assert len(cards) == 3
    shown = {c["label"]: c for c in cards}
    assert shown["Paid help this month"]["value"] == "$45.00"
    # The case is from a day ago: its overdue count moved into the open cases' hint.
    assert shown["Open cases"]["hint"] == "1 overdue"
    assert shown["Open cases"]["tone"] == "bad"


# ---------------------------------------------------------------------------
# the single-user server
# ---------------------------------------------------------------------------


def test_a_child_or_teen_profile_cannot_open_service(env, api, local_index):
    """Regression: /api/service/* ignored the household profile, so a child read customers' emails and phone
    numbers and could mark payments."""
    kit.forget_schema()
    api.post("/api/suite/business", {"name": "Glow", "email": "owner@glow.example"})
    case = api.post(
        f"{S}/cases",
        {
            "subject": "Call",
            "customer_email": "sec@x.example",
            "customer_phone": "+1 555 000 1111",
        },
        expect=201,
    )["case"]
    charge = api.post(
        f"{S}/cases/{case['id']}/charges",
        {"label": "Fix", "amount_cents": 900},
        expect=201,
    )["charge"]
    household = Household(env, api)
    kid = household.add("Mia", age_group="child", pin="2468")
    teen = household.add("Sam", age_group="teen", pin="1357")
    grown_up = household.add("Ravi", age_group="adult", pin="8642")
    for young in (kid, teen):
        for method, path, body in (
            ("GET", f"{S}/customers?format=csv", None),
            ("GET", f"{S}/cases?view=all", None),
            ("GET", f"{S}/overview", None),
            ("GET", f"{S}/reports", None),
            ("POST", f"{S}/offers", {"title": "Cheap", "price_cents": 100}),
            ("POST", f"{S}/charges/{charge['id']}/paid", {"paid": True}),
            ("DELETE", f"{S}/cases/{case['id']}", None),
        ):
            status, data = young.call(method, path, body, raw=True)
            assert status == 403, (young.name, method, path, status)
            assert b"sec@x.example" not in data and b"555 000 1111" not in data
    # A made-up or locked profile is sent to the picker, not let in.
    status, data = household.owner.call("GET", f"{S}/overview")
    assert status == 200
    locked = type(grown_up)(env, api, grown_up.profile, grown_up.id)  # no PIN token
    assert locked.call("GET", f"{S}/overview")[1].get("code") == "profile_locked"
    # Grown-ups (and the account itself, with no profile header) use it as before.
    assert grown_up.get(f"{S}/customers")["customers"][0]["email"] == "sec@x.example"
    assert api.get(f"{S}/offers")["due"][0]["status"] == "due"
    assert api.get(f"{S}/cases?view=all")["cases"][0]["id"] == case["id"]


def test_merge_on_the_server_refuses_another_customers_case(env, api, local_index):
    kit.forget_schema()
    api.post(
        "/api/suite/business",
        {"name": "Glow", "payment_link": "https://pay.example/glow"},
    )
    offer = api.post(
        f"{S}/offers", {"title": "Extra hour", "price_cents": 4500}, expect=201
    )["offer"]
    token = api.post(f"{S}/help/link")["link"]["token"]
    status, sent = _anonymous(
        env,
        "POST",
        f"/api/public/{token}/request",
        {"offer_id": offer["id"], "name": "Ana", "email": "ana@example.com"},
    )
    assert status == 201
    ana_token = sent["path"].rsplit("/", 1)[1]
    ana_id = api.get(f"{S}/offers")["due"][0]["case_id"]
    cy = api.post(
        f"{S}/cases",
        {
            "subject": "Password reset broken",
            "description": "I cannot reset my password",
            "customer_email": "cy@example.com",
        },
        expect=201,
    )["case"]
    status, data = api.call("POST", f"{S}/cases/{ana_id}/merge", {"into": cy["id"]})
    assert status == 400 and "another customer" in data["error"], data
    status, page = _anonymous(env, "GET", f"/api/public/{ana_token}")
    assert page["page"]["subject"] == "Extra hour" and page["page"]["due_cents"] == 4500
    assert "password" not in json.dumps(page).lower()
    assert len(api.get(f"{S}/offers")["due"]) == 1
    # Ana's own duplicate merges, and her charge follows.
    again = api.post(
        f"{S}/cases",
        {"subject": "Booking the hour", "customer_email": "ana@example.com"},
        expect=201,
    )["case"]
    api.post(f"{S}/cases/{ana_id}/merge", {"into": again["id"]})
    status, page = _anonymous(env, "GET", f"/api/public/{ana_token}")
    assert page["page"]["subject"] == "Booking the hour"
    assert page["page"]["due_cents"] == 4500
    assert api.get(f"{S}/offers")["due"][0]["case_id"] == again["id"]
    removed = api.ok("DELETE", f"{S}/cases/{again['id']}")
    assert removed == {"deleted": True, "cases": 2}
    assert _anonymous(env, "GET", f"/api/public/{ana_token}")[0] == 404
