"""Videos: regressions for problems a tester found in the ways a creator earns.

* The Business hub is the whole household's (children included): only the household owner's earnings join it, and
  its lines never name a member, a supporter or a brand.
* "Only for my members" and a time that has passed are refused before the file goes up (``/videos/check``), and
  files that never became part of a video are removed after a day.
* A time that has already passed is refused, not published at once.
* A membership payment or support marked "not received" by mistake can still be marked received.
* A brand deal's "Includes paid promotion" goes off with the deal; a deal records the day it was paid.
* What members bring in a month follows what they paid, not today's price.
"""

# ruff: noqa: F811
from __future__ import annotations

import sqlite3
from contextlib import closing
from datetime import timedelta

import pytest
from life_support import Household
from test_suite_videos import (  # noqa: F401  (fixtures used by name)
    _file,
    _no_model,
    _person,
    _video,
    people,
    sc,
)
from test_suite_videos_earning import _seller
from videos_support import PNG, Clock, make_mp4, upload

import test_careers
from aira.suite import kit, public
from aira.suite.videos import creator, db, deals, media, members, service, store

env = test_careers.env
api = test_careers.api
stub = test_careers.stub


# ---------------------------------------------------------------------------
# the Business hub on a shared household computer
# ---------------------------------------------------------------------------


def _local():
    kit.forget_schema()
    db.forget()
    import auto_apply_assistant as local

    public.set_index(local._public_link_index)


def _creator(person, upi):
    person.post("/api/videos/earnings/terms", {})
    person.ok("PUT", "/api/videos/support", {"upi": upi, "enabled": True})


def test_the_business_hub_names_nobody_and_keeps_other_peoples_earnings_private(
    env, api
):
    _local()
    household = Household(env, api)
    owner = household.owner
    partner = household.add("Partner")
    teeny = household.add("Teeny", age_group="teen")
    kiddo = household.add("Kiddo", age_group="child", pin="2468")
    _creator(owner, "home@okbank")
    owner.ok("PUT", "/api/videos/memberships", {"enabled": True, "price_cents": 400})
    # the owner's paid brand deal, recorded for money that arrived in an earlier month
    paid_on = (store.utcnow() - timedelta(days=40)).date().isoformat()
    deal = owner.post(
        "/api/videos/deals",
        {
            "brand": "Private Brand",
            "amount_cents": 1000,
            "status": "paid",
            "notes": "secret terms",
            "paid_on": paid_on,
        },
        expect=201,
    )["deal"]
    assert deal["paid_on"] == paid_on
    # Partner joins the owner's channel; the owner marks it received
    channel = owner.get("/api/videos/me")["channel"]["id"]
    payment = partner.post(
        f"/api/videos/channels/{channel}/join", {"months": 1}, expect=201
    )["payment"]
    owner.post(f"/api/videos/memberships/{payment['id']}", {"action": "received"})
    # Partner earns too (their own deal): it is theirs, not the household's
    _creator(partner, "partner@okbank")
    partner.post(
        "/api/videos/deals",
        {"brand": "Partner Brand", "amount_cents": 7000, "status": "paid"},
        expect=201,
    )
    statuses = {}
    for person in (owner, partner, teeny, kiddo):
        status, data = person.call("GET", "/api/suite/revenue?app=videos")
        statuses[person.name] = status
        if status != 200:
            continue  # a household that keeps the hub from someone shows them nothing at all
        lines = [line for line in data["lines"] if not line["voided_at"]]
        text = repr(lines)
        assert "Private Brand" not in text and "Partner" not in text, person.name
        assert "Partner Brand" not in text and "secret" not in text
        assert sorted(
            (line["source_type"], line["label"], line["amount_cents"]) for line in lines
        ) == [
            ("creator_deal", "Brand deal (Videos)", 1000),
            ("creator_membership", "Channel membership (Videos)", 400),
        ]
        hub_deal = next(line for line in lines if line["source_type"] == "creator_deal")
        assert hub_deal["at"].startswith(paid_on)  # the day the money arrived
    assert statuses[owner.name] == 200
    # the creator's own pages still name the brand and the member
    assert owner.get("/api/videos/deals")["deals"][0]["brand"] == "Private Brand"
    log = owner.get("/api/videos/income")["log"]
    assert {item["source"] for item in log} >= {"deals", "memberships"}
    assert any(
        item["at"].startswith(paid_on) for item in log if item["source"] == "deals"
    )


