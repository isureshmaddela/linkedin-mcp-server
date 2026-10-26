"""Apps as subscriptions in the hosted shell: what a plan includes, what an organization turns on per candidate
seat, how the gateway refuses an app's routes until then, and what operators can grant."""

from __future__ import annotations

import json
import socket
import threading

import pytest
from cloud_support import (
    Browser,
    audit_actions,
    build_org,
    make_app,
    sign_in,
    signup,
    webhook,
)
from test_cloud_integrations import stripe, stripe_cloud  # noqa: F401
from test_cloud_ui_billing import operator_browser

from aira import apps as app_catalog
from aira.apps import app_of_path
from aira.hosting import HOOKS
from aira_cloud import plans
from aira_cloud.config import DAY
from aira_cloud.errors import AppRequired, Conflict, PlanRequired, SubscriptionInactive
from aira_cloud.policy import APP_FREE_ROUTES, POLICY

LEADS_SEAT = 400  # Marketing (with Leads) per seat on Team
MARKETING_STARTER_SEAT = 500
API_SEAT = 300


def seat_price(cloud, plan_id: str) -> int:
    return int(cloud.catalog.get(plan_id)["pricing"]["seat_cents_month"])


def apps_of(cloud, org_id: str) -> set[str]:
    return set(cloud.entitlements.resolve(org_id, fresh=True).apps)


def app_row(cloud, org_id: str, app_id: str) -> dict | None:
    with cloud.db.read() as connection:
        row = connection.execute(
            "SELECT * FROM org_apps WHERE org_id = ? AND app_id = ?", (org_id, app_id)
        ).fetchone()
    return dict(row) if row else None


def card(summary: dict, app_id: str) -> dict:
    return next(a for a in summary["apps"] if a["id"] == app_id)


# ---------------------------------------------------------------------------
# the pricing table and entitlements
# ---------------------------------------------------------------------------


def test_the_pricing_table_decides_what_each_plan_includes():
    for plan_id in ("trial", "starter", "team", "enterprise"):
        assert plans.app_state(plan_id, "careers") == "included"
        assert plans.app_price(plan_id, "careers") == 0
    assert (
        plans.app_state("trial", "marketing")
        == plans.app_state("trial", "api")
        == "included"
    )
    assert (
        plans.app_state("starter", "marketing") == "addon"
        and plans.app_price("starter", "marketing") == MARKETING_STARTER_SEAT
    )
    assert (
        plans.app_state("team", "marketing") == "addon"
        and plans.app_price("team", "marketing") == LEADS_SEAT
    )
    assert plans.app_state("enterprise", "marketing") == "included"
    assert (
        plans.app_state("starter", "api") == "unavailable"
        and plans.app_price("starter", "api") is None
    )
    assert (
        plans.app_state("team", "api") == "addon"
        and plans.app_price("team", "api") == API_SEAT
    )
    assert plans.app_state("enterprise", "api") == "included"
    assert (
        plans.plan_for_app("api") == "team"
        and plans.plan_for_app("marketing") == "starter"
    )
    assert plans.app_state("team", "unknown") == "unavailable"
    assert (
        "api.console" in plans.DEFAULT_PLANS["enterprise"]["entitlements"]["features"]
    )
    for plan_id in ("starter", "team"):
        features = plans.DEFAULT_PLANS[plan_id]["entitlements"]["features"]
        assert "leads" not in features and "api.console" not in features


def test_entitlements_follow_the_plan_and_the_subscriptions(cloud):
    trial = signup(cloud, "Trial Org", "owner@trial.test")
    assert apps_of(cloud, trial["org"]["org_id"]) == set(app_catalog.APP_IDS), (
        "the trial has every app"
    )
    starter = build_org(cloud, "Starter", plan="starter", seats=5, slug_email="s.test")
    assert apps_of(cloud, starter.org_id) == {"careers"}
    team = build_org(cloud, "Team", plan="team", seats=25, slug_email="t.test")
    assert apps_of(cloud, team.org_id) == {"careers"}
    entitlements = cloud.entitlements.resolve(team.org_id, fresh=True)
    assert not entitlements.has("leads") and not entitlements.has("api.console"), (
        "an app that is off takes its feature away"
    )
    cloud.billing.subscribe_app(team.owner, "marketing")
    entitlements = cloud.entitlements.resolve(team.org_id, fresh=True)
    assert entitlements.has_app("marketing") and entitlements.has("leads")
    assert entitlements.has_app("leads")  # Leads is a section of Marketing
    assert entitlements.as_dict()["apps"] == ["careers", "marketing"]
    operator = operator_browser(cloud)
    status, _ = operator.call(
        "POST",
        f"/api/platform/tenants/{starter.org_id}/activate-enterprise",
        {"seats": 100, "invoice_cents": 250000, "reference": "PO-1", "overrides": {}},
    )
    assert status == 200
    assert apps_of(cloud, starter.org_id) == set(app_catalog.APP_IDS), (
        "Enterprise includes every app"
    )
    assert cloud.entitlements.resolve(starter.org_id, fresh=True).has("api.console")


