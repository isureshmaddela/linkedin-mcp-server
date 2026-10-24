"""Organizations as tenants: policy, members, coaches, pause switches, consent, offboarding, export and deletion."""

from __future__ import annotations

import io
import json
import sqlite3
import zipfile
from pathlib import Path

import pytest
from cloud_support import (
    PASSWORD,
    add_member,
    audit_actions,
    build_org,
    local_module,
    seed_workspace,
    signup,
)  # noqa: F401

from aira_cloud.db import INTEGRITY_ERRORS, table_columns, table_names
from aira_cloud.errors import (
    Conflict,
    Forbidden,
    NotFound,
    PlanRequired,
    Unauthorized,
    ValidationFailed,
)

CONSENTS = ["automation", "coach_access", "sensitive_policy"]


# ----------------------------------------------------------------------------- policy


def test_default_policy_is_safe(cloud):
    result = signup(cloud)
    policy = cloud.tenancy.policy(result["org"]["org_id"])
    assert policy["submission_mode"] == "review" and policy["linkedin_allowed"] is False
    assert policy["daily_cap_per_candidate"] == 10 and policy["retention_days"] == 365
    assert policy["candidate_data_policy"] == "candidate_keeps"


def test_policy_can_be_relaxed_only_within_the_plan(cloud):
    trial = signup(cloud, "Trial Org", "t@trial.test")
    owner = trial["owner"]
    with pytest.raises(PlanRequired) as raised:
        cloud.tenancy.update_policy(owner, {"submission_mode": "auto"})
    assert (
        raised.value.extra["feature"] == "automation.submit"
        and raised.value.extra["required_plan"] == "starter"
    )
    with pytest.raises(PlanRequired):
        cloud.tenancy.update_policy(
            owner, {"daily_cap_per_candidate": 20}
        )  # Trial allows 3 a day
    with pytest.raises(PlanRequired):
        cloud.tenancy.update_policy(owner, {"retention_days": 400})
    assert (
        cloud.tenancy.update_policy(owner, {"daily_cap_per_candidate": 2})[
            "daily_cap_per_candidate"
        ]
        == 2
    )

    org = build_org(cloud, plan="starter", seats=10, slug_email="starter.test")
    assert (
        cloud.tenancy.update_policy(
            org.owner, {"submission_mode": "auto", "daily_cap_per_candidate": 10}
        )["submission_mode"]
        == "auto"
    )
    with pytest.raises(PlanRequired) as raised:
        cloud.tenancy.update_policy(org.owner, {"linkedin_allowed": True})
    assert raised.value.extra["required_plan"] == "team"
    with pytest.raises(PlanRequired):
        cloud.tenancy.update_policy(
            org.owner, {"allowed_sources": ["aira_jobs", "linkedin"]}
        )

    team = build_org(cloud, plan="team", seats=25, slug_email="team.test")
    updated = cloud.tenancy.update_policy(
        team.owner,
        {
            "linkedin_allowed": True,
            "allowed_sources": ["aira_jobs", "linkedin"],
            "easy_apply_allowed": True,
        },
    )
    assert (
        updated["allowed_sources"] == ["aira_jobs", "linkedin"]
        and updated["easy_apply_allowed"] is True
    )
    changes = cloud.audit.list(team.org_id, action="org.policy")[0]["detail"]["changes"]
    assert changes["linkedin_allowed"] == {"from": False, "to": True}


def test_policy_validation_and_permissions(cloud):
    org = build_org(cloud)
    for bad in (
        {"nope": 1},
        {"daily_cap_per_candidate": "x"},
        {"daily_cap_per_candidate": 0},
        {"allowed_sources": ["myspace"]},
        {"coach_messages": "all"},
        {"candidate_data_policy": "sell"},
    ):
        with pytest.raises(ValidationFailed):
            cloud.tenancy.update_policy(org.owner, bad)
    for role in (org.coach, org.candidate, org.billing, org.auditor):
        with pytest.raises(Forbidden):
            cloud.tenancy.update_policy(role, {"cover_letters": False})
    cloud.tenancy.update_policy(org.admin, {"cover_letters": False})


def test_removing_linkedin_policy_removes_the_source(cloud):
    org = build_org(cloud)
    cloud.tenancy.update_policy(
        org.owner,
        {"linkedin_allowed": True, "allowed_sources": ["aira_jobs", "linkedin"]},
    )
    after = cloud.tenancy.update_policy(org.owner, {"linkedin_allowed": False})
    assert "linkedin" not in after["allowed_sources"]


