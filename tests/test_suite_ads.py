"""Ads (aira/suite/ads): campaigns, review, serving, counting, the balance, creator earnings and payouts, in both
builds."""

from __future__ import annotations

import base64

import pytest
from cloud_support import build_org, sign_in
from life_support import Household
from videos_support import PNG, Clock, make_mp4, upload

import test_careers
from aira.suite import kit
from aira.suite.ads import service as ads
from aira.suite.ads.store import CENT, MICROS
from aira.suite.videos import db, media, service as videos, store

env = test_careers.env
api = test_careers.api
stub = test_careers.stub

ADVERTISER = "org-adv"


@pytest.fixture
def sc():
    db.forget()
    connection = db.connect()
    yield connection
    connection.close()


@pytest.fixture
def clock(monkeypatch):
    return Clock(monkeypatch)


def _person(sc, key, name, *, age="adult", home="home-1", country=""):
    ident = {
        "key": key,
        "profile": {"id": key, "name": name, "age_group": age, "prefs": {}},
        "home": home,
        "owner": False,
        "operator": False,
        "hosted": False,
    }
    me = videos.viewer(sc, ident)
    if country:
        videos.update_channel(sc, me, {"country": country})
        me = videos.viewer(sc, ident)
    return me


def _file(sc, owner, home, data, kind="video"):
    started = media.start(sc, owner, home, kind, f"x.{kind}", len(data))
    media.append(sc, started["id"], owner, 0, base64.b64encode(data).decode())
    return media.complete(sc, started["id"], owner)["id"]


def _monetized_creator(sc, *, home="home-creator"):
    creator = _person(sc, "p:cara", "Cara", home=home)
    video = videos.create_video(
        sc,
        creator,
        {
            "title": "Cooking dal",
            "category": "food",
            "file_id": _file(sc, creator["id"], home, make_mp4(300, 1280, 720)),
        },
    )
    ads.accept_terms(sc, creator)
    ads.set_monetized(sc, videos.channel(sc, creator["id"]), True)
    return videos.viewer_of(videos.channel(sc, creator["id"])), store.row(
        sc, "SELECT * FROM vi_videos WHERE id = ?", (video["id"],)
    )


def _campaign(
    sc,
    *,
    home=ADVERTISER,
    objective="views",
    bid_cents=200,
    total_cents=5000,
    daily_cents=2000,
    kind="preroll",
    approve=True,
    **raw,
):
    found = ads.create_campaign(
        sc,
        home,
        "Spice Shop",
        {
            "name": "Spices",
            "objective": objective,
            "bid_cents": bid_cents,
            "total_budget_cents": total_cents,
            "daily_budget_cents": daily_cents,
            "landing_url": "https://spices.example/sale",
            **raw,
        },
    )
    data = (
        PNG if kind == "banner" else make_mp4(15, 720, 1280 if kind == "reel" else 720)
    )
    file_id = _file(
        sc, ads.owner_key(home), home, data, "image" if kind == "banner" else "video"
    )
    creative = ads.create_creative(
        sc,
        home,
        found["id"],
        {
            "kind": kind,
            "headline": "Fresh spices",
            "file_id": file_id,
            "cta": "Shop now",
        },
    )
    if approve:
        ads.review(sc, creative["id"], "approve", "", "aira:test")
    ads.set_status(sc, home, found["id"], "active")
    return ads.campaign(sc, home, found["id"]), creative


def _watch_ad(sc, clock, viewer, video, slot="preroll"):
    served = ads.serve(sc, viewer, video, slot)
    if served is None:
        return None, None
    clock.advance(seconds=3)
    return served, ads.seen(sc, viewer, served["impression_id"])


# ---------------------------------------------------------------------------
# campaigns and review
# ---------------------------------------------------------------------------


def test_campaigns_are_validated(sc):
    base = {
        "name": "X",
        "objective": "views",
        "bid_cents": 200,
        "total_budget_cents": 5000,
        "daily_budget_cents": 1000,
        "landing_url": "https://x.example",
    }
    with pytest.raises(ValueError, match="between"):
        ads.create_campaign(sc, ADVERTISER, "A", {**base, "bid_cents": 10})
    with pytest.raises(ValueError, match="at least \\$5.00"):
        ads.create_campaign(
            sc,
            ADVERTISER,
            "A",
            {**base, "total_budget_cents": 100, "daily_budget_cents": 100},
        )
    with pytest.raises(ValueError, match="cannot be more"):
        ads.create_campaign(sc, ADVERTISER, "A", {**base, "daily_budget_cents": 6000})
    with pytest.raises(ValueError, match="https"):
        ads.create_campaign(
            sc, ADVERTISER, "A", {**base, "landing_url": "http://x.example"}
        )
    with pytest.raises(ValueError, match="two-letter"):
        ads.create_campaign(sc, ADVERTISER, "A", {**base, "countries": "India"})
    with pytest.raises(ValueError, match="categories"):
        ads.create_campaign(sc, ADVERTISER, "A", {**base, "categories": ["casinos"]})
    made = ads.create_campaign(
        sc,
        ADVERTISER,
        "A",
        {
            **base,
            "objective": "clicks",
            "bid_cents": 30,
            "countries": "in, us",
            "categories": "food,travel",
        },
    )
    assert (
        made["bid_type"] == "cpc"
        and made["countries"] == ["IN", "US"]
        and made["categories"] == ["food", "travel"]
    )
    with pytest.raises(ValueError, match="Add an ad"):
        ads.set_status(sc, ADVERTISER, made["id"], "active")
    with pytest.raises(LookupError):
        ads.campaign(sc, "someone-else", made["id"])


