"""Plans the superadmin creates, the plan new sign-ups start on, and the superadmin's control over one organization's
plan and apps (platform console > Plans, Tenants)."""

from __future__ import annotations

import pytest
from cloud_support import (
    Browser,
    add_member,
    build_org,
    make_app,
    pay,
    sign_in,
    signup,
)
from test_cloud_apps import _open, served  # noqa: F401
from test_cloud_ui_billing import operator_browser

from aira import apps as app_catalog
from aira.hosting import HOOKS
from monosage_cloud import plans
from monosage_cloud.config import DAY, CloudConfig
from monosage_cloud.db import table_columns

EVERY_APP = set(app_catalog.APP_IDS)
EVERY_FEATURE = set(plans.KNOWN_FEATURES) - {"overage"}


@pytest.fixture
def shipped(tmp_path):
    """An installation with the shipped sign-up default (the other hosted tests pin the 14-day trial)."""
    HOOKS.reset()
    app = make_app(tmp_path, signup_plan=CloudConfig.signup_plan)
    yield app
    app.close()


def platform_actions(app, prefix: str = "") -> list[str]:
    return [
        r["action"]
        for r in app.platform_audit.list(limit=500)
        if r["action"].startswith(prefix)
    ]


GROWTH = {
    "plan_id": "growth",
    "name": "Growth",
    "description": "Between Starter and Team.",
    "based_on": "starter",
    "pricing": {"seat_cents_month": 900, "min_seats": 2, "trial_days": 10},
    "entitlements": {
        "candidate_seats": 60,
        "apps_per_candidate_day": 12,
        "features": [
            "sources.aira_jobs",
            "sources.company_boards",
            "sources.google_jobs",
            "sources.linkedin",
            "automation.submit",
            "ai.cover_letters",
            "auth.mfa",
        ],
    },
    "apps": {"marketing": "included", "social": 250, "api": None},
}


def create(operator, body=None):
    status, data = operator.call("POST", "/api/platform/plans", body or GROWTH)
    assert status == 201, data
    return data["plan"]


# ----------------------------------------------------------------------------- the sign-up default


def test_a_new_sign_up_gets_every_app_and_every_feature_by_default(shipped):
    assert CloudConfig.signup_plan == plans.ALL_ACCESS
    result = signup(shipped, "Fresh Org", "owner@fresh.test")
    # a time-boxed trial of All access (14 days), not All access with no end
    assert result["subscription"] == {
        "plan_id": "all_access",
        "status": "trialing",
        "trial_days": 14,
        "trial_end": shipped.clock.now() + 14 * DAY,
    }
    org_id = result["org"]["org_id"]
    entitlements = shipped.entitlements.resolve(org_id, fresh=True)
    assert entitlements.plan_id == "all_access" and entitlements.status == "trialing"
    assert EVERY_FEATURE <= entitlements.features
    assert set(entitlements.apps) == EVERY_APP
    assert entitlements.automation_allowed and not entitlements.read_only
    # a person invited into the organization is an ordinary member (candidate, "Member") who opens every app
    member = add_member(shipped, result["owner"], "pat@fresh.test", "candidate")
    assert member.role == "candidate"
    browser = sign_in(shipped, member)
    status, catalog = browser.call("GET", "/api/apps")
    assert status == 200 and catalog["plan_id"] == "all_access"
    assert {a["id"] for a in catalog["apps"] if a["enabled"] and a["allowed"]} == (
        EVERY_APP
    ), [a for a in catalog["apps"] if not (a["enabled"] and a["allowed"])]
    owner = sign_in(shipped, result["owner"])
    assert owner.call("GET", "/api/leads/overview")[0] == 200
    # an organization that had All access with no end before trials were the default keeps it: a free plan renews
    # by itself and never invoices
    with shipped.db.write() as connection:
        connection.execute(
            "UPDATE subscriptions SET status = 'active', trial_end = NULL WHERE org_id = ?",
            (org_id,),
        )
    shipped.entitlements.invalidate(org_id)
    shipped.clock.advance(days=31)
    done = shipped.billing.process_due()
    assert org_id in done["renewed"]
    summary = shipped.billing.summary(org_id)
    assert summary["status"] == "active" and summary["plan_free"] is True
    assert (
        summary["checkout_required"] is True
    )  # a paid plan is bought through checkout
    assert shipped.billing.invoices(result["owner"]) == []
    # and an owner moving to a paid plan goes through checkout, never a free "upgrade"
    owner = sign_in(shipped, result["owner"])  # a month on: a new session
    status, refused = owner.call(
        "POST", "/api/org/billing/change-plan", {"plan": "team", "seats": 25}
    )
    assert status == 409 and refused["code"] == "not_subscribed"
    status, started = owner.call(
        "POST", "/api/org/billing/checkout", {"plan": "team", "seats": 25}
    )
    assert status == 200 and started["quote"]["plan_id"] == "team"
    pay(shipped, org_id, "team", 25)
    assert shipped.billing.subscription(org_id)["plan_id"] == "team"


