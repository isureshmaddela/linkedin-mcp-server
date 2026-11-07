"""Videos (aira/suite/videos): uploads and media, who sees what, views, the library, safety, copyright and erasure,
in both builds."""

from __future__ import annotations

import base64
import json
import urllib.error
import urllib.request

import pytest
from cloud_support import build_org, sign_in
from life_support import Household
from videos_support import PNG, VTT, Clock, make_mp4, make_webm, upload

import test_careers
from aira import suite
from aira.suite import kit, public
from aira.suite.videos import db, media, service, store

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
def sc():
    db.forget()
    connection = db.connect()
    yield connection
    connection.close()


def _person(sc, key, name, *, age="adult", home="home-1", owner=False, prefs=None):
    ident = {
        "key": key,
        "profile": {
            "id": key,
            "name": name,
            "emoji": "🙂",
            "age_group": age,
            "prefs": prefs or {},
        },
        "home": home,
        "owner": owner,
        "operator": owner,
        "hosted": False,
    }
    return service.viewer(sc, ident)


def _file(sc, me, data, kind="video"):
    started = media.start(sc, me["id"], me["home"], kind, f"x.{kind}", len(data))
    offset = 0
    while offset < len(data):
        part = data[offset : offset + 1000]
        media.append(
            sc, started["id"], me["id"], offset, base64.b64encode(part).decode()
        )
        offset += len(part)
    return media.complete(sc, started["id"], me["id"])


def _video(sc, me, **raw):
    data = raw.pop("data", None) or make_mp4(
        raw.pop("seconds", 120), *raw.pop("size", (1280, 720))
    )
    found = _file(sc, me, data)
    return service.create_video(
        sc, me, {"title": "A walk", "file_id": found["id"], **raw}
    )


@pytest.fixture
def people(sc):
    """Pat (adult, owner) and Mia (Pat's child) in one household, Teo (a teen) and Sam (adult) elsewhere."""
    pat = _person(sc, "p:pat", "Pat", owner=True)
    mia = _person(sc, "p:mia", "Mia", age="child")
    teo = _person(sc, "p:teo", "Teo", age="teen", home="home-2")
    sam = _person(sc, "p:sam", "Sam", home="home-3")
    return pat, mia, teo, sam


# ---------------------------------------------------------------------------
# media: uploads, types, space, ranges, signatures
# ---------------------------------------------------------------------------


def test_media_types_are_checked_by_their_first_bytes():
    assert media.sniff(make_mp4()[:64], "video") == "video/mp4"
    assert (
        media.sniff(b"\x00\x00\x00\x14ftypqt  " + b"\x00" * 8, "video")
        == "video/quicktime"
    )
    assert media.sniff(make_webm()[:64], "video") == "video/webm"
    assert media.sniff(PNG[:16], "image") == "image/png"
    assert media.sniff(b"\xff\xd8\xff\xe0" + b"\x00" * 12, "image") == "image/jpeg"
    assert media.sniff(b"RIFF\x00\x00\x00\x00WEBPVP8 ", "image") == "image/webp"
    assert media.sniff(b"\xef\xbb\xbfWEBVTT\n", "captions") == "text/vtt"
    for head, kind in (
        (b"<html><script>", "video"),
        (b"GIF89a", "image"),
        (b"1\n00:00", "captions"),
        (PNG, "video"),
    ):
        with pytest.raises(ValueError):
            media.sniff(head, kind)


def test_chunked_upload_resumes_and_reads_the_mp4_header(sc, people):
    pat = people[0]
    data = make_mp4(42.5, 1920, 1080, rotate=True, payload=10_000)
    started = media.start(sc, pat["id"], pat["home"], "video", "trip.mp4", len(data))
    assert started["received"] == 0 and started["chunk_bytes"] == media.CHUNK_BYTES
    first = media.append(
        sc, started["id"], pat["id"], 0, base64.b64encode(data[:4000]).decode()
    )
    assert first["received"] == 4000 and first["mime"] == "video/mp4"
    with pytest.raises(ValueError, match="starts at byte 4000"):
        media.append(
            sc, started["id"], pat["id"], 0, base64.b64encode(data[:10]).decode()
        )
    with pytest.raises(LookupError):
        media.append(
            sc,
            started["id"],
            "someone-else",
            4000,
            base64.b64encode(data[4000:]).decode(),
        )
    # the connection drops; the browser asks where it got to and sends the rest
    assert media.upload(sc, started["id"], pat["id"])["received"] == 4000
    media.append(
        sc, started["id"], pat["id"], 4000, base64.b64encode(data[4000:]).decode()
    )
    done = media.complete(sc, started["id"], pat["id"])
    assert done["mime"] == "video/mp4" and done["size"] == len(data)
    assert done["duration"] == 42.5 and (done["width"], done["height"]) == (
        1080,
        1920,
    )  # turned upright
    assert (media.root() / media.file_row(sc, done["id"])["path"]).read_bytes() == data
    with pytest.raises(ValueError, match="not finished"):
        unfinished = media.start(sc, pat["id"], pat["home"], "video", "x.mp4", 100)
        media.complete(sc, unfinished["id"], pat["id"])