def test_ads_run_only_after_review_and_a_change_is_reviewed_again(sc, clock):
    creator, video = _monetized_creator(sc)
    viewer = _person(sc, "p:vic", "Vic", home="home-viewer")
    ads.credit(sc, ADVERTISER, 20 * MICROS, kind="payment", reference="cash")
    campaign, creative = _campaign(sc, approve=False)
    assert ads.serve(sc, viewer, video, "preroll") is None
    with pytest.raises(ValueError, match="why"):
        ads.review(sc, creative["id"], "reject", "", "aira")
    assert [c["id"] for c in ads.review_queue(sc)] == [creative["id"]]
    ads.review(sc, creative["id"], "approve", "", "aira")
    assert ads.serve(sc, viewer, video, "preroll")["headline"] == "Fresh spices"
    changed = ads.update_creative(
        sc, ADVERTISER, creative["id"], {"headline": "Free delivery"}
    )
    assert changed["review_status"] == "pending"
    assert ads.serve(sc, viewer, video, "preroll") is None
    del campaign, creator


# ---------------------------------------------------------------------------
# serving
# ---------------------------------------------------------------------------


def test_the_highest_effective_bid_wins_within_targeting(sc, clock):
    _creator, video = _monetized_creator(sc)
    viewer = _person(sc, "p:vic", "Vic", home="home-viewer", country="IN")
    for home in (ADVERTISER, "org-b", "org-c", "org-d"):
        ads.credit(sc, home, 50 * MICROS, kind="payment", reference="x")
    low, _ = _campaign(sc, home=ADVERTISER, bid_cents=200)
    high, _ = _campaign(sc, home="org-b", bid_cents=500)
    elsewhere, _ = _campaign(sc, home="org-c", bid_cents=900, countries="US")
    wrong_topic, _ = _campaign(sc, home="org-d", bid_cents=900, categories="gaming")
    served = ads.serve(sc, viewer, video, "preroll")
    impression = store.row(
        sc, "SELECT * FROM ad_impressions WHERE id = ?", (served["impression_id"],)
    )
    assert (
        impression["campaign_id"] == high["id"] and served["advertiser"] == "Spice Shop"
    )
    assert served["skip_after"] == 5 and served["click_url"].startswith(
        "/api/videos/ads/go/"
    )
    # a CPC campaign competes on bid x click rate x 1000: $0.30 x 2% x 1000 = $6 a thousand beats $5
    ads.credit(sc, "org-e", 50 * MICROS, kind="payment", reference="x")
    clicks, _ = _campaign(sc, home="org-e", objective="clicks", bid_cents=30)
    assert (
        store.row(
            sc,
            "SELECT campaign_id FROM ad_impressions WHERE id = ?",
            (ads.serve(sc, viewer, video, "preroll")["impression_id"],),
        )["campaign_id"]
        == clicks["id"]
    )
    del low, elsewhere, wrong_topic


def test_children_never_see_ads_and_family_videos_and_teens_get_family_safe_ones(
    sc, clock
):
    creator, video = _monetized_creator(sc)
    ads.credit(sc, ADVERTISER, 50 * MICROS, kind="payment", reference="x")
    _campaign(sc)
    child = _person(sc, "p:kid", "Kid", age="child", home="home-viewer")
    teen = _person(sc, "p:teen", "Teen", age="teen", home="home-viewer")
    adult = _person(sc, "p:vic", "Vic", home="home-viewer")
    assert ads.serve(sc, child, video, "preroll") is None
    assert ads.serve(sc, teen, video, "preroll") is None
    kids = videos.create_video(
        sc,
        creator,
        {
            "title": "Counting song",
            "for_kids": True,
            "file_id": _file(sc, creator["id"], creator["home"], make_mp4()),
        },
    )
    kids_row = store.row(sc, "SELECT * FROM vi_videos WHERE id = ?", (kids["id"],))
    assert ads.serve(sc, adult, kids_row, "preroll") is None
    ads.credit(sc, "org-safe", 50 * MICROS, kind="payment", reference="x")
    _campaign(sc, home="org-safe", family_safe=True)
    assert ads.serve(sc, teen, video, "preroll") is not None
    assert ads.serve(sc, adult, kids_row, "preroll") is not None
    mature = videos.create_video(
        sc,
        creator,
        {
            "title": "Horror",
            "rating": "mature",
            "file_id": _file(sc, creator["id"], creator["home"], make_mp4()),
        },
    )
    assert (
        ads.serve(
            sc,
            adult,
            store.row(sc, "SELECT * FROM vi_videos WHERE id = ?", (mature["id"],)),
            "preroll",
        )
        is None
    )