def test_the_superadmin_sets_the_default_and_existing_organizations_keep_their_plans(
    shipped,
):
    first = signup(shipped, "First Org", "owner@first.test")["org"]["org_id"]
    operator = operator_browser(shipped)
    status, body = operator.call("GET", "/api/platform/plans")
    assert status == 200 and body["signup"]["plan_id"] == "all_access"
    assert body["signup"]["trial_days"] == 14 and "sources.linkedin" in body["features"]
    status, body = operator.call(
        "PUT",
        "/api/platform/plans/signup-default",
        {"plan_id": "team", "trial_days": 7},
    )
    assert status == 200, body
    assert body["signup"]["plan_id"] == "team" and body["signup"]["trial_days"] == 7
    second = signup(shipped, "Second Org", "owner@second.test")
    sub = shipped.billing.subscription(second["org"]["org_id"])
    assert sub["plan_id"] == "team" and sub["status"] == "trialing"
    assert sub["trial_end"] == shipped.clock.now() + 7 * DAY
    assert shipped.billing.subscription(first)["plan_id"] == "all_access"
    # the trial of a paid plan can be extended and ends like any trial
    assert (
        operator.call(
            "POST",
            f"/api/platform/tenants/{second['org']['org_id']}/extend-trial",
            {"days": 3},
        )[0]
        == 200
    )
    shipped.clock.advance(days=11)
    assert second["org"]["org_id"] in shipped.billing.process_due()["trial_ended"]
    # the public plans route says what a sign-up gets
    public = Browser(shipped).call("GET", "/api/billing/plans")[1]
    assert public["signup"] == {"plan_id": "team", "plan_name": "Team", "trial_days": 7}
    # back to the trial: its own length when none is given (eleven days on, the operator signs in again)
    operator = operator_browser(shipped, "ops2@aira.test")
    status, body = operator.call(
        "PUT", "/api/platform/plans/signup-default", {"plan_id": "trial"}
    )
    assert status == 200 and body["signup"]["trial_days"] == 14
    third = signup(shipped, "Third Org", "owner@third.test")["org"]["org_id"]
    assert shipped.billing.subscription(third)["plan_id"] == "trial"
    assert platform_actions(shipped, "plan.signup_default") == [
        "plan.signup_default",
        "plan.signup_default",
    ]
    org_create = [
        r for r in shipped.audit.list(third, limit=50) if r["action"] == "org.create"
    ]
    assert org_create[0]["detail"]["plan"] == "trial"


