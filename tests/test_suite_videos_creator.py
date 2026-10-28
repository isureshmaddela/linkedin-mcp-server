"""Videos: the creator's analytics (views, watch time, best and lowest earners, CSV), support from viewers (pledges paid
on the creator's own page, marked received into the Business hub), carrying on where you stopped, chapters, Today, and
the bugs found using the app (watch time counted twice, search wildcards), in both builds."""

# ruff: noqa: F811
from __future__ import annotations

import csv
import io
import json

import pytest
from cloud_support import build_org, sign_in
from life_support import Household
from test_suite_videos import (  # noqa: F401  (fixtures used by name)
    _anonymous,
    _no_model,
    _person,
    _video,
    people,
    sc,
)
from videos_support import Clock, make_mp4, upload

import test_careers
from aira.suite import kit, public
from aira.suite.videos import creator, db, service, store

env = test_careers.env
api = test_careers.api
stub = test_careers.stub


def _watch(sc, viewer, video_id, clock, seconds, *, start=0.0):
    """``viewer`` opens the video and watches ``seconds`` of it, reporting every 10 seconds like the page does."""
    service.progress(sc, viewer, video_id, {"position": start, "watched": 0})
    done = 0.0
    while done < seconds:
        step = min(10.0, seconds - done)
        clock.advance(seconds=step)
        done += step
        service.progress(
            sc, viewer, video_id, {"position": start + done, "watched": done}
        )


def _adult_creator(sc, ch):
    sc.execute(
        "UPDATE vi_channels SET terms_accepted_at = ? WHERE id = ?",
        (store.stamp(), ch["id"]),
    )
    sc.commit()
    return service.viewer_of(service.channel(sc, ch["id"]))


def _impression(sc, video, micros, *, clicked=0, day=""):
    when = day or store.day()
    sc.execute(
        """INSERT INTO ad_impressions (id, campaign_id, creative_id, home, slot, video_id, channel_id, viewer_key, served_at,
        status, cost_micros, creator_micros, earning_status, click_counted, day, month)
        VALUES (?, 'c', 'k', 'h', 'preroll', ?, ?, 'p:x', ?, 'counted', ?, ?, 'estimated', ?, ?, ?)""",
        (
            store.new_id(),
            video["id"],
            video["channel"]["id"],
            store.stamp(),
            micros * 2,
            micros,
            clicked,
            when,
            when[:7],
        ),
    )
    sc.commit()


# ---------------------------------------------------------------------------
# watching: carry on, chapters, the watch-time log
# ---------------------------------------------------------------------------


def _logged(sc, video_id):
    return store.scalar(
        sc,
        "SELECT COALESCE(SUM(seconds), 0) FROM vi_watch_days WHERE video_id = ?",
        (video_id,),
    )


def test_watch_time_is_counted_once_when_the_page_is_opened_again(
    sc, people, monkeypatch
):
    """Each page reports the time watched since it opened. Opening the video again counts only the new watching."""
    clock = Clock(monkeypatch)
    pat, _mia, _teo, sam = people
    video = _video(sc, pat, seconds=120)
    _watch(sc, sam, video["id"], clock, 30)
    clock.advance(minutes=5)
    _watch(
        sc, sam, video["id"], clock, 20, start=30
    )  # the page opened again: its count starts from 0
    assert _logged(sc, video["id"]) == pytest.approx(50, abs=0.01)
    # the uploader's own watching is not watch time
    _watch(sc, pat, video["id"], clock, 30)
    assert _logged(sc, video["id"]) == pytest.approx(50, abs=0.01)