def test_me_and_the_catalog_say_which_apps_are_on(cloud):
    cloud.config.local_app_url = "http://127.0.0.1:8765"
    org = build_org(cloud, plan="team", seats=25)
    candidate = sign_in(cloud, org.candidate)
    status, me = candidate.call("GET", "/api/auth/me")
    assert status == 200
    by_id = {a["id"]: a for a in me["apps"]}
    assert by_id["careers"]["enabled"] is True and by_id["careers"]["reason"] == ""
    assert (
        by_id["marketing"]["enabled"] is False
        and "not turned on" in by_id["marketing"]["reason"].lower()
    )
    assert "api" in by_id and by_id["api"]["enabled"] is False
    status, data = candidate.call("GET", "/api/apps")
    assert status == 200
    assert data["account_url"] == cloud.config.base_url
    assert data["local_url"] == "http://127.0.0.1:8765"
    assert data["plan_id"] == "team"
    assert data["installation"] == "hosted"
    leads = next(a for a in data["apps"] if a["id"] == "marketing")
    assert (
        leads["name"] == "Marketing" and leads["enabled"] is False and leads["reason"]
    )
    assert set(leads) >= {"id", "name", "tagline", "home", "sections"}
    cloud.billing.subscribe_app(org.owner, "marketing")
    status, me = sign_in(cloud, org.candidate).call("GET", "/api/auth/me")
    assert next(a for a in me["apps"] if a["id"] == "marketing")["enabled"] is True
    assert "leads" in me["features"]


# ---------------------------------------------------------------------------
# the gateway
# ---------------------------------------------------------------------------


def test_the_gateway_refuses_an_apps_routes_until_the_organization_subscribes(cloud):
    org = build_org(cloud, plan="team", seats=25)
    candidate = sign_in(cloud, org.candidate)
    coach = sign_in(cloud, org.coach)
    status, body = coach.call("GET", "/api/leads/overview")
    assert status == 402 and body["code"] == "app_required", body
    assert (
        body["app"] == "marketing" and body["name"] == "Marketing" and body["plan_hint"]
    )
    assert body["audience"] == "staff", body
    status, body = candidate.call("GET", "/api/care/medicines")
    assert status == 402 and body["code"] == "app_required", body
    assert body["app"] == "care" and body["name"] == "Health"
    assert body["audience"] == "candidate" and body["required_plan"] is None
    assert "upgrade" not in body["error"].lower(), (
        "a candidate sees the organization's rule, not a sales prompt"
    )
    owner = sign_in(cloud, org.owner)
    status, body = owner.call(
        "POST", "/api/airaapi/search", {"engine": "google", "params": {"q": "x"}}
    )
    assert status == 402 and body["code"] == "app_required" and body["app"] == "api"
    assert "Apps" in body["plan_hint"] and body["audience"] == "staff"
    # Careers is part of every plan, and the organization's SerpApi key is a Careers integration
    status, body = candidate.call("GET", "/api/applications")
    assert body.get("code") != "app_required" and status < 500, (status, body)
    status, body = owner.call("GET", "/api/airaapi/account")
    assert body.get("code") != "app_required", body
    status, body = owner.call("POST", "/api/org/apps/marketing/subscribe", {})
    assert status == 200 and body["status"] == "active", body
    assert coach.call("GET", "/api/leads/overview")[0] == 200
    assert candidate.call("GET", "/api/leads/overview")[0] == 403, (
        "Leads belongs to the business: a candidate has no Leads"
    )
    starter = build_org(cloud, "Starter", plan="starter", seats=5, slug_email="s.test")
    status, body = sign_in(cloud, starter.owner).call(
        "POST", "/api/airaapi/search", {"engine": "google", "params": {"q": "x"}}
    )
    assert status == 402 and body["code"] == "app_required"
    assert body["required_plan"] == "team" and "Team" in body["plan_hint"], (
        "an app the plan does not sell names the plan that does"
    )


