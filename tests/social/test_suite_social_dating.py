"""Dating in Social (aira/suite/social/dating.py): adults only, opt-in, preferences, likes and matches, chats of a
match, safety (blocks, hiding from friends, reports) and the hosted build (members of different organizations meet;
an organization's admins never see a member's dating)."""

from __future__ import annotations

import io
import json
import zipfile
from datetime import date, timedelta

import pytest
from cloud_support import build_org, sign_in
from life_support import Household

import test_careers
from aira import suite
from aira.suite import kit
from aira.suite.social import dating, db, service

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
    db.set_path(lambda: tmp_path / "social-dating.sqlite3")
    db.forget()
    yield
    db.reset_path()
    db.forget()


@pytest.fixture
def sc():
    connection = db.connect()
    yield connection
    connection.close()


def _born(years: int) -> str:
    today = date.today()
    return (
        today.replace(year=today.year - years).isoformat()
        if not (today.month == 2 and today.day == 29)
        else date(today.year - years, 3, 1).isoformat()
    )


class Person:
    """A Social account and who is asking (what the routes build from the request)."""

    def __init__(self, sc, key, name, *, age="adult", home="", birthday=""):
        self.ident = {
            "key": key,
            "profile": {
                "id": key,
                "name": name,
                "emoji": "🙂",
                "age_group": age,
                "birthday": birthday,
            },
            "home": home or f"home-{key}",
            "moderator": False,
            "hosted": False,
        }
        service.create_account(
            sc, self.ident, {"handle": name.lower(), "display_name": f"{name} Example"}
        )
        self.sc = sc

    @property
    def acct(self):
        return service.me(self.sc, self.ident)

    def date(self, **raw):
        values = {
            "birthday": _born(30),
            "confirm_adult": True,
            "gender": "woman",
            "show_me": "everyone",
            "looking_for": "relationship",
            "bio": "Gardener and baker.",
            "city": "Riverside",
            "prompts": [
                {"id": "slow_sunday", "answer": "Pancakes and a long walk"},
                {"id": "small_joy", "answer": "A good cup of tea"},
                {"id": "first_date", "answer": "A picnic in the park"},
            ],
            "interests": ["hiking", "baking", "reading"],
            **raw,
        }
        if "photos" not in raw:
            import base64

            from test_suite_social_plus import png

            from aira.suite.social import photos

            upload = photos.save(self.sc, self.acct, base64.b64encode(png()).decode())
            values["photos"] = [upload["ref"]]
        return dating.save(self.sc, self.ident, self.acct, values)

    def deck(self, **kw):
        return dating.discover(self.sc, self.ident, self.acct, **kw)

    def names(self, **kw):
        return [card["first_name"] for card in self.deck(**kw)["cards"]]

    def swipe(self, other, action="like"):
        return dating.swipe(self.sc, self.ident, self.acct, other.profile["id"], action)

    @property
    def profile(self):
        return dating.profile_of(self.sc, self.acct["id"])


def _friends(sc, a, b):
    service.request_friend(sc, a.acct, b.acct["id"])
    service.request_friend(sc, b.acct, a.acct["id"])
    assert service.are_friends(sc, a.acct["id"], b.acct["id"])


def _match(sc, a, b):
    assert a.swipe(b)["match"] is None
    made = b.swipe(a)["match"]
    assert made is not None
    return made


# ---------------------------------------------------------------------------
# adults only
# ---------------------------------------------------------------------------


