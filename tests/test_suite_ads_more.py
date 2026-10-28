"""Ads, part two: editing a running campaign safely, starting again, copying, tracking tags, sales and the return on
ad spend, the forecast before buying, and how long the balance lasts; in both builds."""

from __future__ import annotations

from contextlib import closing

import pytest
from cloud_support import build_org, sign_in
from life_support import Household
from test_suite_ads import (
    ADVERTISER,
    _campaign,
    _file,
    _monetized_creator,
    _person,
    _watch_ad,
)
from videos_support import PNG, Clock, make_mp4, upload

import test_careers
from aira.suite import kit
from aira.suite.ads import service as ads
from aira.suite.ads.store import CENT, MICROS
from aira.suite.videos import db, service as videos, store

env = test_careers.env
api = test_careers.api
stub = test_careers.stub

BASE = {
    "name": "Spices",
    "objective": "views",
    "bid_cents": 200,
    "total_budget_cents": 5000,
    "daily_budget_cents": 1000,
    "landing_url": "https://spices.example/sale",
}


@pytest.fixture
def sc():
    db.forget()
    connection = db.connect()
    yield connection
    connection.close()


@pytest.fixture
def clock(monkeypatch):
    return Clock(monkeypatch)


def _view(sc, video, viewer):
    videos.count_view(sc, video, viewer["id"])
    sc.commit()


# ---------------------------------------------------------------------------
# editing a campaign
# ---------------------------------------------------------------------------


def test_a_partial_edit_keeps_the_targeting(sc):
    """Regression: the stored targeting (JSON lists) was read back as text, so any edit that did not resend every
    field failed with "Use two-letter codes" or "categories must be from"."""
    made = ads.create_campaign(
        sc, ADVERTISER, "A", {**BASE, "categories": "food", "countries": "IN"}
    )
    renamed = ads.update_campaign(sc, ADVERTISER, made["id"], {"name": "Diwali"})
    assert renamed["name"] == "Diwali" and renamed["categories"] == ["food"]
    assert renamed["countries"] == ["IN"] and renamed["languages"] == []
    raised = ads.update_campaign(
        sc, ADVERTISER, made["id"], {"total_budget_cents": 9000}
    )
    assert raised["total_budget_micros"] == 90 * MICROS
    assert raised["categories"] == ["food"]


def test_an_end_date_in_the_past_is_refused(sc):
    with pytest.raises(ValueError, match="end date has passed"):
        ads.create_campaign(sc, ADVERTISER, "A", {**BASE, "ends_on": "2020-01-01"})


def test_a_new_page_or_a_family_safe_claim_sends_the_ads_back_to_review(sc, clock):
    """Regression: an approved ad kept running after its campaign's page was changed to one nobody reviewed."""
    _creator, video = _monetized_creator(sc)
    viewer = _person(sc, "p:vic", "Vic", home="home-viewer")
    ads.credit(sc, ADVERTISER, 20 * MICROS, kind="payment", reference="cash")
    campaign, creative = _campaign(sc)
    assert ads.serve(sc, viewer, video, "preroll") is not None
    ads.update_campaign(
        sc, ADVERTISER, campaign["id"], {"name": "Spices, autumn"}
    )  # the same page
    assert ads.campaign(sc, ADVERTISER, campaign["id"])["creatives"][0][
        "review_status"
    ] == ("approved")
    ads.update_campaign(
        sc, ADVERTISER, campaign["id"], {"landing_url": "https://other.example/win"}
    )
    found = ads.campaign(sc, ADVERTISER, campaign["id"])
    assert found["creatives"][0]["review_status"] == "pending"
    assert [item["id"] for item in ads.review_queue(sc)] == [creative["id"]]
    assert ads.serve(sc, viewer, video, "preroll") is None
    ads.review(sc, creative["id"], "approve", "", "aira")
    ads.update_campaign(sc, ADVERTISER, campaign["id"], {"family_safe": True})
    assert (
        ads.campaign(sc, ADVERTISER, campaign["id"])["creatives"][0]["review_status"]
        == "pending"
    )


