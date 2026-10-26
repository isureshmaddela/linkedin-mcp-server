"""Social (aira/suite/social): profiles, friends, feed, stories, chats, groups, pages and safety, in both builds."""

from __future__ import annotations

from datetime import timedelta

import pytest
from cloud_support import build_org, sign_in
from life_support import Household

import test_careers
from aira import suite
from aira.suite import kit, public
from aira.suite.social import db, service, store

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
    """Every test gets its own social database (the hosted build points it at its data folder)."""
    db.set_path(lambda: tmp_path / "social-test.sqlite3")
    db.forget()
    yield
    db.reset_path()
    db.forget()


@pytest.fixture
def sc():
    connection = db.connect()
    yield connection
    connection.close()


def _join(sc, key, name, *, age="adult", home="home-1", handle="", **raw):
    ident = {
        "key": key,
        "profile": {"id": key, "name": name, "emoji": "🙂", "age_group": age},
        "home": home,
        "moderator": False,
        "hosted": False,
    }
    service.create_account(sc, ident, {"handle": handle or name.lower(), **raw})
    return service.me(sc, ident)


@pytest.fixture
def people(sc):
    """Pat and Ana (one household), Sam (another household), Mia (Pat's child)."""
    pat = _join(sc, "p:pat", "Pat")
    ana = _join(sc, "p:ana", "Ana")
    sam = _join(sc, "p:sam", "Sam", home="home-2")
    mia = _join(sc, "p:mia", "Mia", age="child")
    return pat, ana, sam, mia


def _friends(sc, a, b):
    service.request_friend(sc, a, b["id"])
    service.request_friend(sc, b, a["id"])
    assert service.are_friends(sc, a["id"], b["id"])


def _fresh(sc, account):
    return service.account(sc, account["id"])


# ---------------------------------------------------------------------------
# unit
# ---------------------------------------------------------------------------


def test_handles_profiles_and_young_people_stay_private(sc, people):
    pat, ana, sam, mia = people
    assert service.suggest_handle(sc, "Pat") == "pat2"
    assert service.suggest_handle(sc, "1 ☺") == "friend1"
    with pytest.raises(ValueError, match="taken"):
        _join(sc, "p:other", "Pat")
    with pytest.raises(ValueError, match="Social name"):
        _join(sc, "p:bad", "X", handle="no spaces")
    assert mia["visibility"] == "private" and mia["open_messages"] is False
    with pytest.raises(PermissionError):
        service.update_account(sc, mia, {"visibility": "public"})
    teen = _join(sc, "p:teo", "Teo", age="teen", visibility="public")
    assert teen["visibility"] == "private"
    with pytest.raises(PermissionError):
        service.update_account(sc, teen, {"open_messages": True})
    updated = service.update_account(
        sc,
        pat,
        {
            "bio": "Gardener",
            "headline": "Nurse",
            "skills": "care, care, Spanish",
            "experience": [{"title": "Nurse", "company": "City", "start": "2019"}],
            "open_to_work": True,
        },
    )
    assert updated["skills"] == ["care", "Spanish"] and updated["open_to_work"] is True
    with pytest.raises(ValueError):
        service.update_account(sc, pat, {"avatar": "http://insecure.example/me.png"})
    with pytest.raises(ValueError):
        service.update_account(
            sc, pat, {"experience": [{"title": "X", "start": "May"}]}
        )


