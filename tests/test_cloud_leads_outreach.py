"""Aira Leads outreach in the hosted shell: who may send, plan gating, limits, the kill switch, safety and data handling.

The mail server is a stand-in (``tests/smtp_stub.py``) on loopback. A hosted build refuses internal mail servers,
so tests that need a message to arrive use the ``open_mail_servers`` fixture, the one test-only seam: it replaces
``aira.leads.mail.check_smtp_host`` for the duration of a test. Nothing in the product reads an environment
variable to do the same.
"""

# ruff: noqa: F811
from __future__ import annotations

import io
import json
import logging
import zipfile
from typing import Any

import pytest
from cloud_support import build_org, local_module, seed_workspace, sign_in
from smtp_stub import SmtpStub
from test_cloud_leads import bridge, configure, override, serpapi, team, used  # noqa: F401

from aira.leads import mail
from aira_cloud import plans
from aira_cloud.policy import POLICY

PASSWORD = "s3cret-Sm7p-passw0rd"
SENDER = {
    "from_name": "Hosted Rep",
    "from_email": "rep@seller.test",
    "smtp_host": "127.0.0.1",
    "smtp_security": "none",
    "postal_address": "12 Example Street, Austin, TX 78701, USA",
    "offer": "I build websites and run ads for local businesses",
    "services": ["website", "ads"],
}
CSV = (
    "Name,Company,Email,Website,Location\n"
    'Biz A,Biz A,a@biza.test,biza.test,"Austin, TX"\n'
    'Biz B,Biz B,b@bizb.test,bizb.test,"Austin, TX"\n'
    'Biz C,Biz C,c@bizc.test,bizc.test,"Austin, TX"\n'
)
SEND = "/api/leads/outbox/send"


@pytest.fixture(autouse=True)
def _no_real_model(bridge):
    """Drafts ask the model gateway; tests use the fake gateway (template drafts), never a real Ollama."""


@pytest.fixture
def smtp():
    server = SmtpStub().start()
    yield server
    server.stop()


@pytest.fixture
def open_mail_servers(monkeypatch):
    """Test-only seam: let the hosted build talk to the loopback stand-in mail server."""
    monkeypatch.setattr(mail, "check_smtp_host", lambda host, port: None)


@pytest.fixture
def no_pause(monkeypatch):
    """Test-only seam: no real wait between messages (the pause itself is covered by the backend tests)."""
    monkeypatch.setitem(
        mail.send_approved.__kwdefaults__, "sleep", lambda _seconds: None
    )


def settings_for(smtp: SmtpStub, **extra: Any) -> dict[str, Any]:
    return {**SENDER, "smtp_port": smtp.port, **extra}


def prepare(cloud, org, smtp, *, count=3, password=PASSWORD):
    """A candidate with sender settings saved and ``count`` approved messages."""
    candidate = sign_in(cloud, org.candidate)
    status, body = candidate.call(
        "POST",
        "/api/leads/outreach/settings",
        {**settings_for(smtp), "smtp_password": password},
    )
    assert status == 200, body
    assert candidate.call("POST", "/api/leads/items/import-csv", {"csv": CSV})[0] == 201
    ids = [
        lead["id"] for lead in candidate.call("GET", "/api/leads/items")[1]["leads"]
    ][:count]
    status, drafts = candidate.call(
        "POST", "/api/leads/outbox/drafts", {"lead_ids": ids, "services": ["website"]}
    )
    assert status == 201 and drafts["created"] == count, drafts
    message_ids = [m["id"] for m in drafts["messages"]]
    status, approved = candidate.call(
        "POST", "/api/leads/outbox/approve", {"ids": message_ids}
    )
    assert status == 200 and approved["approved"] == count
    return candidate, ids, message_ids


@pytest.fixture
def outreach(cloud, team, smtp, open_mail_servers, no_pause):
    candidate, lead_ids, message_ids = prepare(cloud, team, smtp)
    return team, candidate, lead_ids, message_ids, smtp


# ---------------------------------------------------------------------------
# policy, plans and roles
# ---------------------------------------------------------------------------

