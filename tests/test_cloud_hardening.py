"""Key rotation, legal hold, the SIEM audit stream, HSTS and queue priority."""

from __future__ import annotations


import pytest
from cloud_support import (
    CONSENTS,
    PASSWORD,
    Browser,
    audit_actions,
    build_org,
    local_module,
    make_app,
    seed_workspace,
    sign_in,
)  # noqa: F401
from serpapi_stub import DEFAULT_KEY, SerpApiStub

from aira.hosting import HOOKS
from aira_cloud.errors import Conflict, PlanRequired
from aira_cloud.worker import Worker


def operator(app):
    app.identity.create_operator("ops@aira.test", PASSWORD, role="platform_admin")
    return app.identity.principal_for_token(
        app.identity.operator_login("ops@aira.test", PASSWORD)["token"], kind="operator"
    )


def make_enterprise(app, org, ops=None):
    ops = ops or operator(app)
    app.billing.activate_enterprise(ops, org.org_id, seats=100)
    return ops


# ----------------------------------------------------------------------------- key rotation


def test_rotation_re_encrypts_documents_and_api_secrets_before_old_keys_are_retired(
    cloud, monkeypatch
):
    stub = SerpApiStub()
    stub.start()
    monkeypatch.setenv("AIRA_SERPAPI_BASE_URL", stub.base_url)
    try:
        org = build_org(cloud, plan="team", seats=10, apps=["api"])
        owner = sign_in(cloud, org.owner)
        assert (
            owner.call("POST", "/api/airaapi/key", {"api_key": DEFAULT_KEY})[0] == 200
        )
        candidate_id = org.candidate.member_id
        document = seed_workspace(cloud, org.org_id, candidate_id)["document_id"]
        cloud.secrets.put(org.org_id, "extra", "another secret")
        first = cloud.secrets.current_version(org.org_id)
        blob_path = next(
            cloud.workspaces.candidate_dir(org.org_id, candidate_id).glob("*.bin")
        )
        before = blob_path.read_bytes()
        old_key = cloud.secrets.key(org.org_id, first)
        detail = cloud.lifecycle.rotate_org_keys(org.org_id)
        assert (
            detail["documents"] == 1
            and detail["api_secrets"] == 1
            and detail["secrets"]
            == 2  # "extra" and the owner's TOTP secret (Team requires MFA)
            and detail["keys_retired"] == 1
        )
        assert cloud.secrets.current_version(org.org_id) == first + 1
        after = blob_path.read_bytes()
        assert after != before, "the document was re-encrypted"
        from cryptography.fernet import Fernet, InvalidToken

        with pytest.raises(InvalidToken):
            Fernet(old_key).decrypt(
                after
            )  # the retired key can no longer read anything
        assert (
            Fernet(cloud.secrets.key(org.org_id))
            .decrypt(after)
            .startswith(b"Cam Candidate resume.")
        )
        # everything still works under the new key
        local = local_module()
        with cloud.workspaces.bind(org.org_id, candidate_id, kind="system"):
            with local._connect() as connection:
                _, content = local._document_store().read(
                    connection, owner_id=candidate_id, document_id=document
                )
        assert content.startswith(b"Cam Candidate resume.")
        assert cloud.secrets.get(org.org_id, "extra") == "another secret"
        status, account = owner.call("GET", "/api/org/integrations")
        assert (
            account["serpapi"]["configured"]
            and account["serpapi"]["last4"] == DEFAULT_KEY[-4:]
        )
        assert (
            owner.call(
                "POST",
                "/api/airaapi/search",
                {"engine": "google", "params": {"q": "coffee"}},
            )[0]
            == 200
        ), "the SerpApi key still decrypts"
        assert "security.keys_rotated" in audit_actions(cloud, org.org_id, "security.")
    finally:
        stub.stop()


