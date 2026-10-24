import io
import json
import sqlite3
import zipfile

import pytest

import auto_apply_assistant as app
from linkedin_mcp_server.job_sources import JobPosting, JobSearchResult


def test_job_tracking_persists_documents_and_approval(tmp_path, monkeypatch):
    monkeypatch.setattr(app, "DB_PATH", tmp_path / "applications.sqlite3")
    app._save_profile_settings(
        "A long source resume with verified engineering experience. " * 3, ""
    )

    app._upsert_job("12345", title="Engineer")
    app._save_documents(
        "12345",
        "Tailored resume",
        "Cover letter",
        app._resume_hash(app._profile_settings()["resume"]),
    )
    app._set_stage("12345", "saved")
    job = app._set_stage("12345", "ready")

    assert job["title"] == "Engineer"
    assert job["resume_draft"] == "Tailored resume"
    assert job["cover_letter"] == "Cover letter"
    assert job["stage"] == "ready_to_apply"
    events = app._application_events("12345")
    assert [(event["previous_status"], event["new_status"]) for event in events] == [
        (None, "discovered"),
        ("discovered", "saved"),
        ("saved", "ready_to_apply"),
    ]
    assert all(event["correlation_id"] for event in events)


def test_application_events_endpoint_requires_auth_and_returns_history(
    tmp_path, monkeypatch
):
    monkeypatch.setattr(app, "DB_PATH", tmp_path / "applications.sqlite3")
    monkeypatch.setattr(app._request_context, "data_path", app.DB_PATH, raising=False)
    app._upsert_job("12345", title="Engineer")
    source_job = app._upsert_job(
        "12345",
        source="greenhouse",
        source_url="https://boards.greenhouse.io/example/jobs/12345",
        title="Greenhouse Engineer",
    )
    expected = app._application_events("12345")
    responses = []

    handler = object.__new__(app.RequestHandler)
    handler.path = "/api/jobs/12345/events"
    handler._request_user = lambda: {"user_id": "test-user"}
    handler._send_json = lambda status, payload: responses.append((status, payload))

    handler.do_GET()

    assert responses == [(200, {"events": expected})]

    responses.clear()
    handler.path = f"/api/jobs/{source_job['job_id']}/events"
    handler.do_GET()

    assert responses == [
        (200, {"events": app._application_events(source_job["job_id"])})
    ]

    responses.clear()
    handler = object.__new__(app.RequestHandler)
    handler.path = f"/api/jobs/{source_job['job_id']}/events"
    handler._request_user = lambda: None
    handler._send_json = lambda status, payload: responses.append((status, payload))

    handler.do_GET()

    assert responses == [(401, {"error": "Sign in to continue."})]


def test_namespaced_job_details_route_returns_unsupported_source_error(
    tmp_path, monkeypatch
):
    monkeypatch.setattr(app, "DB_PATH", tmp_path / "namespaced-detail-route.sqlite3")
    monkeypatch.setattr(app._request_context, "data_path", app.DB_PATH, raising=False)
    app._upsert_job(
        "role-1",
        source="greenhouse",
        source_url="https://boards.greenhouse.io/example/jobs/1",
        title="Engineer",
    )
    responses = []
    handler = object.__new__(app.RequestHandler)
    handler.path = "/api/jobs/greenhouse:role-1/details"
    handler._origin_is_local = lambda: True
    handler._request_user = lambda: {"user_id": "test-user"}
    handler._read_json = lambda: {}
    handler._send_json = lambda status, payload: responses.append((status, payload))
    handler.send_error = lambda status, *args: responses.append((status, {}))

    handler.do_POST()

    assert responses == [
        (400, {"error": "Job details are not available for this source yet."})
    ]

    responses.clear()
    handler.path = "/api/jobs/greenhouse:role-1/stage"
    handler._read_json = lambda: {"stage": "saved"}
    handler.do_PATCH()

    assert responses[0][0] == 200
    assert responses[0][1]["job"]["stage"] == "saved"


def test_greenhouse_search_endpoint_persists_source_aware_job(tmp_path, monkeypatch):
    monkeypatch.setattr(app, "DB_PATH", tmp_path / "greenhouse-search.sqlite3")
    normalized = JobPosting(
        source="greenhouse",
        external_id="123",
        source_url="https://boards.greenhouse.io/acme/jobs/123",
        application_url="https://boards.greenhouse.io/acme/jobs/123",
        title="Cloud Engineer",
        company="Acme",
        location="Remote",
        description="W-2 hourly work building infrastructure",
    )
    non_w2 = JobPosting(
        source="greenhouse",
        external_id="124",
        source_url="https://boards.greenhouse.io/acme/jobs/124",
        title="Cloud Engineer",
        company="Acme",
        location="Remote",
        description="1099 contract building infrastructure",
    )
    captured = {}

    class FakeAdapter:
        def __init__(self, board_token):
            captured["board_token"] = board_token

        def search(self, query):
            captured["query"] = query
            return JobSearchResult((normalized, non_w2))

    monkeypatch.setattr(app, "GreenhouseJobAdapter", FakeAdapter)
    responses = []
    handler = object.__new__(app.RequestHandler)
    handler.path = "/api/search/greenhouse"
    handler._origin_is_local = lambda: True
    handler._request_user = lambda: {"user_id": "test-user"}
    handler._read_json = lambda: {
        "board_token": "acme",
        "keywords": "cloud engineer",
        "location": "Remote",
        "w2_search": True,
    }
    handler._send_json = lambda status, payload: responses.append((status, payload))
    handler.send_error = lambda status, *args: responses.append((status, {}))

    handler.do_POST()

    job = app._job_row("greenhouse:123")
    assert responses[0][0] == 200
    assert responses[0][1]["count"] == 1
    assert captured["board_token"] == "acme"
    assert captured["query"].keywords == "cloud engineer"
    assert job["source"] == "greenhouse"
    assert job["external_id"] == "123"
    assert job["description"] == "W-2 hourly work building infrastructure"
    assert app._job_row("greenhouse:124") is None


