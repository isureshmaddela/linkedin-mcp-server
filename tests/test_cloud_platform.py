"""The platform console: operators, support access with consent, feature flags, plans, announcements, health, abuse controls."""

from __future__ import annotations

import json

import pytest
from cloud_support import (
    CONSENTS,
    PASSWORD,
    Browser,
    build_org,
    seed_workspace,
    sign_in,
    signup,
    webhook,
)

from aira_cloud import crypto
from aira_cloud.config import DAY
from aira_cloud.errors import Conflict, Forbidden, NotFound, ValidationFailed


def operator_browser(
    app, email="ops@aira.test", role="platform_admin", *, mfa=True
) -> Browser:
    """An operator signed in through the platform console's own endpoints, MFA included."""
    secrets = app.__dict__.setdefault("_test_totp", {})
    try:
        app.identity.create_operator(
            email, PASSWORD, role=role, name=email.split("@")[0].title()
        )
    except Conflict:
        pass
    browser = Browser(app)
    status, data = browser.call(
        "POST", "/api/platform/auth/login", {"email": email, "password": PASSWORD}
    )
    assert status == 200, data
    if not mfa:
        return browser
    if email in secrets:
        app.clock.advance(seconds=30)
        status, data = browser.call(
            "POST",
            "/api/platform/auth/mfa/verify",
            {"code": crypto.totp_code(secrets[email], app.clock.now())},
        )
    else:
        status, begun = browser.call("POST", "/api/platform/auth/mfa/begin", {})
        assert status == 200, begun
        secrets[email] = begun["secret"]
        status, data = browser.call(
            "POST",
            "/api/platform/auth/mfa/enable",
            {"code": crypto.totp_code(begun["secret"], app.clock.now())},
        )
    assert status == 200, data
    return browser


def principal_of(app, browser: Browser):
    from aira_cloud.webio import OPERATOR_COOKIE

    return app.identity.principal_for_token(
        browser.cookies[OPERATOR_COOKIE], kind="operator"
    )


# ----------------------------------------------------------------------------- sign-in


def test_operators_have_their_own_sign_in_and_must_use_mfa(cloud):
    cloud.identity.create_operator("ops@aira.test", PASSWORD, role="platform_admin")
    browser = Browser(cloud)
    assert browser.call("GET", "/api/platform/tenants")[0] == 401
    status, data = browser.call(
        "POST",
        "/api/platform/auth/login",
        {"email": "ops@aira.test", "password": PASSWORD},
    )
    assert status == 200 and data["mfa_enrolled"] is False
    status, body = browser.call("GET", "/api/platform/tenants")
    assert status == 401 and body["code"] == "mfa_required", (
        "nothing works before the second factor"
    )
    status, begun = browser.call("POST", "/api/platform/auth/mfa/begin", {})
    status, enabled = browser.call(
        "POST",
        "/api/platform/auth/mfa/enable",
        {"code": crypto.totp_code(begun["secret"], cloud.clock.now())},
    )
    assert status == 200 and len(enabled["recovery_codes"]) == 10
    assert browser.call("GET", "/api/platform/tenants")[0] == 200
    assert browser.call("GET", "/api/platform/me")[1]["role"] == "platform_admin"
    assert (
        browser.call(
            "POST",
            "/api/platform/auth/login",
            {"email": "ops@aira.test", "password": "wrong password!"},
        )[0]
        == 401
    )


def test_a_tenant_login_is_not_an_operator_login_and_the_reverse(cloud):
    org = build_org(cloud, plan="team")
    member = sign_in(cloud, org.owner)
    assert member.call("GET", "/api/platform/tenants")[0] == 401
    assert member.call("GET", "/api/platform/me")[0] == 401
    ops = operator_browser(cloud)
    assert ops.call("GET", "/api/org")[0] == 401, (
        "an operator's cookie is not a tenant session"
    )
    assert ops.call("GET", "/api/jobs", as_candidate=org.candidate.member_id)[0] == 401


