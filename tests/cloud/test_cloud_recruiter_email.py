"""Recruiter email from a list in the hosted shell: a candidate writes to the recruiters in their own workspace
through the organization's mail (Business hub > Email, set up by an owner), within the organization's sending
policy: who may send, the plan, the operator's switch, the month's allowance and both do-not-contact lists.

The mail server is the loopback stand-in (``tests/smtp_stub.py``); a hosted build refuses internal mail servers, so
``open_mail_servers`` replaces ``aira.leads.mail.check_smtp_host`` for a test, the same seam the Leads outreach tests
use. Nothing here talks to a real mail server.
"""

from __future__ import annotations

import json
from email import message_from_string
from typing import Any

import pytest
from cloud_support import audit_actions, build_org, seed_workspace, sign_in
from smtp_stub import SmtpStub

from aira.leads import mail
from aira.recruiter_outreach import OPT_OUT_LINE
from monosage_cloud.policy import POLICY
from monosage_cloud.tenancy import REQUIRED_CONSENTS

SMTP_LOGIN = ("mailer@careers.test", "s3cret-0rg-Sm7p")
PEOPLE = (
    ("Marco Diaz", "Contoso Labs", "marco@contosolabs.test"),
    ("Lena Park", "Northwind", "lena@northwind.test"),
    ("Priya Shah", "Fabrikam", "priya@fabrikam.test"),
)
SUBJECT = "Checking in, {first_name}"
BODY = "Hi {first_name},\n\nAny news at {company|your team}?\n\nThank you,\n{my_name}"


@pytest.fixture
def smtp():
    server = SmtpStub(require_login=SMTP_LOGIN).start()
    yield server
    server.stop()


@pytest.fixture
def open_mail_servers(monkeypatch):
    """Test-only seam: let the hosted build talk to the loopback stand-in mail server."""
    monkeypatch.setattr(mail, "check_smtp_host", lambda host, port: None)


def team_org(cloud, name="Acme Careers", slug="careers.test", plan="team"):
    org = build_org(cloud, name, plan=plan, seats=25, slug_email=slug, apps="all")
    seed_workspace(
        cloud, org.org_id, org.candidate.member_id, resume=False, document=False
    )
    return org


def org_mail(cloud, org, smtp, *, from_name="Acme Careers"):
    """An owner sets up the organization's email in Business hub > Email."""
    owner = sign_in(cloud, org.owner)
    status, body = owner.call(
        "POST",
        "/api/suite/email",
        {
            "from_name": from_name,
            "from_email": f"careers@{org.owner.email.split('@')[1]}",
            "smtp_host": "127.0.0.1",
            "smtp_port": smtp.port,
            "smtp_security": "none",
            "smtp_username": SMTP_LOGIN[0],
            "smtp_password": SMTP_LOGIN[1],
        },
    )
    assert status == 200 and body["ready"], body
    return owner


def recruiters(browser, people=PEOPLE) -> tuple[str, dict[str, str]]:
    """The candidate's contacts and a list of all of them: (list id, {email: recruiter id})."""
    ids = {}
    for name, company, email in people:
        status, body = browser.call(
            "POST",
            "/api/recruiters",
            {"name": name, "company": company, "email": email},
        )
        assert status == 201, body
        ids[email] = body["recruiter"]["recruiter_id"]
    status, body = browser.call(
        "POST", "/api/recruiters/lists", {"name": "Everyone", "filters": {}}
    )
    assert status == 201, body
    return body["list"]["list_id"], ids


def preview(browser, list_id, **extra: Any) -> tuple[int, Any]:
    return browser.call(
        "POST",
        f"/api/recruiters/lists/{list_id}/email/preview",
        {"subject": SUBJECT, "body": BODY, **extra},
    )


def send(browser, list_id, ids, **extra: Any) -> tuple[int, Any]:
    return browser.call(
        "POST",
        f"/api/recruiters/lists/{list_id}/email/send",
        {
            "subject": SUBJECT,
            "body": BODY,
            "recruiter_ids": list(ids),
            "confirm": True,
            **extra,
        },
    )