def test_an_operator_queues_rotation_and_a_worker_does_it(cloud):
    org = build_org(cloud, plan="team")
    seed_workspace(cloud, org.org_id, org.candidate.member_id)
    ops = operator(cloud)
    browser = Browser(cloud)
    status, data = browser.call(
        "POST",
        "/api/platform/auth/login",
        {"email": "ops@aira.test", "password": PASSWORD},
    )
    assert status == 200
    from aira_cloud import crypto

    assert browser.call("POST", "/api/platform/auth/mfa/begin", {})[0] == 200
    pending = cloud.secrets.get("_platform", f"mfa-pending:{ops.operator_id}")
    assert (
        browser.call(
            "POST",
            "/api/platform/auth/mfa/enable",
            {"code": crypto.totp_code(pending, cloud.clock.now())},
        )[0]
        == 200
    )
    status, queued = browser.call(
        "POST", f"/api/platform/tenants/{org.org_id}/rotate-keys", {}
    )
    assert status == 202 and queued["queued"] is True
    assert Worker(cloud, threads=1, run_tick=False).drain() == 1
    assert cloud.secrets.current_version(org.org_id) == 2
    assert "tenant.rotate_keys_queued" in [
        r["action"] for r in cloud.platform_audit.list(org_id=org.org_id)
    ]


# ----------------------------------------------------------------------------- legal hold


def test_legal_hold_is_an_enterprise_feature(cloud):
    org = build_org(cloud, plan="team")
    with pytest.raises(PlanRequired) as raised:
        cloud.tenancy.update_policy(org.owner, {"legal_hold": True})
    assert raised.value.extra["required_plan"] == "enterprise"
    make_enterprise(cloud, org)
    assert (
        cloud.tenancy.update_policy(org.owner, {"legal_hold": True})["legal_hold"]
        is True
    )


def test_legal_hold_stops_every_way_data_could_be_deleted(cloud):
    org = build_org(cloud, plan="team")
    ops = make_enterprise(cloud, org)
    candidate_id = org.candidate.member_id
    seed_workspace(cloud, org.org_id, candidate_id, job_title="Cloud Architect")
    cloud.tenancy.update_policy(org.owner, {"legal_hold": True})
    # a candidate workspace cannot be deleted
    with pytest.raises(Conflict) as raised:
        cloud.lifecycle.delete_candidate_workspace(org.owner, candidate_id)
    assert raised.value.code == "legal_hold"
    # a departing candidate's data is kept, whatever the data policy says
    result = cloud.tenancy.remove_member(org.owner, candidate_id)
    assert (
        result["data_policy"] == "organization_keeps"
        and cloud.workspaces.data_path(org.org_id, candidate_id).exists()
    )
    assert cloud.workspaces.row(org.org_id, candidate_id)["status"] == "archived"
    # retention does not purge documents or audit history
    audit_before = cloud.audit.count(org.org_id)
    cloud.clock.advance(days=4000)
    cloud.lifecycle._housekeeping()
    assert cloud.audit.count(org.org_id) >= audit_before
    # a scheduled deletion (closure) waits while the hold is on
    cloud.lifecycle.request_closure(org.owner, PASSWORD)
    cloud.clock.advance(days=cloud.config.deletion_grace_days + 1)
    cloud.billing.process_due()
    assert cloud.billing.subscription(org.org_id)["status"] == "cancelled", (
        "not deleted while on hold"
    )
    assert cloud.workspaces.org_dir(org.org_id).exists()
    with pytest.raises(Conflict):
        cloud.lifecycle.delete_org(org.org_id, reason="try", trigger_type="scheduler")
    # lifting the hold lets the pending deletion go ahead
    with cloud.db.write() as connection:
        policy = cloud.tenancy.org(org.org_id)["policy"]
        policy["legal_hold"] = False
        import json

        connection.execute(
            "UPDATE organizations SET policy = ? WHERE org_id = ?",
            (json.dumps(policy), org.org_id),
        )
    cloud.tenancy.invalidate_policy(org.org_id)
    cloud.billing.process_due()
    assert cloud.billing.subscription(org.org_id)["status"] == "deleted"
    assert ops is not None


# ----------------------------------------------------------------------------- SIEM export


