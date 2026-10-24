"""HTTP-level integration tests for the Aira Career Copilot API.

These start the real request handler on a loopback port with isolated SQLite
databases and walk through the critical user journeys without network access:
profile and answers, job import via a stubbed portal adapter, scoring, the
prepare/review/approve/submit workflow, email-driven interview creation,
calendar output, recruiter CRM, documents, analytics, and security controls.
"""

from __future__ import annotations

import base64
import http.cookiejar
import json
import threading
import urllib.error
import urllib.request
from datetime import datetime, timezone
from http.server import ThreadingHTTPServer

import pytest

import auto_apply_assistant as app
from linkedin_mcp_server.job_sources import JobPosting

RESUME = (
    "Jordan Example, Cloud Architect. 12 years designing AWS and Azure platforms, Kubernetes, Terraform, Python, "
    "microservices, event-driven architecture, enterprise architecture governance, TOGAF, stakeholder management. "
) * 3
PROFILE = {
    "first_name": "Jordan",
    "last_name": "Example",
    "email": "jordan@example.com",
    "phone": "+1 555 010 2030",
    "city": "Seattle",
    "state": "WA",
    "linkedin_url": "https://www.linkedin.com/in/jordan-example/",
    "years_of_experience": "12",
    "career_profile": "cloud_architect",
    "target_seniority": "principal",
    "work_authorization": "authorized",
    "sponsorship_required": "no",
}
POSTING = JobPosting(
    source="greenhouse",
    external_id="4242",
    source_url="https://boards.greenhouse.io/acme/jobs/4242",
    application_url="https://boards.greenhouse.io/acme/jobs/4242",
    title="Principal Cloud Architect",
    company="Acme",
    location="Remote, United States",
    workplace_type="remote",
    description=(
        "Requirements:\n- 10+ years of experience with AWS, Kubernetes, and Terraform.\n- Python and microservices.\n"
        "- Enterprise architecture and stakeholder management.\nNice to have:\n- Azure.\nWe are unable to sponsor visas at this time? No, we do offer visa sponsorship."
    ),
)


class Client:
    def __init__(self, base: str) -> None:
        self.base = base
        self.opener = urllib.request.build_opener(
            urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar())
        )

    def call(self, method: str, path: str, body=None, *, headers=None, raw=False):
        data = json.dumps(body).encode() if body is not None else None
        request = urllib.request.Request(self.base + path, data=data, method=method)
        request.add_header("Content-Type", "application/json")
        request.add_header("X-Requested-With", "aira")
        request.add_header("Origin", self.base)
        for key, value in (headers or {}).items():
            request.add_header(key, value)
        try:
            with self.opener.open(request, timeout=30) as response:
                status, payload, headers_out = (
                    response.status,
                    response.read(),
                    response.headers,
                )
        except urllib.error.HTTPError as error:
            status, payload, headers_out = error.code, error.read(), error.headers
        if raw:
            return status, payload, headers_out
        return (
            status,
            json.loads(payload.decode("utf-8")) if payload else {},
            headers_out,
        )


@pytest.fixture
def server(tmp_path, monkeypatch):
    monkeypatch.setattr(app, "DB_PATH", tmp_path / "applications.sqlite3")
    monkeypatch.setattr(app, "AUTH_DB_PATH", tmp_path / "accounts.sqlite3")
    monkeypatch.setattr(app, "USER_DB_ROOT", tmp_path / "accounts")
    monkeypatch.setattr(
        app,
        "_login_limiter",
        app.RateLimiter(limit=8, window_seconds=600, lockout_seconds=900),
    )
    monkeypatch.setattr(
        app, "_api_limiter", app.RateLimiter(limit=5000, window_seconds=60)
    )
    monkeypatch.setattr(app, "import_job_from_url", lambda url, **_: POSTING)
    monkeypatch.setattr(
        app,
        "fetch_greenhouse_questions",
        lambda *_args, **_kwargs: [
            {
                "label": "First Name",
                "required": True,
                "type": "text",
                "options": [],
                "field_name": "first_name",
            },
            {
                "label": "Email",
                "required": True,
                "type": "text",
                "options": [],
                "field_name": "email",
            },
            {
                "label": "Are you legally authorized to work in the United States?",
                "required": True,
                "type": "boolean",
                "options": ["Yes", "No"],
                "field_name": "q1",
            },
            {
                "label": "Favorite programming language",
                "required": False,
                "type": "text",
                "options": [],
                "field_name": "q2",
            },
        ],
    )
    monkeypatch.setattr(app, "_ollama_available", lambda: False)
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), app.RequestHandler)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    client = Client(f"http://127.0.0.1:{httpd.server_address[1]}")
    try:
        yield client
    finally:
        httpd.shutdown()
        httpd.server_close()