def test_watch_time_is_not_counted_twice_from_two_open_pages(sc, people, monkeypatch):
    """Regression: with the same video open twice, the second page's small count replaced the stored total, so the
    first page's next report counted everything it had already reported a second time (40 seconds became 75)."""
    clock = Clock(monkeypatch)
    pat, _mia, _teo, sam = people
    video = _video(sc, pat, seconds=300)
    service.progress(sc, sam, video["id"], {"position": 0, "watched": 0})
    clock.advance(seconds=30)
    service.progress(
        sc, sam, video["id"], {"position": 30, "watched": 30}
    )  # the first page
    clock.advance(seconds=5)
    service.progress(
        sc, sam, video["id"], {"position": 5, "watched": 5}
    )  # a second page, opened 5 seconds ago
    clock.advance(seconds=5)
    service.progress(
        sc, sam, video["id"], {"position": 40, "watched": 40}
    )  # the first page again
    assert (
        _logged(sc, video["id"]) <= 30 + 5 + 6
    )  # never more than the time that passed
    # a page cannot claim more than the time since its last report
    clock.advance(seconds=10)
    service.progress(sc, sam, video["id"], {"position": 60, "watched": 600})
    assert _logged(sc, video["id"]) <= 30 + 5 + 6 + 11


def test_carry_on_where_you_stopped(sc, people, monkeypatch):
    """Regression: the Library promised to carry on where you stopped, but the player always started at 0."""
    clock = Clock(monkeypatch)
    pat, _mia, _teo, sam = people
    video = _video(sc, pat, seconds=120, title="Long talk")
    reel = _video(sc, pat, seconds=40, size=(720, 1280), kind="reel")
    assert (
        service.video_view(sc, sam, service._video_row(sc, video["id"]), full=True)[
            "position"
        ]
        == 0
    )
    _watch(sc, sam, video["id"], clock, 40)
    _watch(sc, sam, reel["id"], clock, 20)
    view = service.video_view(sc, sam, service._video_row(sc, video["id"]), full=True)
    assert view["position"] == 40
    shelf = service.continue_watching(sc, sam)
    assert [item["id"] for item in shelf] == [
        video["id"]
    ]  # reels loop: they are not "continued"
    assert shelf[0]["position"] == 40
    # nearly at the end counts as finished; clearing it from history forgets the place
    service.progress(sc, sam, video["id"], {"position": 118, "watched": 40})
    assert service.continue_watching(sc, sam) == []
    service.progress(sc, sam, video["id"], {"position": 50, "watched": 40})
    service.clear_history(sc, sam, video["id"])
    assert (
        service.video_view(sc, sam, service._video_row(sc, video["id"]), full=True)[
            "position"
        ]
        == 0
    )
    # Pat's own place in someone else's video is Pat's alone
    assert service.continue_watching(sc, pat) == []


@pytest.mark.parametrize(
    ("description", "duration", "expected"),
    [
        (
            "0:00 Intro\n1:05 The recipe\n12:30 - Tasting",
            900,
            [(0, "Intro"), (65, "The recipe"), (750, "Tasting")],
        ),
        (
            "Watch this!\n00:00 Start\n1:00:05 Late part",
            4000,
            [(0, "Start"), (3605, "Late part")],
        ),
        ("0:00 Only one", 100, []),
        ("0:10 Not from the start\n0:20 Later", 100, []),
        ("0:00 A\n0:30 B\n0:20 Going back", 100, []),
        ("0:00 A\n0:30 B\n5:00 After the end", 100, [(0, "A"), (30, "B")]),
        ("0:00 A\n0:75 Not a time\n1:00 B", 100, [(0, "A"), (60, "B")]),
        ("", 0, []),
    ],
)
def test_chapters_are_read_from_the_description(description, duration, expected):
    assert [
        (item["at"], item["title"]) for item in service.chapters(description, duration)
    ] == expected


def test_search_treats_percent_and_underscore_as_plain_characters(sc, people):
    """Regression: a search for "_" or "%" was a wildcard and listed every video."""
    pat, _mia, _teo, sam = people
    _video(sc, pat, title="Plain title")
    _video(sc, pat, title="100% fun")
    _video(sc, pat, title="snake_case explained")
    assert [item["title"] for item in service.search(sc, sam, "%")["videos"]] == [
        "100% fun"
    ]
    assert [item["title"] for item in service.search(sc, sam, "_")["videos"]] == [
        "snake_case explained"
    ]
    assert service.search(sc, sam, "!")["videos"] == []
    assert service.search(sc, sam, "%", "reel")["videos"] == []


# ---------------------------------------------------------------------------
# analytics
# ---------------------------------------------------------------------------