OUTREACH = {
    "services_get": ("leads.read", "leads"),
    "outreach_settings_get": ("leads.outreach", "leads"),
    "outreach_settings_save": ("leads.outreach", "leads_outreach"),
    "outreach_test": ("leads.outreach", "leads_outreach"),
    "outbox_list": ("leads.read", "leads"),
    "outbox_drafts": ("leads.write", "leads"),
    "outbox_update": ("leads.write", "leads"),
    "outbox_delete": ("leads.write", "leads"),
    "outbox_approve": ("leads.outreach", "leads_outreach"),
    "outbox_reject": ("leads.write", "leads"),
    "outbox_send": ("leads.outreach", "leads_outreach"),
    "suppressions_list": ("leads.read", "leads"),
    "suppressions_add": ("leads.write", "leads"),
    "suppressions_remove": ("leads.outreach", "leads"),
    "item_dial": ("leads.read", "leads"),
    "item_call_script": ("leads.write", "leads"),
    "item_calls": ("leads.read", "leads"),
    "item_call_log": ("leads.write", "leads"),
    "follow_ups": ("leads.read", "leads"),
}


def test_every_outreach_route_has_the_policy_it_should():
    local = local_module()
    names = {name for _m, _p, name, _a in local.ROUTES if "leads_routes" in name}
    for short, (permission, feature) in OUTREACH.items():
        name = f"airaapi__leads_routes__{short}"
        assert name in names and name in POLICY, short
        rule = POLICY[name]
        assert (rule.scope, rule.permission, rule.feature) == (
            "candidate",
            permission,
            feature,
        ), short
    # sending, approving and the sender identity are the candidate's alone
    from aira_cloud.rbac import CANDIDATE_ONLY

    assert "leads.outreach" in CANDIDATE_ONLY
    for short in (
        "outreach_settings_save",
        "outreach_test",
        "outbox_approve",
        "outbox_send",
    ):
        assert POLICY[f"airaapi__leads_routes__{short}"].write


def test_the_plans_carry_the_outreach_feature_and_limits_and_old_versions_get_them():
    shipped = plans.DEFAULT_PLANS
    for plan_id, (monthly, daily) in {
        "trial": (0, 0),
        "starter": (0, 0),
        "team": (1500, 50),
        "enterprise": (25000, 100),
    }.items():
        entitlements = shipped[plan_id]["entitlements"]
        assert (
            entitlements["lead_emails_per_month"] == monthly
            and entitlements["lead_email_daily_cap"] == daily
        )
        assert ("leads_outreach" in entitlements["features"]) == (
            plan_id in {"team", "enterprise"}
        )
    old = {"features": ["sources.aira_jobs", "leads"]}
    merged = plans.with_later_limits("team", old)
    assert (
        "leads_outreach" in merged["features"]
        and merged["lead_emails_per_month"] == 1500
    )
    starter = plans.with_later_limits("starter", {"features": ["leads"]})
    assert (
        "leads_outreach" not in starter["features"]
        and starter["lead_emails_per_month"] == 0
    )


def test_a_starter_plan_is_told_to_upgrade_to_team_for_every_send_step(cloud):
    org = build_org(
        cloud, "Starter Two", plan="starter", seats=5, slug_email="startertwo.test"
    )
    candidate = sign_in(cloud, org.candidate)
    for method, path, body in (
        ("POST", "/api/leads/outreach/settings", {"from_name": "x"}),
        ("POST", "/api/leads/outreach/test", {}),
        ("POST", "/api/leads/outbox/approve", {"ids": []}),
        ("POST", SEND, {}),
    ):
        status, response = candidate.call(method, path, body)
        assert (
            status == 402
            and response["code"] == "plan_required"
            and response["required_plan"] == "team"
        ), (path, status, response)
    assert candidate.call("GET", "/api/leads/outbox")[0] == 200
    # a draft needs no mail server: writing one is part of Aira Leads on every paid plan
    assert candidate.call("POST", "/api/leads/items/import-csv", {"csv": CSV})[0] == 201
    ids = [lead["id"] for lead in candidate.call("GET", "/api/leads/items")[1]["leads"]]
    status, drafts = candidate.call(
        "POST", "/api/leads/outbox/drafts", {"lead_ids": ids[:1]}
    )
    assert status == 201 and drafts["created"] == 1


def test_a_trial_organization_has_no_leads_at_all(cloud):
    from cloud_support import add_member, signup

    owner = signup(cloud, "Trial Out", "owner@trialout.test")["owner"]
    candidate = sign_in(
        cloud, add_member(cloud, owner, "cand@trialout.test", "candidate")
    )
    status, body = candidate.call("GET", "/api/leads/outbox")
    assert status == 402 and body["required_plan"] == "starter"


