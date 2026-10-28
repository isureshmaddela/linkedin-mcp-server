"""Learn's newer features and fixes: progress (calendar, retention, forecast, hardest cards, the learning log),
cards in and out of a deck (paste or CSV, CSV download), selling a deck (preview page, buy request, payment
confirmed, access code, revenue, unpaid, cancel), books edited and deleted, and Today's lines; in unit tests,
through the single-user server and through the hosted gateway."""

from __future__ import annotations

import csv
import io
import json
import sqlite3
import urllib.error
import urllib.request
from datetime import date, datetime, timedelta, timezone

import pytest
from cloud_support import Browser, build_org, sign_in
from life_support import Household

import auto_apply_assistant as local
import test_careers
from aira import suite
from aira.suite import kit, life, public
from aira.suite.learn import cards_io, learn_routes, progress, sales, service, store

env = test_careers.env
api = test_careers.api
stub = test_careers.stub

NOON = datetime(2026, 10, 21, 12, 0, tzinfo=timezone.utc)


@pytest.fixture(autouse=True)
def no_model():
    """Templates only: the machine's own writing model must not answer in tests."""
    previous = suite.gateway()
    suite.set_gateway(suite._NoGateway())
    yield
    suite.set_gateway(previous)


@pytest.fixture
def db(tmp_path):
    kit.forget_schema()
    public.set_index(public._MemoryIndex())
    connection = sqlite3.connect(tmp_path / "learn.sqlite3")
    connection.row_factory = sqlite3.Row
    store.ensure(connection)
    yield connection
    connection.close()


class Req:
    def __init__(self) -> None:
        self.user = {"email": "pat@home.example"}

    def header(self, name: str, default: str = "") -> str:
        return default


def person(db, name="Pat", age_group="adult"):
    owner = life.current(Req(), db) if not life.profiles(db) else life.profiles(db)[0]
    if name == owner["name"]:
        return owner
    return life.add(db, owner, {"name": name, "age_group": age_group})


def deck_with(db, owner, n=8, shared=False, title="Spanish"):
    deck = store.create_deck(db, owner["id"], {"title": title, "shared": shared})
    for index in range(n):
        store.add_card(
            db, deck["id"], {"front": f"Word {index}", "back": f"Meaning {index}"}
        )
    db.commit()
    return deck


# ---------------------------------------------------------------------------
# the schema grows on an older database
# ---------------------------------------------------------------------------


def test_an_older_database_gets_the_new_columns_and_tables(tmp_path):
    kit.forget_schema()
    connection = sqlite3.connect(tmp_path / "old.sqlite3")
    connection.row_factory = sqlite3.Row
    life.ensure(connection)
    kit.ensure(connection, "learn.v1", store.SCHEMA)  # what an older Learn made
    connection.execute(
        "INSERT INTO ln_decks (id, owner_id, title, created_at, updated_at) VALUES ('d1', 'p1', 'Old deck', 'x', 'x')"
    )
    connection.commit()
    kit.forget_schema()
    store.ensure(connection)
    deck = store.deck(connection, "d1")
    assert deck["price_cents"] == 0 and deck["preview_count"] == 5
    assert kit.count(connection, "SELECT COUNT(*) FROM ln_sales") == 0
    kit.forget_schema()
    store.ensure(connection)  # running again changes nothing
    connection.close()


# ---------------------------------------------------------------------------
# cards in and out
# ---------------------------------------------------------------------------