def test_analytics_totals_days_best_and_lowest_earners_and_csv(sc, people, monkeypatch):
    clock = Clock(monkeypatch)
    pat, _mia, teo, sam = people
    best = _video(sc, pat, seconds=100, title="=Best video")
    low = _video(sc, pat, seconds=100, title="Quiet video")
    _video(sc, pat, seconds=100, title="Just for me", visibility="private")
    _watch(sc, sam, best["id"], clock, 50)
    _watch(sc, teo, best["id"], clock, 30)
    _watch(sc, sam, low["id"], clock, 40)
    service.like(sc, sam, best["id"], True)
    service.like(sc, pat, best["id"], True)  # the creator's own like is not counted
    service.comment(sc, sam, best["id"], {"text": "Great"})
    service.subscribe(sc, sam, pat["id"], True)
    _impression(sc, best, 40_000, clicked=1)
    _impression(sc, best, 20_000)
    _impression(sc, low, 5_000)
    service.count_view(
        sc, service._video_row(sc, low["id"]), service.visitor_key("1.2.3.4", "ua")
    )
    sc.commit()
    data = creator.analytics(sc, pat, 28)
    t = data["totals"]
    assert (
        t["views"],
        t["public_views"],
        t["likes"],
        t["comments"],
        t["new_subscribers"],
    ) == (4, 1, 1, 1, 1)
    assert t["watch_seconds"] == pytest.approx(120, abs=0.1)
    assert t["average_seconds"] == pytest.approx(30, abs=0.1)
    assert t["earned_micros"] == 65_000 and t["rpm_micros"] == 65_000 * 1000 // 4
    assert (
        len(data["daily"]) == 28
        and data["daily"][-1]["views"] == 4
        and data["daily"][-1]["earned_micros"] == 65_000
    )
    by_id = {item["id"]: item for item in data["videos"]}
    assert data["videos"][0]["id"] == best["id"]  # best earner first
    assert (
        by_id[best["id"]]["views"] == 2 and by_id[best["id"]]["average_percent"] == 40.0
    )
    assert by_id[best["id"]]["impressions"] == 2 and by_id[best["id"]]["clicks"] == 1
    assert data["top_earner"] == best["id"] and data["lowest_earner"] == low["id"]
    assert data["most_watched"] == best["id"]
    # the period before is there to compare with; other channels' numbers are not
    clock.advance(days=30)
    later = creator.analytics(sc, pat, 28)
    assert later["totals"]["views"] == 0 and later["totals"]["previous_views"] == 4
    assert later["top_earner"] is None and later["lowest_earner"] is None
    assert creator.analytics(sc, sam, 28)["videos"] == []
    with pytest.raises(ValueError):
        creator.analytics(sc, pat, 3)
    # the CSV opens in a spreadsheet; a title starting with "=" is not run as a formula
    rows = list(
        csv.reader(io.StringIO(creator.analytics_csv(sc, pat, 90).decode("utf-8-sig")))
    )
    assert rows[0][:4] == ["Video", "Kind", "Uploaded", "Views"]
    assert rows[1][0] == "'=Best video" and rows[1][3] == "2" and rows[1][11] == "0.06"


# ---------------------------------------------------------------------------
# support from viewers
# ---------------------------------------------------------------------------


def test_support_is_set_up_by_adults_who_accepted_the_terms(sc, people):
    pat, mia, teo, _sam = people
    with pytest.raises(PermissionError):
        creator.save_support(
            sc, pat, {"enabled": True, "link": "https://pay.example/pat"}
        )  # terms first
    pat = _adult_creator(sc, pat)
    with pytest.raises(ValueError, match="https"):
        creator.save_support(
            sc, pat, {"enabled": True, "link": "http://pay.example/pat"}
        )
    with pytest.raises(ValueError, match="UPI"):
        creator.save_support(sc, pat, {"enabled": True, "upi": "not a upi"})
    with pytest.raises(ValueError, match="payment page or your UPI"):
        creator.save_support(sc, pat, {"enabled": True})
    with pytest.raises(ValueError):
        creator.save_support(
            sc, pat, {"enabled": True, "upi": "pat@okbank", "currency": "XYZ"}
        )
    saved = creator.save_support(
        sc,
        pat,
        {"enabled": True, "upi": "pat@okbank", "currency": "inr", "note": "Thanks!"},
    )
    assert saved["enabled"] and saved["currency"] == "INR" and saved["can_enable"]
    for young in (mia, teo):
        with pytest.raises(PermissionError):
            creator.save_support(sc, young, {"enabled": True, "upi": "kid@okbank"})
        assert creator.support_settings(sc, young)["can_enable"] is False


