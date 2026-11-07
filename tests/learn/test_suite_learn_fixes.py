"""Learn's fixes after the second review: a hosted member's deck income is their own (their payment link, not the
organization's revenue, feed or outbox), extra practice that leaves the schedule alone, the deck CSV reading back
in unchanged, lists that mix separators, a deck never sold without a way to pay or with too few cards, stopping a
sale, copies of decks on sale, the revenue date of a sale paid again, and Today's sales card."""

# ruff: noqa: F811
from __future__ import annotations

from datetime import timedelta

import pytest
from cloud_support import Browser, build_org, sign_in
from life_support import Household
from test_suite_learn_features import (  # noqa: F401 - fixtures
    NOON,
    _buy,
    _public,
    db,
    deck_with,
    local_index,
    no_model,
    person,
)

import test_careers
from aira.suite import kit
from aira.suite.learn import cards_io, learn_routes, progress, sales, service, store

env = test_careers.env
api = test_careers.api
stub = test_careers.stub

PAY = "https://pay.example/pat"


def _sale_id(db, request):
    return kit.fetch_one(
        db, "SELECT id FROM ln_sales WHERE reference = ?", (request["reference"],)
    )["id"]


# ---------------------------------------------------------------------------
# hosted: a member's deck sale is the member's money
# ---------------------------------------------------------------------------


def test_a_hosted_members_deck_sale_is_their_own_not_the_organizations(cloud):
    kit.forget_schema()
    org = build_org(cloud)
    cloud.billing.subscribe_app(org.owner, "learn")
    owner = sign_in(cloud, org.owner)
    assert (
        owner.call(
            "POST",
            "/api/suite/business",
            {"name": "Acme", "payment_link": "https://pay.acme.example/org"},
        )[0]
        == 200
    )
    member = sign_in(cloud, org.candidate)
    status, made = member.call(
        "POST",
        "/api/learn/decks",
        {
            "title": "Cand deck",
            "cards": [{"front": f"F{i}", "back": f"B{i}"} for i in range(4)],
        },
    )
    assert status == 201, made
    deck = made["deck"]["id"]
    # The organization's payment link is not the member's: not ready, and a price alone is refused.
    summary = member.call("GET", "/api/learn/sales")[1]["summary"]
    assert summary["payment_ready"] is False and summary["hosted"] is True
    assert summary["payment"]["business_link"] == ""
    status, refused = member.call(
        "POST", f"/api/learn/decks/{deck}/price", {"price_cents": 700}
    )
    assert status == 400 and "payment" in refused["error"]
    # The member sets their own link (in Earn, or with the price in the Sell dialog).
    status, saved = member.call(
        "POST", "/api/learn/seller", {"payment_link": "https://pay.example/cand"}
    )
    assert status == 200 and saved["payment"]["ready"], saved
    status, priced = member.call(
        "POST",
        f"/api/learn/decks/{deck}/price",
        {"price_cents": 700, "preview_count": 1},
    )
    assert status == 200, priced
    token = priced["deck"]["link"]["token"]
    visitor = Browser(cloud)
    status, page = visitor.call("GET", f"/api/public/{token}")
    assert (
        status == 200 and page["page"]["payment"]["link"] == "https://pay.example/cand"
    )
    assert page["page"]["buyable"] is True
    status, bought = visitor.call(
        "POST", f"/api/public/{token}/buy", {"name": "Vi", "email": "vi@example.com"}
    )
    assert status == 201 and bought["request"]["payment"]["link"] == (
        "https://pay.example/cand"
    )
    sale_id = member.call("GET", "/api/learn/sales")[1]["sales"][0]["id"]
    status, paid = member.call("POST", f"/api/learn/sales/{sale_id}/paid", {})
    assert status == 200, paid
    # Not in the organization's outbox: the member gets the email ready to send themselves.
    assert paid["email"] == "none"
    assert paid["message"]["to"] == "vi@example.com"
    assert (
        paid["code"] in paid["message"]["body"]
        and f"/p/{token}" in (paid["message"]["body"])
    )
    status, full = visitor.call(
        "GET", f"/api/public/{token}/cards", headers={"Monosage-Secret": paid["code"]}
    )
    assert status == 200 and len(full["cards"]) == 4
    # The member's earnings are counted for them in Earn...
    mine = member.call("GET", "/api/learn/sales")[1]["summary"]
    assert mine["earned_cents"] == 700 and mine["sold"] == 1
    # ...and nowhere in the organization's revenue, outbox or activity feed.
    revenue = owner.call("GET", "/api/suite/revenue")[1]["summary"]
    assert "learn" not in revenue["by_app"], revenue
    outbox = owner.call("GET", "/api/suite/outbox")[1]["messages"]
    assert not [m for m in outbox if m["app"] == "learn"]
    activity = owner.call("GET", "/api/suite/activity")[1]["activity"]
    assert not [
        a
        for a in activity
        if a["app"] == "learn" and a["kind"] in ("sale", "sale_request", "sell")
    ]
    assert "vi@example.com" not in str(activity) and "Vi " not in str(activity)
    # Marked unpaid and paid again: still nothing in the organization's ledger.
    assert member.call("POST", f"/api/learn/sales/{sale_id}/unpaid", {})[0] == 200
    assert member.call("POST", f"/api/learn/sales/{sale_id}/paid", {})[0] == 200
    revenue = owner.call("GET", "/api/suite/revenue")[1]["summary"]
    assert "learn" not in revenue["by_app"]
    # A member cannot clear their only way to be paid while a deck is on sale.
    status, kept = member.call(
        "POST", "/api/learn/seller", {"payment_link": "", "payment_instructions": ""}
    )
    assert status == 400 and "stop selling" in kept["error"]