def test_children_and_teens_are_refused_and_never_shown(sc):
    pat = Person(sc, "p:pat", "Pat")
    pat.date(first_name="Pat")
    teo = Person(sc, "p:teo", "Teo", age="teen")
    mia = Person(sc, "p:mia", "Mia", age="child")
    for young in (teo, mia):
        with pytest.raises(PermissionError, match="18"):
            young.date()
        with pytest.raises(PermissionError, match="18"):
            dating.mine(sc, young.ident, young.acct)
        with pytest.raises(PermissionError):
            young.deck()
        assert dating.badge(sc, young.ident, young.acct) == {
            "matches": 0,
            "messages": 0,
            "likes": 0,
        }
    # an "adult" profile whose birthday says 17, typed in or kept by the household profile, is refused
    sam = Person(sc, "p:sam", "Sam")
    with pytest.raises(PermissionError, match="18"):
        sam.date(birthday=_born(17))
    kid = Person(sc, "p:kid", "Kid", birthday=_born(16))
    with pytest.raises(PermissionError, match="18"):
        kid.date(
            birthday=_born(30)
        )  # the household profile's birthday wins over what is typed
    with pytest.raises(ValueError, match="18 or over"):
        sam.date(confirm_adult=False)
    # someone made a profile as an adult, then their household profile says teen: they are never shown again
    ana = Person(sc, "p:ana", "Ana")
    ana.date(first_name="Ana")
    assert pat.names() == ["Ana"]
    ana.ident["profile"]["age_group"] = "teen"
    service.me(sc, ana.ident)  # their next visit to Social brings the age group over
    assert pat.names() == []
    with pytest.raises(PermissionError):
        ana.deck()
    # and an adult whose birthday becomes 17 on the household profile is refused too
    lee = Person(sc, "p:lee", "Lee")
    lee.date(first_name="Lee")
    lee.ident["profile"]["birthday"] = _born(17)
    with pytest.raises(PermissionError):
        dating.mine(sc, lee.ident, lee.acct)
    assert "Lee" not in pat.names()


# ---------------------------------------------------------------------------
# the profile: opt-in, pause, delete
# ---------------------------------------------------------------------------


def test_dating_is_off_until_turned_on_and_pause_and_delete_work(sc):
    pat = Person(sc, "p:pat", "Pat")
    ana = Person(sc, "p:ana", "Ana")
    assert dating.mine(sc, pat.ident, pat.acct) is None
    with pytest.raises(LookupError, match="Turn Dating on"):
        pat.deck()
    made = pat.date()
    assert (
        made["status"] == "active" and made["first_name"] == "Pat"
    )  # the first word of the Social name
    assert made["age_min"] == 22 and made["age_max"] == 38  # around their own age
    with pytest.raises(ValueError, match="three prompts"):
        pat.date(prompts=[{"id": "slow_sunday", "answer": "x"}])
    with pytest.raises(ValueError, match="different"):
        pat.date(prompts=[{"id": "slow_sunday", "answer": "x"}] * 3)
    with pytest.raises(ValueError, match="list"):
        pat.date(interests=["skydiving-off-roofs"])
    # only photos uploaded from the device: a link elsewhere could log every viewer and change after review
    for link in ("http://img.example/me.jpg", "https://img.example/me.jpg"):
        with pytest.raises(ValueError, match="Upload the photo"):
            pat.date(photos=[link])
    with pytest.raises(ValueError):
        pat.date(height_cm="tall")
    assert pat.date(height_cm="172")["height_cm"] == 172
    ana.date(first_name="Ana")
    assert ana.names() == ["Pat"]
    card = ana.deck()["cards"][0]
    for private in ("account_id", "handle", "birthday", "display_name"):
        assert private not in card
    assert card["age"] == 30 and card["shared"] == ["hiking", "baking", "reading"]

    # pause hides Pat from Discover and keeps the match and its chat
    match = _match(sc, pat, ana)
    dating.pause(sc, pat.ident, pat.acct, True)
    carl = Person(sc, "p:carl", "Carl")
    carl.date(first_name="Carl")
    assert "Pat" not in carl.names()
    assert pat.deck()["paused"] is True
    assert dating.matches(sc, ana.ident, ana.acct)["matches"][0]["id"] == match["id"]
    dating.chat_send(sc, ana.ident, ana.acct, match["id"], {"text": "Still here?"})
    dating.pause(sc, pat.ident, pat.acct, False)
    assert "Pat" in carl.names()

    # delete removes the profile, the likes and the match with its chat
    chat_id = sc.execute("SELECT chat_id FROM so_dating_matches").fetchone()[0]
    dating.delete(sc, pat.ident, pat.acct)
    assert dating.mine(sc, pat.ident, pat.acct) is None
    assert dating.matches(sc, ana.ident, ana.acct)["matches"] == []
    assert (
        sc.execute(
            "SELECT COUNT(*) FROM so_messages WHERE chat_id = ?", (chat_id,)
        ).fetchone()[0]
        == 0
    )
    assert sc.execute("SELECT COUNT(*) FROM so_dating_swipes").fetchone()[0] == 0
    assert "Pat" not in carl.names()


