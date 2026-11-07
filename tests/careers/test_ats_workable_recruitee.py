"""Workable and Recruitee board adapters: normalizing, search, single-job import, health and portal detection.

Every test serves the provider's JSON from an injected getter or from a stand-in HTTP server on loopback; nothing
here reaches the network.
"""

from __future__ import annotations

import json
import threading
from collections.abc import Callable, Iterator
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any
from urllib.parse import urlsplit

import pytest

from aira import autopilot
from aira.ats import (
    PortalReference,
    RecruiteeJobAdapter,
    WorkableJobAdapter,
    _get_json,
    build_adapter,
    detect_portal,
    find_source,
    import_job_from_url,
)
from linkedin_mcp_server.job_sources import JobSearchQuery

WORKABLE_FEED_URL = "https://www.workable.com/api/accounts/acme?details=true"
RECRUITEE_OFFERS_URL = "https://acme.recruitee.com/api/offers/"

WORKABLE_FEED: dict[str, Any] = {
    "name": "Acme Robotics",
    "description": "We build robots.",
    "jobs": [
        {
            "title": "  Senior   Platform Engineer ",
            "shortcode": "A1B2C3D4E5",
            "code": "ENG-1",
            "employment_type": "Full-time",
            "telecommuting": True,
            "department": "Engineering",
            "function": "Infrastructure",
            "industry": "Robotics",
            "url": "https://apply.workable.com/j/A1B2C3D4E5",
            "shortlink": "https://apply.workable.com/j/A1B2C3D4E5",
            "application_url": "https://apply.workable.com/j/A1B2C3D4E5/apply",
            "published_on": "2026-02-10",
            "created_at": "2026-02-01",
            "country": "India",
            "city": "Bengaluru",
            "state": "Karnataka",
            "experience": "Mid-Senior level",
            "locations": [
                {
                    "country": "India",
                    "countryCode": "IN",
                    "city": "Bengaluru",
                    "region": "Karnataka",
                    "hidden": False,
                },
                {
                    "country": "India",
                    "countryCode": "IN",
                    "city": "Pune",
                    "region": "Maharashtra",
                    "hidden": False,
                },
                {"country": "Secret", "city": "Hidden", "hidden": True},
            ],
            "description": "<p>Run Kubernetes &amp; Terraform at scale.</p>",
            "requirements": "<ul><li>5 years with Python</li></ul>",
            "benefits": "<p>Health cover</p>",
            "salary": {
                "salary_from": 3000000,
                "salary_to": 2400000,
                "salary_currency": "inr",
            },
        },
        {
            "title": "Product Designer",
            "shortcode": "F6E5D4C3B2",
            "employment_type": "Contract",
            "telecommuting": False,
            "workplace": "hybrid",
            "department": "Design",
            "published_on": "not a date",
            "country": "Germany",
            "city": "Berlin",
            "description": "<p>Craft interfaces at scale.</p>",
        },
        {
            "title": "Duplicate designer",
            "shortcode": "F6E5D4C3B2",
            "country": "Germany",
        },
        {"title": "No shortcode"},
        "not a record",
    ],
}

RECRUITEE_OFFERS: dict[str, Any] = {
    "offers": [
        {
            "id": 914211,
            "slug": "backend-engineer",
            "title": " Backend Engineer ",
            "status": "published",
            "company_name": "Acme BV",
            "department": "Engineering",
            "tags": ["golang", "payments"],
            "description": "<p>Build payment services in Go.</p>",
            "requirements": "<p>PostgreSQL &amp; Kubernetes</p>",
            "location": "Amsterdam, Netherlands",
            "city": "Amsterdam",
            "country": "Netherlands",
            "state_name": "North Holland",
            "remote": False,
            "hybrid": True,
            "on_site": False,
            "employment_type_code": "fulltime_permanent",
            "experience_code": "mid_level",
            "careers_url": "https://acme.recruitee.com/o/backend-engineer",
            "careers_apply_url": "https://acme.recruitee.com/o/backend-engineer/c/new",
            "published_at": "2026-03-04 09:30:00 UTC",
            "salary": {"min": "60000", "max": "80000", "currency": "EUR"},
            "locations": [{"name": "Rotterdam, Netherlands"}],
        },
        {
            "id": 914212,
            "slug": "data-intern",
            "title": "Data Intern",
            "status": "published",
            "department": "Data",
            "description": "<p>Learn SQL and build dashboards.</p>",
            "city": "Utrecht",
            "country": "Netherlands",
            "remote": True,
            "employment_type_code": "internship",
            "published_at": "2026-03-05T10:00:00Z",
        },
        {
            "id": 914213,
            "slug": "old-role",
            "title": "Archived Backend Engineer",
            "status": "closed",
            "description": "<p>Build things that are gone</p>",
        },
        {"title": "No identifiers"},
        7,
    ]
}