def test_pasted_cards_are_read_with_any_common_separator():
    rows, problems = cards_io.parse(
        "Hola = Hello\nGracias = Thank you, friend\n\nAdiós\n"
    )
    assert [(r["front"], r["back"]) for r in rows] == [
        ("Hola", "Hello"),
        ("Gracias", "Thank you, friend"),
    ]
    assert problems == [
        {"line": 4, "text": "Adiós", "reason": "Needs a front and a back."}
    ]
    rows, _ = cards_io.parse(
        'front,back,hint,example\n"Paris, France",Capital,"Eiffel",\n2+2,4,,"Two and two"'
    )
    assert rows == [
        {"front": "Paris, France", "back": "Capital", "hint": "Eiffel", "example": ""},
        {"front": "2+2", "back": "4", "hint": "", "example": "Two and two"},
    ]
    rows, _ = cards_io.parse("cat\tgato\tmeow\ndog\tperro")
    assert rows[0] == {"front": "cat", "back": "gato", "hint": "meow", "example": ""}
    rows, _ = cards_io.parse("Wren - tiny, brown and loud\nRobin - red breast")
    assert (
        rows[0]["back"] == "tiny, brown and loud"
    )  # " - " wins over a comma inside the answer
    rows, _ = cards_io.parse(
        "'-5 + 2;'-3"
    )  # the apostrophe a CSV export puts before "-" comes off again
    assert rows[0]["front"] == "-5 + 2" and rows[0]["back"] == "-3"
    with pytest.raises(ValueError, match="at least one card"):
        cards_io.parse("   \n  ")
    with pytest.raises(ValueError, match="at most 500"):
        cards_io.parse("\n".join(f"a{i}=b" for i in range(501)))


def test_import_skips_cards_already_in_the_deck_and_only_the_owner_imports(db):
    me = person(db)
    deck = deck_with(db, me, n=2)
    result = cards_io.import_cards(
        db, me, deck["id"], "Word 0 = again\nUno = One\nDos = Two\nnope"
    )
    assert (
        result["added"] == 2
        and result["skipped"] == 1
        and result["problems"][0]["line"] == 4
    )
    assert (
        cards_io.import_cards(db, me, deck["id"], "uno = One")["added"] == 0
    )  # case does not matter
    assert len(store.cards(db, deck["id"])) == 4
    mia = person(db, "Mia", "child")
    with pytest.raises(LookupError):
        cards_io.import_cards(db, mia, deck["id"], "a = b")
    shared = deck_with(db, me, n=1, shared=True, title="Family")
    with pytest.raises(PermissionError):
        cards_io.import_cards(db, mia, shared["id"], "a = b")
    # Export is for anyone who may study the deck, and reads back in unchanged.
    name, body = cards_io.export_csv(db, mia, shared["id"])
    assert name == "family.csv"
    back = list(csv.DictReader(io.StringIO(body.decode())))
    assert back == [{"front": "Word 0", "back": "Meaning 0", "hint": "", "example": ""}]
    with pytest.raises(LookupError):
        cards_io.export_csv(db, mia, deck["id"])
    copy = store.create_deck(db, me["id"], {"title": "Copy"})
    assert (
        cards_io.import_cards(
            db, me, copy["id"], cards_io.export_csv(db, me, deck["id"])[1].decode()
        )["added"]
        == 4
    )


# ---------------------------------------------------------------------------
# progress
# ---------------------------------------------------------------------------


