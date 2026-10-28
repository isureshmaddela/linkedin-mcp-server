"""Social: regressions found by using it (edited label, live reactions, old chats and young people, groups and
pages that could not be changed) and the features added after: a grown-up says yes to a child's new friends,
the Marketplace (selling records the sale in the seller's ledger; a listing page for people without Aira) and
downloading your own data. Unit tests on the service, then the single-user server, then the hosted build."""

from __future__ import annotations

import json

import pytest
from cloud_support import build_org, sign_in
from life_support import Household

import test_careers
from aira import suite
from aira.suite import kit, public
from aira.suite.social import db, market, service, store

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
    db.set_path(lambda: tmp_path / "social-market.sqlite3")
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


def _fresh(sc, account):
    return service.account(sc, account["id"])


@pytest.fixture
def people(sc):
    """Pat (adult) and Mia (child) at home-1; Sam (adult) and Teo (teen) elsewhere."""
    pat = _join(sc, "p:pat", "Pat")
    mia = _join(sc, "p:mia", "Mia", age="child")
    sam = _join(sc, "p:sam", "Sam", home="home-2")
    teo = _join(sc, "p:teo", "Teo", age="teen", home="home-3")
    return pat, mia, sam, teo


# ---------------------------------------------------------------------------
# regressions
# ---------------------------------------------------------------------------


def test_a_comment_or_reaction_does_not_mark_the_post_edited(sc, people):
    pat, mia, sam, teo = people
    _friends(sc, pat, sam)
    post = service.create_post(sc, pat, {"text": "Garden day"})
    first = service.feed(sc, sam)
    service.comment(sc, sam, post["id"], {"text": "Lovely"})
    assert (
        service.visible_post(sc, sam, post["id"])
        and not service.post_view(sc, sam, service.visible_post(sc, sam, post["id"]))[
            "edited"
        ]
    )
    # a reaction is a change the live feed picks up (before, others' reactions never arrived)
    changed = service.feed(sc, sam, since=first["now"])
    service.react(sc, pat, post["id"], "🎉")
    again = service.feed(sc, sam, since=changed["now"])
    assert [p["reactions"] for p in again["posts"]] == [{"🎉": 1}]
    assert again["posts"][0]["edited"] is False
    assert service.edit_post(sc, pat, post["id"], {"text": "Garden day!"})["edited"]


def test_an_old_chat_cannot_be_used_to_keep_messaging_a_teen_after_unfriending(
    sc, people
):
    pat, mia, sam, teo = people
    _friends(sc, sam, teo)
    chat = service.open_direct(sc, sam, teo["id"])
    service.send(sc, sam, chat["id"], {"text": "hi"})
    service.remove_friend(sc, teo, sam["id"])
    with pytest.raises(PermissionError, match="friends"):
        service.send(sc, sam, chat["id"], {"text": "still there?"})
    assert (
        service.chat_summary(
            sc,
            sam,
            store.row(sc, "SELECT * FROM so_chats WHERE id = ?", (chat["id"],)) or {},
        )["can_send"]
        is False
    )
    # the same for an adult who closed their messages to people who are not friends
    service.update_account(sc, pat, {"open_messages": True})
    talk = service.open_direct(sc, sam, _fresh(sc, pat)["id"])
    service.send(sc, sam, talk["id"], {"text": "hello"})
    service.update_account(sc, _fresh(sc, pat), {"open_messages": False})
    with pytest.raises(PermissionError):
        service.send(sc, sam, talk["id"], {"text": "again"})
    # reading the history still works
    assert [m["text"] for m in service.messages(sc, sam, talk["id"])["messages"]] == [
        "hello"
    ]