def test_a_campaign_that_spent_its_budget_starts_again_after_a_raise(sc, clock):
    _creator, video = _monetized_creator(sc)
    ads.credit(sc, ADVERTISER, 100 * MICROS, kind="payment", reference="x")
    campaign, _ = _campaign(sc, bid_cents=10000, total_cents=500, daily_cents=500)
    clock.now = clock.now.replace(hour=23)
    for n in range(60):
        _watch_ad(sc, clock, _person(sc, f"p:b{n}", f"B{n}", home=f"home-b{n}"), video)
    ended = ads.campaign(sc, ADVERTISER, campaign["id"])
    assert ended["status"] == "ended" and ended["restartable"] is True
    assert ended["ready_to_restart"] is False  # the screen offers Edit, not Start again
    with pytest.raises(ValueError, match="budget is spent"):
        ads.set_status(sc, ADVERTISER, campaign["id"], "active")
    ads.update_campaign(
        sc,
        ADVERTISER,
        campaign["id"],
        {"total_budget_cents": 1500, "daily_budget_cents": 1500},
    )
    assert ads.campaign(sc, ADVERTISER, campaign["id"])["ready_to_restart"] is True
    assert (
        ads.set_status(sc, ADVERTISER, campaign["id"], "active")["status"] == "active"
    )
    viewer = _person(sc, "p:new", "New", home="home-new")
    assert _watch_ad(sc, clock, viewer, video)[1]["counted"] is True
    # lowering the budget below what is spent ends it again
    again = ads.update_campaign(
        sc,
        ADVERTISER,
        campaign["id"],
        {"total_budget_cents": 500, "daily_budget_cents": 500},
    )
    assert again["status"] == "ended" and again["paused_reason"] == "budget_spent"
    # ended by hand is for good
    ads.update_campaign(
        sc,
        ADVERTISER,
        campaign["id"],
        {"total_budget_cents": 3000, "daily_budget_cents": 500},
    )
    ads.set_status(sc, ADVERTISER, campaign["id"], "active")
    ads.set_status(sc, ADVERTISER, campaign["id"], "ended")
    assert ads.campaign(sc, ADVERTISER, campaign["id"])["restartable"] is False
    with pytest.raises(ValueError, match="has ended"):
        ads.update_campaign(sc, ADVERTISER, campaign["id"], {"name": "Back"})
    with pytest.raises(ValueError, match="has ended"):
        ads.set_status(sc, ADVERTISER, campaign["id"], "active")


def test_a_campaign_past_its_end_date_starts_again_once_the_date_moves(sc, clock):
    ads.credit(sc, ADVERTISER, 20 * MICROS, kind="payment", reference="x")
    today = store.day()
    campaign, _ = _campaign(sc, ends_on=today)
    clock.advance(days=2)
    assert ads.tick(sc)["ended"] == 1
    with pytest.raises(ValueError, match="end date has passed"):
        ads.set_status(sc, ADVERTISER, campaign["id"], "active")
    ads.update_campaign(sc, ADVERTISER, campaign["id"], {"ends_on": ""})
    assert (
        ads.set_status(sc, ADVERTISER, campaign["id"], "active")["status"] == "active"
    )


# ---------------------------------------------------------------------------
# copying a campaign
# ---------------------------------------------------------------------------


def test_a_copy_keeps_the_settings_and_the_reviewed_ads_but_nothing_spent(sc, clock):
    _creator, video = _monetized_creator(sc)
    ads.credit(sc, ADVERTISER, 20 * MICROS, kind="payment", reference="x")
    campaign, creative = _campaign(sc, categories="food", frequency_cap=7)
    ads.update_campaign(sc, ADVERTISER, campaign["id"], {"utm": False})
    _watch_ad(sc, clock, _person(sc, "p:vic", "Vic", home="home-viewer"), video)
    copy = ads.duplicate_campaign(sc, ADVERTISER, campaign["id"])
    assert copy["id"] != campaign["id"] and copy["name"] == "Spices (copy)"
    assert copy["status"] == "draft" and copy["spent_micros"] == 0
    assert copy["categories"] == ["food"] and copy["frequency_cap"] == 7
    assert copy["utm"] is False
    assert [item["review_status"] for item in copy["creatives"]] == ["approved"]
    # changing the copy's picture keeps the original's file
    new_file = _file(sc, ads.owner_key(ADVERTISER), ADVERTISER, make_mp4(10), "video")
    ads.update_creative(
        sc, ADVERTISER, copy["creatives"][0]["id"], {"file_id": new_file}
    )
    original = ads.campaign(sc, ADVERTISER, campaign["id"])["creatives"][0]
    assert original["id"] == creative["id"] and original["media_url"]
    assert store.row(
        sc,
        "SELECT id FROM vi_files WHERE id = ?",
        (
            store.row(
                sc, "SELECT file_id FROM ad_creatives WHERE id = ?", (creative["id"],)
            )["file_id"],
        ),
    )
    with pytest.raises(LookupError):
        ads.duplicate_campaign(sc, "someone-else", campaign["id"])


