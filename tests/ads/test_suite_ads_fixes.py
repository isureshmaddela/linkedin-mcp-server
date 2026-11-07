"""Ads, part three: regressions from the independent test round (an old click link and a changed page, who in the
household may spend the ad balance, rivals' bids in the forecast, copying dates, sales of a deleted campaign, a bid
out of range in the forecast, a wrong kind of file for an ad)."""

from __future__ import annotations

import base64
from datetime import timedelta
from urllib.parse import parse_qs, urlsplit

import pytest
from life_support import Household, Person
from test_suite_ads import (
    ADVERTISER,
    _campaign,
    _monetized_creator,
    _person,
    _watch_ad,
)
from videos_support import PNG, Clock, make_mp4

import test_careers
from aira.suite import kit
from aira.suite.ads import service as ads
from aira.suite.ads.store import CENT, MICROS
from aira.suite.videos import db, store

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


def _click(sc, served):
    signature = parse_qs(urlsplit(served["click_url"]).query)["s"][0]
    return ads.click(sc, served["impression_id"], signature)


# ---------------------------------------------------------------------------
# an old click link after the page changed
# ---------------------------------------------------------------------------


def test_an_old_click_link_keeps_going_to_the_page_that_was_reviewed(sc, clock):
    """Regression: the click redirect read the campaign's current page, so a link handed out before the page was
    changed sent people to the new page nobody had reviewed (for as long as the link was kept)."""
    _creator, video = _monetized_creator(sc)
    ads.credit(sc, ADVERTISER, 20 * MICROS, kind="payment", reference="cash")
    campaign, creative = _campaign(sc, landing_url="https://spices.example/autumn")
    viewer = _person(sc, "p:vic", "Vic", home="home-viewer")
    served, seen = _watch_ad(sc, clock, viewer, video)
    assert seen["counted"] is True
    assert _click(sc, served).startswith(
        "https://spices.example/autumn?utm_source=aira"
    )
    ads.update_campaign(
        sc,
        ADVERTISER,
        campaign["id"],
        {"landing_url": "https://other-unreviewed.example/x"},
    )
    assert _click(sc, served).startswith("https://spices.example/autumn?")
    clock.advance(seconds=10 * 24 * 3600)
    assert _click(sc, served).startswith("https://spices.example/autumn?")
    # once a reviewer approves the new page, ads served from then on lead there; the old link still does not
    ads.review(sc, creative["id"], "approve", "", "aira:test")
    later = ads.serve(
        sc, _person(sc, "p:new", "New", home="home-new"), video, "preroll"
    )
    assert _click(sc, later).startswith("https://other-unreviewed.example/x?")
    assert _click(sc, served).startswith("https://spices.example/autumn?")
    # a third page: each impression goes to the page of its own time
    ads.update_campaign(
        sc, ADVERTISER, campaign["id"], {"landing_url": "https://third.example/"}
    )
    assert _click(sc, later).startswith("https://other-unreviewed.example/x?")
    assert _click(sc, served).startswith("https://spices.example/autumn?")
    # an ad a reviewer turned down leads nowhere any more
    ads.review(sc, creative["id"], "reject", "Misleading", "aira:test")
    with pytest.raises(LookupError, match="no longer shown"):
        _click(sc, served)


def test_erasing_an_advertiser_removes_its_pages_sales_and_options(sc, clock):
    ads.credit(sc, ADVERTISER, 20 * MICROS, kind="payment", reference="cash")
    campaign, _ = _campaign(sc)
    ads.update_campaign(
        sc, ADVERTISER, campaign["id"], {"landing_url": "https://b.example/"}
    )
    ads.add_sale(sc, ADVERTISER, campaign["id"], {"value_cents": 500}, "me")
    ads.set_options(sc, ADVERTISER, campaign["id"], utm=False)
    ads.erase_advertiser(sc, ADVERTISER)
    for table in ("ad_campaign_pages", "ad_sales", "ad_campaign_options"):
        assert (
            store.scalar(
                sc, f"SELECT COUNT(*) FROM {table} WHERE home = ?", (ADVERTISER,)
            )  # noqa: S608
            == 0
        ), table


# ---------------------------------------------------------------------------
# the forecast keeps rivals' bids private and refuses a bid out of range
# ---------------------------------------------------------------------------