def test_group_and_page_admins_change_and_delete_them(sc, people):
    pat, mia, sam, teo = people
    group = service.create_group(sc, pat, {"name": "Walkers"})
    service.join_group(sc, sam, group["id"])
    with pytest.raises(PermissionError):
        service.update_group(sc, sam, group["id"], {"name": "Mine now"})
    assert (
        service.update_group(
            sc, pat, group["id"], {"name": "Morning walkers", "visibility": "private"}
        )["name"]
        == "Morning walkers"
    )
    post = service.create_post(
        sc, sam, {"text": "See you at 7", "group_id": group["id"]}
    )
    before = service.feed(sc, sam)
    assert post["id"] in {p["id"] for p in before["posts"]}
    with pytest.raises(PermissionError):
        service.delete_group(sc, sam, group["id"])
    service.delete_group(sc, pat, group["id"])
    assert service.feed(sc, sam, since=before["now"])["removed"] == [post["id"]]
    with pytest.raises(LookupError):
        service.group_row(sc, group["id"])
    page = service.create_page(sc, pat, {"name": "Bakery"})
    service.follow(sc, sam, "page", page["id"], True)
    with pytest.raises(ValueError):
        service.update_page(sc, pat, page["id"], {"link": "http://insecure.example"})
    assert (
        service.update_page(sc, pat, page["id"], {"category": "Bread"})["category"]
        == "Bread"
    )
    with pytest.raises(PermissionError):
        service.delete_page(sc, sam, page["id"])
    service.delete_page(sc, pat, page["id"])
    assert service.pages(sc, sam)["mine"] == []


# ---------------------------------------------------------------------------
# a grown-up says yes to a child's new friends
# ---------------------------------------------------------------------------


def test_a_childs_new_friend_waits_for_a_grown_up(sc, people):
    pat, mia, sam, teo = people
    ana = _join(sc, "p:ana", "Ana")  # Mia's household too
    # family needs no yes
    assert service.request_friend(sc, mia, ana["id"]) == {"relation": "request_sent"}
    # someone from outside: nothing reaches Sam until a grown-up says yes
    assert service.request_friend(sc, mia, sam["id"]) == {"relation": "awaiting_parent"}
    assert service.relation(sc, mia, sam) == "awaiting_parent"
    assert service.friends_overview(sc, sam)["incoming"] == []
    assert [p["id"] for p in service.friends_overview(sc, mia)["awaiting_parent"]] == [
        sam["id"]
    ]
    asking = service.friends_overview(sc, pat)["children_asking"]
    assert [(a["child"]["id"], a["other"]["id"], a["action"]) for a in asking] == [
        (mia["id"], sam["id"], "request")
    ]
    assert "guardian_request" in {
        n["kind"] for n in service.notifications(sc, pat)["notifications"]
    }
    # Today shows the grown-up a "Say yes" button
    lines = service.today_cards(sc, {"id": "pat"})[0]["lines"]
    yes = next(
        line for line in lines if "Mia wants to be friends with Sam" in line["text"]
    )
    assert yes["action"][
        "path"
    ] == f"/api/social/guardian/{mia['id']}/{sam['id']}" and yes["action"]["body"] == {
        "approve": True
    }
    # only a grown-up of Mia's household answers
    for stranger in (sam, teo, mia):
        with pytest.raises(LookupError):
            service.answer_guardian(sc, stranger, mia["id"], sam["id"], True)
    assert service.answer_guardian(sc, ana, mia["id"], sam["id"], True) == {
        "relation": "request_sent"
    }
    assert [p["id"] for p in service.friends_overview(sc, sam)["incoming"]] == [
        mia["id"]
    ]
    assert service.guardian_requests(sc, pat) == []
    assert service.request_friend(sc, sam, mia["id"]) == {"relation": "friends"}
    assert "guardian_answer" in {
        n["kind"] for n in service.notifications(sc, mia)["notifications"]
    }


def test_a_child_accepting_an_outside_request_also_waits_and_no_means_no(sc, people):
    pat, mia, sam, teo = people
    # Sam asked while Mia's profile was still a teen's; now she is a child, accepting waits for a grown-up
    sc.execute("UPDATE so_accounts SET age_group = 'teen' WHERE id = ?", (mia["id"],))
    service.request_friend(sc, sam, mia["id"])
    sc.execute("UPDATE so_accounts SET age_group = 'child' WHERE id = ?", (mia["id"],))
    sc.commit()
    mia = _fresh(sc, mia)
    assert service.request_friend(sc, mia, sam["id"]) == {"relation": "awaiting_parent"}
    assert not service.are_friends(sc, mia["id"], sam["id"])
    assert service.friends_overview(sc, mia)["incoming"] == []
    assert [a["action"] for a in service.guardian_requests(sc, pat)] == ["accept"]
    service.answer_guardian(sc, pat, mia["id"], sam["id"], False)
    assert service.friendship(sc, mia["id"], sam["id"]) is None
    assert service.relation(sc, mia, sam) == "none"
    # children in two households cannot even find each other (unchanged): no request to wait for
    kid = _join(sc, "p:kid", "Kid", age="child", home="home-2")
    with pytest.raises(LookupError):
        service.request_friend(sc, kid, mia["id"])
    # cancelling and blocking clear the waiting request; a child alone has nobody to ask
    service.request_friend(sc, mia, sam["id"])
    service.remove_friend(sc, mia, sam["id"])
    assert service.guardian_requests(sc, pat) == []
    alone = _join(sc, "p:alone", "Alone", age="child", home="home-9")
    with pytest.raises(PermissionError, match="grown-up"):
        service.request_friend(sc, alone, sam["id"])


