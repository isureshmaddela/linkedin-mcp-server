"""The superadmin sets up subscriptions, and the industry apps follow what an organization has."""

from __future__ import annotations

import pytest
from cloud_support import build_org, sign_in, signup
from test_cloud_ui_billing import operator_browser

from aira import apps as app_catalog
from aira_cloud import plans
from aira_cloud.errors import AppRequired


def test_every_industry_app_is_sold_per_seat_and_included_in_enterprise():
    for app in app_catalog.INDUSTRY_APPS:
        assert (
            app.scope == "org"
            and app.category == "industry"
            and app.feature == f"app.{app.id}"
        )
        assert plans.app_state("trial", app.id) == "included"
        assert plans.app_state("starter", app.id) == "addon"
        assert plans.app_state("team", app.id) == "addon"
        assert plans.app_state("enterprise", app.id) == "included"
        for section in app.sections:  # every section's routes belong to the app (Commerce: /api/shop, /api/bookings)
            assert app_catalog.app_of_path(f"/api/{section}/anything").id == app.id


def test_an_organization_only_sees_the_apps_it_has(cloud):
    org = build_org(cloud, plan="team")
    owner = sign_in(cloud, org.owner)
    status, data = owner.call("GET", "/api/apps")
    enabled = {a["id"] for a in data["apps"] if a["enabled"]}
    assert "careers" in enabled and "studio" not in enabled
    with pytest.raises(AppRequired):
        cloud.entitlements.require_app(org.org_id, "studio")
    cloud.billing.subscribe_app(org.owner, "studio")
    status, data = owner.call("GET", "/api/apps")
    assert "studio" in {a["id"] for a in data["apps"] if a["enabled"]}
    card = next(
        a for a in cloud.billing.apps_overview(org.org_id) if a["id"] == "studio"
    )
    assert card["category"] == "industry" and card["highlights"]


def test_the_superadmin_sets_a_subscription_bought_outside_the_product(cloud):
    result = signup(cloud, "Glow Salon", "owner@glow.test")
    org_id = result["org"]["org_id"]
    assert cloud.billing.subscription(org_id)["status"] == "trialing"
    operator = operator_browser(cloud)
    status, data = operator.call(
        "POST",
        f"/api/platform/tenants/{org_id}/subscription",
        {
            "plan_id": "starter",
            "seats": 5,
            "months": 12,
            # an old app id ("bookings") is the app it became (Commerce)
            "apps": {"bookings": True, "service": True, "api": True},
            "note": "INV-778",
        },
    )
    assert status == 200, data
    subscription = cloud.billing.subscription(org_id)
    assert subscription["status"] == "active" and subscription["plan_id"] == "starter"
    assert subscription["candidate_seats"] == 5
    assert subscription["period_end"] >= cloud.clock.now() + 12 * 30 * 86400 - 5
    statuses = {a["id"]: a["status"] for a in data["apps"]}
    assert statuses["commerce"] == "granted" and statuses["service"] == "granted"
    assert "bookings" not in statuses and "shop" not in statuses
    assert statuses["api"] == "granted"  # not on Starter, but a superadmin can grant it
    assert statuses["studio"] == "off"
    entitlements = cloud.entitlements.resolve(org_id, fresh=True)
    assert entitlements.has_app("commerce") and entitlements.has_app(
        "bookings"
    )  # a section of Commerce
    assert not entitlements.has_app("studio")
    # Taking a grant back; validation; and both audit logs.
    status, data = operator.call(
        "POST",
        f"/api/platform/tenants/{org_id}/subscription",
        {"plan_id": "starter", "seats": 5, "apps": {"service": False}},
    )
    assert {a["id"]: a["status"] for a in data["apps"]}["service"] == "off"
    assert (
        operator.call(
            "POST",
            f"/api/platform/tenants/{org_id}/subscription",
            {"plan_id": "starter", "seats": 2},
        )[0]
        == 400
    )
    assert (
        operator.call(
            "POST",
            f"/api/platform/tenants/{org_id}/subscription",
            {"plan_id": "trial", "seats": 5},
        )[0]
        == 400
    )
    actions = [row["action"] for row in cloud.audit.list(org_id, limit=100)]
    assert "billing.subscription_set" in actions and "billing.app_granted" in actions
    # A member cannot call the operator route.
    owner = sign_in(cloud, result["owner"])
    assert owner.call(
        "POST",
        f"/api/platform/tenants/{org_id}/subscription",
        {"plan_id": "team", "seats": 30},
    )[0] in (401, 403)


def test_every_life_app_is_for_every_member_and_never_for_support_staff():
    from aira_cloud.rbac import NEVER_VIA_SUPPORT, ORG_PERMISSIONS

    for app in app_catalog.LIFE_APPS:
        assert app.category == "life" and app.scope == "org"
        assert plans.app_state("trial", app.id) == "included"
        assert plans.app_state("starter", app.id) == "addon"
        assert plans.app_state("enterprise", app.id) == "included"
        for section in app.sections:
            assert app_catalog.app_of_path(f"/api/{section}/x").id == app.id
    # /api/health stays the server's own health check, not the Health app
    assert app_catalog.app_of_path("/api/health") is None
    for role, permissions in ORG_PERMISSIONS.items():
        assert {"life.read", "life.manage"} <= permissions, role
    assert {"life.read", "life.manage"} <= NEVER_VIA_SUPPORT


