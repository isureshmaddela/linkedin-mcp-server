from datetime import datetime, timezone

import pytest

from linkedin_mcp_server.job_sources import (
    JobPosting,
    JobSearchQuery,
    GreenhouseJobAdapter,
    GoogleJobsAdapter,
    LinkedInJobAdapter,
    canonicalize_job_url,
)


def test_job_posting_validates_urls_salary_and_timezone():
    with pytest.raises(ValueError, match="Source URL"):
        JobPosting(source="linkedin", external_id="1", source_url="/jobs/view/1/")
    with pytest.raises(ValueError, match="Minimum salary"):
        JobPosting(
            source="linkedin",
            external_id="1",
            source_url="https://www.linkedin.com/jobs/view/1/",
            salary_min=120_000,
            salary_max=100_000,
        )
    with pytest.raises(ValueError, match="timezone"):
        JobPosting(
            source="linkedin",
            external_id="1",
            source_url="https://www.linkedin.com/jobs/view/1/",
            posted_at=datetime(2026, 1, 1),
        )


def test_job_posting_fingerprint_and_json_values():
    job = JobPosting(
        source=" LINKEDIN ",
        external_id=" 42 ",
        source_url="https://www.linkedin.com/jobs/view/42/",
        title="AI Architect",
        company="Example Co",
        required_skills=("Python", "Architecture"),
        posted_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
    )

    serialized = job.to_dict()

    assert job.source == "linkedin"
    assert job.external_id == "42"
    assert job.content_fingerprint
    assert serialized["required_skills"] == ["Python", "Architecture"]
    assert serialized["posted_at"] == "2026-01-01T00:00:00+00:00"


def test_linkedin_adapter_normalizes_search_references_and_fallback_ids():
    result = LinkedInJobAdapter.normalize_search_result(
        {
            "job_ids": ["42", "99", "bad"],
            "references": {
                "search_results": [
                    {
                        "kind": "job",
                        "url": "/jobs/view/42/?tracking=search",
                        "text": "  AI   Architect  ",
                    },
                    {"kind": "company", "url": "/company/example/"},
                    {"kind": "job", "url": "https://example.com/jobs/view/77/"},
                ]
            },
            "section_errors": {"search_results": {"error_type": "partial"}},
        }
    )

    assert [job.external_id for job in result.jobs] == ["42", "99"]
    assert result.jobs[0].title == "AI Architect"
    assert result.jobs[0].source_url == "https://www.linkedin.com/jobs/view/42/"
    assert result.jobs[1].title == ""
    assert result.section_errors == {"search_results": {"error_type": "partial"}}


def test_linkedin_adapter_search_uses_query_and_declares_capabilities():
    calls = []

    def fake_search(tool_name, arguments):
        calls.append((tool_name, arguments))
        return {"job_ids": ["123"], "section_errors": {}}

    adapter = LinkedInJobAdapter(fake_search)
    result = adapter.search(
        JobSearchQuery(
            keywords="  cloud architect ",
            location="Remote",
            max_pages=2,
            filters={"work_type": "remote"},
        )
    )

    assert calls == [
        (
            "search_jobs",
            {
                "keywords": "cloud architect",
                "location": "Remote",
                "max_pages": 2,
                "work_type": "remote",
            },
        )
    ]
    assert result.jobs[0].external_id == "123"
    assert adapter.capabilities.search
    assert adapter.capabilities.normalize
    assert not adapter.capabilities.submit_application
    assert not adapter.capabilities.read_application_status


def test_linkedin_adapter_rejects_unsupported_filter_and_missing_search():
    adapter = LinkedInJobAdapter(lambda *_: {})
    with pytest.raises(ValueError, match="Unsupported LinkedIn job filters"):
        adapter.search(JobSearchQuery("engineer", filters={"salary": 100_000}))
    with pytest.raises(RuntimeError, match="not configured"):
        LinkedInJobAdapter().search(JobSearchQuery("engineer"))


def test_search_query_validates_input():
    with pytest.raises(ValueError, match="keywords"):
        JobSearchQuery(" ")
    with pytest.raises(ValueError, match="Maximum pages"):
        JobSearchQuery("engineer", max_pages=11)


def test_canonicalize_job_url_removes_tracking_and_host_aliases():
    assert (
        canonicalize_job_url(
            "https://WWW.Greenhouse.io/jobs/123/?gh_src=campaign#apply"
        )
        == "https://greenhouse.io/jobs/123"
    )
    assert (
        canonicalize_job_url(
            "https://boards.greenhouse.io/example?gh_jid=123&utm_source=campaign"
        )
        == "https://boards.greenhouse.io/example?gh_jid=123"
    )
    assert canonicalize_job_url("https://example.com") == "https://example.com/"


def test_canonicalize_job_url_rejects_non_http_urls():
    with pytest.raises(ValueError, match="HTTP"):
        canonicalize_job_url("javascript:alert(1)")