def test_a_pledge_is_paid_outside_aira_and_marked_received_by_the_creator(sc, people):
    pat, mia, teo, sam = people
    video = _video(sc, pat, title="Cooking")
    assert creator.support_for_viewer(sc, sam, pat["id"]) is None  # not turned on yet
    with pytest.raises(LookupError):
        creator.pledge(sc, sam, pat["id"], {"amount_cents": 500})
    pat = _adult_creator(sc, pat)
    creator.save_support(
        sc,
        pat,
        {"enabled": True, "link": "https://pay.example/pat", "upi": "pat@okbank"},
    )
    offer = creator.support_for_viewer(sc, sam, pat["id"])
    assert (
        offer["can_give"] is True
        and offer["currency"] == "USD"
        and offer["supporters"] == []
    )
    assert (
        creator.support_for_viewer(sc, pat, pat["id"]) is None
    )  # not on your own videos
    assert creator.support_for_viewer(sc, teo, pat["id"])["can_give"] is False
    # who may give, and how much
    for young in (mia, teo):
        with pytest.raises(PermissionError):
            creator.pledge(sc, young, pat["id"], {"amount_cents": 500})
    with pytest.raises(ValueError):
        creator.pledge(sc, pat, pat["id"], {"amount_cents": 500})
    for amount in (50, 100_001, "lots"):
        with pytest.raises(ValueError):
            creator.pledge(sc, sam, pat["id"], {"amount_cents": amount})
    result = creator.pledge(
        sc,
        sam,
        pat["id"],
        {"amount_cents": 500, "message": "Loved it, shit yes", "video_id": video["id"]},
    )
    tip = result["tip"]
    assert (
        tip["status"] == "pledged"
        and tip["amount"] == "$5.00"
        and tip["reference"].startswith("TIP-")
    )
    assert tip["message"] == "Loved it, **** yes" and tip["video"]["title"] == "Cooking"
    assert result["pay"] == {
        "link": "https://pay.example/pat",
        "upi": "pat@okbank",
        "name": "Pat",
        "note": "",
        # a phone opens a UPI app with the creator and the reference filled in (no amount: UPI pays in rupees)
        "upi_link": f"upi://pay?pa=pat@okbank&pn=Pat&tn={tip['reference']}",
    }
    assert (
        "sending you $5.00"
        in service.notifications(sc, pat)["notifications"][0]["text"]
    )
    assert creator.waiting_count(sc, pat["id"]) == 1
    # the creator marks it received: the supporter is thanked and named (they chose to be)
    received = creator.decide_tip(sc, pat, tip["id"], "received")
    assert received["status"] == "received" and received["supporter"]["name"] == "Sam"
    assert (
        "received your support"
        in service.notifications(sc, sam)["notifications"][0]["text"]
    )
    assert [
        item["name"]
        for item in creator.support_for_viewer(sc, sam, pat["id"])["supporters"]
    ] == ["Sam"]
    with pytest.raises(ValueError):
        creator.decide_tip(sc, pat, tip["id"], "received")
    with pytest.raises(ValueError):
        creator.cancel_tip(sc, sam, tip["id"])  # too late to withdraw
    # marked received by mistake: it can still be marked not received
    assert (
        creator.decide_tip(sc, pat, tip["id"], "not_received")["status"] == "declined"
    )
    assert creator.support_for_viewer(sc, sam, pat["id"])["supporters"] == []
    # an anonymous pledge, withdrawn before it was paid
    quiet = creator.pledge(
        sc, sam, pat["id"], {"amount_cents": 1000, "show_name": False}
    )["tip"]
    assert quiet["show_name"] is False
    with pytest.raises(LookupError):
        creator.cancel_tip(sc, pat, quiet["id"])  # only the supporter withdraws
    assert creator.cancel_tip(sc, sam, quiet["id"])["status"] == "cancelled"
    with pytest.raises(ValueError):
        creator.decide_tip(sc, pat, quiet["id"], "received")
    with pytest.raises(LookupError):
        creator.decide_tip(
            sc, service.viewer_of(service.channel(sc, sam["id"])), tip["id"], "received"
        )
    listing = creator.received_tips(sc, pat)
    assert [item["status"] for item in listing["tips"]] == [
        "cancelled",
        "declined",
    ] and listing["waiting"] == 0
    assert [item["status"] for item in creator.my_tips(sc, sam)] == [
        "cancelled",
        "declined",
    ]
    # the creator's analytics count support that arrived
    creator.decide_tip(
        sc,
        pat,
        creator.pledge(sc, sam, pat["id"], {"amount_cents": 700})["tip"]["id"],
        "received",
    )
    assert creator.analytics(sc, pat, 7)["totals"]["support"][0]["cents"] == 700
    # a suspended creator takes no support
    service.set_suspended(sc, pat["id"], True)
    assert creator.support_for_viewer(sc, sam, pat["id"]) is None