def test_every_app_route_prefix_is_known_to_the_gateway_and_the_policy():
    for app in app_catalog.APPS:
        for prefix in app.route_prefixes:
            assert app_of_path(prefix + "/anything") is app
    assert app_of_path("/api/org/apps/marketing/subscribe") is None, (
        "the Apps page itself is never gated by an app"
    )
    assert app_of_path("/api/apps") is None
    assert APP_FREE_ROUTES <= set(POLICY), sorted(APP_FREE_ROUTES - set(POLICY))
    assert POLICY["api_apps"].scope == "deny", (
        "the control plane answers /api/apps; the local route is never reached"
    )


# ---------------------------------------------------------------------------
# subscribing, quotes and proration (stand-in provider)
# ---------------------------------------------------------------------------


def test_subscribing_quotes_prorates_and_bills_with_the_plan(cloud):
    org = build_org(cloud, plan="team", seats=25)
    owner = sign_in(cloud, org.owner)
    status, overview = owner.call("GET", "/api/org/apps")
    assert status == 200 and overview["can_manage"] is True
    leads = card(overview, "marketing")
    assert leads["state"] == "addon" and leads["status"] == "off"
    assert leads["seat_cents_month"] == LEADS_SEAT and leads["monthly_cents"] == 0
    assert card(overview, "careers")["status"] == "included"
    assert card(overview, "api")["state"] == "addon"
    assert overview["monthly_cents"] == 25 * seat_price(cloud, "team")
    assert overview["addons_monthly_cents"] == 0
    cloud.clock.advance(days=15)
    owner = sign_in(cloud, org.owner)  # the session did not outlive the jump in time
    status, quote = owner.call("POST", "/api/org/apps/marketing/quote", {})
    assert status == 200
    assert quote["monthly_cents"] == 25 * LEADS_SEAT == 10000
    assert abs(quote["prorated_charge_cents"] - 5000) <= 100, quote
    status, result = owner.call("POST", "/api/org/apps/marketing/subscribe", {})
    assert status == 200 and result["status"] == "active"
    assert result["prorated_charge_cents"] == quote["prorated_charge_cents"]
    row = app_row(cloud, org.org_id, "marketing")
    assert row["status"] == "active" and row["seats_at_start"] == 25
    assert "billing.app_subscribed" in audit_actions(cloud, org.org_id, "billing.")
    invoices = cloud.billing.invoices(org.owner)
    proration = next(i for i in invoices if i["kind"] == "proration")
    assert proration["amount_cents"] == result["prorated_charge_cents"]
    lines = (
        proration["lines"]
        if isinstance(proration["lines"], list)
        else json.loads(proration["lines"])
    )
    assert lines[0]["app_id"] == "marketing" and "Marketing" in lines[0]["description"]
    record = cloud.billing.standin.subscription_record(f"sub_{org.org_id[:8]}")
    assert record["addons"] == [
        {"app_id": "marketing", "seats": 25, "amount_cents": 10000}
    ]
    assert record["amount_cents"] == 25 * seat_price(cloud, "team") + 10000
    assert record["prorate"] is True
    summary = cloud.billing.summary(org.org_id)
    assert card(summary, "marketing")["status"] == "active"
    assert card(summary, "marketing")["monthly_cents"] == 10000
    assert summary["addons_monthly_cents"] == 10000
    assert summary["monthly_cents"] == 25 * seat_price(cloud, "team") + 10000
    status, body = owner.call("POST", "/api/org/apps/marketing/subscribe", {})
    assert status == 409 and body["code"] == "already_subscribed"
    status, body = owner.call("POST", "/api/org/apps/careers/subscribe", {})
    assert status == 409 and body["code"] == "included"
    status, body = owner.call("POST", "/api/org/apps/nope/subscribe", {})
    assert status == 404
    # the API app on Team is another line
    status, result = owner.call("POST", "/api/org/apps/api/subscribe", {})
    assert status == 200
    record = cloud.billing.standin.subscription_record(f"sub_{org.org_id[:8]}")
    assert {a["app_id"]: a["amount_cents"] for a in record["addons"]} == {
        "marketing": 10000,
        "api": 25 * API_SEAT,
    }
    # the plan quote carries the add-ons along
    quote = cloud.billing.quote(org.org_id, "team", 25)
    assert {line["app_id"] for line in quote["addons"]} == {"marketing", "api"}
    assert quote["monthly_cents"] == 25 * (
        seat_price(cloud, "team") + LEADS_SEAT + API_SEAT
    )
    assert quote["dropped_apps"] == []
    quote = cloud.billing.quote(org.org_id, "starter", 10)
    assert quote["dropped_apps"] == ["api"] and [
        line["app_id"] for line in quote["addons"]
    ] == ["marketing"]