def _account_database(env):
    """The account's own database file on the test server (the one with the Business hub's revenue)."""
    for path in sorted(env.tmp_path.rglob("*.sqlite3")):
        with closing(sqlite3.connect(str(path))) as connection:
            if connection.execute(
                "SELECT 1 FROM sqlite_master WHERE name = 'suite_revenue'"
            ).fetchone():
                return path
    raise AssertionError("no account database with the Business hub")


def test_lines_written_before_the_rule_lose_their_names_and_others_stop_counting(
    env, api
):
    _local()
    household = Household(env, api)
    owner = household.owner
    partner = household.add("Partner")
    _creator(owner, "home@okbank")
    _creator(partner, "partner@okbank")
    mine = owner.post(
        "/api/videos/deals",
        {"brand": "Named Brand", "amount_cents": 900, "status": "paid"},
        expect=201,
    )["deal"]
    theirs = partner.post(
        "/api/videos/deals",
        {"brand": "Partner Brand", "amount_cents": 700, "status": "agreed"},
        expect=201,
    )["deal"]
    # as an earlier release left the database: the brand in the label, and Partner's deal counted
    path = _account_database(env)
    with closing(sqlite3.connect(str(path))) as connection:
        connection.execute(
            "UPDATE suite_revenue SET label = 'Brand deal with Named Brand (Videos)', customer = 'Named Brand'"
        )
        connection.execute(
            """INSERT INTO suite_revenue (id, app, source_type, source_id, label, customer, amount_cents, currency, at)
            VALUES ('old1', 'videos', 'creator_deal', ?, 'Brand deal with Partner Brand (Videos)', 'Partner Brand',
            700, 'USD', '2026-01-05T10:00:00Z')""",
            (theirs["id"],),
        )
        connection.commit()
    owner.get("/api/videos/income")  # the next look at the money tidies them
    lines = owner.get("/api/suite/revenue?app=videos")["lines"]
    by_id = {line["source_id"]: line for line in lines}
    assert by_id[mine["id"]]["label"] == "Brand deal (Videos)"
    assert by_id[mine["id"]]["customer"] == "A brand"
    assert not by_id[mine["id"]]["voided_at"]
    assert by_id[theirs["id"]]["voided_at"]
    assert "Named Brand" not in repr(lines) and "Partner Brand" not in repr(lines)


# ---------------------------------------------------------------------------
# uploads: checked before the file goes up; files left over are removed
# ---------------------------------------------------------------------------


def test_the_upload_form_is_checked_before_the_file_goes_up(sc, people):
    pat, mia, _teo, _sam = people
    from test_suite_videos_creator import _adult_creator

    pat = _adult_creator(sc, pat)
    # memberships are off: "Only for my members" is refused by the check, with nothing uploaded
    with pytest.raises(ValueError, match="Turn on memberships first"):
        service.check_video(sc, pat, {"title": "Secret", "members_only": True})
    with pytest.raises(ValueError, match="already passed"):
        service.check_video(
            sc, pat, {"title": "Late", "publish_at": "2020-01-01T00:00:00Z"}
        )
    with pytest.raises(ValueError, match="within the next year"):
        service.check_video(
            sc,
            pat,
            {
                "title": "Far",
                "publish_at": (store.utcnow() + timedelta(days=400)).isoformat(),
            },
        )
    with pytest.raises(ValueError, match="tall"):
        service.check_video(
            sc, pat, {"title": "Reel", "kind": "reel", "width": 1280, "height": 720}
        )
    assert store.rows(sc, "SELECT id FROM vi_files") == []
    later = (store.utcnow() + timedelta(days=2)).isoformat()
    assert service.check_video(sc, pat, {"title": "Fine", "publish_at": later})[
        "scheduled"
    ]
    assert service.check_video(sc, mia, {"title": "Mine"})["ok"]
    # with memberships on, it passes
    pat = _seller(sc, pat)
    assert service.check_video(sc, pat, {"title": "Secret", "members_only": True})["ok"]