def test_a_disguised_file_too_big_or_too_many_uploads_is_refused(
    sc, people, monkeypatch
):
    pat = people[0]
    started = media.start(sc, pat["id"], pat["home"], "video", "evil.mp4", 100)
    with pytest.raises(ValueError, match="MP4, MOV or WebM"):
        media.append(
            sc,
            started["id"],
            pat["id"],
            0,
            base64.b64encode(b"<html>" + b"x" * 94).decode(),
        )
    with pytest.raises(ValueError, match="too large"):
        media.start(sc, pat["id"], pat["home"], "image", "big.png", 11 * media.MB)
    monkeypatch.setenv("AIRA_VIDEO_MAX_BYTES", "5000")
    with pytest.raises(ValueError, match="too large"):
        media.start(sc, pat["id"], pat["home"], "video", "big.mp4", 6000)
    for _ in range(4):
        media.start(sc, pat["id"], pat["home"], "captions", "a.vtt", 10)
    with pytest.raises(PermissionError, match="other uploads"):
        media.start(sc, pat["id"], pat["home"], "captions", "a.vtt", 10)


def test_an_upload_that_would_fill_the_drive_names_aira_media_dir(
    sc, people, monkeypatch
):
    pat = people[0]
    monkeypatch.setattr(media, "free_bytes", lambda target: 900 * media.MB)
    with pytest.raises(ValueError, match="AIRA_MEDIA_DIR") as refused:
        media.start(sc, pat["id"], pat["home"], "video", "film.mp4", 10 * media.MB)
    assert "900 MB is free in" in str(refused.value) and "2.0 GB free" in str(
        refused.value
    )
    monkeypatch.setenv("AIRA_MEDIA_RESERVE_BYTES", str(100 * media.MB))
    assert (
        media.start(sc, pat["id"], pat["home"], "video", "film.mp4", 10 * media.MB)[
            "size"
        ]
        == 10 * media.MB
    )
    media.show_paths = False  # the hosted build never names its folders
    monkeypatch.setenv("AIRA_MEDIA_RESERVE_BYTES", "")
    with pytest.raises(ValueError) as hosted:
        media.start(sc, pat["id"], pat["home"], "video", "film.mp4", 10 * media.MB)
    assert " in " not in str(hosted.value).split("free")[0]


def test_aira_media_dir_moves_the_files(sc, people, monkeypatch, tmp_path):
    target = tmp_path / "elsewhere"
    monkeypatch.setenv("AIRA_MEDIA_DIR", str(target))
    done = _file(sc, people[0], make_mp4())
    assert (target / media.file_row(sc, done["id"])["path"]).is_file()


def test_ranges_answer_206_and_never_read_more_than_a_piece(sc, people, monkeypatch):
    data = make_mp4(payload=50_000)
    found = media.file_row(sc, _file(sc, people[0], data)["id"])
    status, body, headers = media.read_range(found, "bytes=100-199")
    assert (
        status == 206
        and body == data[100:200]
        and ("Content-Range", f"bytes 100-199/{len(data)}") in headers
    )
    status, body, _headers = media.read_range(found, "bytes=-50")
    assert status == 206 and body == data[-50:]
    assert media.read_range(found, f"bytes={len(data)}-")[0] == 416
    assert media.read_range(found, "")[:2] == (200, data)
    monkeypatch.setattr(media, "RANGE_BYTES", 1000)
    status, body, headers = media.read_range(found, "bytes=0-")
    assert (
        status == 206
        and len(body) == 1000
        and ("Content-Range", f"bytes 0-999/{len(data)}") in headers
    )
    status, body, _headers = media.read_range(found, "")
    assert status == 206 and len(body) == 1000


def test_signed_addresses_expire_and_cannot_be_reused_for_another_file(
    sc, people, monkeypatch
):
    first = _file(sc, people[0], make_mp4())["id"]
    second = _file(sc, people[0], make_mp4())["id"]
    address = media.signed_url(sc, first)
    query = dict(part.split("=") for part in address.split("?")[1].split("&"))
    media.verify(sc, first, query["e"], query["s"])
    with pytest.raises(LookupError):
        media.verify(sc, second, query["e"], query["s"])
    with pytest.raises(LookupError):
        media.verify(sc, first, query["e"], "0" * 32)
    monkeypatch.setattr(media.time, "time", lambda: int(query["e"]) + 1)
    with pytest.raises(LookupError):
        media.verify(sc, first, query["e"], query["s"])


# ---------------------------------------------------------------------------
# videos: kinds, ratings, who sees what
# ---------------------------------------------------------------------------


