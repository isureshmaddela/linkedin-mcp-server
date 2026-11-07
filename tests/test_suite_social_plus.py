"""Social's newer features on the server: photos from the device (metadata removed, signed and private), polls,
close friends, pinned and saved posts with collections, comment replies and likes, story replies, reactions and
highlights, Explore, memories, profile grids, follow lists and notifications."""

from __future__ import annotations

import base64
import struct
import zlib
from datetime import timedelta

import pytest

from aira.suite.social import db, market, photos, service, store


@pytest.fixture(autouse=True)
def _social_db(tmp_path):
    db.set_path(lambda: tmp_path / "social-plus.sqlite3")
    db.forget()
    photos.forget()
    yield
    db.reset_path()
    db.forget()
    photos.forget()


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


@pytest.fixture
def people(sc):
    """Pat and Ana (friends, one household), Sam (another household, public), Mia (Pat's child)."""
    pat = _join(sc, "p:pat", "Pat")
    ana = _join(sc, "p:ana", "Ana")
    sam = _join(sc, "p:sam", "Sam", home="home-2")
    mia = _join(sc, "p:mia", "Mia", age="child")
    _friends(sc, pat, ana)
    return pat, ana, sam, mia


def png(text_chunk: bytes = b"Author\x00Pat at 51.5N 0.1W") -> bytes:
    def chunk(kind: bytes, body: bytes) -> bytes:
        return (
            struct.pack(">I", len(body))
            + kind
            + body
            + struct.pack(">I", zlib.crc32(kind + body))
        )

    raw = b"".join(b"\x00" + b"\xff\x00\x00" * 4 for _ in range(3))
    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", struct.pack(">IIBBBBB", 4, 3, 8, 2, 0, 0, 0))
        + chunk(b"tEXt", text_chunk)
        + chunk(b"IDAT", zlib.compress(raw))
        + chunk(b"IEND", b"")
    )


def jpeg_with_exif() -> bytes:
    exif = b"Exif\x00\x00" + b"GPS 51.5N camera X100"
    sof = (
        b"\x08"
        + struct.pack(">HH", 480, 640)
        + b"\x03"
        + b"\x01\x11\x00\x02\x11\x01\x03\x11\x01"
    )
    return (
        b"\xff\xd8"
        + b"\xff\xe0"
        + struct.pack(">H", 16)
        + b"JFIF\x00\x01\x01\x00\x00\x01\x00\x01\x00\x00"
        + b"\xff\xe1"
        + struct.pack(">H", len(exif) + 2)
        + exif
        + b"\xff\xfe"
        + struct.pack(">H", 9)
        + b"comment"
        + b"\xff\xc0"
        + struct.pack(">H", len(sof) + 2)
        + sof
        + b"\xff\xda"
        + struct.pack(">H", 8)
        + b"\x01\x01\x00\x00\x3f\x00"
        + b"\x12\x34\x56"
        + b"\xff\xd9"
    )


def b64(data: bytes) -> str:
    return base64.b64encode(data).decode()


# ---------------------------------------------------------------------------
# photos
# ---------------------------------------------------------------------------


