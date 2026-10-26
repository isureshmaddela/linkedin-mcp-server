"""Family (aira/suite/family): feed, chat, calendar, dates and the public board, in both builds."""

from __future__ import annotations

import sqlite3
from datetime import date, timedelta

import pytest
from cloud_support import Browser, build_org, sign_in
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
    connection = sqlite3.connect(tmp_path / "family.sqlite3")
    connection.row_factory = sqlite3.Row
    store.ensure(connection)
    yield connection
    connection.close()


class Req:
    def __init__(self, header=""):
        self._header = header
        self.user = {"email": "pat@home.example"}

    def header(self, name, default=""):
        return self._header if name == "X-Aira-Profile" else default


@pytest.fixture
def home(db):
    """Pat (owner), Ana (adult), Mia (child)."""
    pat = life.current(Req(), db)
    ana = life.add(db, pat, {"name": "Ana", "age_group": "adult"})
    mia = life.add(db, pat, {"name": "Mia", "age_group": "child"})
    return pat, ana, mia


# ---------------------------------------------------------------------------
# unit
# ---------------------------------------------------------------------------


def test_posts_reactions_comments_and_moderation(db, home):
    pat, ana, mia = home
    post = service.create_post(
        db,
        mia,
        {
            "text": "I lost a tooth!",
            "milestone": "achievement",
            "photos": ["https://img.example/t.jpg"],
        },
    )
    assert post["author"]["name"] == "Mia" and post["photos"] == [
        "https://img.example/t.jpg"
    ]
    with pytest.raises(ValueError):
        service.create_post(db, mia, {"text": ""})
    with pytest.raises(ValueError):
        service.create_post(
            db, mia, {"text": "x", "photos": ["http://insecure.example/a.jpg"]}
        )
    with pytest.raises(ValueError):
        service.create_post(
            db,
            mia,
            {"text": "x", "photos": [f"https://img.example/{i}.jpg" for i in range(7)]},
        )
    # one reaction per person per emoji: a second tap takes it back
    assert service.react(db, post["id"], ana["id"], "❤️")["reactions"] == {"❤️": 1}
    assert service.react(db, post["id"], pat["id"], "❤️")["reactions"] == {"❤️": 2}
    assert service.react(db, post["id"], ana["id"], "❤️")["reactions"] == {"❤️": 1}
    with pytest.raises(ValueError):
        service.react(db, post["id"], ana["id"], "💩")
    comment = service.add_comment(db, ana, post["id"], {"text": "Well done!"})
    # a child cannot remove someone else's comment or pin; an adult moderates
    with pytest.raises(PermissionError):
        service.remove_comment(db, mia, comment["id"])
    with pytest.raises(PermissionError):
        service.pin_post(db, mia, post["id"], True)
    assert service.pin_post(db, pat, post["id"], True)["pinned"] is True
    with pytest.raises(PermissionError):
        service.update_post(db, ana, post["id"], {"text": "changed"})
    other = service.create_post(db, ana, {"text": "Dinner at 7"})
    with pytest.raises(PermissionError):
        service.remove_post(db, mia, other["id"])
    service.remove_post(db, pat, other["id"])  # an adult moderates
    feed = service.list_posts(db, ana)
    assert [p["id"] for p in feed["posts"]] == [post["id"]] and feed["posts"][0][
        "comments"
    ][0]["text"] == "Well done!"


def test_unread_posts_and_since_updates(db, home):
    pat, ana, _ = home
    first = service.list_posts(db, pat)
    assert first["unread"] == 0
    service.create_post(db, ana, {"text": "Hello"})
    service.create_post(db, ana, {"text": "Look at this"})
    assert service.unread_posts(db, pat) == 2 and service.unread_posts(db, ana) == 0
    store.mark_seen(db, pat["id"], "feed")
    assert service.unread_posts(db, pat) == 0
    since = first["now"]
    third = service.create_post(db, pat, {"text": "Third"})
    changed = service.list_posts(db, ana, since=since)
    assert {p["id"] for p in changed["posts"]} >= {third["id"]}
    later = service.list_posts(db, ana, since=changed["now"])
    assert later["posts"] == []
    service.remove_post(db, pat, third["id"])
    removed = service.list_posts(db, ana, since=changed["now"])
    assert (
        removed["posts"][0]["status"] == "removed" and removed["posts"][0]["text"] == ""
    )
    with pytest.raises(ValueError):
        service.list_posts(db, ana, since="yesterday")