def test_who_may_change_apps(cloud):
    org = build_org(cloud, plan="team", seats=25)
    billing = sign_in(cloud, org.billing)
    assert billing.call("GET", "/api/org/apps")[0] == 200
    assert billing.call("POST", "/api/org/apps/marketing/subscribe", {})[0] == 200
    admin = sign_in(cloud, org.admin)
    status, data = admin.call("GET", "/api/org/apps")
    assert status == 200 and data["can_manage"] is False
    assert admin.call("POST", "/api/org/apps/api/subscribe", {})[0] == 403
    assert admin.call("POST", "/api/org/apps/marketing/unsubscribe", {})[0] == 403
    assert sign_in(cloud, org.auditor).call("GET", "/api/org/apps")[0] == 200
    assert sign_in(cloud, org.candidate).call("GET", "/api/org/apps")[0] == 403
    assert (
        sign_in(cloud, org.coach).call("POST", "/api/org/apps/api/quote", {})[0] == 403
    )


def test_an_app_that_the_plan_does_not_sell_or_a_trial_cannot_be_bought(cloud):
    starter = build_org(cloud, "Starter", plan="starter", seats=5, slug_email="s.test")
    owner = sign_in(cloud, starter.owner)
    status, body = owner.call("POST", "/api/org/apps/api/quote", {})
    assert status == 402 and body["code"] == "plan_required"
    assert body["required_plan"] == "team" and body["app"] == "api"
    with pytest.raises(PlanRequired):
        cloud.billing.subscribe_app(starter.owner, "api")
    trial = signup(cloud, "Trial Org", "owner@trial.test")
    with pytest.raises(Conflict) as caught:
        cloud.billing.subscribe_app(trial["owner"], "marketing")
    assert caught.value.code == "included", "the trial already has every app"


def test_unsubscribing_runs_to_the_period_end_and_keep_undoes_it(cloud):
    org = build_org(cloud, plan="team", seats=25)
    owner = sign_in(cloud, org.owner)
    assert owner.call("POST", "/api/org/apps/marketing/subscribe", {})[0] == 200
    assert owner.call("POST", "/api/org/apps/api/subscribe", {})[0] == 200
    period_end = cloud.billing.subscription(org.org_id)["period_end"]
    status, body = owner.call("POST", "/api/org/apps/marketing/unsubscribe", {})
    assert (
        status == 200
        and body["status"] == "cancelling"
        and body["effective_at"] == period_end
    )
    assert "billing.app_cancelled" in audit_actions(cloud, org.org_id, "billing.")
    assert "marketing" in apps_of(cloud, org.org_id), "it keeps working until then"
    overview = owner.call("GET", "/api/org/apps")[1]
    assert card(overview, "marketing")["status"] == "cancelling"
    assert card(overview, "marketing")["effective_until"] == period_end
    assert card(overview, "marketing")["monthly_cents"] == 10000, (
        "still on the bill this period"
    )
    status, body = owner.call("POST", "/api/org/apps/marketing/keep", {})
    assert status == 200 and body["status"] == "active"
    assert (
        card(owner.call("GET", "/api/org/apps")[1], "marketing")["status"] == "active"
    )
    assert owner.call("POST", "/api/org/apps/marketing/keep", {})[0] == 409
    assert owner.call("POST", "/api/org/apps/marketing/unsubscribe", {})[0] == 200
    assert (
        owner.call("POST", "/api/org/apps/careers/unsubscribe", {})[1]["code"]
        == "included"
    )
    cloud.clock.advance(seconds=31 * DAY)
    cloud.billing.process_due()
    owner = sign_in(cloud, org.owner)  # the session did not outlive the jump in time
    row = app_row(cloud, org.org_id, "marketing")
    assert row["status"] == "cancelled" and row["cancelled_at"]
    assert apps_of(cloud, org.org_id) == {"careers", "api"}
    status, body = sign_in(cloud, org.candidate).call("GET", "/api/leads/overview")
    assert status == 402 and body["code"] == "app_required"
    renewal = next(
        i
        for i in cloud.billing.invoices(org.owner)
        if i["kind"] == "subscription" and i["period_start"] == period_end
    )
    lines = (
        renewal["lines"]
        if isinstance(renewal["lines"], list)
        else json.loads(renewal["lines"])
    )
    assert [line.get("app_id") for line in lines] == [None, "api"], lines
    assert renewal["amount_cents"] == 25 * (seat_price(cloud, "team") + API_SEAT)
    record = cloud.billing.standin.subscription_record(f"sub_{org.org_id[:8]}")
    assert [a["app_id"] for a in record["addons"]] == ["api"], (
        "the provider's subscription follows at the period end"
    )
    status, body = owner.call("POST", "/api/org/apps/marketing/subscribe", {})
    assert (
        status == 200 and app_row(cloud, org.org_id, "marketing")["status"] == "active"
    ), "an app can be turned on again"