def test_reels_and_short_films_have_their_own_rules(sc, people):
    pat = people[0]
    with pytest.raises(ValueError, match="at most 90 seconds"):
        _video(sc, pat, kind="reel", seconds=95, size=(720, 1280))
    with pytest.raises(ValueError, match="tall"):
        _video(sc, pat, kind="reel", seconds=20, size=(1280, 720))
    reel = _video(sc, pat, kind="reel", seconds=20, size=(720, 1280))
    assert reel["kind"] == "reel" and reel["duration"] == 20
    film = _video(
        sc,
        pat,
        kind="short",
        genre="documentary",
        credits="Director: Pat\nCamera: Ana",
        seconds=600,
    )
    assert film["genre"] == "documentary" and film["credits"] == [
        {"role": "Director", "name": "Pat"},
        {"role": "Camera", "name": "Ana"},
    ]
    with pytest.raises(ValueError, match="children must be rated for everyone"):
        _video(sc, pat, for_kids=True, rating="teen")
    with pytest.raises(ValueError, match="already a video"):
        service.create_video(
            sc, pat, {"title": "Again", "file_id": reel_file(sc, reel)}
        )
    other = _person(sc, "p:other", "Other", home="home-9")
    with pytest.raises(LookupError):
        service.create_video(
            sc, other, {"title": "Stolen", "file_id": reel_file(sc, reel)}
        )


def reel_file(sc, video):
    return store.row(sc, "SELECT file_id FROM vi_videos WHERE id = ?", (video["id"],))[
        "file_id"
    ]


def test_ratings_and_visibility_decide_who_sees_a_video(sc, people):
    pat, mia, teo, sam = people
    everyone = _video(sc, pat, title="Garden tour", rating="everyone")
    teen = _video(sc, pat, title="Skate tricks", rating="teen")
    mature = _video(sc, pat, title="War film", rating="mature")
    unlisted = _video(sc, pat, title="Family only", visibility="unlisted")
    private = _video(sc, pat, title="Diary", visibility="private")
    listed = lambda viewer: {v["title"] for v in service.home(sc, viewer)["recent"]}  # noqa: E731
    assert listed(sam) == {"Garden tour", "Skate tricks", "War film"}
    assert listed(teo) == {"Garden tour", "Skate tricks"}
    assert listed(mia) == {"Garden tour"}
    easy = _person(
        sc, "p:gran", "Gran", age="senior", home="home-4", prefs={"easy_mode": True}
    )
    assert listed(easy) == {"Garden tour", "Skate tricks"}
    service.update_channel(sc, sam, {"restricted": True})
    assert listed(service.viewer_of(service.channel(sc, sam["id"]))) == {
        "Garden tour",
        "Skate tricks",
    }
    for viewer, video in ((teo, mature), (mia, teen), (sam, private)):
        with pytest.raises(LookupError):
            service.visible_video(sc, viewer, video["id"])
    assert service.visible_video(sc, sam, unlisted["id"])["title"] == "Family only"
    assert service.visible_video(sc, pat, private["id"])["title"] == "Diary"
    assert service.search(sc, sam, "family")["videos"] == []  # unlisted is never listed
    assert [v["title"] for v in service.search(sc, mia, "garden")["videos"]] == [
        "Garden tour"
    ]
    assert {v["title"] for v in service.channel_page(sc, sam, pat["id"])["videos"]} == {
        "Garden tour",
        "Skate tricks",
        "War film",
    }
    assert len(service.channel_page(sc, pat, pat["id"])["videos"]) == 5
    del everyone


def test_children_upload_privately_and_do_not_comment_or_earn(sc, people):
    pat, mia, _teo, sam = people
    drawing = _video(sc, mia, title="My drawing", visibility="public")
    assert drawing["visibility"] == "private"
    with pytest.raises(LookupError):
        service.channel_page(
            sc, sam, mia["id"]
        )  # children's channels are not shown to others
    video = _video(sc, pat, title="Story time")
    with pytest.raises(PermissionError):
        service.comment(sc, mia, video["id"], {"text": "Nice"})
    with pytest.raises(PermissionError):
        service.update_channel(sc, mia, {"payout_details": "bank"})