# ---------------------------------------------------------------------------
# extra practice leaves the schedule alone and earns XP once a day
# ---------------------------------------------------------------------------


def test_practising_hardest_cards_early_does_not_push_them_away_or_farm_xp(db):
    me = person(db)
    store.save_settings(db, me, {"goal_value": 50})
    deck = deck_with(db, me, n=1)
    card = store.cards(db, deck["id"])[0]["id"]
    day1 = NOON - timedelta(days=4)
    service.answer(db, me, card, "again", now=day1)
    service.answer(db, me, card, "good", now=day1)  # due the next day
    day2 = day1 + timedelta(days=1)
    review = service.answer(db, me, card, "good", now=day2)  # a real review: in 6 days
    assert review["practice"] is False and review["interval_days"] == 6
    day3 = day2 + timedelta(days=1)
    weak = progress.weak_queue(db, me, now=day3)
    assert [c["id"] for c in weak["cards"]] == [card] and weak["done_today"] == 0
    first = service.answer(db, me, card, "good", now=day3)
    assert first["practice"] is True
    assert first["interval_days"] == 6 and first["due"] == review["due"]
    assert first["xp_gained"] == service.XP_FOR["good"]
    # Answered right today: it waits until tomorrow, and repeating it earns nothing and moves nothing.
    weak = progress.weak_queue(db, me, now=day3)
    assert weak["cards"] == [] and weak["done_today"] == 1
    for _ in range(5):
        again = service.answer(db, me, card, "good", now=day3)
        assert again["interval_days"] == 6 and again["due"] == review["due"]
        assert again["xp_gained"] == 0
    row = store.day_row(db, me["id"], day3.date().isoformat())
    assert row["cards"] == 1 and row["xp"] == service.XP_FOR["good"]
    state = kit.fetch_one(db, "SELECT * FROM ln_progress WHERE card_id = ?", (card,))
    assert state["interval_days"] == 6 and state["lapses"] == 1
    # Practice answers do not count as reviews in "how much you remember".
    assert (
        kit.count(
            db,
            "SELECT COUNT(*) FROM ln_reviews WHERE practice = 1 AND card_id = ?",
            (card,),
        )
        == 6
    )
    # A miss while practising is a real miss: the card comes back today and is due like any other.
    missed = service.answer(db, me, card, "again", now=day3)
    assert missed["interval_days"] == 0 and missed["due"] == day3.date().isoformat()
    assert [c["id"] for c in progress.weak_queue(db, me, now=day3)["cards"]] == [card]
    # Once a card is known well (three weeks or more), it is no longer one of the hardest.
    db.execute("UPDATE ln_progress SET interval_days = 30 WHERE card_id = ?", (card,))
    db.commit()
    assert progress.weak_queue(db, me, now=day3 + timedelta(days=1))["cards"] == []
    assert progress.stats(db, me, now=day3)["hardest"] == []


# ---------------------------------------------------------------------------
# cards in and out
# ---------------------------------------------------------------------------


def test_the_deck_csv_download_reads_back_in_unchanged(db):
    me = person(db)
    deck = store.create_deck(db, me["id"], {"title": "Roundtrip"})
    cards = [
        {"front": "France", "back": "Paris", "hint": "", "example": ""},
        {
            "front": "Two lines",
            "back": "line one\nline two",
            "hint": 'h, with "comma"',
            "example": "an example\nover two lines",
        },
        {"front": "=1+1", "back": "-2", "hint": "", "example": ""},
        {"front": "Semi; colon", "back": "tab\there", "hint": "", "example": ""},
    ]
    for card in cards:
        store.add_card(db, deck["id"], card)
    db.commit()
    _, body = cards_io.export_csv(db, me, deck["id"])
    copy = store.create_deck(db, me["id"], {"title": "Copy"})
    result = cards_io.import_cards(db, me, copy["id"], body.decode("utf-8"))
    assert result == {"added": 4, "skipped": 0, "problems": []}
    back = [{k: c[k] for k in cards_io.COLUMNS} for c in store.cards(db, copy["id"])]
    assert back == cards