def register(client: Client) -> None:
    status, body, _ = client.call(
        "POST",
        "/api/auth/register",
        {"email": "copilot@example.com", "password": "a long enough password 123"},
    )
    assert status == 201, body
    status, body, _ = client.call(
        "POST",
        "/api/profile",
        {
            "resume": RESUME,
            "profile_url": PROFILE["linkedin_url"],
            "resume_name": "resume.txt",
            "application_profile": PROFILE,
            "matching_preferences": {
                "locations": ["Seattle", "Remote"],
                "workplace_types": ["remote"],
                "verified_skills": ["AWS", "Kubernetes", "Terraform"],
            },
        },
    )
    assert status == 200 and body["completeness"]["ready"], body


def import_job(client: Client) -> dict:
    status, body, _ = client.call(
        "POST",
        "/api/jobs/import",
        {"url": "https://boards.greenhouse.io/acme/jobs/4242"},
    )
    assert status == 201, body
    return body["job"]


def test_health_security_headers_and_auth_gate(server):
    status, body, headers = server.call("GET", "/api/health")
    assert status == 200 and body["status"] == "ok"
    assert headers.get("X-Request-ID")
    assert "script-src 'self'" in headers.get("Content-Security-Policy", "")
    assert headers.get("X-Frame-Options") == "DENY"
    status, body, _ = server.call("GET", "/api/dashboard")
    assert status == 401
    status, payload, _ = server.call("GET", "/static/app.js", raw=True)
    assert status == 200 and b"Aira" in payload
    status, _, _ = server.call("GET", "/static/missing.js", raw=True)
    assert status == 404


def test_cross_origin_mutations_are_rejected(server):
    register(server)
    status, body, _ = server.call(
        "POST",
        "/api/settings",
        {"daily_application_limit": 3},
        headers={"Origin": "https://evil.example", "Sec-Fetch-Site": "cross-site"},
    )
    assert status == 403
    status, body, _ = server.call(
        "POST", "/api/settings", {"daily_application_limit": 3}
    )
    assert status == 200 and body["settings"]["daily_application_limit"] == "3"


def test_login_rate_limit_and_logout(server):
    register(server)
    server.call("POST", "/api/auth/logout", {})
    assert server.call("GET", "/api/auth/me")[0] == 401
    statuses = [
        server.call(
            "POST",
            "/api/auth/login",
            {"email": "copilot@example.com", "password": "wrong password value"},
        )[0]
        for _ in range(9)
    ]
    assert statuses[0] == 400 and statuses[-1] == 429
    status, body, _ = server.call(
        "POST",
        "/api/auth/login",
        {"email": "other@example.com", "password": "a long enough password 123"},
    )
    assert status == 400


def test_import_scores_and_explains_job(server):
    register(server)
    job = import_job(server)
    assert job["job_id"] == "greenhouse:4242"
    assert job["match_score"] is not None and job["match_classification"]
    status, body, _ = server.call("GET", "/api/jobs/greenhouse:4242")
    detail = body["job"]
    dimensions = {d["name"]: d for d in detail["match_explanation"]["dimensions"]}
    assert dimensions["required_skills"]["score"] is not None
    assert any(
        e["source"] == "verified_skill"
        for e in dimensions["required_skills"]["evidence"]
    )
    assert "ready_to_apply" in detail["allowed_stages"]
    assert [e["new_status"] for e in detail["events"]] == ["discovered"]
    status, body, _ = server.call("GET", "/api/jobs?view=recommended")
    assert status == 200 and body["total"] in (0, 1)
    status, body, _ = server.call(
        "POST",
        "/api/jobs/import",
        {"url": "https://boards.greenhouse.io/acme/jobs/4242"},
    )
    assert body["job"]["job_id"] == "greenhouse:4242"
    status, body, _ = server.call("GET", "/api/jobs?view=portals")
    assert body["total"] == 1