def override(cloud, org, values):
    with cloud.db.write() as connection:
        connection.execute(
            "UPDATE subscriptions SET overrides = ? WHERE org_id = ?",
            (json.dumps(values), org.org_id),
        )
    cloud.entitlements.invalidate(org.org_id)


def used(cloud, org) -> int:
    return cloud.usage.used(org.org_id, "lead_emails")


def operator_for(cloud):
    cloud.identity.create_operator(
        "ops@aira.test", "operator-password-123", role="platform_admin"
    )
    token = cloud.identity.operator_login("ops@aira.test", "operator-password-123")[
        "token"
    ]
    return cloud.identity.principal_for_token(token, kind="operator")


# ---------------------------------------------------------------------------
# policy
# ---------------------------------------------------------------------------


def test_the_recruiter_email_routes_have_hosted_rules():
    expected = {
        "api_recruiter_outreach": ("messages.read", False, ""),
        "api_recruiter_outreach_save": (
            "messages.send",
            True,
            "recruiter_outreach_settings",
        ),
        "api_recruiter_list_email_preview": ("messages.send", True, ""),
        "api_recruiter_list_email_send": (
            "messages.send",
            True,
            "recruiter_email_send",
        ),
    }
    for name, (permission, writes, kind) in expected.items():
        rule = POLICY[name]
        assert (rule.scope, rule.permission, rule.write, rule.kind) == (
            "candidate",
            permission,
            writes,
            kind,
        ), name
    assert POLICY["api_recruiter_list_email_send"].audit is True


# ---------------------------------------------------------------------------
# a candidate sends through the organization's mail
# ---------------------------------------------------------------------------


def test_a_candidate_previews_and_sends_through_the_organizations_mail(
    cloud, smtp, open_mail_servers
):
    org = team_org(cloud)
    candidate = sign_in(cloud, org.candidate)
    list_id, ids = recruiters(candidate)

    # Before the organization has set up email the page says who does it, and nothing can be sent.
    status, view = candidate.call("GET", "/api/recruiters/outreach")
    assert status == 200, view
    assert view["email"]["organization"] is True and view["email"]["ready"] is False
    assert view["email"]["unavailable"] == ""
    status, refused = send(candidate, list_id, ids.values())
    assert status == 400 and "Business hub > Email" in refused["error"], refused
    assert used(cloud, org) == 0, "a refused send gives its reservation back"

    org_mail(cloud, org, smtp)
    status, view = candidate.call("GET", "/api/recruiters/outreach")
    assert view["email"]["ready"] is True and view["email"]["has_password"] is True
    assert view["email"]["from_email"] == "careers@careers.test"
    assert view["email"]["reply_to"] == org.candidate.email
    assert view["max_daily_limit"] == 50, "the Team plan's daily cap"
    assert SMTP_LOGIN[1] not in json.dumps(view)

    status, plan = preview(candidate, list_id)
    assert status == 200, plan
    assert plan["ready"] == 3 and plan["email"]["ready"] is True
    first = plan["items"][0]
    assert (
        first["subject"].startswith("Checking in, ") and OPT_OUT_LINE in first["body"]
    )
    assert smtp.messages == [], "a preview sends nothing"

    status, result = send(candidate, list_id, ids.values())
    assert status == 200, result
    assert (result["sent"], result["failed"], result["skipped"]) == (3, 0, 0)
    assert "note" not in result
    assert len(smtp.messages) == 3
    letters = {
        message_from_string(m["data"])["To"]: message_from_string(m["data"])
        for m in smtp.messages
    }
    assert set(letters) == {email for _n, _c, email in PEOPLE}
    letter = letters["marco@contosolabs.test"]
    assert letter["From"] == "Cam Candidate via Acme Careers <careers@careers.test>"
    assert letter["Reply-To"] == org.candidate.email, "replies reach the candidate"
    text = letter.get_payload(decode=True).decode("utf-8")
    assert "Hi Marco," in text and "Contoso Labs" in text and OPT_OUT_LINE in text

    # Metered as outreach emails, audited as counts only (no address, no text).
    assert used(cloud, org) == 3
    entries = cloud.audit.list(
        org.org_id, limit=50, action="candidate.recruiter_list_email_send"
    )
    assert entries and entries[0]["candidate_id"] == org.candidate.member_id
    detail = json.dumps(entries[0]["detail"])
    assert (
        '"sent": 3' in detail and "contosolabs" not in detail and "Marco" not in detail
    )

    # The send log stays in the candidate's workspace and shows in the contact's story.
    status, story = candidate.call(
        "GET", f"/api/recruiters/{ids['marco@contosolabs.test']}/story"
    )
    assert [i["status"] for i in story["items"] if i["kind"] == "outreach"] == ["sent"]
    connection = cloud.workspaces.org_api_connection(org.org_id)
    try:
        assert (
            connection.execute(
                "SELECT COUNT(*) FROM suite_outbox WHERE kind = 'recruiter_email'"
            ).fetchone()[0]
            == 0
        ), "the organization lends its mail server, not its outbox"
    finally:
        connection.close()

    # Everyone was just emailed: the follow-up wait applies, nothing goes out twice.
    status, again = send(candidate, list_id, ids.values())
    assert status == 200 and again["sent"] == 0 and len(smtp.messages) == 3
    assert used(cloud, org) == 3, "only messages actually sent are counted"