@pytest.mark.parametrize("work_type", ["remote", "hybrid", "on_site"])
def test_linkedin_search_passes_arrangement_and_employment_filters(
    monkeypatch, work_type
):
    captured = {}

    def fake_mcp(name, arguments):
        captured["name"] = name
        captured["arguments"] = arguments
        return {"job_ids": []}

    monkeypatch.setattr(app, "_call_mcp", fake_mcp)

    result = app._search_jobs(
        "Cloud Architect",
        "Seattle",
        2,
        work_type=work_type,
        job_type="contract",
        w2_search=True,
    )

    assert captured["name"] == "search_jobs"
    assert captured["arguments"]["keywords"] == 'Cloud Architect ("W-2" OR W2)'
    assert captured["arguments"]["work_type"] == work_type
    assert captured["arguments"]["job_type"] == "contract"
    assert captured["arguments"]["location"] == "Seattle"
    assert result["count"] == 0


def test_linkedin_search_rejects_unknown_arrangement_and_employment_filters():
    with pytest.raises(ValueError, match="work arrangement"):
        app._search_jobs("Architect", "", 1, work_type="flexible")
    with pytest.raises(ValueError, match="employment type"):
        app._search_jobs("Architect", "", 1, job_type="w2")


def test_google_search_endpoint_uses_server_key_and_persists_jobs(
    tmp_path, monkeypatch
):
    monkeypatch.setattr(app, "DB_PATH", tmp_path / "google-search.sqlite3")
    monkeypatch.setenv("SERPAPI_API_KEY", "server-side-test-key")
    normalized = JobPosting(
        source="google_jobs",
        external_id="google-123",
        source_url="https://www.google.com/search?ibp=htl;jobs&htidocid=123",
        application_url="https://careers.example.com/jobs/123",
        title="Remote Cloud Engineer",
        company="Example Corp",
        location="Seattle, WA",
        workplace_type="remote",
        employment_type="full_time",
        description="W-2 Python cloud role",
    )
    captured = {}

    class FakeAdapter:
        def __init__(self, api_key):
            captured["api_key"] = api_key

        def search(self, query):
            captured["query"] = query
            return JobSearchResult((normalized,))

    monkeypatch.setattr(app, "GoogleJobsAdapter", FakeAdapter)
    responses = []
    handler = object.__new__(app.RequestHandler)
    handler.path = "/api/search/google"
    handler._origin_is_local = lambda: True
    handler._request_user = lambda: {"user_id": "test-user"}
    handler._read_json = lambda: {
        "keywords": "cloud engineer",
        "location": "Seattle",
        "max_pages": 2,
        "work_type": "remote",
        "job_type": "full_time",
        "w2_search": True,
    }
    handler._send_json = lambda status, payload: responses.append((status, payload))
    handler.send_error = lambda status, *args: responses.append((status, {}))

    handler.do_POST()

    job = app._job_row("google_jobs:google-123")
    assert responses[0][0] == 200
    assert responses[0][1]["count"] == 1
    assert captured["api_key"] == "server-side-test-key"
    assert captured["query"].max_pages == 2
    assert captured["query"].filters == {
        "w2_search": True,
        "work_type": "remote",
        "job_type": "full_time",
    }
    assert job["source"] == "google_jobs"
    assert job["workplace_type"] == "remote"
    assert job["application_url"] == "https://careers.example.com/jobs/123"


def test_google_search_requires_server_api_key(tmp_path, monkeypatch):
    monkeypatch.setattr(app, "DB_PATH", tmp_path / "google-no-key.sqlite3")
    monkeypatch.delenv("SERPAPI_API_KEY", raising=False)
    with pytest.raises(ValueError, match="SERPAPI_API_KEY"):
        app._search_google_jobs("engineer", "Seattle", 1)


def test_approval_requires_both_generated_documents(tmp_path, monkeypatch):
    monkeypatch.setattr(app, "DB_PATH", tmp_path / "applications.sqlite3")
    app._upsert_job("12345")

    with pytest.raises(ValueError, match="Generate both documents"):
        app._set_stage("12345", "ready")


def test_job_ids_and_stages_are_validated(tmp_path, monkeypatch):
    monkeypatch.setattr(app, "DB_PATH", tmp_path / "applications.sqlite3")

    with pytest.raises(ValueError, match="digits"):
        app._upsert_job("../../profile")
    app._upsert_job("12345")
    with pytest.raises(ValueError, match="Unknown application stage"):
        app._set_stage("12345", "submit")


def test_source_aware_job_upsert_is_idempotent_and_keeps_sources_distinct(
    tmp_path, monkeypatch
):
    monkeypatch.setattr(app, "DB_PATH", tmp_path / "source-jobs.sqlite3")

    linkedin = app._upsert_job("12345", title="LinkedIn role")
    greenhouse = app._upsert_job(
        "12345",
        source="greenhouse",
        source_url="https://boards.greenhouse.io/example/jobs/12345",
        content_fingerprint="fingerprint-a",
        title="Greenhouse role",
    )
    greenhouse_updated = app._upsert_job(
        "12345",
        source="greenhouse",
        source_url="https://boards.greenhouse.io/example/jobs/12345",
        content_fingerprint="fingerprint-b",
        title="Updated greenhouse role",
    )

    assert linkedin["job_id"] == "12345"
    assert greenhouse["job_id"] == "greenhouse:12345"
    assert greenhouse_updated["job_id"] == greenhouse["job_id"]
    assert greenhouse_updated["title"] == "Updated greenhouse role"
    assert greenhouse_updated["content_fingerprint"] == "fingerprint-b"
    assert [(job["source"], job["external_id"]) for job in app._list_jobs()] == [
        ("greenhouse", "12345"),
        ("linkedin", "12345"),
    ]


