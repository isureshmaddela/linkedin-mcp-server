"""The small API the billing screens and platform console add: trial plan, plan versions, tenant invoices."""

from __future__ import annotations

from cloud_support import PASSWORD, Browser, build_org, sign_in  # noqa: F401

from aira_cloud import crypto


def operator_browser(
    app, email: str = "ops@aira.test", role: str = "platform_admin"
) -> Browser:
    app.identity.create_operator(email, PASSWORD, role=role)
    browser = Browser(app)
    status, data = browser.call(
        "POST", "/api/platform/auth/login", {"email": email, "password": PASSWORD}
    )
    assert status == 200, data
    status, begun = browser.call("POST", "/api/platform/auth/mfa/begin", {})
    status, done = browser.call(
        "POST",
        "/api/platform/auth/mfa/enable",
        {"code": crypto.totp_code(begun["secret"], app.clock.now())},
    )
    assert status == 200, done
    return browser


def test_trial_plan_is_public_and_shaped_like_a_public_plan(cloud):
    status, data = Browser(cloud).call("GET", "/api/billing/trial-plan")
    assert status == 200
    plan = data["plan"]
    assert plan["plan_id"] == "trial" and plan["limits"]["candidate_seats"] == 3
    assert "sources.aira_jobs" in plan["features"] and "features" not in plan["limits"]
    _, public = Browser(cloud).call("GET", "/api/billing/plans")
    assert {p["plan_id"] for p in public["plans"]} == {
        "starter",
        "team",
        "enterprise",
    }, "the trial stays out of the public list"
    assert set(plan) >= set(public["plans"][0]) - {"trial_days"}


def test_plan_versions_count_subscriptions_per_version(cloud):
    one = build_org(cloud, "First Org", plan="starter", seats=10, slug_email="one.test")
    build_org(cloud, "Second Org", plan="starter", seats=10, slug_email="two.test")
    operator = operator_browser(cloud)
    status, data = operator.call("GET", "/api/platform/plans/versions")
    assert status == 200 and data["versions"]["starter"] == {"1": 2}
    version = cloud.catalog.update(
        "starter", entitlements={"apps_per_candidate_day": 12}, pricing=None, by="test"
    )
    status, data = operator.call("GET", "/api/platform/plans/versions")
    assert version == 2 and data["versions"]["starter"] == {"1": 2}, (
        "existing subscriptions stay on version 1 until migrated"
    )
    cloud.catalog.schedule_migration("starter", 2, cloud.clock.now() - 1, "test")
    cloud.catalog.apply_due_migrations()
    status, data = operator.call("GET", "/api/platform/plans/versions")
    assert data["versions"]["starter"] == {"2": 2}
    assert one


def test_plan_versions_and_tenant_invoices_need_an_operator(cloud):
    org = build_org(cloud, plan="starter", seats=10)
    member = sign_in(cloud, org.owner)
    assert member.call("GET", "/api/platform/plans/versions")[0] == 401
    assert member.call("GET", f"/api/platform/tenants/{org.org_id}/invoices")[0] == 401
    assert Browser(cloud).call("GET", "/api/platform/plans/versions")[0] == 401


def test_operator_lists_a_tenants_invoices_and_marks_one_paid(cloud):
    org = build_org(cloud, plan="starter", seats=10)
    operator = operator_browser(cloud)
    status, data = operator.call("GET", f"/api/platform/tenants/{org.org_id}/invoices")
    assert (
        status == 200
        and any(
            i["kind"] == "subscription" or i["kind"] == "proration"
            for i in data["invoices"]
        )
        or data["invoices"] == []
    )
    status, data = operator.call(
        "POST",
        f"/api/platform/tenants/{org.org_id}/activate-enterprise",
        {"seats": 100, "invoice_cents": 250000, "reference": "PO-9", "overrides": {}},
    )
    assert status == 200, data
    status, listed = operator.call(
        "GET", f"/api/platform/tenants/{org.org_id}/invoices"
    )
    invoice = next(i for i in listed["invoices"] if i["kind"] == "enterprise")
    assert (
        invoice["amount_cents"] == 250000
        and invoice["status"] == "open"
        and invoice["lines"] == []
    )
    status, done = operator.call(
        "POST",
        f"/api/platform/tenants/{org.org_id}/invoice-paid",
        {"invoice_id": invoice["invoice_id"]},
    )
    assert status == 200
    status, listed = operator.call(
        "GET", f"/api/platform/tenants/{org.org_id}/invoices"
    )
    assert (
        next(i for i in listed["invoices"] if i["kind"] == "enterprise")["status"]
        == "paid"
    )
    status, missing = operator.call("GET", f"/api/platform/tenants/{'0' * 32}/invoices")
    assert status == 404


def test_a_support_operator_can_read_invoices_but_not_change_them(cloud):
    org = build_org(cloud, plan="starter", seats=10)
    support = operator_browser(cloud, "support@aira.test", "support")
    assert support.call("GET", f"/api/platform/tenants/{org.org_id}/invoices")[0] == 200
    assert (
        support.call(
            "POST",
            f"/api/platform/tenants/{org.org_id}/invoice-paid",
            {"invoice_id": "x" * 32},
        )[0]
        == 403
    )
    assert support.call("GET", "/api/platform/plans/versions")[0] == 200