# ---------------------------------------------------------------------------
# tracking tags on the page link
# ---------------------------------------------------------------------------


def test_tracking_tags_are_added_unless_turned_off_and_existing_ones_are_kept(sc):
    campaign = {"name": "Diwali Sale 2026!"}
    assert ads.tracked_url("https://shop.example/p?id=4", campaign, "reel") == (
        "https://shop.example/p?id=4&utm_source=aira&utm_medium=video_ad&utm_campaign=diwali-sale-2026&utm_content=reel"
    )
    assert ads.tracked_url(
        "https://shop.example/?utm_source=insta", campaign, "banner"
    ).startswith("https://shop.example/?utm_source=insta&utm_medium=video_ad")
    made = ads.create_campaign(sc, ADVERTISER, "A", {**BASE, "utm": False})
    assert made["utm"] is False
    assert ads.update_campaign(sc, ADVERTISER, made["id"], {"utm": True})["utm"] is True


def test_a_click_without_tags_goes_to_the_page_as_it_is(sc, clock):
    _creator, video = _monetized_creator(sc)
    ads.credit(sc, ADVERTISER, 10 * MICROS, kind="payment", reference="x")
    _campaign(sc, objective="clicks", bid_cents=50, kind="banner", utm=False)
    served, _ = _watch_ad(
        sc, clock, _person(sc, "p:vic", "Vic", home="home-viewer"), video, "banner"
    )
    target = ads.click(sc, served["impression_id"], served["click_url"].split("s=")[1])
    assert target == "https://spices.example/sale"


# ---------------------------------------------------------------------------
# sales and the return on ad spend
# ---------------------------------------------------------------------------


def test_sales_give_the_return_on_ad_spend(sc, clock):
    _creator, video = _monetized_creator(sc)
    ads.credit(sc, ADVERTISER, 20 * MICROS, kind="payment", reference="x")
    campaign, _ = _campaign(sc, bid_cents=10000, frequency_cap=5)  # 10 cents each
    for n in range(4):
        _watch_ad(sc, clock, _person(sc, f"p:s{n}", f"S{n}", home=f"home-s{n}"), video)
    with pytest.raises(ValueError, match="future"):
        ads.add_sale(
            sc,
            ADVERTISER,
            campaign["id"],
            {"day": "2999-01-01", "value_cents": 100},
            "me",
        )
    with pytest.raises(ValueError, match="worth"):
        ads.add_sale(sc, ADVERTISER, campaign["id"], {"value_cents": -5}, "me")
    with pytest.raises(ValueError, match="orders"):
        ads.add_sale(
            sc, ADVERTISER, campaign["id"], {"orders": 0, "value_cents": 100}, "me"
        )
    with pytest.raises(LookupError):
        ads.add_sale(sc, "someone-else", campaign["id"], {"value_cents": 100}, "me")
    sale = ads.add_sale(
        sc,
        ADVERTISER,
        campaign["id"],
        {"orders": 2, "value_cents": 180, "note": "2 jars"},
        "me",
    )
    assert sale["orders"] == 2 and sale["value_micros"] == 180 * CENT
    found = ads.campaign(sc, ADVERTISER, campaign["id"])
    assert found["spent_micros"] == 40 * CENT
    assert found["sales"] == {
        "orders": 2,
        "sales_micros": 180 * CENT,
        "roas": 4.5,
        "cost_per_order_micros": 20 * CENT,
        "profit_micros": 140 * CENT,
    }
    report = ads.report(sc, ADVERTISER)
    assert report["totals"]["roas"] == 4.5 and report["totals"]["orders"] == 2
    assert report["days"][-1]["sales_micros"] == 180 * CENT
    assert report["campaigns"][0]["id"] == campaign["id"]
    assert report["campaigns"][0]["roas"] == 4.5
    csv = ads.report_csv(sc, ADVERTISER).decode()
    assert csv.splitlines()[0].endswith("orders,sales_usd")
    assert csv.splitlines()[1].endswith(",2,1.80")
    listing = ads.campaigns_csv(sc, ADVERTISER).decode().splitlines()
    assert listing[0].startswith("name,status,pays_for") and "4.5" in listing[1]
    with pytest.raises(LookupError):
        ads.delete_sale(sc, "someone-else", sale["id"])
    ads.delete_sale(sc, ADVERTISER, sale["id"])
    assert ads.campaign(sc, ADVERTISER, campaign["id"])["sales"]["roas"] == 0.0


