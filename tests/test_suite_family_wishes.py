"""Family: wish lists (secret "I'll get this"), the board showing them to relatives, the calendar as a file for
phones, and removing a date you added. Unit tests on the service, then the single-user server and the hosted build."""

from __future__ import annotations

import sqlite3
from datetime import timedelta

import pytest
from cloud_support import build_org, sign_in
from life_support import Household

import test_careers
from aira import suite
from aira.suite import kit, life, public
from aira.suite.family import service, store

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


@pytest.fixture
def db(tmp_path):
    kit.forget_schema()
    public.set_index(public._MemoryIndex())
    connection = sqlite3.connect(tmp_path / "family-wishes.sqlite3")
    connection.row_factory = sqlite3.Row
    store.ensure(connection)
    yield connection
    connection.close()


class Req:
    def __init__(self):
        self.user = {"email": "pat@home.example"}

    def header(self, name, default=""):
        return default


@pytest.fixture
def home(db):
    """Pat (owner), Ana (adult), Mia (child)."""
    pat = life.current(Req(), db)
    ana = life.add(db, pat, {"name": "Ana", "age_group": "adult"})
    mia = life.add(db, pat, {"name": "Mia", "age_group": "child"})
    return pat, ana, mia


def _list(db, viewer, owner):
    return next(
        item
        for item in service.wish_lists(db, viewer)
        if item["person"]["id"] == owner["id"]
    )


def test_wish_lists_keep_the_surprise(db, home):
    pat, ana, mia = home
    scarf = service.add_wish(
        db,
        ana,
        {"title": "A blue scarf", "link": "https://shop.example/scarf", "note": "Wool"},
    )
    assert scarf["mine"] is True and "claimed" not in scarf
    with pytest.raises(ValueError):
        service.add_wish(db, ana, {"title": "X", "link": "http://insecure.example"})
    with pytest.raises(ValueError):
        service.add_wish(db, ana, {"title": ""})
    # a grown-up looks after a child's list; nobody else edits someone's list
    kite = service.add_wish(db, pat, {"title": "A kite", "owner_id": mia["id"]})
    with pytest.raises(PermissionError):
        service.add_wish(db, mia, {"title": "Socks", "owner_id": ana["id"]})
    with pytest.raises(PermissionError):
        service.update_wish(db, pat, scarf["id"], {"title": "Mine now"})
    with pytest.raises(PermissionError):
        service.remove_wish(db, mia, scarf["id"])
    # Pat says he will get the scarf: Mia sees it is taken, Ana (it is for her) never does
    with pytest.raises(PermissionError):
        service.claim_wish(db, ana, scarf["id"], True)
    claimed = service.claim_wish(db, pat, scarf["id"], True)
    assert claimed["claimed_by_me"] and claimed["claimed_by"] == "Pat"
    assert _list(db, mia, ana)["wishes"][0]["claimed_by"] == "Pat"
    own = _list(db, ana, ana)["wishes"][0]
    assert "claimed" not in own and "claimed_by" not in own
    with pytest.raises(ValueError, match="already"):
        service.claim_wish(db, mia, scarf["id"], True)
    with pytest.raises(PermissionError):
        service.claim_wish(db, mia, scarf["id"], False)
    service.claim_wish(db, pat, scarf["id"], False)
    assert _list(db, mia, ana)["wishes"][0]["claimed"] is False
    # the count for birthdays never changes when someone claims (it would spoil the surprise)
    service.claim_wish(db, mia, scarf["id"], True)
    assert service.wish_counts(db) == {ana["id"]: 1, mia["id"]: 1}
    # received wishes cannot be claimed and stop counting; removing hides it
    service.update_wish(db, pat, kite["id"], {"received": True})
    with pytest.raises(ValueError):
        service.claim_wish(db, ana, kite["id"], True)
    assert service.wish_counts(db) == {ana["id"]: 1}
    service.remove_wish(db, ana, scarf["id"])
    assert _list(db, pat, ana)["wishes"] == []
    assert [item["person"]["id"] for item in service.wish_lists(db, mia)][0] == mia[
        "id"
    ]