def test_the_default_is_validated(shipped):
    operator = operator_browser(shipped)

    def put(body):
        return operator.call("PUT", "/api/platform/plans/signup-default", body)

    assert put({"plan_id": "trial", "trial_days": 0})[0] == 400
    status, body = put({"plan_id": "team", "trial_days": 0})
    assert status == 400 and body["code"] == "trial_required", body
    assert put({"plan_id": "team", "trial_days": 400})[0] == 400
    assert put({"plan_id": "team", "trial_days": "7"})[0] == 400
    assert put({"plan_id": "nope_plan", "trial_days": 7})[0] == 404
    create(operator)
    assert operator.call("POST", "/api/platform/plans/growth/archive", {})[0] == 200
    assert put({"plan_id": "growth", "trial_days": 5})[0] == 400
    # the default plan cannot be archived while sign-ups start on it
    status, body = operator.call("POST", "/api/platform/plans/all_access/archive", {})
    assert status == 409 and body["code"] == "signup_default"
    assert shipped.billing.signup_default()["plan_id"] == "all_access"


# ----------------------------------------------------------------------------- creating, editing, archiving


def test_the_superadmin_creates_a_plan_with_features_limits_and_apps(shipped):
    operator = operator_browser(shipped)
    plan = create(operator)
    assert plan["plan_id"] == "growth" and plan["version"] == 1
    assert plan["builtin"] is False and plan["public"] is True and not plan["archived"]
    ent = plan["entitlements"]
    assert ent["candidate_seats"] == 60 and ent["apps_per_candidate_day"] == 12
    # every limit not named comes from the base plan: never "no limit" by accident
    starter = plans.DEFAULT_PLANS["starter"]["entitlements"]
    for name in plans.LIMIT_NAMES:
        if name not in GROWTH["entitlements"]:
            assert ent[name] == starter[name], name
    assert ent["coach_max_scope"] == "act" and "sources.linkedin" in ent["features"]
    assert plan["pricing"] == {
        "currency": "usd",
        "seat_cents_month": 900,
        "min_seats": 2,
        "overage": {},
        "trial_days": 10,
    }
    assert plan["apps"]["marketing"]["state"] == "included"
    assert plan["apps"]["social"]["state"] == "addon"
    assert plan["apps"]["social"]["seat_cents_month"] == 250
    assert plan["apps"]["api"]["state"] == "unavailable"
    assert (
        plan["apps"]["care"]["state"] == "unavailable"
    )  # a created plan offers nothing it was not told to
    assert plan["apps"]["careers"]["state"] == "included"
    listed = operator.call("GET", "/api/platform/plans")[1]
    assert [p["plan_id"] for p in listed["plans"]][-1] == "growth"
    # the Apps page shows the new plan's prices too
    marketing = next(
        a
        for a in operator.call("GET", "/api/platform/apps")[1]["apps"]
        if a["id"] == "marketing"
    )
    assert (
        marketing["prices"]["growth"] == 0
        and marketing["default_prices"]["growth"] is None
    )
    assert "growth" not in marketing["changed_plans"]
    assert "plan.create" in platform_actions(shipped, "plan.")


@pytest.mark.parametrize(
    ("body", "status"),
    [
        ({**GROWTH, "plan_id": "Growth!"}, 400),
        ({**GROWTH, "plan_id": "ab"}, 400),
        ({**GROWTH, "plan_id": "versions"}, 400),
        ({**GROWTH, "plan_id": "starter"}, 409),
        ({**GROWTH, "name": ""}, 400),
        ({**GROWTH, "entitlements": {"features": ["teleport"]}}, 400),
        ({**GROWTH, "entitlements": {"candidate_seats": -1}}, 400),
        ({**GROWTH, "entitlements": {"made_up_limit": 3}}, 400),
        ({**GROWTH, "entitlements": {"coach_max_scope": "everything"}}, 400),
        ({**GROWTH, "pricing": {"seat_cents_month": -5}}, 400),
        ({**GROWTH, "pricing": {"trial_days": 999}}, 400),
        ({**GROWTH, "apps": {"marketing": 10**7}}, 400),
        ({**GROWTH, "apps": {"nope": 0}}, 404),
        ({**GROWTH, "based_on": "nope"}, 404),
    ],
)
def test_a_plan_is_validated(shipped, body, status):
    operator = operator_browser(shipped)
    got, data = operator.call("POST", "/api/platform/plans", body)
    assert got == status, data
    assert "growth" not in shipped.catalog.order(include_archived=True)