def test_a_coach_drafts_and_edits_but_only_the_candidate_approves_and_sends(
    cloud, team, smtp, open_mail_servers, no_pause
):
    prepare(cloud, team, smtp, count=2)
    candidate = sign_in(cloud, team.candidate)
    cid = team.candidate.member_id
    coach = sign_in(cloud, team.coach)
    cloud.tenancy.assign_coach(team.owner, team.coach.member_id, cid, "act")
    messages = coach.call("GET", "/api/leads/outbox", as_candidate=cid)[1]["messages"]
    assert len(messages) == 2, "a coach with act scope sees the outbox"
    mid = messages[0]["id"]
    status, edited = coach.call(
        "PATCH",
        f"/api/leads/outbox/{mid}",
        {"body": "A coach's edit."},
        as_candidate=cid,
    )
    assert status == 200 and edited["message"]["status"] == "draft", (
        "an edit withdraws the candidate's approval"
    )
    assert (
        coach.call(
            "POST", "/api/leads/outbox/reject", {"ids": [mid]}, as_candidate=cid
        )[0]
        == 200
    )
    lead = candidate.call("GET", "/api/leads/items")[1]["leads"][0]
    assert (
        coach.call(
            "POST", "/api/leads/suppressions", {"value": "nope.test"}, as_candidate=cid
        )[0]
        == 201
    )
    assert (
        coach.call(
            "POST", f"/api/leads/items/{lead['id']}/call-script", {}, as_candidate=cid
        )[0]
        == 200
    )
    assert (
        coach.call(
            "POST",
            f"/api/leads/items/{lead['id']}/calls",
            {"outcome": "no_answer"},
            as_candidate=cid,
        )[0]
        == 201
    )
    for method, path, body in (
        ("POST", "/api/leads/outreach/settings", {"from_name": "Coach"}),
        ("POST", "/api/leads/outreach/test", {}),
        ("POST", "/api/leads/outbox/approve", {"ids": [messages[1]["id"]]}),
        ("POST", SEND, {}),
        ("POST", "/api/leads/suppressions/remove", {"key": "domain:nope.test"}),
        ("GET", "/api/leads/outreach/settings", None),
    ):
        status, response = coach.call(method, path, body, as_candidate=cid)
        assert status == 403, (path, status, response)
    assert smtp.messages == []
    for who in (team.owner, team.admin, team.auditor, team.billing):
        browser = sign_in(cloud, who)
        for method, path, body in (
            ("GET", "/api/leads/outbox", None),
            ("POST", "/api/leads/outbox/drafts", {"lead_ids": [lead["id"]]}),
            ("POST", SEND, {}),
            ("GET", "/api/leads/suppressions", None),
        ):
            assert browser.call(method, path, body, as_candidate=cid)[0] == 403, (
                who.role,
                path,
            )


def test_a_view_scope_coach_sees_no_outreach_at_all(
    cloud, team, smtp, open_mail_servers, no_pause
):
    prepare(cloud, team, smtp, count=1)
    cid = team.candidate.member_id
    coach = sign_in(cloud, team.coach)
    cloud.tenancy.assign_coach(team.owner, team.coach.member_id, cid, "view")
    for path in (
        "/api/leads/outbox",
        "/api/leads/follow-ups",
        "/api/leads/suppressions",
    ):
        assert coach.call("GET", path, as_candidate=cid)[0] == 403, path


# ---------------------------------------------------------------------------
# sending, the daily cap and the monthly allowance
# ---------------------------------------------------------------------------


def test_the_candidate_sends_and_the_month_counts_messages_actually_sent(
    cloud, outreach
):
    org, candidate, _leads, message_ids, smtp = outreach
    status, body = candidate.call("POST", SEND, {})
    assert status == 200 and body["sent"] == 3 and body["failed"] == 0, body
    assert len(smtp.messages) == 3
    assert used(cloud, org, "lead_emails") == 3, (
        "three sent, three counted; the rest of the batch was given back"
    )
    assert body["remaining_today"] == 17, (
        "the sender's own daily cap (20) less what was sent"
    )
    plan_usage = cloud.dashboard.organization(org.owner)["plan_usage"]["leads"]
    assert (
        plan_usage["emails"] == 3
        and plan_usage["emails_limit"] == 1500
        and plan_usage["email_daily_cap"] == 50
    )
    assert candidate.call("POST", SEND, {})[1]["sent"] == 0
    assert used(cloud, org, "lead_emails") == 3, "nothing to send, nothing counted"