def test_the_board_shows_wish_lists_only_when_the_family_says_so(db, home):
    pat, ana, mia = home
    wish = service.add_wish(db, mia, {"title": "Paint set"})
    assert service.board_page(db)["wish_lists"] == []
    with pytest.raises(LookupError):
        service.board_claim(
            db, {"wish_id": wish["id"], "name": "Granny"}, {"client": "1.2.3.4"}
        )
    with pytest.raises(PermissionError):
        service.save_board_settings(db, mia, {"show_wishes": True})
    assert (
        service.save_board_settings(db, pat, {"show_wishes": True})["show_wishes"]
        is True
    )
    page = service.board_page(db)
    assert page["wish_lists"] == [
        {
            "person": {
                "name": "Mia",
                "emoji": page["wish_lists"][0]["person"]["emoji"],
                "color": page["wish_lists"][0]["person"]["color"],
            },
            "wishes": [
                {
                    "id": wish["id"],
                    "title": "Paint set",
                    "link": "",
                    "note": "",
                    "received": False,
                    "created_at": wish["created_at"],
                }
            ],
        }
    ]
    with pytest.raises(ValueError):
        service.board_claim(
            db, {"wish_id": wish["id"], "name": ""}, {"client": "1.2.3.4"}
        )
    assert service.board_claim(
        db, {"wish_id": wish["id"], "name": "Granny"}, {"client": "1.2.3.4"}
    ) == {"claimed": True}
    # the board never says a gift was taken (the person it is for may open it); a second relative is told
    assert "claimed" not in service.board_page(db)["wish_lists"][0]["wishes"][0]
    with pytest.raises(ValueError, match="already"):
        service.board_claim(
            db, {"wish_id": wish["id"], "name": "Grandpa"}, {"client": "5.6.7.8"}
        )
    assert _list(db, pat, mia)["wishes"][0]["claimed_by"] == "Granny"
    assert "claimed" not in _list(db, mia, mia)["wishes"][0]


def test_the_calendar_downloads_for_a_phone(db, home):
    pat, ana, mia = home
    start = kit.utcnow() + timedelta(days=2)
    service.create_event(
        db,
        pat,
        {
            "title": "Lunch, at Gran's; bring cake",
            "starts_at": kit.iso(start),
            "place": "Gran's house",
            "notes": "Line one\nLine two",
        },
    )
    service.create_event(
        db,
        pat,
        {
            "title": "Camp",
            "all_day": True,
            "starts_at": (start + timedelta(days=5)).date().isoformat(),
            "ends_at": (start + timedelta(days=6)).date().isoformat(),
        },
    )
    service.create_event(
        db,
        pat,
        {
            "title": "Surprise for Ana",
            "starts_at": kit.iso(start),
            "invitees": [pat["id"], mia["id"]],
        },
    )
    text = service.calendar_ics(db, ana).decode("utf-8")
    assert text.startswith("BEGIN:VCALENDAR\r\n") and text.endswith("END:VCALENDAR\r\n")
    assert text.count("BEGIN:VEVENT") == 2 and "Surprise" not in text
    assert "SUMMARY:Lunch\\, at Gran's\\; bring cake" in text
    assert "DESCRIPTION:Line one\\nLine two\\nGoing: Pat" in text
    camp_start = (start + timedelta(days=5)).date()
    assert f"DTSTART;VALUE=DATE:{camp_start.strftime('%Y%m%d')}" in text
    assert (
        f"DTEND;VALUE=DATE:{(camp_start + timedelta(days=2)).strftime('%Y%m%d')}"
        in text
    )
    assert all(len(line.encode()) <= 75 for line in text.split("\r\n"))
    assert service.calendar_ics(db, mia).decode().count("BEGIN:VEVENT") == 3


def test_dates_say_who_added_them(db, home):
    pat, ana, mia = home
    added = service.add_date(db, mia, {"name": "Uncle Raj", "date": "1980-04-02"})
    assert added["created_by"] == mia["id"]
    service.delete_date(db, mia, added["id"])
    assert service.list_dates(db) == []


# ---------------------------------------------------------------------------
# the single-user build and the hosted build
# ---------------------------------------------------------------------------


