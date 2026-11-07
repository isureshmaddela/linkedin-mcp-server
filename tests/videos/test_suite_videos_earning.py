"""Videos: the ways a creator earns beyond ads (memberships with videos for members, brand deals with the paid
promotion label), every payment in one income overview with this month's outlook, scheduled videos, and Studio's
search, filters and spreadsheet; in the single-user build (the money joins the Business hub) and hosted (a member's
money is their own)."""

# ruff: noqa: F811
from __future__ import annotations

import csv
import io
import sqlite3
from contextlib import closing
from datetime import timedelta

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
from test_suite_videos_creator import _adult_creator, _impression
from videos_support import Clock, make_mp4, upload

import test_careers
from aira.suite import kit, public
from aira.suite.videos import creator, db, deals, members, service, store

env = test_careers.env
api = test_careers.api
stub = test_careers.stub


def _seller(sc, ch, *, upi="pat@okbank", price=500, currency="USD"):
    """An adult creator with the terms accepted, support on (a UPI id) and memberships at ``price`` a month."""
    ch = _adult_creator(sc, ch)
    creator.save_support(sc, ch, {"upi": upi, "enabled": True})
    members.save_plan(
        sc,
        ch,
        {
            "enabled": True,
            "price_cents": price,
            "currency": currency,
            "perks": "A video for members every week",
        },
    )
    return service.viewer_of(service.channel(sc, ch["id"]))


# ---------------------------------------------------------------------------
# an old database gets the new columns
# ---------------------------------------------------------------------------


def test_a_database_made_before_memberships_gets_the_new_columns(tmp_path):
    path = tmp_path / "old.sqlite3"
    old = sqlite3.connect(str(path))
    # the table as the first release made it (store.SCHEMA keeps that statement; the new columns are added later)
    old.execute(
        next(item for item in store.SCHEMA if "TABLE IF NOT EXISTS vi_videos" in item)
    )
    old.execute(
        "INSERT INTO vi_videos (id, channel_id, title, file_id, created_at, updated_at) VALUES ('a', 'c', 'Old', 'f', 'x', 'x')"
    )
    old.commit()
    old.close()
    previous = db._provider, db._default
    db.forget()
    db.set_path(lambda: path)
    try:
        with closing(db.connect()) as connection:
            columns = {
                item[1] for item in connection.execute("PRAGMA table_info(vi_videos)")
            }
            assert {"members_only", "paid_promotion"} <= columns
            found = connection.execute(
                "SELECT members_only, paid_promotion FROM vi_videos"
            ).fetchone()
            assert tuple(found) == (0, 0)
            store.migrate(connection)  # a second run (another process) changes nothing
    finally:
        db._provider, db._default = previous
        db.forget()


# ---------------------------------------------------------------------------
# scheduled videos
# ---------------------------------------------------------------------------