# ---------------------------------------------------------------------------
# the Marketplace
# ---------------------------------------------------------------------------


def _bike(sc, seller, **extra):
    return market.create(
        sc,
        seller,
        {
            "title": "Child's bike",
            "price_cents": 4500,
            "category": "kids",
            "condition": "good",
            "place": "Riverside",
            "photos": ["https://img.example/bike.jpg"],
            **extra,
        },
        currency="GBP",
    )


def test_selling_browsing_saving_and_who_sees_what(sc, people):
    pat, mia, sam, teo = people
    for young in (mia, teo):
        with pytest.raises(PermissionError, match="adults"):
            _bike(sc, young)
    bike = _bike(sc, pat)
    assert bike["price"] == "£45.00" and bike["status"] == "available" and bike["mine"]
    sofa = market.create(
        sc,
        pat,
        {"title": "Sofa", "price": "120", "category": "home", "audience": "friends"},
    )
    free = market.create(sc, pat, {"title": "Garden pots", "category": "garden"})
    assert free["price"] == "Free" and sofa["price_cents"] == 12000
    with pytest.raises(ValueError):
        market.create(
            sc, pat, {"title": "X", "photos": ["http://insecure.example/a.jpg"]}
        )
    with pytest.raises(ValueError):
        market.create(sc, pat, {"title": "X", "payment_link": "http://pay.example"})
    # a listing is the seller's own: the business's payment link only when they sell it for the business
    shop = {"name": "Pat's Bakery", "payment_link": "https://pay.example/bakery"}
    lamp = market.create(sc, pat, {"title": "Lamp", "payment_link": ""}, business=shop)
    assert lamp["payment_link"] == "" and lamp["for_business"] is False
    cake = market.create(
        sc, pat, {"title": "Cake", "for_business": True}, business=shop
    )
    assert cake["payment_link"] == "https://pay.example/bakery" and cake["for_business"]
    with pytest.raises(PermissionError, match="run the business"):
        market.create(sc, pat, {"title": "Cake", "for_business": True})
    for item in (lamp, cake):
        market.delete(sc, pat, item["id"])
    # Sam is not Pat's friend: he sees the public ones, not the sofa
    titles = [item["title"] for item in market.browse(sc, sam)["listings"]]
    assert titles == ["Garden pots", "Child's bike"]
    assert [i["title"] for i in market.browse(sc, sam, sort="dearest")["listings"]] == [
        "Child's bike",
        "Garden pots",
    ]
    assert [i["title"] for i in market.browse(sc, sam, q="riverside")["listings"]] == [
        "Child's bike"
    ]
    assert [
        i["title"] for i in market.browse(sc, sam, category="garden")["listings"]
    ] == ["Garden pots"]
    assert [i["title"] for i in market.browse(sc, sam, max_price="10")["listings"]] == [
        "Garden pots"
    ]
    with pytest.raises(LookupError):
        market.visible(sc, sam, sofa["id"])
    _friends(sc, pat, sam)
    assert "Sofa" in [i["title"] for i in market.browse(sc, sam)["listings"]]
    # children never see it; teens may look
    with pytest.raises(PermissionError):
        market.browse(sc, mia)
    with pytest.raises(LookupError):
        market.visible(sc, mia, bike["id"])
    assert market.browse(sc, teo)["listings"]
    # save, then the seller sees how it is going
    market.save(sc, sam, bike["id"], True)
    assert [i["id"] for i in market.saved(sc, sam)] == [bike["id"]]
    market.seen(sc, sam, market.visible(sc, sam, bike["id"]))
    mine = {i["id"]: i for i in market.selling(sc, pat)["listings"]}
    assert mine[bike["id"]]["saves"] == 1 and mine[bike["id"]]["views"] == 1
    # blocking hides it both ways
    service.block(sc, sam, pat["id"])
    assert market.browse(sc, sam)["listings"] == [] and market.saved(sc, sam) == []
    # only the seller changes it
    with pytest.raises(LookupError):
        market.update(sc, sam, bike["id"], {"title": "mine"})
    assert market.update(sc, pat, bike["id"], {"price": "40"})["price"] == "£40.00"