# ----------------------------------------------------------------------------- members


def test_role_changes_protect_the_last_owner_and_candidates(cloud):
    org = build_org(cloud)
    with pytest.raises(Conflict):
        cloud.tenancy.change_role(org.owner, org.owner.member_id, "admin")
    with pytest.raises(Conflict):
        cloud.tenancy.remove_member(org.owner, org.owner.member_id)
    with pytest.raises(Forbidden):
        cloud.tenancy.change_role(org.admin, org.owner.member_id, "admin")
    with pytest.raises(Forbidden):
        cloud.tenancy.change_role(org.admin, org.coach.member_id, "owner")
    with pytest.raises(ValidationFailed):
        cloud.tenancy.change_role(org.owner, org.candidate.member_id, "coach")
    with pytest.raises(ValidationFailed):
        cloud.tenancy.change_role(org.owner, org.coach.member_id, "candidate")
    second = add_member(cloud, org.owner, "owner2@acme.test", "owner")
    cloud.tenancy.change_role(
        org.owner, org.owner.member_id, "admin"
    )  # allowed now: another owner exists
    assert cloud.tenancy.member(org.org_id, org.owner.member_id)["role"] == "admin"
    assert second


def test_changing_a_coaches_role_drops_their_assignments(cloud):
    org = build_org(cloud)
    cloud.tenancy.assign_coach(
        org.owner, org.coach.member_id, org.candidate.member_id, "act"
    )
    cloud.tenancy.change_role(org.owner, org.coach.member_id, "auditor")
    assert cloud.tenancy.list_assignments(org.owner) == []


def test_member_list_is_scoped_by_role(cloud):
    org = build_org(cloud)
    cloud.tenancy.assign_coach(
        org.owner, org.coach.member_id, org.candidate.member_id, "view"
    )
    everyone = cloud.tenancy.list_members(org.owner)
    assert {m["role"] for m in everyone} == {
        "owner",
        "admin",
        "coach",
        "candidate",
        "billing",
        "auditor",
    }
    coach_view = cloud.tenancy.list_members(org.coach)
    assert {m["email"] for m in coach_view if m["role"] == "candidate"} == {
        "cand@acme.test"
    }
    with pytest.raises(Forbidden):
        cloud.tenancy.list_members(org.candidate)
    assert "password_hash" not in json.dumps(everyone)


def test_removing_a_member_ends_their_sessions_and_assignments(cloud):
    org = build_org(cloud)
    cloud.tenancy.assign_coach(
        org.owner, org.coach.member_id, org.candidate.member_id, "act"
    )
    token = cloud.identity.login("coach@acme.test", PASSWORD)["token"]
    cloud.tenancy.remove_member(org.owner, org.coach.member_id)
    assert cloud.identity.principal_for_token(token) is None
    assert cloud.tenancy.list_assignments(org.owner) == []
    assert "member.remove" in audit_actions(cloud, org.org_id)


# ----------------------------------------------------------------------------- coaches


def test_assignments_bulk_reassign_and_visibility(cloud):
    org = build_org(cloud)
    second = add_member(cloud, org.owner, "coach2@acme.test", "coach")
    cloud.tenancy.assign_coach(
        org.owner, org.coach.member_id, org.candidate.member_id, "act"
    )
    cloud.tenancy.assign_coach(
        org.owner, org.coach.member_id, org.other.member_id, "view"
    )
    assert len(cloud.tenancy.list_assignments(org.owner)) == 2
    assert len(cloud.tenancy.list_assignments(org.coach)) == 2
    assert cloud.tenancy.list_assignments(second) == []
    assert [
        a["coach_email"] for a in cloud.tenancy.list_assignments(org.candidate)
    ] == ["coach@acme.test"]
    moved = cloud.tenancy.bulk_reassign(
        org.owner, org.coach.member_id, second.member_id
    )
    assert moved == {"moved": 2}
    assert cloud.tenancy.list_assignments(org.coach) == []
    assert {a["scope"] for a in cloud.tenancy.list_assignments(second)} == {
        "act",
        "view",
    }
    for role in (org.coach, org.candidate, org.auditor):
        with pytest.raises(Forbidden):
            cloud.tenancy.assign_coach(
                role, second.member_id, org.candidate.member_id, "view"
            )
    with pytest.raises(ValidationFailed):
        cloud.tenancy.assign_coach(
            org.owner, org.admin.member_id, org.candidate.member_id, "view"
        )  # an admin is not a coach
    with pytest.raises(NotFound):
        cloud.tenancy.assign_coach(org.owner, second.member_id, "f" * 32, "view")