def test_editing_publishes_a_version_and_the_name_and_apps_change_at_once(shipped):
    operator = operator_browser(shipped)
    create(operator)
    org = build_org(shipped, "On Growth", plan="trial", slug_email="g.test")
    assert (
        operator.call(
            "POST",
            f"/api/platform/tenants/{org.org_id}/subscription",
            {"plan_id": "growth", "seats": 5, "months": 12},
        )[0]
        == 200
    )
    status, body = operator.call(
        "PUT",
        "/api/platform/plans/growth",
        {"entitlements": {"apps_per_candidate_day": 20}},
    )
    assert status == 200 and body["version"] == 2
    assert (
        shipped.entitlements.resolve(org.org_id, fresh=True).limit(
            "apps_per_candidate_day"
        )
        == 12
    ), "existing subscriptions keep their version"
    status, body = operator.call(
        "PUT",
        "/api/platform/plans/growth",
        {
            "name": "Growth Plus",
            "public": False,
            "apps": {"care": "included", "social": None},
        },
    )
    assert status == 200 and body["version"] == 2, (
        "no new version for the name and apps"
    )
    entitlements = shipped.entitlements.resolve(org.org_id, fresh=True)
    assert entitlements.has_app("care") and not entitlements.has_app("social")
    assert shipped.catalog.name("growth") == "Growth Plus"
    assert "growth" not in shipped.catalog.self_serve()
    assert (
        operator.call("PUT", "/api/platform/plans/growth", {"public": "no"})[0] == 400
    )
    assert operator.call("PUT", "/api/platform/plans/growth", {"apps": [1]})[0] == 400
    assert (
        operator.call("PUT", "/api/platform/plans/trial", {"apps": {"care": None}})[0]
        == 400
    )
    assert platform_actions(shipped, "plan.update") == ["plan.update", "plan.update"]


def test_archiving_stops_new_subscriptions_and_keeps_existing_ones(shipped):
    operator = operator_browser(shipped)
    create(operator)
    org = build_org(shipped, "Keeps It", plan="trial", slug_email="k.test")
    pay(shipped, org.org_id, "growth", 5)
    assert shipped.billing.subscription(org.org_id)["plan_id"] == "growth"
    status, body = operator.call("POST", "/api/platform/plans/growth/archive", {})
    assert status == 200 and body["plan"]["archived"] is True
    assert body["plan"]["subscriptions"] == 1
    assert operator.call("POST", "/api/platform/plans/growth/archive", {})[0] == 409
    assert operator.call("POST", "/api/platform/plans/trial/archive", {})[0] == 400
    # existing organization keeps it
    entitlements = shipped.entitlements.resolve(org.org_id, fresh=True)
    assert (
        entitlements.plan_id == "growth" and entitlements.limit("candidate_seats") == 5
    )
    # nobody new can buy it or be put on it, and it is not suggested as an upgrade
    other = build_org(shipped, "Wants It", plan="trial", slug_email="w.test")
    owner = sign_in(shipped, other.owner)
    assert (
        owner.call("POST", "/api/org/billing/quote", {"plan": "growth", "seats": 5})[0]
        == 400
    )
    assert "growth" not in [p["plan_id"] for p in shipped.billing.public_plans()]
    assert (
        operator.call(
            "POST",
            f"/api/platform/tenants/{other.org_id}/subscription",
            {"plan_id": "growth", "seats": 5},
        )[0]
        == 400
    )
    assert "growth" not in shipped.catalog.upgrade_order()
    listed = operator.call("GET", "/api/platform/plans")[1]["plans"]
    assert next(p for p in listed if p["plan_id"] == "growth")["archived"] is True
    assert operator.call("POST", "/api/platform/plans/growth/restore", {})[0] == 200
    assert (
        owner.call("POST", "/api/org/billing/quote", {"plan": "growth", "seats": 5})[0]
        == 200
    )
    assert platform_actions(shipped, "plan.archive") == ["plan.archive"]
    assert platform_actions(shipped, "plan.restore") == ["plan.restore"]