def test_job_upsert_merges_exact_canonical_urls_and_tracks_both_sources(
    tmp_path, monkeypatch
):
    monkeypatch.setattr(app, "DB_PATH", tmp_path / "deduplicated-jobs.sqlite3")
    linked_in = app._upsert_job("12345", title="Cloud Architect")

    imported = app._upsert_job(
        "role-99",
        source="greenhouse",
        source_url="https://WWW.Linkedin.com/jobs/view/12345/?utm_source=import#apply",
        title="Different provider label",
    )

    assert imported["job_id"] == linked_in["job_id"]
    assert len(app._list_jobs()) == 1
    assert app._list_jobs()[0]["sources"] == ["greenhouse", "linkedin"]


def test_linkedin_detail_fetch_uses_associated_record_for_ats_canonical_job(
    tmp_path, monkeypatch
):
    monkeypatch.setattr(app, "DB_PATH", tmp_path / "source-details.sqlite3")
    canonical = app._upsert_job(
        "gh-1",
        source="greenhouse",
        source_url="https://boards.greenhouse.io/example/jobs/123",
        title="Cloud Architect",
    )
    app._upsert_job(
        "12345",
        source="linkedin",
        source_url="https://boards.greenhouse.io/example/jobs/123?utm_source=linkedin",
        title="Cloud Architect",
    )
    requested = []

    def fake_mcp(name, arguments):
        requested.append((name, arguments))
        return {
            "sections": {"job_posting": "Example Corp\nCloud Architect\nBuild systems"}
        }

    monkeypatch.setattr(app, "_call_mcp", fake_mcp)

    updated = app._load_job_details(canonical["job_id"])

    assert requested == [("get_job_details", {"job_id": "12345"})]
    assert updated["job_id"] == canonical["job_id"]
    assert updated["description"] == "Example Corp\nCloud Architect\nBuild systems"


def test_job_upsert_uses_full_fingerprint_and_never_title_only(tmp_path, monkeypatch):
    monkeypatch.setattr(app, "DB_PATH", tmp_path / "fingerprint-jobs.sqlite3")
    duplicate = app._upsert_job(
        "gh-1",
        source="greenhouse",
        source_url="https://boards.greenhouse.io/example/jobs/1",
        title="Platform Engineer",
        company="Example Corp",
        location="Seattle",
        description="Build reliable systems",
        content_fingerprint="same-content",
    )
    matched = app._upsert_job(
        "lever-2",
        source="lever",
        source_url="https://jobs.lever.co/example/2",
        title=" platform engineer ",
        company=" example corp ",
        location=" SEATTLE ",
        description="Build reliable systems",
        content_fingerprint="same-content",
    )
    title_only = app._upsert_job(
        "ashby-3",
        source="ashby",
        source_url="https://jobs.ashbyhq.com/example/3",
        title="Platform Engineer",
    )

    assert matched["job_id"] == duplicate["job_id"]
    assert title_only["job_id"] != duplicate["job_id"]
    assert len(app._list_jobs()) == 2


def test_non_linkedin_job_requires_valid_source_url(tmp_path, monkeypatch):
    monkeypatch.setattr(app, "DB_PATH", tmp_path / "invalid-source-url.sqlite3")
    with pytest.raises(ValueError, match="absolute HTTP"):
        app._upsert_job("role-1", source="greenhouse", source_url="javascript:alert(1)")


def test_loading_job_details_generates_content_fingerprint(tmp_path, monkeypatch):
    monkeypatch.setattr(app, "DB_PATH", tmp_path / "job-details.sqlite3")
    app._upsert_job("12345", location="Seattle")
    monkeypatch.setattr(
        app,
        "_call_mcp",
        lambda *_: {
            "sections": {
                "job_posting": "Example Corp\nCloud Architect\nDesign distributed systems."
            },
            "references": {
                "job_posting": [
                    {"kind": "company", "text": "Example Corp 1,000 followers"}
                ]
            },
        },
    )

    job = app._load_job_details("12345")

    assert job["title"] == "Cloud Architect"
    assert job["company"] == "Example Corp"
    assert (
        job["description"]
        == "Example Corp\nCloud Architect\nDesign distributed systems."
    )
    assert job["content_fingerprint"]


def test_job_schema_migrates_existing_linkedin_rows(tmp_path, monkeypatch):
    database = tmp_path / "legacy-jobs.sqlite3"
    monkeypatch.setattr(app, "DB_PATH", database)
    with sqlite3.connect(database) as connection:
        connection.execute(
            """CREATE TABLE jobs (
                job_id TEXT PRIMARY KEY, title TEXT NOT NULL, company TEXT NOT NULL DEFAULT '',
                location TEXT NOT NULL DEFAULT '', job_url TEXT NOT NULL,
                description TEXT NOT NULL DEFAULT '', fit_score INTEGER,
                fit_summary TEXT NOT NULL DEFAULT '', strengths TEXT NOT NULL DEFAULT '[]',
                gaps TEXT NOT NULL DEFAULT '[]', resume_draft TEXT NOT NULL DEFAULT '',
                cover_letter TEXT NOT NULL DEFAULT '', stage TEXT NOT NULL DEFAULT 'discovered',
                updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            )"""
        )
        connection.execute(
            "INSERT INTO jobs (job_id, title, job_url) VALUES (?, ?, ?)",
            ("12345", "Legacy role", "https://www.linkedin.com/jobs/view/12345/"),
        )

    migrated = app._job_row("12345")

    assert migrated["source"] == "linkedin"
    assert migrated["external_id"] == "12345"
    assert migrated["content_fingerprint"] == ""
    assert migrated["application_url"] == migrated["job_url"]
    with app._connect() as connection:
        source_record = connection.execute(
            "SELECT source, external_id, job_id, application_url, canonical_url FROM job_source_records"
        ).fetchone()
    assert tuple(source_record) == (
        "linkedin",
        "12345",
        "12345",
        "https://www.linkedin.com/jobs/view/12345/",
        "https://linkedin.com/jobs/view/12345",
    )
    app._connect().close()
    with app._connect() as connection:
        assert (
            connection.execute("SELECT COUNT(*) FROM job_source_records").fetchone()[0]
            == 1
        )