def test_files_that_never_became_a_video_are_removed_after_a_day(
    sc, people, monkeypatch
):
    clock = Clock(monkeypatch)
    pat, _mia, _teo, _sam = people
    used = _video(sc, pat, title="Kept")
    picture = _file(sc, pat, PNG, "image")
    service.edit_video(sc, pat, used["id"], {"thumb_file_id": picture["id"]})
    orphan_video = _file(sc, pat, make_mp4(10, 640, 360))
    orphan_picture = _file(sc, pat, PNG, "image")
    # an advertiser's file is Ads' business, never swept here
    ad_upload = media.start(sc, "ad:home-9", "home-9", "image", "ad.png", len(PNG))
    import base64

    media.append(sc, ad_upload["id"], "ad:home-9", 0, base64.b64encode(PNG).decode())
    ad_file = media.complete(sc, ad_upload["id"], "ad:home-9")
    assert media.sweep_orphans(sc) == 0  # not yet: someone may be filling in the form
    clock.advance(hours=25)
    paths = {
        item["id"]: media.root() / item["path"]
        for item in store.rows(sc, "SELECT id, path FROM vi_files")
    }
    assert media.sweep_orphans(sc) == 2
    left = {item["id"] for item in store.rows(sc, "SELECT id FROM vi_files")}
    assert orphan_video["id"] not in left and orphan_picture["id"] not in left
    assert not paths[orphan_video["id"]].exists()
    video_row = service._video_row(sc, used["id"])
    assert {video_row["file_id"], picture["id"], ad_file["id"]} <= left


def test_the_server_refuses_members_only_and_a_past_time_before_the_upload(env, api):
    _local()
    household = Household(env, api)
    owner = household.owner
    owner.post("/api/videos/earnings/terms", {})
    me = owner.get("/api/videos/me")
    assert me["members_plan_on"] is False
    status, body = owner.call(
        "POST", "/api/videos/videos/check", {"title": "Secret", "members_only": True}
    )
    assert status == 400 and "Turn on memberships first" in body["error"]
    status, body = owner.call(
        "POST",
        "/api/videos/videos/check",
        {"title": "Late", "publish_at": "2020-01-01T00:00:00Z"},
    )
    assert status == 400 and "already passed" in body["error"]
    # creating it with a time in the past is refused too (it used to go out at once)
    file_id = upload(owner, "video", make_mp4(30, 1280, 720))
    status, body = owner.call(
        "POST",
        "/api/videos/videos",
        {"file_id": file_id, "title": "Late", "publish_at": "2020-01-01T00:00:00Z"},
    )
    assert status == 400 and "already passed" in body["error"]
    owner.ok("PUT", "/api/videos/support", {"upi": "home@okbank"})
    owner.ok("PUT", "/api/videos/memberships", {"enabled": True, "price_cents": 400})
    assert owner.get("/api/videos/me")["members_plan_on"] is True
    made = owner.post(
        "/api/videos/videos",
        {"file_id": file_id, "title": "Secret", "members_only": True},
        expect=201,
    )["video"]
    assert made["members_only"] is True


# ---------------------------------------------------------------------------
# a mistake on a payment can be put right
# ---------------------------------------------------------------------------


def test_support_marked_not_received_by_mistake_can_be_received(sc, people):
    pat, _mia, _teo, sam = people
    pat = _seller(sc, pat)
    pledged = creator.pledge(sc, sam, pat["id"], {"amount_cents": 300})["tip"]
    creator.decide_tip(sc, pat, pledged["id"], "received")
    assert creator.decide_tip(sc, pat, pledged["id"], "not_received")["status"] == (
        "declined"
    )
    assert creator.decide_tip(sc, pat, pledged["id"], "received")["status"] == (
        "received"
    )
    with pytest.raises(ValueError, match="already answered"):
        creator.decide_tip(sc, pat, pledged["id"], "received")
    other = creator.pledge(sc, sam, pat["id"], {"amount_cents": 200})["tip"]
    creator.cancel_tip(sc, sam, other["id"])
    with pytest.raises(ValueError, match="withdrew"):
        creator.decide_tip(sc, pat, other["id"], "received")


# ---------------------------------------------------------------------------
# brand deals: the label goes with the deal; the day it was paid
# ---------------------------------------------------------------------------


def _promoted(sc, video_id):
    return bool(service._video_row(sc, video_id)["paid_promotion"])


