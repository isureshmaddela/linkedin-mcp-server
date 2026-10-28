"""Ads: the list of ads the search across the top looks through (GET /api/ads/creatives), on this computer."""

from __future__ import annotations

from life_support import Household
from test_suite_ads_more import BASE
from videos_support import PNG, Clock, upload

import test_careers
from aira.suite import kit
from aira.suite.videos import db

env = test_careers.env
api = test_careers.api
stub = test_careers.stub


def test_the_search_lists_the_advertisers_own_ads_with_their_campaign(
    env, api, monkeypatch
):
    kit.forget_schema()
    db.forget()
    Clock(monkeypatch)
    household = Household(env, api)
    shop = household.owner
    cakes = shop.post(
        "/api/ads/campaigns", {**BASE, "name": "Wedding cakes"}, expect=201
    )["campaign"]
    gone = shop.post("/api/ads/campaigns", {**BASE, "name": "Old offer"}, expect=201)[
        "campaign"
    ]

    def ad(campaign, headline):
        return shop.post(
            f"/api/ads/campaigns/{campaign['id']}/creatives",
            {
                "kind": "banner",
                "headline": headline,
                "cta": "Shop now",
                "file_id": upload(shop, "image", PNG, base="/api/ads"),
            },
            expect=201,
        )["creative"]

    almond = ad(cakes, "Three-tier almond cake")
    removed = ad(cakes, "Removed ad")
    ad(gone, "Ad of a deleted campaign")
    shop.ok("DELETE", f"/api/ads/creatives/{removed['id']}")
    shop.ok("DELETE", f"/api/ads/campaigns/{gone['id']}")

    listed = shop.get("/api/ads/creatives")["creatives"]
    assert [item["headline"] for item in listed] == ["Three-tier almond cake"]
    item = listed[0]
    assert item["id"] == almond["id"] and item["campaign_id"] == cakes["id"]
    assert (
        item["campaign_name"] == "Wedding cakes" and item["campaign_status"] == "draft"
    )
    assert item["kind_label"] and item["review_status"] == "pending"
    assert "media_url" not in item  # a light list: no signed media links

    # A child's profile is refused like every other Ads route; another account sees only its own ads.
    mia = household.add("Mia", age_group="child")
    status, data = mia.call("GET", "/api/ads/creatives")
    assert status == 403 and data["code"] == "grown_up_only"
    other_client = env.api()
    other_client.call(
        "POST",
        "/api/auth/register",
        {"email": "other@example.com", "password": test_careers.PASSWORD},
    )
    other = Household(env, other_client).owner
    assert other.get("/api/ads/creatives")["creatives"] == []
