"""Social and Family: regressions from the third round of testing.

* A Marketplace listing is the seller's own: it never borrows the household's or organization's payment link,
  public header or revenue ledger unless someone who runs the business sells it for the business.
* A Marketplace chat between strangers closes once its listing is gone (removed, hidden, no longer visible to
  the buyer, or long sold).
* Money is typed, not guessed ("ten" is an error, not "Free"); a giveaway never writes a 0 revenue line; a sold
  listing that is removed still counts in "Earned from sales" as it does in the Business hub.
* The family board never shows that a gift was taken; a grown-up can free a gift a visitor took on the board.
* The first Social screen of a new account survives "database schema has changed" while it is migrated.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import timedelta

import pytest
from cloud_support import build_org, sign_in
from life_support import Household

import test_careers
from aira import suite
from aira.suite import kit, life, public
from aira.suite.family import family_routes
from aira.suite.family import service as family
from aira.suite.family import store as family_store
from aira.suite.social import db, market, service, social_routes, store

env = test_careers.env
api = test_careers.api
stub = test_careers.stub


class _NoModel:
    def model(self, prompt):
        return None


@pytest.fixture(autouse=True)
def _no_model():
    previous = suite.gateway()
    suite.set_gateway(_NoModel())
    yield
    suite.set_gateway(previous)


@pytest.fixture(autouse=True)
def _social_db(tmp_path):
    db.set_path(lambda: tmp_path / "social-selling.sqlite3")
    db.forget()
    yield
    db.reset_path()
    db.forget()


@pytest.fixture
def sc():
    connection = db.connect()
    yield connection
    connection.close()


def _join(sc, key, name, *, age="adult", home="home-1", **raw):
    ident = {
        "key": key,
        "profile": {"id": key, "name": name, "emoji": "🙂", "age_group": age},
        "home": home,
        "moderator": False,
        "hosted": False,
    }
    service.create_account(sc, ident, {"handle": name.lower(), **raw})
    return service.me(sc, ident)


def _friends(sc, a, b):
    service.request_friend(sc, a, b["id"])
    service.request_friend(sc, b, a["id"])
    assert service.are_friends(sc, a["id"], b["id"])


# ---------------------------------------------------------------------------
# unit: the Marketplace chat, money and earnings
# ---------------------------------------------------------------------------


def test_a_marketplace_chat_closes_once_the_listing_is_gone(sc):
    pat = _join(sc, "p:pat", "Pat")
    sam = _join(sc, "p:sam", "Sam", home="home-2", open_messages=False)
    _friends(sc, pat, sam)
    chat = service.open_direct(sc, pat, sam["id"])
    service.send(sc, pat, chat["id"], {"text": "hi"})
    service.remove_friend(sc, sam, pat["id"])
    with pytest.raises(PermissionError):
        service.send(sc, pat, chat["id"], {"text": "still there?"})
    # Sam sells a sofa to everyone: Pat may ask about it, in the same old chat, while it is for sale
    sofa = market.create(sc, sam, {"title": "Sofa", "price": "50"})
    asked = market.ask_seller(sc, pat, sofa["id"], {})
    assert asked["id"] == chat["id"]
    service.send(sc, pat, chat["id"], {"text": "Can I come at 5?"})
    service.send(sc, sam, chat["id"], {"text": "Yes"})
    # removed: the chat is an ordinary one again, and Sam takes messages from friends only
    market.delete(sc, sam, sofa["id"])
    with pytest.raises(PermissionError):
        service.send(sc, pat, chat["id"], {"text": "and now?"})
    # a friends-only listing the buyer can no longer see does not keep it open either
    lamp = market.create(sc, sam, {"title": "Lamp", "price": "5"})
    market.ask_seller(sc, pat, lamp["id"], {})
    market.update(sc, sam, lamp["id"], {"audience": "friends"})
    with pytest.raises(PermissionError):
        service.send(sc, pat, chat["id"], {"text": "?"})
    # sold: open for the pick-up, closed two weeks later
    market.update(sc, sam, lamp["id"], {"audience": "public"})
    market.set_status(sc, sam, lamp["id"], {"status": "sold"})
    service.send(sc, pat, chat["id"], {"text": "On my way"})
    old = store.stamp(store.utcnow() - timedelta(days=15))
    sc.execute("UPDATE so_listings SET sold_at = ? WHERE id = ?", (old, lamp["id"]))
    sc.commit()
    with pytest.raises(PermissionError):
        service.send(sc, pat, chat["id"], {"text": "Hello again"})


def test_typed_prices_are_checked_and_a_removed_sale_still_counts(sc):
    pat = _join(sc, "p:pat", "Pat")
    for bad in ("ten", "12abc"):
        with pytest.raises(ValueError, match="number like 12.50"):
            market.create(sc, pat, {"title": "Typo price", "price": bad})
    bike = market.create(sc, pat, {"title": "Bike", "price": "12"}, currency="EUR")
    assert bike["price_cents"] == 1200
    # emptying the price in the form gives it away (it used to keep the old price)
    assert market.update(sc, pat, bike["id"], {"price": ""})["price"] == "Free"
    market.update(sc, pat, bike["id"], {"price": "30"})
    with pytest.raises(ValueError, match="number"):
        market.set_status(sc, pat, bike["id"], {"status": "sold", "sold_price": "ten"})
    market.set_status(sc, pat, bike["id"], {"status": "sold", "sold_price": "28"})
    market.delete(sc, pat, bike["id"])
    summary = market.selling(sc, pat)["summary"]
    assert summary["sold"] == 1 and summary["earned"] == [
        {"currency": "EUR", "cents": 2800, "text": "€28.00"}
    ]
    assert market.selling(sc, pat)["listings"] == []
    # a giveaway counts as sold, not as money
    pots = market.create(sc, pat, {"title": "Pots"}, currency="EUR")
    given = market.set_status(
        sc, pat, pots["id"], {"status": "sold", "sold_price": "0"}
    )
    assert given["sold_cents"] == 0
    assert market.selling(sc, pat)["summary"]["earned"][0]["cents"] == 2800


def test_who_may_sell_for_the_business():
    adult = {"age_group": "adult"}
    assert market.may_sell_for_business(adult)
    assert not market.may_sell_for_business({"age_group": "teen"})
    shop = {"name": "Acme", "payment_link": "http://not-secure.example"}
    assert market.business_for(adult, shop) == {"name": "Acme", "payment_link": ""}
    previous = suite._actor_resolver
    try:
        for role, allowed in (
            ("owner", True),
            ("admin", True),
            ("coach", True),
            ("candidate", False),
            ("billing", False),
            ("auditor", False),
        ):
            suite.set_actor_resolver(lambda role=role: {"role": role})
            assert market.may_sell_for_business(adult) is allowed, role
            assert (market.business_for(adult, shop) is not None) is allowed
    finally:
        suite.set_actor_resolver(previous)


class _Racing:
    """A request whose first connect meets a migration running in another request."""

    def __init__(self, error="database schema has changed"):
        self.calls, self.error = 0, error

    def connect(self):
        self.calls += 1
        if self.calls == 1:
            raise sqlite3.OperationalError(self.error)
        return "connection"


def test_the_first_screen_survives_a_migration_running_at_the_same_time():
    racing = _Racing()
    assert social_routes._account_db(racing) == "connection" and racing.calls == 2
    with pytest.raises(sqlite3.OperationalError):
        social_routes._account_db(_Racing("disk I/O error"))
    # the first Family screen asks several things at once too
    racing = _Racing()
    assert family_routes._connect(racing) == "connection" and racing.calls == 2
    with pytest.raises(sqlite3.OperationalError):
        family_routes._connect(_Racing("disk I/O error"))


# ---------------------------------------------------------------------------
# unit: the family board keeps the surprise; a grown-up frees a prank claim
# ---------------------------------------------------------------------------


class _Req:
    def __init__(self):
        self.user = {"email": "pat@home.example"}

    def header(self, name, default=""):
        return default


@pytest.fixture
def home(tmp_path):
    kit.forget_schema()
    public.set_index(public._MemoryIndex())
    connection = sqlite3.connect(tmp_path / "family-selling.sqlite3")
    connection.row_factory = sqlite3.Row
    family_store.ensure(connection)
    pat = life.current(_Req(), connection)
    ana = life.add(connection, pat, {"name": "Ana", "age_group": "adult"})
    mia = life.add(connection, pat, {"name": "Mia", "age_group": "child"})
    yield connection, pat, ana, mia
    connection.close()


def _wish(connection, viewer, wish_id):
    for item in family.wish_lists(connection, viewer):
        for wish in item["wishes"]:
            if wish["id"] == wish_id:
                return wish
    raise AssertionError("wish not found")


def test_the_board_never_tells_and_a_grown_up_frees_a_board_claim(home):
    connection, pat, ana, mia = home
    scarf = family.add_wish(connection, ana, {"title": "Blue scarf"})
    family.claim_wish(connection, pat, scarf["id"], True)
    family.save_board_settings(connection, pat, {"show_wishes": True})
    board = family.board_page(connection)
    shown = next(w for item in board["wish_lists"] for w in item["wishes"])
    assert shown["title"] == "Blue scarf" and "claimed" not in json.dumps(board)
    # a relative who tries to take it too learns then
    with pytest.raises(ValueError, match="already"):
        family.board_claim(
            connection, {"wish_id": scarf["id"], "name": "Granny"}, {"client": "x"}
        )
    # only Pat takes his own claim back
    assert not _wish(connection, mia, scarf["id"])["can_unclaim"]
    with pytest.raises(PermissionError):
        family.claim_wish(connection, mia, scarf["id"], False)
    family.claim_wish(connection, pat, scarf["id"], False)
    # a visitor's claim (a mistake or a prank): a grown-up of the family frees it, not a child, not Ana herself
    family.board_claim(
        connection, {"wish_id": scarf["id"], "name": "Prankster"}, {"client": "x"}
    )
    seen = _wish(connection, pat, scarf["id"])
    assert seen["claimed_on_board"] and seen["claimed_by"] == "Prankster"
    assert (
        seen["can_unclaim"] and not _wish(connection, mia, scarf["id"])["can_unclaim"]
    )
    with pytest.raises(PermissionError, match="grown-up"):
        family.claim_wish(connection, mia, scarf["id"], False)
    with pytest.raises(PermissionError):
        family.claim_wish(connection, ana, scarf["id"], False)
    freed = family.claim_wish(connection, pat, scarf["id"], False)
    assert freed["claimed"] is False
    assert "claimed" not in _wish(connection, ana, scarf["id"])


# ---------------------------------------------------------------------------
# the single-user server
# ---------------------------------------------------------------------------


def _join_http(person, handle):
    return person.post("/api/social/me", {"handle": handle}, expect=201)["account"]


def _anonymous(env, path):
    import urllib.request

    request = urllib.request.Request(env.base + path, method="GET")
    with urllib.request.urlopen(request, timeout=30) as response:
        return json.loads(response.read() or b"{}")


def test_a_household_member_sells_as_themselves_not_as_the_bakery(env, api):
    kit.forget_schema()
    public.set_default_index(test_careers.app._public_link_index)
    db.reset_path()
    db.forget()
    house = Household(env, api)
    pat = house.owner
    ana = house.add("Ana")
    _join_http(pat, "patbakes")
    _join_http(ana, "ana")
    pat.post(
        "/api/suite/business",
        {"name": "Pat's Bakery", "payment_link": "https://pay.example/pats-bakery"},
    )
    setup = ana.get("/api/social/market/setup")
    assert setup["business"] == {"name": "Pat's Bakery", "has_payment_link": True}
    assert "EUR" in setup["currencies"]
    bike = ana.post("/api/social/market", {"title": "Bike", "price": "20"}, expect=201)[
        "listing"
    ]
    assert bike["payment_link"] == "" and bike["for_business"] is False
    assert (
        ana.call("POST", "/api/social/market", {"title": "Typo", "price": "ten"})[0]
        == 400
    )
    # the public page carries Ana's name, not the bakery's (the renderer heads it with the seller)
    link = ana.post(f"/api/social/market/{bike['id']}/share", {}, expect=201)["link"]
    page = _anonymous(env, f"/api/public/{link['token']}")["page"]["listing"]
    assert page["for_business"] is False and page["payment_link"] == ""
    assert page["seller"]["name"] == "Ana"
    # her sale is her own: "Earned from sales", not the bakery's revenue
    ana.post(
        f"/api/social/market/{bike['id']}/status",
        {"status": "sold", "sold_price": "18"},
    )
    assert ana.get("/api/social/market/mine")["summary"]["earned"][0]["cents"] == 1800
    assert pat.get("/api/suite/revenue?app=social")["lines"] == []
    # Pat sells cakes for the bakery: its link, its ledger; removing the sold listing keeps the line counted
    cakes = pat.post(
        "/api/social/market",
        {"title": "Cakes", "price": "30", "for_business": True},
        expect=201,
    )["listing"]
    assert cakes["payment_link"] == "https://pay.example/pats-bakery"
    pat.post(f"/api/social/market/{cakes['id']}/status", {"status": "sold"})
    pat.ok("DELETE", f"/api/social/market/{cakes['id']}")
    revenue = pat.get("/api/suite/revenue?app=social")
    assert [(line["amount_cents"], line["voided_at"]) for line in revenue["lines"]] == [
        (3000, None)
    ]
    assert pat.get("/api/social/market/mine")["summary"]["earned"][0]["cents"] == 3000
    # a giveaway for the business writes no 0 line; turning "for the business" off takes a sale back out
    bread = pat.post(
        "/api/social/market", {"title": "Bread", "for_business": True}, expect=201
    )["listing"]
    pat.post(
        f"/api/social/market/{bread['id']}/status",
        {"status": "sold", "sold_price": "0"},
    )
    rolls = pat.post(
        "/api/social/market",
        {"title": "Rolls", "price": "4", "for_business": True},
        expect=201,
    )["listing"]
    pat.post(f"/api/social/market/{rolls['id']}/status", {"status": "sold"})
    pat.ok("PATCH", f"/api/social/market/{rolls['id']}", {"for_business": False})
    counted = [
        (line["label"], line["amount_cents"])
        for line in pat.get("/api/suite/revenue?app=social")["lines"]
        if line["voided_at"] is None
    ]
    assert counted == [("Sold: Cakes", 3000)]
    # an invalid highest price is a clear 400
    status, error = ana.call("GET", "/api/social/market?max_price=ten")
    assert status == 400 and "number like 12.50" in error["error"]


# ---------------------------------------------------------------------------
# the hosted build
# ---------------------------------------------------------------------------


def test_hosted_members_never_sell_with_the_organizations_link_or_books(cloud):
    kit.forget_schema()
    db.forget()
    org = build_org(cloud)
    owner = sign_in(cloud, org.owner)
    cloud.billing.subscribe_app(org.owner, "social")
    assert (
        owner.call(
            "POST",
            "/api/suite/business",
            {"payment_link": "https://pay.example/acme-org", "currency": "EUR"},
        )[0]
        == 200
    )
    candidate = sign_in(cloud, org.candidate)
    auditor = sign_in(cloud, org.auditor)
    coach = sign_in(cloud, org.coach)
    for person, handle in ((candidate, "hcand"), (auditor, "haud"), (coach, "hcoach")):
        assert person.call("POST", "/api/social/me", {"handle": handle})[0] == 201
    status, setup = candidate.call("GET", "/api/social/market/setup")
    assert status == 200 and setup["business"] is None and setup["currency"] == "EUR"
    status, chair = candidate.call(
        "POST", "/api/social/market", {"title": "Chair", "price": "20"}
    )
    assert status == 201 and chair["listing"]["payment_link"] == ""
    assert (
        candidate.call(
            "POST",
            "/api/social/market",
            {"title": "Chair", "price": "20", "for_business": True},
        )[0]
        == 403
    )
    assert (
        auditor.call(
            "POST", "/api/social/market", {"title": "Desk", "for_business": True}
        )[0]
        == 403
    )
    lid = chair["listing"]["id"]
    status, _ = candidate.call(
        "POST", f"/api/social/market/{lid}/status", {"status": "sold"}
    )
    assert status == 200
    status, revenue = owner.call("GET", "/api/suite/revenue?app=social")
    assert status == 200 and revenue["lines"] == []
    status, mine = candidate.call("GET", "/api/social/market/mine")
    assert mine["summary"]["earned"][0]["cents"] == 2000
    # the coach runs the organization's business apps: selling for it uses its link and its books
    status, setup = coach.call("GET", "/api/social/market/setup")
    assert setup["business"]["has_payment_link"] is True
    status, desk = coach.call(
        "POST",
        "/api/social/market",
        {"title": "Desk", "price": "80", "for_business": True},
    )
    assert (
        status == 201
        and desk["listing"]["payment_link"] == "https://pay.example/acme-org"
    )
    coach.call(
        "POST", f"/api/social/market/{desk['listing']['id']}/status", {"status": "sold"}
    )
    status, revenue = owner.call("GET", "/api/suite/revenue?app=social")
    assert [line["amount_cents"] for line in revenue["lines"]] == [8000]