def test_fit_review_is_saved_as_structured_data(tmp_path, monkeypatch):
    monkeypatch.setattr(app, "DB_PATH", tmp_path / "applications.sqlite3")
    app._upsert_job("12345")
    fit = {
        "score": 82,
        "summary": "Strong match",
        "strengths": ["Python"],
        "gaps": ["Kubernetes"],
    }

    job = app._save_fit("12345", fit)

    assert job["fit_score"] == 82
    assert job["fit_summary"] == "Strong match"
    assert json.loads(job["strengths"]) == ["Python"]
    assert json.loads(job["gaps"]) == ["Kubernetes"]
    assert app._application_events("12345")[-1]["new_status"] == "needs_review"


def test_fit_review_persists_versioned_keyword_evidence(tmp_path, monkeypatch):
    monkeypatch.setattr(app, "DB_PATH", tmp_path / "fit-evidence.sqlite3")
    resume = (
        "Senior cloud architect with Python and platform engineering experience. " * 3
    )
    app._save_profile_settings(
        resume,
        "",
        matching_preferences={
            "locations": ["Seattle"],
            "workplace_types": ["remote"],
            "verified_skills": ["Kubernetes"],
        },
    )
    app._upsert_job(
        "12345",
        title="Cloud Architect",
        location="Remote - Seattle, WA",
        workplace_type="remote",
        description="Build Python cloud platforms and distributed systems.",
    )
    monkeypatch.setattr(
        app,
        "_ollama_json",
        lambda *_: {
            "score": 82,
            "summary": "Strong match",
            "strengths": [],
            "gaps": [],
        },
    )

    job, fit = app._review_fit("12345", "test-model")

    evidence = json.loads(job["match_evidence"])
    assert job["fit_score"] == 82
    assert evidence["version"] == "lexical-evidence-v1"
    assert evidence["score_label"].startswith("Evidence combines exact-term coverage")
    dimensions = {dimension["name"]: dimension for dimension in evidence["dimensions"]}
    assert dimensions["location"]["score"] == 100
    assert dimensions["workplace"]["score"] == 100
    assert fit["match_evidence"] == evidence


def test_profile_settings_persist_locally_and_validate_link(tmp_path, monkeypatch):
    monkeypatch.setattr(app, "DB_PATH", tmp_path / "applications.sqlite3")
    profile_url = "https://www.linkedin.com/in/hyma-somireddy/"

    saved = app._save_profile_settings(
        "Resume content",
        profile_url,
        application_profile={
            "full_name": "Hyma Somireddy",
            "email": "hyma.bvrit@gmail.com",
        },
    )

    assert saved["resume"] == "Resume content"
    assert saved["profile_url"] == profile_url
    assert saved["application_profile"]["full_name"] == "Hyma Somireddy"
    assert saved["application_profile"]["email"] == "hyma.bvrit@gmail.com"
    assert app._profile_settings() == {
        "resume": "Resume content",
        "profile_url": profile_url,
        "application_profile": {
            "full_name": "Hyma Somireddy",
            "email": "hyma.bvrit@gmail.com",
            "phone": "",
            "location": "",
            "work_authorization": "",
            "sponsorship_required": "",
        },
        "matching_preferences": {
            "locations": [],
            "workplace_types": [],
            "verified_skills": [],
        },
    }

    with pytest.raises(ValueError, match="valid LinkedIn profile URL"):
        app._save_profile_settings("Resume content", "https://example.com/profile")


def test_application_profile_validates_contact_email_and_sponsorship(
    tmp_path, monkeypatch
):
    monkeypatch.setattr(app, "DB_PATH", tmp_path / "application-profile.sqlite3")

    with pytest.raises(ValueError, match="valid application contact email"):
        app._save_profile_settings(
            "Resume content",
            "",
            application_profile={"email": "not-an-email"},
        )
    with pytest.raises(ValueError, match="Sponsorship must be"):
        app._save_profile_settings(
            "Resume content",
            "",
            application_profile={"sponsorship_required": "maybe"},
        )


def test_matching_preferences_are_explicit_validated_and_persisted(
    tmp_path, monkeypatch
):
    monkeypatch.setattr(app, "DB_PATH", tmp_path / "matching-preferences.sqlite3")

    saved = app._save_profile_settings(
        "Resume content",
        "",
        matching_preferences={
            "locations": [" Seattle ", "Seattle", "Portland, OR"],
            "workplace_types": ["remote", "hybrid"],
            "verified_skills": ["Python", "Cloud architecture", "Python"],
        },
    )

    assert saved["matching_preferences"] == {
        "locations": ["Seattle", "Portland, OR"],
        "workplace_types": ["remote", "hybrid"],
        "verified_skills": ["Python", "Cloud architecture"],
    }
    with pytest.raises(ValueError, match="workplace preferences"):
        app._save_profile_settings(
            "Resume content",
            "",
            matching_preferences={"workplace_types": ["mostly_remote"]},
        )


def test_application_details_are_used_in_job_draft_context(tmp_path, monkeypatch):
    monkeypatch.setattr(app, "DB_PATH", tmp_path / "application-context.sqlite3")
    app._save_profile_settings(
        "Senior Salesforce developer with Apex and LWC experience. " * 3,
        "https://www.linkedin.com/in/example/",
        application_profile={
            "full_name": "Jordan Example",
            "email": "jordan@example.com",
        },
    )
    app._upsert_job(
        "67890", title="Salesforce Developer", description="Apex and Lightning role"
    )
    captured = {}

    def fake_ollama(model, prompt):
        captured["prompt"] = prompt
        return {"tailored_resume": "resume", "cover_letter": "letter"}

    monkeypatch.setattr(app, "_ollama_json", fake_ollama)
    app._draft_documents("67890", "local-model")

    assert "Jordan Example" in captured["prompt"]
    assert "jordan@example.com" in captured["prompt"]