class _Recorder:
    """Serves canned JSON by URL and records every request."""

    def __init__(self, responses: dict[str, Any]) -> None:
        self.responses = dict(responses)
        self.calls: list[str] = []

    def __call__(self, url: str) -> Any:
        self.calls.append(url)
        if url not in self.responses:
            raise AssertionError(f"Unexpected URL requested: {url}")
        payload = self.responses[url]
        if isinstance(payload, Exception):
            raise payload
        return payload


# --------------------------------------------------------------------------- #
# Workable
# --------------------------------------------------------------------------- #


def test_workable_normalizes_every_mapped_field():
    adapter = WorkableJobAdapter("Acme", _Recorder({WORKABLE_FEED_URL: WORKABLE_FEED}))
    assert adapter.account == "acme"

    result = adapter.search(JobSearchQuery(keywords="platform"))
    assert len(result.jobs) == 1
    job = result.jobs[0]
    assert job.source == "workable"
    assert job.external_id == "A1B2C3D4E5"
    assert job.title == "Senior Platform Engineer"
    assert job.company == "Acme Robotics"
    assert job.source_url == "https://apply.workable.com/j/A1B2C3D4E5"
    assert job.application_url == "https://apply.workable.com/j/A1B2C3D4E5/apply"
    assert job.location == "Bengaluru, Karnataka, India"
    assert job.workplace_type == "remote"
    assert job.employment_type == "full_time"
    assert (job.salary_min, job.salary_max, job.currency) == (2400000, 3000000, "INR")
    assert job.seniority == "mid-senior level"
    assert job.posted_at == datetime(2026, 2, 10, tzinfo=timezone.utc)
    assert job.application_method == "workable_jobs_feed"
    assert job.description == (
        "Run Kubernetes & Terraform at scale.\n\n"
        "## Requirements\n5 years with Python\n\n"
        "## Benefits\nHealth cover"
    )


def test_workable_sparse_records_fall_back_to_the_account_and_public_url():
    adapter = WorkableJobAdapter("acme", lambda _url: {"jobs": []})
    job = adapter.normalize(WORKABLE_FEED["jobs"][1])
    assert job.company == "acme"  # no feed loaded yet: the subdomain stands in
    assert job.source_url == "https://apply.workable.com/acme/j/F6E5D4C3B2/"
    assert job.application_url == job.source_url
    assert job.workplace_type == "hybrid"
    assert job.employment_type == "contract"
    assert job.posted_at is None
    with pytest.raises(ValueError, match="no shortcode"):
        adapter.normalize({"title": "x"})


def test_workable_search_matches_locally_and_skips_bad_records():
    adapter = WorkableJobAdapter("acme", _Recorder({WORKABLE_FEED_URL: WORKABLE_FEED}))

    every = adapter.search(JobSearchQuery(keywords="scale"))
    assert [job.external_id for job in every.jobs] == ["A1B2C3D4E5", "F6E5D4C3B2"]
    # Keywords match the department, function and industry too.
    assert [j.external_id for j in adapter.search(JobSearchQuery("robotics")).jobs] == [
        "A1B2C3D4E5"
    ]
    # Every listed location counts; hidden ones do not.
    assert [
        j.external_id
        for j in adapter.search(JobSearchQuery("scale", location="pune")).jobs
    ] == ["A1B2C3D4E5"]
    assert adapter.search(JobSearchQuery("scale", location="hidden")).jobs == ()
    filtered = adapter.search(
        JobSearchQuery("scale", filters={"work_type": "hybrid", "job_type": "contract"})
    )
    assert [job.external_id for job in filtered.jobs] == ["F6E5D4C3B2"]
    with pytest.raises(ValueError, match="Unsupported Workable job filters"):
        adapter.search(JobSearchQuery("scale", filters={"salary": "1"}))


def test_workable_validates_account_shortcode_and_payload_shape():
    for bad in ("", "-acme", "acme corp", "a" * 101):
        with pytest.raises(ValueError, match="Workable account"):
            WorkableJobAdapter(bad)
    with pytest.raises(RuntimeError, match="invalid jobs feed"):
        WorkableJobAdapter("acme", lambda _url: []).search(JobSearchQuery("x"))
    with pytest.raises(RuntimeError, match="invalid jobs list"):
        WorkableJobAdapter("acme", lambda _url: {"jobs": {}}).search(
            JobSearchQuery("x")
        )
    with pytest.raises(ValueError, match="Workable job shortcode"):
        WorkableJobAdapter("acme", lambda _url: {"jobs": []}).fetch_job("../x")