def test_progress_has_a_calendar_retention_forecast_and_hardest_cards(db):
    me = person(db)
    store.save_settings(db, me, {"goal_value": 3, "new_per_day": 50})
    deck = deck_with(db, me, n=10)
    empty = progress.stats(db, me, now=NOON)
    assert (
        len(empty["calendar"]) == progress.CALENDAR_DAYS
        and empty["calendar"][-1]["day"] == "2026-10-21"
    )
    assert (
        empty["retention"] is None
        and empty["hardest"] == []
        and empty["totals"]["cards"] == 0
    )
    day1 = NOON - timedelta(days=2)
    for index, card in enumerate(service.study_queue(db, me, now=day1)["cards"]):
        service.answer(db, me, card["id"], "again" if index < 2 else "good", now=day1)
    # The two missed cards come back the same day; the others tomorrow.
    for card in service.study_queue(db, me, now=day1)["cards"]:
        service.answer(db, me, card["id"], "again", now=day1)
    day2 = NOON - timedelta(days=1)
    for index, card in enumerate(service.study_queue(db, me, now=day2)["cards"]):
        service.answer(db, me, card["id"], "good" if index % 4 else "again", now=day2)
    stats = progress.stats(db, me, now=NOON)
    calendar = {c["day"]: c for c in stats["calendar"]}
    assert calendar["2026-10-19"]["cards"] == 12 and calendar["2026-10-19"]["goal_met"]
    assert calendar["2026-10-21"]["cards"] == 0
    assert stats["totals"]["streak"] == 2 and stats["totals"]["best_streak"] == 2
    assert (
        stats["retention_reviews"] >= progress.RETENTION_MIN_REVIEWS
        and 0 <= stats["retention"] <= 100
    )
    hardest = stats["hardest"]
    assert (
        hardest[0]["lapses"] == 3 and hardest[0]["deck_title"] == "Spanish"
    )  # missed twice, then again
    assert [c["lapses"] for c in hardest] == sorted(
        (c["lapses"] for c in hardest), reverse=True
    )
    assert (
        sum(d["cards"] for d in stats["forecast"]) > 0
        and stats["forecast"][0]["day"] == "2026-10-21"
    )
    # The weak cards can be practised now, hardest first; another person has none.
    weak = progress.weak_queue(db, me)["cards"]
    assert weak[0]["id"] == hardest[0]["id"] and all(not c["is_new"] for c in weak)
    assert progress.weak_queue(db, me, deck_id=deck["id"])["cards"] == weak
    mia = person(db, "Mia", "child")
    assert (
        progress.weak_queue(db, mia)["cards"] == []
        and progress.stats(db, mia, now=NOON)["totals"]["cards"] == 0
    )
    with pytest.raises(LookupError):
        progress.weak_queue(db, mia, deck_id=deck["id"])
    log = list(csv.DictReader(io.StringIO(progress.daily_log(db, me).decode())))
    assert [row["day"] for row in log] == ["2026-10-20", "2026-10-19"] and log[1][
        "goal_met"
    ] == "yes"
    assert (
        progress.best_streak(
            [
                {"day": d, "goal_met": 1, "frozen": 0}
                for d in (
                    "2026-01-01",
                    "2026-01-02",
                    "2026-01-05",
                    "2026-01-06",
                    "2026-01-07",
                )
            ]
        )
        == 3
    )


# ---------------------------------------------------------------------------
# selling a deck
# ---------------------------------------------------------------------------


def _buy(db, deck, name="Sam", email="sam@example.com"):
    return sales.request_purchase(
        db, store.deck(db, deck["id"]), {"name": name, "email": email}
    )