def test_prepare_review_approve_submit_workflow(server):
    register(server)
    job_id = import_job(server)["job_id"]
    status, body, _ = server.call(
        "PATCH",
        f"/api/jobs/{job_id}/documents",
        {"resume": "Tailored " * 10, "cover_letter": "Letter " * 10},
    )
    assert status == 200 and body["job"]["resume_hash"]
    status, body, _ = server.call("POST", f"/api/jobs/{job_id}/prepare", {})
    assert status == 201, body
    prep = body["preparation"]
    assert prep["questions_source"] == "greenhouse_job_board"
    by_label = {f["label"]: f for f in prep["fields"]}
    assert (
        by_label["First Name"]["status"] == "prefilled"
        and by_label["First Name"]["value"] == "Jordan"
    )
    assert (
        by_label["Are you legally authorized to work in the United States?"][
            "sensitivity"
        ]
        == "sensitive"
    )
    assert (
        by_label["Are you legally authorized to work in the United States?"]["status"]
        == "needs_confirmation"
    )
    assert by_label["Favorite programming language"]["status"] in {"unknown", "missing"}
    status, body, _ = server.call(
        "POST", f"/api/preparations/{prep['prep_id']}/approve", {}
    )
    assert status == 400 and "sensitive" in body["error"].lower()
    status, body, _ = server.call(
        "PATCH",
        f"/api/preparations/{prep['prep_id']}",
        {
            "answers": {"q1": "Yes", "q2": "Python"},
            "notes": "ok",
            "remember_answers": True,
        },
    )
    assert status == 200 and body["preparation"]["status"] == "approved"
    status, body, _ = server.call(
        "POST", f"/api/preparations/{prep['prep_id']}/approve", {}
    )
    assert status == 200
    status, body, _ = server.call("GET", f"/api/jobs/{job_id}")
    assert body["job"]["stage"] == "ready_to_apply"
    status, body, _ = server.call("GET", "/api/profile/answers")
    remembered = {a["canonical_field"]: a for a in body["answers"]}
    assert remembered["work_authorization"]["confirmed"] is True
    status, body, _ = server.call("POST", f"/api/jobs/{job_id}/apply", {})
    assert status == 200 and body["job"]["stage"] == "applying"
    evidence = base64.b64encode(b"Confirmation #REQ-1").decode()
    status, body, _ = server.call(
        "POST",
        f"/api/jobs/{job_id}/submit",
        {
            "confirmation_number": "REQ-1",
            "evidence_base64": evidence,
            "evidence_filename": "confirmation.txt",
        },
    )
    assert (
        status == 200
        and body["job"]["stage"] == "applied"
        and body["submission"]["evidence_document_id"]
    )
    status, body, _ = server.call(
        "POST", f"/api/jobs/{job_id}/submit", {"confirmation_number": "REQ-1"}
    )
    assert body["submission"]["duplicate"] is True
    status, body, _ = server.call("GET", "/api/applications/queue")
    assert (
        body["submitted_today"] == 1
        and body["auto_submit"]["available"] is True
        and "Auto-apply" in body["auto_submit"]["reason"]
    )
    status, body, _ = server.call("GET", "/api/audit?limit=100")
    assert {
        "application.prepare",
        "application.review",
        "application.approve",
        "application.start",
        "application.submit",
    } <= {a["action"] for a in body["audit"]}


def test_daily_limit_blocks_new_applications(server, monkeypatch):
    register(server)
    first = import_job(server)["job_id"]
    status, body, _ = server.call(
        "POST", f"/api/jobs/{first}/submit", {"confirmation_number": "A-1"}
    )
    assert status == 200 and body["submission"]["duplicate"] is False
    second_posting = JobPosting(
        source="lever",
        external_id="abc",
        source_url="https://jobs.lever.co/acme/abc",
        title="Staff Engineer",
        company="Acme",
        location="Remote",
        description="Python and AWS. 8+ years of experience.",
    )
    monkeypatch.setattr(app, "import_job_from_url", lambda url, **_: second_posting)
    second = import_job(server)["job_id"]
    server.call("POST", "/api/settings", {"daily_application_limit": 1})
    status, body, _ = server.call("POST", f"/api/jobs/{second}/apply", {})
    assert status == 400 and "limit" in body["error"].lower(), body
    server.call("POST", "/api/settings", {"daily_application_limit": 0})
    status, body, _ = server.call("POST", f"/api/jobs/{second}/apply", {})
    assert status == 400 and "approve" in body["error"].lower(), (
        "0 means unlimited; the next guard is packet approval"
    )