def test_seat_changes_re_price_the_add_ons(cloud):
    org = build_org(cloud, plan="team", seats=25)
    cloud.billing.subscribe_app(org.owner, "marketing")
    quote = cloud.billing.quote(org.org_id, "team", 30)
    assert quote["addons"] == [
        {
            "app_id": "marketing",
            "name": "Marketing",
            "seat_cents_month": LEADS_SEAT,
            "seats": 30,
            "amount_cents": 12000,
        }
    ]
    assert quote["monthly_cents"] == 30 * (seat_price(cloud, "team") + LEADS_SEAT)
    result = cloud.billing.change_plan(org.owner, "team", 30)
    assert result["applied"] is True and result["dropped_apps"] == []
    record = cloud.billing.standin.subscription_record(f"sub_{org.org_id[:8]}")
    assert record["seats"] == 30 and record["addons"] == [
        {"app_id": "marketing", "seats": 30, "amount_cents": 12000}
    ]
    summary = cloud.billing.summary(org.org_id)
    assert summary["addons_monthly_cents"] == 12000
    assert (
        card(summary, "marketing")["seats"] == 30
        and card(summary, "marketing")["monthly_cents"] == 12000
    )


def test_a_downgrade_to_a_plan_without_the_app_cancels_it(cloud):
    org = build_org(cloud, plan="team", seats=25)
    cloud.billing.subscribe_app(org.owner, "marketing")
    cloud.billing.subscribe_app(org.owner, "api")
    result = cloud.billing.change_plan(org.owner, "starter", 10)
    assert result["scheduled"] is True and result["dropped_apps"] == ["api"]
    assert apps_of(cloud, org.org_id) == {"careers", "marketing", "api"}, (
        "nothing changes until then"
    )
    cloud.clock.advance(seconds=31 * DAY)
    cloud.billing.process_due()
    assert cloud.billing.subscription(org.org_id)["plan_id"] == "starter"
    assert app_row(cloud, org.org_id, "api")["status"] == "cancelled"
    assert app_row(cloud, org.org_id, "marketing")["status"] == "active", (
        "Marketing is sold on Starter too"
    )
    assert apps_of(cloud, org.org_id) == {"careers", "marketing"}
    record = cloud.billing.standin.subscription_record(f"sub_{org.org_id[:8]}")
    assert [a["app_id"] for a in record["addons"]] == ["marketing"]
    with cloud.db.read() as connection:
        notices = [
            dict(r)
            for r in connection.execute(
                "SELECT * FROM org_notices WHERE org_id = ?", (org.org_id,)
            ).fetchall()
        ]
    assert any(
        "Developers" in n["body"] and "not available" in n["body"].lower()
        for n in notices
    ), notices
    cancelled = [
        row
        for row in cloud.audit.list(org.org_id, action="billing.app_cancelled")
        if row["target_id"] == "api"
    ]
    assert cancelled and "Starter" in json.dumps(cancelled[0]["detail"])
    # an upgrade applies now and takes the add-ons with it
    cloud.billing.subscribe_app(org.owner, "marketing") if app_row(
        cloud, org.org_id, "marketing"
    )["status"] != "active" else None
    result = cloud.billing.change_plan(org.owner, "team", 25)
    assert result["applied"] is True
    record = cloud.billing.standin.subscription_record(f"sub_{org.org_id[:8]}")
    assert record["addons"] == [
        {"app_id": "marketing", "seats": 25, "amount_cents": 10000}
    ]