def test_photos_lose_their_metadata_stay_private_and_only_their_owner_attaches_them(
    sc, people, tmp_path
):
    pat, ana, sam, _mia = people
    kept = photos.save(sc, pat, b64(jpeg_with_exif()))
    assert kept["ref"].startswith("media:") and (kept["width"], kept["height"]) == (
        640,
        480,
    )
    body, mime = photos.read(sc, kept["id"])
    assert (
        mime == "image/jpeg"
        and b"Exif" not in body
        and b"GPS" not in body
        and b"comment" not in body
    )
    assert body.startswith(b"\xff\xd8\xff\xe0") and body.endswith(b"\xff\xd9")
    picture = photos.save(sc, pat, "data:image/png;base64," + b64(png()))
    body, mime = photos.read(sc, picture["id"])
    assert (
        mime == "image/png"
        and b"tEXt" not in body
        and b"51.5N" not in body
        and b"IDAT" in body
    )
    # files sit under the media folder, never inside the database
    assert (tmp_path / "media" / "social" / picture["id"][:2]).is_dir()
    with pytest.raises(ValueError, match="JPEG, PNG or WebP"):
        photos.save(sc, pat, b64(b"<svg onload=alert(1)></svg>" + b"x" * 20))
    with pytest.raises(ValueError, match="too large"):
        photos.save(sc, pat, "A" * (photos.MAX_BYTES * 2))
    # a signed address works for a while, a forged one never
    address = photos.url(kept["id"], sc)
    _, query = address.split("?")
    params = dict(part.split("=") for part in query.split("&"))
    photos.verify(kept["id"], params["e"], params["s"], sc)
    with pytest.raises(LookupError):
        photos.verify(kept["id"], params["e"], "0" * 32, sc)
    with pytest.raises(LookupError):
        photos.verify(picture["id"], params["e"], params["s"], sc)
    # only Pat attaches Pat's photos (also when the browser sends back the signed address)
    post = service.create_post(
        sc,
        pat,
        {
            "text": "Garden",
            "photos": [kept["ref"], address],
            "alts": ["Red roses"],
            "filter": "warm",
        },
    )
    assert post["photos"][0].startswith(f"/api/social/photos/{kept['id']}?e=")
    assert post["alts"] == ["Red roses", ""] and post["filter"] == "warm"
    with pytest.raises(ValueError, match="not found"):
        service.create_post(sc, ana, {"text": "Mine now", "photos": [kept["ref"]]})
    with pytest.raises(ValueError):
        service.create_post(
            sc, ana, {"text": "Made up", "photos": ["media:" + "a" * 32]}
        )
    with pytest.raises(ValueError, match="filter"):
        service.create_post(
            sc, pat, {"text": "x", "photos": [kept["ref"]], "filter": "sepia-ish"}
        )
    # Sam is not Pat's friend: the friends-only post (and so its signed photo) is not his to see
    assert service.feed(sc, sam)["posts"] == []
    # profile picture and cover from the device; a listing with an uploaded photo
    cover = photos.save(sc, pat, b64(png()))
    updated = service.account_out(
        service.update_account(sc, pat, {"avatar": kept["ref"], "cover": cover["ref"]})
    )
    assert updated["avatar"].startswith("/api/social/photos/") and updated[
        "cover"
    ].startswith("/api/social/photos/")
    with pytest.raises(ValueError):
        service.update_account(sc, ana, {"cover": cover["ref"]})
    assert service.mini(service.account(sc, pat["id"]))["avatar"].startswith(
        "/api/social/photos/"
    )
    listing = market.create(
        sc,
        service.account(sc, pat["id"]),
        {"title": "Bench", "price": "20", "photos": [picture["ref"]]},
    )
    assert listing["photos"][0].startswith("/api/social/photos/")
    page = market.public_page(sc, listing["id"], "T" * 43)
    assert page["listing"]["photos"] == [
        f"/api/public/{'T' * 43}/social-photo/{picture['id']}"
    ]
    # a group's picture
    group = service.create_group(
        sc, pat, {"name": "Garden club", "photo": cover["ref"]}
    )
    assert group["photo"].startswith("/api/social/photos/")
    # an upload nothing uses goes after a day; one in use stays
    stray = photos.save(sc, pat, b64(png()))
    old = store.stamp(store.utcnow() - timedelta(days=2))
    sc.execute("UPDATE so_media SET created_at = ?", (old,))
    sc.commit()
    assert photos.sweep(sc) == 1
    assert store.row(sc, "SELECT id FROM so_media WHERE id = ?", (stray["id"],)) is None
    assert store.row(sc, "SELECT id FROM so_media WHERE id = ?", (kept["id"],))
    # erasing Pat removes his photo files from the disk too
    path = (
        tmp_path
        / "media"
        / store.row(sc, "SELECT path FROM so_media WHERE id = ?", (kept["id"],))["path"]
    )
    assert path.exists()
    service.erase_accounts(sc, [pat["id"]])
    assert not path.exists()
    assert store.row(sc, "SELECT COUNT(*) AS n FROM so_media", ())["n"] == 0