def test_a_scheduled_video_goes_out_at_its_time(sc, people, monkeypatch):
    clock = Clock(monkeypatch)
    pat, _mia, _teo, sam = people
    later = clock.now + timedelta(hours=3)
    video = _video(sc, pat, title="Premiere", publish_at=later.isoformat())
    assert video["scheduled"] is True and video["created_at"] == store.stamp(later)
    # nobody else sees it, lists it or finds it until then; its owner watches it
    with pytest.raises(LookupError):
        service.visible_video(sc, sam, video["id"])
    assert service.home(sc, sam)["recent"] == []
    assert service.search(sc, sam, "premiere")["videos"] == []
    assert service.visible_video(sc, pat, video["id"])["id"] == video["id"]
    assert [item["id"] for item in service.my_videos(sc, pat, show="scheduled")] == [
        video["id"]
    ]
    assert service.public_video(sc, video["id"], "t")["available"] is False
    # moved an hour later, then out at its time
    moved = service.edit_video(
        sc, pat, video["id"], {"publish_at": (later + timedelta(hours=1)).isoformat()}
    )
    assert moved["created_at"] == store.stamp(later + timedelta(hours=1))
    clock.advance(hours=4, minutes=1)
    assert [item["id"] for item in service.home(sc, sam)["recent"]] == [video["id"]]
    assert service.my_videos(sc, pat, show="scheduled") == []
    # a video that is out cannot be scheduled again (its own past date is left alone); a year at most
    with pytest.raises(ValueError, match="already out"):
        service.edit_video(
            sc,
            pat,
            video["id"],
            {"publish_at": (clock.now + timedelta(days=1)).isoformat()},
        )
    assert service.edit_video(
        sc,
        pat,
        video["id"],
        {"publish_at": (clock.now - timedelta(days=1)).isoformat()},
    )["created_at"] == store.stamp(later + timedelta(hours=1))
    # a time that has passed is a slip (a wrong day, AM for PM): refused, never published at once; a time a
    # few minutes ago (the form took a moment) means now
    with pytest.raises(ValueError, match="already passed"):
        _video(
            sc,
            pat,
            title="Slip",
            publish_at=(clock.now - timedelta(days=1)).isoformat(),
        )
    now_video = _video(
        sc, pat, title="Now", publish_at=(clock.now - timedelta(minutes=2)).isoformat()
    )
    assert now_video["scheduled"] is False
    with pytest.raises(ValueError, match="within the next year"):
        _video(
            sc,
            pat,
            title="Far",
            publish_at=(clock.now + timedelta(days=400)).isoformat(),
        )
    with pytest.raises(ValueError):
        _video(sc, pat, title="Bad", publish_at="next tuesday")


def test_a_scheduled_video_can_be_sent_out_now(sc, people, monkeypatch):
    clock = Clock(monkeypatch)
    pat, _mia, _teo, sam = people
    video = _video(
        sc, pat, publish_at=(clock.now + timedelta(days=2)).isoformat(), title="Soon"
    )
    out = service.edit_video(sc, pat, video["id"], {"publish_at": ""})
    assert out["scheduled"] is False
    assert service.visible_video(sc, sam, video["id"])["id"] == video["id"]


# ---------------------------------------------------------------------------
# memberships
# ---------------------------------------------------------------------------


def test_a_membership_plan_needs_the_terms_a_way_to_pay_and_a_fair_price(sc, people):
    pat, mia, _teo, _sam = people
    with pytest.raises(PermissionError, match="terms"):
        members.save_plan(sc, pat, {"enabled": True, "price_cents": 500})
    pat = _adult_creator(sc, pat)
    with pytest.raises(ValueError, match="payment page or UPI"):
        members.save_plan(sc, pat, {"enabled": True, "price_cents": 500})
    assert members.plan_settings(sc, pat)["can_enable"] is False
    creator.save_support(sc, pat, {"upi": "pat@okbank"})
    assert members.plan_settings(sc, pat)["can_enable"] is True
    for price in (0, 50, 100_001):
        with pytest.raises(ValueError, match="monthly price"):
            members.save_plan(sc, pat, {"enabled": True, "price_cents": price})
    plan = members.save_plan(
        sc, pat, {"enabled": True, "price_cents": 300, "currency": "INR"}
    )
    assert plan["enabled"] and plan["price_cents"] == 300 and plan["currency"] == "INR"
    with pytest.raises(PermissionError, match="adults"):
        members.save_plan(sc, mia, {"enabled": False})


def test_videos_for_members_follow_their_own_rules(sc, people):
    pat, _mia, _teo, _sam = people
    with pytest.raises(ValueError, match="Turn on memberships first"):
        _video(sc, pat, title="Secret", members_only=True)
    pat = _seller(sc, pat)
    with pytest.raises(ValueError, match="Reels are for everyone"):
        _video(
            sc,
            pat,
            title="Tall",
            kind="reel",
            seconds=20,
            size=(720, 1280),
            members_only=True,
        )
    with pytest.raises(ValueError, match="made for children"):
        _video(sc, pat, title="Kids", members_only=True, for_kids=True)
    video = _video(sc, pat, title="Secret", members_only=True)
    assert video["members_only"] is True
    # turning memberships off later keeps the video for members, and it can still be edited
    members.save_plan(sc, pat, {"enabled": False})
    edited = service.edit_video(sc, pat, video["id"], {"title": "Secret, edited"})
    assert edited["members_only"] is True and edited["title"] == "Secret, edited"