def test_siem_stream_is_enterprise_only_cursor_based_and_can_speak_cef(cloud):
    org = build_org(cloud, plan="team")
    owner = sign_in(cloud, org.owner)
    status, body = owner.call("GET", "/api/org/audit/stream")
    assert (
        status == 402
        and body["feature"] == "siem_export"
        and body["required_plan"] == "enterprise"
    )
    make_enterprise(cloud, org)
    owner = sign_in(cloud, org.owner)
    status, raw = owner.call("GET", "/api/org/audit/stream?limit=5", raw=True)
    assert status == 200
    lines = raw.decode().splitlines()
    assert len(lines) == 5 and owner.header("X-Aira-Events") == "5"
    cursor = owner.header("X-Aira-Next-Cursor")
    import json

    assert json.loads(lines[-1])["seq"] == int(cursor)
    status, rest = owner.call(
        "GET", f"/api/org/audit/stream?after={cursor}&limit=5000", raw=True
    )
    first_ids = {json.loads(line)["audit_id"] for line in lines}
    assert all(
        json.loads(line)["audit_id"] not in first_ids
        for line in rest.decode().splitlines()
    ), "the cursor does not repeat events"
    status, cef = owner.call(
        "GET", "/api/org/audit/stream?format=cef&limit=3", raw=True
    )
    first = cef.decode().splitlines()[0]
    assert (
        first.startswith("CEF:0|Aira|Career Copilot|1.0|")
        and " suser=" in first
        and " externalId=" in first
    )
    for role in (org.coach, org.candidate, org.billing):
        assert sign_in(cloud, role).call("GET", "/api/org/audit/stream")[0] == 403
    assert (
        sign_in(cloud, org.auditor).call(
            "GET", "/api/org/audit/stream?limit=1", raw=True
        )[0]
        == 200
    )


# ----------------------------------------------------------------------------- HSTS, priority, browser cap


def test_hsts_is_sent_only_over_https(tmp_path):
    HOOKS.reset()
    plain = make_app(tmp_path / "plain")
    browser = Browser(plain)
    browser.call("GET", "/api/health")
    assert not browser.header("Strict-Transport-Security")
    plain.close()
    HOOKS.reset()
    secure = make_app(tmp_path / "secure", base_url="https://aira.example.test")
    browser = Browser(secure)
    browser.call("GET", "/api/health", headers={"Host": "aira.example.test"})
    assert "max-age=31536000" in browser.header("Strict-Transport-Security")
    status, _ = browser.call(
        "POST",
        "/api/auth/login",
        {"email": "x@y.test", "password": PASSWORD},
        headers={"Host": "aira.example.test", "Origin": "https://aira.example.test"},
    )
    assert any(
        "Secure" in value
        for key, value in browser.last_headers
        if key.lower() == "set-cookie"
    ) or status in (401, 403)
    secure.close()


def test_enterprise_work_is_queued_ahead(cloud):
    team = build_org(cloud, "Team Org", plan="team", slug_email="t.test")
    big = build_org(cloud, "Big Org", plan="team", slug_email="b.test")
    make_enterprise(cloud, big)
    for org in (team, big):
        cloud.tenancy.record_consent(org.candidate, CONSENTS)
        seed_workspace(
            cloud, org.org_id, org.candidate.member_id, job_title="Cloud Architect"
        )
        with cloud.workspaces.bind(org.org_id, org.candidate.member_id, kind="system"):
            local = local_module()
            from aira import autopilot

            with local._connect() as connection:
                plan = autopilot.create_plan(
                    connection,
                    {
                        "name": "P",
                        "positions": ["Architect"],
                        "sources": ["aira_jobs"],
                        "boards": [],
                        "mode": "review",
                        "min_score": 50,
                        "max_per_run": 1,
                        "interval_minutes": 240,
                    },
                )
                connection.execute(
                    "UPDATE autopilot_plans SET next_run_at = '2000-01-01T00:00:00+00:00' WHERE plan_id = ?",
                    (plan["plan_id"],),
                )
    cloud.scheduler.enqueue_due()
    priorities = {
        org.org_id: cloud.queue.active_for(org.org_id, org.candidate.member_id)[
            "priority"
        ]
        for org in (team, big)
    }
    assert priorities[big.org_id] < priorities[team.org_id]
    first = cloud.queue.lease("w1", per_org_limit=1)
    assert first["org_id"] == big.org_id