def test_workable_fetch_job_reads_the_feed_and_reports_closed_jobs():
    recorder = _Recorder({WORKABLE_FEED_URL: WORKABLE_FEED})
    adapter = WorkableJobAdapter("acme", recorder)
    assert adapter.fetch_job("a1b2c3d4e5").title == "Senior Platform Engineer"
    with pytest.raises(RuntimeError, match="not on the Workable account 'acme'"):
        adapter.fetch_job("ZZZZ9999")
    assert recorder.calls == [WORKABLE_FEED_URL, WORKABLE_FEED_URL]


def test_workable_capabilities_and_health():
    adapter = WorkableJobAdapter("acme", _Recorder({WORKABLE_FEED_URL: WORKABLE_FEED}))
    assert adapter.capabilities.search and adapter.capabilities.report_health
    healthy = adapter.health()
    assert healthy["ok"] is True
    assert (
        healthy["message"]
        == "Workable account 'acme' responded with 4 published job(s)."
    )

    def broken(_url: str) -> Any:
        raise RuntimeError("www.workable.com returned HTTP 404 (not found).")

    failed = WorkableJobAdapter("acme", broken).health()
    assert failed["ok"] is False
    assert failed["source"] == "workable"
    assert failed["message"] == "www.workable.com returned HTTP 404 (not found)."


# --------------------------------------------------------------------------- #
# Recruitee
# --------------------------------------------------------------------------- #


def test_recruitee_normalizes_every_mapped_field():
    adapter = RecruiteeJobAdapter(
        "ACME", _Recorder({RECRUITEE_OFFERS_URL: RECRUITEE_OFFERS})
    )
    assert adapter.company == "acme"
    job = adapter.search(JobSearchQuery("backend")).jobs[0]
    assert job.source == "recruitee"
    assert job.external_id == "914211"
    assert job.title == "Backend Engineer"
    assert job.company == "Acme BV"
    assert job.source_url == "https://acme.recruitee.com/o/backend-engineer"
    assert job.application_url == "https://acme.recruitee.com/o/backend-engineer/c/new"
    assert job.location == "Amsterdam, Netherlands"
    assert job.workplace_type == "hybrid"
    assert job.employment_type == "full_time"
    assert job.seniority == "mid level"
    assert (job.salary_min, job.salary_max, job.currency) == (60000, 80000, "EUR")
    assert job.posted_at == datetime(2026, 3, 4, 9, 30, tzinfo=timezone.utc)
    assert job.application_method == "recruitee_careers_site_api"
    assert job.description == (
        "Build payment services in Go.\n\n## Requirements\nPostgreSQL & Kubernetes"
    )


def test_recruitee_sparse_records_and_employment_codes():
    adapter = RecruiteeJobAdapter("acme", lambda _url: {"offers": []})
    intern = adapter.normalize(RECRUITEE_OFFERS["offers"][1])
    assert intern.company == "acme"
    assert intern.source_url == "https://acme.recruitee.com/o/data-intern"
    assert intern.location == "Utrecht, Netherlands"
    assert intern.workplace_type == "remote"
    assert intern.employment_type == "internship"
    assert intern.posted_at == datetime(2026, 3, 5, 10, 0, tzinfo=timezone.utc)
    for code, expected in (
        ("parttime_fixed_term", "part_time"),
        ("freelance", "contract"),
        ("contract", "contract"),
        ("temporary", "temporary"),
        ("volunteer", None),
    ):
        record = {"id": 1, "title": "x", "employment_type_code": code}
        assert adapter.normalize(record).employment_type == expected, code
    slug_only = adapter.normalize({"slug": "role", "title": "Role"})
    assert slug_only.external_id == "role"
    with pytest.raises(ValueError, match="no ID"):
        adapter.normalize({"title": "x"})


def test_recruitee_search_skips_unpublished_and_matches_tags_locally():
    adapter = RecruiteeJobAdapter(
        "acme", _Recorder({RECRUITEE_OFFERS_URL: RECRUITEE_OFFERS})
    )
    every = adapter.search(JobSearchQuery("build"))
    assert [job.external_id for job in every.jobs] == ["914211", "914212"]
    assert [j.external_id for j in adapter.search(JobSearchQuery("payments")).jobs] == [
        "914211"
    ]
    assert [
        j.external_id
        for j in adapter.search(JobSearchQuery("build", location="rotterdam")).jobs
    ] == ["914211"]
    remote = adapter.search(JobSearchQuery("build", filters={"work_type": "remote"}))
    assert [job.external_id for job in remote.jobs] == ["914212"]
    with pytest.raises(ValueError, match="Unsupported Recruitee job filters"):
        adapter.search(JobSearchQuery("build", filters={"team": "x"}))