def test_preferences_filter_discovery_both_ways(sc):
    ana = Person(sc, "p:ana", "Ana")
    ana.date(first_name="Ana", gender="woman", show_me="men", age_min=25, age_max=35)
    ben = Person(sc, "p:ben", "Ben")
    ben.date(first_name="Ben", gender="man", show_me="women", birthday=_born(30))
    old = Person(sc, "p:old", "Otto")
    old.date(first_name="Otto", gender="man", show_me="women", birthday=_born(50))
    kai = Person(sc, "p:kai", "Kai")
    kai.date(first_name="Kai", gender="nonbinary", show_me="everyone")
    men_only = Person(sc, "p:max", "Max")
    men_only.date(first_name="Max", gender="man", show_me="men")
    assert ana.names() == [
        "Ben"
    ]  # not Otto (too old for Ana), Kai (Ana shows men) or Max (shows men)
    assert ben.names() == ["Ana"]  # not Kai: Ben shows women
    assert (
        old.names() == []
    )  # Otto is outside Ana's range, so Ana is not shown to him either
    assert kai.names() == []  # Ana, Ben and Max asked for women or men only
    assert sorted(men_only.names()) == []  # Ben and Otto show women
    # shared interests first, then the same area
    zoe = Person(sc, "p:zoe", "Zoe")
    zoe.date(first_name="Zoe", gender="woman", interests=["gaming"], city="Elsewhere")
    amy = Person(sc, "p:amy", "Amy")
    amy.date(
        first_name="Amy",
        gender="woman",
        interests=["hiking", "baking"],
        city="Riverside",
    )
    assert ben.names()[:3] == ["Ana", "Amy", "Zoe"]


def test_likes_passes_stars_have_daily_limits_and_undo(sc, monkeypatch):
    monkeypatch.setattr(dating, "LIKES_PER_DAY", 2)
    monkeypatch.setattr(dating, "STARS_PER_DAY", 1)
    me = Person(sc, "p:me", "Mel")
    me.date(first_name="Mel")
    others = []
    for n in range(5):
        person = Person(sc, f"p:o{n}", f"Other{n}")
        person.date(first_name=f"Other{n}")
        others.append(person)
    limits = me.deck()["limits"]
    assert (
        limits["likes_left"] == 2
        and limits["stars_left"] == 1
        and limits["can_undo"] is False
    )
    assert me.swipe(others[0], "star")["limits"]["stars_left"] == 0
    with pytest.raises(PermissionError, match="stars"):
        me.swipe(others[1], "star")
    assert me.swipe(others[1], "like")["limits"]["likes_left"] == 0
    with pytest.raises(PermissionError, match="likes"):
        me.swipe(others[2], "like")
    with pytest.raises(ValueError, match="already"):
        me.swipe(others[1], "pass")
    # passes are not limited; the last pass can be undone and the person comes back
    me.swipe(others[2], "pass")
    me.swipe(others[3], "pass")
    assert me.deck()["limits"]["can_undo"] is True
    back = dating.undo(sc, me.ident, me.acct)
    assert back["card"]["first_name"] == "Other3"
    assert "Other3" in me.names() and "Other2" not in me.names()
    with pytest.raises(ValueError, match="already"):
        me.swipe(others[2], "like")
    with pytest.raises(ValueError, match="action|chose"):
        me.swipe(others[4], "superlike")
    # a star is shown first, marked, to the person starred
    deck = others[0].deck()
    assert (
        deck["cards"][0]["first_name"] == "Mel"
        and deck["cards"][0]["starred_you"] is True
    )


# ---------------------------------------------------------------------------
# matches and their chats
# ---------------------------------------------------------------------------


