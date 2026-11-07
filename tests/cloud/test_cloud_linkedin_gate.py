"""LinkedIn in the hosted build: off unless the operator turns it on (CloudConfig.linkedin_automation).

LinkedIn's User Agreement forbids automated access, and many candidates' sessions running from data-centre
addresses risk restrictions, so the hosted build keeps LinkedIn automation behind the operator's own switch
(AIRA_CLOUD_LINKEDIN_AUTOMATION), ahead of the plan feature, the organization's policy and the candidate's opt-in
(docs/multitenant.md, "LinkedIn in the hosted build"). An organization's stored grant is data: with the switch off
it stays stored and is simply not honoured.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from cloud_support import build_org, dumps, make_app, sign_in

from aira.hosting import HOOKS
from monosage_cloud.config import CloudConfig
from monosage_cloud.errors import Forbidden
from monosage_cloud.gate import LINKEDIN_OPERATOR_OFF

AUTOPILOT_PLAN = {
    "name": "Linked",
    "positions": ["Architect"],
    "sources": ["aira_jobs"],
    "boards": [],
    "mode": "review",
    "min_score": 60,
    "max_per_run": 2,
    "interval_minutes": 240,
}


@pytest.fixture
def operator_off(tmp_path: Path):
    HOOKS.reset()
    app = make_app(tmp_path, linkedin_automation=False)
    yield app
    app.close()


@pytest.fixture
def operator_on(tmp_path: Path):
    HOOKS.reset()
    app = make_app(tmp_path, linkedin_automation=True)
    yield app
    app.close()


def _grant_linkedin(app, org) -> None:
    """An organization that allowed LinkedIn and Easy Apply, with a candidate who opted in to both: the data an
    installation keeps from before its operator's switch was off (written straight to the database)."""
    with app.db.write() as connection:
        policy = {
            **app.tenancy.policy(org.org_id),
            "linkedin_allowed": True,
            "easy_apply_allowed": True,
            "allowed_sources": ["aira_jobs", "company_boards", "linkedin"],
        }
        connection.execute(
            "UPDATE organizations SET policy = ? WHERE org_id = ?",
            (dumps(policy), org.org_id),
        )
        connection.execute(
            "UPDATE workspaces SET linkedin_opt_in_at = ?, easy_apply_opt_in_at = ? WHERE candidate_id = ?",
            (app.clock.now(), app.clock.now(), org.candidate.member_id),
        )
    app.tenancy.invalidate_policy(org.org_id)


def test_the_shipped_default_is_off_and_the_operator_turns_it_on(monkeypatch):
    monkeypatch.delenv("AIRA_CLOUD_LINKEDIN_AUTOMATION", raising=False)
    assert CloudConfig().linkedin_automation is False
    monkeypatch.setenv("AIRA_CLOUD_LINKEDIN_AUTOMATION", "1")
    assert CloudConfig().linkedin_automation is True
    monkeypatch.setenv("AIRA_CLOUD_LINKEDIN_AUTOMATION", "no")
    assert CloudConfig().linkedin_automation is False


def test_an_organizations_grant_is_not_honoured_while_the_operator_switch_is_off(
    operator_off,
):
    app = operator_off
    org = build_org(app, plan="team")
    _grant_linkedin(app, org)
    candidate = sign_in(app, org.candidate)

    calls = [
        ("POST", "/api/linkedin/connect", {"acknowledge": True}),  # sign-in
        ("POST", "/api/linkedin/restart", {}),
        ("GET", "/api/inbox", None),  # messaging
        ("POST", "/api/inbox/sync", {}),
        ("POST", "/api/search", {"keywords": "architect"}),  # LinkedIn job search
        ("POST", "/api/jobs/search", {"source": "linkedin", "keywords": "architect"}),
        (
            "POST",
            "/api/autopilot/plans",
            {**AUTOPILOT_PLAN, "sources": ["linkedin"]},
        ),
        (
            "POST",
            "/api/autopilot/plans",
            {**AUTOPILOT_PLAN, "name": "Easy", "linkedin_easy_apply": True},
        ),
    ]
    for method, path, body in calls:
        status, data = candidate.call(method, path, body)
        assert status == 403 and data["code"] == "linkedin_off", (path, status, data)
        assert "operator has not enabled it" in data["error"], path

    # Every other check answers the same: the worker's source gate and automatic Easy Apply.
    candidate_id = org.candidate.member_id
    assert (
        app.gate.source_block(org.org_id, "linkedin", candidate_id)
        == LINKEDIN_OPERATOR_OFF
    )
    assert app.gate._easy_apply_allowed(org.org_id, candidate_id) is False
    # Other sources are untouched.
    assert app.gate.source_block(org.org_id, "aira_jobs", candidate_id) == ""

    # The candidate's screens say LinkedIn is off here, and the checklist does not ask them to connect it.
    status, me = candidate.call("GET", "/api/auth/me")
    assert status == 200
    assert me["policy"]["linkedin_allowed"] is False
    assert me["policy"]["linkedin_operator"] is False
    onboarding = app.tenancy.onboarding(org.org_id, candidate_id)
    assert "linkedin" not in {item["id"] for item in onboarding["items"]}

    # The grant is data: it stays stored, and saving the rest of the policy (with it still on) keeps working.
    stored = app.tenancy.org(org.org_id)["policy"]
    assert stored["linkedin_allowed"] is True and stored["easy_apply_allowed"] is True
    app.tenancy.update_policy(
        org.owner,
        {"linkedin_allowed": True, "easy_apply_allowed": True, "cover_letters": False},
    )
    assert app.tenancy.org(org.org_id)["policy"]["linkedin_allowed"] is True