def test_friend_requests_follows_and_who_sees_which_posts(sc, people):
    pat, ana, sam, mia = people
    assert service.request_friend(sc, pat, sam["id"]) == {"relation": "request_sent"}
    assert service.relation(sc, sam, pat) == "request_received"
    assert [n["kind"] for n in service.notifications(sc, sam)["notifications"]] == [
        "friend_request"
    ]
    assert service.request_friend(sc, sam, pat["id"]) == {"relation": "friends"}
    public_post = service.create_post(
        sc, pat, {"text": "Hello world", "audience": "public"}
    )
    friends_post = service.create_post(
        sc, pat, {"text": "Friends only", "audience": "friends"}
    )
    mine = service.create_post(sc, pat, {"text": "Diary", "audience": "only_me"})
    seen_by_sam = {p["id"] for p in service.feed(sc, sam)["posts"]}
    assert {public_post["id"], friends_post["id"]} <= seen_by_sam and mine[
        "id"
    ] not in seen_by_sam
    # Ana is not Pat's friend: she sees only the public post, and only on Pat's profile (she does not follow Pat)
    assert service.feed(sc, ana)["posts"] == []
    assert [p["id"] for p in service.person_posts(sc, ana, pat)] == [public_post["id"]]
    service.follow(sc, ana, "account", pat["id"], True)
    assert [p["id"] for p in service.feed(sc, ana)["posts"]] == [public_post["id"]]
    with pytest.raises(LookupError):
        service.visible_post(sc, ana, friends_post["id"])
    # a private profile shows strangers only the name; following it is refused
    service.update_account(sc, sam, {"visibility": "private", "bio": "Secret"})
    view = service.profile_view(sc, ana, _fresh(sc, sam))
    assert "bio" not in view and view["relation"] == "none"
    with pytest.raises(PermissionError):
        service.follow(sc, ana, "account", sam["id"], True)
    assert service.profile_view(sc, pat, _fresh(sc, sam))["bio"] == "Secret"
    # unfriending ends it; declining a request removes it
    service.remove_friend(sc, sam, pat["id"])
    assert friends_post["id"] not in {p["id"] for p in service.feed(sc, sam)["posts"]}
    # suggestions: friends of friends and people from the same household
    _friends(sc, pat, sam)
    _friends(sc, pat, ana)
    assert {sam["id"], mia["id"]} <= {s["id"] for s in service.suggestions(sc, ana)}
    assert mia["id"] not in {s["id"] for s in service.suggestions(sc, sam)}


def test_blocking_hides_everything_both_ways(sc, people):
    pat, ana, sam, mia = people
    _friends(sc, pat, sam)
    chat = service.open_direct(sc, pat, sam["id"])
    service.send(sc, sam, chat["id"], {"text": "hi"})
    post = service.create_post(sc, sam, {"text": "Public news", "audience": "public"})
    service.block(sc, pat, sam["id"])
    assert not service.are_friends(sc, pat["id"], sam["id"])
    for viewer, other in ((pat, sam), (sam, pat)):
        with pytest.raises(LookupError):
            service.visible_account(sc, viewer, other["id"])
        assert other["id"] not in {
            p["id"] for p in service.search(sc, viewer, other["display_name"])["people"]
        }
        with pytest.raises(PermissionError):
            service.send(sc, viewer, chat["id"], {"text": "still there?"})
        with pytest.raises(LookupError):
            service.request_friend(sc, viewer, other["id"])
    with pytest.raises(LookupError):
        service.visible_post(sc, pat, post["id"])
    assert service.messages(sc, pat, chat["id"])["messages"] == []
    assert service.chats(sc, pat)["chats"][0]["blocked"] is True
    assert [b["id"] for b in service.blocks(sc, pat)] == [sam["id"]]
    service.unblock(sc, pat, sam["id"])
    assert service.visible_post(sc, pat, post["id"])["id"] == post["id"]