def test_a_like_back_is_a_match_with_a_chat_and_unmatch_closes_it(sc):
    pat = Person(sc, "p:pat", "Pat")
    ana = Person(sc, "p:ana", "Ana")
    pat.date(first_name="Pat")
    ana.date(first_name="Ana")
    assert dating.likes_you(sc, ana.ident, ana.acct)["count"] == 0
    assert pat.swipe(ana)["match"] is None
    hints = dating.likes_you(sc, ana.ident, ana.acct)
    assert hints["count"] == 1 and hints["hints"][0]["initial"] == "P"
    assert "first_name" not in hints["hints"][0] and "id" not in hints["hints"][0]
    assert [c["likes_you"] for c in ana.deck(mode="likes")["cards"]] == [True]
    made = ana.swipe(pat)["match"]
    assert made and made["profile"]["first_name"] == "Pat" and made["new"] is False
    assert dating.likes_you(sc, ana.ident, ana.acct)["count"] == 0
    # Pat sees the new match; neither sees the other in Discover any more
    pat_matches = dating.matches(sc, pat.ident, pat.acct)
    assert (
        pat_matches["new"] == 1
        and pat_matches["matches"][0]["profile"]["first_name"] == "Ana"
    )
    assert pat.names() == [] and ana.names() == []
    detail = dating.match_detail(sc, pat.ident, pat.acct, made["id"])
    assert len(detail["icebreakers"]) >= 3 and any(
        "hiking" in idea for idea in detail["icebreakers"]
    )
    dating.seen(sc, pat.ident, pat.acct, made["id"])
    assert dating.matches(sc, pat.ident, pat.acct)["new"] == 0

    # they are not friends and Ana does not take messages from strangers, yet the match may chat
    assert not service.are_friends(sc, pat.acct["id"], ana.acct["id"])
    assert not service.can_message(sc, pat.acct, ana.acct)
    sent = dating.chat_send(sc, pat.ident, pat.acct, made["id"], {"text": "Hello Ana!"})
    assert sent["message"]["text"] == "Hello Ana!"
    page = dating.chat_messages(sc, ana.ident, ana.acct, made["id"])
    assert [m["text"] for m in page["messages"]] == ["Hello Ana!"] and page[
        "can_send"
    ] is True
    assert "author" not in page["messages"][0]  # nothing of the Social account
    assert dating.badge(sc, ana.ident, ana.acct)["messages"] == 1
    # the dating chat stays out of Social's chats, its badge and its reminders
    assert service.chats(sc, ana.acct)["chats"] == []
    assert service.unread_messages(sc, ana.acct) == 0
    assert service.pulse(sc, ana.ident["profile"], "")["alerts"] == []
    dating.chat_read(sc, ana.ident, ana.acct, made["id"])
    assert dating.badge(sc, ana.ident, ana.acct)["messages"] == 0
    # only the two of them
    carl = Person(sc, "p:carl", "Carl")
    carl.date(first_name="Carl")
    with pytest.raises(LookupError):
        dating.chat_messages(sc, carl.ident, carl.acct, made["id"])
    chat_id = sc.execute("SELECT chat_id FROM so_dating_matches").fetchone()[0]
    with pytest.raises(LookupError):
        service.messages(sc, carl.acct, chat_id)

    # unmatch: the chat is gone for both and they never meet again
    dating.unmatch(sc, ana.ident, ana.acct, made["id"])
    assert dating.matches(sc, pat.ident, pat.acct)["matches"] == []
    with pytest.raises(LookupError):
        dating.chat_send(sc, pat.ident, pat.acct, made["id"], {"text": "Hey?"})
    with pytest.raises(LookupError):
        service.send(sc, pat.acct, chat_id, {"text": "Hey?"})
    assert (
        sc.execute(
            "SELECT COUNT(*) FROM so_messages WHERE chat_id = ?", (chat_id,)
        ).fetchone()[0]
        == 0
    )
    assert pat.names() == ["Carl"] and "Pat" not in ana.names()