def test_greenhouse_adapter_search_normalizes_public_job_board_results():
    calls = []
    response = {
        "jobs": [
            {
                "id": 42,
                "title": "AI Architect",
                "absolute_url": "https://boards.greenhouse.io/acme/jobs/42",
                "company_name": "Acme",
                "location": {"name": "Remote - United States"},
                "content": "<h2>Responsibilities</h2><p>Design systems &amp; platforms</p><script>ignore()</script>",
                "first_published": "2026-08-01T10:00:00Z",
                "application_deadline": "2026-09-01T10:00:00Z",
                "departments": [{"name": "Engineering"}],
                "offices": [{"name": "Remote"}],
            },
            {
                "id": 43,
                "title": "Product Designer",
                "absolute_url": "https://boards.greenhouse.io/acme/jobs/43",
                "location": {"name": "New York"},
            },
            {"title": "Missing posting ID"},
        ]
    }

    def fake_get_json(url):
        calls.append(url)
        return response

    adapter = GreenhouseJobAdapter("acme", fake_get_json)
    result = adapter.search(
        JobSearchQuery(
            "AI Architect",
            location="Remote",
            filters={"department": "engineering"},
        )
    )

    assert calls == [
        "https://boards-api.greenhouse.io/v1/boards/acme/jobs?content=true"
    ]
    assert len(result.jobs) == 1
    job = result.jobs[0]
    assert (job.source, job.external_id, job.title, job.company) == (
        "greenhouse",
        "42",
        "AI Architect",
        "Acme",
    )
    assert job.location == "Remote - United States"
    assert job.source_url == job.application_url
    assert "Design systems & platforms" in job.description
    assert "ignore()" not in job.description
    assert job.posted_at.isoformat() == "2026-08-01T10:00:00+00:00"
    assert adapter.capabilities.search
    assert adapter.capabilities.resolve_application_link
    assert not adapter.capabilities.report_health
    assert not adapter.capabilities.submit_application
    assert not adapter.capabilities.capture_confirmation


def test_greenhouse_adapter_validates_board_token_and_filters():
    with pytest.raises(ValueError, match="board token"):
        GreenhouseJobAdapter("../acme")
    adapter = GreenhouseJobAdapter("acme", lambda _: {"jobs": []})
    with pytest.raises(ValueError, match="Unsupported Greenhouse job filters"):
        adapter.search(JobSearchQuery("engineer", filters={"salary": 100_000}))
    with pytest.raises(RuntimeError, match="invalid jobs list"):
        GreenhouseJobAdapter("acme", lambda _: {"jobs": {}}).search(
            JobSearchQuery("engineer")
        )


def test_greenhouse_adapter_decodes_entity_encoded_description_html():
    adapter = GreenhouseJobAdapter("acme", lambda _: {"jobs": []})

    job = adapter.normalize(
        {
            "id": 123,
            "title": "Platform Engineer",
            "absolute_url": "https://boards.greenhouse.io/acme/jobs/123",
            "content": "&amp;lt;p&amp;gt;Build reliable systems&amp;lt;/p&amp;gt;",
        }
    )

    assert job.description == "Build reliable systems"


def test_google_jobs_adapter_paginates_and_applies_explicit_filters():
    calls = []
    pages = [
        {
            "jobs_results": [
                {
                    "job_id": "google-1",
                    "title": "Cloud Engineer",
                    "company_name": "Example",
                    "location": "Seattle, WA",
                    "share_link": "https://www.google.com/search?ibp=htl;jobs&htidocid=1",
                    "description": "W-2 role building cloud infrastructure",
                    "detected_extensions": {
                        "work_from_home": True,
                        "schedule_type": "Full-time",
                        "salary": "$20 an hour",
                    },
                    "apply_options": [
                        {"title": "Company site", "link": "https://jobs.example.com/1"}
                    ],
                }
            ],
            "serpapi_pagination": {"next_page_token": "page-2"},
        },
        {
            "jobs_results": [
                {
                    "job_id": "google-2",
                    "title": "Platform Engineer",
                    "company_name": "Example",
                    "location": "Remote - US",
                    "share_link": "https://www.google.com/search?ibp=htl;jobs&htidocid=2",
                    "description": "W2 distributed platforms",
                    "detected_extensions": {
                        "work_from_home": True,
                        "schedule_type": "Full-time",
                    },
                },
                {
                    "job_id": "google-1099",
                    "title": "Cloud Engineer",
                    "company_name": "Example",
                    "location": "Remote - US",
                    "share_link": "https://www.google.com/search?ibp=htl;jobs&htidocid=3",
                    "description": "1099 project",
                    "detected_extensions": {
                        "work_from_home": True,
                        "schedule_type": "Full-time",
                    },
                },
                {
                    "job_id": "unknown-workplace",
                    "title": "Cloud Engineer",
                    "company_name": "Example",
                    "location": "Remote - US",
                    "share_link": "https://www.google.com/search?ibp=htl;jobs&htidocid=4",
                    "description": "W-2 role, workplace unknown",
                    "detected_extensions": {"schedule_type": "Full-time"},
                },
            ]
        },
    ]

    def fake_get_json(parameters):
        calls.append(dict(parameters))
        return pages[len(calls) - 1]

    adapter = GoogleJobsAdapter("test-serpapi-key", fake_get_json)
    result = adapter.search(
        JobSearchQuery(
            "Cloud Engineer",
            location="Seattle",
            max_pages=3,
            filters={"work_type": "remote", "job_type": "full_time", "w2_search": True},
        )
    )

    assert len(calls) == 2
    assert calls[0]["engine"] == "google_jobs"
    assert calls[0]["api_key"] == "test-serpapi-key"
    assert calls[0]["q"] == 'Cloud Engineer ("W-2" OR W2)'
    assert calls[0]["location"] == "Seattle"
    assert calls[1]["next_page_token"] == "page-2"
    assert all("start" not in parameters for parameters in calls)
    assert [job.external_id for job in result.jobs] == ["google-1", "google-2"]
    assert result.jobs[0].workplace_type == "remote"
    assert result.jobs[0].employment_type == "full_time"
    assert result.jobs[0].application_url == "https://jobs.example.com/1"
    assert result.jobs[0].salary_min is None
    assert result.jobs[0].salary_max is None