def test_a_failed_send_is_not_counted(cloud, team, smtp, open_mail_servers, no_pause):
    refusing = SmtpStub(refuse={"a@biza.test", "b@bizb.test", "c@bizc.test"}).start()
    try:
        candidate, *_ = prepare(cloud, team, refusing, count=2)
        status, body = candidate.call("POST", SEND, {})
        assert status == 200 and body["sent"] == 0 and body["failed"] == 2
        assert used(cloud, team, "lead_emails") == 0
    finally:
        refusing.stop()


def test_the_monthly_allowance_is_a_hard_stop_that_resets_next_month(
    cloud, team, smtp, open_mail_servers, no_pause
):
    override(cloud, team, {"lead_emails_per_month": 2})
    candidate, *_ = prepare(cloud, team, smtp, count=3)
    status, body = candidate.call("POST", SEND, {})
    assert status == 200 and body["sent"] == 2, (
        "the batch is cut to what is left of the month"
    )
    assert used(cloud, team, "lead_emails") == 2 and len(smtp.messages) == 2
    status, body = candidate.call("POST", SEND, {})
    assert (
        status == 402
        and body["code"] == "plan_required"
        and body["limit"] == "lead_emails_per_month"
        and body["limit_value"] == 2
    )
    assert len(smtp.messages) == 2
    cloud.clock.advance(days=35)
    candidate = sign_in(cloud, team.candidate)  # the session expired with the month
    status, body = candidate.call("POST", SEND, {})
    assert status == 200 and body["sent"] == 1 and len(smtp.messages) == 3


def test_overage_never_buys_extra_emails(
    cloud, team, smtp, open_mail_servers, no_pause
):
    override(cloud, team, {"lead_emails_per_month": 1})
    cloud.billing.set_overage(team.owner, True)
    candidate, *_ = prepare(cloud, team, smtp, count=2)
    assert candidate.call("POST", SEND, {})[1]["sent"] == 1
    assert candidate.call("POST", SEND, {})[0] == 402, (
        "there is no price for extra emails"
    )


def test_the_daily_cap_is_clamped_by_the_plan_on_the_server(
    cloud, team, smtp, open_mail_servers
):
    candidate = sign_in(cloud, team.candidate)
    status, body = candidate.call(
        "POST",
        "/api/leads/outreach/settings",
        {**settings_for(smtp), "daily_cap": 100, "delay_seconds": 0},
    )
    assert status == 200 and body["settings"]["daily_cap"] == 50, "Team allows 50 a day"
    assert body["settings"]["delay_seconds"] == 2
    assert (
        candidate.call("POST", "/api/leads/outreach/settings", {"delay_seconds": 600})[
            1
        ]["settings"]["delay_seconds"]
        == 10
    )
    assert (
        candidate.call("POST", "/api/leads/outreach/settings", {"daily_cap": 10})[1][
            "settings"
        ]["daily_cap"]
        == 10
    )
    override(cloud, team, {"lead_email_daily_cap": 3})
    assert (
        candidate.call("POST", "/api/leads/outreach/settings", {"daily_cap": 40})[1][
            "settings"
        ]["daily_cap"]
        == 3
    )
    assert (
        candidate.call("POST", "/api/leads/outreach/settings", {"daily_cap": 5000})[0]
        == 200
    )  # clamped before the handler's own check


def test_a_setting_stored_before_a_downgrade_is_clamped_again_at_send_time(
    cloud, team, smtp, open_mail_servers, no_pause
):
    candidate, *_ = prepare(cloud, team, smtp, count=3)
    local = local_module()
    with cloud.workspaces.bind(
        team.org_id, team.candidate.member_id, kind="system", source="test"
    ):
        with local._connect() as connection:
            mail.save_settings(connection, {"daily_cap": 100, "delay_seconds": 300})
    override(cloud, team, {"lead_email_daily_cap": 2})
    status, body = candidate.call("POST", SEND, {})
    assert status == 200 and body["sent"] == 2 and body["daily_cap"] == 2, (
        "the stored 100 a day became the plan's 2"
    )
    settings = candidate.call("GET", "/api/leads/outreach/settings")[1]["settings"]
    assert settings["daily_cap"] == 2 and settings["delay_seconds"] == 10