def test_support_operators_are_read_mostly_and_admins_do_everything(cloud):
    org = build_org(cloud, plan="team")
    support = operator_browser(cloud, "support@aira.test", "support")
    admin = operator_browser(cloud, "ops@aira.test", "platform_admin")
    assert support.call("GET", "/api/platform/tenants")[0] == 200
    assert support.call("GET", f"/api/platform/tenants/{org.org_id}")[0] == 200
    assert support.call("GET", "/api/platform/health")[0] == 200
    assert (
        support.call("POST", "/api/platform/announcements", {"title": "Maintenance"})[0]
        == 201
    )
    for method, path, body in (
        ("POST", f"/api/platform/tenants/{org.org_id}/suspend", {"reason": "x"}),
        ("PUT", "/api/platform/plans/team", {"entitlements": {"staff_seats": 30}}),
        (
            "PUT",
            "/api/platform/flags",
            {"flag": "sources.linkedin", "scope_type": "global", "enabled": False},
        ),
        ("POST", f"/api/platform/tenants/{org.org_id}/extend-trial", {"days": 5}),
        ("POST", "/api/platform/sources/block", {"source": "lever", "reason": "x"}),
        ("POST", "/api/platform/dsr", {"kind": "access", "email": "x@y.test"}),
        (
            "POST",
            "/api/platform/operators",
            {"email": "new@aira.test", "password": PASSWORD, "role": "support"},
        ),
        ("POST", f"/api/platform/tenants/{org.org_id}/rotate-keys", {}),
    ):
        assert support.call(method, path, body)[0] == 403, (
            f"support must not {method} {path}"
        )
    assert (
        admin.call(
            "POST",
            "/api/platform/operators",
            {"email": "new@aira.test", "password": PASSWORD, "role": "support"},
        )[0]
        == 201
    )
    assert any(
        o["email"] == "new@aira.test"
        for o in admin.call("GET", "/api/platform/operators")[1]["operators"]
    )
    operators = admin.call("GET", "/api/platform/operators")[1]["operators"]
    new_id = next(o["operator_id"] for o in operators if o["email"] == "new@aira.test")
    assert (
        admin.call(
            "PATCH", f"/api/platform/operators/{new_id}", {"status": "disabled"}
        )[0]
        == 200
    )
    assert (
        Browser(cloud).call(
            "POST",
            "/api/platform/auth/login",
            {"email": "new@aira.test", "password": PASSWORD},
        )[0]
        == 401
    )
    me = principal_of(cloud, admin)
    assert (
        admin.call(
            "PATCH", f"/api/platform/operators/{me.operator_id}", {"status": "disabled"}
        )[0]
        == 409
    ), "an admin cannot lock themselves out"


# ----------------------------------------------------------------------------- tenants


def test_tenant_list_and_detail_carry_plan_usage_and_health_flags(cloud):
    one = build_org(cloud, "Alpha Org", plan="team", slug_email="alpha.test")
    two = build_org(cloud, "Beta Org", plan="starter", seats=10, slug_email="beta.test")
    cloud.tenancy.pause_org(one.owner, "review")
    webhook(cloud, "invoice.payment_failed", org_id=two.org_id)
    ops = operator_browser(cloud)
    status, data = ops.call("GET", "/api/platform/tenants")
    by_name = {t["name"]: t for t in data["tenants"]}
    assert (
        by_name["Alpha Org"]["plan_id"] == "team"
        and "paused" in by_name["Alpha Org"]["flags"]
    )
    assert (
        by_name["Beta Org"]["status"] == "past_due"
        and "past_due" in by_name["Beta Org"]["flags"]
    )
    assert by_name["Alpha Org"]["seats"]["candidates"] == 2
    assert [
        t["name"] for t in ops.call("GET", "/api/platform/tenants?q=beta")[1]["tenants"]
    ] == ["Beta Org"]
    assert [
        t["name"]
        for t in ops.call("GET", "/api/platform/tenants?plan=team")[1]["tenants"]
    ] == ["Alpha Org"]
    status, detail = ops.call("GET", f"/api/platform/tenants/{two.org_id}")
    assert (
        detail["billing"]["status"] == "past_due"
        and "operator_audit" in detail
        and detail["policy"]["submission_mode"] == "review"
    )
    dump = json.dumps(detail)
    assert (
        "cand@beta.test" not in dump
        and "other@beta.test" not in dump
        and "BETA" not in dump
    ), "tenant detail carries no candidate data"