def test_a_sale_on_a_day_without_ads_is_still_in_the_report(sc, clock):
    ads.credit(sc, ADVERTISER, 20 * MICROS, kind="payment", reference="x")
    campaign, _ = _campaign(sc)
    ads.add_sale(sc, ADVERTISER, campaign["id"], {"value_cents": 999}, "me")
    report = ads.report(sc, ADVERTISER)
    assert report["days"] == [
        {
            "day": store.day(),
            "impressions": 0,
            "clicks": 0,
            "spend_micros": 0,
            "ctr_percent": 0.0,
            "ecpm_micros": 0,
            "orders": 1,
            "sales_micros": 999 * CENT,
        }
    ]
    assert report["totals"]["roas"] is None  # nothing spent: no ratio to show


# ---------------------------------------------------------------------------
# the forecast
# ---------------------------------------------------------------------------


def test_the_forecast_reads_last_weeks_views_and_the_competition(sc, clock):
    estimate = ads.estimate(sc, ADVERTISER, BASE)
    assert estimate["views_per_day"] == 0 and estimate["impressions_per_day"] == 0
    assert estimate["confidence"] == "low" and estimate["competitors"] == 0
    assert (
        estimate["suggested_bid_micros"] == 50 * CENT
    )  # nobody to beat: the lowest bid
    creator, video = _monetized_creator(sc)
    for n in range(70):
        _view(sc, video, _person(sc, f"p:w{n}", f"W{n}", home=f"home-w{n}"))
    _view(sc, video, _person(sc, "p:kid", "Kid", age="child", home="home-kid"))
    _view(sc, video, creator)  # the creator's own view never shows an ad
    estimate = ads.estimate(sc, ADVERTISER, BASE)
    assert estimate["views_per_day"] == 10.0 and estimate["win_share_percent"] == 100
    assert estimate["impressions_per_day"] == 10.0
    assert estimate["spend_per_day_micros"] == 10 * 2000  # $2 per thousand
    assert estimate["creators_per_day_micros"] == 10 * 1100
    assert estimate["budget_days"] == 2500.0 and estimate["balance_days"] == 0.0
    # another category, or a teen-only audience, finds nothing
    assert (
        ads.estimate(sc, ADVERTISER, {**BASE, "categories": "gaming"})["views_per_day"]
        == 0
    )
    assert (
        ads.estimate(sc, ADVERTISER, {**BASE, "countries": "US"})["views_per_day"] == 0
    )
    # a rival bidding $5 a thousand wins most of it; the advice is the next step above (rivals' bids stay private)
    ads.credit(sc, "org-rival", 50 * MICROS, kind="payment", reference="x")
    _campaign(sc, home="org-rival", bid_cents=500)
    estimate = ads.estimate(sc, ADVERTISER, BASE)
    assert estimate["competitors"] == 1 and estimate["win_share_percent"] == 10
    assert estimate["impressions_per_day"] == 1.0
    assert estimate["suggested_bid_micros"] == 550 * CENT
    assert (
        ads.estimate(sc, ADVERTISER, {**BASE, "bid_cents": 550})["win_share_percent"]
        == 100
    )
    # the daily budget caps it: $1 a day at $100 a thousand is 10 impressions
    capped = ads.estimate(
        sc,
        ADVERTISER,
        {**BASE, "bid_cents": 10000, "daily_budget_cents": 100},
    )
    assert capped["budget_limited"] is False and capped["impressions_per_day"] == 10.0
    capped = ads.estimate(
        sc, ADVERTISER, {**BASE, "bid_cents": 10000, "daily_budget_cents": 50}
    )
    assert capped["budget_limited"] is True and capped["impressions_per_day"] == 5.0
    # clicks: a new click campaign is ranked at a 2% click rate
    clicks = ads.estimate(
        sc, ADVERTISER, {**BASE, "objective": "clicks", "bid_cents": 30}
    )
    assert clicks["bid_type"] == "cpc" and clicks["win_share_percent"] == 100
    assert clicks["suggested_bid_micros"] == 30 * CENT
    with pytest.raises(ValueError, match="two-letter"):
        ads.estimate(sc, ADVERTISER, {**BASE, "countries": "India"})