# ---------------------------------------------------------------------------
# posts: polls, feelings, close friends, pins, saves, comment threads
# ---------------------------------------------------------------------------


def test_polls_vote_once_and_show_results_after_voting(sc, people):
    pat, ana, sam, _mia = people
    with pytest.raises(ValueError, match="two to four"):
        service.create_post(sc, pat, {"text": "Lunch?", "poll": {"options": ["Pizza"]}})
    with pytest.raises(ValueError, match="different"):
        service.create_post(
            sc, pat, {"text": "Lunch?", "poll": {"options": ["Pizza", "pizza"]}}
        )
    with pytest.raises(ValueError, match="1, 3 or 7"):
        service.create_post(
            sc, pat, {"text": "Lunch?", "poll": {"options": ["A", "B"], "days": 2}}
        )
    with pytest.raises(ValueError, match="question"):
        service.create_post(sc, pat, {"poll": {"options": ["A", "B"]}})
    post = service.create_post(
        sc,
        pat,
        {
            "text": "Where shall we eat?",
            "poll": {"options": ["Pizza", "Curry", "Salad"], "days": 1},
            "feeling": "excited",
            "place": "Riverside",
        },
    )
    assert post["feeling"] == "excited" and post["place"] == "Riverside"
    assert [o["text"] for o in post["poll"]["options"]] == ["Pizza", "Curry", "Salad"]
    seen = service.post_view(sc, ana, service._post_row(sc, post["id"]))
    assert (
        seen["poll"]["results"] is False and seen["poll"]["options"][0]["votes"] is None
    )
    voted = service.vote(sc, ana, post["id"], 1)
    assert voted["poll"]["my_vote"] == 1 and voted["poll"]["options"][1]["votes"] == 1
    assert voted["poll"]["options"][1]["percent"] == 100
    with pytest.raises(ValueError, match="already voted"):
        service.vote(sc, ana, post["id"], 0)
    with pytest.raises(ValueError, match="answers"):
        service.vote(sc, pat, post["id"], 7)
    with pytest.raises(LookupError):
        service.vote(sc, sam, post["id"], 0)  # a friends-only post Sam cannot see
    kinds = [n["kind"] for n in service.notifications(sc, pat)["notifications"]]
    assert "poll_vote" in kinds
    # when the poll is over, everyone who voted and its author hear which answer won (once)
    sc.execute("UPDATE so_polls SET closes_at = ?", (store.later(-1),))
    sc.commit()
    for who in (pat, ana):
        results = [
            n
            for n in service.notifications(sc, who)["notifications"]
            if n["kind"] == "poll_result"
        ]
        assert len(results) == 1 and "Curry" in results[0]["text"]
    with pytest.raises(PermissionError, match="over"):
        service.vote(sc, pat, post["id"], 0)
    assert (
        service.post_view(sc, sam, service._post_row(sc, post["id"]))["poll"]["closed"]
        is True
    )


def test_close_friends_see_close_friends_posts_and_stories(sc, people):
    pat, ana, sam, _mia = people
    tom = _join(sc, "p:tom", "Tom")
    _friends(sc, pat, tom)
    with pytest.raises(PermissionError, match="friends"):
        service.set_close_friend(sc, pat, sam["id"], True)
    service.set_close_friend(sc, pat, ana["id"], True)
    assert [p["id"] for p in service.close_friends(sc, pat)] == [ana["id"]]
    post = service.create_post(
        sc, pat, {"text": "Only for my closest", "audience": "close_friends"}
    )
    assert post["audience"] == "close_friends"
    assert [p["id"] for p in service.feed(sc, ana)["posts"]] == [post["id"]]
    assert service.feed(sc, tom)["posts"] == []
    story = service.create_story(
        sc, pat, {"text": "Close friends story", "audience": "close_friends"}
    )
    assert [s["id"] for e in service.stories(sc, ana) for s in e["stories"]] == [
        story["id"]
    ]
    assert service.stories(sc, tom) == []
    with pytest.raises(LookupError):
        service.see_story(sc, tom, story["id"])
    # the list is private: Ana sees only that she may see the post, Pat sees her mark on her profile
    assert (
        service.profile_view(sc, pat, service.account(sc, ana["id"]))["close_friend"]
        is True
    )
    assert (
        service.profile_view(sc, ana, service.account(sc, pat["id"]))["close_friend"]
        is False
    )
    # unfriending ends it; blocking clears the list both ways
    service.remove_friend(sc, ana, pat["id"])
    assert service.feed(sc, ana)["posts"] == []
    _friends(sc, pat, ana)
    service.block(sc, ana, pat["id"])
    assert service.close_friends(sc, pat) == []


