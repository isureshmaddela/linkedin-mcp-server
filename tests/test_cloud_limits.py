"""Contract limits set by an operator, and document retention that follows the policy and the plan."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from cloud_support import build_org, local_module, seed_workspace, sign_in
from test_cloud_platform import operator_browser, principal_of

from aira_cloud.errors import ValidationFailed


def enterprise(cloud, name="Contract Org", slug="contract.test", **overrides):
    org = build_org(cloud, name, plan="team", seats=25, slug_email=slug)
    ops = operator_browser(cloud)
    cloud.billing.activate_enterprise(
        principal_of(cloud, ops), org.org_id, seats=500, overrides=overrides
    )
    cloud.entitlements.invalidate(org.org_id)
    return org, ops


# --------------------------------------------------------------------------- custom limits


def test_an_operator_sets_contract_limits_that_replace_the_plans(cloud):
    org, ops = enterprise(cloud)
    assert cloud.entitlements.resolve(org.org_id).limit("apps_per_org_month") == 250000
    status, body = ops.call(
        "PUT",
        f"/api/platform/tenants/{org.org_id}/limits",
        {
            "limits": {"apps_per_org_month": 600000, "storage_bytes": None},
            "reason": "Order form OF-1182",
        },
    )
    assert status == 200, body
    assert body["overrides"]["apps_per_org_month"] == 600000
    assert body["effective"]["apps_per_org_month"] == 600000
    assert body["effective"]["storage_bytes"] is None, "null means unlimited"
    assert body["plan_limits"]["apps_per_org_month"] == 250000
    assert cloud.entitlements.resolve(org.org_id).limit("apps_per_org_month") == 600000

    # both audit logs say who, what and why
    with cloud.db.read() as connection:
        platform = connection.execute(
            "SELECT detail FROM platform_audit_logs WHERE action = 'billing.custom_limits' AND org_id = ?",
            (org.org_id,),
        ).fetchall()
    assert platform and "Order form OF-1182" in platform[0]["detail"]
    tenant = [
        r
        for r in cloud.audit.list(org.org_id, limit=100)
        if r["action"] == "billing.custom_limits"
    ]
    assert tenant and tenant[0]["actor_type"] == "operator"

    # a second change keeps the first unless asked to replace
    ops.call(
        "PUT",
        f"/api/platform/tenants/{org.org_id}/limits",
        {"limits": {"audit_days": 4000}, "reason": "Order form OF-1183"},
    )
    kept = cloud.billing.subscription(org.org_id)["overrides"]
    assert kept["apps_per_org_month"] == 600000 and kept["audit_days"] == 4000
    ops.call(
        "PUT",
        f"/api/platform/tenants/{org.org_id}/limits",
        {"limits": {"audit_days": 3000}, "replace": True, "reason": "Renewal"},
    )
    assert cloud.billing.subscription(org.org_id)["overrides"] == {"audit_days": 3000}

    status, body = ops.call(
        "DELETE", f"/api/platform/tenants/{org.org_id}/limits?reason=Contract+ended"
    )
    assert status == 200 and body["overrides"] == {}
    assert cloud.entitlements.resolve(org.org_id).limit("apps_per_org_month") == 250000


def test_custom_limits_are_validated_and_need_the_plan_feature_and_a_reason(cloud):
    org, ops = enterprise(cloud)
    url = f"/api/platform/tenants/{org.org_id}/limits"
    for bad in (
        {"candidate_seats": -1},
        {"candidate_seats": "many"},
        {"candidate_seats": True},
        {"not_a_limit": 3},
        {"coach_max_scope": "admin"},
        "everything",
    ):
        status, _ = ops.call("PUT", url, {"limits": bad, "reason": "Order form 1"})
        assert status == 400, bad
    assert ops.call("PUT", url, {"limits": {"audit_days": 9}, "reason": ""})[0] == 400

    team = build_org(
        cloud, "Plain Team", plan="team", seats=25, slug_email="plain.test"
    )
    status, body = ops.call(
        "PUT",
        f"/api/platform/tenants/{team.org_id}/limits",
        {"limits": {"audit_days": 9}, "reason": "Order form 2"},
    )
    assert status == 402 and body["feature"] == "custom_limits", body


def test_support_operators_and_tenants_cannot_change_limits(cloud):
    org, _ops = enterprise(cloud)
    url = f"/api/platform/tenants/{org.org_id}/limits"
    payload = {"limits": {"audit_days": 9}, "reason": "Not allowed here"}
    support = operator_browser(cloud, "support@aira.test", role="support")
    assert support.call("PUT", url, payload)[0] == 403
    assert support.call("GET", url)[0] == 403
    owner = sign_in(cloud, org.owner)
    assert owner.call("PUT", url, payload)[0] in {401, 403, 404}


def test_a_lower_contract_limit_pauses_automation_until_the_organization_is_within_it(
    cloud,
):
    org, ops = enterprise(cloud)
    url = f"/api/platform/tenants/{org.org_id}/limits"
    ops.call(
        "PUT", url, {"limits": {"candidate_seats": 1}, "reason": "Reduced contract"}
    )
    over = cloud.lifecycle.recompute_over_limits(org.org_id)
    assert any(item.startswith("candidate seats") for item in over), over
    ops.call("DELETE", url + "?reason=Restored+contract")
    assert cloud.lifecycle.recompute_over_limits(org.org_id) == []


def test_enterprise_activation_rejects_limits_that_do_not_exist(cloud):
    org = build_org(cloud, "Bad Contract", plan="team", seats=25, slug_email="bad.test")
    ops = operator_browser(cloud)
    with pytest.raises(ValidationFailed):
        cloud.billing.activate_enterprise(
            principal_of(cloud, ops), org.org_id, seats=100, overrides={"nope": 1}
        )


# --------------------------------------------------------------------------- retention


def age_documents(cloud, org, days):
    local = local_module()
    with cloud.workspaces.bind(
        org.org_id, org.candidate.member_id, kind="system", source="test"
    ):
        with local._connect() as connection:
            moment = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
            connection.execute("UPDATE documents SET created_at = ?", (moment,))
            connection.commit()


def live_documents(cloud, org) -> int:
    local = local_module()
    with cloud.workspaces.bind(
        org.org_id, org.candidate.member_id, kind="system", source="test"
    ):
        with local._connect() as connection:
            return connection.execute(
                "SELECT COUNT(*) FROM documents WHERE deleted_at IS NULL"
            ).fetchone()[0]


def test_the_organizations_retention_policy_purges_documents_it_already_holds(cloud):
    org = build_org(
        cloud, "Retention Org", plan="team", seats=25, slug_email="ret.test"
    )
    seed_workspace(cloud, org.org_id, org.candidate.member_id)
    before = live_documents(cloud, org)
    assert before >= 1
    age_documents(cloud, org, 40)
    assert cloud.lifecycle._housekeeping()["documents_purged"] == 0, (
        "365 days: nothing is old enough"
    )
    assert live_documents(cloud, org) == before

    cloud.tenancy.update_policy(org.owner, {"retention_days": 30})
    done = cloud.lifecycle._housekeeping()
    assert done["documents_purged"] == before, (
        "a shorter policy reaches documents uploaded earlier"
    )
    assert live_documents(cloud, org) == 0


def test_a_contract_retention_limit_applies_the_same_way(cloud):
    org, ops = enterprise(cloud, "Contract Retention", "cr.test")
    seed_workspace(cloud, org.org_id, org.candidate.member_id)
    age_documents(cloud, org, 100)
    assert cloud.lifecycle._housekeeping()["documents_purged"] == 0
    ops.call(
        "PUT",
        f"/api/platform/tenants/{org.org_id}/limits",
        {"limits": {"retention_days": 60}, "reason": "Contract: 60 day retention"},
    )
    assert cloud.workspaces.retention_ceiling(org.org_id) == 60
    assert cloud.lifecycle._housekeeping()["documents_purged"] >= 1
    assert live_documents(cloud, org) == 0


def test_a_legal_hold_still_stops_retention(cloud):
    org, _ops = enterprise(cloud, "Held Org", "held.test")
    seed_workspace(cloud, org.org_id, org.candidate.member_id)
    age_documents(cloud, org, 400)
    cloud.tenancy.update_policy(org.owner, {"retention_days": 30})
    cloud.tenancy.update_policy(org.owner, {"legal_hold": True})
    assert cloud.lifecycle._housekeeping()["documents_purged"] == 0
    assert live_documents(cloud, org) >= 1
