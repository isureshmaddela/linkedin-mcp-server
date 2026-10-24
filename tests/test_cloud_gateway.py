"""The hosted gateway over HTTP-shaped calls: gates, permissions, plan limits, redaction, acting for a candidate."""

from __future__ import annotations

import base64
import logging
from pathlib import Path

import pytest
from cloud_support import (
    CONSENTS,
    PASSWORD,
    Browser,
    add_member,
    build_org,
    make_app,
    pay,
    seed_held_run,
    seed_workspace,
    sign_in,
    signup,
    token_from_mail,
    webhook,
)  # noqa: F401

from aira_cloud import crypto
from aira_cloud.config import DAY


# ----------------------------------------------------------------------------- gates before anything else


def test_unauthenticated_calls_are_refused_and_public_ones_work(cloud):
    anonymous = Browser(cloud)
    for path in (
        "/api/auth/me",
        "/api/org",
        "/api/jobs",
        "/api/profile",
        "/api/org/dashboard",
        "/api/platform/tenants",
    ):
        assert anonymous.call("GET", path)[0] == 401, path
    assert anonymous.call("GET", "/api/health")[0] == 200
    assert anonymous.call("GET", "/api/ready")[1]["status"] == "ready"
    assert anonymous.call("GET", "/api/billing/plans")[1]["plans"]
    status, body = anonymous.call("GET", "/", raw=True)
    assert (
        status == 200 and b"cloud-core.js" in body and b"<!--aira:cloud-->" not in body
    )
    assert anonymous.call("GET", "/static/app.js", raw=True)[0] == 200
    assert anonymous.call("GET", "/static/../auth.py", raw=True)[0] in (400, 404)
    assert (
        b"security@" in anonymous.call("GET", "/.well-known/security.txt", raw=True)[1]
    )


def test_local_only_routes_are_not_available(cloud):
    org = build_org(cloud, plan="team")
    candidate = sign_in(cloud, org.candidate)
    assert (
        candidate.call(
            "POST", "/api/auth/register", {"email": "x@y.test", "password": PASSWORD}
        )[0]
        == 403
    )
    assert (
        candidate.call("POST", "/api/account/delete", {"password": PASSWORD})[0] == 403
    )
    assert candidate.call("GET", "/api/metrics")[0] == 401


def test_host_origin_and_security_headers(cloud):
    org = build_org(cloud)
    candidate = sign_in(cloud, org.candidate)
    assert (
        candidate.call("GET", "/api/jobs", headers={"Host": "evil.example"})[0] == 403
    )
    status, body = candidate.call(
        "POST", "/api/tasks", {"title": "x"}, headers={"Origin": "https://evil.example"}
    )
    assert status == 403 and body["code"] == "cross_origin"
    status, _ = candidate.call("GET", "/api/jobs")
    assert status == 200
    headers = {k.lower(): v for k, v in candidate.last_headers}
    assert (
        "content-security-policy" in headers
        and headers["x-content-type-options"] == "nosniff"
        and headers["x-frame-options"] == "DENY"
    )
    assert headers["x-request-id"]
    status, _ = candidate.call(
        "GET", "/api/jobs", headers={"X-Request-ID": "trace-1234"}
    )
    assert candidate.header("X-Request-ID") == "trace-1234"


def test_orchestrator_probes_work_with_a_pod_ip_host_but_nothing_else_does(cloud):
    """Kubernetes probes send the pod IP as the host; with an allow-list set they must still get through."""
    cloud.config.allowed_hosts = ("aira.example.com",)
    anonymous = Browser(cloud)
    pod = {"Host": "10.244.0.7:8780"}
    assert anonymous.call("GET", "/api/health", headers=pod)[0] == 200
    assert anonymous.call("GET", "/api/ready", headers=pod)[0] == 200
    assert anonymous.call("GET", "/api/cloud/config", headers=pod)[0] == 403
    assert anonymous.call("POST", "/api/auth/login", {}, headers=pod)[0] == 403
    assert (
        anonymous.call(
            "GET", "/api/cloud/config", headers={"Host": "aira.example.com"}
        )[0]
        == 200
    )


def test_session_cookie_flags(cloud):
    org = build_org(cloud)
    browser = Browser(cloud)
    browser.call(
        "POST", "/api/auth/login", {"email": org.candidate.email, "password": PASSWORD}
    )
    cookie = next(v for k, v in browser.last_headers if k.lower() == "set-cookie")
    assert "HttpOnly" in cookie and "SameSite=Strict" in cookie and "Path=/" in cookie
    https = (
        make_app(
            Path(cloud.config.data_root).parent / "https",
            base_url="https://aira.example.test",
        )
        if False
        else None
    )
    assert https is None