def test_wish_lists_and_the_calendar_file_on_this_computer(env, api):
    kit.forget_schema()
    public.set_default_index(test_careers.app._public_link_index)
    house = Household(env, api)
    pat = house.owner
    ana = house.add("Ana")
    wish = ana.post("/api/family/wishlists", {"title": "Garden gloves"}, expect=201)[
        "wish"
    ]
    pat.post(f"/api/family/wishlists/{wish['id']}/claim", {})
    mine = next(
        item for item in ana.get("/api/family/wishlists")["lists"] if item["mine"]
    )
    assert (
        mine["wishes"][0]["title"] == "Garden gloves"
        and "claimed" not in mine["wishes"][0]
    )
    theirs = next(
        item
        for item in pat.get("/api/family/wishlists")["lists"]
        if item["person"]["id"] == ana.id
    )
    assert theirs["wishes"][0]["claimed_by_me"] is True
    assert ana.call("POST", f"/api/family/wishlists/{wish['id']}/claim", {})[0] == 403
    pat.ok("DELETE", f"/api/family/wishlists/{wish['id']}/claim")
    assert pat.get("/api/family/dates")["wishes"] == {ana.id: 1}
    pat.post(
        "/api/family/events",
        {"title": "Picnic", "starts_at": kit.iso(kit.utcnow() + timedelta(days=1))},
        expect=201,
    )
    status, body = ana.call("GET", "/api/family/events.ics", raw=True)
    assert status == 200 and b"SUMMARY:Picnic" in body
    # the board shows the lists only once a grown-up turns it on, and a relative claims one
    pat.post("/api/family/board", {"show_wishes": True})
    link = pat.post("/api/family/board/link", {})["link"]
    import json
    import urllib.request

    def anonymous(method, path, payload=None):
        request = urllib.request.Request(
            env.base + path,
            data=json.dumps(payload).encode() if payload is not None else None,
            method=method,
        )
        for name, value in {
            "Content-Type": "application/json",
            "X-Requested-With": "aira",
            "Origin": env.base,
        }.items():
            request.add_header(name, value)
        with urllib.request.urlopen(request, timeout=30) as response:
            return json.loads(response.read() or b"{}")

    page = anonymous("GET", f"/api/public/{link['token']}")["page"]
    assert page["wish_lists"][0]["wishes"][0]["title"] == "Garden gloves"
    assert anonymous(
        "POST",
        f"/api/public/{link['token']}/claim",
        {"wish_id": wish["id"], "name": "Granny"},
    ) == {"claimed": True}
    theirs = next(
        item
        for item in pat.get("/api/family/wishlists")["lists"]
        if item["person"]["id"] == ana.id
    )
    assert theirs["wishes"][0]["claimed_by"] == "Granny"


def test_hosted_members_share_wish_lists_inside_their_organization(cloud):
    kit.forget_schema()
    org = build_org(cloud)
    owner = sign_in(cloud, org.owner)
    assert owner.call("GET", "/api/family/wishlists")[0] == 402
    cloud.billing.subscribe_app(org.owner, "social")
    coach = sign_in(cloud, org.coach)
    status, made = coach.call(
        "POST", "/api/family/wishlists", {"title": "Coffee beans"}
    )
    assert status == 201, made
    status, lists = owner.call("GET", "/api/family/wishlists")
    coach_list = next(item for item in lists["lists"] if item["wishes"])
    assert coach_list["wishes"][0]["title"] == "Coffee beans"
    assert (
        owner.call("POST", f"/api/family/wishlists/{made['wish']['id']}/claim", {})[0]
        == 200
    )
    status, own = coach.call("GET", "/api/family/wishlists")
    assert (
        "claimed"
        not in next(item for item in own["lists"] if item["mine"])["wishes"][0]
    )
    status, ics = owner.call("GET", "/api/family/events.ics", raw=True)
    assert status == 200 and ics.startswith(b"BEGIN:VCALENDAR")
    other = build_org(cloud, "Other Org", slug_email="other.test")
    stranger = sign_in(cloud, other.owner)
    cloud.billing.subscribe_app(other.owner, "social")
    status, theirs = stranger.call("GET", "/api/family/wishlists")
    assert status == 200 and all(not item["wishes"] for item in theirs["lists"])


def test_today_shows_gift_ideas_next_to_a_birthday(db, home):
    pat, ana, mia = home
    soon = (kit.utcnow() + timedelta(days=2)).date()
    born = soon.replace(year=2015) if not (soon.month == 2 and soon.day == 29) else soon
    life.update(db, pat, mia["id"], {"birthday": born.isoformat()})
    service.add_wish(db, pat, {"title": "A kite", "owner_id": mia["id"]})
    service.add_wish(db, pat, {"title": "Paints", "owner_id": mia["id"]})
    cards = {card["title"]: card for card in service.today_cards(db, ana)}
    line = next(
        line
        for line in cards["Birthdays and dates this week"]["lines"]
        if line["text"].startswith("Mia's birthday")
    )
    assert line["sub"] == "2 gift ideas on the wish list"