def test_chat_rooms_privacy_unread_and_receipts(db, home):
    pat, ana, mia = home
    group = service.group_room(db)
    sent = service.send_message(db, pat, group["id"], {"text": "Dinner is ready"})
    assert (
        service.unread_in(db, group["id"], ana) == 1
        and service.unread_in(db, group["id"], pat) == 0
    )
    direct = service.direct_room(db, pat, ana["id"])
    assert service.direct_room(db, ana, pat["id"])["id"] == direct["id"]
    service.send_message(db, ana, direct["id"], {"text": "Can you pick up Mia?"})
    # Mia is not in Pat and Ana's room: it does not exist for her
    with pytest.raises(LookupError):
        service.messages(db, mia, direct["id"])
    with pytest.raises(LookupError):
        service.send_message(db, mia, direct["id"], {"text": "hi"})
    assert [r["kind"] for r in service.list_rooms(db, mia)] == ["group"]
    assert service.unread_messages(db, pat) == 1
    page = service.messages(db, pat, direct["id"])
    assert (
        page["messages"][0]["text"] == "Can you pick up Mia?"
        and page["read_by"][0]["name"] == "Ana"
    )
    service.mark_read(db, pat, direct["id"])
    assert service.unread_messages(db, pat) == 0
    # since returns only what changed, including a deletion
    after = service.messages(db, ana, direct["id"], since=page["now"])
    assert after["messages"] == []
    with pytest.raises(PermissionError):
        service.delete_message(db, ana, sent["id"])
    service.delete_message(db, pat, sent["id"])
    changed = service.messages(db, ana, group["id"], since=page["now"])
    assert (
        changed["messages"][0]["deleted"] is True
        and changed["messages"][0]["text"] == ""
    )
    with pytest.raises(ValueError):
        service.direct_room(db, pat, pat["id"])
    with pytest.raises(ValueError):
        service.send_message(db, pat, group["id"], {"text": " "})


def test_calendar_invitations_and_answers(db, home):
    pat, ana, mia = home
    start = kit.iso(kit.utcnow() + timedelta(days=2))
    party = service.create_event(
        db, pat, {"title": "Grandma's party", "starts_at": start, "place": "Our house"}
    )
    assert party["everyone"] and party["my_answer"] == "yes"
    assert service.rsvp(db, mia, party["id"], "maybe")["my_answer"] == "maybe"
    private = service.create_event(
        db,
        ana,
        {"title": "Parents' dinner", "starts_at": start, "invitees": [pat["id"]]},
    )
    assert [e["title"] for e in service.list_events(db, mia)] == ["Grandma's party"]
    with pytest.raises(LookupError):
        service.rsvp(db, mia, private["id"], "yes")
    with pytest.raises(ValueError):
        service.rsvp(db, pat, private["id"], "perhaps")
    with pytest.raises(ValueError):
        service.create_event(
            db,
            pat,
            {
                "title": "Backwards",
                "starts_at": start,
                "ends_at": kit.iso(kit.utcnow()),
            },
        )
    with pytest.raises(PermissionError):
        service.cancel_event(db, mia, party["id"])
    whole_day = service.create_event(
        db, pat, {"title": "Holiday", "starts_at": "2026-12-24", "all_day": True}
    )
    assert whole_day["all_day"] and whole_day["starts_at"] == "2026-12-24T00:00:00Z"
    service.cancel_event(db, pat, party["id"])
    with pytest.raises(ValueError):
        service.rsvp(db, ana, party["id"], "yes")


def test_dates_come_from_profiles_and_the_family(db, home):
    pat, ana, mia = home
    today = date(2026, 10, 25)
    life.update(db, pat, mia["id"], {"birthday": "2018-10-27"})
    service.add_date(
        db, pat, {"name": "Uncle Raj", "date": "1960-11-30", "relation": "uncle"}
    )
    service.add_date(
        db, pat, {"name": "Rosa & Joe", "date": "1990-10-25", "kind": "anniversary"}
    )
    dates = service.list_dates(db, today=today)
    assert [d["name"] for d in dates] == ["Rosa & Joe", "Mia", "Uncle Raj"]
    assert (
        dates[0]["in_days"] == 0 and dates[0]["years"] == 36 and dates[1]["years"] == 8
    )
    with pytest.raises(PermissionError):
        service.delete_date(db, mia, dates[2]["id"])
    wished = service.wish(db, pat, {"name": "Mia"})
    assert "Happy birthday, Mia!" in wished["text"] and service.wished_recently(
        db, pat, "Mia"
    )