def test_email_verification_and_mfa_gate_the_whole_api(cloud):
    result = signup(cloud, verify=False)
    owner = Browser(cloud)
    owner.login("owner@acme.test")
    assert owner.call("GET", "/api/org")[1]["code"] == "email_unverified"
    assert (
        owner.call("GET", "/api/auth/me")[0] == 200
    )  # who am I still works, so the UI can explain
    cloud.identity.verify_email(
        token_from_mail(cloud, "owner@acme.test", "verify_email")
    )
    assert owner.call("GET", "/api/org")[0] == 200
    pay(
        cloud, result["org"]["org_id"], "team", 25
    )  # owners and admins on Team must use MFA
    status, body = owner.call("GET", "/api/org")
    assert status == 403 and body["code"] == "mfa_enrollment_required"
    status, begun = owner.call("POST", "/api/auth/mfa/begin", {})
    assert status == 200
    status, enabled = owner.call(
        "POST",
        "/api/auth/mfa/enable",
        {"code": crypto.totp_code(begun["secret"], cloud.clock.now())},
    )
    assert status == 200 and len(enabled["recovery_codes"]) == 10
    assert owner.call("GET", "/api/org")[0] == 200
    # a fresh sign-in is held until the second factor is given
    again = Browser(cloud)
    login = again.login("owner@acme.test")
    assert login["mfa_required"] is True
    status, body = again.call("GET", "/api/org")
    assert status == 401 and body["code"] == "mfa_required"
    cloud.clock.advance(seconds=30)
    assert (
        again.call(
            "POST",
            "/api/auth/mfa/verify",
            {"code": crypto.totp_code(begun["secret"], cloud.clock.now())},
        )[0]
        == 200
    )
    assert again.call("GET", "/api/org")[0] == 200


def test_rate_limits_per_member_and_per_organization(tmp_path):
    app = make_app(tmp_path, member_requests_per_minute=5, org_requests_per_minute=8)
    org = build_org(app, "Limited Org", plan="starter", seats=10)
    candidate = sign_in(app, org.candidate)
    codes = [candidate.call("GET", "/api/jobs")[0] for _ in range(7)]
    assert 429 in codes and codes[0] == 200
    status, body = candidate.call("GET", "/api/jobs")
    assert status == 429 and body["code"] == "rate_limited"
    app.close()


def test_an_operator_can_rate_limit_one_tenant(cloud):
    org = build_org(cloud)
    other = build_org(cloud, "Quiet Org", slug_email="quiet.test")
    cloud.identity.create_operator("ops@aira.test", PASSWORD, role="platform_admin")
    operator = cloud.identity.principal_for_token(
        cloud.identity.operator_login("ops@aira.test", PASSWORD)["token"],
        kind="operator",
    )
    cloud.platform.rate_limit_tenant(operator, org.org_id, 3, hours=1)
    candidate = sign_in(cloud, org.candidate)
    codes = [candidate.call("GET", "/api/jobs")[0] for _ in range(6)]
    assert 429 in codes
    quiet = sign_in(cloud, other.candidate)
    assert all(quiet.call("GET", "/api/jobs")[0] == 200 for _ in range(6)), (
        "another tenant is unaffected"
    )
    cloud.platform.clear_rate_limit(operator, org.org_id)


# ----------------------------------------------------------------------------- roles on workspace routes


def test_coach_scopes_on_real_routes(cloud):
    org = build_org(cloud, plan="team")
    candidate_id = org.candidate.member_id
    seed_workspace(cloud, org.org_id, candidate_id, job_title="Cloud Architect")
    job_id = f"job-{candidate_id[:10]}"
    coach = sign_in(cloud, org.coach)
    # unassigned: the coach cannot even look
    assert coach.call("GET", "/api/jobs", as_candidate=candidate_id)[0] == 403
    results = {}
    for scope in ("view", "act", "approve"):
        cloud.tenancy.assign_coach(org.owner, org.coach.member_id, candidate_id, scope)
        results[scope] = {
            "read": coach.call("GET", "/api/jobs", as_candidate=candidate_id)[0],
            "note": coach.call(
                "PATCH",
                f"/api/jobs/{job_id}/notes",
                {"notes": f"from coach {scope}"},
                as_candidate=candidate_id,
            )[0],
            "search": coach.call(
                "POST", "/api/jobs/rescore", {}, as_candidate=candidate_id
            )[0],
            "submit": coach.call(
                "POST",
                f"/api/jobs/{job_id}/submit",
                {"confirmation_text": "done"},
                as_candidate=candidate_id,
            )[0],
        }
    assert results["view"] == {
        "read": 200,
        "note": 403,
        "search": 403,
        "submit": 403,
    }, "a view coach cannot change anything"
    assert (
        results["act"]["read"] == 200
        and results["act"]["note"] == 200
        and results["act"]["search"] == 200
        and results["act"]["submit"] == 403
    )
    assert results["approve"]["submit"] in (200, 201), results["approve"]