def test_a_pasted_list_may_mix_separators():
    rows, problems = cards_io.parse(
        "front,back\nFrance,Paris\nSpain = Madrid\nItaly,Rome\nGermany;Berlin\nPortugal=Lisbon\n\nnot a card"
    )
    assert [(r["front"], r["back"]) for r in rows] == [
        ("France", "Paris"),
        ("Spain", "Madrid"),
        ("Italy", "Rome"),
        ("Germany", "Berlin"),
        ("Portugal", "Lisbon"),
    ]
    assert problems == [
        {"line": 8, "text": "not a card", "reason": "Needs a front and a back."}
    ]
    # The list's own separator still wins on a line that has two.
    rows, _ = cards_io.parse("Hola = Hello\nGracias = Thank you, friend\nAdiós - Bye")
    assert [r["back"] for r in rows] == ["Hello", "Thank you, friend", "Bye"]


# ---------------------------------------------------------------------------
# selling: a way to pay, enough cards, stopping, copies, the ledger date, Today
# ---------------------------------------------------------------------------


def test_a_deck_is_never_sold_without_a_way_for_buyers_to_pay(db):
    me = person(db)
    deck = deck_with(db, me, n=4)
    with pytest.raises(ValueError, match="payment"):
        sales.set_price(db, me, deck["id"], {"price_cents": 499})
    assert not sales.for_sale(store.deck(db, deck["id"]))
    assert sales.summary(db, me)["payment_ready"] is False
    # The Sell dialog sends the seller's own way to pay with the price.
    sales.set_price(
        db,
        me,
        deck["id"],
        {"price_cents": 499, "payment_instructions": "Bank 12-34, account 5678"},
    )
    page = sales.page_data(db, store.deck(db, deck["id"]))
    assert page["payment"] == {"link": "", "instructions": "Bank 12-34, account 5678"}
    assert page["buyable"] is True
    assert _buy(db, deck)["payment"]["instructions"] == "Bank 12-34, account 5678"
    # Clearing it while a deck is on sale is refused; locally the business profile's link can stand in.
    with pytest.raises(ValueError, match="stop selling"):
        sales.save_payment(db, me, {"payment_instructions": ""})
    kit.save_business(db, {"payment_link": PAY})
    view = sales.save_payment(db, me, {"payment_instructions": ""})
    assert (
        view["ready"] and view["using"] == "business" and view["business_link"] == PAY
    )
    assert sales.page_data(db, store.deck(db, deck["id"]))["payment"]["link"] == PAY
    # A seller's own link wins over the business profile's.
    sales.save_payment(db, me, {"payment_link": "https://pay.example/own"})
    assert sales.page_data(db, store.deck(db, deck["id"]))["payment"]["link"] == (
        "https://pay.example/own"
    )
    with pytest.raises(ValueError):
        sales.save_payment(db, me, {"payment_link": "not a link"})


def test_a_deck_on_sale_keeps_two_cards_and_its_page(env, api, local_index):
    kit.forget_schema()
    home = Household(env, api)
    pat = home.owner
    deck = pat.post(
        "/api/learn/decks",
        {
            "title": "Small",
            "cards": [{"front": f"F{i}", "back": f"B{i}"} for i in range(3)],
        },
        expect=201,
    )["deck"]
    priced = pat.post(
        f"/api/learn/decks/{deck['id']}/price",
        {"price_cents": 500, "payment_link": PAY},
    )["deck"]
    token = priced["link"]["token"]
    cards = pat.get(f"/api/learn/decks/{deck['id']}")["deck"]["cards"]
    pat.ok("DELETE", f"/api/learn/cards/{cards[0]['id']}")
    status, refused = pat.call("DELETE", f"/api/learn/cards/{cards[1]['id']}")
    assert status == 400 and "two cards" in refused["error"]
    page = _public(env, "GET", f"/api/public/{token}")[1]["page"]
    assert page["total"] == 2 and len(page["cards"]) == 1 and page["buyable"]
    # The page of a deck on sale stays on: buyers pay and study there.
    status, refused = pat.call("DELETE", f"/api/learn/decks/{deck['id']}/link")
    assert status == 400 and "Stop selling" in refused["error"]
    assert _public(env, "GET", f"/api/public/{token}")[0] == 200
    # Stopping the sale closes waiting requests; a free deck needs no code.
    _public(
        env,
        "POST",
        f"/api/public/{token}/buy",
        {"name": "Ann", "email": "ann@example.com"},
    )
    waiting = pat.get("/api/learn/sales?status=pending")["sales"]
    assert len(waiting) == 1
    pat.post(f"/api/learn/decks/{deck['id']}/price", {"price_cents": 0})
    assert pat.get("/api/learn/sales?status=pending")["sales"] == []
    status, refused = pat.call("POST", f"/api/learn/sales/{waiting[0]['id']}/paid", {})
    assert status == 400
    # The Earn CSV follows the search box.
    status, sheet = pat.call("GET", "/api/learn/sales?format=csv&q=nobody", raw=True)
    assert status == 200 and "Ann" not in sheet.decode()
    # Now free, the link can be turned off.
    pat.ok("DELETE", f"/api/learn/decks/{deck['id']}/link")