def test_inbox_conversations_use_thread_references():
    result = {
        "references": {
            "inbox": [
                {
                    "kind": "conversation",
                    "url": "/messaging/thread/2-abc+123==/",
                    "text": "Alex Example",
                },
                {"kind": "person", "url": "/in/alex-example/", "text": "Alex Example"},
            ]
        }
    }

    assert app._inbox_conversations(result) == [
        {"thread_id": "2-abc+123==", "participant": "Alex Example"}
    ]


def test_reply_draft_persists_locally_per_thread(tmp_path, monkeypatch):
    monkeypatch.setattr(app, "DB_PATH", tmp_path / "applications.sqlite3")

    draft = app._save_reply_draft(
        "2-abc_123", "Alex Example", "Thanks for reaching out."
    )

    assert draft == {
        "thread_id": "2-abc_123",
        "participant": "Alex Example",
        "reply": "Thanks for reaching out.",
    }
    with app._connect() as connection:
        row = connection.execute(
            "SELECT participant, reply FROM message_drafts WHERE thread_id = ?",
            ("2-abc_123",),
        ).fetchone()
    assert tuple(row) == ("Alex Example", "Thanks for reaching out.")


def test_app_accounts_hash_passwords_and_isolate_data(tmp_path, monkeypatch):
    monkeypatch.setattr(app, "DB_PATH", tmp_path / "legacy.sqlite3")
    monkeypatch.setattr(app, "AUTH_DB_PATH", tmp_path / "accounts.sqlite3")
    monkeypatch.setattr(app, "USER_DB_ROOT", tmp_path / "user-data")

    first_user, first_token = app._create_account(
        "owner@example.com", "a long app password 123"
    )
    second_user, second_token = app._create_account(
        "teammate@example.com", "another app password 456"
    )

    assert first_user["data_path"] == str(tmp_path / "legacy.sqlite3")
    assert second_user["data_path"] != first_user["data_path"]
    assert "a long app password 123" not in first_user["password_hash"]
    first_session = app._user_for_session(first_token)
    second_session = app._user_for_session(second_token)
    assert first_session is not None
    assert second_session is not None
    assert first_session["user_id"] == first_user["user_id"]
    assert second_session["user_id"] == second_user["user_id"]
    assert (
        app._authenticate("OWNER@example.com", "a long app password 123")[0]["user_id"]
        == first_user["user_id"]
    )
    with pytest.raises(ValueError, match="incorrect"):
        app._authenticate("owner@example.com", "wrong password")

    app._request_context.data_path = first_user["data_path"]
    app._upsert_job("12345", title="Private role")
    app._request_context.data_path = second_user["data_path"]
    assert app._list_jobs() == []
    del app._request_context.data_path


def test_schedule_limits_protect_the_linkedin_account(tmp_path, monkeypatch):
    monkeypatch.setattr(app, "DB_PATH", tmp_path / "schedules.sqlite3")

    with pytest.raises(ValueError, match="between 1 and"):
        app._create_schedule(
            {"kind": "jobs", "keywords": "salesforce", "interval_minutes": 0}
        )
    with pytest.raises(ValueError, match="1 to 5"):
        app._create_schedule(
            {
                "kind": "jobs",
                "keywords": "salesforce",
                "interval_minutes": 60,
                "max_items": 50,
            }
        )
    with pytest.raises(ValueError, match="keywords"):
        app._create_schedule({"kind": "jobs", "interval_minutes": 60})
    with pytest.raises(ValueError, match="jobs or messages"):
        app._create_schedule({"kind": "apply", "interval_minutes": 60})


def test_due_schedule_is_claimed_only_once(tmp_path, monkeypatch):
    monkeypatch.setattr(app, "DB_PATH", tmp_path / "schedules.sqlite3")
    schedule = app._create_schedule(
        {"kind": "jobs", "keywords": "salesforce", "interval_minutes": 60}
    )
    due = schedule["next_run_at"] + 1

    assert [item["id"] for item in app._claim_due_schedules(due)] == [schedule["id"]]
    assert app._claim_due_schedules(due) == []

    app._set_schedule_enabled(schedule["id"], False)
    assert app._claim_due_schedules(due + 7200) == []


def test_job_schedule_prepares_drafts_but_never_approves_or_applies(
    tmp_path, monkeypatch
):
    monkeypatch.setattr(app, "DB_PATH", tmp_path / "jobs.sqlite3")
    monkeypatch.setattr(app, "ITEM_DELAY_SECONDS", 0)
    app._save_profile_settings(
        "Senior Salesforce developer resume with Apex and LWC. " * 3, ""
    )

    def fake_mcp(name, arguments):
        if name == "search_jobs":
            return {
                "job_ids": ["111", "222"],
                "references": {
                    "search_results": [
                        {
                            "kind": "job",
                            "url": "/jobs/view/111/",
                            "text": "Salesforce Developer",
                        },
                        {
                            "kind": "job",
                            "url": "/jobs/view/222/",
                            "text": "Unrelated Role",
                        },
                    ]
                },
            }
        return {
            "sections": {"job_posting": "Acme\nSalesforce Developer\nApex and LWC"},
            "references": {
                "job_posting": [{"kind": "company", "text": "Acme 100 followers"}]
            },
        }

    def fake_ollama(model, prompt):
        if "Compare this resume with the job description" in prompt:
            return {
                "score": 90
                if "Salesforce" in prompt.split("JOB DESCRIPTION")[1]
                else 20,
                "summary": "Fit",
                "strengths": [],
                "gaps": [],
            }
        return {"tailored_resume": "Tailored resume", "cover_letter": "Cover letter"}

    monkeypatch.setattr(app, "_call_mcp", fake_mcp)
    monkeypatch.setattr(app, "_ollama_json", fake_ollama)
    monkeypatch.setattr(
        app,
        "_load_job_details",
        lambda job_id: app._upsert_job(
            job_id,
            description="Salesforce Apex role"
            if job_id == "111"
            else "Warehouse forklift role",
        ),
    )

    summary = app._run_jobs_schedule(
        {
            "keywords": "salesforce",
            "location": "United States",
            "max_items": 3,
            "min_fit": 60,
        }
    )

    drafted = app._job_row("111")
    skipped = app._job_row("222")
    assert drafted is not None
    assert skipped is not None
    assert drafted["resume_draft"] == "Tailored resume"
    assert drafted["stage"] == "needs_review"
    assert skipped["resume_draft"] == ""
    assert {drafted["stage"], skipped["stage"]}.isdisjoint(
        {"ready_to_apply", "applied"}
    )
    assert "1 drafted for your approval" in summary
    assert "1 below 60% fit" in summary