def test_email_creates_interview_and_calendar_entry(server):
    register(server)
    job_id = import_job(server)["job_id"]
    server.call("PATCH", f"/api/jobs/{job_id}/stage", {"stage": "needs_review"})
    server.call(
        "PATCH",
        f"/api/jobs/{job_id}/documents",
        {"resume": "Tailored " * 10, "cover_letter": "Letter " * 10},
    )
    server.call("PATCH", f"/api/jobs/{job_id}/stage", {"stage": "ready_to_apply"})
    server.call("PATCH", f"/api/jobs/{job_id}/stage", {"stage": "applying"})
    server.call("PATCH", f"/api/jobs/{job_id}/stage", {"stage": "applied"})
    invite = {
        "subject": "Interview invitation - Principal Cloud Architect at Acme",
        "sender": "sam@acme.com",
        "sender_name": "Sam Recruiter",
        "body": "Hi Jordan,\n\nWe would like to schedule a technical interview for the Principal Cloud Architect position at Acme on Tuesday, November 10, 2026 at 2:00 PM PST.\nJoin: https://zoom.us/j/123456789\n\nBest,\nSam",
        "received_at": "2026-10-22T10:00:00Z",
    }
    status, body, _ = server.call("POST", "/api/email/import", invite)
    assert status == 201, body
    message = body["message"]
    assert message["category"] == "interview_invitation" and message["job_id"] == job_id
    assert message["details"]["start_at"].startswith("2026-11-10T14:00")
    status, body, _ = server.call("POST", "/api/email/import", invite)
    assert body["duplicate"] is True
    status, body, _ = server.call(
        "POST", f"/api/email/{message['message_id']}/apply", {"confirm": True}
    )
    assert status == 200 and body["applied"]["interview_id"]
    interview_id = body["applied"]["interview_id"]
    status, body, _ = server.call("GET", f"/api/jobs/{job_id}")
    assert body["job"]["stage"] == "technical_interview"
    status, body, _ = server.call("GET", f"/api/interviews/{interview_id}")
    assert "DTSTART;TZID=America/Los_Angeles:20261110T140000" in body["calendar"]["ics"]
    assert "20261110T220000Z" in body["calendar"]["google"]
    status, payload, headers = server.call(
        "GET", f"/api/interviews/{interview_id}/calendar.ics", raw=True
    )
    assert (
        status == 200
        and payload.startswith(b"BEGIN:VCALENDAR")
        and "text/calendar" in headers.get("Content-Type", "")
    )
    status, body, _ = server.call(
        "PATCH",
        f"/api/interviews/{interview_id}",
        {"start_at": "2026-11-11T15:00", "end_at": "2026-11-11T16:00"},
    )
    assert (
        body["interview"]["calendar_sequence"] == 1
        and body["interview"]["status"] == "rescheduled"
    )
    status, body, _ = server.call("GET", "/api/tasks")
    assert {t["kind"] for t in body["tasks"]} >= {"interview_prep", "follow_up"}
    status, body, _ = server.call(
        "POST",
        "/api/email/import",
        {
            "subject": "Your application",
            "sender": "noreply@greenhouse.io",
            "body": "Unfortunately we will not be moving forward with your application.",
        },
    )
    assert (
        body["message"]["category"] == "rejection"
        and body["message"]["suggested_action"]["requires_confirmation"] is True
    )
    status, body, _ = server.call("GET", "/api/jobs/" + job_id)
    assert body["job"]["stage"] == "technical_interview", (
        "rejection must not apply without confirmation"
    )