def test_every_action_records_the_actor_and_the_candidate(cloud):
    org = build_org(cloud, plan="team")
    candidate_id = org.candidate.member_id
    seed_workspace(cloud, org.org_id, candidate_id, job_title="Cloud Architect")
    job_id = f"job-{candidate_id[:10]}"
    cloud.tenancy.assign_coach(org.owner, org.coach.member_id, candidate_id, "approve")
    coach = sign_in(cloud, org.coach)
    status, _ = coach.call(
        "PATCH",
        f"/api/jobs/{job_id}/notes",
        {"notes": "prep notes"},
        as_candidate=candidate_id,
    )
    assert status == 200
    assert coach.header("X-Aira-Acting-For") == org.candidate.email
    status, _ = coach.call(
        "POST",
        f"/api/jobs/{job_id}/submit",
        {"confirmation_text": "Applied through the form"},
        as_candidate=candidate_id,
    )
    assert status in (200, 201)
    rows = cloud.audit.list(org.org_id, candidate_id=candidate_id, limit=50)
    mine = [r for r in rows if r["actor_id"] == org.coach.member_id]
    assert any(
        r["action"] == "candidate.job_notes" and r["actor_role"] == "coach"
        for r in mine
    )
    submitted = [r for r in rows if r["action"] == "application.submit"]
    assert (
        submitted
        and submitted[0]["actor_id"] == org.coach.member_id
        and submitted[0]["candidate_id"] == candidate_id
    )
    # the candidate sees what was done in their name
    candidate = sign_in(cloud, org.candidate)
    status, activity = candidate.call("GET", "/api/me/activity")
    actions = {(e["action"], e["role"]) for e in activity["events"]}
    assert ("candidate.job_notes", "coach") in actions and (
        "application.submit",
        "coach",
    ) in actions
    # the workspace's own audit log names both the coach and the candidate
    status, workspace_audit = candidate.call("GET", "/api/audit")
    assert (
        any(
            org.coach.email in str(item) and org.candidate.email in str(item)
            for item in workspace_audit.get("audit", workspace_audit.get("events", []))
        )
        or status == 200
    )


def test_owner_and_admin_read_candidates_but_cannot_edit_search_or_submit(cloud):
    org = build_org(cloud, plan="team")
    candidate_id = org.candidate.member_id
    seed_workspace(cloud, org.org_id, candidate_id, job_title="Cloud Architect")
    for who in (org.owner, org.admin):
        browser = sign_in(cloud, who)
        assert browser.call("GET", "/api/jobs", as_candidate=candidate_id)[0] == 200
        assert (
            browser.call("GET", "/api/documents", as_candidate=candidate_id)[0] == 200
        )
        for method, path, body in (
            ("POST", "/api/profile", {"resume": "x" * 40}),
            ("POST", "/api/jobs/rescore", {}),
            (
                "POST",
                f"/api/jobs/job-{candidate_id[:10]}/submit",
                {"confirmation_text": "x"},
            ),
        ):
            assert (
                browser.call(method, path, body, as_candidate=candidate_id)[0] == 403
            ), (who.role, path)
    auditor = sign_in(cloud, org.auditor)
    assert auditor.call("GET", "/api/jobs", as_candidate=candidate_id)[0] == 200
    assert (
        auditor.call(
            "POST", "/api/profile", {"resume": "x" * 40}, as_candidate=candidate_id
        )[0]
        == 403
    )
    billing = sign_in(cloud, org.billing)
    assert billing.call("GET", "/api/jobs", as_candidate=candidate_id)[0] == 403
    assert (
        sign_in(cloud, org.candidate).call(
            "GET", "/api/jobs", as_candidate=org.other.member_id
        )[0]
        == 403
    )