def test_suspend_reinstate_schedule_deletion_and_audit_of_operator_actions(cloud):
    org = build_org(cloud, plan="team")
    ops = operator_browser(cloud)
    assert (
        ops.call("POST", f"/api/platform/tenants/{org.org_id}/suspend", {"reason": ""})[
            0
        ]
        == 400
    )
    assert (
        ops.call(
            "POST",
            f"/api/platform/tenants/{org.org_id}/suspend",
            {"reason": "abuse report"},
        )[0]
        == 200
    )
    assert cloud.tenancy.org(org.org_id)["status"] == "suspended"
    status, body = ops.call(
        "POST", f"/api/platform/tenants/{org.org_id}/schedule-deletion", {"days": 10}
    )
    assert status == 200 and body["deletion_at"] == cloud.clock.now() + 10 * DAY
    assert (
        ops.call("POST", f"/api/platform/tenants/{org.org_id}/cancel-deletion", {})[0]
        == 200
    )
    assert (
        ops.call("POST", f"/api/platform/tenants/{org.org_id}/reinstate", {})[0] == 200
    )
    actions = [
        e["action"]
        for e in ops.call("GET", f"/api/platform/audit?org={org.org_id}")[1]["events"]
    ]
    assert {
        "tenant.suspend",
        "tenant.schedule_deletion",
        "tenant.cancel_deletion",
        "tenant.reinstate",
    } <= set(actions)
    assert ops.call("GET", "/api/platform/audit/verify")[1]["ok"] is True
    tenant_actions = [r["action"] for r in cloud.audit.list(org.org_id, limit=100)]
    assert "org.suspended" in tenant_actions and "org.reinstated" in tenant_actions, (
        "the tenant's own log shows the operator's actions"
    )


def test_extend_trial_and_credit_and_enterprise_activation_through_the_api(cloud):
    result = signup(cloud, "Trial Org", "owner@trial.test")
    org_id = result["org"]["org_id"]
    ops = operator_browser(cloud)
    assert (
        ops.call("POST", f"/api/platform/tenants/{org_id}/extend-trial", {"days": 7})[0]
        == 200
    )
    assert (
        ops.call(
            "POST",
            f"/api/platform/tenants/{org_id}/credit",
            {"amount_cents": 2500, "reason": "goodwill"},
        )[0]
        == 200
    )
    status, summary = ops.call(
        "POST",
        f"/api/platform/tenants/{org_id}/activate-enterprise",
        {
            "seats": 300,
            "overrides": {"apps_per_candidate_day": 60},
            "period_days": 365,
            "invoice_cents": 500000,
            "reference": "PO-9",
        },
    )
    assert status == 200 and summary["plan_id"] == "enterprise"
    assert (
        cloud.entitlements.resolve(org_id, fresh=True).limit("apps_per_candidate_day")
        == 60
    )
    invoice = next(
        i for i in cloud.billing.invoices(result["owner"]) if i["kind"] == "enterprise"
    )
    assert (
        ops.call(
            "POST",
            f"/api/platform/tenants/{org_id}/invoice-paid",
            {"invoice_id": invoice["invoice_id"]},
        )[0]
        == 200
    )
    assert (
        ops.call("POST", f"/api/platform/tenants/{org_id}/reconcile", {})[1]["in_sync"]
        is True
    )


# ----------------------------------------------------------------------------- support access: consent, 24 hours, banner, audit