def test_a_deck_on_sale_shows_a_preview_and_a_paid_buyer_unlocks_every_card(db):
    kit.save_business(
        db, {"currency": "INR", "payment_link": "https://pay.example/pat"}
    )
    me = person(db)
    deck = deck_with(db, me, n=12)
    free = sales.page_data(db, store.deck(db, deck["id"]))
    assert free["for_sale"] is False and len(free["cards"]) == 12
    with pytest.raises(ValueError, match="free"):
        _buy(db, deck)
    sales.set_price(db, me, deck["id"], {"price_cents": 49900, "preview_count": 3})
    page = sales.page_data(db, store.deck(db, deck["id"]))
    assert (
        page["for_sale"] and page["price_cents"] == 49900 and page["currency"] == "INR"
    )
    assert len(page["cards"]) == 3 and page["total"] == 12 and page["seller"] == "Pat"
    # A request: validated, and the same email asking twice gets the same request.
    with pytest.raises(ValueError):
        sales.request_purchase(
            db, store.deck(db, deck["id"]), {"name": "Sam", "email": "not-an-email"}
        )
    with pytest.raises(ValueError):
        sales.request_purchase(
            db, store.deck(db, deck["id"]), {"email": "sam@example.com"}
        )
    first = _buy(db, deck)
    assert (
        first["status"] == "pending"
        and first["amount_cents"] == 49900
        and first["reference"].startswith("LN-")
    )
    assert _buy(db, deck, email="SAM@example.com")["reference"] == first["reference"]
    sale_id = kit.fetch_one(
        db, "SELECT id FROM ln_sales WHERE reference = ?", (first["reference"],)
    )["id"]
    with pytest.raises(PermissionError):
        sales.unlock(db, store.deck(db, deck["id"]), "ABCDE-FGHJK")
    paid = sales.mark_paid(db, me, sale_id)
    assert (
        paid["sale"]["status"] == "paid"
        and len(paid["code"]) == 11
        and paid["email"] == "queued"
    )
    assert (
        len(
            sales.unlock(
                db, store.deck(db, deck["id"]), paid["code"].lower().replace("-", "")
            )
        )
        == 12
    )
    ledger = kit.fetch_all(
        db, "SELECT * FROM suite_revenue WHERE app = 'learn' AND voided_at IS NULL"
    )
    assert [(r["amount_cents"], r["currency"], r["customer"]) for r in ledger] == [
        (49900, "INR", "Sam")
    ]
    mail = kit.outbox(db, app="learn")
    assert (
        len(mail) == 1
        and mail[0]["recipient"] == "sam@example.com"
        and paid["code"] in mail[0]["body"]
    )
    assert mail[0]["kind"] == "deck_access" and "/p/" in mail[0]["body"]
    # A lost code: confirming again sends a new one and the old one stops working; revenue is not doubled.
    again = sales.mark_paid(db, me, sale_id)
    assert again["code"] != paid["code"]
    with pytest.raises(PermissionError):
        sales.unlock(db, store.deck(db, deck["id"]), paid["code"])
    assert (
        kit.count(
            db,
            "SELECT COUNT(*) FROM suite_revenue WHERE app = 'learn' AND voided_at IS NULL",
        )
        == 1
    )
    summary = sales.summary(db, me)
    assert (
        summary["earned_cents"] == 49900
        and summary["sold"] == 1
        and summary["payment_ready"]
    )
    assert summary["decks"][0]["sold"] == 1 and summary["decks"][0]["link"][
        "path"
    ].startswith("/p/")
    # Marked unpaid: the ledger line stops counting and the code stops working.
    assert sales.mark_unpaid(db, me, sale_id)["status"] == "pending"
    assert (
        kit.count(
            db,
            "SELECT COUNT(*) FROM suite_revenue WHERE app = 'learn' AND voided_at IS NULL",
        )
        == 0
    )
    with pytest.raises(PermissionError):
        sales.unlock(db, store.deck(db, deck["id"]), again["code"])
    with pytest.raises(ValueError, match="Only a paid"):
        sales.mark_unpaid(db, me, sale_id)
    # Cancel a request; a cancelled one cannot be confirmed; a paid one cannot be cancelled.
    other = _buy(db, deck, name="Ann", email="ann@example.com")
    other_id = kit.fetch_one(
        db, "SELECT id FROM ln_sales WHERE reference = ?", (other["reference"],)
    )["id"]
    assert sales.cancel(db, me, other_id)["status"] == "cancelled"
    with pytest.raises(ValueError, match="cancelled"):
        sales.mark_paid(db, me, other_id)
    sales.mark_paid(db, me, sale_id)
    with pytest.raises(ValueError, match="unpaid first"):
        sales.cancel(db, me, sale_id)
    # Search and filters, and the CSV rows.
    assert [s["buyer_name"] for s in sales.sales(db, me, q="ann")] == ["Ann"]
    assert [s["buyer_name"] for s in sales.sales(db, me, status="paid")] == ["Sam"]
    assert sales.csv_rows(sales.sales(db, me, status="paid"))[0]["amount"] == "499.00"
    with pytest.raises(ValueError):
        sales.sales(db, me, status="refunded")
    # Free again: every card shows, and a code is not needed.
    sales.set_price(db, me, deck["id"], {"price_cents": 0})
    assert len(sales.page_data(db, store.deck(db, deck["id"]))["cards"]) == 12


def test_a_deck_needs_cards_to_sell_and_always_keeps_one_for_buyers(db):
    me = person(db)
    kit.save_business(db, {"payment_link": "https://pay.example/pat"})
    empty = store.create_deck(db, me["id"], {"title": "Empty"})
    with pytest.raises(ValueError, match="two cards"):
        sales.set_price(db, me, empty["id"], {"price_cents": 100})
    sales.set_price(db, me, empty["id"], {"price_cents": 0})  # free is always fine
    small = deck_with(db, me, n=3)
    sales.set_price(db, me, small["id"], {"price_cents": 100, "preview_count": 10})
    page = sales.page_data(db, store.deck(db, small["id"]))
    assert len(page["cards"]) == 2 and page["total"] == 3


