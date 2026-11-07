"""Applying by email in the hosted shell: a candidate's application goes out through the organization's mail
(Business hub > Email, set up by an owner) with the candidate's name on it and Reply-To their own address, on every
plan, inside the operator's outbound switch and the organization's do-not-contact list.

The mail server is the loopback stand-in; ``open_mail_servers`` is the same test seam the recruiter email tests use.
"""

# ruff: noqa: F811
from __future__ import annotations

import sqlite3
from email import message_from_string, policy

import pytest
from cloud_support import PASSWORD, build_org, seed_workspace, sign_in
from test_cloud_recruiter_email import (  # noqa: F401
    SMTP_LOGIN,
    open_mail_servers,
    org_mail,
    smtp,
)

from monosage_cloud.outreach_mail import APPLICATIONS_SWITCHED_OFF
from monosage_cloud.policy import POLICY

APPLY_TO = "talent@acme-hiring.test"
DESCRIPTION = (
    "Requirements: 10 years of AWS, Kubernetes and Terraform. Python. Stakeholder management. "
    * 3
    + f"\n\nTo apply, please send your resume to {APPLY_TO}. Reference: CL-7731."
)


def email_org(cloud, plan="starter"):
    org = build_org(
        cloud,
        "Acme Careers",
        plan=plan,
        seats=10,
        slug_email="careers.test",
        apps="all",
    )
    seed_workspace(
        cloud, org.org_id, org.candidate.member_id, job_title="Cloud Architect"
    )
    path = cloud.workspaces.data_path(org.org_id, org.candidate.member_id)
    connection = sqlite3.connect(path)
    connection.execute(
        "UPDATE jobs SET description = ?, application_url = ?",
        (DESCRIPTION, "https://careers.acme-hiring.test/jobs/1"),
    )
    connection.commit()
    connection.close()
    return org, f"job-{org.candidate.member_id[:10]}"


def test_both_routes_have_a_policy():
    assert POLICY["api_job_apply_email_preview"].scope == "candidate"
    send = POLICY["api_job_apply_email_send"]
    assert send.write and send.permission == "applications.submit"
    assert send.kind == "email_apply" and send.audit


def test_a_candidate_applies_by_email_through_the_organizations_mail(
    cloud,
    smtp,
    open_mail_servers,  # noqa: F811
):
    org, job_id = email_org(cloud)
    candidate = sign_in(cloud, org.candidate)

    # before the organization sets up email: the person is told, nothing is sent
    status, preview = candidate.call("GET", f"/api/jobs/{job_id}/apply-email")
    assert status == 200 and preview["available"], preview
    assert not preview["mail"]["ready"]
    assert "your organization has not set up email" in preview["mail"]["problem"]
    status, body = candidate.call(
        "POST", f"/api/jobs/{job_id}/apply-email", {"confirm": True}
    )
    assert status == 400 and smtp.messages == []

    org_mail(cloud, org, smtp)
    status, preview = candidate.call("GET", f"/api/jobs/{job_id}/apply-email")
    assert status == 200 and preview["mail"]["ready"], preview
    # the member's own name on the organization's address, as recruiter email has it
    assert " via Acme Careers <careers@" in preview["mail"]["from"]
    assert preview["mail"]["reply_to"] == org.candidate.email
    assert "(CL-7731)" in preview["subject"]

    status, sent = candidate.call(
        "POST", f"/api/jobs/{job_id}/apply-email", {"confirm": True}
    )
    assert status == 200, sent
    [delivered] = smtp.messages
    assert delivered["to"] == [APPLY_TO]
    message = message_from_string(delivered["data"], policy=policy.default)
    assert message["Reply-To"] == org.candidate.email
    assert " via Acme Careers" in message["From"]
    assert [part.get_filename() for part in message.iter_attachments()], (
        "the resume is attached"
    )
    status, job = candidate.call("GET", f"/api/jobs/{job_id}")
    assert job["job"]["submission"]["method"] == "email"
    assert job["job"]["stage"] == "applied"

    # one email per job, ever
    status, _ = candidate.call(
        "POST", f"/api/jobs/{job_id}/apply-email", {"confirm": True}
    )
    assert status == 400 and len(smtp.messages) == 1


def test_the_operators_outbound_switch_holds_applications_by_email(
    cloud,
    smtp,
    open_mail_servers,  # noqa: F811
):
    org, job_id = email_org(cloud)
    org_mail(cloud, org, smtp)
    cloud.identity.create_operator("ops@aira.test", PASSWORD, role="platform_admin")
    operator = cloud.identity.principal_for_token(
        cloud.identity.operator_login("ops@aira.test", PASSWORD)["token"],
        kind="operator",
    )
    cloud.platform.block_source(operator, cloud.gate.OUTREACH_SWITCH, "mail incident")
    candidate = sign_in(cloud, org.candidate)
    status, preview = candidate.call("GET", f"/api/jobs/{job_id}/apply-email")
    assert preview["mail"]["problem"] == APPLICATIONS_SWITCHED_OFF
    status, body = candidate.call(
        "POST", f"/api/jobs/{job_id}/apply-email", {"confirm": True}
    )
    assert status == 400 and smtp.messages == []
    cloud.platform.unblock_source(operator, cloud.gate.OUTREACH_SWITCH)
    status, _ = candidate.call(
        "POST", f"/api/jobs/{job_id}/apply-email", {"confirm": True}
    )
    assert status == 200 and len(smtp.messages) == 1


@pytest.mark.parametrize("who", ["auditor", "billing"])
def test_only_people_who_may_submit_send_it(cloud, smtp, open_mail_servers, who):  # noqa: F811
    org, job_id = email_org(cloud)
    org_mail(cloud, org, smtp)
    browser = sign_in(cloud, getattr(org, who))
    status, _ = browser.call(
        "POST",
        f"/api/jobs/{job_id}/apply-email",
        {"confirm": True},
        as_candidate=org.candidate.member_id,
    )
    assert status in (403, 404) and smtp.messages == []