def test_blocks_and_hiding_from_friends_work_both_ways(sc):
    pat = Person(sc, "p:pat", "Pat")
    ana = Person(sc, "p:ana", "Ana")
    sam = Person(sc, "p:sam", "Sam")
    for person in (pat, ana, sam):
        person.date(first_name=person.ident["profile"]["name"])
    assert sorted(pat.names()) == ["Ana", "Sam"]
    # blocked on Social: never shown, either way
    service.block(sc, ana.acct, pat.acct["id"])
    assert pat.names() == ["Sam"] and ana.names() == ["Sam"]
    with pytest.raises(LookupError):
        dating.view(sc, pat.ident, pat.acct, ana.profile["id"])
    service.unblock(sc, ana.acct, pat.acct["id"])
    # Sam and Pat are friends; Pat hides from friends: neither sees the other
    _friends(sc, pat, sam)
    assert "Sam" in pat.names()
    pat.date(hide_friends=True)
    assert "Sam" not in pat.names() and "Pat" not in sam.names()
    pat.date(hide_friends=False)
    sam.date(hide_friends=True)
    assert "Sam" not in pat.names() and "Pat" not in sam.names()
    # a household (on this computer, always) never sees its own people
    mum = Person(sc, "p:mum", "Mum", home="home-p:pat")
    mum.date(first_name="Mum", hide_home=False)
    assert mum.profile["hide_home"] is True
    assert "Mum" not in pat.names() and "Pat" not in mum.names()
    # blocking a match ends it (and its chat)
    match = _match(sc, ana, sam)
    dating.block(sc, ana.ident, ana.acct, sam.profile["id"])
    assert dating.matches(sc, ana.ident, ana.acct)["matches"] == []
    assert service.blocked(sc, ana.acct["id"], sam.acct["id"])
    with pytest.raises(LookupError):
        dating.chat_messages(sc, sam.ident, sam.acct, match["id"])


def test_reports_reach_the_moderators_and_hide_the_profile(sc):
    pat = Person(sc, "p:pat", "Pat")
    ana = Person(sc, "p:ana", "Ana")
    lee = Person(sc, "p:lee", "Lee")
    for person in (pat, ana, lee):
        person.date(first_name=person.ident["profile"]["name"])
    assert dating.report(
        sc, pat.ident, pat.acct, ana.profile["id"], {"reason": "Fake photos"}
    ) == {
        "reported": True,
        "blocked": False,
    }
    [queued] = service.reports(sc)
    assert queued["target_type"] == "account" and queued["target_id"] == ana.acct["id"]
    assert queued["reason"] == "Dating profile: Fake photos"
    assert "Ana" not in pat.names()
    # a moderator suspends Ana: she disappears from Dating for everyone
    service.moderate(sc, "@mod", queued["id"], "suspend")
    assert "Ana" not in lee.names()
    # report and block in one go
    dating.report(
        sc, pat.ident, pat.acct, lee.profile["id"], {"reason": "Rude", "block": True}
    )
    assert (
        service.blocked(sc, pat.acct["id"], lee.acct["id"]) and "Pat" not in lee.names()
    )
    with pytest.raises(LookupError):
        dating.report(sc, pat.ident, pat.acct, "0" * 32, {"reason": "x"})


def test_nothing_of_dating_shows_on_the_social_profile_feed_or_export(sc):
    pat = Person(sc, "p:pat", "Pat")
    ana = Person(sc, "p:ana", "Ana")
    pat.date(first_name="Pat", bio="SECRET-DATING-BIO")
    ana.date(first_name="Ana")
    match = _match(sc, pat, ana)
    dating.chat_send(
        sc, pat.ident, pat.acct, match["id"], {"text": "SECRET-DATING-CHAT"}
    )
    view = service.profile_view(sc, ana.acct, pat.acct)
    exported = service.export_account(sc, pat.acct["id"])
    feed = service.feed(sc, ana.acct)
    for blob in (view, exported, feed):
        text = json.dumps(blob, default=str)
        assert "SECRET-DATING" not in text and "dating" not in text.lower()
    own = dating.export(sc, pat.ident, pat.acct)
    assert own["profile"]["bio"] == "SECRET-DATING-BIO"
    assert [m["text"] for m in own["messages_you_wrote"]] == ["SECRET-DATING-CHAT"]
    # erasing the Social account (a member removed, an organization deleted) erases Dating too
    pat_id = pat.acct["id"]
    service.erase_accounts(sc, [pat_id])
    assert dating.profile_of(sc, pat_id) is None
    assert (
        sc.execute(
            "SELECT COUNT(*) FROM so_dating_swipes WHERE actor_id = ? OR target_id = ?",
            (pat_id, pat_id),
        ).fetchone()[0]
        == 0
    )
    assert sc.execute("SELECT COUNT(*) FROM so_dating_matches").fetchone()[0] == 0
    assert dating.matches(sc, ana.ident, ana.acct)["matches"] == []


# ---------------------------------------------------------------------------
# over HTTP on this computer
# ---------------------------------------------------------------------------