def test_children_are_protected(sc, people):
    pat, ana, sam, mia = people
    # a stranger cannot find a child, ask to be friends or message them
    assert mia["id"] not in {p["id"] for p in service.search(sc, sam, "Mia")["people"]}
    with pytest.raises(LookupError):
        service.request_friend(sc, sam, mia["id"])
    with pytest.raises(LookupError):
        service.open_direct(sc, sam, mia["id"])
    # her own household finds her; she accepts her mother's request; then they chat
    assert mia["id"] in {p["id"] for p in service.search(sc, pat, "Mia")["people"]}
    service.request_friend(sc, pat, mia["id"])
    service.request_friend(sc, mia, pat["id"])
    chat = service.open_direct(sc, mia, pat["id"])
    service.send(sc, mia, chat["id"], {"text": "Hi mum"})
    # her posts never go public, and she cannot make groups or pages or join public groups
    post = service.create_post(sc, mia, {"text": "My drawing", "audience": "public"})
    assert post["audience"] == "friends"
    with pytest.raises(PermissionError):
        service.create_group(sc, mia, {"name": "Kids"})
    with pytest.raises(PermissionError):
        service.create_page(sc, mia, {"name": "Mia's"})
    group = service.create_group(sc, sam, {"name": "Walkers", "visibility": "public"})
    with pytest.raises(PermissionError):
        service.join_group(sc, mia, group["id"])
    # an adult with open messages still cannot be messaged by a child who is not their friend
    service.update_account(sc, ana, {"open_messages": True})
    with pytest.raises(PermissionError):
        service.open_direct(sc, mia, ana["id"])


def test_messaging_rules_receipts_unread_and_since(sc, people):
    pat, ana, sam, mia = people
    with pytest.raises(PermissionError):
        service.open_direct(sc, pat, sam["id"])
    service.update_account(sc, sam, {"open_messages": True})
    chat = service.open_direct(sc, pat, _fresh(sc, sam)["id"])
    assert service.open_direct(sc, pat, sam["id"])["id"] == chat["id"]
    first = service.messages(sc, sam, chat["id"])
    sent = service.send(sc, pat, chat["id"], {"text": "Hello Sam"})["message"]
    service.send(sc, pat, chat["id"], {"photo": "https://img.example/a.jpg"})
    with pytest.raises(ValueError):
        service.send(sc, pat, chat["id"], {"photo": "http://img.example/a.jpg"})
    assert service.unread_messages(sc, sam) == 2
    fresh = service.messages(sc, sam, chat["id"], since=first["now"])
    assert [m["text"] for m in fresh["messages"]] == ["Hello Sam", ""]
    assert service.messages(sc, sam, chat["id"], since=fresh["now"])["messages"] == []
    assert service.messages(sc, pat, chat["id"])["messages"][0]["seen"] is False
    service.mark_read(sc, sam, chat["id"])
    assert service.unread_messages(sc, sam) == 0
    assert service.messages(sc, pat, chat["id"])["messages"][0]["seen"] is True
    # Ana is not in the chat
    with pytest.raises(LookupError):
        service.messages(sc, ana, chat["id"])
    # delete for everyone within an hour; older messages stay
    before = service.messages(sc, sam, chat["id"])["now"]
    service.delete_message(sc, pat, sent["id"])
    changed = service.messages(sc, sam, chat["id"], since=before)["messages"]
    assert changed[0]["deleted"] is True and changed[0]["text"] == ""
    old = service.send(sc, pat, chat["id"], {"text": "old"})["message"]
    sc.execute(
        "UPDATE so_messages SET created_at = ? WHERE id = ?",
        (store.stamp(store.utcnow() - timedelta(hours=2)), old["id"]),
    )
    sc.commit()
    with pytest.raises(PermissionError):
        service.delete_message(sc, pat, old["id"])
    with pytest.raises(LookupError):
        service.delete_message(sc, sam, old["id"])
    # muting hides the chat from the unread count
    service.send(sc, pat, chat["id"], {"text": "ping"})
    service.mute(sc, sam, chat["id"], True)
    assert service.unread_messages(sc, sam) == 0
    # group chats are for friends; admins add and remove; the last admin leaving hands over
    with pytest.raises(PermissionError):
        service.create_group_chat(sc, pat, {"title": "Trip", "member_ids": [ana["id"]]})
    _friends(sc, pat, ana)
    _friends(sc, pat, sam)
    group = service.create_group_chat(
        sc, pat, {"title": "Trip", "member_ids": [ana["id"]]}
    )
    service.add_member(sc, pat, group["id"], sam["id"])
    with pytest.raises(PermissionError):
        service.add_member(sc, ana, group["id"], sam["id"])
    service.remove_member(sc, pat, group["id"], sam["id"])
    with pytest.raises(LookupError):
        service.messages(sc, sam, group["id"])
    service.leave(sc, pat, group["id"])
    assert service.chats(sc, ana)["chats"][0]["admin"] is True


