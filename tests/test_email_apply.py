"""Applying by email (aira/email_apply.py): no browser, one email per job, ever.

The detector finds an apply address only where the posting asks for applications by email; the message carries
the resume and cover letter as attachments and the person's own contact details; the submission is recorded with
method ``email`` and the Message-ID as evidence. The mail server is the loopback stand-in (``tests/smtp_stub.py``):
nothing reaches the network.
"""

# ruff: noqa: F811
from __future__ import annotations

import sqlite3
from email import message_from_string, policy

import pytest
from smtp_stub import SmtpStub
from test_autopilot import (  # noqa: F401
    DESCRIPTION,
    BoardAdapter,
    _bound_user,
    make_plan,
    ok,
    server,
    sign_up,
    wait_idle,
)

import auto_apply_assistant as app
from aira import email_apply
from aira.pdf_text import text_to_pdf
from linkedin_mcp_server.job_sources import JobPosting

SMTP_LOGIN = ("jordan@example.com", "app-password-123")
APPLY_TO = "talent@acme.test"
EMAIL_DESCRIPTION = (
    DESCRIPTION
    + "\n\nHow to apply: please send your resume and cover letter to talent@acme.test. Reference: ENG-2041."
    + "\n\nQuestions about accessibility? Write to help@acme.test."
)


# --------------------------------------------------------------------------- finding the address


@pytest.mark.parametrize(
    ("text", "address"),
    [
        ("Please send your resume and cover letter to jobs@acme.io.", "jobs@acme.io"),
        ("To apply, email your CV to Careers@Acme.io", "careers@acme.io"),
        ("Apply by email: hiring@acme.io", "hiring@acme.io"),
        (
            "Interested candidates should send their CV to talent@acme.io.",
            "talent@acme.io",
        ),
        ("Applications should be sent to people@acme.io by Friday.", "people@acme.io"),
        ("Envía tu CV a empleo@acme.es", "empleo@acme.es"),
        ("Envoyez votre CV et lettre de motivation à rh@acme.fr", "rh@acme.fr"),
        ("Senden Sie Ihre Bewerbung an bewerbung@acme.de.", "bewerbung@acme.de"),
        ("Envie seu currículo para vagas@acme.com.br", "vagas@acme.com.br"),
        ("Invia il tuo CV a lavoro@acme.it", "lavoro@acme.it"),
        ("Stuur je cv naar werk@acme.nl", "werk@acme.nl"),
    ],
)
def test_apply_wording_before_an_address_makes_it_an_apply_address(text, address):
    found = email_apply.find_apply_address(text)
    assert found is not None and found.address == address and found.how == "posting"
    assert address.split("@")[0].lower() in found.evidence.lower()


@pytest.mark.parametrize(
    "text",
    [
        "Questions? Contact hr@acme.io for more information.",
        "If you need an accommodation during the application process, please email accommodations@acme.io.",
        "We are an equal opportunity employer. Contact us at info@acme.io.",
        "Recruitment agencies: please do not send unsolicited resumes to talent@acme.io.",
        "Apply on our website. For privacy questions write to privacy@acme.io",
        "Send your resume to noreply@acme.io",
        "Send your resume to you@example.com",  # a placeholder
        "Apply on our careers page.\n\nAcme Inc. · 1 Main St · jobs@acme.io",  # a footer
        "Please apply through the form. Send any questions about the role to jobs@acme.io.",
    ],
)
def test_an_address_in_a_footer_or_for_something_else_is_not_an_apply_address(text):
    assert email_apply.find_apply_address(text) is None


def test_a_mailto_application_link_wins_and_keeps_its_subject():
    found = email_apply.find_apply_address(
        EMAIL_DESCRIPTION,
        "mailto:Jobs@Acme.io?subject=Senior%20Engineer%20application",
        "https://acme.io/jobs/1",
    )
    assert found.how == "mailto" and found.address == "jobs@acme.io"
    assert found.subject == "Senior Engineer application"
    assert email_apply.find_apply_address("", "mailto:noreply@acme.io") is None