def test_opt_outs_on_either_list_and_by_reply_are_respected(
    cloud, smtp, open_mail_servers
):
    org = team_org(cloud)
    owner = org_mail(cloud, org, smtp)
    candidate = sign_in(cloud, org.candidate)
    people = (*PEOPLE, ("Sam Reed", "Tailspin", "sam@tailspin.test"))
    list_id, ids = recruiters(candidate, people)
    # The candidate's own do-not-contact list.
    status, _ = candidate.call(
        "POST",
        f"/api/recruiters/{ids['marco@contosolabs.test']}/do-not-contact",
        {"reason": "Asked me to stop"},
    )
    assert status == 200
    # The organization's do-not-contact list (Leads), for mail through its server: a whole domain here.
    status, body = owner.call(
        "POST", "/api/leads/suppressions", {"value": "northwind.test"}
    )
    assert status == 201, body
    # An "unsubscribe" reply the candidate imported.
    status, body = candidate.call(
        "POST",
        "/api/email/import",
        {
            "subject": "Re: Checking in",
            "body": "Please unsubscribe me, I have filled the role.",
            "sender": "priya@fabrikam.test",
            "sender_name": "Priya Shah",
            "received_at": "2026-09-20T10:00:00+00:00",
        },
    )
    assert status == 201, body

    status, plan = preview(candidate, list_id)
    assert status == 200, plan
    reasons = {item["email"]: item.get("reason", "") for item in plan["items"]}
    assert plan["ready"] == 1 and plan["skipped"] == 3, reasons
    assert "do-not-contact" in reasons["marco@contosolabs.test"]
    assert (
        reasons["lena@northwind.test"] == "On your organization's do-not-contact list."
    )
    assert "do-not-contact" in reasons["priya@fabrikam.test"]
    assert reasons["sam@tailspin.test"] == ""

    status, result = send(candidate, list_id, ids.values())
    assert status == 200 and result["sent"] == 1 and result["skipped"] == 3, result
    assert [message_from_string(m["data"])["To"] for m in smtp.messages] == [
        "sam@tailspin.test"
    ]
    assert used(cloud, org) == 1
    # The organization's list holds the organization's entries only: the candidate's contacts stay private.
    status, listed = owner.call("GET", "/api/leads/suppressions")
    assert [s["value"] for s in listed["suppressions"]] == ["northwind.test"]


# ---------------------------------------------------------------------------
# who may send
# ---------------------------------------------------------------------------