def test_the_balance_says_how_long_it_lasts(sc, clock):
    _creator, video = _monetized_creator(sc)
    ads.credit(sc, ADVERTISER, 7 * 10 * CENT, kind="payment", reference="x")
    assert ads.overview(sc, ADVERTISER)["balance_days"] is None  # nothing spent yet
    _campaign(sc, bid_cents=10000, frequency_cap=20)
    for n in range(7):
        _watch_ad(sc, clock, _person(sc, f"p:r{n}", f"R{n}", home=f"home-r{n}"), video)
    data = ads.overview(sc, ADVERTISER)
    assert data["spend_per_day_micros"] == 10 * CENT  # 70 cents over 7 days
    assert data["balance_micros"] == 0 and data["balance_days"] == 0.0


# ---------------------------------------------------------------------------
# the single-user server
# ---------------------------------------------------------------------------


def test_the_advertiser_plans_copies_and_counts_sales_on_this_computer(
    env, api, monkeypatch
):
    kit.forget_schema()
    db.forget()
    Clock(monkeypatch)
    shop = Household(env, api).owner
    shop.post("/api/suite/business", {"name": "Spice Shop"})
    planned = shop.post("/api/ads/estimate", {**BASE, "categories": "food"})
    assert planned["estimate"]["confidence"] == "low" and planned["balance_micros"] == 0
    half_typed = shop.post("/api/ads/estimate", {"countries": "I"})
    assert half_typed["estimate"] is None and "two-letter" in half_typed["problem"]
    campaign = shop.post("/api/ads/campaigns", BASE, expect=201)["campaign"]
    assert campaign["utm"] is True
    assert (
        shop.post("/api/ads/estimate", {"campaign_id": campaign["id"]})["estimate"][
            "bid_type"
        ]
        == "cpm"
    )
    renamed = shop.call(
        "PATCH", f"/api/ads/campaigns/{campaign['id']}", {"name": "Autumn"}
    )
    assert renamed[0] == 200 and renamed[1]["campaign"]["name"] == "Autumn"
    ad_file = upload(shop, "image", PNG, base="/api/ads")
    shop.post(
        f"/api/ads/campaigns/{campaign['id']}/creatives",
        {"kind": "banner", "headline": "Fresh spices", "file_id": ad_file},
        expect=201,
    )
    copy = shop.post(f"/api/ads/campaigns/{campaign['id']}/duplicate", {}, expect=201)[
        "campaign"
    ]
    assert copy["name"] == "Autumn (copy)" and len(copy["creatives"]) == 1
    sold = shop.post(
        f"/api/ads/campaigns/{campaign['id']}/sales",
        {"orders": 3, "value_cents": 4500},
        expect=201,
    )
    assert sold["campaign"]["sales"]["orders"] == 3
    listed = shop.get(f"/api/ads/campaigns/{campaign['id']}/sales")["sales"]
    assert [item["value_micros"] for item in listed] == [45 * MICROS]
    status, csv = shop.call("GET", "/api/ads/campaigns?format=csv", raw=True)
    assert status == 200 and csv.startswith(b"name,status,pays_for")
    assert b"Autumn (copy)" in csv
    activity = shop.get("/api/suite/activity")
    assert any(
        "3 sales worth $45.00" in item["title"]
        for item in activity.get("items", activity.get("activity", []))
    )
    shop.ok("DELETE", f"/api/ads/sales/{listed[0]['id']}")
    assert shop.get(f"/api/ads/campaigns/{campaign['id']}/sales")["sales"] == []
    # another account on this computer is another advertiser: it cannot see or change these
    other_client = env.api()
    other_client.call(
        "POST",
        "/api/auth/register",
        {"email": "other@example.com", "password": test_careers.PASSWORD},
    )
    other = Household(env, other_client).owner
    assert other.call("GET", f"/api/ads/campaigns/{campaign['id']}/sales")[0] == 404
    assert (
        other.call("POST", f"/api/ads/campaigns/{campaign['id']}/duplicate", {})[0]
        == 404
    )