def test_ads_only_run_on_monetized_channels_and_not_for_the_creator(sc, clock):
    creator = _person(sc, "p:cara", "Cara", home="home-creator")
    video = videos.create_video(
        sc,
        creator,
        {
            "title": "Dal",
            "file_id": _file(sc, creator["id"], "home-creator", make_mp4()),
        },
    )
    row = store.row(sc, "SELECT * FROM vi_videos WHERE id = ?", (video["id"],))
    ads.credit(sc, ADVERTISER, 50 * MICROS, kind="payment", reference="x")
    _campaign(sc)
    viewer = _person(sc, "p:vic", "Vic", home="home-viewer")
    assert ads.serve(sc, viewer, row, "preroll") is None
    state = ads.eligibility(sc, creator)
    assert not state["eligible"] and [
        c["id"] for c in state["checks"] if not c["done"]
    ] == ["terms"]
    with pytest.raises(PermissionError, match="creator terms"):
        ads.set_monetized(sc, creator, True)
    ads.accept_terms(sc, creator)
    ads.set_monetized(sc, videos.channel(sc, creator["id"]), True)
    assert ads.serve(sc, viewer, row, "preroll") is not None
    me = videos.viewer_of(videos.channel(sc, creator["id"]))
    assert (
        ads.serve(sc, me, row, "preroll") is None
    )  # nobody earns from watching their own video
    with pytest.raises(PermissionError, match="adults"):
        ads.accept_terms(sc, _person(sc, "p:teo", "Teo", age="teen", home="home-x"))


def test_frequency_cap_and_pacing(sc, clock):
    _creator, video = _monetized_creator(sc)
    viewer = _person(sc, "p:vic", "Vic", home="home-viewer")
    ads.credit(sc, ADVERTISER, 50 * MICROS, kind="payment", reference="x")
    _campaign(sc, frequency_cap=2)
    assert ads.serve(sc, viewer, video, "preroll") and ads.serve(
        sc, viewer, video, "preroll"
    )
    assert ads.serve(sc, viewer, video, "preroll") is None
    clock.now = clock.now.replace(
        hour=0, minute=10
    )  # just after midnight: an hour of the daily budget at most
    other = _person(sc, "p:pace", "Pace", home="home-pace")
    ads.credit(sc, "org-pace", 50 * MICROS, kind="payment", reference="x")
    paced, _ = _campaign(
        sc,
        home="org-pace",
        bid_cents=10000,
        daily_cents=2400,
        total_cents=5000,
        frequency_cap=20,
    )
    for n in range(20):
        _watch_ad(sc, clock, _person(sc, f"p:v{n}", f"V{n}", home=f"home-v{n}"), video)
    counted = store.scalar(
        sc,
        "SELECT COUNT(*) FROM ad_impressions WHERE campaign_id = ? AND status = 'counted'",
        (paced["id"],),
    )
    assert (
        1 <= counted <= 12
    )  # $100 CPM is 10 cents each; $24 a day allows about $1.17 by 00:11
    assert (
        store.scalar(
            sc,
            "SELECT COUNT(*) FROM ad_impressions WHERE campaign_id != ? AND status = 'counted'",
            (paced["id"],),
        )
        > 0
    )
    assert ads.campaign(sc, "org-pace", paced["id"])[
        "spent_today_micros"
    ] <= 2400 * CENT * (1 / 24 + 0.01)
    del other


# ---------------------------------------------------------------------------
# counting, charging, earning
# ---------------------------------------------------------------------------


def test_a_counted_impression_charges_the_advertiser_and_credits_the_creator(sc, clock):
    creator, video = _monetized_creator(sc)
    viewer = _person(sc, "p:vic", "Vic", home="home-viewer")
    ads.credit(sc, ADVERTISER, 10 * MICROS, kind="payment", reference="x")
    campaign, _ = _campaign(sc, bid_cents=200)  # $2 CPM: 2000 micros an impression
    served = ads.serve(sc, viewer, video, "preroll")
    assert ads.seen(sc, viewer, served["impression_id"]) == {
        "counted": False,
        "reason": "too_soon",
    }
    clock.advance(seconds=2.5)
    assert ads.seen(sc, viewer, served["impression_id"]) == {
        "counted": True,
        "cost_micros": 2000,
    }
    assert (
        ads.seen(sc, viewer, served["impression_id"])["counted"] is True
    )  # idempotent
    impression = store.row(
        sc, "SELECT * FROM ad_impressions WHERE id = ?", (served["impression_id"],)
    )
    assert (
        impression["creator_micros"] == 1100
        and impression["earning_status"] == "estimated"
    )  # 55%
    assert ads.account(sc, ADVERTISER)["balance_micros"] == 10 * MICROS - 2000
    earned = ads.earnings(sc, creator)
    assert (
        earned["today_micros"] == 1100
        and earned["balances"]["estimated_micros"] == 1100
    )
    assert earned["balances"]["payable_micros"] == 0
    assert earned["videos"][0]["impressions"] == 1
    totals = ads.totals(sc)
    assert (
        totals["gross_micros"],
        totals["creators_micros"],
        totals["platform_micros"],
    ) == (2000, 1100, 900)
    with pytest.raises(LookupError):
        ads.seen(
            sc, _person(sc, "p:other", "Other", home="home-o"), served["impression_id"]
        )
    del campaign