def test_joining_paying_and_watching_a_video_for_members(sc, people, monkeypatch):
    clock = Clock(monkeypatch)
    pat, mia, teo, sam = people
    pat = _seller(sc, pat)
    video = _video(sc, pat, title="Members' cut", members_only=True)
    # Sam sees it listed but locked: no stream, cannot play, like or comment, no ad before it
    listed = service.home(sc, sam)["recent"]
    assert listed[0]["id"] == video["id"] and listed[0]["locked"] is True
    view = service.video_view(
        sc, sam, service.visible_video(sc, sam, video["id"]), full=True
    )
    assert (
        view["locked"] is True
        and view["stream_url"] == ""
        and view["captions_url"] == ""
    )
    for call in (
        lambda: service.progress(sc, sam, video["id"], {"position": 1, "watched": 1}),
        lambda: service.like(sc, sam, video["id"], True),
        lambda: service.comment(sc, sam, video["id"], {"text": "Hi"}),
        lambda: service.comments(sc, sam, video["id"]),
    ):
        with pytest.raises(PermissionError, match="members"):
            call()
    offer = members.for_viewer(sc, sam, pat["id"])
    assert offer["open"] and offer["can_join"] and offer["price"] == "$5.00"
    assert offer["member"] is False
    # children and teens cannot join; nobody joins their own channel
    assert members.for_viewer(sc, mia, pat["id"])["can_join"] is False
    for person in (mia, teo):
        with pytest.raises(PermissionError, match="adults"):
            members.join(sc, person, pat["id"], {"months": 1})
    assert members.for_viewer(sc, pat, pat["id"]) is None
    with pytest.raises(ValueError):
        members.join(sc, sam, pat["id"], {"months": 2})
    # Sam joins for three months: pays $15 on Pat's UPI with a reference
    joined = members.join(sc, sam, pat["id"], {"months": 3})
    payment = joined["payment"]
    assert payment["amount"] == "$15.00" and payment["reference"].startswith("MEM-")
    assert joined["pay"]["upi"] == "pat@okbank"
    assert joined["pay"]["upi_link"].startswith("upi://pay?pa=pat@okbank")
    with pytest.raises(ValueError, match="already have a payment waiting"):
        members.join(sc, sam, pat["id"], {"months": 1})
    assert members.waiting_count(sc, pat["id"]) == 1
    assert service.locked(sc, sam, service._video_row(sc, video["id"])) is True
    # only Pat answers; once received, Sam watches for 90 days
    with pytest.raises(LookupError):
        members.decide(sc, sam, payment["id"], "received")
    decided = members.decide(sc, pat, payment["id"], "received")
    assert decided["status"] == "received"
    assert decided["paid_until"] == store.stamp(clock.now + timedelta(days=90))
    assert service.locked(sc, sam, service._video_row(sc, video["id"])) is False
    assert service.progress(sc, sam, video["id"], {"position": 0, "watched": 0})
    assert members.for_viewer(sc, sam, pat["id"])["member"] is True
    assert "Welcome" in service.notifications(sc, sam)["notifications"][0]["text"]
    # a renewal adds a month from when it would have ended
    renewal = members.join(sc, sam, pat["id"], {"months": 1})["payment"]
    renewed = members.decide(sc, pat, renewal["id"], "received")
    assert renewed["paid_until"] == store.stamp(clock.now + timedelta(days=120))
    # marked received by mistake: those days come back off
    undone = members.decide(sc, pat, renewal["id"], "not_received")
    assert undone["status"] == "declined"
    assert undone["paid_until"] == store.stamp(clock.now + timedelta(days=90))
    with pytest.raises(ValueError, match="already answered"):
        members.decide(sc, pat, renewal["id"], "not_received")
    # ...and "not received" pressed by mistake is put right: the month comes back
    again = members.decide(sc, pat, renewal["id"], "received")
    assert again["status"] == "received"
    assert again["paid_until"] == store.stamp(clock.now + timedelta(days=120))
    members.decide(sc, pat, renewal["id"], "not_received")
    # the creator's page lists the member; after the 90 days the video locks again
    page = members.creator_view(sc, pat)
    assert [item["member"]["name"] for item in page["members"]] == ["Sam"]
    assert page["plan"]["active_members"] == 1 and page["plan"]["monthly_cents"] == 500
    clock.advance(days=91)
    assert service.locked(sc, sam, service._video_row(sc, video["id"])) is True
    assert members.creator_view(sc, pat)["members"][0]["active"] is False