def test_board_moderation_and_children(db, home):
    pat, ana, mia = home
    service.create_post(db, ana, {"text": "Family news"})
    with pytest.raises(PermissionError):
        service.save_board_settings(db, mia, {"auto_publish": True})
    visitor = {"client": "203.0.113.9"}
    held = service.board_post(
        db, {"name": "Aunt May", "text": "Love you all!"}, visitor
    )
    assert held["status"] == "pending"
    page = service.board_page(db)
    assert [p["text"] for p in page["posts"]] == ["Family news"]
    assert "id" not in page["posts"][0]["author"]
    with pytest.raises(PermissionError):
        service.approve_post(db, mia, held["id"])
    service.approve_post(db, pat, held["id"])
    assert len(service.board_page(db)["posts"]) == 2
    service.save_board_settings(db, pat, {"auto_publish": True})
    assert (
        service.board_post(db, {"name": "Cousin Li", "text": "Hi!"}, visitor)["status"]
        == "live"
    )
    post_id = service.board_page(db)["posts"][0]["id"]
    assert service.board_react(db, {"post_id": post_id, "emoji": "🎉"}, visitor)[
        "reactions"
    ] == {"🎉": 1}
    assert service.board_react(
        db, {"post_id": post_id, "emoji": "🎉"}, {"client": "198.51.100.4"}
    )["reactions"] == {"🎉": 2}
    with pytest.raises(ValueError):
        service.board_post(db, {"name": "", "text": "x"}, visitor)


def test_today_and_pulse(db, home):
    pat, ana, mia = home
    service.send_message(
        db,
        ana,
        service.direct_room(db, ana, pat["id"])["id"],
        {"text": "Call me when you can"},
    )
    soon = kit.iso(kit.utcnow() + timedelta(minutes=30))
    event = service.create_event(db, ana, {"title": "Dentist", "starts_at": soon})
    service.rsvp(db, pat, event["id"], "yes")
    cards = {c["title"]: c for c in service.today_cards(db, pat)}
    assert "1 new message from Ana" in cards["Messages from family"]["lines"][0]["text"]
    assert cards["Family calendar"]["lines"][0]["done"] is True
    beat = service.pulse(db, pat, "")
    assert beat["badge"] == 1
    titles = [a["title"] for a in beat["alerts"]]
    assert "Message from Ana" in titles and "Starting soon: Dentist" in titles
    assert all(a["id"].startswith("fm:") for a in beat["alerts"])
    mia_cards = {c["title"] for c in service.today_cards(db, mia)}
    assert (
        "Messages from family" not in mia_cards
    )  # Mia has no unread direct messages and no new posts


# ---------------------------------------------------------------------------
# the single-user build
# ---------------------------------------------------------------------------


def _anonymous(env, method, path, body=None):
    import json
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