def test_pins_saves_collections_and_comment_threads(sc, people):
    pat, ana, sam, _mia = people
    first = service.create_post(sc, pat, {"text": "First", "audience": "public"})
    second = service.create_post(sc, pat, {"text": "Second", "audience": "public"})
    service.pin_post(sc, pat, first["id"], True)
    assert [
        p["text"] for p in service.person_posts(sc, ana, service.account(sc, pat["id"]))
    ][:2] == ["First", "Second"]
    with pytest.raises(LookupError):
        service.pin_post(sc, ana, first["id"], True)
    for n in range(3):
        extra = service.create_post(sc, pat, {"text": f"Extra {n}"})
        if n < 2:
            service.pin_post(sc, pat, extra["id"], True)
    with pytest.raises(ValueError, match="up to 3"):
        service.pin_post(sc, pat, second["id"], True)
    # saving, into named collections
    recipes = service.create_collection(sc, ana, {"name": "Recipes"})
    with pytest.raises(ValueError, match="already"):
        service.create_collection(sc, ana, {"name": "recipes"})
    service.save_post(sc, ana, first["id"], {"collection_id": recipes["id"]})
    service.save_post(sc, ana, second["id"], {})
    shelves = {c["name"]: c["count"] for c in service.collections(sc, ana)}
    assert shelves == {"All saved": 2, "Recipes": 1}
    assert [p["id"] for p in service.saved_posts(sc, ana, recipes["id"])] == [
        first["id"]
    ]
    assert (
        service.post_view(sc, ana, service._post_row(sc, first["id"]))["collection_id"]
        == recipes["id"]
    )
    with pytest.raises(LookupError):
        service.saved_posts(sc, sam, recipes["id"])  # someone else's collection
    service.delete_collection(sc, ana, recipes["id"])
    assert {c["name"] for c in service.collections(sc, ana)} == {"All saved"}
    assert len(service.saved_posts(sc, ana)) == 2
    service.delete_post(sc, pat, second["id"])
    assert [p["id"] for p in service.saved_posts(sc, ana)] == [first["id"]]
    service.save_post(sc, ana, first["id"], {"saved": False})
    assert service.saved_posts(sc, ana) == []
    # one level of replies; a reply to a reply joins its thread; likes on comments
    top = service.comment(sc, sam, first["id"], {"text": "Lovely"})["comment"]
    reply = service.comment(
        sc, pat, first["id"], {"text": "Thank you", "parent_id": top["id"]}
    )["comment"]
    deeper = service.comment(
        sc, ana, first["id"], {"text": "Agreed", "parent_id": reply["id"]}
    )["comment"]
    assert reply["parent_id"] == top["id"] and deeper["parent_id"] == top["id"]
    liked = service.like_comment(sc, pat, top["id"], True)
    assert liked == {"liked": True, "likes": 1}
    thread = service.comments(sc, ana, first["id"])
    assert [(c["text"], c["parent_id"] == top["id"]) for c in thread] == [
        ("Lovely", False),
        ("Thank you", True),
        ("Agreed", True),
    ]
    assert thread[0]["likes"] == 1 and thread[0]["liked"] is False
    kinds = [n["kind"] for n in service.notifications(sc, sam)["notifications"]]
    assert "reply" in kinds and "comment_like" in kinds
    # a removed comment takes its replies with it; a blocked person's replies disappear for the blocker
    service.block(sc, ana, sam["id"])
    assert [c["text"] for c in service.comments(sc, ana, first["id"])] == []
    service.delete_comment(sc, sam, top["id"])
    assert service.comments(sc, pat, first["id"]) == []
    with pytest.raises(LookupError):
        service.comment(sc, pat, first["id"], {"text": "x", "parent_id": top["id"]})