def test_a_waiting_membership_payment_can_be_withdrawn(sc, people):
    pat, _mia, _teo, sam = people
    pat = _seller(sc, pat)
    payment = members.join(sc, sam, pat["id"], {"months": 1})["payment"]
    assert members.my_memberships(sc, sam)[0]["waiting"]["id"] == payment["id"]
    with pytest.raises(LookupError):
        members.withdraw(sc, pat, payment["id"])
    assert members.withdraw(sc, sam, payment["id"])["status"] == "cancelled"
    with pytest.raises(ValueError):
        members.withdraw(sc, sam, payment["id"])
    assert members.my_memberships(sc, sam) == []  # joined, withdrew, never paid
    with pytest.raises(ValueError, match="withdrew"):
        members.decide(sc, pat, payment["id"], "received")
    # with memberships off nobody new can join
    members.save_plan(sc, pat, {"enabled": False})
    with pytest.raises(LookupError):
        members.join(sc, sam, pat["id"], {"months": 1})


# ---------------------------------------------------------------------------
# brand deals
# ---------------------------------------------------------------------------


def test_brand_deals_from_first_message_to_payment(sc, people, monkeypatch):
    Clock(monkeypatch)
    pat, mia, _teo, sam = people
    video = _video(sc, pat, title="Kitchen tour")
    other = _video(sc, sam, title="Not Pat's")
    with pytest.raises(PermissionError, match="adults"):
        deals.create(sc, mia, {"brand": "Toys"})
    with pytest.raises(ValueError):
        deals.create(sc, pat, {"brand": ""})
    with pytest.raises(LookupError):
        deals.create(sc, pat, {"brand": "Spice Co", "video_id": other["id"]})
    deal = deals.create(
        sc,
        pat,
        {
            "brand": "Spice Co",
            "contact": "asha@spice.example",
            "amount_cents": 25_000,
            "currency": "INR",
            "status": "agreed",
            "due_on": store.day(store.utcnow() - timedelta(days=2)),
            "video_id": video["id"],
            "notes": "=1+1 mention at 2:00",
        },
    )
    assert deal["amount"] == "₹250.00" and deal["overdue"] is True
    # the video tells viewers it includes paid promotion
    shown = service.video_view(
        sc, sam, service.visible_video(sc, sam, video["id"]), full=True
    )
    assert shown["paid_promotion"] is True
    assert service.public_video(sc, video["id"], "t")["video"]["paid_promotion"] is True
    second = deals.create(sc, pat, {"brand": "Bike Shop", "amount_cents": 9900})
    listed = deals.listing(sc, pat)
    assert {item["brand"] for item in listed["deals"]} == {"Spice Co", "Bike Shop"}
    assert listed["overdue"] == 1
    assert [item["amount"] for item in listed["waiting"]] == ["₹250.00"]
    assert [item["brand"] for item in deals.listing(sc, pat, q="spice")["deals"]] == [
        "Spice Co"
    ]
    assert [item["brand"] for item in deals.listing(sc, pat, "proposed")["deals"]] == [
        "Bike Shop"
    ]
    assert len(deals.listing(sc, pat, "open")["deals"]) == 2
    with pytest.raises(ValueError):
        deals.listing(sc, pat, "lost")
    # paid: dated, counted this year, no longer late; Sam cannot touch Pat's deals
    with pytest.raises(LookupError):
        deals.update(sc, sam, deal["id"], {"status": "paid"})
    paid, before = deals.update(sc, pat, deal["id"], {"status": "paid"})
    assert before == "agreed" and paid["paid_at"] and paid["overdue"] is False
    assert [item["amount"] for item in deals.listing(sc, pat)["paid_this_year"]] == [
        "₹250.00"
    ]
    back, _ = deals.update(sc, pat, deal["id"], {"status": "delivered"})
    assert back["paid_at"] is None
    # the spreadsheet never runs a note as a formula
    table = list(csv.reader(io.StringIO(deals.as_csv(sc, pat).decode("utf-8-sig"))))
    assert table[0][0] == "Brand" and len(table) == 3
    assert "'=1+1 mention at 2:00" in [row[-1] for row in table]
    deals.delete(sc, pat, second["id"])
    assert [item["brand"] for item in deals.listing(sc, pat)["deals"]] == ["Spice Co"]