def test_pledges_are_limited_each_day(sc, people):
    pat, _mia, _teo, sam = people
    pat = _adult_creator(sc, pat)
    creator.save_support(sc, pat, {"enabled": True, "upi": "pat@okbank"})
    for _ in range(service.LIMITS["tip"][0]):
        creator.pledge(sc, sam, pat["id"], {"amount_cents": 100})
    with pytest.raises(PermissionError):
        creator.pledge(sc, sam, pat["id"], {"amount_cents": 100})


def test_export_and_erasure_cover_support_and_watch_time(sc, people, monkeypatch):
    clock = Clock(monkeypatch)
    pat, _mia, _teo, sam = people
    video = _video(sc, pat, seconds=100)
    _watch(sc, sam, video["id"], clock, 30)
    pat = _adult_creator(sc, pat)
    creator.save_support(sc, pat, {"enabled": True, "upi": "pat@okbank"})
    given = creator.pledge(sc, sam, pat["id"], {"amount_cents": 300, "message": "hi"})[
        "tip"
    ]
    creator.decide_tip(sc, pat, given["id"], "received")
    pending = creator.pledge(sc, sam, pat["id"], {"amount_cents": 400})["tip"]
    exported = service.export_channel(sc, pat["id"])
    assert (
        exported["support"]["upi"] == "pat@okbank"
        and len(exported["support_received"]) == 2
    )
    assert [
        item["amount_cents"]
        for item in service.export_channel(sc, sam["id"])["support_given"]
    ] == [300, 400]
    # Sam leaves: their open pledge ends, what they gave stays with Pat without their name
    service.erase_channels(sc, [sam["id"]])
    left = {item["id"]: item for item in store.rows(sc, "SELECT * FROM vi_tips")}
    assert (
        pending["id"] not in left
        and left[given["id"]]["supporter_id"] == ""
        and left[given["id"]]["message"] == ""
    )
    assert (
        creator.received_tips(sc, pat)["tips"][0]["supporter"]["name"]
        == "A former member"
    )
    # Pat leaves: their support settings, pledges and watch time go
    service.erase_channels(sc, [pat["id"]])
    for table in ("vi_tips", "vi_support", "vi_watch_days"):
        assert store.scalar(sc, f"SELECT COUNT(*) FROM {table}") == 0  # noqa: S608


def test_today_offers_watch_later_and_carry_on(sc, people, monkeypatch):
    clock = Clock(monkeypatch)
    pat, _mia, _teo, sam = people
    service.subscribe(sc, sam, pat["id"], True)
    video = _video(sc, pat, seconds=100, title="New one")
    lines = service.today_lines(sc, sam)
    assert (
        lines[0]["text"] == "New one"
        and lines[0]["action"]["path"] == f"/api/videos/videos/{video['id']}/later"
    )
    assert lines[0]["action"]["body"] == {"on": True} and lines[0]["done"] is False
    service.later(sc, sam, video["id"], True)
    saved = service.today_lines(sc, sam)[0]
    assert (
        saved["done"] is False
        and "action" not in saved
        and "watch later" in saved["sub"]
    )
    _watch(sc, sam, video["id"], clock, 20)
    assert service.clock(service.continue_watching(sc, sam, 1)[0]["position"]) == "0:20"
    assert service.clock(3725) == "1:02:05"