def test_a_role_without_messages_send_is_refused(cloud, smtp, open_mail_servers):
    org = team_org(cloud)
    org_mail(cloud, org, smtp)
    candidate = sign_in(cloud, org.candidate)
    list_id, ids = recruiters(candidate)
    target = org.candidate.member_id
    cloud.tenancy.assign_coach(org.owner, org.coach.member_id, target, "act")
    for who in ("admin", "auditor", "coach", "billing"):
        browser = sign_in(cloud, getattr(org, who))
        status, body = browser.call(
            "POST",
            f"/api/recruiters/lists/{list_id}/email/send",
            {"recruiter_ids": list(ids.values()), "confirm": True},
            as_candidate=target,
        )
        assert status in (403, 404), (who, status, body)
        status, _ = browser.call(
            "POST",
            f"/api/recruiters/lists/{list_id}/email/preview",
            {},
            as_candidate=target,
        )
        assert status in (403, 404), who
    # A coach the policy lets read messages may look at the templates, not send.
    cloud.tenancy.update_policy(org.owner, {"coach_messages": "view"})
    coach = sign_in(cloud, org.coach)
    assert coach.call("GET", "/api/recruiters/outreach", as_candidate=target)[0] == 200
    status, body = coach.call(
        "POST",
        f"/api/recruiters/lists/{list_id}/email/send",
        {"recruiter_ids": list(ids.values()), "confirm": True},
        as_candidate=target,
    )
    assert status == 403 and body["permission"] == "messages.send", body
    # A coach who may act on messages still needs the candidate's consent.
    cloud.tenancy.update_policy(org.owner, {"coach_messages": "act"})
    status, body = coach.call(
        "POST",
        f"/api/recruiters/lists/{list_id}/email/send",
        {"recruiter_ids": list(ids.values()), "confirm": True},
        as_candidate=target,
    )
    assert status == 403 and body["code"] == "consent_required", body
    cloud.tenancy.record_consent(org.candidate, list(REQUIRED_CONSENTS))
    status, body = coach.call(
        "POST",
        f"/api/recruiters/lists/{list_id}/email/send",
        {
            "subject": SUBJECT,
            "body": BODY,
            "recruiter_ids": list(ids.values()),
            "confirm": True,
        },
        as_candidate=target,
    )
    assert status == 200 and body["sent"] == 3, body
    assert smtp.messages and all(
        message_from_string(m["data"])["Reply-To"] == org.candidate.email
        for m in smtp.messages
    ), "the email is the candidate's, whoever pressed Send"
    assert "candidate.recruiter_list_email_send" in audit_actions(cloud, org.org_id)


# ---------------------------------------------------------------------------
# the organization's sending policy: plan, switch, allowance, batches, daily cap
# ---------------------------------------------------------------------------


def test_a_plan_without_outbound_email_is_told_and_refused(
    cloud, smtp, open_mail_servers
):
    org = build_org(
        cloud, "Starter Careers", plan="starter", seats=5, slug_email="st.test"
    )
    candidate = sign_in(cloud, org.candidate)
    list_id, ids = recruiters(candidate)
    status, view = candidate.call("GET", "/api/recruiters/outreach")
    assert status == 200
    assert (
        view["email"]["unavailable"]
        == "Recruiter email is not part of your organization's plan."
    )
    status, body = send(candidate, list_id, ids.values())
    assert status == 402 and body["code"] == "plan_required", body
    assert smtp.messages == [] and used(cloud, org) == 0


def test_the_operator_switch_stops_recruiter_email(cloud, smtp, open_mail_servers):
    org = team_org(cloud)
    org_mail(cloud, org, smtp)
    candidate = sign_in(cloud, org.candidate)
    list_id, ids = recruiters(candidate)
    operator = operator_for(cloud)
    cloud.platform.block_source(operator, "leads_outreach", "mail reputation 9")
    assert (
        "mail reputation 9"
        in candidate.call("GET", "/api/recruiters/outreach")[1]["email"]["unavailable"]
    )
    status, body = send(candidate, list_id, ids.values())
    assert status == 403 and body["code"] == "outreach_blocked", body
    assert smtp.messages == [] and used(cloud, org) == 0
    cloud.platform.unblock_source(operator, "leads_outreach")
    assert send(candidate, list_id, ids.values())[1]["sent"] == 3