def test_asking_the_seller_opens_a_chat_that_works_without_being_friends(sc, people):
    pat, mia, sam, teo = people
    bike = _bike(sc, pat)
    assert not service.can_message(sc, sam, pat)
    chat = market.ask_seller(sc, sam, bike["id"], {})
    assert chat["listing_id"] == bike["id"] and chat["can_send"]
    first = service.messages(sc, pat, chat["id"])["messages"]
    assert first[0]["text"] == "Hi! Is “Child's bike” still available?"
    service.send(sc, pat, chat["id"], {"text": "Yes, come by at 5"})
    assert market.ask_seller(sc, sam, bike["id"], {"text": "Great"})["id"] == chat["id"]
    # a teen asks only sellers who are friends; nobody asks about their own or a sold thing
    with pytest.raises(PermissionError):
        market.ask_seller(sc, teo, bike["id"], {})
    with pytest.raises(ValueError):
        market.ask_seller(sc, pat, bike["id"], {})
    market.set_status(sc, pat, bike["id"], {"status": "sold"})
    with pytest.raises(ValueError):
        market.ask_seller(sc, sam, bike["id"], {})
    # blocking ends the Marketplace chat too
    service.block(sc, pat, sam["id"])
    with pytest.raises(PermissionError):
        service.send(sc, sam, chat["id"], {"text": "?"})


def test_marking_sold_counts_what_selling_earned(sc, people):
    pat, mia, sam, teo = people
    bike = _bike(sc, pat)
    pots = market.create(sc, pat, {"title": "Pots", "price": "5"}, currency="GBP")
    reserved = market.set_status(sc, pat, bike["id"], {"status": "reserved"})
    assert reserved["status"] == "reserved" and reserved["sold"] == ""
    with pytest.raises(ValueError, match="number"):
        market.set_status(sc, pat, bike["id"], {"status": "sold", "sold_price": "lots"})
    sold = market.set_status(
        sc, pat, bike["id"], {"status": "sold", "sold_price": "42.50", "buyer": "Sam"}
    )
    assert (
        sold["sold_cents"] == 4250
        and sold["buyer"] == "Sam"
        and sold["sold"] == "£42.50"
    )
    market.set_status(sc, pat, pots["id"], {"status": "sold"})
    summary = market.selling(sc, pat)["summary"]
    assert summary["sold"] == 2 and summary["earned"] == [
        {"currency": "GBP", "cents": 4750, "text": "£47.50"}
    ]
    # sold things leave the Marketplace; available again brings it back and the money back out
    assert market.browse(sc, sam)["listings"] == []
    market.set_status(sc, pat, pots["id"], {"status": "available"})
    assert market.selling(sc, pat)["summary"]["earned"][0]["cents"] == 4250
    with pytest.raises(ValueError):
        market.set_status(sc, pat, pots["id"], {"status": "gone"})
    with pytest.raises(LookupError):
        market.set_status(sc, sam, pots["id"], {"status": "sold"})