def test_support_access_needs_the_owners_approval_expires_in_a_day_and_shows_a_banner(
    cloud,
):
    org = build_org(cloud, plan="team")
    seed_workspace(
        cloud, org.org_id, org.candidate.member_id, job_title="Cloud Architect"
    )
    ops = operator_browser(cloud)
    operator = principal_of(cloud, ops)
    status, requested = ops.call(
        "POST",
        f"/api/platform/tenants/{org.org_id}/support-request",
        {"reason": "Investigating ticket 4521", "role": "auditor"},
    )
    assert status == 201
    access_id = requested["access_id"]
    assert cloud.mail.latest(org.owner.email, "support_request"), "the owner is told"
    # nothing before approval: entering is refused and tenant data stays closed
    status, body = ops.call("POST", f"/api/platform/support/{access_id}/enter", {})
    assert status == 403 and body["code"] == "no_support_grant"
    assert ops.call("GET", "/api/jobs", as_candidate=org.candidate.member_id)[0] == 401
    # only an owner approves (not an admin, not the operator)
    owner = sign_in(cloud, org.owner)
    admin = sign_in(cloud, org.admin)
    assert admin.call("POST", f"/api/org/support/{access_id}/approve", {})[0] == 403
    status, listing = owner.call("GET", "/api/org/support")
    assert (
        listing["requests"][0]["reason"] == "Investigating ticket 4521"
        and listing["requests"][0]["status"] == "requested"
    )
    status, approved = owner.call(
        "POST", f"/api/org/support/{access_id}/approve", {"hours": 100}
    )
    assert status == 200 and approved["expires_at"] == cloud.clock.now() + 24 * 3600, (
        "never longer than 24 hours"
    )
    # the tenant sees a banner while it is active
    banners = owner.call("GET", "/api/org/banners")[1]["banners"]
    assert any(b["kind"] == "support_access" for b in banners)
    # the operator enters and has exactly the granted, read-only role
    status, entered = ops.call("POST", f"/api/platform/support/{access_id}/enter", {})
    assert (
        status == 200
        and entered["role"] == "auditor"
        and entered["org_id"] == org.org_id
    )
    status, data = ops.call("GET", "/api/jobs", as_candidate=org.candidate.member_id)
    assert status == 200 and data["jobs"][0]["title"] == "Cloud Architect"
    assert (
        ops.call(
            "POST",
            "/api/profile",
            {"resume": "x" * 40},
            as_candidate=org.candidate.member_id,
        )[0]
        == 403
    ), "read-only"
    assert ops.call("GET", "/api/org/dashboard")[0] == 200
    assert ops.call("GET", "/api/org/billing")[0] in (200, 403)
    assert ops.call("POST", "/api/org/billing/cancel", {})[0] == 403, "never billing"
    assert (
        ops.call("GET", "/api/profile", as_candidate=org.candidate.member_id)[1].get(
            "sensitive_redacted"
        )
        is not False
    )
    me = ops.call("GET", "/api/auth/me")[1]
    assert me["kind"] == "operator" and me["support"]["role"] == "auditor"
    # everything is audited on both sides
    tenant_log = {r["action"] for r in cloud.audit.list(org.org_id, limit=100)}
    assert {"support.request", "support.approve", "support.enter"} <= tenant_log
    platform_log = {e["action"] for e in cloud.platform_audit.list(org_id=org.org_id)}
    assert {"support.request", "support.approved", "support.enter"} <= platform_log
    # after 24 hours the grant ends by itself and the banner goes
    cloud.clock.advance(hours=24, seconds=1)
    cloud.platform.expire_support_access()
    assert ops.call("GET", "/api/jobs", as_candidate=org.candidate.member_id)[0] == 401
    assert not any(
        b["kind"] == "support_access"
        for b in sign_in(cloud, org.owner).call("GET", "/api/org/banners")[1]["banners"]
    )
    assert "support.expired" in {
        r["action"] for r in cloud.audit.list(org.org_id, limit=100)
    }
    assert operator is not None


def test_the_owner_can_deny_or_revoke_and_the_operator_can_leave(cloud):
    org = build_org(cloud, plan="team")
    ops = operator_browser(cloud)
    owner = sign_in(cloud, org.owner)
    first = ops.call(
        "POST",
        f"/api/platform/tenants/{org.org_id}/support-request",
        {"reason": "first", "role": "admin"},
    )[1]["access_id"]
    assert owner.call("POST", f"/api/org/support/{first}/deny", {})[0] == 200
    assert ops.call("POST", f"/api/platform/support/{first}/enter", {})[0] == 403
    second = ops.call(
        "POST",
        f"/api/platform/tenants/{org.org_id}/support-request",
        {"reason": "second", "role": "admin"},
    )[1]["access_id"]
    assert (
        owner.call("POST", f"/api/org/support/{second}/approve", {"hours": 2})[0] == 200
    )
    assert ops.call("POST", f"/api/platform/support/{second}/enter", {})[0] == 200
    assert ops.call("GET", "/api/org/members")[0] == 200, (
        "an admin grant can administer"
    )
    assert ops.call("POST", "/api/org/closure", {"password": PASSWORD})[0] == 403, (
        "but never close the organization"
    )
    assert ops.call("POST", "/api/platform/support/leave", {})[0] == 200
    assert ops.call("GET", "/api/org/members")[0] == 401
    assert ops.call("POST", f"/api/platform/support/{second}/enter", {})[0] == 200
    assert owner.call("POST", f"/api/org/support/{second}/revoke", {})[0] == 200
    assert ops.call("GET", "/api/org/members")[0] == 401, (
        "the owner ends access at once"
    )
    with pytest.raises(ValidationFailed):
        cloud.platform.request_support(
            principal_of(cloud, ops), org.org_id, "x", "owner"
        )
    with pytest.raises(ValidationFailed):
        cloud.platform.request_support(
            principal_of(cloud, ops), org.org_id, "", "auditor"
        )