def test_the_superadmins_order_ranks_the_plans(shipped):
    operator = operator_browser(shipped)
    create(operator)
    order = shipped.catalog.order(include_archived=True)
    assert order == ["trial", "starter", "team", "enterprise", "all_access", "growth"]
    wanted = ["trial", "starter", "growth", "team", "enterprise", "all_access"]
    status, body = operator.call(
        "PUT", "/api/platform/plans/sort-order", {"order": wanted}
    )
    assert status == 200 and body["order"] == wanted
    assert [p["plan_id"] for p in shipped.billing.public_plans()] == [
        "starter",
        "growth",
        "team",
        "enterprise",
    ]
    assert (
        operator.call("PUT", "/api/platform/plans/sort-order", {"order": wanted[:-1]})[
            0
        ]
        == 400
    )
    # a paid organization on Growth moving to Starter is a downgrade now (scheduled), to Team an upgrade
    org = build_org(shipped, "Ranked", plan="growth", seats=10, slug_email="r.test")
    owner = sign_in(shipped, org.owner)
    status, body = owner.call(
        "POST", "/api/org/billing/change-plan", {"plan": "starter", "seats": 10}
    )
    assert status == 200 and body["scheduled"] is True, body
    assert "plan.order" in platform_actions(shipped, "plan.")


# ----------------------------------------------------------------------------- entitlements and required_plan


def test_a_custom_plan_decides_features_limits_and_apps(shipped):
    operator = operator_browser(shipped)
    create(operator)
    org = build_org(shipped, "Growth Org", plan="growth", seats=6, slug_email="go.test")
    entitlements = shipped.entitlements.resolve(org.org_id, fresh=True)
    assert entitlements.plan_id == "growth" and entitlements.status == "active"
    assert entitlements.has("sources.linkedin") and not entitlements.has("api.v1")
    assert (
        entitlements.limit("candidate_seats") == 6
    )  # the seats bought, under the plan's 60
    assert entitlements.has_app("marketing") and entitlements.has_app("careers")
    assert not entitlements.has_app("social") and not entitlements.has_app("api")
    assert "leads" in entitlements.features  # carried by Marketing
    member = sign_in(shipped, org.candidate)
    assert member.call("GET", "/api/leads/overview")[0] in (
        200,
        403,
    )  # candidates do not use Leads
    owner = sign_in(shipped, org.owner)
    assert owner.call("GET", "/api/leads/overview")[0] == 200
    # an add-on on this plan is bought per seat at the plan's price
    quote = shipped.billing.app_quote(org.org_id, "social")
    assert quote["state"] == "addon" and quote["monthly_cents"] == 6 * 250
    shipped.billing.subscribe_app(org.owner, "social")
    assert shipped.entitlements.resolve(org.org_id, fresh=True).has_app("social")
    lines = shipped.billing.invoice_lines(shipped.billing.subscription(org.org_id))
    assert lines[0]["amount_cents"] == 6 * 900 and lines[1]["app_id"] == "social"
    # what is not available names the cheapest plan that has it
    status, body = owner.call("GET", "/api/airaapi/account")
    assert status == 402 and body["code"] in ("app_required", "plan_required"), body