def test_one_minute_schedules_wait_after_each_run_and_back_off_on_errors(
    tmp_path, monkeypatch
):
    monkeypatch.setattr(app, "DB_PATH", tmp_path / "frequent.sqlite3")
    jobs = app._create_schedule(
        {"kind": "jobs", "keywords": "salesforce", "interval_minutes": 1}
    )
    messages = app._create_schedule({"kind": "messages", "interval_minutes": 1})
    monkeypatch.setattr(app, "_run_jobs_schedule", lambda item: "done")
    monkeypatch.setattr(
        app,
        "_run_messages_schedule",
        lambda item: (_ for _ in ()).throw(RuntimeError("blocked")),
    )

    app._execute_schedule(jobs)
    app._execute_schedule(messages)

    saved = {item["id"]: item for item in app._list_schedules()}
    assert saved[jobs["id"]]["next_run_at"] - saved[jobs["id"]]["last_run_at"] == 60
    assert (
        saved[messages["id"]]["next_run_at"] - saved[messages["id"]]["last_run_at"]
        == app.ERROR_BACKOFF_SECONDS
    )


def test_job_schedule_requires_a_saved_resume(tmp_path, monkeypatch):
    monkeypatch.setattr(app, "DB_PATH", tmp_path / "jobs.sqlite3")

    with pytest.raises(ValueError, match="Add your resume"):
        app._run_jobs_schedule(
            {
                "keywords": "salesforce",
                "location": "United States",
                "max_items": 1,
                "min_fit": 60,
            }
        )


def test_message_schedule_drafts_only_threads_that_need_a_reply(tmp_path, monkeypatch):
    monkeypatch.setattr(app, "DB_PATH", tmp_path / "messages.sqlite3")
    monkeypatch.setattr(app, "ITEM_DELAY_SECONDS", 0)
    calls = []
    generated = []

    def fake_mcp(name, arguments):
        if name == "get_inbox":
            return {
                "references": {
                    "inbox": [
                        {
                            "kind": "conversation",
                            "url": "/messaging/thread/needs-reply/",
                            "text": "Recruiter",
                        },
                        {
                            "kind": "conversation",
                            "url": "/messaging/thread/you-spoke-last/",
                            "text": "Friend",
                        },
                    ]
                }
            }
        if name == "get_conversation":
            calls.append(arguments["thread_id"])
            return {
                "sections": {"conversation": f"Conversation {arguments['thread_id']}"},
                "references": {
                    "conversation": [{"kind": "person", "text": "Alex Example"}]
                },
            }
        return {
            "sections": {"conversation": f"Conversation {arguments['thread_id']}"},
            "references": {
                "conversation": [{"kind": "person", "text": "Alex Example"}]
            },
        }

    monkeypatch.setattr(app, "_call_mcp", fake_mcp)

    def fake_ollama(model, prompt):
        generated.append(prompt)
        return {
            "reply": "Thanks for reaching out.",
            "needs_reply": "needs-reply" in prompt,
        }

    monkeypatch.setattr(app, "_ollama_json", fake_ollama)

    summary = app._run_messages_schedule({"max_items": 5})

    draft = app._message_draft("needs-reply")
    assert draft is not None
    assert draft["reply"] == "Thanks for reaching out."
    assert app._message_draft("you-spoke-last") is None
    assert "1 reply drafts for your approval" in summary
    assert "1 need no reply" in summary
    assert "0 unchanged" in summary

    next_summary = app._run_messages_schedule({"max_items": 5})
    assert calls == ["needs-reply", "you-spoke-last", "needs-reply", "you-spoke-last"]
    assert len(generated) == 2
    assert "2 unchanged" in next_summary


def test_message_schedule_redrafts_when_a_thread_changes(tmp_path, monkeypatch):
    monkeypatch.setattr(app, "DB_PATH", tmp_path / "new-message.sqlite3")
    monkeypatch.setattr(app, "ITEM_DELAY_SECONDS", 0)
    latest = {"text": "Earlier conversation"}

    def fake_mcp(name, arguments):
        if name == "get_inbox":
            return {
                "references": {
                    "inbox": [
                        {
                            "kind": "conversation",
                            "url": "/messaging/thread/recruiter/",
                            "text": "Recruiter",
                        }
                    ]
                }
            }
        return {
            "sections": {"conversation": latest["text"]},
            "references": {"conversation": [{"kind": "person", "text": "Recruiter"}]},
        }

    monkeypatch.setattr(app, "_call_mcp", fake_mcp)
    monkeypatch.setattr(
        app,
        "_ollama_json",
        lambda model, prompt: {
            "reply": prompt.rsplit("):\n", 1)[-1],
            "needs_reply": True,
        },
    )

    app._run_messages_schedule({"max_items": 1})
    first_draft_record = app._message_draft("recruiter")
    assert first_draft_record is not None
    first_draft = first_draft_record["reply"]
    app._run_messages_schedule({"max_items": 1})
    unchanged_record = app._message_draft("recruiter")
    assert unchanged_record is not None
    assert unchanged_record["reply"] == first_draft

    latest["text"] = "New inbound message asking for availability"
    summary = app._run_messages_schedule({"max_items": 1})

    assert "1 reply drafts for your approval" in summary
    updated_draft = app._message_draft("recruiter")
    assert updated_draft is not None
    assert updated_draft["reply"] == latest["text"]