def test_invalid_traffic_does_not_count_or_earn(sc, clock):
    creator, video = _monetized_creator(sc)
    ads.credit(sc, ADVERTISER, 10 * MICROS, kind="payment", reference="x")
    _campaign(sc, frequency_cap=5)
    colleague = _person(
        sc, "p:col", "Colleague", home=ADVERTISER
    )  # the advertiser's own people
    _served, result = _watch_ad(sc, clock, colleague, video)
    assert result == {"counted": False, "reason": "own_ads"}
    housemate = _person(
        sc, "p:house", "Housemate", home=creator["home"]
    )  # the creator's own household
    _served, result = _watch_ad(sc, clock, housemate, video)
    assert result == {"counted": False, "reason": "own_video"}
    viewer = _person(sc, "p:vic", "Vic", home="home-viewer")
    late = ads.serve(sc, viewer, video, "preroll")
    clock.advance(minutes=31)
    assert ads.seen(sc, viewer, late["impression_id"]) == {
        "counted": False,
        "reason": "late",
    }
    assert ads.account(sc, ADVERTISER)["balance_micros"] == 10 * MICROS
    assert ads.earnings(sc, creator)["balances"]["estimated_micros"] == 0
    report = ads.report(sc, ADVERTISER)
    assert {item["reason"] for item in report["invalid"]} == {
        "own_ads",
        "own_video",
        "late",
    }


def test_campaigns_pause_at_zero_and_resume_after_a_top_up(sc, clock):
    _creator, video = _monetized_creator(sc)
    ads.credit(
        sc, ADVERTISER, 3 * 10_000, kind="payment", reference="three cents"
    )  # 1.5 impressions at $20 CPM
    campaign, _ = _campaign(sc, bid_cents=2000, frequency_cap=10)
    viewers = [_person(sc, f"p:v{n}", f"V{n}", home=f"home-v{n}") for n in range(3)]
    _served, result = _watch_ad(sc, clock, viewers[0], video)
    assert result["counted"] is True
    assert ads.campaign(sc, ADVERTISER, campaign["id"])["status"] == "paused"
    assert ads.campaign(sc, ADVERTISER, campaign["id"])["paused_reason"] == "no_balance"
    assert ads.serve(sc, viewers[1], video, "preroll") is None
    ads.credit(sc, ADVERTISER, 5 * MICROS, kind="topup", reference="ii_1")
    assert ads.campaign(sc, ADVERTISER, campaign["id"])["status"] == "active"
    _served, result = _watch_ad(sc, clock, viewers[1], video)
    assert result["counted"] is True


def test_a_spent_total_budget_ends_the_campaign(sc, clock):
    _creator, video = _monetized_creator(sc)
    ads.credit(sc, ADVERTISER, 100 * MICROS, kind="payment", reference="x")
    campaign, _ = _campaign(
        sc, bid_cents=10000, total_cents=500, daily_cents=500
    )  # $5 total, 10 cents each
    clock.now = clock.now.replace(hour=23)
    for n in range(60):
        _watch_ad(sc, clock, _person(sc, f"p:b{n}", f"B{n}", home=f"home-b{n}"), video)
    found = ads.campaign(sc, ADVERTISER, campaign["id"])
    assert found["status"] == "ended" and found["spent_micros"] <= 5 * MICROS


def test_clicks_go_through_a_signed_redirect_and_cpc_is_charged_once(sc, clock):
    creator, video = _monetized_creator(sc)
    viewer = _person(sc, "p:vic", "Vic", home="home-viewer")
    ads.credit(sc, ADVERTISER, 10 * MICROS, kind="payment", reference="x")
    _campaign(sc, objective="clicks", bid_cents=50, kind="banner")
    served, result = _watch_ad(sc, clock, viewer, video, "banner")
    assert result == {
        "counted": True,
        "cost_micros": 0,
    }  # a CPC ad's impression is free
    impression_id = served["impression_id"]
    signature = served["click_url"].split("s=")[1]
    with pytest.raises(LookupError):
        ads.click(sc, impression_id, "0" * 32)
    assert ads.click(sc, impression_id, signature) == "https://spices.example/sale"
    assert ads.click(sc, impression_id, signature) == "https://spices.example/sale"
    assert ads.account(sc, ADVERTISER)["balance_micros"] == 10 * MICROS - 50 * CENT
    assert (
        ads.earnings(sc, creator)["balances"]["estimated_micros"]
        == 50 * CENT * 55 // 100
    )
    # a second ad to the same viewer the same day: the click is recorded, not charged again
    second, _ = _watch_ad(sc, clock, viewer, video, "banner")
    ads.click(sc, second["impression_id"], second["click_url"].split("s=")[1])
    assert ads.account(sc, ADVERTISER)["balance_micros"] == 10 * MICROS - 50 * CENT
    stats = ads.report(sc, ADVERTISER)["totals"]
    assert (
        stats["clicks"] == 1
        and stats["impressions"] == 2
        and stats["ctr_percent"] == 50.0
    )