def test_listings_are_reported_hidden_shared_and_erased(sc, people):
    pat, mia, sam, teo = people
    bike = _bike(sc, pat, payment_link="https://pay.example/pat")
    service.report(
        sc, sam, {"target_type": "listing", "target_id": bike["id"], "reason": "Scam"}
    )
    queue = service.reports(sc)
    assert (
        queue[0]["preview"] == "Child's bike" and queue[0]["author"]["id"] == pat["id"]
    )
    page = market.public_page(sc, bike["id"])
    assert (
        page["available"]
        and page["listing"]["payment_link"] == "https://pay.example/pat"
    )
    assert "seller_id" not in page["listing"] and page["listing"]["seller"] == {
        "name": "Pat",
        "avatar": "🙂",
    }
    market.interested(
        sc,
        bike["id"],
        {"name": "Lee", "contact": "lee@example.com", "message": "Tomorrow?"},
    )
    note = service.notifications(sc, pat)["notifications"][0]
    assert (
        note["kind"] == "market_interest"
        and "lee@example.com" in note["text"]
        and note["ref_id"] == bike["id"]
    )
    with pytest.raises(ValueError):
        market.interested(sc, bike["id"], {"name": "", "contact": "x"})
    service.moderate(sc, "operator", queue[0]["id"], "hide")
    assert market.public_page(sc, bike["id"])["available"] is False
    with pytest.raises(LookupError):
        market.visible(sc, sam, bike["id"])
    # friends-only listings are never public pages; a person's export carries their listings; erasing removes them
    friends_only = _bike(sc, pat, audience="friends")
    assert market.public_page(sc, friends_only["id"])["available"] is False
    assert {
        item["id"] for item in service.export_account(sc, pat["id"])["listings"]
    } == {bike["id"], friends_only["id"]}
    service.erase_accounts(sc, [pat["id"]])
    assert store.rows(sc, "SELECT * FROM so_listings") == []


# ---------------------------------------------------------------------------
# the single-user build
# ---------------------------------------------------------------------------


def _join_http(person, handle):
    return person.post("/api/social/me", {"handle": handle}, expect=201)["account"]


def _anonymous(env, method, path, body=None):
    import urllib.error
    import urllib.request

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


def test_selling_on_this_computer_records_earnings_and_shares_a_listing_page(env, api):
    kit.forget_schema()
    public.set_default_index(test_careers.app._public_link_index)
    db.reset_path()
    db.forget()
    house = Household(env, api)
    pat = house.owner
    ana = house.add("Ana")
    mia = house.add("Mia", age_group="child", pin="2468")
    pat_account = _join_http(pat, "pat")
    _join_http(ana, "ana")
    mia_account = _join_http(mia, "mia")
    pat.post(
        "/api/suite/business",
        {"currency": "EUR", "payment_link": "https://pay.example/pat"},
    )
    listing = pat.post(
        "/api/social/market",
        {
            "title": "Piano lessons",
            "price_cents": 2500,
            "category": "services",
            "for_business": True,
        },
        expect=201,
    )["listing"]
    assert (
        listing["currency"] == "EUR"
        and listing["payment_link"] == "https://pay.example/pat"
    )
    assert mia.call("POST", "/api/social/market", {"title": "Toy"})[0] == 403
    assert mia.call("GET", "/api/social/market")[0] == 403
    assert [
        i["id"] for i in ana.get("/api/social/market?q=piano&sort=cheapest")["listings"]
    ] == [listing["id"]]
    assert ana.call("GET", "/api/social/market?category=boats")[0] == 400
    chat = ana.post(f"/api/social/market/{listing['id']}/ask", {}, expect=201)["chat"]
    assert pat.get(f"/api/social/chats/{chat['id']}/messages")["messages"][0][
        "text"
    ].startswith("Hi! Is “Piano lessons”")
    # sold: the money is in the household's revenue ledger (Business hub); available again takes it out
    pat.post(
        f"/api/social/market/{listing['id']}/status", {"status": "sold", "buyer": "Ana"}
    )
    lines = pat.get("/api/suite/revenue?app=social")["lines"]
    assert [
        (line["amount_cents"], line["currency"], line["customer"], line["voided_at"])
        for line in lines
    ] == [(2500, "EUR", "Ana", None)]
    pat.post(f"/api/social/market/{listing['id']}/status", {"status": "available"})
    assert pat.get("/api/suite/revenue?app=social")["summary"]["total_cents"] == 0
    # the public page: anyone opens it, says they are interested; turning it off ends it
    assert ana.call("POST", f"/api/social/market/{listing['id']}/share", {})[0] == 404
    link = pat.post(f"/api/social/market/{listing['id']}/share", {}, expect=201)["link"]
    status, page = _anonymous(env, "GET", f"/api/public/{link['token']}")
    assert status == 200 and page["page"]["listing"]["title"] == "Piano lessons"
    assert "seller_id" not in json.dumps(page)
    status, _ = _anonymous(
        env,
        "POST",
        f"/api/public/{link['token']}/interested",
        {"name": "Lee", "contact": "lee@example.com"},
    )
    assert status == 201
    assert (
        pat.get("/api/social/notifications")["notifications"][0]["kind"]
        == "market_interest"
    )
    pat.ok("DELETE", f"/api/social/market/{listing['id']}/share")
    assert _anonymous(env, "GET", f"/api/public/{link['token']}")[0] == 404
    # a person downloads their own data
    status, raw = pat.call("GET", "/api/social/me/export", raw=True)
    data = json.loads(raw)
    assert (
        status == 200
        and data["account"]["handle"] == "pat"
        and "person_key" not in data["account"]
    )
    assert [item["title"] for item in data["listings"]] == ["Piano lessons"]
    # the grown-up says yes to the child's new friend (Sam on another account)
    second = env.api()
    assert (
        second.call(
            "POST",
            "/api/auth/register",
            {"email": "sam-market@example.com", "password": test_careers.PASSWORD},
        )[0]
        == 201
    )
    sam = Household(env, second).owner
    sam_account = _join_http(sam, "sam")
    assert mia.post(f"/api/social/friends/{sam_account['id']}", {}) == {
        "relation": "awaiting_parent"
    }
    assert sam.get("/api/social/friends")["incoming"] == []
    assert (
        ana.get("/api/social/guardian")["requests"][0]["child"]["id"]
        == mia_account["id"]
    )
    assert (
        sam.call(
            "POST",
            f"/api/social/guardian/{mia_account['id']}/{sam_account['id']}",
            {"approve": True},
        )[0]
        == 404
    )
    today = pat.get("/api/life/today")
    social = [c for c in today["cards"] if c["app"] == "social"][0]
    action = next(
        line["action"]
        for line in social["lines"]
        if "Mia wants to be friends" in line["text"]
    )
    pat.post(action["path"], action["body"])
    assert [p["id"] for p in sam.get("/api/social/friends")["incoming"]] == [
        mia_account["id"]
    ]
    assert pat_account["id"]