def test_a_hosted_send_request_carries_at_most_five_messages(
    cloud, team, smtp, open_mail_servers, no_pause
):
    candidate, *_ = prepare(cloud, team, smtp, count=3)
    for index in range(3, 8):
        candidate.call(
            "POST",
            "/api/leads/items",
            {
                "leads": [
                    {
                        "kind": "company",
                        "name": f"Extra {index}",
                        "company": f"Extra {index}",
                        "email": f"x{index}@extra{index}.test",
                        "website": f"https://extra{index}.test",
                        "location": "Austin, TX",
                    }
                ]
            },
        )
    ids = [
        lead["id"]
        for lead in candidate.call("GET", "/api/leads/items?limit=50")[1]["leads"]
        if lead["name"].startswith("Extra")
    ]
    drafts = candidate.call("POST", "/api/leads/outbox/drafts", {"lead_ids": ids})[1]
    candidate.call(
        "POST",
        "/api/leads/outbox/approve",
        {"ids": [m["id"] for m in drafts["messages"]]},
    )
    first = candidate.call("POST", SEND, {})[1]
    assert first["sent"] == 5, "eight approved, but one request sends five"
    second = candidate.call("POST", "/api/leads/outbox/send?limit=10", {})[1]
    assert second["sent"] == 3, "asking for ten does not raise the hosted batch of five"
    assert len(smtp.messages) == 8 and used(cloud, team, "lead_emails") == 8


# ---------------------------------------------------------------------------
# the kill switch and the organization gates
# ---------------------------------------------------------------------------


def operator_for(cloud):
    cloud.identity.create_operator(
        "ops@aira.test", "operator-password-123", role="platform_admin"
    )
    token = cloud.identity.operator_login("ops@aira.test", "operator-password-123")[
        "token"
    ]
    return cloud.identity.principal_for_token(token, kind="operator")


def test_an_operator_can_stop_all_outbound_lead_email_at_once(cloud, outreach):
    org, candidate, _leads, message_ids, smtp = outreach
    operator = operator_for(cloud)
    cloud.platform.block_source(operator, "leads_outreach", "abuse report 4471")
    status, body = candidate.call("POST", SEND, {})
    assert (
        status == 403
        and body["code"] == "outreach_blocked"
        and "abuse report 4471" in body["error"]
    )
    assert candidate.call("POST", "/api/leads/outreach/test", {})[0] == 403
    assert smtp.messages == [] and used(cloud, org, "lead_emails") == 0
    assert candidate.call("GET", "/api/leads/outbox")[0] == 200, (
        "drafting and reading carry on"
    )
    cloud.platform.unblock_source(operator, "leads_outreach")
    assert candidate.call("POST", SEND, {})[1]["sent"] == 3


@pytest.mark.parametrize(
    "state", ["org_paused", "past_due", "candidate_paused", "suspended"]
)
def test_an_organization_that_may_not_automate_cannot_send(cloud, outreach, state):
    org, candidate, _leads, _ids, smtp = outreach
    if state == "org_paused":
        cloud.tenancy.pause_org(org.owner, "audit week")
    elif state == "past_due":
        from cloud_support import webhook

        webhook(cloud, "invoice.payment_failed", org_id=org.org_id)
    elif state == "candidate_paused":
        cloud.tenancy.pause_candidate(org.owner, org.candidate.member_id, "on leave")
    else:
        operator = operator_for(cloud)
        cloud.platform.suspend(operator, org.org_id, "test")
    status, body = candidate.call("POST", SEND, {})
    assert status in {401, 402, 403, 409}, (state, status, body)
    assert smtp.messages == [] and used(cloud, org, "lead_emails") == 0


def test_a_read_only_subscription_cannot_send(cloud, outreach):
    org, candidate, *_rest = outreach
    smtp = _rest[-1]
    with cloud.db.write() as connection:
        connection.execute(
            "UPDATE subscriptions SET status = 'read_only' WHERE org_id = ?",
            (org.org_id,),
        )
    cloud.entitlements.invalidate(org.org_id)
    status, body = candidate.call("POST", SEND, {})
    assert status != 200 and smtp.messages == [], body


# ---------------------------------------------------------------------------
# hosted safety: mail servers, passwords, audit, logs
# ---------------------------------------------------------------------------