# ---------------------------------------------------------------------------
# stories: replies, reactions, viewers, highlights
# ---------------------------------------------------------------------------


def test_story_replies_reactions_viewers_and_highlights(sc, people):
    pat, ana, sam, _mia = people
    story = service.create_story(sc, pat, {"text": "Beach day"})
    service.see_story(sc, ana, story["id"])
    assert service.react_story(sc, ana, story["id"], "🔥") == {"my_reaction": "🔥"}
    with pytest.raises(ValueError):
        service.react_story(sc, ana, story["id"], "💩")
    viewers = service.story_viewers(sc, pat, story["id"])
    assert [(v["id"], v["reaction"]) for v in viewers] == [(ana["id"], "🔥")]
    with pytest.raises(LookupError):
        service.story_viewers(sc, ana, story["id"])
    replied = service.reply_story(sc, ana, story["id"], {"text": "So jealous!"})
    messages = service.messages(sc, pat, replied["chat"]["id"])["messages"]
    assert "So jealous!" in messages[-1]["text"] and "Beach day" in messages[-1]["text"]
    kinds = [n["kind"] for n in service.notifications(sc, pat)["notifications"]]
    assert {"story_reply", "story_reaction"} <= set(kinds)
    with pytest.raises(LookupError):
        service.reply_story(
            sc, sam, story["id"], {"text": "Hi"}
        )  # not a friend: the story is not his
    # a highlight keeps the story on the profile after its day
    highlight = service.save_highlight(
        sc, pat, {"title": "Summer", "story_ids": [story["id"]]}
    )
    sc.execute("UPDATE so_stories SET expires_at = ?", (store.later(-1),))
    sc.commit()
    assert service.stories(sc, ana) == []
    kept = service.highlights(sc, ana, service.account(sc, pat["id"]))
    assert [h["title"] for h in kept] == ["Summer"] and kept[0]["stories"][0][
        "id"
    ] == story["id"]
    service.see_story(sc, ana, story["id"])  # still viewable from the highlight
    assert (
        service.highlights(sc, sam, service.account(sc, pat["id"])) == []
    )  # stories are for friends
    with pytest.raises(LookupError):
        service.save_highlight(sc, ana, {"title": "Stolen", "story_ids": [story["id"]]})
    with pytest.raises(ValueError, match="at least one"):
        service.save_highlight(sc, pat, {"title": "Empty", "story_ids": []})
    assert [s["id"] for s in service.my_stories(sc, pat)] == [story["id"]]
    service.delete_highlight(sc, pat, highlight["id"])
    assert service.highlights(sc, ana, service.account(sc, pat["id"])) == []


# ---------------------------------------------------------------------------
# Explore, memories, profile grids, follow lists, notifications
# ---------------------------------------------------------------------------