def test_google_jobs_adapter_marks_unknown_workplace_and_rejects_missing_key():
    with pytest.raises(ValueError, match="SERPAPI_API_KEY"):
        GoogleJobsAdapter("")
    adapter = GoogleJobsAdapter(
        "test-key",
        lambda _: {
            "jobs_results": [
                {
                    "job_id": "unknown",
                    "title": "Engineer",
                    "share_link": "https://www.google.com/search?ibp=htl;jobs",
                }
            ]
        },
    )
    result = adapter.search(
        JobSearchQuery("Engineer", filters={"work_type": "on_site"})
    )
    assert result.jobs == ()


def _serpapi_job_id(htidocid: str, uule: str = "w+CAIQICINVW5pdGVkIFN0YXRlcw") -> str:
    """Build a job_id the way SerpApi does: base64-encoded JSON with padding."""
    import base64
    import json as _json

    payload = {
        "job_title": "Senior Cloud Solutions Architect - Enterprise Platforms",
        "company_name": "Example Corporation",
        "address_city": "Seattle, WA",
        "htidocid": htidocid,
        "uule": uule,
        "hl": "en",
        "gl": "us",
    }
    return base64.b64encode(_json.dumps(payload).encode("utf-8")).decode("ascii")


def test_google_jobs_converts_serpapi_job_ids_to_storage_safe_ids():
    from auto_apply_assistant import EXTERNAL_JOB_ID_RE, JOB_KEY_RE

    adapter = GoogleJobsAdapter("test-key", json_getter=lambda _parameters: {})
    raw_id = _serpapi_job_id("5gdLcFw5H8gAAAAAAAAAAA==")
    assert not EXTERNAL_JOB_ID_RE.fullmatch(raw_id), (
        "fixture must be rejected as-is, like real SerpApi IDs"
    )
    padded_raw_id = _serpapi_job_id("x" * 11)
    assert "=" in padded_raw_id or len(padded_raw_id) > 180
    assert EXTERNAL_JOB_ID_RE.fullmatch(GoogleJobsAdapter.stable_job_id(padded_raw_id))

    record = {
        "job_id": raw_id,
        "title": "Cloud Architect",
        "company_name": "Example",
        "location": "Seattle, WA",
        "share_link": "https://www.google.com/search?ibp=htl;jobs&htidocid=abc",
    }
    job = adapter.normalize(record)

    assert EXTERNAL_JOB_ID_RE.fullmatch(job.external_id)
    assert JOB_KEY_RE.fullmatch(f"google_jobs:{job.external_id}")
    # Same Google document seen from a different search location keeps its ID.
    other_search = adapter.normalize(
        {
            **record,
            "job_id": _serpapi_job_id(
                "5gdLcFw5H8gAAAAAAAAAAA==", uule="w+CAIQICIGQ2FuYWRh"
            ),
        }
    )
    assert other_search.external_id == job.external_id
    # A different Google document gets a different ID.
    different = adapter.normalize(
        {**record, "job_id": _serpapi_job_id("ZZZZcFw5H8gAAAAAAAAAAA==")}
    )
    assert different.external_id != job.external_id
    # Undecodable long IDs are hashed; short safe IDs pass through unchanged.
    assert EXTERNAL_JOB_ID_RE.fullmatch(
        GoogleJobsAdapter.stable_job_id("not base64 at all / " * 20)
    )
    assert GoogleJobsAdapter.stable_job_id("google-1") == "google-1"
    assert GoogleJobsAdapter.stable_job_id("") == ""