def test_groups_and_pages(sc, people):
    pat, ana, sam, mia = people
    private = service.create_group(
        sc, pat, {"name": "Book club", "visibility": "private"}
    )
    service.create_post(
        sc, pat, {"text": "Chapter 3 tonight", "group_id": private["id"]}
    )
    hidden = service.group_view(
        sc, sam, service.group_row(sc, private["id"]), with_posts=True
    )
    assert "posts" not in hidden
    with pytest.raises(PermissionError):
        service.create_post(sc, sam, {"text": "Let me in", "group_id": private["id"]})
    assert service.join_group(sc, sam, private["id"])["status"] == "pending"
    assert [
        r["id"]
        for r in service.group_view(sc, pat, service.group_row(sc, private["id"]))[
            "requests"
        ]
    ] == [sam["id"]]
    with pytest.raises(PermissionError):
        service.approve_member(sc, sam, private["id"], sam["id"], True)
    service.approve_member(sc, pat, private["id"], sam["id"], True)
    view = service.group_view(
        sc, sam, service.group_row(sc, private["id"]), with_posts=True
    )
    assert (
        view["status"] == "member" and view["posts"][0]["text"] == "Chapter 3 tonight"
    )
    assert any(
        p["group"]["id"] == private["id"] for p in service.feed(sc, sam)["posts"]
    )
    public_group = service.create_group(sc, ana, {"name": "Runners"})
    assert service.join_group(sc, sam, public_group["id"])["status"] == "member"
    assert public_group["id"] in {g["id"] for g in service.groups(sc, pat)["discover"]}
    assert private["id"] not in {g["id"] for g in service.groups(sc, ana)["discover"]}
    # pages: admins post as the page; followers see it in their feed
    with pytest.raises(ValueError):
        service.create_page(
            sc, pat, {"name": "Bakery", "link": "http://bakery.example"}
        )
    page = service.create_page(
        sc, pat, {"name": "Bakery", "link": "https://bakery.example/p/x"}
    )
    with pytest.raises(PermissionError):
        service.create_post(sc, sam, {"text": "Fake news", "page_id": page["id"]})
    service.create_post(sc, pat, {"text": "Fresh bread", "page_id": page["id"]})
    service.follow(sc, sam, "page", page["id"], True)
    assert service.feed(sc, sam)["posts"][0]["page"]["name"] == "Bakery"
    assert service.page_view(sc, sam, service.page(sc, page["id"]))["followers"] == 1


def test_reactions_comments_shares_tags_and_mentions(sc, people):
    pat, ana, sam, mia = people
    _friends(sc, pat, ana)
    post = service.create_post(
        sc, pat, {"text": "Spring is here #Garden @ana", "audience": "public"}
    )
    assert post["tags"] == ["garden"]
    kinds = [n["kind"] for n in service.notifications(sc, ana)["notifications"]]
    assert "mention" in kinds
    assert service.react(sc, ana, post["id"], "❤️")["reactions"] == {"❤️": 1}
    assert service.react(sc, ana, post["id"], "❤️")["reactions"] == {}
    with pytest.raises(ValueError):
        service.react(sc, ana, post["id"], "💩")
    service.comment(sc, ana, post["id"], {"text": "Lovely!"})
    assert [c["text"] for c in service.comments(sc, pat, post["id"])] == ["Lovely!"]
    assert service.unread_notifications(sc, pat) >= 1
    service.read_notifications(sc, pat)
    assert service.unread_notifications(sc, pat) == 0
    shared = service.create_post(
        sc, sam, {"shared_post_id": post["id"], "text": "Look", "audience": "public"}
    )
    assert shared["shared"]["id"] == post["id"]
    friends_only = service.create_post(
        sc, pat, {"text": "Private", "audience": "friends"}
    )
    with pytest.raises(PermissionError):
        service.create_post(sc, ana, {"shared_post_id": friends_only["id"]})
    assert [p["id"] for p in service.tag_posts(sc, sam, "garden")] == [post["id"]]
    assert [p["id"] for p in service.search(sc, sam, "#garden")["posts"]] == [
        post["id"]
    ]
    edited = service.edit_post(sc, pat, post["id"], {"text": "Spring #flowers"})
    assert edited["edited"] is True and service.tag_posts(sc, sam, "garden") == []
    with pytest.raises(LookupError):
        service.edit_post(sc, sam, post["id"], {"text": "mine now"})
    service.delete_post(sc, pat, post["id"])
    assert service.create_post(sc, ana, {"text": "x"})  # still fine
    with pytest.raises(LookupError):
        service.visible_post(sc, sam, post["id"])
    assert service.post_view(
        sc,
        sam,
        store.row(sc, "SELECT * FROM so_posts WHERE id = ?", (shared["id"],)) or {},
    )["shared"] == {"gone": True}