def test_month_close_reviews_traffic_then_earnings_become_payable(sc, clock):
    creator, video = _monetized_creator(sc)
    ads.credit(sc, ADVERTISER, 100 * MICROS, kind="payment", reference="x")
    _campaign(
        sc, bid_cents=10000, total_cents=10000, daily_cents=10000, frequency_cap=20
    )
    clock.now = clock.now.replace(month=9, day=10, hour=22)
    heavy = _person(sc, "p:heavy", "Heavy", home="home-heavy")
    for _ in range(20):
        _watch_ad(sc, clock, heavy, video)
    for n in range(80):
        _watch_ad(sc, clock, _person(sc, f"p:m{n}", f"M{n}", home=f"home-m{n}"), video)
    before = ads.earnings(sc, creator)["balances"]
    assert before["estimated_micros"] > 0 and before["payable_micros"] == 0
    with pytest.raises(ValueError, match="once it is over"):
        ads.close_month(sc, store.month(), "aira")
    ads.REVIEW_DAILY_CAP = (
        15  # the heavy viewer gave the channel 20 in one day: 5 are taken back
    )
    try:
        clock.now = clock.now.replace(month=10, day=2)
        closed = ads.close_month(sc, f"{clock.now.year}-09", "aira")
    finally:
        ads.REVIEW_DAILY_CAP = 20
    assert closed["invalid"] == 5 and closed["refunded_micros"] == 5 * 10 * CENT
    after = ads.earnings(sc, creator)["balances"]
    assert (
        after["estimated_micros"] == 0
        and after["confirmed_micros"] == before["estimated_micros"] - 5 * 55_000
    )
    assert after["payable_micros"] == after["confirmed_micros"]
    with pytest.raises(ValueError, match="already closed"):
        ads.close_month(sc, closed["month"], "aira")
    # payouts: above the minimum, within what is payable, one at a time
    with pytest.raises(ValueError, match=r"smallest payout is \$10\.00"):
        ads.request_payout(sc, creator, {"details": "UPI cara@bank"})
    ads.save_settings(sc, {"min_payout_cents": 100})
    with pytest.raises(ValueError, match="smallest payout"):
        ads.request_payout(
            sc, creator, {"amount_cents": 50, "details": "UPI cara@bank"}
        )
    with pytest.raises(ValueError, match="up to"):
        ads.request_payout(
            sc, creator, {"amount_cents": 10**6, "details": "UPI cara@bank"}
        )
    payout = ads.request_payout(sc, creator, {"details": "UPI cara@bank"})
    assert (
        payout["amount_micros"] == after["payable_micros"]
        and payout["status"] == "requested"
    )
    with pytest.raises(ValueError, match="already asked"):
        ads.request_payout(sc, creator, {"details": "UPI cara@bank"})
    with pytest.raises(ValueError, match="reference"):
        ads.decide_payout(sc, payout["id"], "paid", "", "", "aira")
    paid = ads.decide_payout(sc, payout["id"], "paid", "UTR-123", "", "aira:ops")
    assert (
        paid["status"] == "paid"
        and ads.balances(sc, creator["id"])["payable_micros"] == 0
    )
    assert videos.notifications(sc, creator)["notifications"][0]["kind"] == "payout"
    totals = ads.totals(sc)
    assert (
        totals["paid_micros"] == payout["amount_micros"]
        and totals["refunded_micros"] == 5 * 10 * CENT
    )


def test_a_clawback_refunds_advertisers_and_takes_the_earnings_back(sc, clock):
    creator, video = _monetized_creator(sc)
    ads.credit(sc, ADVERTISER, 10 * MICROS, kind="payment", reference="x")
    _campaign(sc, bid_cents=1000, frequency_cap=5)
    for n in range(4):
        _watch_ad(sc, clock, _person(sc, f"p:c{n}", f"C{n}", home=f"home-c{n}"), video)
    spent = 10 * MICROS - ads.account(sc, ADVERTISER)["balance_micros"]
    assert spent == 4 * 10_000
    result = ads.clawback(sc, creator["id"], store.month(), "Bought views", "aira")
    assert result["impressions"] == 4 and result["refunded_micros"] == spent
    assert ads.account(sc, ADVERTISER)["balance_micros"] == 10 * MICROS
    assert ads.earnings(sc, creator)["balances"]["estimated_micros"] == 0
    assert ads.earnings(sc, creator)["months"][0]["clawed_micros"] == 4 * 5_500