# ---------------------------------------------------------------------------
# all income, the outlook, Today
# ---------------------------------------------------------------------------


def test_income_brings_every_way_of_earning_together(sc, people, monkeypatch):
    clock = Clock(monkeypatch)
    pat, _mia, _teo, sam = people
    pat = _seller(sc, pat)
    video = _video(sc, pat, title="Cooking")
    # ads: $0.40 today; a tip of $5, a membership of $5 and a deal of $100, all received this month
    _impression(sc, video, 400_000)
    tip = creator.pledge(sc, sam, pat["id"], {"amount_cents": 500})["tip"]
    creator.decide_tip(sc, pat, tip["id"], "received")
    payment = members.join(sc, sam, pat["id"], {"months": 1})["payment"]
    members.decide(sc, pat, payment["id"], "received")
    deal = deals.create(sc, pat, {"brand": "Spice Co", "amount_cents": 10_000})
    deals.update(sc, pat, deal["id"], {"status": "paid"})
    agreed = deals.create(
        sc, pat, {"brand": "Bike Shop", "amount_cents": 2_000, "status": "agreed"}
    )
    data = creator.income(sc, pat)
    now = data["months"][0]
    assert now["month"] == store.month() and len(data["months"]) == 12
    assert [item["amount"] for item in now["ads"]] == ["$0.40"]
    assert [item["amount"] for item in now["support"]] == ["$5.00"]
    assert [item["amount"] for item in now["memberships"]] == ["$5.00"]
    assert [item["amount"] for item in now["deals"]] == ["$100.00"]
    assert [item["amount"] for item in now["total"]] == ["$110.40"]
    assert data["top_source"] == "deals" and data["best_month"] == store.month()
    assert data["lowest_month"] is None  # one month with money: nothing lower
    assert {item["source"] for item in data["log"]} == {
        "ads",
        "support",
        "memberships",
        "deals",
    }
    ads_line = next(item for item in data["log"] if item["source"] == "ads")
    assert ads_line["estimated"] is True and ads_line["from"] == "Ads on your videos"
    assert data["ways"] == {
        "terms": True,
        "ads": False,
        "support": True,
        "memberships": True,
        "deals": True,
    }
    outlook = data["outlook"]
    assert outlook["ads_so_far_micros"] == 400_000
    assert (
        outlook["ads_projected_micros"]
        == 400_000 * outlook["days_in_month"] // clock.now.day
    )
    assert outlook["members_active"] == 1 and outlook["members_monthly"]["cents"] == 500
    assert [item["amount"] for item in outlook["deals_due"]] == ["$20.00"]
    # an earlier, smaller month makes this the best one and that the lowest
    _impression(
        sc, video, 100_000, day=store.day(clock.now.replace(day=1) - timedelta(days=3))
    )
    data = creator.income(sc, pat)
    assert data["best_month"] == store.month()
    assert data["lowest_month"] == data["months"][1]["month"]
    # the log as a spreadsheet
    table = list(
        csv.reader(io.StringIO(creator.income_csv(sc, pat).decode("utf-8-sig")))
    )
    assert table[0] == ["Date", "Source", "From", "Amount", "Currency", "Note"]
    assert {row[1] for row in table[1:]} == {
        "Ads on your videos",
        "Support from viewers",
        "Memberships",
        "Brand deals",
    }
    assert ["Spice Co", "100.00", "USD"] in [row[2:5] for row in table]
    # analytics counts members and deals in the period too
    totals = creator.analytics(sc, pat, 7)["totals"]
    assert (
        totals["memberships"][0]["cents"] == 500
        and totals["deals"][0]["cents"] == 10_000
    )
    assert agreed["status"] == "agreed"