def test_recruitee_validates_company_offer_and_payload_shape():
    for bad in ("", "-acme", "acme.io", "a" * 64):
        with pytest.raises(ValueError, match="Recruitee company"):
            RecruiteeJobAdapter(bad)
    with pytest.raises(RuntimeError, match="invalid offers response"):
        RecruiteeJobAdapter("acme", lambda _url: []).search(JobSearchQuery("x"))
    with pytest.raises(RuntimeError, match="invalid offers list"):
        RecruiteeJobAdapter("acme", lambda _url: {"offers": None}).search(
            JobSearchQuery("x")
        )
    adapter = RecruiteeJobAdapter("acme", lambda _url: {"offer": []})
    with pytest.raises(ValueError, match="Recruitee offer ID or slug"):
        adapter.fetch_job("../../admin")
    with pytest.raises(RuntimeError, match="invalid offer response"):
        adapter.fetch_job("backend-engineer")


def test_recruitee_fetch_job_takes_an_id_or_a_slug():
    offer = RECRUITEE_OFFERS["offers"][0]
    recorder = _Recorder(
        {
            f"{RECRUITEE_OFFERS_URL}backend-engineer": {"offer": offer},
            f"{RECRUITEE_OFFERS_URL}914211": {"offer": offer},
        }
    )
    adapter = RecruiteeJobAdapter("acme", recorder)
    assert adapter.fetch_job("backend-engineer").external_id == "914211"
    assert adapter.fetch_job("914211").title == "Backend Engineer"
    assert recorder.calls == [
        f"{RECRUITEE_OFFERS_URL}backend-engineer",
        f"{RECRUITEE_OFFERS_URL}914211",
    ]


def test_recruitee_capabilities_and_health():
    adapter = RecruiteeJobAdapter(
        "acme", _Recorder({RECRUITEE_OFFERS_URL: RECRUITEE_OFFERS})
    )
    healthy = adapter.health()
    assert healthy["ok"] is True
    assert (
        healthy["message"]
        == "Recruitee company 'acme' responded with 4 published offer(s)."
    )

    def broken(_url: str) -> Any:
        raise RuntimeError("Request to acme.recruitee.com timed out.")

    failed = RecruiteeJobAdapter("acme", broken).health()
    assert (failed["ok"], failed["source"]) == (False, "recruitee")
    assert failed["message"] == "Request to acme.recruitee.com timed out."


# --------------------------------------------------------------------------- #
# Catalog, factory, portal detection and import
# --------------------------------------------------------------------------- #


def test_catalog_factory_and_board_plans_know_both_sources():
    for source, label, cls in (
        ("workable", "Workable", WorkableJobAdapter),
        ("recruitee", "Recruitee", RecruiteeJobAdapter),
    ):
        descriptor = find_source(source)
        assert descriptor is not None
        assert (descriptor.label, descriptor.group, descriptor.status) == (
            label,
            "ats",
            "available",
        )
        assert descriptor.identifier_label and descriptor.identifier_help
        adapter = build_adapter(source, "acme")
        assert isinstance(adapter, cls)
        assert adapter.capabilities == descriptor.capabilities
        assert source in autopilot.BOARD_SOURCES


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        ("https://apply.workable.com/acme/", PortalReference("workable", "acme")),
        (
            "https://apply.workable.com/acme/j/a1b2c3d4e5/",
            PortalReference("workable", "acme", "A1B2C3D4E5"),
        ),
        (
            "https://apply.workable.com/Acme/j/A1B2C3D4E5/apply?utm_source=x",
            PortalReference("workable", "acme", "A1B2C3D4E5"),
        ),
        ("https://acme.workable.com", PortalReference("workable", "acme")),
        (
            "https://acme.workable.com/j/A1B2C3D4E5",
            PortalReference("workable", "acme", "A1B2C3D4E5"),
        ),
        ("https://apply.workable.com/j/A1B2C3D4E5", None),
        ("https://apply.workable.com/", None),
        ("https://apply.workable.com/acme/jobs/123", None),
        ("https://jobs.workable.com/view/abc", None),
        ("https://www.workable.com/api/accounts/acme", None),
        ("https://acme.recruitee.com", PortalReference("recruitee", "acme")),
        (
            "https://acme.recruitee.com/o/backend-engineer",
            PortalReference("recruitee", "acme", "backend-engineer"),
        ),
        (
            "https://acme.recruitee.com/o/backend-engineer/c/new",
            PortalReference("recruitee", "acme", "backend-engineer"),
        ),
        ("https://acme.recruitee.com/about", None),
        ("https://app.recruitee.com/o/backend-engineer", None),
        ("https://recruitee.com/o/backend-engineer", None),
    ],
)
def test_detect_portal_patterns(url, expected):
    assert detect_portal(url) == expected