def test_a_household_chats_and_relatives_use_the_board(env, api):
    kit.forget_schema()
    public.set_default_index(test_careers.app._public_link_index)
    house = Household(env, api)
    pat = house.owner
    ana = house.add("Ana")
    mia = house.add("Mia", age_group="child", pin="2468")
    overview = pat.get("/api/family/overview")
    assert overview["is_adult"] is True and len(overview["people"]) == 3
    rooms = pat.get("/api/family/rooms")["rooms"]
    group_id = rooms[0]["id"]
    first = ana.get(f"/api/family/rooms/{group_id}/messages")
    pat.post(
        f"/api/family/rooms/{group_id}/messages", {"text": "Hello family"}, expect=201
    )
    mia.post(f"/api/family/rooms/{group_id}/messages", {"text": "Hi!!"}, expect=201)
    fresh = ana.get(f"/api/family/rooms/{group_id}/messages?since={first['now']}")
    assert [m["text"] for m in fresh["messages"]] == ["Hello family", "Hi!!"]
    assert (
        ana.get(f"/api/family/rooms/{group_id}/messages?since={fresh['now']}")[
            "messages"
        ]
        == []
    )
    direct = pat.post("/api/family/rooms/direct", {"profile_id": ana.id})["room"]
    pat.post(
        f"/api/family/rooms/{direct['id']}/messages", {"text": "Just us"}, expect=201
    )
    assert mia.call("GET", f"/api/family/rooms/{direct['id']}/messages")[0] == 404
    assert ana.get("/api/life/pulse")["apps"]["family"]["badge"] == 3
    # Mia (a child) cannot open the board settings or make its link
    assert mia.call("POST", "/api/family/board/link", {})[0] == 403
    link = pat.post("/api/family/board/link", {})["link"]
    status, page = _anonymous(env, "GET", f"/api/public/{link['token']}")
    assert status == 200 and page["page"]["title"] == "Our family"
    status, sent = _anonymous(
        env,
        "POST",
        f"/api/public/{link['token']}/post",
        {"name": "Aunt May", "text": "Miss you all"},
    )
    assert status == 201 and sent["status"] == "pending"
    pending = pat.get("/api/family/board")["pending"]
    assert pending[0]["author"]["name"] == "Aunt May"
    pat.post(f"/api/family/posts/{pending[0]['id']}/approve", {})
    status, page = _anonymous(env, "GET", f"/api/public/{link['token']}")
    assert page["page"]["posts"][0]["text"] == "Miss you all"
    status, _ = _anonymous(
        env,
        "POST",
        f"/api/public/{link['token']}/react",
        {"post_id": pending[0]["id"], "emoji": "❤️"},
    )
    assert status == 200
    # Today shows Ana the unread messages; the wishes action posts to the feed
    today = ana.get("/api/life/today")
    family = [c for c in today["cards"] if c["app"] == "family"]
    assert family and "new message" in family[0]["lines"][0]["text"]
    pat.post("/api/family/wishes", {"name": "Grandma"}, expect=201)
    assert (
        "Happy birthday, Grandma!" in ana.get("/api/family/posts")["posts"][0]["text"]
    )
    pat.ok("DELETE", "/api/family/board/link")
    assert _anonymous(env, "GET", f"/api/public/{link['token']}")[0] == 404


# ---------------------------------------------------------------------------
# the hosted build
# ---------------------------------------------------------------------------


def test_hosted_family_is_for_every_member_and_keeps_rooms_private(cloud):
    kit.forget_schema()
    org = build_org(cloud)
    owner = sign_in(cloud, org.owner)
    status, data = owner.call("GET", "/api/family/overview")
    assert status == 402 and data["code"] == "app_required"
    cloud.billing.subscribe_app(org.owner, "family")
    browsers = {
        who: sign_in(cloud, getattr(org, who))
        for who in ("owner", "candidate", "auditor", "billing", "coach")
    }
    for who, browser in browsers.items():
        assert browser.call("GET", "/api/family/overview")[0] == 200, who
    candidate_id = browsers["candidate"].call("GET", "/api/life/me")[1]["profile"]["id"]
    status, room = browsers["owner"].call(
        "POST", "/api/family/rooms/direct", {"profile_id": candidate_id}
    )
    assert status == 200
    room_id = room["room"]["id"]
    assert (
        browsers["owner"].call(
            "POST", f"/api/family/rooms/{room_id}/messages", {"text": "Welcome!"}
        )[0]
        == 201
    )
    status, page = browsers["candidate"].call(
        "GET", f"/api/family/rooms/{room_id}/messages"
    )
    assert status == 200 and page["messages"][0]["text"] == "Welcome!"
    assert (
        browsers["coach"].call("GET", f"/api/family/rooms/{room_id}/messages")[0] == 404
    )
    status, link = browsers["owner"].call("POST", "/api/family/board/link", {})
    token = link["link"]["token"]
    visitor = Browser(cloud)
    status, sent = visitor.call(
        "POST", f"/api/public/{token}/post", {"name": "Grandpa", "text": "Proud of you"}
    )
    assert status == 201 and sent["status"] == "pending"
    other = build_org(cloud, "Other Org", slug_email="other.test")
    cloud.billing.subscribe_app(other.owner, "family")
    status, rooms = sign_in(cloud, other.owner).call("GET", "/api/family/rooms")
    assert status == 200 and all(r["kind"] == "group" for r in rooms["rooms"])
    status, feed = sign_in(cloud, other.owner).call("GET", "/api/family/posts")
    assert feed["posts"] == []