def test_candidate_list_is_scoped_and_filtered(cloud):
    org = build_org(cloud)
    cloud.tenancy.assign_coach(
        org.owner, org.coach.member_id, org.candidate.member_id, "view"
    )
    seed_workspace(cloud, org.org_id, org.candidate.member_id, document=False)
    everyone = cloud.tenancy.list_candidates(org.owner)
    assert {c["email"] for c in everyone} == {"cand@acme.test", "other@acme.test"}
    assert {c["email"] for c in cloud.tenancy.list_candidates(org.coach)} == {
        "cand@acme.test"
    }
    assert len(cloud.tenancy.list_candidates(org.auditor)) == 2
    with pytest.raises(Forbidden):
        cloud.tenancy.list_candidates(org.candidate)
    with pytest.raises(Forbidden):
        cloud.tenancy.list_candidates(org.billing)
    attention = cloud.tenancy.list_candidates(org.owner, attention=True)
    assert {c["email"] for c in attention} == {
        "other@acme.test"
    }  # no coach and no resume; the seeded candidate is fine
    by_coach = cloud.tenancy.list_candidates(org.owner, coach_id=org.coach.member_id)
    assert [c["email"] for c in by_coach] == ["cand@acme.test"]
    assert (
        cloud.tenancy.list_candidates(org.owner, q="other")[0]["email"]
        == "other@acme.test"
    )


# ----------------------------------------------------------------------------- pause switches


def test_pause_requires_a_reason_and_shows_on_the_dashboard(cloud):
    org = build_org(cloud)
    with pytest.raises(ValidationFailed):
        cloud.tenancy.pause_org(org.owner, " ")
    cloud.tenancy.pause_org(org.admin, "Quarterly review")
    banners = cloud.dashboard.organization(org.owner)["banners"]
    assert any(
        b["kind"] == "paused" and b["body"] == "Quarterly review" for b in banners
    )
    cloud.tenancy.record_consent(org.candidate, CONSENTS)
    assert (
        cloud.gate.automation_block(org.org_id, org.candidate.member_id)[0]
        == "org_paused"
    )
    for role in (org.coach, org.candidate, org.billing, org.auditor):
        with pytest.raises(Forbidden):
            cloud.tenancy.pause_org(role, "x")
    cloud.tenancy.resume_org(org.owner)
    assert cloud.gate.automation_block(org.org_id, org.candidate.member_id) is None


def test_candidate_pause_rules(cloud):
    org = build_org(cloud)
    cloud.tenancy.record_consent(org.candidate, CONSENTS)
    candidate = org.candidate.member_id
    cloud.tenancy.assign_coach(org.owner, org.coach.member_id, candidate, "view")
    with pytest.raises(Forbidden):
        cloud.tenancy.pause_candidate(
            org.coach, candidate, "view scope cannot change anything"
        )
    cloud.tenancy.assign_coach(org.owner, org.coach.member_id, candidate, "act")
    cloud.tenancy.pause_candidate(org.coach, candidate, "Interview week")
    assert cloud.gate.automation_block(org.org_id, candidate)[0] == "candidate_paused"
    cloud.tenancy.resume_candidate(
        org.candidate, candidate
    )  # a candidate may resume what a coach paused
    assert cloud.gate.automation_block(org.org_id, candidate) is None
    cloud.tenancy.pause_candidate(org.admin, candidate, "Policy review")
    for role in (org.candidate, org.coach):
        with pytest.raises(Forbidden):
            cloud.tenancy.resume_candidate(
                role, candidate
            )  # an administrator's pause stands
    cloud.tenancy.resume_candidate(org.admin, candidate)
    with pytest.raises(Forbidden):
        cloud.tenancy.pause_candidate(org.candidate, org.other.member_id, "not theirs")


# ----------------------------------------------------------------------------- consent and onboarding