# ---------------------------------------------------------------------------
# the single-user server: a household, the Business hub, the CSV
# ---------------------------------------------------------------------------


def test_a_household_supports_a_creator_and_the_money_joins_the_business_hub(
    env, api, monkeypatch
):
    kit.forget_schema()
    db.forget()
    import auto_apply_assistant as local

    public.set_index(local._public_link_index)
    clock = Clock(monkeypatch)
    household = Household(env, api)
    pat = household.owner
    sam = household.add("Sam")
    mia = household.add("Mia", age_group="child", pin="2468")
    video = pat.post(
        "/api/videos/videos",
        {
            "file_id": upload(pat, "video", make_mp4(120, 1280, 720)),
            "title": "Garden tour",
            "description": "0:00 Gate\n0:40 Roses\n1:20 Pond",
        },
        expect=201,
    )["video"]
    assert [item["title"] for item in video["chapters"]] == ["Gate", "Roses", "Pond"]
    # support is off until Pat accepts the terms and says where to pay
    assert (
        pat.call("PUT", "/api/videos/support", {"enabled": True, "upi": "pat@okbank"})[
            0
        ]
        == 403
    )
    pat.post("/api/videos/earnings/terms", {})
    assert (
        pat.call(
            "PUT",
            "/api/videos/support",
            {"enabled": True, "link": "javascript:alert(1)"},
        )[0]
        == 400
    )
    settings = pat.ok(
        "PUT",
        "/api/videos/support",
        {"enabled": True, "link": "https://pay.example/pat"},
    )["settings"]
    assert settings["enabled"] is True
    seen = sam.get(f"/api/videos/videos/{video['id']}")
    assert seen["support"]["can_give"] is True and seen["video"]["position"] == 0
    assert mia.get(f"/api/videos/videos/{video['id']}")["support"]["can_give"] is False
    assert (
        mia.call(
            "POST",
            f"/api/videos/channels/{video['channel']['id']}/support",
            {"amount_cents": 500},
        )[0]
        == 403
    )
    assert (
        sam.get(f"/api/videos/channels/{video['channel']['id']}")["support"]["can_give"]
        is True
    )
    pledged = sam.post(
        f"/api/videos/channels/{video['channel']['id']}/support",
        {"amount_cents": 1500, "video_id": video["id"]},
        expect=201,
    )
    assert pledged["pay"]["link"] == "https://pay.example/pat"
    tip_id = pledged["tip"]["id"]
    assert [item["id"] for item in sam.get("/api/videos/support/given")["tips"]] == [
        tip_id
    ]
    # Today tells Pat support is on its way; the menu badge counts the notification
    card = next(
        item for item in pat.get("/api/life/today")["cards"] if item["app"] == "videos"
    )
    assert card["lines"][0]["text"] == "Support is on its way to you"
    assert pat.get("/api/videos/support")["waiting"] == 1
    # marked received: the Business hub has it; marked not received: it stops counting
    assert (
        sam.call("POST", f"/api/videos/support/{tip_id}", {"action": "received"})[0]
        == 404
    )
    assert (
        pat.post(f"/api/videos/support/{tip_id}", {"action": "received"})["tip"][
            "status"
        ]
        == "received"
    )
    lines = pat.get("/api/suite/revenue?app=videos")["lines"]
    assert [
        (line["source_type"], line["amount_cents"], line["voided_at"]) for line in lines
    ] == [("creator_tip", 1500, None)]
    pat.post(f"/api/videos/support/{tip_id}", {"action": "not_received"})
    assert pat.get("/api/suite/revenue?app=videos")["lines"][0]["voided_at"]
    # Sam carries on where they stopped; Today says so
    sam.post(
        f"/api/videos/videos/{video['id']}/progress", {"position": 0, "watched": 0}
    )
    clock.advance(seconds=45)
    sam.post(
        f"/api/videos/videos/{video['id']}/progress", {"position": 45, "watched": 45}
    )
    assert sam.get(f"/api/videos/videos/{video['id']}")["video"]["position"] == 45
    assert [item["id"] for item in sam.get("/api/videos/home")["continue"]] == [
        video["id"]
    ]
    assert sam.get("/api/videos/continue")["videos"][0]["position"] == 45
    card = next(
        item for item in sam.get("/api/life/today")["cards"] if item["app"] == "videos"
    )
    assert card["lines"][0]["text"] == "Carry on watching: Garden tour"
    # Pat's analytics and the CSV
    data = pat.get("/api/videos/analytics?days=7")
    assert (
        data["days"] == 7
        and data["totals"]["views"] == 1
        and data["totals"]["watch_seconds"] == 45
    )
    assert pat.call("GET", "/api/videos/analytics?days=5")[0] == 400
    status, raw = pat.call("GET", "/api/videos/analytics?days=7&format=csv", raw=True)
    assert status == 200 and raw.decode("utf-8-sig").splitlines()[1].startswith(
        "Garden tour,video,"
    )
    assert mia.get("/api/videos/analytics")["videos"] == []
    # the public page (no account) has the chapters too
    link = pat.post(f"/api/videos/videos/{video['id']}/link", {}, expect=201)["link"]
    status, _headers, raw = _anonymous(env, "GET", f"/api/public/{link['token']}")
    assert status == 200
    assert [item["at"] for item in json.loads(raw)["page"]["video"]["chapters"]] == [
        0,
        40,
        80,
    ]
    # a search filter by kind
    assert sam.get("/api/videos/search?q=garden&kind=reel")["videos"] == []
    assert [
        item["id"]
        for item in sam.get("/api/videos/search?q=garden&kind=video")["videos"]
    ] == [video["id"]]