# ---------------------------------------------------------------------------
# fewer apps, more sections (Commerce = Shop + Bookings, Marketing includes Leads, Social includes Family)
# ---------------------------------------------------------------------------


def test_the_catalog_combines_sections_into_fewer_apps():
    ids = [app.id for app in app_catalog.APPS]
    assert ids == [
        "careers",
        "api",
        "studio",
        "marketing",
        "service",
        "commerce",
        "experiences",
        "ads",
        "social",
        "care",
        "learn",
        "play",
        "planner",
        "news",
        "videos",
        "journey",
        "ai",
        "stocks",
    ]
    assert (
        app_catalog.BY_ID["studio"].name == "Sales"
        and app_catalog.BY_ID["api"].name == "Developers"
    )
    assert app_catalog.BY_ID["commerce"].sections == ("shop", "bookings")
    assert app_catalog.BY_ID["experiences"].sections == ("academy", "events")
    assert app_catalog.BY_ID["marketing"].sections == ("marketing", "leads")
    assert app_catalog.BY_ID["social"].sections == ("social", "family")
    for old, new in app_catalog.LEGACY_IDS.items():
        assert old not in app_catalog.BY_ID and app_catalog.owner_of(old).id == new
        assert app_catalog.current_id(old) == new
    assert app_catalog.owner_of("developers").id == "api"
    assert (
        app_catalog.owner_of("nothing") is None
        and app_catalog.current_id("nothing") == "nothing"
    )
    keys = [app.key for app in app_catalog.APPS if app.key]
    assert len(keys) == len(set(keys))
    sections = [section for app in app_catalog.APPS for section in app.sections]
    assert len(sections) == len(set(sections))  # a section belongs to one app
    assert "leads" not in plans.APP_PRICING


def test_an_organization_that_subscribed_an_old_app_keeps_it(cloud):
    org = build_org(cloud, plan="team")
    now = cloud.clock.now()
    with cloud.db.write() as connection:
        for app_id, status in (
            ("shop", "cancelled"),
            ("bookings", "active"),
            ("leads", "granted"),
        ):
            connection.execute(
                """INSERT INTO org_apps (org_id, app_id, status, seats_at_start, activated_at, cancel_at_period_end, granted_by, note)
                VALUES (?, ?, ?, 25, ?, 0, '', '')""",
                (org.org_id, app_id, status, now),
            )
    entitlements = cloud.entitlements.resolve(org.org_id, fresh=True)
    assert entitlements.has_app("commerce") and entitlements.has_app("marketing")
    assert entitlements.has_app("shop") and entitlements.has_app(
        "leads"
    )  # sections answer for their app
    assert {
        "leads",
        "leads_outreach",
        "app.marketing",
        "app.commerce",
    } <= entitlements.features
    cards = {card["id"]: card for card in cloud.billing.apps_overview(org.org_id)}
    assert (
        cards["commerce"]["status"] == "active"
    )  # the paid one wins over the cancelled one
    assert cards["marketing"]["status"] == "granted"
    with cloud.db.read() as connection:
        stored = {
            row["app_id"]
            for row in connection.execute(
                "SELECT app_id FROM org_apps WHERE org_id = ?", (org.org_id,)
            )
        }
    assert stored == {"commerce", "marketing"}  # moved to the new ids once


def test_marketing_brings_leads_and_the_gateway_follows_it(cloud):
    org = build_org(cloud, plan="team")
    owner = sign_in(cloud, org.owner)
    entitlements = cloud.entitlements.resolve(org.org_id, fresh=True)
    assert not entitlements.has("leads")
    status, body = owner.call("GET", "/api/leads/overview")
    assert (
        status == 402 and body["code"] == "app_required" and body["app"] == "marketing"
    )
    cloud.billing.subscribe_app(org.owner, "marketing")
    entitlements = cloud.entitlements.resolve(org.org_id, fresh=True)
    assert entitlements.has("leads") and entitlements.has(
        "leads_outreach"
    )  # Team sends email
    assert owner.call("GET", "/api/leads/overview")[0] == 200
    # Leads is the business's: a candidate no longer has it, a coach (team member) does
    assert sign_in(cloud, org.coach).call("GET", "/api/leads/overview")[0] == 200
    assert sign_in(cloud, org.candidate).call("GET", "/api/leads/overview")[0] == 403
    starter = build_org(
        cloud,
        "Starter",
        plan="starter",
        seats=5,
        slug_email="s.test",
        apps=["marketing"],
    )
    entitlements = cloud.entitlements.resolve(starter.org_id, fresh=True)
    assert entitlements.has("leads") and not entitlements.has("leads_outreach"), (
        "Marketing brings Leads; sending email stays a Team feature"
    )


def test_family_is_part_of_social(cloud):
    org = build_org(cloud, plan="team")
    owner = sign_in(cloud, org.owner)
    assert owner.call("GET", "/api/family/overview")[0] == 402
    cloud.billing.subscribe_app(org.owner, "social")
    assert owner.call("GET", "/api/family/overview")[0] == 200
    status, day = owner.call("GET", "/api/life/today")
    assert status == 200 and {"social", "family"} <= set(day["apps"])