def test_required_plan_names_the_cheapest_plan_including_created_ones(shipped):
    assert shipped.entitlements.required_plan("sources.linkedin") == "team"
    assert shipped.entitlements.required_plan("auth.scim") == "enterprise"
    operator = operator_browser(shipped)
    create(operator)  # $9 x 2 seats = $18 a month: cheaper than Starter's $70
    assert shipped.entitlements.required_plan("sources.linkedin") == "growth"
    assert shipped.entitlements.required_plan("sources.google_jobs") == "growth"
    assert shipped.entitlements.required_plan("auth.sso") == "team"
    # a private plan is given, not sold, so it is never named
    operator.call("PUT", "/api/platform/plans/growth", {"public": False})
    assert shipped.entitlements.required_plan("sources.linkedin") == "team"
    operator.call("PUT", "/api/platform/plans/growth", {"public": True})
    operator.call("POST", "/api/platform/plans/growth/archive", {})
    assert shipped.entitlements.required_plan("sources.linkedin") == "team"
    # All access has everything but is private: never an upgrade suggestion
    assert "all_access" not in shipped.catalog.upgrade_order()
    assert shipped.app_settings.plan_for("marketing") == "starter"


def test_the_upgrade_prompt_names_a_created_plan(shipped):
    operator = operator_browser(shipped)
    create(operator)
    org = build_org(shipped, "Small", plan="starter", seats=5, slug_email="s.test")
    with pytest.raises(Exception) as raised:
        shipped.entitlements.require_feature(
            org.org_id, "sources.linkedin", label="LinkedIn"
        )
    assert "Upgrade to Growth" in str(raised.value)
    assert raised.value.extra["required_plan"] == "growth"


# ----------------------------------------------------------------------------- the superadmin and one organization


def test_operator_sets_a_custom_plan_and_switches_apps_for_one_organization(shipped):
    operator = operator_browser(shipped)
    create(operator)
    org = build_org(shipped, "Contract", plan="trial", slug_email="c.test")
    status, body = operator.call(
        "POST",
        f"/api/platform/tenants/{org.org_id}/subscription",
        {
            "plan_id": "growth",
            "seats": 8,
            "months": 6,
            "apps": {"api": True},
            "note": "INV-7",
        },
    )
    assert status == 200, body
    assert (
        body["subscription"]["plan_id"] == "growth"
        and body["subscription"]["status"] == "active"
    )
    entitlements = shipped.entitlements.resolve(org.org_id, fresh=True)
    assert entitlements.has_app("api"), "granted although the plan does not offer it"
    assert (
        operator.call(
            "POST",
            f"/api/platform/tenants/{org.org_id}/subscription",
            {"plan_id": "trial", "seats": 8},
        )[0]
        == 400
    )
    assert (
        operator.call(
            "POST",
            f"/api/platform/tenants/{org.org_id}/subscription",
            {"plan_id": "growth", "seats": 1},
        )[0]
        == 400
    ), "the plan's minimum seats"
    # switch an included app off for this organization only
    status, body = operator.call(
        "PUT",
        f"/api/platform/tenants/{org.org_id}/apps/marketing",
        {"status": "disabled", "note": "abuse report 12"},
    )
    assert status == 200, body
    assert (
        next(a for a in body["apps"] if a["id"] == "marketing")["status"] == "blocked"
    )
    owner = sign_in(shipped, org.owner)
    status, refused = owner.call("GET", "/api/leads/overview")
    assert status == 403 and refused["code"] == "app_disabled", refused
    catalog = owner.call("GET", "/api/apps")[1]["apps"]
    marketing = next(a for a in catalog if a["id"] == "marketing")
    assert (
        not marketing["enabled"]
        and marketing["reason"] == "Switched off for your organization."
    )
    status, refused = owner.call("POST", "/api/org/apps/marketing/subscribe", {})
    assert status in (403, 409), refused
    other = build_org(shipped, "Other", plan="growth", seats=5, slug_email="o.test")
    assert shipped.entitlements.resolve(other.org_id, fresh=True).has_app("marketing")
    assert (
        operator.call(
            "PUT",
            f"/api/platform/tenants/{org.org_id}/apps/marketing",
            {"status": "disabled"},
        )[0]
        == 409
    )
    assert (
        operator.call(
            "PUT",
            f"/api/platform/tenants/{org.org_id}/apps/careers",
            {"status": "disabled"},
        )[0]
        == 400
    )
    status, body = operator.call(
        "PUT",
        f"/api/platform/tenants/{org.org_id}/apps/marketing",
        {"status": "enabled"},
    )
    assert status == 200
    assert (
        next(a for a in body["apps"] if a["id"] == "marketing")["status"] == "included"
    )
    assert owner.call("GET", "/api/leads/overview")[0] == 200
    # an app the organization pays for is not switched off behind its back
    shipped.billing.subscribe_app(org.owner, "social")
    status, body = operator.call(
        "PUT", f"/api/platform/tenants/{org.org_id}/apps/social", {"status": "disabled"}
    )
    assert status == 409 and body["code"] == "already_subscribed"
    # and an app switched off for everyone is off here too (the existing kill switch)
    operator.call(
        "PUT", "/api/platform/apps/marketing", {"enabled": False, "note": "incident"}
    )
    assert not shipped.entitlements.resolve(org.org_id, fresh=True).has_app("marketing")
    actions = platform_actions(shipped, "billing.")
    assert "billing.subscription_set" in actions
    assert "billing.app_disabled" in actions and "billing.app_enabled" in actions
    org_actions = [r["action"] for r in shipped.audit.list(org.org_id, limit=200)]
    assert "billing.app_disabled" in org_actions