def test_feed_since_reports_changes_and_removals(sc, people):
    pat, ana, sam, mia = people
    _friends(sc, pat, ana)
    first = service.feed(sc, ana)
    post = service.create_post(sc, pat, {"text": "One"})
    changed = service.feed(sc, ana, since=first["now"])
    assert [p["id"] for p in changed["posts"]] == [post["id"]]
    service.comment(sc, ana, post["id"], {"text": "Nice"})
    again = service.feed(sc, ana, since=changed["now"])
    assert again["posts"][0]["comments"] == 1
    service.delete_post(sc, pat, post["id"])
    gone = service.feed(sc, ana, since=again["now"])
    assert gone["posts"] == [] and gone["removed"] == [post["id"]]
    with pytest.raises(ValueError):
        store.since_value("yesterday")


def test_reports_moderation_and_suspension(sc, people):
    pat, ana, sam, mia = people
    post = service.create_post(sc, sam, {"text": "Rude words", "audience": "public"})
    service.report(
        sc, pat, {"target_type": "post", "target_id": post["id"], "reason": "Unkind"}
    )
    assert service.report(
        sc, pat, {"target_type": "post", "target_id": post["id"]}
    ) == {"reported": True}
    queue = service.reports(sc, home="home-1")
    assert (
        len(queue) == 1
        and queue[0]["preview"] == "Rude words"
        and queue[0]["can_suspend"] is False
    )
    assert service.reports(sc, home="home-3") == []
    with pytest.raises(LookupError):
        service.moderate(sc, "@x", queue[0]["id"], "hide", home="home-3")
    # a household owner hides it for everyone, but only platform moderators suspend an outsider
    with pytest.raises(PermissionError):
        service.moderate(sc, "@pat", queue[0]["id"], "suspend", home="home-1")
    service.moderate(sc, "@pat", queue[0]["id"], "hide", home="home-1")
    with pytest.raises(LookupError):
        service.visible_post(sc, ana, post["id"])
    assert service.reports(sc, home="home-1") == []
    # the platform moderator (no home) suspends; the person's content disappears and they cannot post
    comment = service.comment(
        sc,
        sam,
        service.create_post(sc, pat, {"text": "Hi", "audience": "public"})["id"],
        {"text": "spam"},
    )
    service.report(
        sc,
        ana,
        {
            "target_type": "comment",
            "target_id": comment["comment"]["id"],
            "reason": "Spam",
        },
    )
    report_id = service.reports(sc)[0]["id"]
    service.moderate(sc, "operator", report_id, "suspend")
    suspended = _fresh(sc, sam)
    assert suspended["suspended_at"]
    with pytest.raises(PermissionError):
        service.create_post(sc, suspended, {"text": "back"})
    with pytest.raises(LookupError):
        service.visible_account(sc, pat, sam["id"])
    with pytest.raises(LookupError):
        service.report(sc, pat, {"target_type": "message", "target_id": "0" * 32})