def test_consent_is_versioned_and_gates_automation(cloud):
    org = build_org(cloud)
    candidate = org.candidate.member_id
    state = cloud.tenancy.consent_state(org.org_id, candidate)
    assert state["complete"] is False and state["required"] == CONSENTS
    cloud.tenancy.record_consent(org.candidate, CONSENTS)
    state = cloud.tenancy.consent_state(org.org_id, candidate)
    assert (
        state["complete"]
        and state["granted"]["automation"]["version"] == state["version"]
    )
    cloud.tenancy.revoke_consent(org.candidate, "automation")
    assert cloud.gate.automation_block(org.org_id, candidate)[0] == "consent"
    with pytest.raises(Forbidden):
        cloud.tenancy.record_consent(org.coach, CONSENTS)
    with pytest.raises(ValidationFailed):
        cloud.tenancy.record_consent(org.candidate, ["everything"])
    assert {"consent.grant", "consent.revoke"} <= set(
        audit_actions(cloud, org.org_id, "consent.")
    )


def test_onboarding_checklist_follows_the_workspace(cloud):
    org = build_org(cloud)
    candidate = org.candidate.member_id
    before = cloud.tenancy.onboarding(org.org_id, candidate)
    assert before["done"] == 0 and not before["complete"]
    cloud.tenancy.record_consent(org.candidate, CONSENTS)
    seed_workspace(cloud, org.org_id, candidate)
    after = cloud.tenancy.onboarding(org.org_id, candidate)
    done = {item["id"] for item in after["items"] if item["done"]}
    assert {"consent", "profile", "resume"} <= done and "plan" not in done
    cloud.tenancy.assign_coach(org.owner, org.coach.member_id, candidate, "act")
    assert any(
        i["id"] == "coach" and i["done"]
        for i in cloud.tenancy.onboarding(org.org_id, candidate)["items"]
    )


def test_candidate_stage_follows_the_workspace_and_is_logged(cloud):
    org = build_org(cloud)
    candidate = org.candidate.member_id
    assert cloud.workspaces.row(org.org_id, candidate)["stage"] == "onboarding"
    seed_workspace(cloud, org.org_id, candidate, job_title="Principal Cloud Architect")
    cloud.workspaces.refresh(org.org_id, candidate, max_age=0)
    assert cloud.workspaces.row(org.org_id, candidate)["stage"] == "searching"
    ws = cloud.workspaces.row(org.org_id, candidate)
    conn = sqlite3.connect(ws["data_path"])
    conn.execute("UPDATE jobs SET stage = 'offer'")
    conn.commit()
    conn.close()
    cloud.workspaces.refresh(org.org_id, candidate, max_age=0)
    assert cloud.workspaces.row(org.org_id, candidate)["stage"] == "offers"
    with cloud.db.read() as connection:
        stages = [
            r["stage"]
            for r in connection.execute(
                "SELECT stage FROM candidate_stage_log WHERE candidate_id = ? ORDER BY id",
                (candidate,),
            )
        ]
    assert stages == ["onboarding", "searching", "offers"]


# ----------------------------------------------------------------------------- branding and data


def test_branding_is_a_team_feature(cloud):
    starter = build_org(cloud, plan="starter", seats=10, slug_email="s.test")
    with pytest.raises(PlanRequired):
        cloud.tenancy.set_branding(starter.owner, logo_url="https://logo.example/a.png")
    team = build_org(cloud, plan="team", seats=25, slug_email="t.test")
    with pytest.raises(ValidationFailed):
        cloud.tenancy.set_branding(team.owner, logo_url="http://insecure.example/a.png")
    assert (
        cloud.tenancy.set_branding(team.owner, logo_url="https://logo.example/a.png")[
            "logo_url"
        ]
        == "https://logo.example/a.png"
    )
    cloud.identity.invite(team.owner, "new@t.test", "coach")
    assert (
        "https://logo.example/a.png"
        in cloud.mail.latest("new@t.test", "invite")["body"]
    )