def test_the_browser_time_cap_bounds_each_run(cloud):
    assert HOOKS.browser_time_cap() == cloud.config.browser_time_cap_seconds == 600

    HOOKS.browser_time_cap = lambda: 45
    local = local_module()
    assert (
        min(
            150,
            (
                local.HOSTING.browser_time_cap()
                if local.HOSTING.browser_time_cap
                else None
            )
            or 150,
        )
        == 45
    )


def test_sign_in_page_branding_is_public_but_only_for_plans_that_include_it(cloud):
    team = build_org(cloud, "Northwind Outplacement", plan="team", slug_email="nw.test")
    starter = build_org(
        cloud, "Contoso Staffing", plan="starter", seats=10, slug_email="ct.test"
    )
    cloud.tenancy.set_branding(team.owner, logo_url="https://cdn.example.test/logo.png")
    anonymous = Browser(cloud)
    status, body = anonymous.call("GET", "/api/orgs/northwind-outplacement/branding")
    assert status == 200 and body == {
        "branded": True,
        "name": "Northwind Outplacement",
        "logo_url": "https://cdn.example.test/logo.png",
    }
    assert anonymous.call("GET", "/api/orgs/contoso-staffing/branding")[1] == {
        "branded": False
    }, "a plan without branding reveals nothing"
    assert anonymous.call("GET", "/api/orgs/no-such-org/branding")[1] == {
        "branded": False
    }
    unset = build_org(cloud, "Never Set Org", plan="team", slug_email="ns.test")
    assert anonymous.call("GET", "/api/orgs/never-set-org/branding")[1] == {
        "branded": False
    }, "a Team organization that never chose branding reveals nothing"
    assert starter is not None and unset is not None


# ----------------------------------------------------------------------------- findings from the security review


def test_a_forged_forwarded_for_prefix_does_not_change_the_client_address(cloud):
    from aira_cloud.gateway import client_address

    config = cloud.config
    config.trust_proxy = False
    assert client_address("10.0.0.5", "6.6.6.6", config) == "10.0.0.5"
    config.trust_proxy = True
    config.trusted_proxy_hops = 1
    # the attacker sends "1.1.1.1"; the one trusted proxy appends the real peer it saw
    assert client_address("10.0.0.5", "1.1.1.1, 203.0.113.9", config) == "203.0.113.9"
    assert client_address("10.0.0.5", "203.0.113.9", config) == "203.0.113.9"
    assert client_address("10.0.0.5", "", config) == "10.0.0.5"
    config.trusted_proxy_hops = 2  # a CDN and a load balancer
    assert (
        client_address("10.0.0.5", "1.1.1.1, 203.0.113.9, 172.16.0.2", config)
        == "203.0.113.9"
    )


def test_organization_supplied_urls_must_be_public_https(monkeypatch):
    from aira_cloud.errors import ValidationFailed
    from aira_cloud.sso import _check_public_url

    monkeypatch.delenv("AIRA_CLOUD_ALLOW_LOCAL_IDP", raising=False)
    monkeypatch.delenv("AIRA_CLOUD_ALLOW_LOCAL_URLS", raising=False)
    for url in (
        "http://127.0.0.1:9000/v1",
        "https://127.0.0.1/v1",
        "https://localhost/v1",
        "http://169.254.169.254/latest/meta-data",
        "https://10.1.2.3/v1",
        "file:///etc/passwd",
        "ftp://example.com/x",
    ):
        with pytest.raises(ValidationFailed):
            _check_public_url(url, what="model endpoint")
    monkeypatch.setenv("AIRA_CLOUD_ALLOW_LOCAL_URLS", "1")
    _check_public_url("http://127.0.0.1:9000/v1", what="model endpoint")


def test_scim_cannot_reroute_an_owner_or_admin_sign_in_address(cloud):
    from aira_cloud.errors import Forbidden

    org = build_org(cloud)
    body = {
        "Operations": [
            {"op": "replace", "path": "userName", "value": "attacker@evil.test"}
        ]
    }
    for victim in (org.owner, org.admin):
        with pytest.raises(Forbidden):
            cloud.sso.scim_patch_user(org.org_id, victim.member_id, body)
        with pytest.raises(Forbidden):
            cloud.sso.scim_replace_user(
                org.org_id, victim.member_id, {"userName": "attacker@evil.test"}
            )
    with cloud.db.read() as connection:
        emails = {
            r[0]
            for r in connection.execute(
                "SELECT email FROM members WHERE org_id = ?", (org.org_id,)
            )
        }
    assert "attacker@evil.test" not in emails
    # ordinary members can still be renamed by the identity provider
    renamed = cloud.sso.scim_patch_user(org.org_id, org.coach.member_id, body)
    assert renamed["userName"] == "attacker@evil.test"