def test_views_count_once_a_day_after_a_real_watch_and_never_for_the_uploader(
    sc, people, monkeypatch
):
    clock = Clock(monkeypatch)
    pat, _mia, teo, sam = people
    video = _video(sc, pat, seconds=600)
    first = service.progress(sc, sam, video["id"], {"position": 1, "watched": 1})
    assert first == {"counted": False, "views": 0}
    clock.advance(seconds=5)
    # a browser claiming 40 seconds after 5 has not watched 40 seconds
    assert (
        service.progress(sc, sam, video["id"], {"position": 40, "watched": 40})[
            "counted"
        ]
        is False
    )
    clock.advance(seconds=26)
    assert service.progress(sc, sam, video["id"], {"position": 31, "watched": 31}) == {
        "counted": True,
        "views": 1,
    }
    clock.advance(seconds=30)
    assert (
        service.progress(sc, sam, video["id"], {"position": 61, "watched": 61})[
            "counted"
        ]
        is False
    )
    service.progress(sc, pat, video["id"], {"position": 1, "watched": 1})
    clock.advance(seconds=40)
    assert (
        service.progress(sc, pat, video["id"], {"position": 41, "watched": 41})[
            "counted"
        ]
        is False
    )
    clock.advance(days=1)
    service.progress(sc, sam, video["id"], {"position": 1, "watched": 1})
    clock.advance(seconds=31)
    assert service.progress(sc, sam, video["id"], {"position": 32, "watched": 32}) == {
        "counted": True,
        "views": 2,
    }
    # a short video counts after 30% of it
    short = _video(sc, pat, seconds=10)
    service.progress(sc, teo, short["id"], {"position": 0, "watched": 0})
    clock.advance(seconds=3)
    assert (
        service.progress(sc, teo, short["id"], {"position": 3, "watched": 3})["counted"]
        is True
    )
    assert [v["id"] for v in service.history(sc, sam)] == [video["id"]]
    service.clear_history(sc, sam)
    assert service.history(sc, sam) == []


def test_likes_subscriptions_watch_later_playlists_and_comments(sc, people):
    pat, _mia, teo, sam = people
    video = _video(sc, pat, title="Bread")
    assert service.like(sc, sam, video["id"], True) == {"liked": True, "likes": 1}
    assert service.like(sc, sam, video["id"], True)["likes"] == 1
    assert service.like(sc, sam, video["id"], False)["likes"] == 0
    with pytest.raises(ValueError):
        service.subscribe(sc, pat, pat["id"], True)
    assert service.subscribe(sc, sam, pat["id"], True) == {
        "subscribed": True,
        "subscribers": 1,
    }
    assert [v["title"] for v in service.home(sc, sam)["subscriptions"]] == ["Bread"]
    assert service.notifications(sc, pat)["unread"] == 1
    service.later(sc, sam, video["id"], True)
    assert [v["id"] for v in service.later_list(sc, sam)] == [video["id"]]
    playlist = service.create_playlist(sc, sam, {"name": "Baking"})
    playlist = service.playlist_item(sc, sam, playlist["id"], video["id"], True)
    assert playlist["count"] == 1
    with pytest.raises(LookupError):
        service.playlist(sc, teo, playlist["id"])  # playlists are their owner's
    store.set_setting(sc, "blocklist", ["darn"])
    added = service.comment(sc, sam, video["id"], {"text": "Darn good bread"})[
        "comments"
    ]
    assert added[0]["text"] == "**** good bread"
    comment_id = added[0]["id"]
    with pytest.raises(PermissionError):
        service.delete_comment(sc, teo, comment_id)
    service.delete_comment(
        sc, pat, comment_id
    )  # the video's channel may remove comments on it
    assert service.comments(sc, sam, video["id"]) == []
    assert (
        store.row(sc, "SELECT comments FROM vi_videos WHERE id = ?", (video["id"],))[
            "comments"
        ]
        == 0
    )


def test_popular_follows_the_published_formula(sc, people, monkeypatch):
    clock = Clock(monkeypatch)
    pat, _mia, _teo, sam = people
    old = _video(sc, pat, title="Old favourite")
    clock.advance(hours=48)
    new = _video(sc, pat, title="Fresh")
    old_row = store.row(sc, "SELECT * FROM vi_videos WHERE id = ?", (old["id"],))
    for n in range(30):
        service.count_view(sc, old_row, f"viewer-{n}")
    sc.commit()
    # 31 / (48 + 2)^1.2 = 0.28 is less than 1 / (0 + 2)^1.2 = 0.44: two days old and little watched loses
    assert [v["title"] for v in service.home(sc, sam)["popular"]] == [
        "Fresh",
        "Old favourite",
    ]
    for n in range(30, 100):
        service.count_view(sc, old_row, f"viewer-{n}")
    sc.commit()
    popular = [v["title"] for v in service.home(sc, sam)["popular"]]
    assert popular == ["Old favourite", "Fresh"]  # 101 / 109 = 0.93
    assert "views in the last 7 days" in service.home(sc, sam)["ranking"]
    del new