def test_cancelling_the_plan_takes_the_apps_with_it(cloud):
    org = build_org(cloud, plan="team", seats=25)
    cloud.billing.subscribe_app(org.owner, "marketing")
    cloud.billing.cancel(org.owner)
    assert app_row(cloud, org.org_id, "marketing")["cancel_at_period_end"] == 1
    assert (
        card(cloud.billing.summary(org.org_id), "marketing")["status"] == "cancelling"
    )
    cloud.billing.reactivate(org.owner)
    assert app_row(cloud, org.org_id, "marketing")["cancel_at_period_end"] == 0
    cloud.billing.cancel(org.owner)
    cloud.clock.advance(seconds=31 * DAY)
    cloud.billing.process_due()
    assert cloud.billing.subscription(org.org_id)["status"] == "cancelled"
    assert app_row(cloud, org.org_id, "marketing")["status"] == "cancelled"


def test_dunning_and_read_only_block_app_changes(cloud):
    org = build_org(cloud, plan="team", seats=25)
    cloud.billing.subscribe_app(org.owner, "marketing")
    webhook(
        cloud,
        "invoice.payment_failed",
        org_id=org.org_id,
        invoice_id="in_1",
        amount_cents=37500,
    )
    assert cloud.billing.subscription(org.org_id)["status"] == "past_due"
    owner = sign_in(cloud, org.owner)
    status, body = owner.call("POST", "/api/org/apps/api/subscribe", {})
    assert status == 402 and body["code"] == "subscription_inactive", body
    status, body = owner.call("POST", "/api/org/apps/marketing/unsubscribe", {})
    assert status == 402 and body["code"] == "subscription_inactive"
    assert owner.call("GET", "/api/org/apps")[0] == 200, "reading stays possible"
    assert "marketing" in apps_of(cloud, org.org_id), (
        "what was bought keeps working while dunning runs"
    )
    cloud.clock.advance(days=15)
    cloud.billing.process_due()
    assert cloud.billing.subscription(org.org_id)["status"] == "read_only"
    with pytest.raises(SubscriptionInactive):
        cloud.billing.subscribe_app(org.owner, "api")


# ---------------------------------------------------------------------------
# operators
# ---------------------------------------------------------------------------


def test_an_operator_grants_and_revokes_an_app(cloud):
    starter = build_org(cloud, "Starter", plan="starter", seats=5, slug_email="s.test")
    operator = operator_browser(cloud)
    status, data = operator.call("GET", f"/api/platform/tenants/{starter.org_id}/apps")
    assert status == 200 and card(data, "api")["status"] == "unavailable"
    status, data = operator.call(
        "PUT",
        f"/api/platform/tenants/{starter.org_id}/apps/api",
        {"status": "granted", "note": "contract C-12"},
    )
    assert status == 200, data
    assert (
        card(data, "api")["status"] == "granted"
        and card(data, "api")["note"] == "contract C-12"
    )
    assert card(data, "api")["monthly_cents"] == 0, "a grant costs nothing"
    assert apps_of(cloud, starter.org_id) == {"careers", "api"}, (
        "a grant works even where the plan does not sell the app"
    )
    status, body = sign_in(cloud, starter.owner).call(
        "POST", "/api/airaapi/search", {"engine": "google", "params": {"q": "x"}}
    )
    assert body.get("code") != "app_required", body
    assert "billing.app_granted" in audit_actions(cloud, starter.org_id, "billing.")
    platform = [
        r
        for r in cloud.platform_audit.list(org_id=starter.org_id)
        if r["action"] == "billing.app_granted"
    ]
    assert platform and platform[0]["detail"] == {"app": "api", "note": "contract C-12"}
    assert cloud.billing.summary(starter.org_id)["addons_monthly_cents"] == 0
    # the organization cannot turn a granted app off, and an operator cannot grant what it pays for
    owner = sign_in(cloud, starter.owner)
    assert (
        owner.call("POST", "/api/org/apps/api/unsubscribe", {})[1]["code"]
        == "not_subscribed"
    )
    assert owner.call("POST", "/api/org/apps/marketing/subscribe", {})[0] == 200
    status, body = operator.call(
        "PUT",
        f"/api/platform/tenants/{starter.org_id}/apps/marketing",
        {"status": "granted"},
    )
    assert status == 409 and body["code"] == "already_subscribed"
    status, body = operator.call(
        "PUT",
        f"/api/platform/tenants/{starter.org_id}/apps/api",
        {"status": "revoked", "note": "contract ended"},
    )
    assert status == 200 and card(body, "api")["status"] == "unavailable"
    assert apps_of(cloud, starter.org_id) == {"careers", "marketing"}
    assert "billing.app_revoked" in audit_actions(cloud, starter.org_id, "billing.")
    assert (
        operator.call(
            "PUT",
            f"/api/platform/tenants/{starter.org_id}/apps/api",
            {"status": "revoked"},
        )[0]
        == 409
    )
    assert (
        operator.call(
            "PUT",
            f"/api/platform/tenants/{starter.org_id}/apps/api",
            {"status": "maybe"},
        )[0]
        == 400
    )
    assert (
        operator.call(
            "PUT",
            f"/api/platform/tenants/{starter.org_id}/apps/nope",
            {"status": "granted"},
        )[0]
        == 404
    )
    assert (
        operator.call(
            "PUT", f"/api/platform/tenants/{'0' * 32}/apps/api", {"status": "granted"}
        )[0]
        == 404
    )
    support = operator_browser(cloud, "support@aira.test", "support")
    assert support.call("GET", f"/api/platform/tenants/{starter.org_id}/apps")[0] == 200
    assert (
        support.call(
            "PUT",
            f"/api/platform/tenants/{starter.org_id}/apps/api",
            {"status": "granted"},
        )[0]
        == 403
    )
    assert owner.call("GET", f"/api/platform/tenants/{starter.org_id}/apps")[0] == 401