def test_recruiters_documents_and_analytics(server):
    register(server)
    job_id = import_job(server)["job_id"]
    status, body, _ = server.call(
        "POST",
        "/api/recruiters",
        {"name": "Sam Recruiter", "company": "Acme", "email": "sam@acme.com"},
    )
    assert status == 201
    recruiter_id = body["recruiter"]["recruiter_id"]
    status, body, _ = server.call(
        "POST", "/api/recruiters", {"name": "S. Recruiter", "email": "sam@acme.com"}
    )
    assert body["recruiter"]["recruiter_id"] == recruiter_id
    status, body, _ = server.call(
        "POST",
        f"/api/recruiters/{recruiter_id}/interactions",
        {
            "kind": "call",
            "direction": "inbound",
            "summary": "Intro call",
            "job_id": job_id,
            "follow_up_at": "2026-11-05T09:00:00Z",
        },
    )
    assert status == 201
    status, body, _ = server.call("GET", f"/api/recruiters/{recruiter_id}")
    assert body["recruiter"]["related_jobs"][0]["job_id"] == job_id
    pdf = base64.b64encode(b"%PDF-1.4\n%%EOF").decode()
    status, body, _ = server.call(
        "POST",
        "/api/documents",
        {
            "filename": "offer.pdf",
            "content_base64": pdf,
            "kind": "offer",
            "job_id": job_id,
        },
    )
    assert status == 201
    doc_id = body["document"]["document_id"]
    status, body, _ = server.call(
        "POST",
        "/api/documents",
        {"filename": "fake.png", "content_base64": pdf, "kind": "screenshot"},
    )
    assert status == 400
    status, payload, _ = server.call(
        "GET", f"/api/documents/{doc_id}/download", raw=True
    )
    assert status == 200 and payload.startswith(b"%PDF")
    status, body, _ = server.call("GET", f"/api/documents/{doc_id}/versions")
    assert any(a["action"] == "download" for a in body["audit"])
    status, body, _ = server.call("DELETE", f"/api/documents/{doc_id}")
    assert body["document"]["deleted_at"]
    status, body, _ = server.call("GET", "/api/analytics?days=30")
    assert (
        body["metrics"]["jobs_discovered"] == 1
        and body["metrics"]["interview_rate"] is None
    )
    assert body["definitions"]["interview_rate"]
    status, body, _ = server.call("GET", "/api/dashboard")
    assert body["counts"]["tracked"] == 1 and body["profile"]["ready"] is True
    status, payload, _ = server.call("GET", "/api/account/export", raw=True)
    assert {"jobs", "recruiters", "documents"} <= set(json.loads(payload)["tables"])


def test_settings_guardrails(server):
    register(server)
    assert server.call("POST", "/api/settings", {"auto_submit_enabled": True})[0] == 400
    assert server.call("POST", "/api/settings", {"timezone": "Mars/Olympus"})[0] == 400
    status, body, _ = server.call(
        "POST",
        "/api/settings",
        {"timezone": "America/Chicago", "email_confidence_threshold": 0.8},
    )
    assert (
        body["settings"]["timezone"] == "America/Chicago"
        and body["settings"]["email_confidence_threshold"] == "0.80"
    )
    status, body, _ = server.call("GET", "/api/settings")
    assert body["schema_version"] >= 8 and body["auto_submit"]["available"] is True


def test_account_deletion_removes_data(server, tmp_path):
    register(server)
    import_job(server)
    status, body, _ = server.call(
        "POST", "/api/account/delete", {"password": "wrong password value"}
    )
    assert status == 400
    status, body, _ = server.call(
        "POST", "/api/account/delete", {"password": "a long enough password 123"}
    )
    assert status == 200
    assert server.call("GET", "/api/auth/me")[0] == 401
    assert not (tmp_path / "applications.sqlite3").exists()


def test_missing_resources_answer_json_404(server):
    register(server)
    # Unmatched paths (malformed ids, unknown routes) use the JSON envelope, not the stdlib HTML page.
    for path in ("/api/interviews/not-a-valid-id", "/api/nope"):
        status, body, headers = server.call("GET", path)
        assert status == 404 and body["error"] == "Not found.", (path, body)
        assert headers.get("Content-Type", "").startswith("application/json")
    # A well-formed id that does not exist is 404 on every resource, never a 400.
    missing = "0" * 32
    for method, path, payload in (
        ("GET", f"/api/documents/{missing}/download", None),
        ("DELETE", f"/api/documents/{missing}", None),
        ("PATCH", f"/api/tasks/{missing}", {"status": "done"}),
        ("PATCH", f"/api/recruiters/{missing}", {"name": "Someone"}),
        (
            "POST",
            f"/api/recruiters/{missing}/interactions",
            {"kind": "note", "summary": "x"},
        ),
        ("PATCH", f"/api/interviews/{missing}", {"status": "completed"}),
        ("PATCH", f"/api/preparations/{missing}", {"answers": {}}),
        ("POST", f"/api/preparations/{missing}/approve", {}),
        ("PATCH", "/api/schedules/999999", {"enabled": False}),
        ("DELETE", "/api/schedules/999999", None),
        ("POST", "/api/schedules/999999/run", {}),
        ("PATCH", "/api/jobs/nosuch:1/stage", {"stage": "saved"}),
        ("PATCH", "/api/jobs/nosuch:1/documents", {"resume": "x", "cover_letter": "y"}),
        ("POST", "/api/jobs/nosuch:1/details", {}),
    ):
        status, body, _ = server.call(method, path, payload)
        assert status == 404 and "not found" in body["error"].lower(), (
            method,
            path,
            status,
            body,
        )