def test_reports_and_moderation_stay_in_the_household_scope(sc, people):
    pat, mia, teo, sam = people
    video = _video(sc, sam, title="Rude prank")
    service.report(
        sc,
        teo,
        {
            "target_type": "video",
            "target_id": video["id"],
            "reason": "Hateful or bullying",
        },
    )
    assert service.report(
        sc, teo, {"target_type": "video", "target_id": video["id"]}
    ) == {"reported": True}
    assert len(service.reports(sc)) == 1
    assert (
        service.reports(sc, home="home-1") == []
    )  # neither reporter nor uploader is Pat's household
    report_id = service.reports(sc, home="home-2")[0]["id"]
    with pytest.raises(PermissionError):
        service.moderate(sc, "teo-home", report_id, "suspend", home="home-2")
    service.moderate(sc, "teo-home", report_id, "hide", home="home-2")
    with pytest.raises(LookupError):
        service.visible_video(sc, pat, video["id"])
    mine = service.visible_video(sc, sam, video["id"])
    assert service.video_view(sc, sam, mine)["blocked"] == "hidden"
    service.report(sc, pat, {"target_type": "channel", "target_id": sam["id"]})
    platform_report = service.reports(sc)[0]["id"]
    service.moderate(
        sc, "aira", platform_report, "suspend"
    )  # the platform suspends anyone
    assert service.channel(sc, sam["id"])["suspended_at"]
    with pytest.raises(service.ChannelSuspended):
        _video(sc, service.viewer_of(service.channel(sc, sam["id"])), title="Back")
    service.set_suspended(sc, sam["id"], False)
    del mia


def test_copyright_notice_takedown_counter_notice_and_strikes(sc, people):
    pat, _mia, _teo, sam = people
    video = _video(sc, sam, title="Copied song")
    with pytest.raises(ValueError, match="Confirm both"):
        service.file_notice(
            sc,
            {
                "video": video["id"],
                "name": "Rita",
                "email": "r@label.test",
                "work": "My song",
                "signature": "Rita",
            },
            "1.2.3.4",
        )
    filed = service.file_notice(
        sc,
        {
            "video": f"http://127.0.0.1:8765/#/videos?watch={video['id']}",
            "name": "Rita Rights",
            "email": "rita@label.test",
            "work": "The song Blue Sky (2024)",
            "signature": "Rita Rights",
            "good_faith": True,
            "accurate": True,
        },
        "1.2.3.4",
    )["notice"]
    assert filed["status"] == "received"
    with pytest.raises(ValueError, match="could not find"):
        service.file_notice(
            sc,
            {
                "video": "f" * 32,
                "name": "x",
                "email": "x@y.zz",
                "work": "w",
                "signature": "x",
                "good_faith": 1,
                "accurate": 1,
            },
            "1.2.3.4",
        )
    taken = service.decide_notice(sc, filed["id"], "takedown", "aira:mod@aira.test")
    assert taken["status"] == "taken_down" and service.strikes(sc, sam["id"]) == 1
    with pytest.raises(LookupError):
        service.visible_video(sc, pat, video["id"])
    assert service.notifications(sc, sam)["notifications"][0]["kind"] == "copyright"
    mine = service.my_notices(sc, sam)
    assert mine[0]["status"] == "taken_down" and "claimant_email" not in mine[0]
    with pytest.raises(ValueError, match="Confirm"):
        service.counter_notice(
            sc, sam, filed["id"], {"name": "Sam", "statement": "I wrote it"}
        )
    countered = service.counter_notice(
        sc,
        sam,
        filed["id"],
        {"name": "Sam Song", "statement": "I wrote and recorded it.", "sworn": True},
    )
    assert countered["status"] == "counter_filed"
    restored = service.decide_notice(
        sc, filed["id"], "restore", "aira:mod@aira.test", "Their own recording"
    )
    assert restored["status"] == "restored" and service.strikes(sc, sam["id"]) == 0
    assert service.visible_video(sc, pat, video["id"])["id"] == video["id"]
    # three standing strikes stop uploads
    for n in range(3):
        other = _video(sc, sam, title=f"Copy {n}")
        notice = service.file_notice(
            sc,
            {
                "video": other["id"],
                "name": "Rita",
                "email": "rita@label.test",
                "work": "Songs",
                "signature": "Rita",
                "good_faith": 1,
                "accurate": 1,
            },
            f"10.0.0.{n}",
        )["notice"]
        service.decide_notice(sc, notice["id"], "takedown", "aira")
    with pytest.raises(PermissionError, match="three copyright strikes"):
        _video(sc, sam, title="One more")


def test_copyright_notices_are_rate_limited_per_sender(sc, people):
    video = _video(sc, people[3])
    raw = {
        "video": video["id"],
        "name": "R",
        "email": "r@x.test",
        "work": "w",
        "signature": "R",
        "good_faith": 1,
        "accurate": 1,
    }
    for _ in range(10):
        service.file_notice(sc, raw, "5.5.5.5")
    with pytest.raises(PermissionError):
        service.file_notice(sc, raw, "5.5.5.5")
    assert service.file_notice(sc, raw, "6.6.6.6")["notice"]["status"] == "received"