def test_rate_limits_and_the_word_filter(sc, people, monkeypatch):
    pat, ana, sam, mia = people
    monkeypatch.setitem(service.LIMITS, "post", 2)
    service.create_post(sc, pat, {"text": "1"})
    service.create_post(sc, pat, {"text": "2"})
    with pytest.raises(PermissionError, match="too often"):
        service.create_post(sc, pat, {"text": "3"})
    assert (
        service.create_post(sc, ana, {"text": "shit happens"})["text"] == "**** happens"
    )
    service.save_blocklist(sc, ["Broccoli", "broccoli", ""])
    assert service.blocklist(sc) == ["broccoli"]
    assert (
        service.create_post(sc, ana, {"text": "No BROCCOLI please"})["text"]
        == "No ******** please"
    )
    with pytest.raises(ValueError):
        service.create_post(sc, ana, {"text": "x" * 2001})
    with pytest.raises(ValueError):
        service.create_post(
            sc,
            ana,
            {"text": "x", "photos": [f"https://i.example/{n}.jpg" for n in range(7)]},
        )


def test_stories_expire_and_track_who_saw_them(sc, people):
    pat, ana, sam, mia = people
    _friends(sc, pat, ana)
    story = service.create_story(sc, pat, {"text": "Sunny day", "color": "#ff8800"})
    strip = service.stories(sc, ana)
    assert strip[0]["author"]["id"] == pat["id"] and strip[0]["unseen"] == 1
    assert service.stories(sc, sam) == []
    with pytest.raises(LookupError):
        service.see_story(sc, sam, story["id"])
    service.see_story(sc, ana, story["id"])
    assert service.stories(sc, ana)[0]["unseen"] == 0
    assert service.stories(sc, pat)[0]["stories"][0]["views"] == 1
    sc.execute(
        "UPDATE so_stories SET expires_at = ? WHERE id = ?",
        (store.later(-1), story["id"]),
    )
    sc.commit()
    assert service.stories(sc, ana) == []
    with pytest.raises(LookupError):
        service.see_story(sc, ana, story["id"])
    with pytest.raises(ValueError):
        service.create_story(sc, pat, {})


def test_today_card_and_pulse(sc, people):
    pat, ana, sam, mia = people
    pat_profile, ana_profile = {"id": "pat"}, {"id": "ana"}
    _friends(sc, pat, ana)
    service.request_friend(sc, sam, pat["id"])
    chat = service.open_direct(sc, ana, pat["id"])
    service.send(sc, ana, chat["id"], {"text": "Dinner at 7?"})
    cards = service.today_cards(sc, pat_profile)
    texts = [line["text"] for line in cards[0]["lines"]]
    assert (
        cards[0]["title"] == "From your friends"
        and "Ana: 1 new" in texts
        and "Sam wants to be friends" in texts
    )
    pulse = service.pulse(sc, pat_profile, "")
    assert pulse["badge"] == 1 + service.unread_notifications(sc, pat)
    assert (
        pulse["alerts"][0]["id"].startswith("social:msg:")
        and pulse["alerts"][0]["body"] == "Dinner at 7?"
    )
    assert service.pulse(sc, pat_profile, store.stamp())["alerts"] == []
    service.mark_read(sc, pat, chat["id"])
    assert service.pulse(sc, pat_profile, "")["alerts"] == []
    assert service.today_cards(sc, {"id": "nobody"}) == []
    assert service.pulse(sc, ana_profile, "")["badge"] >= 0
    assert service.public_profile(sc, mia["id"]) == {
        "available": False,
        "title": "This profile is private.",
    }
    service.create_post(sc, pat, {"text": "Hello", "audience": "public"})
    page = service.public_profile(sc, pat["id"])
    assert page["available"] and page["posts"][0]["text"] == "Hello"


# ---------------------------------------------------------------------------
# the single-user build
# ---------------------------------------------------------------------------