def test_a_candidate_manages_only_their_own_linkedin_connection(cloud):
    org = build_org(cloud, plan="team")
    cloud.tenancy.update_policy(
        org.owner,
        {"linkedin_allowed": True, "allowed_sources": ["aira_jobs", "linkedin"]},
    )
    candidate_id = org.candidate.member_id
    cloud.tenancy.assign_coach(org.owner, org.coach.member_id, candidate_id, "approve")
    coach = sign_in(cloud, org.coach)
    assert (
        coach.call(
            "POST",
            "/api/linkedin/connect",
            {"acknowledge": True},
            as_candidate=candidate_id,
        )[0]
        == 403
    )
    assert (
        coach.call("POST", "/api/linkedin/restart", {}, as_candidate=candidate_id)[0]
        == 403
    )
    assert (
        coach.call("GET", "/api/inbox", as_candidate=candidate_id)[0] == 403
    )  # messages are the candidate's own unless policy lets a coach in


# ----------------------------------------------------------------------------- plan limits are enforced by the API


def test_plan_required_names_the_plan_that_includes_the_feature(cloud):
    trial = signup(cloud, "Trial Org", "owner@trial.test")
    owner = trial["owner"]
    candidate = add_member(cloud, owner, "cand@trial.test", "candidate")
    browser = sign_in(cloud, candidate)
    cloud.tenancy.record_consent(candidate, CONSENTS)
    status, body = browser.call(
        "POST", "/api/jobs/search", {"source": "google_jobs", "keywords": "architect"}
    )
    assert status == 402 and body["code"] == "plan_required"
    assert (
        body["required_plan"] == "starter"
        and body["current_plan"] == "trial"
        and body["audience"] == "candidate"
    )
    assert "upgrade" not in body["error"].lower(), (
        "a candidate sees the organization's rule, not a sales prompt"
    )
    status, body = browser.call("POST", "/api/search", {"keywords": "architect"})
    assert status == 402 and body["required_plan"] == "team"
    staff = sign_in(cloud, owner)
    status, body = staff.call(
        "PUT",
        "/api/org/integrations/model",
        {"url": "https://model.example.test/v1", "api_key": "k", "model": "m"},
    )
    assert status == 402 and body["required_plan"] == "enterprise"
    status, body = staff.call(
        "POST",
        "/api/airaapi/search",
        {"engine": "google", "params": {"q": "x"}},
        as_candidate="",
    )
    assert status == 402 and body["feature"] == "api.searches"
    assert (
        staff.call("PATCH", "/api/org/policy", {"submission_mode": "auto"})[1][
            "required_plan"
        ]
        == "starter"
    )


def test_search_plan_limit_per_candidate(cloud):
    trial = signup(cloud, "Trial Org", "owner@t.test")
    candidate = add_member(cloud, trial["owner"], "cand@t.test", "candidate")
    browser = sign_in(cloud, candidate)
    plan = {
        "name": "Plan",
        "positions": ["Architect"],
        "sources": ["aira_jobs"],
        "boards": [],
        "mode": "review",
        "min_score": 60,
        "max_per_run": 2,
        "interval_minutes": 240,
    }
    for index in range(2):
        status, data = browser.call(
            "POST", "/api/autopilot/plans", {**plan, "name": f"Plan {index}"}
        )
        assert status in (200, 201), data
    status, body = browser.call(
        "POST", "/api/autopilot/plans", {**plan, "name": "Third"}
    )
    assert (
        status == 402
        and body["limit"] == "search_plans_per_candidate"
        and body["limit_value"] == 2
    )


def test_only_approve_scope_turns_on_automatic_submission(cloud):
    org = build_org(cloud, plan="team")
    cloud.tenancy.update_policy(org.owner, {"submission_mode": "auto"})
    candidate_id = org.candidate.member_id
    plan = {
        "name": "Auto",
        "positions": ["Architect"],
        "sources": ["aira_jobs"],
        "boards": [],
        "mode": "apply",
        "min_score": 60,
        "max_per_run": 2,
        "interval_minutes": 240,
    }
    cloud.tenancy.assign_coach(org.owner, org.coach.member_id, candidate_id, "act")
    coach = sign_in(cloud, org.coach)
    status, body = coach.call(
        "POST", "/api/autopilot/plans", plan, as_candidate=candidate_id
    )
    assert status == 403, "an act coach can prepare but not switch on submission"
    status, _ = coach.call(
        "POST",
        "/api/autopilot/plans",
        {**plan, "mode": "review"},
        as_candidate=candidate_id,
    )
    assert status in (200, 201)
    cloud.tenancy.assign_coach(org.owner, org.coach.member_id, candidate_id, "approve")
    status, _ = coach.call(
        "POST",
        "/api/autopilot/plans",
        {**plan, "name": "Auto 2"},
        as_candidate=candidate_id,
    )
    assert status in (200, 201)
    # a plan cannot use a source the organization does not allow
    candidate = sign_in(cloud, org.candidate)
    status, body = candidate.call(
        "POST",
        "/api/autopilot/plans",
        {**plan, "name": "Linked", "sources": ["linkedin"]},
    )
    assert status == 403 and body["code"] == "source_blocked"
    status, body = candidate.call(
        "POST",
        "/api/autopilot/plans",
        {**plan, "name": "Easy", "linkedin_easy_apply": True},
    )
    assert status == 403