def test_import_job_from_url_for_both_providers():
    offer = RECRUITEE_OFFERS["offers"][0]
    recorder = _Recorder(
        {
            WORKABLE_FEED_URL: WORKABLE_FEED,
            f"{RECRUITEE_OFFERS_URL}backend-engineer": {"offer": offer},
        }
    )
    workable = import_job_from_url(
        "https://apply.workable.com/acme/j/A1B2C3D4E5/", json_getter=recorder
    )
    assert (workable.source, workable.external_id, workable.company) == (
        "workable",
        "A1B2C3D4E5",
        "Acme Robotics",
    )
    recruitee = import_job_from_url(
        "https://acme.recruitee.com/o/backend-engineer", json_getter=recorder
    )
    assert (recruitee.source, recruitee.external_id) == ("recruitee", "914211")
    assert recorder.calls == [
        WORKABLE_FEED_URL,
        f"{RECRUITEE_OFFERS_URL}backend-engineer",
    ]


def test_import_job_from_url_explains_board_links_and_unknown_links():
    never = _Recorder({})
    with pytest.raises(ValueError, match="whole Workable job board 'acme'"):
        import_job_from_url("https://apply.workable.com/acme/", json_getter=never)
    with pytest.raises(ValueError, match="whole Recruitee job board 'acme'"):
        import_job_from_url("https://acme.recruitee.com/", json_getter=never)
    with pytest.raises(ValueError, match="Workable or Recruitee job page link"):
        import_job_from_url(
            "https://apply.workable.com/j/A1B2C3D4E5", json_getter=never
        )
    assert never.calls == []


# --------------------------------------------------------------------------- #
# The real HTTP transport against a stand-in board server on loopback
# --------------------------------------------------------------------------- #


class _BoardStub(BaseHTTPRequestHandler):
    """Answers the two providers' public endpoints by path (host-independent)."""

    routes: dict[str, Any] = {}
    seen: list[str] = []

    def do_GET(self) -> None:  # noqa: N802 - the http.server hook name
        type(self).seen.append(self.path)
        payload = self.routes.get(self.path)
        if payload is None:
            self.send_response(404)
            self.end_headers()
            return
        body = json.dumps(payload).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *_args: Any) -> None:
        return


@pytest.fixture
def board_server() -> Iterator[Callable[[str], Any]]:
    _BoardStub.routes = {
        "/api/accounts/acme?details=true": WORKABLE_FEED,
        "/api/offers/": RECRUITEE_OFFERS,
    }
    _BoardStub.seen = []
    server = ThreadingHTTPServer(("127.0.0.1", 0), _BoardStub)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base = f"http://127.0.0.1:{server.server_address[1]}"

    def getter(url: str) -> Any:
        # The adapters name the real hosts; the stand-in answers the same paths on loopback.
        parts = urlsplit(url)
        local = f"{base}{parts.path}" + (f"?{parts.query}" if parts.query else "")
        return _get_json(local, timeout=5, sleeper=lambda _seconds: None)

    try:
        yield getter
    finally:
        server.shutdown()
        server.server_close()


def test_both_adapters_read_a_stand_in_server_through_the_real_transport(board_server):
    workable = WorkableJobAdapter("acme", board_server).search(JobSearchQuery("scale"))
    recruitee = RecruiteeJobAdapter("acme", board_server).search(
        JobSearchQuery("build")
    )
    assert [job.external_id for job in workable.jobs] == ["A1B2C3D4E5", "F6E5D4C3B2"]
    assert [job.external_id for job in recruitee.jobs] == ["914211", "914212"]
    assert _BoardStub.seen == ["/api/accounts/acme?details=true", "/api/offers/"]

    missing = RecruiteeJobAdapter("acme", board_server).health()
    assert missing["ok"] is True
    gone = WorkableJobAdapter("other", board_server).health()
    assert gone["ok"] is False
    assert gone["message"] == "127.0.0.1 returned HTTP 404 (not found)."