def test_a_hosted_build_refuses_internal_mail_servers_whatever_the_environment_says(
    cloud, team, smtp, monkeypatch
):
    assert mail._strict() is True
    candidate = sign_in(cloud, team.candidate)
    status, body = candidate.call(
        "POST", "/api/leads/outreach/settings", settings_for(smtp)
    )
    assert status == 400 and "cannot be used here" in body["error"], (
        "refused when it is saved, not at the first send"
    )
    monkeypatch.setenv(
        "AIRA_LEADS_ALLOW_LOCAL", "1"
    )  # the development switch must not relax a hosted build
    monkeypatch.setenv("AIRA_CLOUD_ALLOW_LOCAL_URLS", "1")
    status, body = candidate.call(
        "POST", "/api/leads/outreach/settings", settings_for(smtp)
    )
    assert status == 400 and smtp.messages == []
    with pytest.raises(mail.SendError, match="cannot be used here"):
        mail.check_smtp_host("127.0.0.1", smtp.port)
    for private in ("10.1.2.3", "192.168.0.9", "169.254.169.254", "localhost"):
        with pytest.raises(mail.SendError):
            mail.check_smtp_host(private, 25)


def test_a_message_never_leaves_for_an_internal_server_even_if_the_settings_were_stored_before(
    cloud, team, smtp, monkeypatch, no_pause
):
    monkeypatch.setattr(mail, "check_smtp_host", lambda host, port: None)
    candidate, *_ = prepare(cloud, team, smtp, count=1)
    monkeypatch.undo()  # the seam is gone: the real check runs at send time
    monkeypatch.setitem(mail.send_approved.__kwdefaults__, "sleep", lambda _s: None)
    status, body = candidate.call("POST", SEND, {})
    assert status == 200 and body["sent"] == 0 and body["failed"] == 1
    assert "cannot be used here" in body["details"][0]["error"] and smtp.messages == []
    assert used(cloud, team, "lead_emails") == 0


def _file_bytes(root) -> bytes:
    blob = b""
    for path in root.rglob("*"):
        if path.is_file() and (".sqlite3" in path.name or path.suffix == ".bin"):
            blob += path.read_bytes()
    return blob


def _zip_bytes(data: bytes) -> bytes:
    blob = b""
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        for name in archive.namelist():
            inner = archive.read(name)
            blob += inner
            if (
                inner[:2] == b"PK"
            ):  # an archive inside the archive (a candidate's export)
                blob += _zip_bytes(inner)
    return blob


def test_the_mail_password_is_encrypted_and_appears_nowhere_else(
    cloud, team, smtp, open_mail_servers, caplog
):
    caplog.set_level(logging.DEBUG)
    candidate = sign_in(cloud, team.candidate)
    saved = candidate.call(
        "POST",
        "/api/leads/outreach/settings",
        {**settings_for(smtp), "smtp_username": "rep", "smtp_password": PASSWORD},
    )
    assert (
        saved[0] == 200
        and saved[1]["has_password"] is True
        and PASSWORD not in json.dumps(saved[1])
    )
    assert PASSWORD not in json.dumps(
        candidate.call("GET", "/api/leads/outreach/settings")[1]
    )
    seed_workspace(cloud, team.org_id, team.candidate.member_id)
    needle = PASSWORD.encode()
    assert needle not in _file_bytes(cloud.config.data_root), (
        "not in the control database or any workspace database"
    )
    assert needle not in _zip_bytes(cloud.backup.backup_org(team.org_id)), (
        "not in a backup"
    )
    export = cloud.lifecycle.export_candidate(team.candidate, team.candidate.member_id)
    _row, blob = cloud.lifecycle.export_for(team.candidate, export["export_id"])
    assert needle not in _zip_bytes(blob), "not in the candidate's own export"
    org_export = cloud.lifecycle.export_organization(team.owner)
    _row, blob = cloud.lifecycle.export_for(team.owner, org_export["export_id"])
    assert needle not in _zip_bytes(blob), "not in the organization's export"
    audit = json.dumps(cloud.audit.list(team.org_id, limit=200), default=str)
    assert PASSWORD not in audit and "smtp_password" not in audit
    assert PASSWORD not in caplog.text
    # the workspace holds only the encrypted secret: with the wrong key it cannot be read
    local = local_module()
    with cloud.workspaces.bind(
        team.org_id, team.candidate.member_id, kind="system", source="test"
    ):
        with local._connect() as connection:
            row = connection.execute(
                "SELECT ciphertext FROM airaapi_secrets WHERE name = ?",
                (mail.PASSWORD_SECRET,),
            ).fetchone()
    assert row is not None and PASSWORD.encode() not in bytes(row[0])