def test_settings_change_the_share_and_the_minimum(sc, clock):
    creator, video = _monetized_creator(sc)
    changed = ads.save_settings(
        sc, {"revenue_share_percent": 70, "min_payout_cents": 2500}
    )
    assert (
        changed["before"]["revenue_share_percent"] == 55
        and changed["after"]["min_payout_micros"] == 25 * MICROS
    )
    with pytest.raises(ValueError):
        ads.save_settings(sc, {"revenue_share_percent": 95})
    ads.credit(sc, ADVERTISER, 10 * MICROS, kind="payment", reference="x")
    _campaign(sc, bid_cents=1000)
    _watch_ad(sc, clock, _person(sc, "p:vic", "Vic", home="home-viewer"), video)
    assert ads.earnings(sc, creator)["balances"]["estimated_micros"] == 7_000


def test_erasing_an_advertiser_keeps_the_creators_earnings(sc, clock):
    creator, video = _monetized_creator(sc)
    ads.credit(sc, ADVERTISER, 10 * MICROS, kind="payment", reference="x")
    _campaign(sc, bid_cents=1000)
    _watch_ad(sc, clock, _person(sc, "p:vic", "Vic", home="home-viewer"), video)
    assert ads.erase_advertiser(sc, ADVERTISER) == 1
    assert (
        store.scalar(
            sc, "SELECT COUNT(*) FROM ad_campaigns WHERE home = ?", (ADVERTISER,)
        )
        == 0
    )
    assert (
        store.scalar(
            sc,
            "SELECT COUNT(*) FROM vi_files WHERE owner_key = ?",
            (ads.owner_key(ADVERTISER),),
        )
        == 0
    )
    assert ads.earnings(sc, creator)["balances"]["estimated_micros"] == 5_500


# ---------------------------------------------------------------------------
# the single-user server: three accounts on one computer
# ---------------------------------------------------------------------------


def _register(env, email):
    client = env.api()
    status, _ = client.call(
        "POST",
        "/api/auth/register",
        {"email": email, "password": test_careers.PASSWORD},
    )
    assert status == 201
    return Household(env, client).owner


def test_an_ad_on_this_computer_from_campaign_to_payout(env, api, monkeypatch):
    kit.forget_schema()
    db.forget()
    clock = Clock(monkeypatch)
    shop = Household(
        env, api
    ).owner  # the first account: an advertiser and the computer's owner
    creator = _register(env, "cara@example.com")
    viewer = _register(env, "vic@example.com")
    shop.post("/api/suite/business", {"name": "Spice Shop"})
    # the creator uploads and turns on earning
    video = creator.post(
        "/api/videos/videos",
        {
            "file_id": upload(creator, "video", make_mp4(300, 1280, 720)),
            "title": "Dal",
            "category": "food",
        },
        expect=201,
    )["video"]
    creator.post("/api/videos/earnings/terms", {})
    assert (
        creator.post("/api/videos/earnings/monetize", {"on": True})["eligibility"][
            "monetized"
        ]
        is True
    )
    # the shop buys a campaign with a pre-roll
    overview = shop.get("/api/ads/overview")
    assert overview["advertiser"] == "Spice Shop" and overview["hosted"] is False
    campaign = shop.post(
        "/api/ads/campaigns",
        {
            "name": "Autumn",
            "objective": "views",
            "bid_cents": 1000,
            "total_budget_cents": 2000,
            "daily_budget_cents": 2000,
            "landing_url": "https://spices.example",
            "categories": ["food"],
        },
        expect=201,
    )["campaign"]
    ad_file = upload(shop, "video", make_mp4(10), base="/api/ads")
    creative = shop.post(
        f"/api/ads/campaigns/{campaign['id']}/creatives",
        {"kind": "preroll", "headline": "Fresh spices", "file_id": ad_file},
        expect=201,
    )["creative"]
    assert (
        shop.post(f"/api/ads/campaigns/{campaign['id']}/status", {"status": "active"})[
            "campaign"
        ]["paused_reason"]
        == "no_balance"
    )
    # the computer's owner reviews it and records the shop's payment; other accounts cannot
    assert creator.call("GET", "/api/ads/admin")[0] == 403
    admin = shop.get("/api/ads/admin")
    assert [c["id"] for c in admin["review"]] == [creative["id"]]
    shop.post(
        f"/api/ads/admin/creatives/{creative['id']}/review", {"decision": "approve"}
    )
    shop.post(
        "/api/ads/admin/payments",
        {"amount_cents": 2000, "reference": "cash 25 Oct"},
        expect=201,
    )
    assert (
        shop.get(f"/api/ads/campaigns/{campaign['id']}")["campaign"]["status"]
        == "active"
    )
    # the viewer watches: the ad is served before the video, counts after 2 seconds, and pays the creator
    served = viewer.post(
        "/api/videos/ads/serve", {"video_id": video["id"], "slot": "preroll"}
    )["ad"]
    assert served["headline"] == "Fresh spices"
    clock.advance(seconds=3)
    assert (
        viewer.post(f"/api/videos/ads/{served['impression_id']}/seen", {})["counted"]
        is True
    )
    assert _redirect_status(viewer, served["click_url"]) == (
        302,
        "https://spices.example",
    )
    assert (
        creator.get("/api/videos/earnings")["today_micros"] == 5_500
    )  # 55% of 1 cent ($10 per thousand)
    report = shop.get("/api/ads/reports")
    assert (
        report["totals"]["impressions"] == 1
        and report["totals"]["spend_micros"] == 10_000
    )
    status, csv = shop.call("GET", "/api/ads/reports?format=csv", raw=True)
    assert status == 200 and csv.startswith(b"day,impressions,clicks")
    # the hub adds up the shop's ads; the creator's Today shows what they earned
    hub = shop.get("/api/suite/home")
    ads_app = next(item for item in hub["apps"] if item["id"] == "ads")
    assert {"label": "Ad spend this month", "value": "$0.01"} in ads_app["cards"]
    today = creator.get("/api/life/today")
    card = next(item for item in today["cards"] if item["app"] == "videos")
    assert card["lines"][0]["text"] == "Earned today: $0.0055"
    # the month closes, the creator asks to be paid, the owner pays; the payout becomes the creator's revenue
    shop.ok("PUT", "/api/ads/admin/settings", {"min_payout_cents": 100})
    from contextlib import closing

    with closing(db.connect()) as sc:
        sc.execute(
            "UPDATE ad_impressions SET earning_status = 'confirmed', creator_micros = 2000000"
        )
        sc.commit()
    payout = creator.post(
        "/api/videos/earnings/payouts", {"details": "UPI cara@okbank"}, expect=201
    )["payout"]
    shop.post(
        f"/api/ads/admin/payouts/{payout['id']}",
        {"action": "paid", "reference": "UPI-778"},
    )
    creator.get("/api/videos/earnings")
    revenue = creator.get("/api/suite/revenue")
    assert (
        revenue["lines"][0]["label"] == "Creator payout from Videos"
        and revenue["lines"][0]["amount_cents"] == 200
    )