def test_turning_linkedin_on_needs_the_operator_switch(operator_off):
    app = operator_off
    org = build_org(app, plan="team")
    for key in ("linkedin_allowed", "easy_apply_allowed"):
        with pytest.raises(Forbidden) as caught:
            app.tenancy.update_policy(org.owner, {key: True})
        assert caught.value.code == "linkedin_off"
        assert caught.value.message == LINKEDIN_OPERATOR_OFF
    policy = app.tenancy.org(org.org_id)["policy"]
    assert policy["linkedin_allowed"] is False and policy["easy_apply_allowed"] is False
    # Turning it off always works.
    app.tenancy.update_policy(org.owner, {"linkedin_allowed": False})

    owner = sign_in(app, org.owner)
    status, data = owner.call("PATCH", "/api/org/policy", {"linkedin_allowed": True})
    assert status == 403 and data["code"] == "linkedin_off"
    candidate = sign_in(app, org.candidate)
    status, data = candidate.call(
        "POST", "/api/me/easy-apply", {"enable": True, "acknowledge": True}
    )
    assert status == 403 and data["code"] == "linkedin_off"
    # Turning one's own Easy Apply off is never refused.
    status, data = candidate.call("POST", "/api/me/easy-apply", {"enable": False})
    assert status == 200 and data == {"easy_apply": False}


def test_with_the_operator_switch_on_the_existing_gates_still_hold(operator_on):
    app = operator_on
    org = build_org(app, plan="team")
    candidate = sign_in(app, org.candidate)
    candidate_id = org.candidate.member_id

    # The organization's policy is still off by default.
    status, data = candidate.call(
        "POST", "/api/linkedin/connect", {"acknowledge": True}
    )
    assert status == 403 and data["code"] == "linkedin_off"
    assert "organization's policy" in data["error"]

    app.tenancy.update_policy(
        org.owner,
        {"linkedin_allowed": True, "allowed_sources": ["aira_jobs", "linkedin"]},
    )
    # The candidate still has to read the User Agreement notice and opt in for themselves.
    status, data = candidate.call("POST", "/api/linkedin/connect", {})
    assert status == 400 and data["code"] == "acknowledgement_required"
    status, data = candidate.call("POST", "/api/search", {"keywords": "architect"})
    assert status == 403 and "turned LinkedIn on" in data["error"]
    assert "turned LinkedIn on" in app.gate.source_block(
        org.org_id, "linkedin", candidate_id
    )

    status, me = candidate.call("GET", "/api/auth/me")
    assert me["policy"]["linkedin_allowed"] is True
    assert me["policy"]["linkedin_operator"] is True
    onboarding = app.tenancy.onboarding(org.org_id, candidate_id)
    assert "linkedin" in {item["id"] for item in onboarding["items"]}

    # Easy Apply: the organization's switch, then the candidate's own.
    app.tenancy.update_policy(org.owner, {"easy_apply_allowed": True})
    assert app.gate._easy_apply_allowed(org.org_id, candidate_id) is False
    status, data = candidate.call(
        "POST", "/api/me/easy-apply", {"enable": True, "acknowledge": True}
    )
    assert status == 200 and data == {"easy_apply": True}
    assert app.gate._easy_apply_allowed(org.org_id, candidate_id) is True


def test_a_starter_plan_still_needs_the_plan_feature_with_the_switch_on(operator_on):
    app = operator_on
    starter = build_org(app, plan="starter", seats=10, slug_email="s.test")
    browser = sign_in(app, starter.candidate)
    status, data = browser.call("POST", "/api/linkedin/connect", {"acknowledge": True})
    assert status == 402 and data["required_plan"] == "team"