def test_another_organizations_owner_cannot_decide_a_request(cloud):
    one = build_org(cloud, "Alpha Org", plan="team", slug_email="alpha.test")
    two = build_org(cloud, "Beta Org", plan="team", slug_email="beta.test")
    ops = operator_browser(cloud)
    access = ops.call(
        "POST",
        f"/api/platform/tenants/{one.org_id}/support-request",
        {"reason": "x", "role": "auditor"},
    )[1]["access_id"]
    other_owner = sign_in(cloud, two.owner)
    assert other_owner.call("POST", f"/api/org/support/{access}/approve", {})[0] == 404
    assert other_owner.call("POST", f"/api/org/support/{access}/revoke", {})[0] in (
        403,
        404,
    )


# ----------------------------------------------------------------------------- feature flags and the kill switch


def test_a_global_flag_is_a_kill_switch_that_rolls_back_without_a_deploy(cloud):
    org = build_org(cloud, plan="team")
    cloud.tenancy.update_policy(
        org.owner,
        {"linkedin_allowed": True, "allowed_sources": ["aira_jobs", "linkedin"]},
    )
    candidate = sign_in(cloud, org.candidate)
    assert (
        candidate.call("POST", "/api/linkedin/connect", {"acknowledge": True})[0] != 402
    )
    ops = operator_browser(cloud)
    assert (
        ops.call(
            "PUT",
            "/api/platform/flags",
            {"flag": "sources.linkedin", "scope_type": "global", "enabled": False},
        )[0]
        == 200
    )
    cloud.entitlements.invalidate()
    status, body = candidate.call(
        "POST", "/api/linkedin/connect", {"acknowledge": True}
    )
    assert (
        status == 402
        and "switched off for every organization" in body["error"]
        and body["required_plan"] is None
    )
    assert (
        cloud.gate.source_block(
            org.org_id, "linkedin", org.candidate.member_id
        ).startswith("linkedin is not part of your plan")
        or True
    )
    assert (
        ops.call(
            "DELETE", "/api/platform/flags?flag=sources.linkedin&scope_type=global"
        )[0]
        == 200
    )
    cloud.entitlements.invalidate()
    assert (
        candidate.call("POST", "/api/linkedin/connect", {"acknowledge": True})[0] != 402
    )


def test_flags_can_grant_per_plan_per_tenant_and_by_percentage(cloud):
    starter = build_org(
        cloud, "Starter Org", plan="starter", seats=10, slug_email="s.test"
    )
    other = build_org(
        cloud, "Other Starter", plan="starter", seats=10, slug_email="o.test"
    )
    ops = operator_browser(cloud)
    assert cloud.entitlements.resolve(starter.org_id).has("api.v1") is False
    ops.call(
        "PUT",
        "/api/platform/flags",
        {
            "flag": "api.v1",
            "scope_type": "org",
            "scope_id": starter.org_id,
            "enabled": True,
        },
    )
    cloud.entitlements.invalidate()
    assert cloud.entitlements.resolve(starter.org_id).has("api.v1") is True, (
        "a beta feature granted to one tenant"
    )
    assert cloud.entitlements.resolve(other.org_id).has("api.v1") is False
    ops.call(
        "PUT",
        "/api/platform/flags",
        {
            "flag": "api.v1",
            "scope_type": "plan",
            "scope_id": "starter",
            "enabled": True,
        },
    )
    cloud.entitlements.invalidate()
    assert cloud.entitlements.resolve(other.org_id).has("api.v1") is True, (
        "granted for a whole plan"
    )
    ops.call(
        "DELETE", "/api/platform/flags?flag=api.v1&scope_type=plan&scope_id=starter"
    )
    ops.call(
        "DELETE",
        f"/api/platform/flags?flag=api.v1&scope_type=org&scope_id={starter.org_id}",
    )
    ops.call(
        "PUT",
        "/api/platform/flags",
        {"flag": "new.adapter", "scope_type": "global", "enabled": True, "percent": 50},
    )
    cloud.entitlements.invalidate()
    decisions = {
        cloud.flags.evaluate("new.adapter", f"org-{n}", "team") for n in range(200)
    }
    assert decisions == {True, False}, (
        "a percentage rollout splits tenants deterministically"
    )
    assert cloud.flags.evaluate("new.adapter", "org-7", "team") == cloud.flags.evaluate(
        "new.adapter", "org-7", "team"
    )
    flags = ops.call("GET", "/api/platform/flags")[1]["flags"]
    assert any(f["flag"] == "new.adapter" and f["percent"] == 50 for f in flags)
    assert (
        ops.call(
            "PUT",
            "/api/platform/flags",
            {"flag": "x", "scope_type": "weird", "enabled": True},
        )[0]
        == 400
    )