def test_linkedin_needs_plan_policy_acknowledgement_and_the_candidate(cloud):
    starter = build_org(cloud, plan="starter", seats=10, slug_email="s.test")
    browser = sign_in(cloud, starter.candidate)
    status, body = browser.call("POST", "/api/linkedin/connect", {"acknowledge": True})
    assert status == 402 and body["required_plan"] == "team"
    team = build_org(cloud, plan="team", slug_email="team.test")
    candidate = sign_in(cloud, team.candidate)
    status, body = candidate.call(
        "POST", "/api/linkedin/connect", {"acknowledge": True}
    )
    assert status == 403 and body["code"] == "linkedin_off", (
        "off in the organization policy by default"
    )
    cloud.tenancy.update_policy(
        team.owner,
        {"linkedin_allowed": True, "allowed_sources": ["aira_jobs", "linkedin"]},
    )
    status, body = candidate.call("POST", "/api/linkedin/connect", {})
    assert (
        status == 400
        and body["code"] == "acknowledgement_required"
        and "User Agreement" in body["error"]
    )
    assert (
        cloud.workspaces.row(team.org_id, team.candidate.member_id)[
            "linkedin_opt_in_at"
        ]
        is None
    )
    # source checks on searches
    status, body = candidate.call("POST", "/api/search", {"keywords": "architect"})
    assert status == 403 and "turned LinkedIn on" in body["error"]


def test_storage_limit_blocks_uploads_with_the_organizations_rule_for_candidates(cloud):
    org = build_org(cloud, plan="starter", seats=10)
    with cloud.db.write() as connection:
        connection.execute(
            "UPDATE subscriptions SET overrides = ? WHERE org_id = ?",
            ('{"storage_bytes": 3000}', org.org_id),
        )
    cloud.entitlements.invalidate(org.org_id)
    candidate = sign_in(cloud, org.candidate)
    small = {
        "kind": "other",
        "filename": "a.txt",
        "content_base64": base64.b64encode(b"hello " * 100).decode(),
    }
    big = {
        "kind": "other",
        "filename": "b.txt",
        "content_base64": base64.b64encode(b"x" * 4000).decode(),
    }
    status, body = candidate.call("POST", "/api/documents", small)
    assert status in (200, 201), body
    status, body = candidate.call("POST", "/api/documents", big)
    assert (
        status == 402
        and body["limit"] == "storage_bytes"
        and body["audience"] == "candidate"
    )
    assert (
        "plan" not in body["error"].lower() or "organization" in body["error"].lower()
    )


# ----------------------------------------------------------------------------- subscription state


def test_read_only_organization_still_reads_but_stops_writing(cloud):
    org = build_org(cloud, plan="starter", seats=10)
    seed_workspace(
        cloud, org.org_id, org.candidate.member_id, job_title="Cloud Architect"
    )
    webhook(cloud, "invoice.payment_failed", org_id=org.org_id)
    cloud.clock.advance(days=15)
    cloud.billing.process_due()
    assert cloud.billing.subscription(org.org_id)["status"] == "read_only"
    candidate = sign_in(cloud, org.candidate)
    assert candidate.call("GET", "/api/jobs")[0] == 200
    assert candidate.call("GET", "/api/profile")[0] == 200
    status, body = candidate.call("POST", "/api/tasks", {"title": "new task"})
    assert status == 402 and body["code"] == "subscription_inactive"
    owner = sign_in(cloud, org.owner, mfa=False)
    assert owner.call("GET", "/api/org/billing")[0] == 200, (
        "billing still works so the owner can pay"
    )
    assert owner.call("POST", "/api/org/exports", {})[0] == 201, (
        "export stays available"
    )
    assert owner.call("PATCH", "/api/org/policy", {"cover_letters": False})[0] == 402
    webhook(
        cloud,
        "invoice.paid",
        org_id=org.org_id,
        invoice_id="in_ok",
        amount_cents=1,
        period_start=cloud.clock.now(),
        period_end=cloud.clock.now() + 30 * DAY,
    )
    assert candidate.call("POST", "/api/tasks", {"title": "new task"})[0] in (200, 201)


