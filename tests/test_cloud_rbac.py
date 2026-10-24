"""The permission matrix, coach scopes, and 404 for anything outside the organization."""

from __future__ import annotations

import pytest
from cloud_support import audit_actions, build_org  # noqa: F401

from aira_cloud.errors import Forbidden, NotFound, PlanRequired

ORG_LEVEL = {
    # permission: roles that hold it
    "org.settings": {"owner", "admin"},
    "billing.manage": {"owner", "billing"},
    "usage.read": {"owner", "admin", "billing", "auditor"},
    "members.manage": {"owner", "admin"},
    "members.manage_owners": {"owner"},
    "coaches.assign": {"owner", "admin"},
    "automation.pause.org": {"owner", "admin"},
    "integrations.manage": {"owner", "admin"},
    "dashboard.read": {
        "owner",
        "admin",
        "coach",
        "candidate",
        "auditor",
    },  # a billing contact sees usage only
    "export.org": {"owner", "admin", "auditor"},
    "audit.read": {"owner", "admin", "auditor"},
    "support.approve": {"owner"},
    "org.close": {"owner"},
    "candidates.list": {"owner", "admin", "coach", "auditor"},
}
ROLE_KEYS = {
    "owner": "owner",
    "admin": "admin",
    "coach": "coach",
    "candidate": "candidate",
    "billing": "billing",
    "auditor": "auditor",
}


@pytest.mark.parametrize("permission", sorted(ORG_LEVEL))
def test_organization_permissions_match_the_matrix(cloud, permission):
    org = build_org(cloud)
    for role, key in ROLE_KEYS.items():
        allowed = cloud.authz.can(getattr(org, key), permission)
        assert allowed == (role in ORG_LEVEL[permission]), f"{role} / {permission}"


def test_platform_permissions_are_never_held_by_members(cloud):
    org = build_org(cloud)
    for key in ROLE_KEYS.values():
        assert not cloud.authz.can(getattr(org, key), "platform.tenants.read")


def test_candidate_level_matrix_for_own_and_other_candidates(cloud):
    org = build_org(cloud)
    own, other = org.candidate.member_id, org.other.member_id
    for permission in (
        "candidates.read",
        "documents.read",
        "candidates.write",
        "search.run",
        "held.answer",
        "applications.submit",
        "automation.pause.candidate",
        "linkedin.manage",
        "export.own",
    ):
        assert cloud.authz.can(org.candidate, permission, candidate_id=own), permission
        assert not cloud.authz.can(org.candidate, permission, candidate_id=other), (
            permission
        )
    # owners and admins read and can pause, but never edit, search, answer or submit
    for role in (org.owner, org.admin):
        for permission in (
            "candidates.read",
            "documents.read",
            "automation.pause.candidate",
        ):
            assert cloud.authz.can(role, permission, candidate_id=own)
        for permission in (
            "candidates.write",
            "search.run",
            "held.answer",
            "applications.submit",
            "linkedin.manage",
            "export.own",
        ):
            assert not cloud.authz.can(role, permission, candidate_id=own), permission
    # auditors read everything and change nothing
    for permission in ("candidates.read", "documents.read"):
        assert cloud.authz.can(org.auditor, permission, candidate_id=own)
    for permission in (
        "candidates.write",
        "search.run",
        "applications.submit",
        "automation.pause.candidate",
    ):
        assert not cloud.authz.can(org.auditor, permission, candidate_id=own)
    # billing contacts see no candidate
    assert not cloud.authz.can(org.billing, "candidates.read", candidate_id=own)


@pytest.mark.parametrize(
    "scope,allowed,denied",
    [
        (
            "view",
            {"candidates.read", "documents.read"},
            {
                "candidates.write",
                "search.run",
                "held.answer",
                "automation.pause.candidate",
                "applications.submit",
            },
        ),
        (
            "act",
            {
                "candidates.read",
                "documents.read",
                "candidates.write",
                "search.run",
                "held.answer",
                "automation.pause.candidate",
            },
            {"applications.submit"},
        ),
        (
            "approve",
            {
                "candidates.read",
                "documents.read",
                "candidates.write",
                "search.run",
                "held.answer",
                "automation.pause.candidate",
                "applications.submit",
            },
            set(),
        ),
    ],
)
def test_coach_scopes(cloud, scope, allowed, denied):
    org = build_org(cloud)
    cloud.tenancy.assign_coach(
        org.owner, org.coach.member_id, org.candidate.member_id, scope
    )
    candidate = org.candidate.member_id
    for permission in allowed:
        assert cloud.authz.can(org.coach, permission, candidate_id=candidate), (
            permission
        )
    for permission in denied:
        assert not cloud.authz.can(org.coach, permission, candidate_id=candidate), (
            permission
        )
    # an unassigned candidate is out of reach for every candidate-level permission
    for permission in allowed | denied:
        assert not cloud.authz.can(
            org.coach, permission, candidate_id=org.other.member_id
        ), permission
    assert "coach.assign" in audit_actions(cloud, org.org_id, "coach.")