def test_income_in_two_currencies_is_never_added_together(sc, people, monkeypatch):
    Clock(monkeypatch)
    pat, _mia, _teo, _sam = people
    pat = _adult_creator(sc, pat)
    for brand, cents, currency in (("A", 10_000, "USD"), ("B", 50_000, "INR")):
        deal = deals.create(
            sc, pat, {"brand": brand, "amount_cents": cents, "currency": currency}
        )
        deals.update(sc, pat, deal["id"], {"status": "paid"})
    data = creator.income(sc, pat)
    assert [item["amount"] for item in data["months"][0]["total"]] == [
        "₹500.00",
        "$100.00",
    ]
    assert data["best_month"] is None and data["top_source"] is None


def test_today_reminds_creators_and_members(sc, people, monkeypatch):
    clock = Clock(monkeypatch)
    from aira.suite.videos import videos_routes

    pat, _mia, _teo, sam = people
    pat = _seller(sc, pat)
    deals.create(
        sc, pat, {"brand": "Spice Co", "status": "agreed", "due_on": store.day()}
    )
    payment = members.join(sc, sam, pat["id"], {"months": 1})["payment"]
    texts = [line["text"] for line in videos_routes._money_lines(sc, pat)]
    assert texts == [
        "Brand deal with Spice Co is due today",
        "A new member is paying to join your channel",
    ]
    members.decide(sc, pat, payment["id"], "received")
    assert videos_routes._renew_lines(sc, sam) == []
    clock.advance(days=27)
    assert videos_routes._renew_lines(sc, sam)[0]["text"].startswith(
        "Your membership of Pat ends on"
    )


# ---------------------------------------------------------------------------
# Studio: search, filters, the spreadsheet; export and erasure
# ---------------------------------------------------------------------------


def test_studio_finds_your_videos_and_writes_them_to_a_spreadsheet(sc, people):
    pat, _mia, _teo, _sam = people
    pat = _seller(sc, pat)
    _video(sc, pat, title="Dal at home", tags="cooking, dal")
    _video(sc, pat, title="50% off_day", visibility="unlisted")
    _video(sc, pat, title="Members' recipe", members_only=True, paid_promotion=True)
    titles = lambda **kw: sorted(  # noqa: E731
        item["title"] for item in service.my_videos(sc, pat, **kw)
    )
    assert titles(q="cooking") == ["Dal at home"]
    assert titles(q="50%") == ["50% off_day"]
    assert titles(q="_") == ["50% off_day"]
    assert titles(show="unlisted") == ["50% off_day"]
    assert titles(show="members") == ["Members' recipe"]
    assert titles(show="promoted") == ["Members' recipe"]
    assert titles(show="problems") == []
    with pytest.raises(ValueError):
        service.my_videos(sc, pat, show="everything")
    table = list(
        csv.reader(
            io.StringIO(creator.videos_csv(sc, pat, show="members").decode("utf-8-sig"))
        )
    )
    assert table[0][:2] == ["Title", "Kind"] and len(table) == 2
    assert table[1][0] == "Members' recipe" and table[1][4] == "yes"


def test_export_and_erasure_cover_memberships_and_deals(sc, people):
    pat, _mia, _teo, sam = people
    pat = _seller(sc, pat)
    deals.create(sc, pat, {"brand": "Spice Co", "amount_cents": 100})
    payment = members.join(sc, sam, pat["id"], {"months": 1})["payment"]
    members.decide(sc, pat, payment["id"], "received")
    exported = service.export_channel(sc, pat["id"])
    assert exported["membership_plan"]["price_cents"] == 500
    assert [item["brand"] for item in exported["brand_deals"]] == ["Spice Co"]
    assert len(exported["membership_payments_received"]) == 1
    assert (
        service.export_channel(sc, sam["id"])["memberships"][0]["channel_id"]
        == pat["id"]
    )
    # Sam leaves: the payment Pat received stays for Pat's books, without Sam
    service.erase_channels(sc, [sam["id"]])
    left = store.rows(sc, "SELECT member_id, status FROM vi_member_payments")
    assert left == [{"member_id": "", "status": "received"}]
    assert store.rows(sc, "SELECT * FROM vi_members") == []
    # Pat leaves: the plan, payments and deals go
    service.erase_channels(sc, [pat["id"]])
    for table in ("vi_member_plans", "vi_member_payments", "vi_deals"):
        assert store.rows(sc, f"SELECT * FROM {table}") == []  # noqa: S608