def test_a_household_member_copies_a_shared_deck_to_change_it(db):
    me = person(db)
    mia = person(db, "Mia", "child")
    shared = deck_with(db, me, n=3, shared=True, title="Family words")
    private = deck_with(db, me, n=2, title="Private")
    copy = service.copy_deck(db, mia, shared["id"])
    assert copy["owner_id"] == mia["id"] and copy["title"] == "Family words (my copy)"
    assert not copy["shared"]
    assert [c["front"] for c in store.cards(db, copy["id"])] == [
        "Word 0",
        "Word 1",
        "Word 2",
    ]
    store.add_card(
        db,
        store.own_deck(db, mia, copy["id"])["id"],
        {"front": "Mine", "back": "Mia's"},
    )
    assert len(store.cards(db, shared["id"])) == 3  # the original is untouched
    with pytest.raises(LookupError):
        service.copy_deck(db, mia, private["id"])


def test_only_adult_owners_sell_and_sellers_see_only_their_own_sales(db):
    me = person(db)
    kit.save_business(db, {"payment_link": "https://pay.example/pat"})
    mia = person(db, "Mia", "child")
    dad = person(db, "Dad", "adult")
    deck = deck_with(db, me, shared=True)
    kids = deck_with(db, mia, title="Mia's deck")
    with pytest.raises(PermissionError, match="adult"):
        sales.set_price(db, mia, kids["id"], {"price_cents": 100})
    with pytest.raises(PermissionError):
        sales.set_price(db, dad, deck["id"], {"price_cents": 100})  # not his deck
    with pytest.raises(ValueError):
        sales.set_price(db, me, deck["id"], {"price_cents": -5})
    # A price that could not be read arrives empty: refused, never taken as "free".
    for unreadable in ({"price_cents": None}, {"price_cents": ""}, {}):
        with pytest.raises(ValueError, match="price"):
            sales.set_price(db, me, deck["id"], unreadable)
    with pytest.raises(ValueError, match="too high"):
        sales.set_price(db, me, deck["id"], {"price_cents": sales.MAX_PRICE_CENTS + 1})
    with pytest.raises(ValueError):
        sales.set_price(db, me, deck["id"], {"price_cents": 100, "preview_count": 50})
    sales.set_price(db, me, deck["id"], {"price_cents": 100})
    request = _buy(db, deck)
    sale_id = kit.fetch_one(
        db, "SELECT id FROM ln_sales WHERE reference = ?", (request["reference"],)
    )["id"]
    with pytest.raises(LookupError):
        sales.mark_paid(db, dad, sale_id)
    with pytest.raises(PermissionError):
        sales.sales(db, mia)
    assert sales.sales(db, dad) == [] and sales.waiting_for(db, dad) == 0
    assert sales.waiting_for(db, me) == 1 and sales.waiting_for(db, mia) == 0
    # A deleted deck cannot be unlocked any more.
    service.delete_deck(db, me, deck["id"])
    with pytest.raises(ValueError, match="deleted"):
        sales.mark_paid(db, me, sale_id)


# ---------------------------------------------------------------------------
# books and Today
# ---------------------------------------------------------------------------


def test_books_can_be_renamed_reopened_and_deleted(db):
    me = person(db)
    book = service.add_book(db, me, {"title": "Matlida", "author": ""})
    fixed = service.update_book(
        db, me, book["id"], {"title": "Matilda", "author": "Roald Dahl"}
    )
    assert (
        fixed["title"] == "Matilda"
        and fixed["author"] == "Roald Dahl"
        and fixed["status"] == "reading"
    )
    assert (
        service.update_book(db, me, book["id"], {"finished": True})["status"]
        == "finished"
    )
    reopened = service.update_book(db, me, book["id"], {"finished": False})
    assert reopened["status"] == "reading" and reopened["finished_at"] is None
    with pytest.raises(ValueError):
        service.update_book(db, me, book["id"], {"title": ""})
    mia = person(db, "Mia", "child")
    with pytest.raises(LookupError):
        service.update_book(db, mia, book["id"], {"title": "Mine"})
    service.log_reading(db, me, {"minutes": 10, "book_id": book["id"]}, now=NOON)
    service.delete_book(db, me, book["id"])
    assert service.books(db, me) == []