def test_routes_refuse_children_and_teens_and_work_for_adults(env, api):
    kit.forget_schema()
    house = Household(env, api)
    pat = house.owner
    teo = house.add("Teo", age_group="teen", pin="1357")
    mia = house.add("Mia", age_group="child", pin="2468")
    for person, handle in ((pat, "patdating"), (teo, "teodating"), (mia, "miadating")):
        person.post("/api/social/me", {"handle": handle}, expect=201)
    for young in (teo, mia):
        for method, path, body in (
            ("GET", "/api/social/dating/me", None),
            ("PUT", "/api/social/dating/me", {"gender": "man"}),
            ("GET", "/api/social/dating/discover", None),
            ("GET", "/api/social/dating/matches", None),
            ("GET", "/api/social/dating/likes", None),
        ):
            status, data = young.call(method, path, body)
            assert status == 403, (young.name, path, status, data)
        assert young.get("/api/social/dating/badge") == {
            "matches": 0,
            "messages": 0,
            "likes": 0,
        }
    info = pat.get("/api/social/dating/me")
    assert info["profile"] is None and len(info["options"]["prompts"]) >= 10
    status, saved = pat.call(
        "PUT",
        "/api/social/dating/me",
        {
            "birthday": _born(41),
            "confirm_adult": True,
            "gender": "man",
            "show_me": "women",
            "prompts": [
                {"id": k, "answer": "Yes"}
                for k in ("slow_sunday", "small_joy", "comfort")
            ],
            "interests": ["music"],
        },
    )
    assert status == 200 and saved["profile"]["age"] == 41
    assert pat.get("/api/social/dating/discover")["cards"] == []
    status, raw = pat.call("GET", "/api/social/dating/me/export", raw=True)
    assert status == 200 and json.loads(raw)["profile"]["age"] == 41
    assert (
        pat.call(
            "POST", f"/api/social/dating/profiles/{'0' * 32}/swipe", {"action": "like"}
        )[0]
        == 404
    )
    assert pat.call("DELETE", "/api/social/dating/me")[0] == 200
    assert pat.get("/api/social/dating/me")["profile"] is None


# ---------------------------------------------------------------------------
# the hosted build
# ---------------------------------------------------------------------------


def _dating_body(gender, show_me, years=32):
    return {
        "birthday": _born(years),
        "confirm_adult": True,
        "gender": gender,
        "show_me": show_me,
        "prompts": [
            {"id": k, "answer": "Hosted answer"}
            for k in ("slow_sunday", "talk_hours", "comfort")
        ],
        "interests": ["travel", "coffee"],
        "bio": "HOSTED-DATING-BIO",
    }