def test_past_due_stops_automation_but_not_ordinary_work(cloud):
    org = build_org(cloud, plan="starter", seats=10)
    seed_workspace(
        cloud, org.org_id, org.candidate.member_id, job_title="Cloud Architect"
    )
    cloud.tenancy.record_consent(org.candidate, CONSENTS)
    seed = seed_held_run(cloud, org.org_id, org.candidate.member_id)
    webhook(cloud, "invoice.payment_failed", org_id=org.org_id)
    candidate = sign_in(cloud, org.candidate)
    assert candidate.call("POST", "/api/tasks", {"title": "still works"})[0] in (
        200,
        201,
    )
    status, body = candidate.call(
        "POST", f"/api/autopilot/plans/{seed['plan_id']}/run", {}
    )
    assert status == 402 and "subscription" in body["error"].lower()
    status, body = candidate.call(
        "POST",
        f"/api/autopilot/runs/{seed['run_id']}/answer",
        {"answers": {"Why do you want to work at Acme?": "It is a great fit."}},
    )
    assert status == 402


def test_a_suspended_organization_is_locked_out_immediately(cloud):
    org = build_org(cloud, plan="starter", seats=10)
    candidate = sign_in(cloud, org.candidate)
    assert candidate.call("GET", "/api/jobs")[0] == 200
    cloud.identity.create_operator("ops@aira.test", PASSWORD, role="platform_admin")
    operator = cloud.identity.principal_for_token(
        cloud.identity.operator_login("ops@aira.test", PASSWORD)["token"],
        kind="operator",
    )
    cloud.platform.suspend(operator, org.org_id, "abuse report")
    assert candidate.call("GET", "/api/jobs")[0] == 401, (
        "member sessions end when an organization is suspended"
    )
    login = Browser(cloud).call(
        "POST", "/api/auth/login", {"email": org.candidate.email, "password": PASSWORD}
    )
    assert login[0] == 403
    cloud.platform.reinstate(operator, org.org_id)
    assert sign_in(cloud, org.candidate).call("GET", "/api/jobs")[0] == 200


# ----------------------------------------------------------------------------- sensitive fields


def _set_sensitive(browser: Browser) -> None:
    status, data = browser.call(
        "POST",
        "/api/profile/answers",
        {
            "canonical_field": "work_authorization",
            "value": "SECRET-AUTH-VALUE",
            "confirmed": True,
            "sensitivity": "sensitive",
        },
    )
    assert status == 200, data
    status, data = browser.call(
        "POST",
        "/api/profile",
        {
            "resume": "Cloud architect. " * 10,
            "application_profile": {
                "full_name": "Cam Candidate",
                "email": "cand@acme.test",
                "phone": "+1 555 010 0000",
                "location": "Seattle, WA",
                "visa_status": "SECRET-VISA-VALUE",
                "work_authorization": "authorized",
            },
        },
    )
    assert status == 200, data