def test_only_a_platform_admin_changes_plans(shipped):
    admin = operator_browser(shipped)
    create(admin)
    support = operator_browser(shipped, "support@aira.test", role="support")
    assert support.call("GET", "/api/platform/plans")[0] == 200
    for method, path, body in (
        ("POST", "/api/platform/plans", {**GROWTH, "plan_id": "other_plan"}),
        ("PUT", "/api/platform/plans/growth", {"name": "Mine"}),
        ("POST", "/api/platform/plans/growth/archive", {}),
        ("POST", "/api/platform/plans/growth/restore", {}),
        (
            "PUT",
            "/api/platform/plans/sort-order",
            {"order": shipped.catalog.order(include_archived=True)},
        ),
        ("PUT", "/api/platform/plans/signup-default", {"plan_id": "trial"}),
    ):
        assert support.call(method, path, body)[0] == 403, path
    org = build_org(shipped, "Members", plan="trial", slug_email="m.test")
    owner = sign_in(shipped, org.owner)
    assert owner.call("GET", "/api/platform/plans")[0] == 401
    assert owner.call("POST", "/api/platform/plans", GROWTH)[0] == 401
    assert (
        Browser(shipped).call(
            "PUT", "/api/platform/plans/signup-default", {"plan_id": "trial"}
        )[0]
        == 401
    )
    assert (
        support.call(
            "PUT",
            f"/api/platform/tenants/{org.org_id}/apps/marketing",
            {"status": "disabled"},
        )[0]
        == 403
    )
    assert shipped.catalog.name("growth") == "Growth"


def test_the_migration_adds_the_plan_columns(shipped):
    with shipped.db.read() as connection:
        columns = table_columns(connection, "plans")
    assert {"description", "archived_at", "created_by"} <= set(columns)
    assert shipped.catalog.current()["all_access"]["description"]


# ----------------------------------------------------------------------------- the console in a real browser