def test_paid_promotion_goes_off_when_its_deal_is_moved_cancelled_or_deleted(
    sc, people
):
    pat, _mia, _teo, _sam = people
    pat = _seller(sc, pat)
    walk = _video(sc, pat, title="Garden walk")
    cook = _video(sc, pat, title="Cooking")
    deal = deals.create(sc, pat, {"brand": "Spice Co", "video_id": walk["id"]})
    assert _promoted(sc, walk["id"])
    # linked to the wrong video, then moved: the old video no longer says so
    deal, _ = deals.update(sc, pat, deal["id"], {"video_id": cook["id"]})
    assert not _promoted(sc, walk["id"]) and _promoted(sc, cook["id"])
    deal, _ = deals.update(sc, pat, deal["id"], {"video_id": ""})
    assert not _promoted(sc, cook["id"])
    # cancelled, and back again
    deal, _ = deals.update(sc, pat, deal["id"], {"video_id": walk["id"]})
    deals.update(sc, pat, deal["id"], {"status": "cancelled"})
    assert not _promoted(sc, walk["id"])
    deals.update(sc, pat, deal["id"], {"status": "agreed"})
    assert _promoted(sc, walk["id"])
    # two deals on one video: the label stays until the last one goes
    second = deals.create(sc, pat, {"brand": "Tea Co", "video_id": walk["id"]})
    deals.delete(sc, pat, deal["id"])
    assert _promoted(sc, walk["id"])
    deals.delete(sc, pat, second["id"])
    assert not _promoted(sc, walk["id"])
    # a label the creator ticked themselves is theirs: a deal on it does not take it away
    service.edit_video(sc, pat, cook["id"], {"paid_promotion": True})
    own = deals.create(sc, pat, {"brand": "Own Co", "video_id": cook["id"]})
    deals.delete(sc, pat, own["id"])
    assert _promoted(sc, cook["id"])


def test_a_deal_records_the_day_it_was_paid(sc, people, monkeypatch):
    clock = Clock(monkeypatch)
    pat, _mia, _teo, _sam = people
    pat = _seller(sc, pat)
    march = (clock.now - timedelta(days=60)).date().isoformat()
    deal = deals.create(
        sc,
        pat,
        {"brand": "Spice Co", "amount_cents": 5000, "status": "paid", "paid_on": march},
    )
    assert deal["paid_on"] == march and deal["paid_at"].startswith(march)
    income = creator.income(sc, pat)
    assert [item["month"] for item in income["months"] if item["deals"]] == [march[:7]]
    # moved to another day; an edit that does not say keeps it; a day ahead is refused
    deal, _ = deals.update(sc, pat, deal["id"], {"paid_on": store.day()})
    assert deal["paid_on"] == store.day()
    deal, _ = deals.update(sc, pat, deal["id"], {"notes": "on time"})
    assert deal["paid_on"] == store.day()
    with pytest.raises(ValueError, match="future"):
        deals.update(
            sc,
            pat,
            deal["id"],
            {"paid_on": (clock.now + timedelta(days=3)).date().isoformat()},
        )
    # without a day: today
    other = deals.create(sc, pat, {"brand": "Tea Co", "status": "paid"})
    assert other["paid_on"] == store.day()
    # moved back from paid, the day goes
    other, _ = deals.update(sc, pat, other["id"], {"status": "delivered"})
    assert other["paid_on"] == "" and other["paid_at"] is None


# ---------------------------------------------------------------------------
# a month from members: by what they paid
# ---------------------------------------------------------------------------


def test_a_month_from_members_follows_what_they_paid_not_todays_price(
    sc, people, monkeypatch
):
    Clock(monkeypatch)
    pat, _mia, _teo, sam = people
    pat = _seller(sc, pat, price=500, currency="USD")
    payment = members.join(sc, sam, pat["id"], {"months": 3})["payment"]
    members.decide(sc, pat, payment["id"], "received")
    members.save_plan(sc, pat, {"price_cents": 9900, "currency": "INR"})
    outlook = creator.outlook(sc, pat)
    assert outlook["members_monthly"]["amount"] == "$5.00"
    assert outlook["members_monthly"]["cents"] == 500
    plan = members.plan_settings(sc, pat)
    assert [item["amount"] for item in plan["monthly"]] == ["$5.00"]