def test_settings_changes_approvals_and_sends_are_audited_without_text_or_passwords(
    cloud, outreach
):
    org, candidate, _leads, _ids, smtp = outreach
    candidate.call("POST", SEND, {})
    entries = cloud.audit.list(org.org_id, limit=200)
    by_action = {e["action"]: e for e in entries}
    sending = by_action["candidate.airaapi__leads_routes__outbox_send"]
    assert (
        sending["actor_id"] == team_candidate_id(org) and sending["detail"]["sent"] == 3
    )
    approving = by_action["candidate.airaapi__leads_routes__outbox_approve"]
    assert approving["detail"]["approved"] == 3
    saving = by_action["candidate.airaapi__leads_routes__outreach_settings_save"]
    assert (
        saving["detail"]["smtp_host"] == "127.0.0.1"
        and saving["detail"]["credentials_changed"] is True
    )
    drafting = by_action["candidate.airaapi__leads_routes__outbox_drafts"]
    assert drafting["detail"]["created"] == 3
    dump = json.dumps(entries, default=str)
    assert PASSWORD not in dump and "Hosted Rep" not in dump
    for message in smtp.messages:
        body = message["data"].split("\n\n", 1)[-1][:40]
        assert body.strip() not in dump


def team_candidate_id(org) -> str:
    return org.candidate.member_id


# ---------------------------------------------------------------------------
# isolation, exports, deletion
# ---------------------------------------------------------------------------


def test_another_organizations_messages_and_leads_are_not_found(
    cloud, team, smtp, open_mail_servers, no_pause
):
    candidate, lead_ids, message_ids = prepare(cloud, team, smtp, count=1)
    other = build_org(
        cloud, "Other Out", plan="team", seats=25, slug_email="otherout.test"
    )
    configure(cloud, other, key=False)
    stranger = sign_in(cloud, other.candidate)
    for method, path, body in (
        ("PATCH", f"/api/leads/outbox/{message_ids[0]}", {"body": "hijack"}),
        ("DELETE", f"/api/leads/outbox/{message_ids[0]}", None),
        ("GET", f"/api/leads/items/{lead_ids[0]}/dial", None),
        ("POST", f"/api/leads/items/{lead_ids[0]}/call-script", {}),
        ("GET", f"/api/leads/items/{lead_ids[0]}/calls", None),
        ("POST", f"/api/leads/items/{lead_ids[0]}/calls", {"outcome": "no_answer"}),
    ):
        assert stranger.call(method, path, body)[0] == 404, (method, path)
    assert stranger.call("GET", "/api/leads/outbox")[1]["messages"] == []
    assert stranger.call("POST", "/api/leads/outbox/approve", {"ids": message_ids})[
        0
    ] in {404, 200}
    assert (
        stranger.call("GET", "/api/leads/outbox?status=approved")[1]["messages"] == []
    )
    assert (
        candidate.call("GET", "/api/leads/outbox?status=approved")[1]["counts"][
            "approved"
        ]
        == 1
    )


def test_staff_exports_leave_the_outreach_tables_out_and_the_candidates_own_has_them(
    cloud, outreach
):
    org, candidate, _leads, _ids, smtp = outreach
    candidate.call("POST", SEND, {})
    lead_id = candidate.call("GET", "/api/leads/items")[1]["leads"][0]["id"]
    candidate.call(
        "POST",
        f"/api/leads/items/{lead_id}/calls",
        {"outcome": "no_answer", "notes": "private call note"},
    )
    candidate.call(
        "POST", "/api/leads/suppressions", {"value": "blocked.test", "reason": "asked"}
    )
    seed_workspace(cloud, org.org_id, org.candidate.member_id)
    own = cloud.lifecycle.export_candidate(org.candidate, org.candidate.member_id)
    _row, blob = cloud.lifecycle.export_for(org.candidate, own["export_id"])
    tables = json.loads(zipfile.ZipFile(io.BytesIO(blob)).read("workspace.json"))[
        "tables"
    ]
    for name in (
        "lead_messages",
        "lead_suppressions",
        "lead_calls",
        "lead_settings",
        "leads",
    ):
        assert tables[name], name
    for staff in (org.admin, org.auditor, org.owner):
        made = cloud.lifecycle.export_candidate(staff, org.candidate.member_id)
        _row, blob = cloud.lifecycle.export_for(staff, made["export_id"])
        text = zipfile.ZipFile(io.BytesIO(blob)).read("workspace.json").decode()
        for name in (
            "lead_messages",
            "lead_suppressions",
            "lead_calls",
            "lead_settings",
        ):
            assert name not in text, (staff.role, name)
        assert (
            "private call note" not in text
            and "blocked.test" not in text
            and "rep@seller.test" not in text
        )
    everything = cloud.lifecycle.export_organization(org.owner)
    _row, blob = cloud.lifecycle.export_for(org.owner, everything["export_id"])
    assert b"private call note" not in _zip_bytes(blob)