def test_inbox_loader_retries_a_transient_empty_first_result(tmp_path, monkeypatch):
    monkeypatch.setattr(app, "DB_PATH", tmp_path / "inbox-retry.sqlite3")
    calls = []

    def fake_mcp(name, arguments):
        calls.append(name)
        if len(calls) == 1:
            return {"sections": {}, "references": {}}
        return {
            "sections": {"inbox": "Recruiter"},
            "references": {
                "inbox": [
                    {
                        "kind": "conversation",
                        "url": "/messaging/thread/recruiter/",
                        "text": "Recruiter",
                    }
                ]
            },
        }

    monkeypatch.setattr(app, "_call_mcp", fake_mcp)
    monkeypatch.setattr(app.time, "sleep", lambda seconds: None)

    conversations, inbox_text = app._load_inbox()

    assert len(calls) == 2
    assert inbox_text == "Recruiter"
    assert conversations == [{"thread_id": "recruiter", "participant": "Recruiter"}]


def test_inbox_loader_retries_when_text_loads_before_thread_references(
    tmp_path, monkeypatch
):
    monkeypatch.setattr(app, "DB_PATH", tmp_path / "inbox-references.sqlite3")
    calls = []

    def fake_mcp(name, arguments):
        calls.append(name)
        if len(calls) == 1:
            return {"sections": {"inbox": "Recruiter\nNew message"}, "references": {}}
        return {
            "sections": {"inbox": "Recruiter\nNew message"},
            "references": {
                "inbox": [
                    {
                        "kind": "conversation",
                        "url": "/messaging/thread/recruiter/",
                        "text": "Recruiter",
                    }
                ]
            },
        }

    monkeypatch.setattr(app, "_call_mcp", fake_mcp)
    monkeypatch.setattr(app.time, "sleep", lambda seconds: None)

    conversations, inbox_text = app._load_inbox()

    assert len(calls) == 2
    assert inbox_text == "Recruiter\nNew message"
    assert conversations == [{"thread_id": "recruiter", "participant": "Recruiter"}]


def test_schedule_run_is_logged_and_failures_do_not_crash(tmp_path, monkeypatch):
    monkeypatch.setattr(app, "DB_PATH", tmp_path / "runs.sqlite3")
    schedule = app._create_schedule({"kind": "messages", "interval_minutes": 30})
    monkeypatch.setattr(
        app,
        "_run_messages_schedule",
        lambda item: (_ for _ in ()).throw(RuntimeError("LinkedIn unavailable")),
    )

    result = app._execute_schedule(schedule)

    assert result == {"status": "error", "summary": "LinkedIn unavailable"}
    assert app._list_runs()[0]["status"] == "error"
    assert app._list_schedules()[0]["last_status"] == "error: LinkedIn unavailable"


def _docx_bytes(paragraphs):
    body = "".join(f"<w:p><w:r><w:t>{text}</w:t></w:r></w:p>" for text in paragraphs)
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr(
            "word/document.xml",
            f'<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body>{body}</w:body></w:document>',
        )
    return buffer.getvalue()


def _minimal_pdf(text):
    stream = f"BT /F1 12 Tf 72 720 Td ({text}) Tj ET".encode()
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Contents 4 0 R /Resources << /Font << /F1 5 0 R >> >> >>",
        b"<< /Length %d >>\nstream\n" % len(stream) + stream + b"\nendstream",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    ]
    output = b"%PDF-1.4\n"
    offsets = []
    for number, body in enumerate(objects, 1):
        offsets.append(len(output))
        output += b"%d 0 obj\n" % number + body + b"\nendobj\n"
    xref = len(output)
    output += b"xref\n0 %d\n0000000000 65535 f \n" % (len(objects) + 1)
    for offset in offsets:
        output += b"%010d 00000 n \n" % offset
    output += b"trailer\n<< /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF" % (
        len(objects) + 1,
        xref,
    )
    return output


def test_resume_state_tracks_version_name_and_updates(tmp_path, monkeypatch):
    monkeypatch.setattr(app, "DB_PATH", tmp_path / "resume.sqlite3")
    assert app._profile_state()["resume_hash"] == ""

    app._save_profile_settings("First resume text", "", "first.pdf")
    first = app._profile_state()
    app._save_profile_settings("First resume text", "", "renamed.pdf")
    unchanged = app._profile_state()
    app._save_profile_settings("Second resume text", "", "second.pdf")
    second = app._profile_state()

    assert first["resume_name"] == "first.pdf"
    assert unchanged["resume_hash"] == first["resume_hash"]
    assert unchanged["resume_name"] == "first.pdf"
    assert second["resume_hash"] != first["resume_hash"]
    assert second["resume_name"] == "second.pdf"