def test_the_job_reference_goes_in_the_subject():
    found = email_apply.find_apply_address(EMAIL_DESCRIPTION)
    assert found.address == APPLY_TO and found.reference == "ENG-2041"
    job = {"title": "Cloud Architect", "company": "Acme"}
    assert (
        email_apply.default_subject(job, found, "Jordan Example")
        == "Application for Cloud Architect (ENG-2041) - Jordan Example"
    )
    body = email_apply.default_body(
        job,
        {
            "full_name": "Jordan Example",
            "email": "jordan@example.com",
            "phone": "+1 555",
        },
        found,
        has_letter=True,
    )
    assert body.startswith("Dear Acme hiring team,")
    assert "resume and cover letter are attached" in body
    assert body.rstrip().endswith("+1 555")


# --------------------------------------------------------------------------- the message and the ledger


@pytest.fixture
def files(tmp_path):
    resume = tmp_path / "Jordan Example Resume.pdf"
    resume.write_bytes(text_to_pdf("Jordan Example\nCloud architect", "Resume"))
    letter = tmp_path / "Cover Letter.pdf"
    letter.write_bytes(text_to_pdf("Dear team", "Cover Letter"))
    return [
        email_apply.Attachment(str(resume), "resume"),
        email_apply.Attachment(str(letter), "cover_letter"),
    ]


def test_the_message_attaches_the_files_and_replies_to_the_person(files):
    settings = {"from_email": "jordan@example.com", "from_name": "My Business"}
    message = email_apply.compose(
        settings,
        to=APPLY_TO,
        subject="Application",
        body="Hello",
        attachments=files,
        from_name="Jordan Example",
        reply_to="jordan@personal.test",
    )
    assert message["From"] == "Jordan Example <jordan@example.com>"
    assert message["Reply-To"] == "jordan@personal.test"
    assert message["Message-ID"].endswith("@example.com>")
    names = [part.get_filename() for part in message.iter_attachments()]
    assert names == ["Jordan Example Resume.pdf", "Cover Letter.pdf"]
    evidence = email_apply.evidence_text(message, "2026-11-06T10:00:00+00:00")
    assert "Attachment: Cover Letter.pdf" in evidence and "Hello" in evidence
    assert "%PDF" not in evidence, "the evidence lists the files, it does not copy them"


def test_files_too_large_to_email_are_refused(files, monkeypatch):
    monkeypatch.setattr(email_apply, "MAX_ATTACHMENT_BYTES", 10)
    with pytest.raises(ValueError, match="larger than"):
        email_apply.check_attachments(files)
    monkeypatch.setattr(email_apply, "MAX_ATTACHMENT_BYTES", 10**9)
    monkeypatch.setattr(email_apply, "MAX_TOTAL_BYTES", 10)
    with pytest.raises(ValueError, match="come to more than"):
        email_apply.check_attachments(files)


def test_one_email_per_job_ever():
    connection = sqlite3.connect(":memory:")
    connection.row_factory = sqlite3.Row
    claim = lambda: email_apply.claim(  # noqa: E731
        connection, "job-1", recipient=APPLY_TO, subject="s", attachments=[]
    )
    assert claim() is True
    assert claim() is False, "a send under way is never started twice"
    email_apply.mark_failed(connection, "job-1", "550 mailbox unavailable")
    assert email_apply.blocked_reason(email_apply.record(connection, "job-1")) == ""
    assert claim() is True, "a refused send may be tried again"
    email_apply.mark_sent(connection, "job-1", "<id@example.com>")
    assert claim() is False
    record = email_apply.record(connection, "job-1")
    assert record["status"] == "sent" and record["message_id"] == "<id@example.com>"
    assert "already emailed" in email_apply.blocked_reason(record)


# --------------------------------------------------------------------------- the local build, end to end


@pytest.fixture
def smtp():
    stub = SmtpStub(require_login=SMTP_LOGIN).start()
    yield stub
    stub.stop()


def set_up_mail(client, smtp):
    ok(
        client,
        "POST",
        "/api/suite/email",
        {
            "from_name": "Jordan's Studio",
            "from_email": "jordan@example.com",
            "smtp_host": "127.0.0.1",
            "smtp_port": smtp.port,
            "smtp_security": "none",
            "smtp_username": SMTP_LOGIN[0],
            "smtp_password": SMTP_LOGIN[1],
        },
    )