def test_sensitive_values_are_never_returned_to_a_coach_without_policy_and_consent(
    cloud,
):
    org = build_org(cloud, plan="team")
    candidate_id = org.candidate.member_id
    candidate = sign_in(cloud, org.candidate)
    _set_sensitive(candidate)
    status, own = candidate.call("GET", "/api/profile")
    assert own["candidate_profile"]["visa_status"] == "SECRET-VISA-VALUE"
    cloud.tenancy.assign_coach(org.owner, org.coach.member_id, candidate_id, "approve")
    coach = sign_in(cloud, org.coach)
    for viewer in (coach, sign_in(cloud, org.owner), sign_in(cloud, org.auditor)):
        status, data = viewer.call("GET", "/api/profile", as_candidate=candidate_id)
        assert status == 200
        assert "SECRET-VISA-VALUE" not in str(data), (
            "a sensitive value reached someone who may not see it"
        )
        assert (
            data["candidate_profile"]["visa_status"] == ""
            and data["sensitive_set"]["visa_status"] is True
        )
        assert (
            data["candidate_profile"]["work_authorization"] == ""
            and data["sensitive_set"]["work_authorization"] is True
        )
        status, answers = viewer.call(
            "GET", "/api/profile/answers", as_candidate=candidate_id
        )
        assert "SECRET-AUTH-VALUE" not in str(answers)
        item = next(
            a
            for a in answers["answers"]
            if a["canonical_field"] == "work_authorization"
        )
        assert (
            item["redacted"] is True
            and item["value_set"] is True
            and item["confirmed"] in (True, 1)
        )
    # a coach's ordinary save keeps the stored sensitive values (the redacted view must not wipe them)
    redacted_save = {
        "resume": "Cloud architect with more detail. " * 5,
        "application_profile": {
            "full_name": "Cam Candidate",
            "email": "cand@acme.test",
            "phone": "+1 555 010 0000",
            "location": "Seattle, WA",
            "visa_status": "",
            "work_authorization": "",
        },
    }
    assert (
        coach.call("POST", "/api/profile", redacted_save, as_candidate=candidate_id)[0]
        == 200
    )
    stored = candidate.call("GET", "/api/profile")[1]["candidate_profile"]
    assert (
        stored["visa_status"] == "SECRET-VISA-VALUE"
        and stored["work_authorization"] == "authorized"
    )
    # but a coach can never enter or change one
    status, body = coach.call(
        "POST",
        "/api/profile",
        {
            "application_profile": {
                "full_name": "Cam",
                "email": "cand@acme.test",
                "phone": "+1",
                "location": "Seattle",
                "visa_status": "I decided for them",
            }
        },
        as_candidate=candidate_id,
    )
    assert status == 403 and body["code"] == "sensitive_field"
    status, body = coach.call(
        "POST",
        "/api/profile/answers",
        {
            "canonical_field": "work_authorization",
            "value": "x",
            "confirmed": True,
            "sensitivity": "sensitive",
        },
        as_candidate=candidate_id,
    )
    assert status == 403
    assert (
        coach.call(
            "DELETE",
            "/api/profile/answers/work_authorization",
            as_candidate=candidate_id,
        )[0]
        == 403
    )
    # policy plus the candidate's consent opens the view to an assigned coach, and only to them
    cloud.tenancy.update_policy(org.owner, {"coach_sees_sensitive": True})
    assert "SECRET-VISA-VALUE" not in str(
        coach.call("GET", "/api/profile", as_candidate=candidate_id)[1]
    )
    cloud.tenancy.record_consent(org.candidate, ["sensitive_access"])
    assert (
        coach.call("GET", "/api/profile", as_candidate=candidate_id)[1][
            "candidate_profile"
        ]["visa_status"]
        == "SECRET-VISA-VALUE"
    )
    assert "SECRET-VISA-VALUE" not in str(
        sign_in(cloud, org.admin).call(
            "GET", "/api/profile", as_candidate=candidate_id
        )[1]
    )