def test_explore_trending_people_tags_and_search_respect_privacy(sc, people):
    pat, ana, sam, mia = people
    loud = service.create_post(
        sc, sam, {"text": "Sunrise over the hills #nature", "audience": "public"}
    )
    quiet = service.create_post(
        sc, ana, {"text": "Quiet #nature morning", "audience": "public"}
    )
    service.create_post(
        sc, ana, {"text": "Friends only #nature secret", "audience": "friends"}
    )
    for who in (pat, ana):
        service.react(sc, who, loud["id"], "❤️")
    service.comment(sc, pat, loud["id"], {"text": "Wow"})
    seen = service.explore(sc, pat)
    assert [t["id"] for t in seen["trending"]][:2] == [loud["id"], quiet["id"]]
    assert {"tag": "nature", "posts": 2} in seen["tags"]
    assert sam["id"] in {p["id"] for p in seen["people"]}  # a public adult to follow
    # search finds what each person may see: Ana's friends-only post for her friend Pat, not for Sam
    assert len(service.explore_search(sc, pat, "secret")["posts"]) == 1
    assert service.explore_search(sc, sam, "secret")["posts"] == []
    assert [p["id"] for p in service.explore_search(sc, pat, "sunrise")["posts"]] == [
        loud["id"]
    ]
    assert service.explore_search(sc, pat, "#nat")["tags"][0]["tag"] == "nature"
    assert service.explore_search(sc, pat, "sam")["people"][0]["id"] == sam["id"]
    # blocking hides both ways
    service.block(sc, pat, sam["id"])
    assert loud["id"] not in [t["id"] for t in service.explore(sc, pat)["trending"]]
    assert service.explore_search(sc, sam, "quiet")["posts"]  # Ana is not blocked
    assert service.explore_search(sc, pat, "sunrise")["posts"] == []
    # a child sees only friends' and the household's posts, and no strangers to follow
    child_view = service.explore(sc, mia)
    assert loud["id"] not in [t["id"] for t in child_view["trending"]]
    assert quiet["id"] in [
        t["id"] for t in child_view["trending"]
    ]  # Ana is in Mia's household
    assert sam["id"] not in {p["id"] for p in child_view["people"]}
    assert service.explore_search(sc, mia, "sunrise")["posts"] == []


def test_memories_grids_follow_lists_and_notifications(sc, people):
    pat, ana, sam, mia = people
    old = service.create_post(sc, pat, {"text": "A year ago today, with @ana"})
    last_year = store.utcnow().replace(year=store.utcnow().year - 1)
    sc.execute(
        "UPDATE so_posts SET created_at = ? WHERE id = ?",
        (store.stamp(last_year), old["id"]),
    )
    sc.commit()
    service.create_post(sc, pat, {"text": "Today"})
    memories = service.memories(sc, pat, store.utcnow().strftime("%m-%d"))
    assert [m["id"] for m in memories] == [old["id"]] and memories[0]["years_ago"] == 1
    assert service.memories(sc, ana) == []
    with pytest.raises(ValueError, match="month and day"):
        service.memories(sc, pat, "2026-13-40")
    # the profile grids: photos, and posts that mention someone
    picture = photos.save(sc, pat, b64(png()))
    with_photo = service.create_post(
        sc, pat, {"text": "Look", "photos": [picture["ref"]], "audience": "public"}
    )
    tiles = service.person_grid(sc, ana, service.account(sc, pat["id"]), "photos")
    assert [t["id"] for t in tiles] == [with_photo["id"]] and tiles[0][
        "photo"
    ].startswith("/api/social/photos/")
    assert [
        t["id"]
        for t in service.person_grid(sc, pat, service.account(sc, ana["id"]), "tagged")
    ] == [old["id"]]
    assert (
        service.person_grid(sc, sam, service.account(sc, ana["id"]), "tagged") == []
    )  # a friends-only post
    # followers and following; a private profile keeps its lists
    service.follow(sc, ana, "account", sam["id"], True)
    service.follow(sc, pat, "account", sam["id"], True)
    lists = service.follow_lists(sc, ana, service.account(sc, sam["id"]))
    assert {p["id"] for p in lists["followers"]} == {ana["id"], pat["id"]}
    assert (
        service.profile_view(sc, sam, service.account(sc, ana["id"]))["following_count"]
        == 1
    )
    service.update_account(sc, ana, {"visibility": "private"})
    with pytest.raises(PermissionError):
        service.follow_lists(sc, sam, service.account(sc, ana["id"]))
    # links in the bio: https only
    with pytest.raises(ValueError, match="https"):
        service.update_account(sc, pat, {"links": [{"url": "http://example.com"}]})
    with pytest.raises(ValueError, match="At most"):
        service.update_account(
            sc, pat, {"links": [f"https://e.com/{n}" for n in range(6)]}
        )
    linked = service.update_account(
        sc, pat, {"links": [{"url": "https://pat.example", "label": "My shop"}]}
    )
    assert linked["links"] == [{"url": "https://pat.example", "label": "My shop"}]
    # notifications: one at a time, or all
    listed = service.notifications(sc, ana)
    assert listed["unread"] >= 1
    first = listed["notifications"][0]
    assert service.read_notification(sc, ana, first["id"]) == listed["unread"] - 1
    with pytest.raises(LookupError):
        service.read_notification(sc, sam, first["id"])
    # the export carries the new things; erasing removes them
    service.save_post(sc, pat, with_photo["id"], {})
    data = service.export_account(sc, pat["id"])
    assert (
        data["saved"]
        and data["photos"]
        and "close_friends" in data
        and "highlights" in data
    )
    service.erase_accounts(sc, [pat["id"]])
    assert store.row(sc, "SELECT COUNT(*) AS n FROM so_saves", ())["n"] == 0
    assert mia  # the child stays