# ---------------------------------------------------------------------------
# the single-user server: the money joins the Business hub
# ---------------------------------------------------------------------------


def test_a_household_earns_from_members_and_brands_on_this_computer(env, api):
    kit.forget_schema()
    db.forget()
    import auto_apply_assistant as local

    public.set_index(local._public_link_index)
    household = Household(env, api)
    pat = household.owner
    sam = household.add("Sam")
    mia = household.add("Mia", age_group="child", pin="2468")
    pat.post("/api/videos/earnings/terms", {})
    assert (
        pat.call(
            "PUT", "/api/videos/memberships", {"enabled": True, "price_cents": 400}
        )[0]
        == 400
    )
    pat.ok("PUT", "/api/videos/support", {"upi": "pat@okbank"})
    plan = pat.ok(
        "PUT",
        "/api/videos/memberships",
        {"enabled": True, "price_cents": 400, "perks": "Early videos"},
    )["plan"]
    assert plan["enabled"] is True
    video = pat.post(
        "/api/videos/videos",
        {
            "file_id": upload(pat, "video", make_mp4(60, 1280, 720)),
            "title": "Behind the scenes",
            "members_only": True,
        },
        expect=201,
    )["video"]
    # a page for anyone is never made for a video for members
    assert pat.call("POST", f"/api/videos/videos/{video['id']}/link", {})[0] == 403
    seen = sam.get(f"/api/videos/videos/{video['id']}")
    assert seen["video"]["locked"] is True and seen["video"]["stream_url"] == ""
    assert seen["membership"]["can_join"] is True
    assert sam.call(
        "POST", "/api/videos/ads/serve", {"video_id": video["id"], "slot": "preroll"}
    )[1] == {"ad": None}
    assert (
        sam.call("POST", f"/api/videos/videos/{video['id']}/like", {"on": True})[0]
        == 403
    )
    assert (
        mia.get(f"/api/videos/channels/{video['channel']['id']}")["membership"][
            "can_join"
        ]
        is False
    )
    assert (
        mia.call(
            "POST", f"/api/videos/channels/{video['channel']['id']}/join", {"months": 1}
        )[0]
        == 403
    )
    joined = sam.post(
        f"/api/videos/channels/{video['channel']['id']}/join", {"months": 1}, expect=201
    )
    payment = joined["payment"]
    assert (
        sam.get("/api/videos/memberships/mine")["memberships"][0]["waiting"]["id"]
        == payment["id"]
    )
    card = next(
        item for item in pat.get("/api/life/today")["cards"] if item["app"] == "videos"
    )
    assert "A new member is paying to join your channel" in [
        line["text"] for line in card["lines"]
    ]
    assert (
        sam.call(
            "POST", f"/api/videos/memberships/{payment['id']}", {"action": "received"}
        )[0]
        == 404
    )
    pat.post(f"/api/videos/memberships/{payment['id']}", {"action": "received"})
    watched = sam.get(f"/api/videos/videos/{video['id']}")["video"]
    assert "locked" not in watched and "/api/videos/media/" in watched["stream_url"]
    # a brand deal, paid: the Business hub has both; moved back from paid, the deal stops counting
    deal = pat.post(
        "/api/videos/deals",
        {
            "brand": "Spice Co",
            "amount_cents": 12_000,
            "status": "agreed",
            "video_id": video["id"],
        },
        expect=201,
    )["deal"]
    pat.ok("PATCH", f"/api/videos/deals/{deal['id']}", {"status": "paid"})
    lines = {
        line["source_type"]: line
        for line in pat.get("/api/suite/revenue?app=videos")["lines"]
    }
    assert lines["creator_membership"]["amount_cents"] == 400
    assert (
        lines["creator_deal"]["amount_cents"] == 12_000
        and not lines["creator_deal"]["voided_at"]
    )
    pat.ok("PATCH", f"/api/videos/deals/{deal['id']}", {"status": "delivered"})
    lines = {
        line["source_type"]: line
        for line in pat.get("/api/suite/revenue?app=videos")["lines"]
    }
    assert lines["creator_deal"]["voided_at"]
    assert mia.call("GET", "/api/videos/deals")[0] == 403
    # all income and the spreadsheets
    income = pat.get("/api/videos/income")
    assert [item["amount"] for item in income["months"][0]["memberships"]] == ["$4.00"]
    status, raw = pat.call("GET", "/api/videos/income?format=csv", raw=True)
    assert status == 200 and "Memberships" in raw.decode("utf-8-sig")
    status, raw = pat.call("GET", "/api/videos/deals?format=csv", raw=True)
    assert status == 200 and "Spice Co" in raw.decode("utf-8-sig")
    status, raw = pat.call(
        "GET", "/api/videos/studio?format=csv&show=members", raw=True
    )
    assert status == 200 and "Behind the scenes" in raw.decode("utf-8-sig")
    studio = pat.get("/api/videos/studio?q=behind")
    assert studio["total"] == 1 and studio["videos"][0]["paid_promotion"] is True
    assert pat.call("GET", "/api/videos/studio?show=nope")[0] == 400