def _join_http(person, handle):
    info = person.get("/api/social/me")
    assert info["account"] is None and info["suggested_handle"]
    return person.post("/api/social/me", {"handle": handle}, expect=201)["account"]


def test_a_household_and_a_second_account_on_this_computer(env, api):
    kit.forget_schema()
    public.set_default_index(test_careers.app._public_link_index)
    db.reset_path()  # the single-user build's own file, next to its accounts database
    db.forget()
    assert db.path().parent == test_careers.app.DB_PATH.parent
    house = Household(env, api)
    pat = house.owner
    ana = house.add("Ana")
    mia = house.add("Mia", age_group="child", pin="2468")
    status, body = pat.call("GET", "/api/social/feed")
    assert status == 404 and body["code"] == "social_setup"
    pat_account = _join_http(pat, "pat")
    ana_account = _join_http(ana, "ana")
    mia_account = _join_http(mia, "mia")
    assert mia_account["visibility"] == "private"
    # Pat and Ana befriend, post, comment and chat live
    pat.post(f"/api/social/friends/{ana_account['id']}", {})
    assert ana.get("/api/social/friends")["incoming"][0]["id"] == pat_account["id"]
    ana.post(f"/api/social/friends/{pat_account['id']}", {})
    post = pat.post(
        "/api/social/posts",
        {"text": "Garden day #garden", "photos": ["https://img.example/g.jpg"]},
        expect=201,
    )["post"]
    feed = ana.get("/api/social/feed")
    assert feed["posts"][0]["id"] == post["id"]
    ana.post(
        f"/api/social/posts/{post['id']}/comments", {"text": "Beautiful"}, expect=201
    )
    assert ana.get(f"/api/social/feed?since={feed['now']}")["posts"][0]["comments"] == 1
    chat = pat.post("/api/social/chats", {"person_id": ana_account["id"]}, expect=201)[
        "chat"
    ]
    first = ana.get(f"/api/social/chats/{chat['id']}/messages")
    pat.post(f"/api/social/chats/{chat['id']}/messages", {"text": "Lunch?"}, expect=201)
    fresh = ana.get(f"/api/social/chats/{chat['id']}/messages?since={first['now']}")
    assert [m["text"] for m in fresh["messages"]] == ["Lunch?"]
    assert ana.get("/api/life/pulse")["apps"]["social"]["badge"] >= 1
    today = ana.get("/api/life/today")
    social = [c for c in today["cards"] if c["app"] == "social"]
    assert (
        social
        and social[0]["lines"][0]["text"] == f"{pat_account['display_name']}: 1 new"
    )
    ana.post(f"/api/social/chats/{chat['id']}/read", {})
    # Mia is protected: her mother may befriend her; Pat is the household's moderator, Ana is not
    pat.post(f"/api/social/friends/{mia_account['id']}", {})
    assert pat.get("/api/social/me")["moderator"] is True
    assert ana.call("GET", "/api/social/moderation")[0] == 403
    # Pat shares a public profile page
    link = pat.post("/api/social/me/share", {}, expect=201)["link"]
    status, page = _anonymous(env, "GET", f"/api/public/{link['token']}")
    assert status == 200 and page["page"]["person"]["handle"] == "pat"
    assert mia.call("POST", "/api/social/me/share", {})[0] == 403

    # a second account on this computer reaches the first through the shared Social database
    second = env.api()
    status, _ = second.call(
        "POST",
        "/api/auth/register",
        {"email": "sam@example.com", "password": test_careers.PASSWORD},
    )
    assert status == 201
    sam = Household(env, second).owner
    sam_account = _join_http(sam, "sam")
    people = sam.get("/api/social/search?q=pat")["people"]
    assert [p["id"] for p in people] == [pat_account["id"]]
    assert sam.get("/api/social/search?q=mia")["people"] == []
    assert sam.call("POST", f"/api/social/friends/{mia_account['id']}", {})[0] == 404
    sam.post(f"/api/social/friends/{pat_account['id']}", {})
    pat.post(f"/api/social/friends/{sam_account['id']}", {})
    talk = sam.post("/api/social/chats", {"person_id": pat_account["id"]}, expect=201)[
        "chat"
    ]
    sam.post(
        f"/api/social/chats/{talk['id']}/messages", {"text": "Hi neighbour"}, expect=201
    )
    assert (
        pat.get(f"/api/social/chats/{talk['id']}/messages")["messages"][-1]["text"]
        == "Hi neighbour"
    )
    assert ana.call("GET", f"/api/social/chats/{talk['id']}/messages")[0] == 404
    # Sam reports Pat's post; it reaches Pat's household queue (the post is about his household)
    sam.post(
        "/api/social/reports",
        {"target_type": "post", "target_id": post["id"], "reason": "test"},
        expect=201,
    )
    assert pat.get("/api/social/moderation")["reports"][0]["target_id"] == post["id"]


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