# ---------------------------------------------------------------------------
# the hosted build
# ---------------------------------------------------------------------------


def test_hosted_members_support_creators_in_other_organizations(cloud):
    kit.forget_schema()
    db.forget()
    acme = build_org(cloud)
    creator_member = sign_in(cloud, acme.candidate)
    assert creator_member.call("GET", "/api/videos/analytics")[0] == 402
    assert creator_member.call("GET", "/api/videos/support")[0] == 402
    cloud.billing.subscribe_app(acme.owner, "videos")
    video = creator_member.call(
        "POST",
        "/api/videos/videos",
        {
            "file_id": upload(creator_member, "video", make_mp4()),
            "title": "Hosted talk",
        },
    )[1]["video"]
    assert creator_member.call("POST", "/api/videos/earnings/terms", {})[0] == 200
    status, saved = creator_member.call(
        "PUT", "/api/videos/support", {"enabled": True, "upi": "member@okbank"}
    )
    assert status == 200, saved
    other = build_org(cloud, "Other Org", slug_email="other.test")
    cloud.billing.subscribe_app(other.owner, "videos")
    fan = sign_in(cloud, other.owner)
    status, pledged = fan.call(
        "POST",
        f"/api/videos/channels/{video['channel']['id']}/support",
        {"amount_cents": 800},
    )
    assert status == 201, pledged
    status, decided = creator_member.call(
        "POST", f"/api/videos/support/{pledged['tip']['id']}", {"action": "received"}
    )
    assert status == 200 and decided["tip"]["status"] == "received"
    # a member's support is their own: it is not the organization's revenue
    status, revenue = sign_in(cloud, acme.owner).call(
        "GET", "/api/suite/revenue?app=videos"
    )
    assert status in (200, 403) and (status == 403 or revenue["lines"] == [])
    # another member of the creator's organization cannot answer the creator's pledges
    assert (
        sign_in(cloud, acme.owner).call(
            "POST",
            f"/api/videos/support/{pledged['tip']['id']}",
            {"action": "not_received"},
        )[0]
        == 404
    )
    analytics = creator_member.call("GET", "/api/videos/analytics?days=28")[1]
    assert analytics["totals"]["support"][0]["cents"] == 800