# ---------------------------------------------------------------------------
# the single-user build over HTTP: upload, signed addresses, a shared listing's photos
# ---------------------------------------------------------------------------

import test_careers  # noqa: E402
from life_support import Household  # noqa: E402

from aira.suite import kit, public  # noqa: E402

env = test_careers.env
api = test_careers.api
stub = test_careers.stub


def _visitor(env, path):
    import urllib.error
    import urllib.request

    try:
        with urllib.request.urlopen(env.base + path, timeout=30) as response:
            return response.status, response.read(), response.headers
    except urllib.error.HTTPError as error:
        return error.code, error.read(), error.headers


def test_photos_over_http_are_signed_private_and_shown_on_shared_pages(env, api):
    kit.forget_schema()
    public.set_default_index(test_careers.app._public_link_index)
    db.reset_path()
    db.forget()
    photos.forget()
    house = Household(env, api)
    pat = house.owner
    ana = house.add("Ana")
    pat.post("/api/social/me", {"handle": "pat"}, expect=201)
    ana_account = ana.post("/api/social/me", {"handle": "ana"}, expect=201)["account"]
    uploaded = pat.post(
        "/api/social/photos", {"data": b64(jpeg_with_exif())}, expect=201
    )["photo"]
    assert uploaded["ref"] == f"media:{uploaded['id']}"
    status, body = pat.call("GET", uploaded["url"], raw=True)
    assert status == 200 and body.startswith(b"\xff\xd8") and b"GPS" not in body
    status, _ = pat.call(
        "GET", uploaded["url"].split("&s=")[0] + "&s=" + "0" * 32, raw=True
    )
    assert status == 404
    status, refused = ana.call(
        "POST", "/api/social/photos", {"data": b64(b"GIF89a" + b"\x00" * 40)}
    )
    assert status == 400 and "JPEG, PNG or WebP" in refused["error"]
    # Ana cannot attach Pat's photo; a friends-only post shows Ana a signed address only once she is a friend
    status, _ = ana.call(
        "POST", "/api/social/posts", {"text": "x", "photos": [uploaded["ref"]]}
    )
    assert status == 400
    post = pat.post(
        "/api/social/posts",
        {"text": "Friends only", "photos": [uploaded["ref"]]},
        expect=201,
    )["post"]
    assert ana.call("GET", f"/api/social/posts/{post['id']}")[0] == 404
    pat.post(f"/api/social/friends/{ana_account['id']}", {})
    ana.post(f"/api/social/friends/{pat.get('/api/social/me')['account']['id']}", {})
    seen = ana.get(f"/api/social/posts/{post['id']}")["post"]
    assert ana.call("GET", seen["photos"][0], raw=True)[0] == 200
    # a listing shared outside Monosage shows its own photo to a visitor, and nothing else
    listing = pat.post(
        "/api/social/market",
        {"title": "Bench", "price": "20", "photos": [uploaded["ref"]]},
        expect=201,
    )["listing"]
    link = pat.post(f"/api/social/market/{listing['id']}/share", {}, expect=201)["link"]
    status, page, _ = _visitor(env, f"/api/public/{link['token']}")
    import json as _json

    shown = _json.loads(page)["page"]["listing"]["photos"]
    assert shown == [f"/api/public/{link['token']}/social-photo/{uploaded['id']}"]
    status, body, headers = _visitor(env, shown[0])
    assert (
        status == 200
        and body.startswith(b"\xff\xd8")
        and headers.get("X-Content-Type-Options") == "nosniff"
    )
    other = pat.post("/api/social/photos", {"data": b64(png())}, expect=201)["photo"]
    assert (
        _visitor(env, f"/api/public/{link['token']}/social-photo/{other['id']}")[0]
        == 404
    )
    # the person's own account comes back with loadable picture addresses
    me = pat.ok(
        "PATCH",
        "/api/social/me",
        {
            "avatar": other["ref"],
            "links": [{"url": "https://pat.example", "label": "Shop"}],
        },
    )["account"]
    assert (
        me["avatar"].startswith("/api/social/photos/")
        and me["links"][0]["label"] == "Shop"
    )
    assert pat.call("GET", me["avatar"], raw=True)[0] == 200