def test_reimport_restores_an_archived_job(server):
    register(server)
    job_id = import_job(server)["job_id"]
    assert server.call("POST", f"/api/jobs/{job_id}/archive", {})[0] == 200
    status, body, _ = server.call("GET", "/api/jobs?view=portals")
    assert body["total"] == 0
    # The user pasted the URL again, so the job must come back into the lists.
    assert import_job(server)["job_id"] == job_id
    status, body, _ = server.call("GET", "/api/jobs?view=portals")
    assert status == 200 and [job["job_id"] for job in body["jobs"]] == [job_id]
    status, body, _ = server.call("GET", "/api/audit?action=job.restore")
    assert status == 200 and body["audit"][0]["target_id"] == job_id


def test_document_reupload_chains_versions_and_deleted_download_is_404(server):
    register(server)
    first = {
        "filename": "notes.txt",
        "content_base64": base64.b64encode(b"first draft").decode(),
        "kind": "other",
    }
    status, body, _ = server.call("POST", "/api/documents", first)
    assert status == 201 and body["document"]["version"] == 1
    first_id = body["document"]["document_id"]
    status, body, _ = server.call(
        "POST",
        "/api/documents",
        {**first, "content_base64": base64.b64encode(b"second draft").decode()},
    )
    assert status == 201 and body["document"]["version"] == 2, body
    second_id = body["document"]["document_id"]
    status, body, _ = server.call("GET", f"/api/documents/{second_id}/versions")
    assert [v["document_id"] for v in body["versions"]] == [first_id, second_id]
    # A different kind, or the same file linked to a job, starts its own chain.
    status, body, _ = server.call(
        "POST", "/api/documents", {**first, "kind": "master_resume"}
    )
    assert status == 201 and body["document"]["version"] == 1
    status, _, _ = server.call("DELETE", f"/api/documents/{second_id}")
    assert status == 200
    status, body, _ = server.call("GET", f"/api/documents/{second_id}/download")
    assert status == 404 and "deleted" in body["error"].lower(), body


def test_score_returns_decoded_explanation(server):
    register(server)
    job_id = import_job(server)["job_id"]
    status, body, _ = server.call("POST", f"/api/jobs/{job_id}/score", {})
    assert status == 200
    job = body["job"]
    assert (
        isinstance(job["match_explanation"], dict)
        and job["match_explanation"]["dimensions"]
    )
    assert isinstance(job["match_evidence"], dict)
    assert isinstance(job["required_skills"], list)
    status, body, _ = server.call("GET", f"/api/jobs/{job_id}")
    assert (
        body["job"]["match_explanation"]["version"]
        == job["match_explanation"]["version"]
    )


def test_standard_answers_only_accept_canonical_fields(server):
    register(server)
    status, body, _ = server.call(
        "POST",
        "/api/profile/answers",
        {"canonical_field": "favourite_colour", "value": "green", "confirmed": True},
    )
    assert status == 400 and "Unknown standard answer field" in body["error"]
    status, body, _ = server.call(
        "POST",
        "/api/profile/answers",
        {
            "canonical_field": "work_authorization",
            "value": "Yes",
            "confirmed": True,
            "sensitivity": "sensitive",
        },
    )
    assert status == 200 and body["answer"]["confirmed"] is True
    assert server.call("DELETE", "/api/profile/answers/work_authorization")[0] == 200
    assert server.call("DELETE", "/api/profile/answers/work_authorization")[0] == 404


def test_schema_migrations_are_recorded(tmp_path, monkeypatch):
    monkeypatch.setattr(app, "DB_PATH", tmp_path / "fresh.sqlite3")
    with app._connect() as connection:
        versions = [
            row["version"]
            for row in connection.execute(
                "SELECT version FROM schema_migrations ORDER BY version"
            )
        ]
        tables = {
            row["name"]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            )
        }
    assert versions == list(range(1, app.LATEST_VERSION + 1))
    assert {
        "jobs",
        "interviews",
        "recruiters",
        "documents",
        "audit_logs",
        "application_submissions",
        "email_messages",
    } <= tables
    assert datetime.now(timezone.utc).year >= 2026