def test_deleting_a_candidate_removes_the_outreach_data_and_a_legal_hold_blocks_it(
    cloud, outreach
):
    org, candidate, *_ = outreach
    candidate.call("POST", SEND, {})
    directory = cloud.workspaces.candidate_dir(org.org_id, org.candidate.member_id)
    assert (directory / "workspace.sqlite3").exists()
    from aira_cloud.errors import Conflict

    override(cloud, org, {"features": {"add": ["legal_hold"]}})
    cloud.tenancy.update_policy(org.owner, {"legal_hold": True})
    with pytest.raises(Conflict) as raised:
        cloud.lifecycle.delete_candidate_workspace(org.owner, org.candidate.member_id)
    assert raised.value.code == "legal_hold" and directory.exists(), (
        "a legal hold keeps the data"
    )
    cloud.tenancy.update_policy(org.owner, {"legal_hold": False})
    cloud.lifecycle.delete_candidate_workspace(org.owner, org.candidate.member_id)
    assert not directory.exists(), (
        "the messages, call log and suppression list went with the workspace"
    )


# ---------------------------------------------------------------------------
# finding people who need services (google_intent)
# ---------------------------------------------------------------------------

INTENT = {
    "source": "google_intent",
    "services": ["website", "ads"],
    "where": ["reddit", "web"],
}


def test_a_search_for_requests_is_a_google_search_gated_and_metered_like_the_others(
    cloud, team, serpapi
):
    candidate = sign_in(cloud, team.candidate)
    status, plan = candidate.call(
        "POST", "/api/leads/search", {**INTENT, "dry_run": True}
    )
    assert status == 200 and plan["dry_run"] and used(cloud, team, "searches") == 0
    status, found = candidate.call("POST", "/api/leads/search", INTENT)
    assert (
        status == 200 and found["count"] == 3 and found["leads"][0]["kind"] == "request"
    )
    assert (
        used(cloud, team, "searches") == 1 and used(cloud, team, "pooled_searches") == 0
    )
    # the organization's own source policy and the platform's source switch apply
    cloud.tenancy.update_policy(
        team.owner, {"allowed_sources": ["aira_jobs", "company_boards"]}
    )
    status, body = candidate.call("POST", "/api/leads/search", INTENT)
    assert status == 403 and "policy" in body["error"]
    cloud.tenancy.update_policy(
        team.owner, {"allowed_sources": ["aira_jobs", "company_boards", "google_jobs"]}
    )
    operator = operator_for(cloud)
    cloud.platform.block_source(operator, "google_jobs", "provider change")
    assert candidate.call("POST", "/api/leads/search", INTENT)[0] == 403
    cloud.platform.unblock_source(operator, "google_jobs")
    assert candidate.call("POST", "/api/leads/search", INTENT)[0] == 200


def test_the_pooled_allowance_settles_by_credits_for_a_request_search(cloud, serpapi):
    org = build_org(
        cloud, "Pooled Intent", plan="team", seats=25, slug_email="pooledintent.test"
    )
    configure(cloud, org, key=False)
    cloud.tenancy.update_policy(org.owner, {"use_pooled_serpapi": True})
    candidate = sign_in(cloud, org.candidate)
    status, body = candidate.call("POST", "/api/leads/search", INTENT)
    if status == 200:
        assert used(cloud, org, "pooled_searches") == body["credits"]
        again = candidate.call("POST", "/api/leads/search", INTENT)[1]
        assert (
            again["cache_hit"]
            and used(cloud, org, "pooled_searches") == body["credits"]
        ), "a cached search costs nothing"