def test_candidate_data_policy_keeps_or_exports_on_departure(cloud):
    org = build_org(cloud)
    candidate = org.candidate.member_id
    seed_workspace(cloud, org.org_id, candidate, job_title="Principal Cloud Architect")
    workspace_dir = cloud.workspaces.candidate_dir(org.org_id, candidate)
    assert (workspace_dir / "workspace.sqlite3").exists()
    # default policy: the candidate keeps their data
    result = cloud.tenancy.remove_member(org.owner, candidate)
    assert result["data_policy"] == "candidate_keeps" and result["export_id"]
    assert not workspace_dir.exists(), "the organization keeps no copy"
    mail = cloud.mail.latest("cand@acme.test", "data_export_ready")
    token = mail["body"].split("/api/exports/download/")[1].split()[0]
    row, data = cloud.lifecycle.export_by_token(token)
    names = zipfile.ZipFile(io.BytesIO(data)).namelist()
    assert "workspace.json" in names and any(n.startswith("documents/") for n in names)
    workspace_json = json.loads(
        zipfile.ZipFile(io.BytesIO(data)).read("workspace.json")
    )
    assert workspace_json["tables"]["jobs"][0]["title"] == "Principal Cloud Architect"
    with cloud.db.read() as connection:
        departed = connection.execute(
            "SELECT data_policy FROM departed_candidates WHERE candidate_id = ?",
            (candidate,),
        ).fetchone()
    assert departed["data_policy"] == "candidate_keeps"
    cloud.clock.advance(days=31)
    with pytest.raises(Exception):
        cloud.lifecycle.export_by_token(token)
    # organization-keeps policy: the workspace stays, archived
    cloud.tenancy.update_policy(
        org.owner, {"candidate_data_policy": "organization_keeps"}
    )
    other = org.other.member_id
    seed_workspace(
        cloud, org.org_id, other, document=False, job_title="Platform Engineer"
    )
    result = cloud.tenancy.remove_member(org.owner, other)
    assert result["data_policy"] == "organization_keeps"
    assert cloud.workspaces.row(org.org_id, other)["status"] == "archived"
    assert cloud.workspaces.data_path(org.org_id, other).exists()


def test_export_one_candidate_and_whole_organization(cloud):
    org = build_org(cloud)
    candidate = org.candidate.member_id
    seed_workspace(cloud, org.org_id, candidate, job_title="Principal Cloud Architect")
    mine = cloud.lifecycle.export_candidate(org.candidate, candidate)
    row, data = cloud.lifecycle.export_for(org.candidate, mine["export_id"])
    assert "workspace.json" in zipfile.ZipFile(io.BytesIO(data)).namelist()
    with pytest.raises(Forbidden):
        cloud.lifecycle.export_candidate(org.candidate, org.other.member_id)
    with pytest.raises(Forbidden):
        cloud.lifecycle.export_candidate(org.coach, candidate)
    everything = cloud.lifecycle.export_organization(org.owner)
    row, data = cloud.lifecycle.export_for(org.owner, everything["export_id"])
    archive = zipfile.ZipFile(io.BytesIO(data))
    assert (
        "organization.json" in archive.namelist()
        and "audit.jsonl" in archive.namelist()
    )
    assert f"candidates/{candidate}.zip" in archive.namelist()
    organization = json.loads(archive.read("organization.json"))
    assert (
        len(organization["members"]) == 7
        and "password_hash" not in archive.read("organization.json").decode()
    )
    for role in (org.coach, org.candidate, org.billing):
        with pytest.raises(Forbidden):
            cloud.lifecycle.export_organization(role)
    cloud.lifecycle.export_organization(org.auditor)
    with pytest.raises(Forbidden):
        cloud.lifecycle.export_for(org.candidate, everything["export_id"])
    assert {"data.export_org", "data.export_candidate"} <= set(
        audit_actions(cloud, org.org_id, "data.")
    )


def test_deleting_a_candidate_workspace_removes_rows_documents_and_profile(cloud):
    org = build_org(cloud)
    candidate = org.candidate.member_id
    seed_workspace(cloud, org.org_id, candidate)
    profile = (
        cloud.workspaces.candidate_dir(org.org_id, candidate) / "linkedin" / "profile"
    )
    profile.mkdir(parents=True)
    (profile / "cookies.json").write_text("{}")
    cloud.lifecycle.delete_candidate_workspace(org.owner, candidate)
    assert not cloud.workspaces.candidate_dir(org.org_id, candidate).exists()
    with pytest.raises(NotFound):
        cloud.workspaces.row(org.org_id, candidate)
    with pytest.raises(Forbidden):
        cloud.lifecycle.delete_candidate_workspace(org.coach, org.other.member_id)


# ----------------------------------------------------------------------------- closure and deletion