def test_a_chat_message_carries_an_uploaded_photo_of_its_sender_only(sc, people):
    pat, ana, _sam, _mia = people
    chat = service.open_direct(sc, pat, ana["id"])
    picture = photos.save(sc, pat, b64(png()))
    sent = service.send(sc, pat, chat["id"], {"photo": picture["ref"]})["message"]
    assert sent["photo"].startswith(f"/api/social/photos/{picture['id']}?")
    seen = service.messages(sc, ana, chat["id"])["messages"][-1]
    assert seen["photo"].startswith("/api/social/photos/")
    with pytest.raises(ValueError):
        service.send(sc, ana, chat["id"], {"photo": picture["ref"]})
    assert photos.in_use(sc, picture["id"])


# ---------------------------------------------------------------------------
# the hosted build: photos cross organizations only to the people who may see them
# ---------------------------------------------------------------------------


def test_hosted_photos_follow_the_post_across_organizations(cloud):
    from cloud_support import build_org, sign_in

    kit.forget_schema()
    db.forget()
    photos.forget()
    acme = build_org(cloud)
    cloud.billing.subscribe_app(acme.owner, "social")
    owner = sign_in(cloud, acme.owner)
    assert owner.call("POST", "/api/social/me", {"handle": "acmeowner"})[0] == 201
    other = build_org(cloud, "Other Org", slug_email="other.test")
    cloud.billing.subscribe_app(other.owner, "social")
    friend = sign_in(cloud, other.owner)
    status, joined = friend.call("POST", "/api/social/me", {"handle": "otherowner"})
    assert status == 201
    status, uploaded = owner.call(
        "POST", "/api/social/photos", {"data": b64(jpeg_with_exif())}
    )
    assert status == 201, uploaded
    photo = uploaded["photo"]
    status, body = owner.call("GET", photo["url"], raw=True)
    assert status == 200 and body.startswith(b"\xff\xd8") and b"GPS" not in body
    status, made = owner.call(
        "POST",
        "/api/social/posts",
        {"text": "Team day", "photos": [photo["ref"]], "audience": "friends"},
    )
    assert status == 201
    post_id = made["post"]["id"]
    # not friends yet: the other organization's member cannot see the post, so gets no address for its photo
    assert friend.call("GET", f"/api/social/posts/{post_id}")[0] == 404
    owner_id = friend.call("GET", "/api/social/search?q=acmeowner")[1]["people"][0][
        "id"
    ]
    friend.call("POST", f"/api/social/friends/{owner_id}", {})
    owner.call("POST", f"/api/social/friends/{joined['account']['id']}", {})
    status, seen = friend.call("GET", f"/api/social/posts/{post_id}")
    assert status == 200
    status, body = friend.call("GET", seen["post"]["photos"][0], raw=True)
    assert status == 200 and body.startswith(b"\xff\xd8")
    # a made-up signature is refused, and an organization without Social gets nothing
    assert (
        friend.call("GET", photo["url"].split("&s=")[0] + "&s=" + "0" * 32, raw=True)[0]
        == 404
    )
    third = build_org(cloud, "Third Org", slug_email="third.test")
    assert sign_in(cloud, third.owner).call("GET", photo["url"], raw=True)[0] == 402