def test_the_console_plans_page_creates_a_plan_and_sets_the_default(served):  # noqa: F811
    playwright_api = pytest.importorskip("patchright.sync_api")
    app, base = served
    with playwright_api.sync_playwright() as playwright:
        try:
            chromium = playwright.chromium.launch()
        except Exception as error:  # noqa: BLE001
            pytest.skip(f"Chromium is not available: {error}")
        try:
            context, page, problems = _open(
                chromium, base, operator_browser(app), "#/platform/plans"
            )
            page.locator("[data-plan=all_access]").wait_for(timeout=30000)
            assert "New sign-ups" in page.locator("#cpSignup").inner_text()
            page.locator("#cpPlanNew").click()
            page.locator("#cpf-plan_id").wait_for(timeout=10000)
            page.locator("#cpf-plan_id").fill("schools")
            page.locator("#cpf-name").fill("Schools")
            page.locator("#cpf-description").fill("For universities")
            page.locator("#cpf-seat_dollars").fill("3")
            page.locator("#cpf-min_seats").fill("10")
            page.locator("#cpf-candidate_seats").fill("400")
            page.locator("input[data-feature='sources.linkedin']").check()
            page.locator("#cpf-apps-all").click()
            page.locator("select[data-app=social]").select_option("addon")
            page.locator("input[aria-label='social price per seat']").fill("1.25")
            page.locator("#cpPlanSave").click()
            page.locator("[data-plan=schools]").wait_for(timeout=30000)
            plan = app.catalog.get("schools")
            assert (
                plan["name"] == "Schools" and plan["pricing"]["seat_cents_month"] == 300
            )
            assert plan["pricing"]["min_seats"] == 10
            assert plan["entitlements"]["candidate_seats"] == 400
            assert "sources.linkedin" in plan["entitlements"]["features"]
            assert app.app_settings.state("schools", "marketing") == "included"
            assert app.app_settings.price("schools", "social") == 125
            # edit it: a new version
            page.locator("[data-plan-edit=schools]").click()
            page.locator("#cpf-apps_per_candidate_day").wait_for(timeout=10000)
            page.locator("#cpf-apps_per_candidate_day").fill("7")
            page.locator("#cpPlanSave").click()
            page.locator(".modal .actions button", has_text="Publish").click()
            page.locator("[data-plan=schools] h2", has_text="version 2").wait_for(
                timeout=30000
            )
            assert (
                app.catalog.get("schools")["entitlements"]["apps_per_candidate_day"]
                == 7
            )
            # new sign-ups start on it, as a 21-day trial
            page.locator("#cpSignupEdit").click()
            page.wait_for_selector(".modal select", timeout=10000)
            page.locator(".modal select").select_option("schools")
            page.locator(".modal input[type=number]").fill("21")
            page.locator(".modal .actions button", has_text="Save").click()
            page.locator("[data-signup=schools]").wait_for(timeout=30000)
            assert app.billing.signup_default() | {"set_at": None} == {
                "plan_id": "schools",
                "plan_name": "Schools",
                "trial_days": 21,
                "set_by": app.billing.signup_default()["set_by"],
                "set_at": None,
                "configured": True,
            }
            # one organization: put it on the new plan, then turn an app off for it alone
            org = build_org(app, "Campus", plan="trial", slug_email="campus.test")
            page.goto(f"{base}/#/platform/tenants/{org.org_id}")
            page.locator("button", has_text="Set subscription").first.click()
            page.wait_for_selector(".modal select", timeout=10000)
            page.locator(".modal select").first.select_option("schools")
            page.locator(".modal input[type=number]").first.fill("12")
            page.locator(".modal .actions button", has_text="Set subscription").click()
            page.locator(".cp-tiles", has_text="Schools").wait_for(timeout=30000)
            assert app.billing.subscription(org.org_id)["plan_id"] == "schools"
            page.locator("[data-app-off=marketing]").click()
            page.locator("tr", has_text="off for this organization").first.wait_for(
                timeout=30000
            )
            assert not app.entitlements.resolve(org.org_id, fresh=True).has_app(
                "marketing"
            )
            page.locator("[data-app-on=marketing]").click()
            page.locator("[data-app-off=marketing]").wait_for(timeout=30000)
            assert app.entitlements.resolve(org.org_id, fresh=True).has_app("marketing")
            assert not [p for p in problems if "/api/auth/me" not in p], problems
            context.close()
        finally:
            chromium.close()