def test_hosted_members_meet_across_organizations_and_admins_see_nothing(cloud):
    from monosage_cloud.policy import POLICY

    kit.forget_schema()
    db.forget()
    acme = build_org(cloud)
    cloud.billing.subscribe_app(acme.owner, "social")
    other = build_org(cloud, "Other Org", slug_email="other.test")
    cloud.billing.subscribe_app(other.owner, "social")
    nobody = build_org(cloud, "Third Org", slug_email="third.test")
    member = sign_in(cloud, acme.candidate)
    colleague = sign_in(cloud, acme.coach)
    admin = sign_in(cloud, acme.owner)
    stranger = sign_in(cloud, other.candidate)
    for browser, handle in (
        (member, "acmecand"),
        (colleague, "acmecoach"),
        (admin, "acmeowner"),
        (stranger, "othercand"),
    ):
        assert browser.call("POST", "/api/social/me", {"handle": handle})[0] == 201
    status, made = member.call(
        "PUT",
        "/api/social/dating/me",
        {**_dating_body("woman", "men"), "first_name": "Cara"},
    )
    assert status == 200, made
    member_profile = made["profile"]["id"]
    assert (
        made["profile"]["hide_home"] is True
    )  # by default not shown to their own organization
    for browser, name in ((colleague, "Cole"), (admin, "Owen"), (stranger, "Stan")):
        status, data = browser.call(
            "PUT",
            "/api/social/dating/me",
            {**_dating_body("man", "women"), "first_name": name},
        )
        assert status == 200, data
    # the other organization's member meets Cara; her colleagues and her organization's owner do not
    assert [
        c["first_name"]
        for c in stranger.call("GET", "/api/social/dating/discover")[1]["cards"]
    ] == ["Cara"]
    for browser in (colleague, admin):
        assert browser.call("GET", "/api/social/dating/discover")[1]["cards"] == []
        assert (
            browser.call("GET", f"/api/social/dating/profiles/{member_profile}")[0]
            == 404
        )
        assert (
            browser.call(
                "POST",
                f"/api/social/dating/profiles/{member_profile}/swipe",
                {"action": "like"},
            )[0]
            == 404
        )
    # a match across organizations, and its chat
    stan = stranger.call("GET", "/api/social/dating/me")[1]["profile"]["id"]
    assert (
        stranger.call(
            "POST",
            f"/api/social/dating/profiles/{member_profile}/swipe",
            {"action": "like"},
        )[1]["match"]
        is None
    )
    status, liked = member.call(
        "POST", f"/api/social/dating/profiles/{stan}/swipe", {"action": "like"}
    )
    assert status == 200 and liked["match"]["profile"]["first_name"] == "Stan"
    match_id = liked["match"]["id"]
    assert (
        member.call(
            "POST",
            f"/api/social/dating/matches/{match_id}/messages",
            {"text": "HOSTED-DATING-CHAT"},
        )[0]
        == 201
    )
    assert (
        stranger.call("GET", f"/api/social/dating/matches/{match_id}/messages")[1][
            "messages"
        ][0]["text"]
        == "HOSTED-DATING-CHAT"
    )
    for browser in (admin, colleague):
        assert browser.call("GET", f"/api/social/dating/matches/{match_id}")[0] == 404
        assert (
            browser.call("GET", f"/api/social/dating/matches/{match_id}/messages")[0]
            == 404
        )
    # an organization without Social cannot reach Dating
    assert sign_in(cloud, nobody.owner).call("GET", "/api/social/dating/me")[0] == 402
    # the owner's export of the member carries their Social data but nothing of Dating
    exported = cloud.lifecycle.export_candidate(acme.owner, acme.candidate.member_id)
    with cloud.db.read() as connection:
        path = connection.execute(
            "SELECT path FROM exports WHERE export_id = ?", (exported["export_id"],)
        ).fetchone()[0]
    with zipfile.ZipFile(io.BytesIO(open(path, "rb").read())) as archive:
        everything = b"".join(archive.read(name) for name in archive.namelist())
    assert b"acmecand" in everything and b"HOSTED-DATING" not in everything
    # nothing of Dating in the organization's audit log, and every Dating route has a hosted rule:
    # life.read for reads, life.manage for the rest, never audited
    assert not [
        r
        for r in cloud.audit.list(acme.org_id, limit=500)
        if "dating" in json.dumps(r, default=str).lower()
    ]
    from aira.airaapi import http as airaapi_http

    routes = [
        e
        for e in airaapi_http.AIRA_ROUTES
        if e.handler.__module__ == "aira.suite.social.dating_routes"
    ]
    assert len(routes) == 20
    for entry in routes:
        rule = POLICY[entry.attr]
        assert rule.permission == (
            "life.read" if entry.method == "GET" else "life.manage"
        ), entry.attr
        assert rule.audit is False, entry.attr
    # removing the member erases their Dating: the match and its chat are gone for the other person
    cloud.tenancy.remove_member(acme.owner, acme.candidate.member_id)
    assert stranger.call("GET", "/api/social/dating/matches")[1]["matches"] == []
    assert (
        stranger.call("GET", f"/api/social/dating/matches/{match_id}/messages")[0]
        == 404
    )


def test_dating_timestamps_reset_daily(sc, monkeypatch):
    me = Person(sc, "p:me", "Mel")
    me.date(first_name="Mel")
    other = Person(sc, "p:o", "Other")
    other.date(first_name="Other")
    me.swipe(other, "like")
    assert dating.limits(sc, me.acct)["likes_left"] == dating.LIKES_PER_DAY - 1
    tomorrow = dating._today() + timedelta(
        days=1
    )  # Dating's day is the UTC day, not this machine's
    monkeypatch.setattr(dating, "_today", lambda: tomorrow)
    assert dating.limits(sc, me.acct)["likes_left"] == dating.LIKES_PER_DAY