def test_batches_the_monthly_allowance_and_the_daily_cap(
    cloud, smtp, open_mail_servers
):
    org = team_org(cloud)
    org_mail(cloud, org, smtp)
    candidate = sign_in(cloud, org.candidate)
    people = tuple(
        (f"Person {n}", f"Company {n}", f"p{n}@company{n}.test") for n in range(12)
    )
    list_id, _ids = recruiters(candidate, people)

    # The plan's daily cap bounds the candidate's own daily limit.
    override(cloud, org, {"lead_email_daily_cap": 11})
    status, saved = candidate.call(
        "POST", "/api/recruiters/outreach", {"daily_limit": 60}
    )
    assert status == 200 and saved["settings"]["daily_limit"] == 11, saved
    assert saved["max_daily_limit"] == 11
    status, plan = preview(candidate, list_id)
    assert plan["ready"] == 11 and plan["later"] == 1 and plan["daily_limit"] == 11
    ready = [i["recruiter_id"] for i in plan["items"] if i["status"] == "ready"]
    later = [i["recruiter_id"] for i in plan["items"] if i["status"] == "later"]

    # One request carries at most ten emails; the rest wait for the next press.
    status, result = send(candidate, list_id, ready)
    assert status == 200 and result["sent"] == 10, result
    assert result["held_back"] == 1 and "Send again for the rest" in result["note"]
    assert used(cloud, org) == 10

    # The month's allowance is a hard stop: one email left, so one goes.
    override(cloud, org, {"lead_email_daily_cap": 11, "lead_emails_per_month": 11})
    status, result = send(candidate, list_id, ready[10:] + later)
    assert status == 200 and result["sent"] == 1, result
    assert used(cloud, org) == 11 and len(smtp.messages) == 11
    status, body = send(candidate, list_id, later)
    assert status == 402 and body["code"] == "plan_required", body
    assert len(smtp.messages) == 11 and used(cloud, org) == 11


# ---------------------------------------------------------------------------
# isolation
# ---------------------------------------------------------------------------


def test_each_organization_sends_through_its_own_mail_only(
    cloud, smtp, open_mail_servers
):
    other_smtp = SmtpStub(require_login=SMTP_LOGIN).start()
    try:
        acme = team_org(cloud)
        globex = team_org(cloud, "Globex Careers", "globex.test")
        org_mail(cloud, acme, smtp)
        acme_candidate = sign_in(cloud, acme.candidate)
        acme_list, acme_ids = recruiters(acme_candidate)
        globex_candidate = sign_in(cloud, globex.candidate)
        globex_list, globex_ids = recruiters(globex_candidate)

        # Globex has not set up email: Acme's mail is not lent to it.
        status, view = globex_candidate.call("GET", "/api/recruiters/outreach")
        assert view["email"]["ready"] is False and view["email"]["from_email"] == ""
        assert send(globex_candidate, globex_list, globex_ids.values())[0] == 400
        assert smtp.messages == []

        # Neither candidate reaches the other's list or contacts.
        assert preview(globex_candidate, acme_list)[0] == 404
        status, body = send(globex_candidate, acme_list, acme_ids.values())
        assert status in (400, 404) and smtp.messages == [], body
        assert globex_candidate.call(
            "GET", "/api/recruiters/outreach", as_candidate=acme.candidate.member_id
        )[0] in (403, 404)

        # Globex's own do-not-contact list does not touch Acme's sends, and each goes through its own server.
        globex_owner = org_mail(cloud, globex, other_smtp, from_name="Globex Careers")
        assert (
            globex_owner.call(
                "POST", "/api/leads/suppressions", {"value": "contosolabs.test"}
            )[0]
            == 201
        )
        assert send(acme_candidate, acme_list, acme_ids.values())[1]["sent"] == 3
        status, result = send(globex_candidate, globex_list, globex_ids.values())
        assert result["sent"] == 2, result
        assert {m["from"] for m in smtp.messages} == {"careers@careers.test"}
        assert {m["from"] for m in other_smtp.messages} == {"careers@globex.test"}
        assert all(
            message_from_string(m["data"])["Reply-To"] == globex.candidate.email
            for m in other_smtp.messages
        )
        assert used(cloud, acme) == 3 and used(cloud, globex) == 2
    finally:
        other_smtp.stop()