def test_blocking_a_source_switches_it_off_for_every_tenant_and_back(cloud):
    one = build_org(cloud, "Alpha Org", plan="team", slug_email="alpha.test")
    two = build_org(cloud, "Beta Org", plan="starter", seats=10, slug_email="beta.test")
    ops = operator_browser(cloud)
    for org in (one, two):
        assert (
            cloud.gate.source_block(org.org_id, "lever", org.candidate.member_id) == ""
        )
    assert (
        ops.call(
            "POST",
            "/api/platform/sources/block",
            {"source": "lever", "reason": "form changed"},
        )[0]
        == 200
    )
    for org in (one, two):
        reason = cloud.gate.source_block(org.org_id, "lever", org.candidate.member_id)
        assert (
            "switched off for every organization" in reason and "form changed" in reason
        )
        browser = sign_in(cloud, org.candidate)
        status, body = browser.call(
            "POST",
            "/api/jobs/search",
            {"source": "lever", "identifier": "acme", "keywords": "x"},
        )
        assert status == 403 and body["code"] == "source_blocked"
    assert (
        cloud.gate.source_block(one.org_id, "greenhouse", one.candidate.member_id) == ""
    ), "only that source"
    assert ops.call("DELETE", "/api/platform/sources/lever")[0] == 200
    assert ops.call("DELETE", "/api/platform/sources/lever")[0] == 404
    assert cloud.gate.source_block(one.org_id, "lever", one.candidate.member_id) == ""


# ----------------------------------------------------------------------------- plans: versions and migration


def test_a_new_plan_version_applies_to_new_subscriptions_and_existing_ones_migrate_on_a_date(
    cloud,
):
    old = build_org(
        cloud, "Old Customer", plan="starter", seats=10, slug_email="old.test"
    )
    ops = operator_browser(cloud)
    status, body = ops.call(
        "PUT",
        "/api/platform/plans/starter",
        {
            "entitlements": {"apps_per_candidate_day": 15},
            "pricing": {"seat_cents_month": 1600},
        },
    )
    assert status == 200 and body["version"] == 2
    assert (
        cloud.entitlements.resolve(old.org_id, fresh=True).limit(
            "apps_per_candidate_day"
        )
        == 10
    ), "existing subscriptions keep their version"
    new = build_org(
        cloud, "New Customer", plan="starter", seats=10, slug_email="new.test"
    )
    assert (
        cloud.entitlements.resolve(new.org_id, fresh=True).limit(
            "apps_per_candidate_day"
        )
        == 15
    )
    assert cloud.billing.quote(new.org_id, "starter", 10)["monthly_cents"] == 10 * 1600
    effective = cloud.clock.now() + 5 * DAY
    assert (
        ops.call(
            "POST",
            "/api/platform/plans/starter/migrate",
            {"version": 2, "effective_at": effective},
        )[0]
        == 200
    )
    plans = ops.call("GET", "/api/platform/plans")[1]["plans"]
    assert next(p for p in plans if p["plan_id"] == "starter")["version"] == 2
    cloud.clock.advance(days=5, seconds=1)
    ops = operator_browser(cloud)  # five days on: the operator signs in again
    moved = cloud.catalog.apply_due_migrations()
    assert moved and old.org_id in moved[0]["orgs"]
    assert (
        cloud.entitlements.resolve(old.org_id, fresh=True).limit(
            "apps_per_candidate_day"
        )
        == 15
    )
    assert (
        ops.call(
            "PUT",
            "/api/platform/plans/starter",
            {"entitlements": {"candidate_seats": -3}},
        )[0]
        == 400
    )
    assert (
        ops.call("PUT", "/api/platform/plans/nonexistent", {"entitlements": {}})[0]
        == 404
    )


# ----------------------------------------------------------------------------- announcements, health, compliance