def email_job(external_id="9001", **extra):
    _bound_user(None)
    try:
        return app._upsert_job(
            external_id,
            source="manual",
            source_url=f"https://careers.acme.test/jobs/{external_id}",
            application_url=f"https://careers.acme.test/jobs/{external_id}",
            title="Cloud Architect",
            company="Acme",
            description=EMAIL_DESCRIPTION,
            **extra,
        )["job_id"]
    finally:
        app._unbind_user()


def parsed(stub_message):
    return message_from_string(stub_message["data"], policy=policy.default)


def test_the_job_drawer_previews_then_sends_one_email(server, smtp):  # noqa: F811
    sign_up(server)
    set_up_mail(server, smtp)
    job_id = email_job()
    job = ok(server, "GET", f"/api/jobs/{job_id}")["job"]
    assert (
        job["apply_by_email"]["address"] == APPLY_TO
        and job["email_application"] is None
    )

    preview = ok(server, "GET", f"/api/jobs/{job_id}/apply-email")
    assert preview["available"] and preview["mail"]["ready"] and not preview["problem"]
    assert (
        preview["subject"]
        == "Application for Cloud Architect (ENG-2041) - Jordan Example"
    )
    assert (
        "Jordan Example" in preview["body"] and "jordan@example.com" in preview["body"]
    )
    assert [item["kind"] for item in preview["attachments"]] == ["resume"]
    assert preview["mail"]["from"] == "Jordan Example <jordan@example.com>"
    assert smtp.messages == [], "a preview sends nothing"

    status, body, _ = server.call(
        "POST", f"/api/jobs/{job_id}/apply-email", {"subject": "x"}
    )
    assert status == 400 and "Confirm" in body["error"] and smtp.messages == []

    sent = ok(
        server,
        "POST",
        f"/api/jobs/{job_id}/apply-email",
        {
            "confirm": True,
            "subject": "Cloud Architect application (ENG-2041)",
            "body": "Hello Acme,\n\nJordan",
        },
    )
    assert sent["recipient"] == APPLY_TO and sent["submission"]["method"] == "email"
    [delivered] = smtp.messages
    assert delivered["to"] == [APPLY_TO]
    message = parsed(delivered)
    assert message["Subject"] == "Cloud Architect application (ENG-2041)"
    assert message["Reply-To"] == "jordan@example.com"
    assert message["Message-ID"] == sent["message_id"]
    [resume] = list(message.iter_attachments())
    assert (
        resume.get_filename().endswith(".pdf") and resume.get_content()[:4] == b"%PDF"
    )

    job = ok(server, "GET", f"/api/jobs/{job_id}")["job"]
    assert job["stage"] == "applied" and job["submission"]["method"] == "email"
    assert sent["message_id"] in job["submission"]["confirmation_text"]
    assert job["email_application"]["status"] == "sent"
    assert job["submission"]["evidence_document_id"]
    assert any(task["kind"] == "follow_up" for task in job["tasks"])

    # one email per job, ever
    status, body, _ = server.call(
        "POST", f"/api/jobs/{job_id}/apply-email", {"confirm": True}
    )
    assert status == 400 and "already submitted" in body["error"]
    assert len(smtp.messages) == 1


def test_without_email_set_up_nothing_is_sent_and_the_person_is_told(server, smtp):  # noqa: F811
    sign_up(server)
    job_id = email_job()
    preview = ok(server, "GET", f"/api/jobs/{job_id}/apply-email")
    assert preview["available"] and not preview["mail"]["ready"]
    assert preview["mail"]["problem"].startswith("Set up email to apply by email")
    status, body, _ = server.call(
        "POST", f"/api/jobs/{job_id}/apply-email", {"confirm": True}
    )
    assert status == 400 and body["error"].startswith("Set up email to apply by email")
    assert smtp.messages == []


def test_a_refused_send_can_be_tried_again_but_an_unconfirmed_one_never(server, smtp):  # noqa: F811
    sign_up(server)
    set_up_mail(server, smtp)
    job_id = email_job()
    smtp.refuse = {APPLY_TO}
    status, body, _ = server.call(
        "POST", f"/api/jobs/{job_id}/apply-email", {"confirm": True}
    )
    assert status == 400 and "not sent" in body["error"]
    job = ok(server, "GET", f"/api/jobs/{job_id}")["job"]
    assert job["email_application"]["status"] == "failed" and job["submission"] is None
    smtp.refuse = set()
    ok(server, "POST", f"/api/jobs/{job_id}/apply-email", {"confirm": True})
    assert len(smtp.messages) == 1

    other = email_job("9002")
    _bound_user(None)
    try:
        with app._connect() as connection:
            assert email_apply.claim(
                connection, other, recipient=APPLY_TO, subject="s", attachments=[]
            )  # a send that started and never finished (the process stopped)
    finally:
        app._unbind_user()
    status, body, _ = server.call(
        "POST", f"/api/jobs/{other}/apply-email", {"confirm": True}
    )
    assert status == 400 and "not confirmed" in body["error"]
    assert len(smtp.messages) == 1