def test_today_keeps_a_streak_line_unstruck_and_tells_a_seller_about_requests(db):
    me = person(db)
    store.save_settings(db, me, {"goal_value": 1})
    deck = deck_with(db, me, n=3)
    yesterday = datetime.now(timezone.utc) - timedelta(days=1)
    card = service.study_queue(db, me, now=yesterday)["cards"][0]
    service.answer(db, me, card["id"], "good", now=yesterday)
    for card in service.study_queue(db, me)["cards"][:1]:
        service.answer(db, me, card["id"], "good")
    lines = learn_routes._today(db, me)[0]["lines"]
    streak = next(line for line in lines if "streak" in line["text"])
    assert "done" not in streak and streak["sub"] == "Kept for today"
    sales.set_price(
        db,
        me,
        deck["id"],
        {"price_cents": 250, "payment_link": "https://pay.example/pat"},
    )
    _buy(db, deck)
    practise, sold = learn_routes._today(db, me)
    assert not any("buy your decks" in line["text"] for line in practise["lines"])
    # Its own card, opening Earn (where payments are confirmed), not Study.
    assert sold["link"] == "#/learn?tab=earn"
    assert any("wants to buy your decks" in line["text"] for line in sold["lines"])
    summary = learn_routes._summary(db)
    assert any(card["label"] == "Deck sales to confirm" for card in summary["cards"])
    assert date.fromisoformat(service.today_of(db).isoformat())


# ---------------------------------------------------------------------------
# the single-user build: a seller, a buyer on the public page, a child
# ---------------------------------------------------------------------------


@pytest.fixture
def local_index():
    public.set_index(local._public_link_index)


def _public(env, method, path, body=None, headers=None):
    data = json.dumps(body).encode() if body is not None else None
    request = urllib.request.Request(env.base + path, data=data, method=method)
    for name, value in {
        "Content-Type": "application/json",
        "X-Requested-With": "aira",
        **(headers or {}),
    }.items():
        request.add_header(name, value)
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            return response.status, json.loads(response.read() or b"{}")
    except urllib.error.HTTPError as error:
        return error.code, json.loads(error.read() or b"{}")