def test_drafts_from_an_older_resume_cannot_be_approved(tmp_path, monkeypatch):
    monkeypatch.setattr(app, "DB_PATH", tmp_path / "stale.sqlite3")
    monkeypatch.setattr(
        app,
        "_ollama_json",
        lambda model, prompt: {
            "tailored_resume": "New draft",
            "cover_letter": "New letter",
        },
    )
    app._save_profile_settings("Old resume with Apex and LWC experience. " * 3, "")
    app._upsert_job("12345", description="Salesforce role")
    app._draft_documents("12345", "model")
    assert app._set_stage("12345", "ready")["stage"] == "ready_to_apply"
    app._set_stage("12345", "needs_review")

    app._save_profile_settings(
        "New resume with Apex, LWC and Agentforce experience. " * 3, ""
    )
    with pytest.raises(ValueError, match="resume changed"):
        app._set_stage("12345", "ready")

    app._draft_documents("12345", "model")
    assert app._set_stage("12345", "ready")["stage"] == "ready_to_apply"


def test_existing_databases_gain_the_resume_hash_column(tmp_path, monkeypatch):
    path = tmp_path / "legacy.sqlite3"
    connection = sqlite3.connect(path)
    connection.execute(
        "CREATE TABLE jobs (job_id TEXT PRIMARY KEY, title TEXT NOT NULL, company TEXT NOT NULL DEFAULT '', "
        "location TEXT NOT NULL DEFAULT '', job_url TEXT NOT NULL, description TEXT NOT NULL DEFAULT '', "
        "fit_score INTEGER, fit_summary TEXT NOT NULL DEFAULT '', strengths TEXT NOT NULL DEFAULT '[]', "
        "gaps TEXT NOT NULL DEFAULT '[]', resume_draft TEXT NOT NULL DEFAULT '', cover_letter TEXT NOT NULL DEFAULT '', "
        "stage TEXT NOT NULL DEFAULT 'found', updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP)"
    )
    connection.execute(
        "INSERT INTO jobs (job_id, title, job_url) VALUES ('1', 'Old job', 'https://www.linkedin.com/jobs/view/1/')"
    )
    connection.commit()
    connection.close()
    monkeypatch.setattr(app, "DB_PATH", path)

    job = app._job_row("1")

    assert job is not None
    assert job["title"] == "Old job"
    assert job["resume_hash"] is None


def test_resume_text_is_extracted_from_txt_docx_and_pdf():
    line = "Senior Salesforce Developer with ten years of Apex, LWC and integration experience."

    assert app._extract_resume_text("resume.txt", line.encode()) == line
    docx = app._extract_resume_text(
        "resume.docx", _docx_bytes([line, "Second &amp; paragraph"])
    )
    assert docx.splitlines()[0] == line
    assert "Second & paragraph" in docx
    assert line in app._extract_resume_text("resume.pdf", _minimal_pdf(line))


def test_resume_upload_rejects_unsupported_or_unreadable_files():
    with pytest.raises(ValueError, match="PDF, DOCX, TXT"):
        app._extract_resume_text("resume.exe", b"x" * 200)
    with pytest.raises(ValueError, match="under 700 KB"):
        app._extract_resume_text("resume.txt", b"x" * 700_001)
    with pytest.raises(ValueError, match="valid DOCX"):
        app._extract_resume_text("resume.docx", b"not a zip")
    with pytest.raises(ValueError, match="Not enough text"):
        app._extract_resume_text("resume.txt", b"short")


def test_linkedin_operations_are_logged_with_outcome(tmp_path, monkeypatch):
    monkeypatch.setattr(app, "DB_PATH", tmp_path / "activity.sqlite3")

    async def fake_call(name, arguments):
        if name == "get_inbox":
            raise RuntimeError("LinkedIn unavailable")
        return {"job_ids": []}

    monkeypatch.setattr(app, "_call_mcp_async", fake_call)

    app._call_mcp(
        "search_jobs", {"keywords": "salesforce", "location": "United States"}
    )
    with pytest.raises(RuntimeError):
        app._call_mcp("get_inbox", {"limit": 20})

    newest, oldest = app._list_activity()
    assert (oldest["operation"], oldest["status"], oldest["target"]) == (
        "search_jobs",
        "ok",
        "salesforce in United States",
    )
    assert (newest["operation"], newest["status"], newest["detail"]) == (
        "get_inbox",
        "error",
        "LinkedIn unavailable",
    )


def test_scheduled_runs_are_logged_as_schedule_activity(tmp_path, monkeypatch):
    monkeypatch.setattr(app, "DB_PATH", tmp_path / "activity.sqlite3")
    schedule = app._create_schedule({"kind": "messages", "interval_minutes": 1})
    monkeypatch.setattr(app, "_run_messages_schedule", lambda item: "done")

    app._execute_schedule(schedule)

    entry = app._list_activity()[0]
    assert (entry["operation"], entry["source"], entry["status"]) == (
        "schedule_messages",
        "schedule",
        "ok",
    )


def test_google_search_skips_unsavable_records_instead_of_failing(
    tmp_path, monkeypatch
):
    monkeypatch.setattr(app, "DB_PATH", tmp_path / "google-partial.sqlite3")
    monkeypatch.setenv("SERPAPI_API_KEY", "server-side-test-key")
    good = JobPosting(
        source="google_jobs",
        external_id="g-good",
        source_url="https://www.google.com/search?ibp=htl;jobs&htidocid=good",
        title="Cloud Architect",
        company="Example",
        location="Remote",
        description="AWS architecture role",
    )
    bad = JobPosting(
        source="google_jobs",
        external_id="has spaces and = signs",
        source_url="https://www.google.com/search?ibp=htl;jobs&htidocid=bad",
        title="Broken record",
    )

    class FakeAdapter:
        def __init__(self, api_key):
            pass

        def search(self, query):
            return JobSearchResult((bad, good))

    monkeypatch.setattr(app, "GoogleJobsAdapter", FakeAdapter)

    result = app._search_google_jobs("cloud architect", "United States", 1)

    assert result["count"] == 1
    assert result["jobs"][0]["job_id"] == "google_jobs:g-good"
    assert result["section_errors"]["import"]["skipped"] == 1
    assert result["section_errors"]["import"]["examples"][0]["title"] == "Broken record"