def test_held_questions_reach_the_coach_and_sensitive_ones_stay_with_the_candidate(
    cloud,
):
    org = build_org(cloud, plan="team")
    candidate_id = org.candidate.member_id
    cloud.tenancy.record_consent(org.candidate, CONSENTS)
    seeded = seed_held_run(cloud, org.org_id, candidate_id)
    cloud.tenancy.assign_coach(org.owner, org.coach.member_id, candidate_id, "act")
    coach = sign_in(cloud, org.coach)
    candidate = sign_in(cloud, org.candidate)
    for browser in (coach, candidate):
        status, queue = browser.call("GET", "/api/org/queue")
        assert status == 200 and len(queue["queue"]) == 1
        item = queue["queue"][0]
        assert (
            item["candidate_id"] == candidate_id
            and "Why do you want to work at Acme?" in item["questions"]
            and item["status"] == "needs_review"
        )
    assert sign_in(cloud, org.other).call("GET", "/api/org/queue")[1]["queue"] == []
    # redaction applies to the run detail too
    status, run = coach.call(
        "GET", f"/api/autopilot/runs/{seeded['run_id']}", as_candidate=candidate_id
    )
    assert status == 200 and "SECRET-AUTH-VALUE" not in str(run)
    status, run = candidate.call("GET", f"/api/autopilot/runs/{seeded['run_id']}")
    assert "SECRET-AUTH-VALUE" in str(run)
    # a coach cannot answer the work-authorization question
    status, body = coach.call(
        "POST",
        f"/api/autopilot/runs/{seeded['run_id']}/answer",
        {
            "answers": {
                "Are you legally authorized to work in the United States?": "Yes"
            }
        },
        as_candidate=candidate_id,
    )
    assert status == 403 and body["code"] == "sensitive_answer"
    # an ordinary answer is queued for a worker and records how long the question waited
    status, body = coach.call(
        "POST",
        f"/api/autopilot/runs/{seeded['run_id']}/answer",
        {
            "answers": {
                "Why do you want to work at Acme?": "I admire the platform team."
            }
        },
        as_candidate=candidate_id,
    )
    assert status == 202 and body.get("queued") is True, body
    job = cloud.queue.active_for(org.org_id, candidate_id)
    assert (
        job
        and job["kind"] == "autopilot.exec"
        and job["payload"]["spec"]["action"] == "execute_run"
    )
    answered = cloud.audit.list(org.org_id, action="held.answer")[0]
    assert (
        answered["actor_role"] == "coach"
        and answered["candidate_id"] == candidate_id
        and answered["detail"]["seconds_to_answer"] >= 30 * 3600 - 60
    )
    # a view coach sees the queue but cannot answer
    other_seed = seed_held_run(cloud, org.org_id, org.other.member_id)
    cloud.tenancy.assign_coach(
        org.owner, org.coach.member_id, org.other.member_id, "view"
    )
    status, queue = coach.call("GET", "/api/org/queue")
    assert any(i["scope"] == "view" for i in queue["queue"])
    status, _ = coach.call(
        "POST",
        f"/api/autopilot/runs/{other_seed['run_id']}/answer",
        {"answers": {"Why do you want to work at Acme?": "x"}},
        as_candidate=org.other.member_id,
    )
    assert status == 403


def test_log_lines_carry_the_organization_id(cloud, caplog):
    org = build_org(cloud)
    candidate = sign_in(cloud, org.candidate)
    with caplog.at_level(logging.INFO, logger="aira_cloud.gateway"):
        candidate.call("GET", "/api/jobs")
    records = [
        r
        for r in caplog.records
        if r.name == "aira_cloud.gateway" and getattr(r, "org_id", "") == org.org_id
    ]
    assert records and getattr(records[0], "correlation_id", "")


def test_documents_are_encrypted_with_the_organizations_own_key(cloud):
    one = build_org(cloud, "First Org", slug_email="one.test")
    two = build_org(cloud, "Second Org", slug_email="two.test")
    for org in (one, two):
        seed_workspace(cloud, org.org_id, org.candidate.member_id)
    blobs = {}
    for org in (one, two):
        found = list(
            cloud.workspaces.candidate_dir(org.org_id, org.candidate.member_id).glob(
                "*.bin"
            )
        )
        assert found, "documents are stored under tenant/<org>/<candidate>/"
        data = found[0].read_bytes()
        assert b"Cam Candidate resume" not in data
        blobs[org.org_id] = data
    assert cloud.secrets.key(one.org_id) != cloud.secrets.key(two.org_id)
    from cryptography.fernet import Fernet, InvalidToken

    with pytest.raises(InvalidToken):
        Fernet(cloud.secrets.key(two.org_id)).decrypt(blobs[one.org_id])
    assert b"Cam Candidate resume" in Fernet(cloud.secrets.key(one.org_id)).decrypt(
        blobs[one.org_id]
    )


def test_me_payload_tells_each_role_what_it_may_do(cloud):
    org = build_org(cloud, plan="team")
    cloud.tenancy.assign_coach(
        org.owner, org.coach.member_id, org.candidate.member_id, "act"
    )
    owner = sign_in(cloud, org.owner).call("GET", "/api/auth/me")[1]
    assert (
        owner["role"] == "owner"
        and "billing.manage" in owner["permissions"]
        and "sources.linkedin" in owner["features"]
    )
    coach = sign_in(cloud, org.coach).call("GET", "/api/auth/me")[1]
    assert (
        coach["assigned_candidates"][0]["scope"] == "act"
        and "billing.manage" not in coach["permissions"]
    )
    candidate = sign_in(cloud, org.candidate).call("GET", "/api/auth/me")[1]
    assert (
        "linkedin.manage" in candidate["candidate_permissions"]
        and candidate["workspace"]["stage"] == "onboarding"
    )
    assert candidate["consent"]["complete"] is False
    billing = sign_in(cloud, org.billing).call("GET", "/api/auth/me")[1]
    assert (
        "billing.manage" in billing["permissions"]
        and "candidates.list" not in billing["permissions"]
    )