def test_closure_needs_the_owner_password_and_an_export_then_deletes_after_the_window(
    cloud,
):
    org = build_org(cloud)
    seed_workspace(
        cloud,
        org.org_id,
        org.candidate.member_id,
        job_title="Principal Cloud Architect",
    )
    with pytest.raises(Forbidden):
        cloud.lifecycle.request_closure(org.admin, PASSWORD)
    with pytest.raises(ValidationFailed) as wrong:
        cloud.lifecycle.request_closure(org.owner, "wrong password entirely")
    assert wrong.value.code == "password_incorrect"
    closing = cloud.lifecycle.request_closure(org.owner, PASSWORD)
    assert closing["export"]["size"] > 0
    assert cloud.billing.subscription(org.org_id)["status"] == "cancelled"
    assert any(b["kind"] == "closure" for b in cloud.dashboard.banners(org.owner))
    cloud.lifecycle.cancel_closure(
        org.owner
    )  # still within the window: closure can be cancelled
    assert cloud.tenancy.org(org.org_id)["closure_requested_at"] is None


def test_deletion_removes_everything_but_the_billing_record_and_an_audit_stub(cloud):
    org = build_org(cloud)
    candidate = org.candidate.member_id
    seed_workspace(cloud, org.org_id, candidate, job_title="Principal Cloud Architect")
    cloud.tenancy.record_consent(org.candidate, CONSENTS)
    cloud.secrets.put(org.org_id, "serp", "secret-value")
    cloud.queue.enqueue(org.org_id, "autopilot.due", candidate_id=candidate)
    cloud.usage.record(org.org_id, "applications", 3)
    events_before = cloud.audit.count(org.org_id)
    tree = cloud.workspaces.org_dir(org.org_id)
    assert tree.exists()
    cloud.lifecycle.request_closure(org.owner, PASSWORD)
    cloud.clock.advance(days=cloud.config.deletion_grace_days + 1)
    cloud.billing.process_due()
    assert cloud.billing.subscription(org.org_id)["status"] == "deleted"
    assert not tree.exists(), "documents, databases and browser profiles are gone"
    with cloud.db.read() as connection:
        leftovers = {}
        for table in table_names(connection):
            columns = table_columns(connection, table)
            if "org_id" in columns:
                n = connection.execute(
                    f"SELECT COUNT(*) FROM {table} WHERE org_id = ?", (org.org_id,)
                ).fetchone()[0]
                if n:
                    leftovers[table] = n
        stub = connection.execute(
            "SELECT * FROM audit_stubs WHERE org_id = ?", (org.org_id,)
        ).fetchone()
    assert set(leftovers) <= {
        "subscriptions",
        "organizations",
        "invoices",
        "subscription_events",
        "billing_events",
        "platform_audit_logs",
        "billing_credits",
        "audit_stubs",
    }, leftovers
    assert stub is not None and stub["event_count"] >= events_before
    assert cloud.secrets.get(org.org_id, "serp") is None
    assert cloud.tenancy.policy(org.org_id)["linkedin_allowed"] is False
    with cloud.db.read() as connection:
        org_row = connection.execute(
            "SELECT name, status FROM organizations WHERE org_id = ?", (org.org_id,)
        ).fetchone()
    assert org_row["name"] == "[deleted]" and org_row["status"] == "deleted"
    with pytest.raises(Unauthorized):
        cloud.identity.login("owner@acme.test", PASSWORD)
    assert Path(cloud.config.tenants_root).exists()


def test_deleting_one_organization_leaves_another_untouched(cloud):
    one = build_org(cloud, "First Org", slug_email="one.test")
    two = build_org(cloud, "Second Org", slug_email="two.test")
    seed_workspace(cloud, two.org_id, two.candidate.member_id, job_title="Safe Job")
    cloud.lifecycle.delete_org(one.org_id, reason="test", trigger_type="scheduler")
    assert cloud.workspaces.data_path(two.org_id, two.candidate.member_id).exists()
    assert cloud.tenancy.list_members(two.owner)
    assert cloud.identity.login("owner@two.test", PASSWORD, org=two.org_id)["token"]


def test_audit_log_is_append_only_hash_chained_and_pruned_by_plan(cloud):
    org = build_org(cloud, plan="starter", seats=10)
    assert cloud.audit.verify(org.org_id)["ok"]
    with pytest.raises(INTEGRITY_ERRORS):
        with cloud.db.write() as connection:
            connection.execute("DELETE FROM audit_logs WHERE org_id = ?", (org.org_id,))
    with pytest.raises(INTEGRITY_ERRORS):
        with cloud.db.write() as connection:
            connection.execute(
                "UPDATE audit_logs SET action = 'x' WHERE org_id = ?", (org.org_id,)
            )
    first_count = cloud.audit.count(org.org_id)
    cloud.clock.advance(days=366)  # Starter keeps audit history for one year
    cloud.audit.record(org.org_id, "test.fresh")
    cloud.lifecycle._housekeeping()
    assert cloud.audit.count(org.org_id) < first_count
    assert cloud.audit.verify(org.org_id)["ok"], (
        "the chain stays verifiable after pruning"
    )