def test_the_forecast_does_not_give_away_a_rivals_exact_bid(sc, clock):
    """Regression: the suggested bid was the highest rival's bid plus 10 cents, so a narrow forecast showed another
    advertiser's exact bid."""
    ads.credit(sc, "org-rival", 50 * MICROS, kind="payment", reference="x")
    rival, _ = _campaign(sc, home="org-rival", bid_cents=1337, categories="food")
    food = {**BASE, "categories": "food"}
    estimate = ads.estimate(sc, ADVERTISER, food)
    assert estimate["suggested_bid_micros"] == 14 * MICROS  # the next whole dollar
    assert estimate["competitors"] == 1
    # nearby bids say nothing finer than the step: $13.38 is not told it beats $13.37
    assert (
        ads.estimate(sc, ADVERTISER, {**food, "bid_cents": 1338})["win_share_percent"]
        == 10
    )
    assert (
        ads.estimate(sc, ADVERTISER, {**food, "bid_cents": 1399})["win_share_percent"]
        == 10
    )
    assert (
        ads.estimate(sc, ADVERTISER, {**food, "bid_cents": 1400})["win_share_percent"]
        == 100
    )
    # any rival bid in the same step gives the same answer
    for cents in (1301, 1399):
        ads.update_campaign(sc, "org-rival", rival["id"], {"bid_cents": cents})
        assert ads.estimate(sc, ADVERTISER, food)["suggested_bid_micros"] == 14 * MICROS
    # a bid of exactly $13 is beaten only by the next step
    ads.update_campaign(sc, "org-rival", rival["id"], {"bid_cents": 1300})
    assert ads.estimate(sc, ADVERTISER, food)["suggested_bid_micros"] == 14 * MICROS
    assert ads.bid_bucket(4.2 * MICROS) == 450 * CENT
    assert ads.bid_bucket(72 * MICROS) == 75 * MICROS


@pytest.mark.parametrize(
    ("bid", "message"),
    [
        (1, "between \\$0.50 and \\$100.00 per 1,000 views"),
        (20000, "between \\$0.50 and \\$100.00"),
        (None, "Enter your bid"),
        ("abc", "whole number|Enter your bid"),
    ],
)
def test_the_forecast_says_when_the_bid_is_out_of_range(sc, bid, message):
    """Regression: an unreadable bid was taken as $0 and one below the minimum as valid, and the forecast showed
    costs like $0.0000 a day lasting hundreds of thousands of days."""
    raw = {**BASE, "bid_cents": bid}
    with pytest.raises(ValueError, match=message):
        ads.estimate(sc, ADVERTISER, raw)
    with pytest.raises(ValueError, match="between \\$0.05 and \\$50.00 per click"):
        ads.estimate(sc, ADVERTISER, {**BASE, "objective": "clicks", "bid_cents": 1})


# ---------------------------------------------------------------------------
# copying and deleting
# ---------------------------------------------------------------------------


def test_a_copy_keeps_a_later_start_date_without_an_end_date(sc, clock):
    """Regression: the start date was copied only with an end date, so a copy of a campaign planned for later
    started at once."""
    starts = store.day(store.utcnow() + timedelta(days=10))
    made = ads.create_campaign(sc, ADVERTISER, "A", {**BASE, "starts_on": starts})
    copy = ads.duplicate_campaign(sc, ADVERTISER, made["id"])
    assert copy["starts_on"] == starts and copy["ends_on"] == ""
    ends = store.day(store.utcnow() + timedelta(days=20))
    both = ads.create_campaign(
        sc, ADVERTISER, "A", {**BASE, "starts_on": starts, "ends_on": ends}
    )
    copy = ads.duplicate_campaign(sc, ADVERTISER, both["id"])
    assert (copy["starts_on"], copy["ends_on"]) == (starts, ends)
    # a start already passed means nothing for a new draft
    ran = ads.create_campaign(sc, ADVERTISER, "A", BASE)
    sc.execute(
        "UPDATE ad_campaigns SET starts_on = '2026-01-01' WHERE id = ?", (ran["id"],)
    )
    sc.commit()
    assert ads.duplicate_campaign(sc, ADVERTISER, ran["id"])["starts_on"] == ""


def test_deleting_a_campaign_takes_its_sales_out_of_the_reports(sc, clock):
    """Regression: the sales of a deleted campaign stayed in the Reports totals, and nothing could remove them."""
    made = ads.create_campaign(sc, ADVERTISER, "A", BASE)
    kept = ads.create_campaign(sc, ADVERTISER, "A", {**BASE, "name": "Kept"})
    ads.add_sale(sc, ADVERTISER, made["id"], {"value_cents": 99900}, "me")
    ads.add_sale(sc, ADVERTISER, kept["id"], {"value_cents": 1000}, "me")
    assert ads.report(sc, ADVERTISER)["totals"]["sales_micros"] == 1009 * MICROS
    ads.delete_campaign(sc, ADVERTISER, made["id"])
    report = ads.report(sc, ADVERTISER)
    assert report["totals"]["sales_micros"] == 10 * MICROS
    assert report["totals"]["orders"] == 1
    assert [item["name"] for item in report["campaigns"]] == ["Kept"]