# ---------------------------------------------------------------------------
# Stripe adapter
# ---------------------------------------------------------------------------


def test_stripe_gets_one_subscription_item_per_add_on(stripe_cloud, stripe):  # noqa: F811
    app = stripe_cloud
    org = build_org(app, plan="team", seats=25)
    sub_id = f"sub_{org.org_id[:8]}"
    with app.db.write() as connection:
        connection.execute(
            "UPDATE subscriptions SET billing_provider = 'stripe', billing_subscription_id = ?, billing_customer_id = 'cus_1' WHERE org_id = ?",
            (sub_id, org.org_id),
        )
    app.billing.subscribe_app(org.owner, "marketing")
    [update] = stripe.find("POST", f"/v1/subscriptions/{sub_id}")
    params = update["params"]
    assert params["proration_behavior"] == "create_prorations"
    assert (
        params["metadata"]["apps"] == "marketing"
        and params["metadata"]["seats"] == "25"
    )
    plan_item, addon = params["items"][0], params["items"][1]
    assert plan_item["id"].startswith("si_") and plan_item["quantity"] == "25"
    assert addon["metadata"]["app_id"] == "marketing" and addon["quantity"] == "25"
    assert addon["price_data"]["unit_amount"] == str(LEADS_SEAT)
    assert addon["price_data"]["recurring"]["interval"] == "month"
    assert addon["price_data"]["product"].startswith("prod_") and "id" not in addon
    products = [c["params"]["name"] for c in stripe.find("POST", "/v1/products")]
    assert any("Marketing" in name and "add-on" in name for name in products), products
    app.billing.unsubscribe_app(org.owner, "marketing")
    app.clock.advance(seconds=31 * DAY)
    app.billing.process_due()
    updates = stripe.find("POST", f"/v1/subscriptions/{sub_id}")
    last = updates[-1]["params"]
    assert last["proration_behavior"] == "none" and last["metadata"]["apps"] == ""
    assert all(
        "app_id" not in (item.get("metadata") or {}) for item in last["items"]
    ), last


# ---------------------------------------------------------------------------
# the Apps page in a real browser
# ---------------------------------------------------------------------------


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


@pytest.fixture
def served(tmp_path):
    from aira_cloud.gateway import Gateway, make_server

    HOOKS.reset()
    port = _free_port()
    app = make_app(tmp_path, base_url=f"http://127.0.0.1:{port}")
    server = make_server(Gateway(app), "127.0.0.1", port)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        yield app, f"http://127.0.0.1:{port}"
    finally:
        server.shutdown()
        server.server_close()
        app.close()