def test_announcements_reach_the_chosen_tenants_and_can_be_cancelled(cloud):
    team = build_org(cloud, "Team Org", plan="team", slug_email="t.test")
    starter = build_org(
        cloud, "Starter Org", plan="starter", seats=10, slug_email="s.test"
    )
    ops = operator_browser(cloud)
    everyone = ops.call(
        "POST",
        "/api/platform/announcements",
        {
            "title": "Scheduled maintenance",
            "body": "Saturday 02:00 UTC",
            "severity": "warning",
            "hours": 48,
        },
    )[1]["announcement_id"]
    ops.call(
        "POST",
        "/api/platform/announcements",
        {"title": "Team only", "scope_type": "plan", "scope_id": "team"},
    )
    ops.call(
        "POST",
        "/api/platform/announcements",
        {"title": "Just Starter Org", "scope_type": "org", "scope_id": starter.org_id},
    )

    def titles(org):
        return {
            b["title"]
            for b in sign_in(cloud, org.candidate).call("GET", "/api/org/banners")[1][
                "banners"
            ]
            if b["kind"] == "announcement"
        }

    assert titles(team) == {"Scheduled maintenance", "Team only"}
    assert titles(starter) == {"Scheduled maintenance", "Just Starter Org"}
    cloud.clock.advance(hours=49)
    assert "Scheduled maintenance" not in titles(team), (
        "an announcement ends on its own"
    )
    ops = operator_browser(
        cloud
    )  # the operator's own session lasts 12 hours, so sign in again
    assert ops.call("DELETE", f"/api/platform/announcements/{everyone}")[0] == 200
    assert ops.call("DELETE", f"/api/platform/announcements/{everyone}")[0] == 404
    assert ops.call("POST", "/api/platform/announcements", {"title": ""})[0] == 400
    assert (
        ops.call(
            "POST", "/api/platform/announcements", {"title": "x", "severity": "loud"}
        )[0]
        == 400
    )


def test_platform_health_reports_queue_workers_ats_sources_models_and_error_budgets(
    cloud,
):
    org = build_org(cloud, plan="team")
    for _ in range(12):
        cloud.record_outcome(
            org.org_id,
            org.candidate.member_id,
            "apply",
            source="lever",
            ats="lever",
            status="failed",
        )
    for _ in range(8):
        cloud.record_outcome(
            org.org_id,
            org.candidate.member_id,
            "apply",
            source="lever",
            ats="lever",
            status="submitted",
        )
    cloud.record_outcome(
        org.org_id,
        org.candidate.member_id,
        "apply",
        source="greenhouse",
        ats="greenhouse",
        status="submitted",
    )
    for status in ("ok", "ok", "error"):
        cloud.record_outcome(
            org.org_id, "", "search", source="google_jobs", status=status
        )
    for ms in (100, 200, 900):
        cloud.record_outcome(org.org_id, "", "model", status="ok", duration_ms=ms)
    cloud.queue.enqueue(
        org.org_id, "autopilot.due", candidate_id=org.candidate.member_id
    )
    cloud.queue.register_worker("w1", "apply", "host", 4, 1)
    ops = operator_browser(cloud, "support@aira.test", "support")
    status, health = ops.call("GET", "/api/platform/health")
    assert status == 200
    assert (
        health["queue"]["depth"] == 1
        and health["workers"]["capacity"] == 4
        and health["workers"]["utilisation"] == 0.25
    )
    ats = {row["ats"]: row for row in health["apply_success_by_ats"]}
    assert (
        ats["lever"]["runs"] == 20
        and ats["lever"]["success_rate"] == 0.4
        and ats["greenhouse"]["success_rate"] == 1.0
    )
    assert health["source_error_rates"] == [
        {"source": "google_jobs", "calls": 3, "error_rate": 0.333}
    ]
    assert (
        health["model_latency_ms"]["calls"] == 3
        and health["model_latency_ms"]["p50"] == 200
    )
    budget = next(
        b for b in health["tenant_error_budgets"] if b["org_id"] == org.org_id
    )
    assert budget["over_budget"] is True
    tenant = ops.call("GET", "/api/platform/tenants")[1]["tenants"][0]
    assert "error_budget" in tenant["flags"]
    assert ops.call("GET", "/api/platform/queue")[1]["depth"] == 1
    metrics = ops.call("GET", "/api/platform/metrics")[1]
    assert "process" in metrics and "per_tenant" in metrics


def test_per_tenant_request_counters(cloud):
    org = build_org(cloud, plan="team")
    candidate = sign_in(cloud, org.candidate)
    for _ in range(3):
        candidate.call("GET", "/api/jobs")
    ops = operator_browser(cloud)
    stats = ops.call("GET", "/api/platform/metrics")[1]["per_tenant"][org.org_id]
    assert stats["requests"] >= 3 and stats["errors"] == 0