# ---------------------------------------------------------------------------
# the hosted build
# ---------------------------------------------------------------------------


def test_hosted_members_join_creators_in_other_organizations(cloud):
    kit.forget_schema()
    db.forget()
    acme = build_org(cloud)
    maker = sign_in(cloud, acme.candidate)
    assert maker.call("GET", "/api/videos/memberships")[0] == 402
    assert maker.call("GET", "/api/videos/deals")[0] == 402
    assert maker.call("GET", "/api/videos/income")[0] == 402
    cloud.billing.subscribe_app(acme.owner, "videos")
    assert maker.call("POST", "/api/videos/earnings/terms", {})[0] == 200
    assert maker.call("PUT", "/api/videos/support", {"upi": "maker@okbank"})[0] == 200
    status, saved = maker.call(
        "PUT", "/api/videos/memberships", {"enabled": True, "price_cents": 700}
    )
    assert status == 200, saved
    status, deal = maker.call(
        "POST",
        "/api/videos/deals",
        {"brand": "Hosted Brand", "amount_cents": 5_000, "status": "paid"},
    )
    assert status == 201, deal
    other = build_org(cloud, "Other Org", slug_email="other.test")
    cloud.billing.subscribe_app(other.owner, "videos")
    fan = sign_in(cloud, other.owner)
    channel_id = maker.call("GET", "/api/videos/me")[1]["channel"]["id"]
    status, joined = fan.call(
        "POST", f"/api/videos/channels/{channel_id}/join", {"months": 1}
    )
    assert status == 201, joined
    status, decided = maker.call(
        "POST",
        f"/api/videos/memberships/{joined['payment']['id']}",
        {"action": "received"},
    )
    assert status == 200 and decided["payment"]["status"] == "received"
    # a member's earnings are their own: not the organization's revenue
    status, revenue = sign_in(cloud, acme.owner).call(
        "GET", "/api/suite/revenue?app=videos"
    )
    assert status in (200, 403) and (status == 403 or revenue["lines"] == [])
    # another member of the creator's organization sees none of the creator's deals or members
    colleague = sign_in(cloud, acme.owner)
    assert colleague.call("GET", "/api/videos/deals")[1]["deals"] == []
    assert colleague.call("GET", "/api/videos/memberships")[1]["members"] == []
    assert (
        colleague.call(
            "POST",
            f"/api/videos/memberships/{joined['payment']['id']}",
            {"action": "not_received"},
        )[0]
        == 404
    )
    income = maker.call("GET", "/api/videos/income")[1]
    assert [item["amount"] for item in income["months"][0]["total"]] == ["$57.00"]