def test_selling_a_deck_on_the_local_build(env, api, local_index):
    kit.forget_schema()
    home = Household(env, api)
    pat = home.owner
    mia = home.add("Mia", age_group="child")
    deck = pat.post(
        "/api/learn/decks",
        {
            "title": "Kitchen Spanish",
            "cards": [{"front": f"Word {i}", "back": f"Palabra {i}"} for i in range(8)],
        },
        expect=201,
    )["deck"]
    # Cards added from a pasted list, and the deck downloaded as CSV.
    imported = pat.post(
        f"/api/learn/decks/{deck['id']}/import",
        {"text": "Cuchara = Spoon\nTenedor = Fork\nWord 1 = dup"},
    )
    assert imported == {"added": 2, "skipped": 1, "problems": []}
    status, body = pat.call("GET", f"/api/learn/decks/{deck['id']}/export", raw=True)
    assert (
        status == 200
        and body.decode().splitlines()[0] == "front,back,hint,example"
        and "Tenedor" in body.decode()
    )
    # A child can neither sell nor see sales.
    assert (
        mia.call("POST", f"/api/learn/decks/{deck['id']}/price", {"price_cents": 100})[
            0
        ]
        == 404
    )
    assert mia.call("GET", "/api/learn/sales")[0] == 403
    # No way to pay yet: refused. The Sell dialog sends the seller's own payment link with the price.
    assert (
        pat.call("POST", f"/api/learn/decks/{deck['id']}/price", {"price_cents": 300})[
            0
        ]
        == 400
    )
    priced = pat.post(
        f"/api/learn/decks/{deck['id']}/price",
        {
            "price_cents": 300,
            "preview_count": 2,
            "payment_link": "https://pay.example/pat",
        },
    )["deck"]
    assert priced["price_cents"] == 300 and priced["link"]["path"].startswith("/p/")
    token = priced["link"]["token"]
    assert (
        pat.post(f"/api/learn/decks/{deck['id']}/price", {"price_cents": 350})["deck"][
            "link"
        ]["token"]
        == token
    )
    # A visitor sees the preview, asks to buy, and cannot open every card without a code.
    status, page = _public(env, "GET", f"/api/public/{token}")
    assert (
        status == 200
        and page["page"]["for_sale"]
        and len(page["page"]["cards"]) == 2
        and page["page"]["total"] == 10
    )
    status, made = _public(
        env,
        "POST",
        f"/api/public/{token}/buy",
        {"name": "Sam", "email": "sam@example.com"},
    )
    assert status == 201 and made["request"]["amount_cents"] == 350
    assert (
        _public(env, "POST", f"/api/public/{token}/buy", {"name": "", "email": "x"})[0]
        == 400
    )
    assert (
        _public(
            env,
            "GET",
            f"/api/public/{token}/cards",
            headers={"Monosage-Secret": "WRONG-CODE1"},
        )[0]
        == 403
    )
    # The seller sees the request (and Today says so), confirms the payment and gets the code.
    listed = pat.get("/api/learn/sales")
    assert (
        listed["summary"]["waiting"] == 1
        and listed["sales"][0]["buyer_email"] == "sam@example.com"
    )
    today = [c for c in pat.get("/api/life/today")["cards"] if c["app"] == "learn"]
    assert any(
        "buy your decks" in line["text"] and card["link"] == "#/learn?tab=earn"
        for card in today
        for line in card["lines"]
    )
    paid = pat.post(f"/api/learn/sales/{listed['sales'][0]['id']}/paid", {})
    status, full = _public(
        env, "GET", f"/api/public/{token}/cards", headers={"Monosage-Secret": paid["code"]}
    )
    assert status == 200 and len(full["cards"]) == 10
    assert pat.get("/api/learn/sales?status=paid")["summary"]["earned_cents"] == 350
    status, sheet = pat.call("GET", "/api/learn/sales?format=csv", raw=True)
    assert status == 200 and "Sam" in sheet.decode() and "3.50" in sheet.decode()
    user = env.user()
    local._bind_user(user)
    try:
        with local._connect() as connection:
            ledger = kit.fetch_all(
                connection,
                "SELECT * FROM suite_revenue WHERE app = 'learn' AND voided_at IS NULL",
            )
            mail = kit.outbox(connection, app="learn")
            summary = kit.SUMMARIES["learn"](connection)
    finally:
        local._unbind_user()
    assert [r["amount_cents"] for r in ledger] == [350]
    assert mail[0]["recipient"] == "sam@example.com" and paid["code"] in mail[0]["body"]
    assert f"/p/{token}" in mail[0]["body"]
    assert any(
        card["label"] == "Earned from decks this month" for card in summary["cards"]
    )
    # Progress and the weak-card mode answer through the server too.
    card = pat.get(f"/api/learn/study?deck_id={deck['id']}")["cards"][0]
    pat.post("/api/learn/study/answer", {"card_id": card["id"], "grade": "again"})
    assert pat.get("/api/learn/study?mode=weak")["cards"][0]["id"] == card["id"]
    stats = pat.get("/api/learn/stats")
    assert (
        stats["totals"]["cards"] == 1 and stats["hardest"][0]["front"] == card["front"]
    )
    status, log = pat.call("GET", "/api/learn/stats?format=csv", raw=True)
    assert status == 200 and log.decode().startswith("day,cards,new_cards")
    assert mia.get("/api/learn/stats")["totals"]["cards"] == 0
    # Books: edit and delete through the server.
    book = pat.post("/api/learn/books", {"title": "Matlida"}, expect=201)["book"]
    assert (
        pat.ok("PATCH", f"/api/learn/books/{book['id']}", {"title": "Matilda"})["book"][
            "title"
        ]
        == "Matilda"
    )
    assert mia.call("PATCH", f"/api/learn/books/{book['id']}", {"title": "x"})[0] == 404
    pat.ok("DELETE", f"/api/learn/books/{book['id']}")
    # Deleting the deck closes open requests and turns the page off.
    _public(
        env,
        "POST",
        f"/api/public/{token}/buy",
        {"name": "Ann", "email": "ann@example.com"},
    )
    pat.ok("DELETE", f"/api/learn/decks/{deck['id']}")
    assert pat.get("/api/learn/sales?status=pending")["sales"] == []
    assert _public(env, "GET", f"/api/public/{token}")[0] == 404