# ---------------------------------------------------------------------------
# on this computer: who in the household may spend, and a wrong kind of file
# ---------------------------------------------------------------------------


def test_only_an_unlocked_grown_up_runs_the_households_ads(env, api, monkeypatch):
    """Regression: the Ads routes looked only at the account, so a child's profile, or a profile with a PIN that
    was not unlocked, could read the balance, create campaigns and start them."""
    kit.forget_schema()
    db.forget()
    Clock(monkeypatch)
    household = Household(env, api)
    shop = household.owner
    campaign = shop.post("/api/ads/campaigns", BASE, expect=201)["campaign"]
    mia = household.add("Mia", age_group="child", pin="2468")
    locked = Person(env, api, mia.profile, mia.id)  # Mia chosen, her PIN not typed
    for person, code in ((locked, "profile_locked"), (mia, "")):
        for method, path, body in (
            ("GET", "/api/ads/overview", None),
            ("GET", "/api/ads/campaigns", None),
            ("GET", "/api/ads/balance", None),
            ("POST", "/api/ads/campaigns", BASE),
            (
                "POST",
                f"/api/ads/campaigns/{campaign['id']}/status",
                {"status": "active"},
            ),
            ("POST", "/api/ads/estimate", BASE),
            (
                "POST",
                "/api/ads/uploads",
                {"kind": "image", "filename": "a.png", "size": 10},
            ),
        ):
            status, data = person.call(method, path, body)
            assert status == 403, (person.header, path, status, data)
            if code:
                assert data["code"] == code
            else:
                assert "grown-up" in data["error"]
                assert (
                    data["code"] == "grown_up_only"
                )  # the screen offers to switch person
    teen = household.add("Ravi", age_group="teen")
    assert teen.call("GET", "/api/ads/overview")[0] == 403
    # another grown-up of the household runs the same account's ads, once their PIN is typed
    dad = household.add("Dad", pin="1357")
    assert (
        Person(env, api, dad.profile, dad.id).call("GET", "/api/ads/overview")[1][
            "code"
        ]
        == "profile_locked"
    )
    assert [c["id"] for c in dad.get("/api/ads/campaigns")["campaigns"]] == [
        campaign["id"]
    ]
    assert (
        len(shop.get("/api/ads/campaigns")["campaigns"]) == 1
    )  # nothing was made by the others


def test_a_wrong_kind_of_file_is_refused_at_once(env, api, monkeypatch):
    """Regression: a video chosen for a banner was refused with a 400 on every part, which the uploader retries,
    so the person waited at 'Uploading 0%' for several seconds."""
    kit.forget_schema()
    db.forget()
    shop = Household(env, api).owner
    video = make_mp4(5)
    started = shop.post(
        "/api/ads/uploads",
        {"kind": "image", "filename": "clip.mp4", "size": len(video)},
        expect=201,
    )["upload"]
    status, data = shop.call(
        "POST",
        f"/api/ads/uploads/{started['id']}/chunk",
        {
            "offset": 0,
            "data": base64.b64encode(video[: started["chunk_bytes"]]).decode(),
        },
    )
    assert status == 422 and data["code"] == "wrong_file"
    assert data["error"] == "This picture is not a JPEG, PNG or WebP image."
    assert (
        shop.call("GET", f"/api/ads/uploads/{started['id']}")[0] == 404
    )  # nothing left behind
    # the right kind of file goes through as before, and a cut-short part is still a 400 the uploader retries
    good = shop.post(
        "/api/ads/uploads",
        {"kind": "image", "filename": "a.png", "size": len(PNG)},
        expect=201,
    )["upload"]
    assert (
        shop.call(
            "POST",
            f"/api/ads/uploads/{good['id']}/chunk",
            {"offset": 5, "data": "AAAA"},
        )[0]
        == 400
    )
    done = shop.post(
        f"/api/ads/uploads/{good['id']}/chunk",
        {"offset": 0, "data": base64.b64encode(PNG).decode()},
    )["upload"]
    assert done["done"] is True