def test_erasing_a_channel_removes_its_videos_files_and_words(sc, people):
    pat, _mia, _teo, sam = people
    video = _video(sc, sam, title="Mine")
    path = media.root() / media.file_row(sc, reel_file(sc, video))["path"]
    service.comment(sc, sam, _video(sc, pat, title="Pat's")["id"], {"text": "Hello"})
    service.subscribe(sc, sam, pat["id"], True)
    exported = service.export_channel(sc, sam["id"])
    assert (
        exported["videos"][0]["title"] == "Mine"
        and exported["comments"][0]["text"] == "Hello"
    )
    assert "person_key" not in exported["channel"]
    assert service.channels_for(sc, home="home-3") == [sam["id"]]
    service.erase_channels(sc, [sam["id"]])
    assert not path.exists()
    assert (
        store.scalar(
            sc, "SELECT COUNT(*) FROM vi_comments WHERE author_id = ?", (sam["id"],)
        )
        == 0
    )
    assert (
        store.scalar(sc, "SELECT COUNT(*) FROM vi_channels WHERE id = ?", (sam["id"],))
        == 0
    )
    assert service.subscriber_count(sc, pat["id"]) == 0


# ---------------------------------------------------------------------------
# the single-user server
# ---------------------------------------------------------------------------


def _raw(env, api, path, headers=None):
    request = urllib.request.Request(env.base + path)
    for name, value in (headers or {}).items():
        request.add_header(name, value)
    try:
        with api.opener.open(request, timeout=30) as response:
            return response.status, dict(response.headers), response.read()
    except urllib.error.HTTPError as error:
        return error.code, dict(error.headers), error.read()


def _anonymous(env, method, path, body=None, headers=None):
    data = json.dumps(body).encode() if body is not None else None
    request = urllib.request.Request(env.base + path, data=data, method=method)
    for name, value in {
        "Content-Type": "application/json",
        "X-Requested-With": "aira",
        "Origin": env.base,
        **(headers or {}),
    }.items():
        request.add_header(name, value)
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            raw = response.read()
            return response.status, dict(response.headers), raw
    except urllib.error.HTTPError as error:
        return error.code, dict(error.headers), error.read()


def test_a_household_uploads_watches_shares_and_reports_on_this_computer(
    env, api, monkeypatch
):
    kit.forget_schema()
    db.forget()
    import auto_apply_assistant as local

    public.set_index(local._public_link_index)
    clock = Clock(monkeypatch)
    household = Household(env, api)
    pat = household.owner
    mia = household.add("Mia", age_group="child", pin="2468")
    me = pat.get("/api/videos/me")
    assert (
        me["moderator"] is True
        and me["operator"] is True
        and me["channel"]["name"] == pat.name
    )
    assert me["limits"]["chunk_bytes"] == media.CHUNK_BYTES
    data = make_mp4(75, 720, 1280, payload=30_000)
    file_id = upload(pat, "video", data, chunk=7000)
    thumb = upload(pat, "image", PNG)
    captions = upload(pat, "captions", VTT)
    status, created = pat.call(
        "POST",
        "/api/videos/videos",
        {
            "file_id": file_id,
            "thumb_file_id": thumb,
            "captions_file_id": captions,
            "kind": "reel",
            "title": "Sunrise",
            "category": "travel",
            "tags": "sun, sky",
        },
    )
    assert status == 201, created
    video = created["video"]
    assert (
        video["kind"] == "reel"
        and video["tags"] == ["sun", "sky"]
        and video["duration"] == 75
    )
    # the player asks for ranges of the signed address; a wrong signature is a 404
    status, headers, body = _raw(
        env, api, video["stream_url"], {"Range": "bytes=10-109"}
    )
    assert status == 206 and body == data[10:110]
    assert (
        headers["Content-Type"] == "video/mp4" and headers["Accept-Ranges"] == "bytes"
    )
    assert headers["X-Content-Type-Options"] == "nosniff"
    assert _raw(env, api, video["stream_url"][:-4] + "0000")[0] == 404
    assert _raw(env, api, video["captions_url"])[2] == VTT
    # Mia sees it (rated for everyone) in Reels, cannot comment; nobody's own view counts
    assert [r["id"] for r in mia.get("/api/videos/reels")["reels"]] == [video["id"]]
    assert (
        mia.call("POST", f"/api/videos/videos/{video['id']}/comments", {"text": "hi"})[
            0
        ]
        == 403
    )
    mia.post(
        f"/api/videos/videos/{video['id']}/progress", {"position": 0, "watched": 0}
    )
    clock.advance(seconds=31)
    assert (
        mia.post(
            f"/api/videos/videos/{video['id']}/progress",
            {"position": 31, "watched": 31},
        )["counted"]
        is True
    )
    # Pat shares it; a visitor without an account watches it and their view counts once
    link = pat.post(f"/api/videos/videos/{video['id']}/link", {}, expect=201)["link"]
    assert mia.call("POST", f"/api/videos/videos/{video['id']}/link", {})[0] == 404
    status, _headers, raw = _anonymous(env, "GET", f"/api/public/{link['token']}")
    page = json.loads(raw)
    assert status == 200 and page["page"]["video"]["title"] == "Sunrise"
    status, headers, body = _anonymous(
        env, "GET", page["page"]["video"]["stream_url"], headers={"Range": "bytes=0-99"}
    )
    assert status == 206 and body == data[:100]
    other_file = upload(pat, "video", make_mp4())
    assert (
        _anonymous(env, "GET", f"/api/public/{link['token']}/media/{other_file}")[0]
        == 404
    )
    status, _headers, raw = _anonymous(
        env, "POST", f"/api/public/{link['token']}/watched", {"watched": 40}
    )
    assert status == 200 and json.loads(raw) == {"counted": True}
    assert json.loads(
        _anonymous(
            env, "POST", f"/api/public/{link['token']}/watched", {"watched": 40}
        )[2]
    ) == {"counted": False}
    assert pat.get(f"/api/videos/videos/{video['id']}")["video"]["views"] == 2
    # making it private turns the link off
    pat.ok("PATCH", f"/api/videos/videos/{video['id']}", {"visibility": "private"})
    assert _anonymous(env, "GET", f"/api/public/{link['token']}")[0] == 404
    pat.ok("PATCH", f"/api/videos/videos/{video['id']}", {"visibility": "public"})
    # the public copyright form needs no account; the computer's owner takes the video down
    status, _headers, html = _anonymous(env, "GET", "/videos/copyright")
    assert status == 200 and b"videos-copyright.js" in html
    status, _headers, raw = _anonymous(
        env,
        "POST",
        "/api/videos/copyright",
        {
            "video": video["id"],
            "name": "Rita",
            "email": "rita@label.test",
            "work": "My film",
            "signature": "Rita",
            "good_faith": True,
            "accurate": True,
        },
    )
    assert status == 201, raw
    queue = pat.get("/api/videos/moderation")
    assert queue["everyone"] is True and queue["notices"][0]["status"] == "received"
    pat.post(
        f"/api/videos/moderation/notices/{queue['notices'][0]['id']}",
        {"action": "takedown"},
    )
    assert mia.call("GET", f"/api/videos/videos/{video['id']}")[0] == 404
    assert mia.call("GET", "/api/videos/moderation")[0] == 403
    studio = pat.get("/api/videos/studio")
    assert studio["strikes"] == 1 and studio["videos"][0]["blocked"] == "taken_down"
    # deleting removes the file
    pat.ok("DELETE", f"/api/videos/videos/{video['id']}")
    assert _raw(env, api, video["stream_url"])[0] == 404
    today = pat.get("/api/life/today")
    assert "videos" in today["apps"]