def test_the_hub_warns_when_the_balance_runs_low(env, api, monkeypatch):
    kit.forget_schema()
    db.forget()
    Clock(monkeypatch)
    shop = Household(env, api).owner
    shop.get("/api/ads/overview")  # the hub finds the advertiser through Ads
    with closing(db.connect()) as vdb:
        found = store.row(vdb, "SELECT home FROM ad_accounts LIMIT 1")
        ads.credit(vdb, found["home"], 20 * CENT, kind="payment", reference="cash")
        campaign = ads.create_campaign(vdb, found["home"], "Shop", BASE)
        moment = store.stamp()
        for n in range(7):
            vdb.execute(
                """INSERT INTO ad_impressions (id, campaign_id, creative_id, home, slot, viewer_key, served_at, status,
                cost_micros, day, month) VALUES (?, ?, 'c', ?, 'preroll', ?, ?, 'counted', ?, ?, ?)""",
                (
                    store.new_id(),
                    campaign["id"],
                    found["home"],
                    f"v{n}",
                    moment,
                    10 * CENT,
                    store.day(),
                    store.month(),
                ),
            )
        vdb.commit()
    hub = shop.get("/api/suite/home")
    card = next(item for item in hub["apps"] if item["id"] == "ads")["cards"][0]
    assert card["label"] == "Ad balance" and card["tone"] == "warn"
    assert card["hint"] == "lasts about 2 days"


# ---------------------------------------------------------------------------
# the hosted build
# ---------------------------------------------------------------------------


def test_hosted_roles_for_the_forecast_copies_and_sales(cloud, monkeypatch):
    kit.forget_schema()
    db.forget()
    Clock(monkeypatch)
    shop = build_org(cloud, "Spice Shop Ltd", slug_email="shop.test")
    owner = sign_in(cloud, shop.owner)
    assert owner.call("POST", "/api/ads/estimate", BASE)[1]["code"] == "app_required"
    cloud.billing.subscribe_app(shop.owner, "ads")
    campaign = owner.call("POST", "/api/ads/campaigns", BASE)[1]["campaign"]
    coach = sign_in(cloud, shop.coach)
    status, sold = coach.call(
        "POST", f"/api/ads/campaigns/{campaign['id']}/sales", {"value_cents": 1200}
    )
    assert status == 201 and sold["sale"]["value_micros"] == 12 * MICROS
    auditor = sign_in(cloud, shop.auditor)
    assert auditor.call("POST", "/api/ads/estimate", BASE)[0] == 200  # a read
    assert (
        auditor.call(
            "POST", f"/api/ads/campaigns/{campaign['id']}/sales", {"value_cents": 1}
        )[0]
        == 403
    )
    assert (
        auditor.call("POST", f"/api/ads/campaigns/{campaign['id']}/duplicate", {})[0]
        == 403
    )
    assert (
        auditor.call("GET", f"/api/ads/campaigns/{campaign['id']}/sales")[1]["sales"][
            0
        ]["value_micros"]
        == 12 * MICROS
    )
    candidate = sign_in(cloud, shop.candidate)
    assert candidate.call("POST", "/api/ads/estimate", BASE)[0] == 403
    # another organization never sees this one's sales or campaigns
    rival = build_org(cloud, "Rival Ltd", slug_email="rival.test")
    cloud.billing.subscribe_app(rival.owner, "ads")
    stranger = sign_in(cloud, rival.owner)
    assert stranger.call("GET", f"/api/ads/campaigns/{campaign['id']}/sales")[0] == 404
    assert stranger.call("GET", "/api/ads/campaigns")[1]["campaigns"] == []