# ---------------------------------------------------------------------------
# the hosted build
# ---------------------------------------------------------------------------


def test_hosted_members_sell_their_own_decks_and_organizations_stay_apart(cloud):
    kit.forget_schema()
    org = build_org(cloud)
    owner = sign_in(cloud, org.owner)
    assert owner.call("GET", "/api/learn/sales")[0] == 402
    cloud.billing.subscribe_app(org.owner, "learn")
    coach = sign_in(cloud, org.coach)
    candidate = sign_in(cloud, org.candidate)
    status, made = coach.call(
        "POST",
        "/api/learn/decks",
        {
            "title": "Interview words",
            "cards": [{"front": f"Q{i}", "back": f"A{i}"} for i in range(4)],
        },
    )
    assert status == 201, made
    deck = made["deck"]
    status, priced = coach.call(
        "POST",
        f"/api/learn/decks/{deck['id']}/price",
        {
            "price_cents": 500,
            "preview_count": 1,
            "payment_link": "https://pay.example/coach",
        },
    )
    assert status == 200, priced
    token = priced["deck"]["link"]["token"]
    # Another member cannot price, import into or read the sales of the coach's deck.
    assert (
        candidate.call(
            "POST", f"/api/learn/decks/{deck['id']}/price", {"price_cents": 1}
        )[0]
        == 404
    )
    assert (
        candidate.call(
            "POST", f"/api/learn/decks/{deck['id']}/import", {"text": "a = b"}
        )[0]
        == 404
    )
    visitor = Browser(cloud)
    status, page = visitor.call("GET", f"/api/public/{token}")
    assert status == 200 and len(page["page"]["cards"]) == 1, page
    status, bought = visitor.call(
        "POST", f"/api/public/{token}/buy", {"name": "Sam", "email": "sam@example.com"}
    )
    assert status == 201, bought
    assert candidate.call("GET", "/api/learn/sales")[1]["sales"] == []
    status, listed = coach.call("GET", "/api/learn/sales")
    assert status == 200 and len(listed["sales"]) == 1
    sale_id = listed["sales"][0]["id"]
    assert candidate.call("POST", f"/api/learn/sales/{sale_id}/paid", {})[0] == 404
    status, paid = coach.call("POST", f"/api/learn/sales/{sale_id}/paid", {})
    assert status == 200, paid
    status, full = visitor.call(
        "GET", f"/api/public/{token}/cards", headers={"Monosage-Secret": paid["code"]}
    )
    assert status == 200 and len(full["cards"]) == 4
    assert coach.call("GET", "/api/learn/stats")[0] == 200
    status, sheet = coach.call("GET", f"/api/learn/decks/{deck['id']}/export", raw=True)
    assert status == 200 and b"front,back" in sheet
    # A member's own deck is not another member's to copy.
    assert candidate.call("POST", f"/api/learn/decks/{deck['id']}/copy", {})[0] == 404
    # Another organization sees nothing of this one.
    other = build_org(cloud, "Other Org", slug_email="other.test")
    cloud.billing.subscribe_app(other.owner, "learn")
    theirs = sign_in(cloud, other.owner)
    assert theirs.call("GET", "/api/learn/sales")[1]["sales"] == []
    assert theirs.call("POST", f"/api/learn/sales/{sale_id}/paid", {})[0] == 404
    assert theirs.call("GET", f"/api/learn/decks/{deck['id']}/export")[0] == 404
