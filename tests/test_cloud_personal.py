"""An owner's or admin's own workspace: staff who use the apps themselves, in a workspace only they can open."""

from __future__ import annotations

import pytest
from cloud_support import add_member, audit_actions, build_org, sign_in

from aira_cloud.errors import Conflict, Forbidden, PlanRequired


def own(browser, member_id: str, method: str, path: str, body=None):
    return browser.call(method, path, body, headers={"X-Aira-Candidate": member_id})


def test_an_owner_starts_a_workspace_and_uses_careers_in_it(cloud):
    org = build_org(cloud)
    owner = sign_in(cloud, org.owner)
    status, me = owner.call("GET", "/api/auth/me")
    assert status == 200
    assert me["personal_workspace"] == {"available": True, "exists": False}
    seats_before = cloud.tenancy.seat_usage(org.org_id)["candidates"]

    # Before it exists, naming it is a clear 404, and Careers without a candidate is unchanged.
    status, data = own(owner, org.owner.member_id, "GET", "/api/jobs")
    assert status == 404 and data["code"] == "personal_workspace_missing"
    assert owner.call("GET", "/api/jobs")[1]["code"] == "candidate_required"

    status, started = owner.call("POST", "/api/workspace/own", {})
    assert status == 201 and started["exists"] is True
    assert "leads.outreach" in started["candidate_permissions"]
    assert cloud.tenancy.seat_usage(org.org_id)["candidates"] == seats_before + 1
    # Starting twice is the same workspace.
    assert owner.call("POST", "/api/workspace/own", {})[0] == 201

    assert own(owner, org.owner.member_id, "GET", "/api/jobs")[0] == 200
    status, task = own(
        owner, org.owner.member_id, "POST", "/api/tasks", {"title": "Call Acme back"}
    )
    assert status in (200, 201), task
    # The consents a candidate gives at onboarding were given by starting it; coach access is not asked.
    status, consent = own(owner, org.owner.member_id, "GET", "/api/me/consent")
    assert status == 200 and consent["complete"] is True
    assert "coach_access" not in consent["required"]
    # The organization role is unchanged outside the workspace, and the audit names the owner, not a candidate.
    status, me = own(owner, org.owner.member_id, "GET", "/api/auth/me")
    assert me["role"] == "owner" and me["personal_workspace"]["exists"] is True
    rows = cloud.audit.list(org.org_id, limit=50, action="workspace.personal_start")
    assert rows and rows[0]["actor_role"] == "owner"


def test_nobody_else_can_open_a_personal_workspace(cloud):
    org = build_org(cloud)
    owner = sign_in(cloud, org.owner)
    assert owner.call("POST", "/api/workspace/own", {})[0] == 201
    owner_id = org.owner.member_id
    for who in ("admin", "coach", "auditor", "candidate"):
        browser = sign_in(cloud, getattr(org, who))
        status, _ = own(browser, owner_id, "GET", "/api/jobs")
        assert status in (403, 404), who
    assert not cloud.authz.can(org.admin, "candidates.read", candidate_id=owner_id)
    assert not cloud.authz.can(org.auditor, "candidates.read", candidate_id=owner_id)
    # Staff still read a real candidate's workspace as before.
    assert cloud.authz.can(
        org.admin, "candidates.read", candidate_id=org.candidate.member_id
    )
    # It is not a candidate: the candidate list and the held queue leave it out.
    admin = sign_in(cloud, org.admin)
    status, listing = admin.call("GET", "/api/org/candidates")
    assert status == 200
    ids = {
        row.get("member_id") or row.get("candidate_id") for row in listing["candidates"]
    }
    assert owner_id not in ids and org.candidate.member_id in ids
    targets = cloud.dashboard.held_queue(org.admin)
    assert all(item.get("candidate_id") != owner_id for item in targets)


def test_only_owners_and_admins_keep_one(cloud):
    org = build_org(cloud)
    for who in ("coach", "billing", "auditor"):
        with pytest.raises(Forbidden):
            cloud.tenancy.start_personal_workspace(getattr(org, who))
    status, me = sign_in(cloud, org.coach).call("GET", "/api/auth/me")
    assert me["personal_workspace"]["available"] is False
    admin = cloud.tenancy.start_personal_workspace(org.admin)
    assert admin["exists"] is True


def test_it_takes_a_candidate_seat(cloud):
    org = build_org(cloud, plan="starter", seats=5)
    for index in range(3):
        add_member(cloud, org.owner, f"more{index}@acme.test", "candidate")
    with pytest.raises(PlanRequired):
        cloud.tenancy.start_personal_workspace(org.owner)


def test_role_changes_removal_and_ending_it(cloud):
    org = build_org(cloud)
    admin_id = org.admin.member_id
    cloud.tenancy.start_personal_workspace(org.admin)
    # An admin with a workspace cannot become a coach until it ends; owner and admin both may keep one.
    with pytest.raises(Conflict):
        cloud.tenancy.change_role(org.owner, admin_id, "coach")
    cloud.tenancy.change_role(org.owner, admin_id, "owner")
    assert cloud.tenancy.has_personal_workspace(org.org_id, admin_id)

    admin = sign_in(cloud, org.relogin("admin"))
    status, ended = admin.call("DELETE", "/api/workspace/own")
    assert status == 200 and ended["exists"] is False and ended["outcome"] == "deleted"
    assert not cloud.tenancy.has_personal_workspace(org.org_id, admin_id)
    assert not cloud.workspaces.data_path(org.org_id, admin_id).exists()
    assert "workspace.personal_end" in audit_actions(cloud, org.org_id, "workspace.")

    # Removing a member who has one deletes it with them.
    cloud.tenancy.start_personal_workspace(org.relogin("admin"))
    result = cloud.tenancy.remove_member(org.owner, admin_id)
    assert result["personal_workspace"] == "deleted"
    assert not cloud.tenancy.has_personal_workspace(org.org_id, admin_id)