# ---------------------------------------------------------------------------
# the hosted build
# ---------------------------------------------------------------------------


def test_hosted_members_sell_to_another_organization_and_earn(cloud):
    kit.forget_schema()
    db.forget()
    acme = build_org(cloud)
    owner = sign_in(cloud, acme.owner)
    assert owner.call("GET", "/api/social/market")[0] == 402
    cloud.billing.subscribe_app(acme.owner, "social")
    coach = sign_in(cloud, acme.coach)
    assert owner.call("POST", "/api/social/me", {"handle": "acmeowner"})[0] == 201
    assert coach.call("POST", "/api/social/me", {"handle": "acmecoach"})[0] == 201
    status, created = coach.call(
        "POST",
        "/api/social/market",
        {"title": "Desk", "price": "80", "for_business": True},
    )
    assert status == 201, created
    listing_id = created["listing"]["id"]
    other = build_org(cloud, "Other Org", slug_email="other.test")
    buyer = sign_in(cloud, other.owner)
    cloud.billing.subscribe_app(other.owner, "social")
    assert buyer.call("POST", "/api/social/me", {"handle": "otherbuyer"})[0] == 201
    status, found = buyer.call("GET", "/api/social/market?q=desk")
    assert status == 200 and [i["id"] for i in found["listings"]] == [listing_id]
    status, chat = buyer.call("POST", f"/api/social/market/{listing_id}/ask", {})
    assert status == 201
    status, thread = coach.call(
        "GET", f"/api/social/chats/{chat['chat']['id']}/messages"
    )
    assert status == 200 and len(thread["messages"]) == 1
    # only the seller marks it sold; the sale lands in the seller's organization's ledger
    assert (
        owner.call(
            "POST", f"/api/social/market/{listing_id}/status", {"status": "sold"}
        )[0]
        == 404
    )
    status, sold = coach.call(
        "POST",
        f"/api/social/market/{listing_id}/status",
        {"status": "sold", "buyer": "Other Org"},
    )
    assert status == 200 and sold["listing"]["sold_cents"] == 8000
    status, revenue = owner.call("GET", "/api/suite/revenue?app=social")
    assert status == 200, revenue
    assert [line["amount_cents"] for line in revenue["lines"]] == [8000]
    status, theirs = buyer.call("GET", "/api/suite/revenue?app=social")
    assert status == 200 and theirs["lines"] == []