def test_a_one_card_deck_left_on_sale_is_not_sold(db):
    me = person(db)
    kit.save_business(db, {"payment_link": PAY})
    deck = deck_with(db, me, n=3)
    sales.set_price(db, me, deck["id"], {"price_cents": 500})
    # (An older database could still hold one: cards were deleted before the rule.)
    db.execute("DELETE FROM ln_cards WHERE deck_id = ? AND position > 1", (deck["id"],))
    db.commit()
    page = sales.page_data(db, store.deck(db, deck["id"]))
    assert page["cards"] == [] and page["total"] == 1 and page["buyable"] is False
    with pytest.raises(ValueError, match="cannot be bought"):
        _buy(db, deck)


def test_a_deck_someone_sells_cannot_be_copied_and_a_copy_cannot_be_sold(db):
    kit.save_business(db, {"payment_link": PAY})
    me = person(db)
    bea = person(db, "Bea", "adult")
    selling = deck_with(db, me, n=3, shared=True, title="Capitals")
    sales.set_price(db, me, selling["id"], {"price_cents": 499})
    with pytest.raises(PermissionError, match="sells this deck"):
        service.copy_deck(db, bea, selling["id"])
    free = deck_with(db, me, n=3, shared=True, title="Free words")
    copy = service.copy_deck(db, bea, free["id"])
    assert copy["copied_from"] == me["id"]
    with pytest.raises(PermissionError, match="not yours to sell"):
        sales.set_price(db, bea, copy["id"], {"price_cents": 100})
    # A copy of a copy still belongs to the first owner; a copy of your own deck is yours to sell.
    db.execute("UPDATE ln_decks SET shared = 1 WHERE id = ?", (copy["id"],))
    db.commit()
    again = service.copy_deck(db, me, copy["id"])
    assert again["copied_from"] == me["id"]
    sales.set_price(db, me, again["id"], {"price_cents": 100})
    # Stopping a sale is always allowed, even for an old copy put on sale before the rule.
    sales.set_price(db, bea, copy["id"], {"price_cents": 0})


def test_a_sale_paid_again_moves_to_the_new_payment_date(db):
    kit.save_business(db, {"payment_link": PAY})
    me = person(db)
    deck = deck_with(db, me, n=3)
    sales.set_price(db, me, deck["id"], {"price_cents": 500})
    sale_id = _sale_id(db, _buy(db, deck))
    sales.mark_paid(db, me, sale_id)
    db.execute(
        "UPDATE ln_sales SET paid_at = '2026-09-30T10:00:00Z' WHERE id = ?", (sale_id,)
    )
    db.execute(
        "UPDATE suite_revenue SET at = '2026-09-30T10:00:00Z' WHERE source_id = ?",
        (sale_id,),
    )
    db.commit()
    sales.mark_unpaid(db, me, sale_id)
    sales.mark_paid(db, me, sale_id)
    paid_at = kit.fetch_one(
        db, "SELECT paid_at FROM ln_sales WHERE id = ?", (sale_id,)
    )["paid_at"]
    line = kit.fetch_one(
        db, "SELECT * FROM suite_revenue WHERE source_id = ?", (sale_id,)
    )
    assert line["voided_at"] is None and line["at"] == paid_at
    assert not paid_at.startswith("2026-09")
    # A lost code resent keeps the date it was paid.
    sales.mark_paid(db, me, sale_id)
    assert (
        kit.fetch_one(
            db, "SELECT at FROM suite_revenue WHERE source_id = ?", (sale_id,)
        )["at"]
        == paid_at
    )


def test_today_has_a_sales_card_that_opens_earn(db):
    kit.save_business(db, {"payment_link": PAY})
    me = person(db)
    deck = deck_with(db, me, n=3)
    assert [c["link"] for c in learn_routes._today(db, me)] == ["#/learn?tab=study"]
    sales.set_price(db, me, deck["id"], {"price_cents": 500})
    _buy(db, deck)
    cards = learn_routes._today(db, me)
    assert [c["link"] for c in cards] == ["#/learn?tab=study", "#/learn?tab=earn"]
    assert "1 person wants to buy your decks" == cards[1]["lines"][0]["text"]