def _redirect_status(person, path):
    import http.client
    from urllib.parse import urlsplit

    parts = urlsplit(person.env.base)
    connection = http.client.HTTPConnection(parts.hostname, parts.port, timeout=30)
    cookies = "; ".join(f"{c.name}={c.value}" for c in _jar(person.api))
    connection.request("GET", path, headers={"Cookie": cookies})
    response = connection.getresponse()
    return response.status, response.getheader("Location")


def _jar(api):
    for handler in api.opener.handlers:
        jar = getattr(handler, "cookiejar", None)
        if jar is not None:
            return list(jar)
    return []


# ---------------------------------------------------------------------------
# the hosted build
# ---------------------------------------------------------------------------


def test_hosted_ads_top_up_through_billing_and_operators_run_the_money(
    cloud, monkeypatch
):
    from test_cloud_ui_billing import operator_browser

    kit.forget_schema()
    db.forget()
    clock = Clock(monkeypatch)
    shop = build_org(cloud, "Spice Shop Ltd", slug_email="shop.test")
    owner = sign_in(cloud, shop.owner)
    assert owner.call("GET", "/api/ads/overview")[1]["code"] == "app_required"
    status, refused = owner.call("POST", "/api/org/ads/topup", {"amount_cents": 2000})
    assert status == 402 and refused["code"] == "app_required"
    cloud.billing.subscribe_app(shop.owner, "ads")
    assert (
        owner.call("GET", "/api/ads/admin")[1]["code"] == "not_available"
    )  # the console does this hosted
    candidate = sign_in(cloud, shop.candidate)
    assert (
        candidate.call("GET", "/api/ads/overview")[0] == 403
    )  # a business app: not for candidates
    admin = sign_in(cloud, shop.admin)
    assert (
        admin.call("POST", "/api/org/ads/topup", {"amount_cents": 2000})[0] == 403
    )  # money is the owner's
    assert owner.call("POST", "/api/org/ads/topup", {"amount_cents": 100})[0] == 400
    status, topped = owner.call("POST", "/api/org/ads/topup", {"amount_cents": 2000})
    assert (
        status == 200
        and topped["balance_micros"] == 20 * MICROS
        and topped["invoice_item"].startswith("ii_standin_")
    )
    invoices = owner.call("GET", "/api/org/billing")[1]
    assert any(
        item["kind"] == "ads_topup" and item["amount_cents"] == 2000
        for item in invoices.get("invoices", [])
    )
    balance = owner.call("GET", "/api/ads/balance")[1]
    assert balance["hosted"] is True and balance["ledger"][0]["kind"] == "topup"
    campaign = owner.call(
        "POST",
        "/api/ads/campaigns",
        {
            "name": "Autumn",
            "objective": "views",
            "bid_cents": 1000,
            "total_budget_cents": 2000,
            "daily_budget_cents": 2000,
            "landing_url": "https://spices.example",
        },
    )[1]["campaign"]
    ad_file = upload(owner, "image", PNG, base="/api/ads")
    creative = owner.call(
        "POST",
        f"/api/ads/campaigns/{campaign['id']}/creatives",
        {"kind": "banner", "headline": "Fresh spices", "file_id": ad_file},
    )[1]["creative"]
    owner.call(
        "POST", f"/api/ads/campaigns/{campaign['id']}/status", {"status": "active"}
    )
    # a creator in another organization
    studio = build_org(cloud, "Cara Studio", slug_email="cara.test")
    cloud.billing.subscribe_app(studio.owner, "videos")
    cara = sign_in(cloud, studio.owner)
    video = cara.call(
        "POST",
        "/api/videos/videos",
        {"file_id": upload(cara, "video", make_mp4()), "title": "Dal"},
    )[1]["video"]
    cara.call("POST", "/api/videos/earnings/terms", {})
    assert cara.call("POST", "/api/videos/earnings/monetize", {"on": True})[0] == 200
    # operators review the ad, then a viewer of a third organization sees it on Cara's video
    support = operator_browser(cloud, "support@aira.test", role="support")
    queue = support.call("GET", "/api/platform/ads/review")[1]["creatives"]
    assert [item["id"] for item in queue] == [creative["id"]] and queue[0][
        "media_url"
    ].startswith("/api/platform/ads/media/")
    status, picture = support.call("GET", queue[0]["media_url"], None, raw=True)
    assert status == 200 and picture == PNG
    assert (
        support.call(
            "POST",
            f"/api/platform/ads/creatives/{creative['id']}/review",
            {"decision": "approve"},
        )[0]
        == 200
    )
    viewers = build_org(cloud, "Viewer Org", slug_email="viewer.test")
    cloud.billing.subscribe_app(viewers.owner, "videos")
    vic = sign_in(cloud, viewers.candidate)
    served = vic.call(
        "POST", "/api/videos/ads/serve", {"video_id": video["id"], "slot": "banner"}
    )[1]["ad"]
    assert served["headline"] == "Fresh spices"
    clock.advance(seconds=3)
    assert (
        vic.call("POST", f"/api/videos/ads/{served['impression_id']}/seen", {})[1][
            "counted"
        ]
        is True
    )
    status, _body = vic.call("GET", served["click_url"], None, raw=True)
    assert status == 302 and vic.header("Location") == "https://spices.example"
    assert cara.call("GET", "/api/videos/earnings")[1]["today_micros"] == 5_500
    # support reads the books but cannot change the money; a platform admin can
    overview = support.call("GET", "/api/platform/ads/overview")[1]
    assert (
        overview["totals"]["gross_micros"] == 10_000
        and overview["can_manage_money"] is False
    )
    assert (
        support.call(
            "PUT", "/api/platform/ads/settings", {"revenue_share_percent": 60}
        )[0]
        == 403
    )
    ops = operator_browser(cloud)
    assert (
        ops.call(
            "PUT",
            "/api/platform/ads/settings",
            {"revenue_share_percent": 60, "min_payout_cents": 100},
        )[1]["settings"]["revenue_share_percent"]
        == 60
    )
    from contextlib import closing

    with closing(db.connect()) as sc:
        sc.execute(
            "UPDATE ad_impressions SET earning_status = 'confirmed', creator_micros = 1500000"
        )
        sc.commit()
    payout = cara.call(
        "POST", "/api/videos/earnings/payouts", {"details": "IBAN DE00 1234"}
    )[1]["payout"]
    queue = ops.call("GET", "/api/platform/ads/overview")[1]["payouts"]
    assert [item["id"] for item in queue] == [payout["id"]]
    assert (
        ops.call(
            "POST",
            f"/api/platform/ads/payouts/{payout['id']}",
            {"action": "paid", "reference": "SEPA-1"},
        )[1]["payout"]["status"]
        == "paid"
    )
    assert (
        ops.call("POST", "/api/platform/ads/close-month", {"month": "1999-01"})[0]
        == 200
    )
    actions = [row["action"] for row in cloud.platform_audit.list(limit=50)]
    assert {
        "ads.review_approved",
        "ads.settings",
        "ads.payout_paid",
        "ads.month_close",
    } <= set(actions)
    assert "billing.ads_topup" in [
        row["action"] for row in cloud.audit.list(shop.org_id, limit=100)
    ]
    # the creator's payout is theirs, not the organization's revenue
    assert cara.call("GET", "/api/suite/revenue")[0] in (200, 403)
    # deleting the advertiser's organization removes its ads but keeps what creators earned
    from aira_cloud.hooks import erase_videos

    erase_videos(shop.org_id)
    with closing(db.connect()) as sc:
        assert sc.execute("SELECT COUNT(*) FROM ad_campaigns").fetchone()[0] == 0
        assert (
            ads.balances(sc, video["channel"]["id"])["paid_micros"]
            == payout["amount_micros"]
        )


def test_hosted_top_up_needs_a_paid_subscription(cloud):
    kit.forget_schema()
    db.forget()
    from cloud_support import signup

    trial = signup(cloud, "Trial Shop", "owner@trial.test")
    owner = sign_in(cloud, trial["owner"])
    status, refused = owner.call("POST", "/api/org/ads/topup", {"amount_cents": 2000})
    assert status == 409 and refused["code"] == "not_subscribed"