def test_a_second_account_on_this_computer_is_not_its_owner(env, api):
    kit.forget_schema()
    db.forget()
    first = Household(env, api).owner
    assert first.get("/api/videos/me")["operator"] is True
    second = env.api()
    status, _ = second.call(
        "POST",
        "/api/auth/register",
        {"email": "sam@example.com", "password": test_careers.PASSWORD},
    )
    assert status == 201
    sam = Household(env, second).owner
    me = sam.get("/api/videos/me")
    assert me["moderator"] is True and me["operator"] is False
    assert sam.get("/api/videos/moderation")["everyone"] is False
    video_id = sam.post(
        "/api/videos/videos",
        {
            "file_id": upload(sam, "video", make_mp4()),
            "title": "From the second account",
        },
        expect=201,
    )["video"]["id"]
    assert (
        first.get(f"/api/videos/videos/{video_id}")["video"]["channel"]["name"]
        == sam.name
    )  # one catalog
    assert sam.call("GET", "/api/ads/admin")[0] == 403


# ---------------------------------------------------------------------------
# the hosted build
# ---------------------------------------------------------------------------


def _hosted_upload(browser, kind, data):
    return upload(browser, kind, data)


def test_hosted_members_of_two_organizations_share_one_catalog(cloud):
    kit.forget_schema()
    db.forget()
    assert db.path() == cloud.config.data_root / "videos.sqlite3"
    assert media.root() == cloud.config.data_root / "media"
    acme = build_org(cloud)
    owner = sign_in(cloud, acme.owner)
    status, data = owner.call("GET", "/api/videos/me")
    assert status == 402 and data["code"] == "app_required"
    cloud.billing.subscribe_app(acme.owner, "videos")
    candidate = sign_in(cloud, acme.candidate)
    me = candidate.call("GET", "/api/videos/me")[1]
    assert me["moderator"] is False and me["hosted"] is True
    file_id = _hosted_upload(candidate, "video", make_mp4())
    status, created = candidate.call(
        "POST", "/api/videos/videos", {"file_id": file_id, "title": "Acme talk"}
    )
    assert status == 201, created
    video = created["video"]
    status, body = candidate.call(
        "GET", video["stream_url"], None, raw=True, headers={"Range": "bytes=0-9"}
    )
    assert status == 206 and body == make_mp4()[:10]
    other = build_org(cloud, "Other Org", slug_email="other.test")
    stranger = sign_in(cloud, other.owner)
    assert stranger.call("GET", f"/api/videos/videos/{video['id']}")[0] == 402
    cloud.billing.subscribe_app(other.owner, "videos")
    status, seen = stranger.call("GET", f"/api/videos/videos/{video['id']}")
    assert status == 200 and seen["video"]["title"] == "Acme talk"
    assert (
        stranger.call(
            "POST",
            "/api/videos/reports",
            {
                "target_type": "video",
                "target_id": video["id"],
                "reason": "Spam or a scam",
            },
        )[0]
        == 201
    )
    # moderation is the platform's, not an organization owner's
    assert owner.call("GET", "/api/videos/moderation")[0] == 403
    assert owner.call("GET", "/api/ads/admin")[0] in (402, 403)
    # a member's public link works for an anonymous visitor
    status, made = candidate.call("POST", f"/api/videos/videos/{video['id']}/link", {})
    assert status == 201, made
    from cloud_support import Browser

    visitor = Browser(cloud)
    status, page = visitor.call("GET", f"/api/public/{made['link']['token']}")
    assert status == 200 and page["page"]["video"]["title"] == "Acme talk"
    status, body = visitor.call(
        "GET", page["page"]["video"]["stream_url"], None, raw=True
    )
    assert status == 200 and body == make_mp4()
    # removing the member erases their channel and files
    path = media.root() / media.file_row(db.connect(), file_id)["path"]
    cloud.tenancy.remove_member(acme.owner, acme.candidate.member_id)
    assert not path.exists()
    assert stranger.call("GET", f"/api/videos/videos/{video['id']}")[0] == 404