def test_secret_rotation_keeps_secrets_and_old_documents_readable(cloud):
    org = build_org(cloud)
    cloud.secrets.put(org.org_id, "serp", "key-one")
    first = cloud.secrets.current_version(org.org_id)
    candidate = org.candidate.member_id
    doc = seed_workspace(cloud, org.org_id, candidate)["document_id"]
    version = cloud.secrets.rotate(org.org_id)
    assert version == first + 1
    assert cloud.secrets.get(org.org_id, "serp") == "key-one"
    local = local_module()
    with cloud.workspaces.bind(org.org_id, candidate, kind="system"):
        with local._connect() as connection:
            _, content = local._document_store().read(
                connection, owner_id=candidate, document_id=doc
            )
    assert content.startswith(b"Cam Candidate resume.")
    assert cloud.secrets.retire_old_keys(org.org_id) == 1


def test_a_removed_audit_row_is_detected_by_the_chain(cloud):
    org = build_org(cloud)
    assert cloud.audit.verify(org.org_id)["ok"]
    with (
        cloud.db.write() as connection
    ):  # what an attacker with database access would have to do
        connection.execute(
            "INSERT OR REPLACE INTO cloud_kv (key, value) VALUES ('audit_maintenance', '1')"
        )
        middle = connection.execute(
            "SELECT seq FROM audit_logs WHERE org_id = ? ORDER BY seq LIMIT 1 OFFSET 3",
            (org.org_id,),
        ).fetchone()["seq"]
        connection.execute("DELETE FROM audit_logs WHERE seq = ?", (middle,))
        connection.execute(
            "INSERT OR REPLACE INTO cloud_kv (key, value) VALUES ('audit_maintenance', '0')"
        )
    result = cloud.audit.verify(org.org_id)
    assert result["ok"] is False and result["broken_at"] > middle


def test_exports_mask_sensitive_values_for_staff_but_not_for_the_candidate(cloud):
    from cloud_support import local_module, seed_held_run

    org = build_org(cloud)
    candidate = org.candidate.member_id
    seed_workspace(cloud, org.org_id, candidate)
    seed_held_run(
        cloud, org.org_id, candidate
    )  # an answer "SECRET-AUTH-VALUE" to a work-authorization question
    with cloud.workspaces.bind(org.org_id, candidate, kind="system", source="test"):
        with local_module()._connect() as connection:
            connection.execute(
                "INSERT OR REPLACE INTO settings (key, value) VALUES ('application_work_authorization', 'SECRET-VISA-CLASS')"
            )
            connection.execute(
                "INSERT OR REPLACE INTO standard_answers (canonical_field, value, confirmed, sensitivity) VALUES ('work_authorization', 'SECRET-STANDARD', 1, 'sensitive')"
            )

    def archive_text(person, who_exports) -> str:
        done = cloud.lifecycle.export_candidate(person, candidate)
        _row, blob = cloud.lifecycle.export_for(person, done["export_id"])
        return zipfile.ZipFile(io.BytesIO(blob)).read("workspace.json").decode()

    own = archive_text(org.candidate, "candidate")
    assert "SECRET-VISA-CLASS" in own
    assert "SECRET-STANDARD" in own, "standard answers are exported"
    assert "SECRET-AUTH-VALUE" in own, "automatic application runs are exported"

    for staff in (org.admin, org.auditor, org.owner):
        text = archive_text(staff, "staff")
        for secret in ("SECRET-VISA-CLASS", "SECRET-AUTH-VALUE", "SECRET-STANDARD"):
            assert secret not in text, (staff.role, secret)
        assert "Cloud Architect" in text  # ordinary data is still there

    everything = cloud.lifecycle.export_organization(org.admin)
    _row, blob = cloud.lifecycle.export_for(org.admin, everything["export_id"])
    inner = zipfile.ZipFile(io.BytesIO(blob)).read(f"candidates/{candidate}.zip")
    text = zipfile.ZipFile(io.BytesIO(inner)).read("workspace.json").decode()
    assert "SECRET-VISA-CLASS" not in text and "SECRET-AUTH-VALUE" not in text