# ---------------------------------------------------------------------------
# the hosted build
# ---------------------------------------------------------------------------


def test_hosted_members_of_two_organizations_befriend_and_chat(cloud):
    kit.forget_schema()
    assert db.path() == cloud.config.data_root / "social.sqlite3"
    db.forget()
    acme = build_org(cloud)
    owner = sign_in(cloud, acme.owner)
    status, data = owner.call("GET", "/api/social/me")
    assert status == 402 and data["code"] == "app_required"
    cloud.billing.subscribe_app(acme.owner, "social")
    coach = sign_in(cloud, acme.coach)
    for who, browser in (("owner", owner), ("coach", coach)):
        status, data = browser.call("POST", "/api/social/me", {"handle": f"acme{who}"})
        assert status == 201, (who, data)
    other = build_org(cloud, "Other Org", slug_email="other.test")
    stranger = sign_in(cloud, other.owner)
    assert stranger.call("GET", "/api/social/me")[0] == 402
    cloud.billing.subscribe_app(other.owner, "social")
    status, joined = stranger.call("POST", "/api/social/me", {"handle": "otherowner"})
    assert status == 201
    stranger_id = joined["account"]["id"]
    status, found = stranger.call("GET", "/api/social/search?q=acmeowner")
    assert status == 200 and len(found["people"]) == 1
    owner_id = found["people"][0]["id"]
    assert stranger.call("POST", f"/api/social/friends/{owner_id}", {})[0] == 200
    assert owner.call("POST", f"/api/social/friends/{stranger_id}", {})[1] == {
        "relation": "friends"
    }
    status, chat = owner.call("POST", "/api/social/chats", {"person_id": stranger_id})
    assert status == 201
    chat_id = chat["chat"]["id"]
    assert (
        owner.call(
            "POST", f"/api/social/chats/{chat_id}/messages", {"text": "Hello from Acme"}
        )[0]
        == 201
    )
    status, page = stranger.call("GET", f"/api/social/chats/{chat_id}/messages")
    assert status == 200 and page["messages"][0]["text"] == "Hello from Acme"
    assert coach.call("GET", f"/api/social/chats/{chat_id}/messages")[0] == 404
    # colleagues are suggested; the other organization is not, until they are friends
    status, friends = coach.call("GET", "/api/social/friends")
    assert {s["id"] for s in friends["suggestions"]} >= {owner_id}
    # an organization without Social cannot use it, even for an existing chat
    third = build_org(cloud, "Third Org", slug_email="third.test")
    assert (
        sign_in(cloud, third.owner).call(
            "GET", f"/api/social/chats/{chat_id}/messages"
        )[0]
        == 402
    )
    # blocking works across organizations
    assert stranger.call("POST", f"/api/social/people/{owner_id}/block", {})[0] == 200
    assert (
        owner.call(
            "POST", f"/api/social/chats/{chat_id}/messages", {"text": "Still there?"}
        )[0]
        == 403
    )
    assert owner.call("GET", f"/api/social/people/{stranger_id}")[0] == 404
    # hosted moderation belongs to the platform's operators, not an organization's owner
    assert owner.call("GET", "/api/social/moderation")[0] == 403