def test_hosted_uploads_count_against_the_organizations_storage(cloud, monkeypatch):
    kit.forget_schema()
    db.forget()
    acme = build_org(cloud, plan="starter")
    cloud.billing.subscribe_app(acme.owner, "videos")
    owner = sign_in(cloud, acme.owner)
    limit = cloud.entitlements.resolve(acme.org_id).limit("storage_bytes")
    monkeypatch.setenv("AIRA_VIDEO_MAX_BYTES", str(limit * 2))
    status, refused = owner.call(
        "POST",
        "/api/videos/uploads",
        {"kind": "video", "filename": "big.mp4", "size": limit + 1},
    )
    assert (
        status == 402
        and refused["code"] == "plan_required"
        and refused["limit"] == "storage_bytes"
    )
    assert (
        owner.call(
            "POST",
            "/api/videos/uploads",
            {"kind": "video", "filename": "ok.mp4", "size": 1000},
        )[0]
        == 201
    )


def test_hosted_platform_console_moderates_videos_and_copyright(cloud):
    from test_cloud_ui_billing import operator_browser

    kit.forget_schema()
    db.forget()
    acme = build_org(cloud)
    cloud.billing.subscribe_app(acme.owner, "videos")
    member = sign_in(cloud, acme.candidate)
    video = member.call(
        "POST",
        "/api/videos/videos",
        {"file_id": upload(member, "video", make_mp4()), "title": "Prank"},
    )[1]["video"]
    coach = sign_in(cloud, acme.coach)
    coach.call(
        "POST",
        "/api/videos/reports",
        {
            "target_type": "video",
            "target_id": video["id"],
            "reason": "Hateful or bullying",
        },
    )
    operator = operator_browser(cloud)
    status, queue = operator.call("GET", "/api/platform/videos/reports")
    assert status == 200 and queue["reports"][0]["preview"] == "Prank"
    status, result = operator.call(
        "POST",
        f"/api/platform/videos/reports/{queue['reports'][0]['id']}",
        {"action": "hide"},
    )
    assert status == 200 and result == {"action": "hide"}
    assert coach.call("GET", f"/api/videos/videos/{video['id']}")[0] == 404
    assert (
        operator.call(
            "POST", f"/api/platform/videos/videos/{video['id']}", {"action": "restore"}
        )[0]
        == 200
    )
    assert coach.call("GET", f"/api/videos/videos/{video['id']}")[0] == 200
    from cloud_support import Browser

    anyone = Browser(cloud)
    status, _ = anyone.call(
        "POST",
        "/api/videos/copyright",
        {
            "video": video["id"],
            "name": "Rita",
            "email": "rita@label.test",
            "work": "My clip",
            "signature": "Rita",
            "good_faith": True,
            "accurate": True,
        },
    )
    assert status == 201
    notice = operator.call("GET", "/api/platform/videos/copyright")[1]["notices"][0]
    assert (
        operator.call(
            "POST",
            f"/api/platform/videos/copyright/{notice['id']}",
            {"action": "takedown"},
        )[1]["notice"]["status"]
        == "taken_down"
    )
    channel_id = video["channel"]["id"]
    assert operator.call(
        "POST",
        f"/api/platform/videos/channels/{channel_id}/suspend",
        {"suspended": True},
    )[1] == {"suspended": True}
    actions = [row["action"] for row in cloud.platform_audit.list(limit=50)]
    assert {
        "videos.moderate",
        "videos.restore",
        "videos.copyright_taken_down",
        "videos.suspend",
    } <= set(actions)