def _open(chromium, base: str, jar: Browser, path: str):
    context = chromium.new_context(viewport={"width": 1200, "height": 900})
    context.add_cookies(
        [
            {"name": name, "value": value, "url": base}
            for name, value in jar.cookies.items()
        ]
    )
    page = context.new_page()
    problems: list[str] = []
    page.on("pageerror", lambda error: problems.append(f"pageerror: {error}"))
    # The account-link script (another feature) asks /api/auth/account-link on every page and gets 403 for staff;
    # the browser logs that as "Failed to load resource", so failing requests are judged by their address instead.
    page.on(
        "response",
        lambda response: (
            problems.append(f"{response.status} {response.url}")
            if response.status >= 400 and "/api/auth/account-link" not in response.url
            else None
        ),
    )
    page.on(
        "console",
        lambda message: (
            problems.append(f"console.{message.type}: {message.text}")
            if message.type == "error" and "Failed to load resource" not in message.text
            else None
        ),
    )
    page.goto(f"{base}/{path}", wait_until="load")
    return context, page, problems


def test_the_apps_page_subscribes_and_cancels_in_a_real_browser(served):
    playwright_api = pytest.importorskip("patchright.sync_api")
    app, base = served
    org = build_org(app, plan="team", seats=25)
    with playwright_api.sync_playwright() as playwright:
        try:
            chromium = playwright.chromium.launch()
        except Exception as error:  # noqa: BLE001
            pytest.skip(f"Chromium is not available: {error}")
        try:
            context, page, problems = _open(
                chromium, base, sign_in(app, org.owner), "#/org/apps"
            )
            page.wait_for_selector(".ca-app[data-app=marketing]", timeout=30000)
            leads = page.locator(".ca-app[data-app=marketing]")
            assert leads.get_attribute("data-status") == "off"
            assert "$4.00 per candidate seat per month" in leads.inner_text()
            assert (
                "Included in your plan"
                in page.locator(".ca-app[data-app=careers]").inner_text()
            )
            assert "$3.00" in page.locator(".ca-app[data-app=api]").inner_text()
            assert page.locator(".ca-totals").inner_text().count("Total per month") == 1
            assert page.locator("a[href='#/billing']").count() >= 1, (
                "a link to Plan & billing"
            )
            leads.locator("button[data-action=subscribe]").click()
            page.wait_for_selector(".modal", timeout=10000)
            modal_text = page.locator(".modal").inner_text()
            assert (
                "$100.00 per month" in modal_text and "charged" in modal_text.lower()
            ), modal_text
            page.locator(".modal .actions button", has_text="Subscribe").click()
            page.wait_for_selector(
                ".ca-app[data-app=marketing][data-status=active]", timeout=30000
            )
            assert app_row(app, org.org_id, "marketing")["status"] == "active"
            assert (
                "$100.00 per month for 25 seats"
                in page.locator(".ca-app[data-app=marketing]").inner_text()
            )
            page.locator(
                ".ca-app[data-app=marketing] button[data-action=unsubscribe]"
            ).click()
            page.wait_for_selector(".modal", timeout=10000)
            page.locator(
                ".modal .actions button", has_text="Turn off at period end"
            ).click()
            page.wait_for_selector(
                ".ca-app[data-app=marketing][data-status=cancelling]", timeout=30000
            )
            assert app_row(app, org.org_id, "marketing")["cancel_at_period_end"] == 1
            page.locator(".ca-app[data-app=marketing] button[data-action=keep]").click()
            page.wait_for_selector(
                ".ca-app[data-app=marketing][data-status=active]", timeout=30000
            )
            assert app_row(app, org.org_id, "marketing")["cancel_at_period_end"] == 0
            assert not problems, problems
            context.close()
            # an administrator reads the page without the buttons
            context, page, problems = _open(
                chromium, base, sign_in(app, org.admin), "#/org/apps"
            )
            page.wait_for_selector(".ca-app[data-app=marketing]", timeout=30000)
            assert page.locator(".ca-app button[data-action]").count() == 0
            assert (
                "owner or billing contact"
                in page.locator(".ca-apps").inner_text().lower()
            )
            assert not problems, problems
            context.close()
        finally:
            chromium.close()


def test_an_app_required_error_names_the_app_for_the_page(cloud):
    org = build_org(cloud, plan="starter", seats=5)
    with pytest.raises(AppRequired) as caught:
        cloud.entitlements.require_app(org.org_id, "api")
    error = caught.value
    assert error.status == 402 and error.code == "app_required"
    assert error.extra["app"] == "api" and error.extra["name"] == "Developers"
    assert error.extra["required_plan"] == "team" and "Team" in error.extra["plan_hint"]
    assert cloud.entitlements.require_app(org.org_id, "careers").has_app("careers")
    assert cloud.entitlements.require_app(org.org_id, "not-an-app") is not None, (
        "an unknown app id is nobody's route"
    )