def test_sensitive_fields_are_candidate_only_unless_policy_and_consent_allow(cloud):
    org = build_org(cloud)
    cloud.tenancy.assign_coach(
        org.owner, org.coach.member_id, org.candidate.member_id, "act"
    )
    candidate = org.candidate.member_id
    assert cloud.authz.can_see_sensitive(org.candidate, candidate)
    assert not cloud.authz.can_see_sensitive(org.coach, candidate)
    cloud.tenancy.update_policy(org.owner, {"coach_sees_sensitive": True})
    assert not cloud.authz.can_see_sensitive(org.coach, candidate), (
        "policy alone is not enough: the candidate must consent"
    )
    cloud.tenancy.record_consent(org.candidate, ["sensitive_access"])
    assert cloud.authz.can_see_sensitive(org.coach, candidate)
    cloud.tenancy.revoke_consent(org.candidate, "sensitive_access")
    assert not cloud.authz.can_see_sensitive(org.coach, candidate)
    for role in (org.owner, org.admin, org.auditor, org.billing):
        assert not cloud.authz.can_see_sensitive(role, candidate)


def test_require_gives_404_for_unknown_candidates_and_403_for_forbidden(cloud):
    org = build_org(cloud)
    with pytest.raises(NotFound):
        cloud.authz.require(org.owner, "candidates.read", candidate_id="0" * 32)
    with pytest.raises(NotFound):
        cloud.authz.require(
            org.owner, "candidates.read", candidate_id=org.coach.member_id
        )  # a coach is not a candidate
    with pytest.raises(Forbidden):
        cloud.authz.require(
            org.coach, "candidates.read", candidate_id=org.candidate.member_id
        )  # not assigned
    cloud.authz.require(
        org.owner, "candidates.read", candidate_id=org.candidate.member_id
    )


def test_a_candidate_of_another_organization_is_a_404_for_everyone(cloud):
    one = build_org(cloud, "First Org", slug_email="one.test")
    two = build_org(cloud, "Second Org", slug_email="two.test")
    foreign = two.candidate.member_id
    for key in ROLE_KEYS.values():
        with pytest.raises(NotFound):
            cloud.authz.require(
                getattr(one, key), "candidates.read", candidate_id=foreign
            )


def test_coach_scope_is_capped_by_the_plan(cloud):
    org = build_org(cloud, plan="starter", seats=10)
    with pytest.raises(PlanRequired) as raised:
        cloud.tenancy.assign_coach(
            org.owner, org.coach.member_id, org.candidate.member_id, "approve"
        )
    assert raised.value.extra["required_plan"] == "team"
    cloud.tenancy.assign_coach(
        org.owner, org.coach.member_id, org.candidate.member_id, "act"
    )
    assert cloud.authz.can(
        org.coach, "search.run", candidate_id=org.candidate.member_id
    )
    # a downgrade below an existing assignment caps it at once
    org2 = build_org(cloud, "Team Org", slug_email="team.test")
    cloud.tenancy.assign_coach(
        org2.owner, org2.coach.member_id, org2.candidate.member_id, "approve"
    )
    assert cloud.authz.can(
        org2.coach, "applications.submit", candidate_id=org2.candidate.member_id
    )
    with cloud.db.write() as connection:
        connection.execute(
            "UPDATE subscriptions SET plan_id = 'starter', plan_version = 1 WHERE org_id = ?",
            (org2.org_id,),
        )
    cloud.entitlements.invalidate(org2.org_id)
    assert not cloud.authz.can(
        org2.coach, "applications.submit", candidate_id=org2.candidate.member_id
    )
    assert cloud.authz.can(
        org2.coach, "search.run", candidate_id=org2.candidate.member_id
    )


def test_coach_messages_need_the_policy(cloud):
    org = build_org(cloud)
    cloud.tenancy.assign_coach(
        org.owner, org.coach.member_id, org.candidate.member_id, "act"
    )
    candidate = org.candidate.member_id
    assert cloud.authz.can(org.candidate, "messages.read", candidate_id=candidate)
    assert not cloud.authz.can(org.coach, "messages.read", candidate_id=candidate)
    cloud.tenancy.update_policy(org.owner, {"coach_messages": "view"})
    assert cloud.authz.can(org.coach, "messages.read", candidate_id=candidate)
    assert not cloud.authz.can(org.coach, "messages.send", candidate_id=candidate)
    cloud.tenancy.update_policy(org.owner, {"coach_messages": "act"})
    assert cloud.authz.can(org.coach, "messages.send", candidate_id=candidate)
    assert not cloud.authz.can(org.owner, "messages.read", candidate_id=candidate)
