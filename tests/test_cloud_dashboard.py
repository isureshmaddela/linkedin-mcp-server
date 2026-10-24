"""The organization dashboard: every section, every number linking to its records, fresh within a minute."""

from __future__ import annotations

from cloud_support import build_org, seed_held_run, seed_workspace


def test_the_dashboard_shows_every_section_and_each_number_links_to_records(cloud):
    org = build_org(cloud)
    cloud.tenancy.assign_coach(
        org.owner, org.coach.member_id, org.candidate.member_id, "approve"
    )
    seed_workspace(cloud, org.org_id, org.candidate.member_id)
    seed_held_run(cloud, org.org_id, org.candidate.member_id)

    board = cloud.dashboard.organization(org.owner)

    assert board["kind"] == "organization"
    for section in (
        "plan_usage",
        "pipeline",
        "automation_today",
        "needs_attention",
        "coach_workload",
        "outcomes",
    ):
        assert section in board, section

    stages = board["pipeline"]["stages"]
    assert stages and all(s["link"].startswith("#/org/candidates") for s in stages)
    assert sum(s["count"] for s in stages) == board["pipeline"]["total"] == 2

    usage = board["plan_usage"]
    assert usage["applications"]["link"].startswith("#/org/")
    assert usage["seats"]["candidates"]["used"] == 2
    assert usage["seats"]["staff"]["used"] == 3  # owner, admin, coach

    assert board["automation_today"]["link"].startswith("#/org/candidates")
    assert board["automation_today"]["held"] >= 1

    workload = {w["coach_id"]: w for w in board["coach_workload"]}
    mine = workload[org.coach.member_id]
    assert mine["candidates"] == 1 and mine["open_items"] >= 1
    assert mine["link"].startswith("#/org/coaches")
    assert board["audit"]["link"] == "#/org/audit"


def test_usage_on_the_dashboard_moves_as_soon_as_work_is_metered(cloud):
    org = build_org(cloud)
    before = cloud.dashboard.organization(org.owner)["plan_usage"]
    assert before["applications"]["used"] == 0
    cloud.usage.reserve(org.org_id, "applications", 3, monthly_limit=15000)
    after = cloud.dashboard.organization(org.owner)["plan_usage"]
    assert after["applications"]["used"] == 3


def test_a_billing_contact_sees_usage_and_no_candidate_data(cloud):
    org = build_org(cloud)
    view = cloud.dashboard.for_principal(org.billing)
    assert view["kind"] == "usage"
    assert "pipeline" not in view and "coach_workload" not in view