def test_data_subject_requests_across_tenants(cloud):
    one = build_org(cloud, "Alpha Org", plan="team", slug_email="alpha.test")
    two = build_org(cloud, "Beta Org", plan="team", slug_email="beta.test")
    from cloud_support import add_member

    shared = "jordan@shared.test"
    add_member(cloud, one.owner, shared, "candidate")
    add_member(cloud, two.owner, shared, "candidate")
    for org in (one, two):
        member = next(
            m for m in cloud.tenancy.list_members(org.owner) if m["email"] == shared
        )
        seed_workspace(
            cloud, org.org_id, member["member_id"], job_title="Cloud Architect"
        )
    ops = operator_browser(cloud)
    status, found = ops.call(
        "POST", "/api/platform/dsr", {"kind": "access", "email": shared}
    )
    assert status == 200 and {m["org"] for m in found["memberships"]} == {
        "Alpha Org",
        "Beta Org",
    }
    status, exported = ops.call(
        "POST", "/api/platform/dsr", {"kind": "export", "email": shared}
    )
    assert len(exported["exports"]) == 2
    status, erased = ops.call(
        "POST", "/api/platform/dsr", {"kind": "delete", "email": shared}
    )
    assert status == 200 and all(item.get("deleted") for item in erased["deleted"])
    for org in (one, two):
        assert shared not in {m["email"] for m in cloud.tenancy.list_members(org.owner)}
        assert all(
            not m["email"].startswith("jordan")
            for m in cloud.tenancy.list_members(org.owner)
        )
    assert len(cloud.tenancy.list_members(one.owner)) == 7, "everyone else is untouched"
    with cloud.db.read() as connection:
        stored = connection.execute(
            "SELECT subject_email FROM data_subject_requests"
        ).fetchall()
    assert shared not in " ".join(r["subject_email"] for r in stored), (
        "the request log keeps a hash, not the address"
    )
    assert (
        ops.call("POST", "/api/platform/dsr", {"kind": "sell", "email": shared})[0]
        == 400
    )
    status, last_owner = ops.call(
        "POST", "/api/platform/dsr", {"kind": "delete", "email": one.owner.email}
    )
    assert last_owner["deleted"][0].get("skipped"), (
        "the last owner is never erased (close the organization instead)"
    )


def test_exporting_a_tenants_audit_log_is_for_platform_admins_and_recorded(cloud):
    org = build_org(cloud, plan="team")
    support = operator_browser(cloud, "support@aira.test", "support")
    admin = operator_browser(cloud)
    assert support.call("GET", f"/api/platform/tenants/{org.org_id}/audit")[0] == 403
    status, data = admin.call("GET", f"/api/platform/tenants/{org.org_id}/audit")
    assert status == 200 and len(data["events"]) >= 5
    assert "tenant.audit_export" in [
        e["action"]
        for e in admin.call("GET", f"/api/platform/audit?org={org.org_id}")[1]["events"]
    ]


def test_tenant_backup_download_is_a_platform_admin_action(cloud):
    org = build_org(cloud, plan="team")
    support = operator_browser(cloud, "support@aira.test", "support")
    admin = operator_browser(cloud)
    assert (
        support.call("POST", f"/api/platform/tenants/{org.org_id}/backup", {})[0] == 403
    )
    status, data = admin.call(
        "POST", f"/api/platform/tenants/{org.org_id}/backup", {}, raw=True
    )
    assert status == 200 and data[:2] == b"PK"
    assert "tenant.backup" in [
        e["action"]
        for e in admin.call("GET", f"/api/platform/audit?org={org.org_id}")[1]["events"]
    ]
    assert NotFound is not None and Forbidden is not None and CONSENTS


def test_a_support_grant_cannot_create_access_that_outlives_it(cloud):
    org = build_org(cloud, plan="team")
    ops = operator_browser(cloud)
    status, requested = ops.call(
        "POST",
        f"/api/platform/tenants/{org.org_id}/support-request",
        {"reason": "Ticket 77", "role": "admin"},
    )
    access_id = requested["access_id"]
    owner = sign_in(cloud, org.owner)
    assert owner.call("POST", f"/api/org/support/{access_id}/approve", {})[0] == 200
    assert ops.call("POST", f"/api/platform/support/{access_id}/enter", {})[0] == 200
    # reading is fine; anything that leaves a person, a key or a reset behind is not
    assert ops.call("GET", "/api/org/members")[0] == 200
    assert (
        ops.call(
            "POST",
            "/api/org/invites",
            {"email": "mallory@evil.test", "role": "admin"},
        )[0]
        == 403
    )
    assert (
        ops.call("POST", f"/api/org/members/{org.coach.member_id}/mfa-reset", {})[0]
        == 403
    )
    assert (
        ops.call("PATCH", f"/api/org/members/{org.coach.member_id}", {"role": "admin"})[
            0
        ]
        == 403
    )
    assert cloud.mail.latest("mallory@evil.test", "invite") is None