def test_a_posting_with_an_application_form_is_not_emailed():
    plan = {"email_apply": True}
    form = {
        "description": EMAIL_DESCRIPTION,
        "application_url": "https://boards.greenhouse.io/acme/jobs/1",
    }
    assert app._email_target(form, plan) is None
    site = {**form, "application_url": "https://careers.acme.test/jobs/1"}
    assert app._email_target(site, plan).address == APPLY_TO
    assert app._email_target(site, {"email_apply": False}) is None
    assert app._email_target(site, None) is None, "Apply for me alone never emails"
    mailto = {**form, "application_url": "mailto:jobs@acme.test"}
    assert app._email_target(mailto, plan).how == "mailto"


# --------------------------------------------------------------------------- Auto-apply


def email_posting(number: int) -> JobPosting:
    return JobPosting(
        source="lever",
        external_id=f"mail-{number}",
        source_url=f"https://careers.acme.test/jobs/{number}",
        application_url=f"https://careers.acme.test/jobs/{number}",
        title="Principal Cloud Architect",
        company="Acme",
        location="Remote, United States",
        workplace_type="remote",
        description=EMAIL_DESCRIPTION,
    )


def test_auto_apply_emails_a_posting_that_asks_for_it(server, smtp, monkeypatch):  # noqa: F811
    sign_up(server)
    set_up_mail(server, smtp)
    monkeypatch.setattr(BoardAdapter, "postings", [email_posting(1)])
    plan = make_plan(server, email_apply=True)
    assert plan["email_apply"] is True
    ok(server, "POST", f"/api/autopilot/plans/{plan['plan_id']}/run", {})
    overview = wait_idle(server)
    [run] = overview["recent"]
    assert run["status"] == "submitted" and run["route"] == "email", run
    assert run["evidence_document_id"]
    [delivered] = smtp.messages
    assert delivered["to"] == [APPLY_TO]
    job = ok(server, "GET", f"/api/jobs/{run['job_id']}")["job"]
    assert job["submission"]["method"] == "email" and job["stage"] == "applied"
    assert overview["status"]["sent_today"] == 1, (
        "an email application counts toward the daily limit"
    )
    ok(server, "POST", f"/api/autopilot/plans/{plan['plan_id']}/run", {})
    wait_idle(server)
    assert len(smtp.messages) == 1, "a job is emailed once"


def test_ask_me_first_prepares_the_email_and_sends_on_the_click(
    server, smtp, monkeypatch
):  # noqa: F811
    sign_up(server)
    set_up_mail(server, smtp)
    monkeypatch.setattr(BoardAdapter, "postings", [email_posting(2)])
    plan = make_plan(server, email_apply=True, mode="review")
    ok(server, "POST", f"/api/autopilot/plans/{plan['plan_id']}/run", {})
    [run] = wait_idle(server)["recent"]
    assert run["status"] == "ready" and run["route"] == "email", run
    assert smtp.messages == []
    ok(server, "POST", f"/api/autopilot/runs/{run['run_id']}/submit", {})
    [run] = wait_idle(server)["recent"]
    assert run["status"] == "submitted" and len(smtp.messages) == 1


def test_auto_apply_without_email_waits_in_needs_you(server, smtp, monkeypatch):  # noqa: F811
    sign_up(server)
    monkeypatch.setattr(BoardAdapter, "postings", [email_posting(3)])
    plan = make_plan(server, email_apply=True)
    ok(server, "POST", f"/api/autopilot/plans/{plan['plan_id']}/run", {})
    [run] = wait_idle(server)["recent"]
    assert run["status"] == "needs_review", run
    assert run["error"].startswith("Set up email to apply by email")
    assert smtp.messages == []