def test_cef_lines_cannot_be_split_by_a_newline_in_a_field():
    from aira_cloud.routes.org import _cef

    line = _cef(
        {
            "target_type": "member",
            "target_id": "x\nCEF:0|Forged|Event|1|fake|fake|10|",
            "at": 1,
            "actor_label": "a\r\nb",
            "actor_id": "1",
            "candidate_id": "",
            "actor_role": "owner",
            "seq": 7,
            "action": "member.invite\n",
        }
    )
    assert "\n" not in line and "\r" not in line


def test_staff_cannot_list_or_download_a_candidates_complete_export(cloud):
    import io
    import zipfile

    from cloud_support import build_org, local_module, seed_workspace

    from aira_cloud.errors import NotFound

    org = build_org(cloud)
    candidate = org.candidate.member_id
    seed_workspace(cloud, org.org_id, candidate)
    with cloud.workspaces.bind(org.org_id, candidate, kind="system", source="test"):
        with local_module()._connect() as connection:
            connection.execute(
                "INSERT OR REPLACE INTO settings (key, value) VALUES ('application_work_authorization', 'SECRET-VISA-CLASS')"
            )
    mine = cloud.lifecycle.export_candidate(org.candidate, candidate)
    _row, blob = cloud.lifecycle.export_for(org.candidate, mine["export_id"])
    assert b"SECRET-VISA-CLASS" in zipfile.ZipFile(io.BytesIO(blob)).read(
        "workspace.json"
    )
    for staff in (org.owner, org.admin, org.auditor):
        assert mine["export_id"] not in [
            e["export_id"] for e in cloud.lifecycle.list_exports(staff)
        ], staff.role
        with pytest.raises(NotFound):
            cloud.lifecycle.export_for(staff, mine["export_id"])
    # what staff export themselves is masked, so they may download it
    theirs = cloud.lifecycle.export_candidate(org.admin, candidate)
    _row, blob = cloud.lifecycle.export_for(org.auditor, theirs["export_id"])
    assert b"SECRET-VISA-CLASS" not in zipfile.ZipFile(io.BytesIO(blob)).read(
        "workspace.json"
    )


def test_one_organizations_rate_limit_never_changes_another_threads_limit():
    import threading

    from aira.security import RateLimiter

    limiter = RateLimiter(limit=100, window_seconds=60)
    for _ in range(5):
        limiter.hit("org:a")
    assert limiter.check("org:a", limit=5)[0] is False  # a throttled tenant
    assert limiter.check("org:a")[0] is True  # everyone else is judged by the default
    assert limiter.limit == 100
    results: list[bool] = []

    def other():
        results.append(limiter.check("org:b", limit=10)[0])

    threads = [threading.Thread(target=other) for _ in range(8)]
    [t.start() for t in threads]
    [t.join() for t in threads]
    assert all(results) and limiter.limit == 100


def test_careers_reports_and_alert_runs_meter_the_credits_they_used():
    from aira_cloud.route_hooks import _search_cost
    from aira_cloud.webio import HttpResponse

    class Ctx:
        def __init__(self, name: str) -> None:
            self.name = name

    def reply(payload) -> HttpResponse:
        return HttpResponse(200, [], __import__("json").dumps(payload).encode())

    report = Ctx("airaapi__careers_routes__report_run")
    assert _search_cost(report, reply({"report": {"credits": 7}})) == 7
    assert _search_cost(report, reply({"credits": 0})) == 0  # all cache hits
    assert (
        _search_cost(Ctx("airaapi__careers_routes__alert_run"), reply({"credits": 3}))
        == 3
    )
    assert _search_cost(report, reply({"unexpected": True})) == 1
    assert _search_cost(Ctx("api_jobs_search"), reply({"credits": 9})) == 1
