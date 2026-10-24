"""Tests for the ATS adapters, source catalog, portal detection and importers."""

from __future__ import annotations

import json
from collections.abc import Callable
from datetime import datetime
from email.message import Message
from typing import Any
from urllib.error import HTTPError, URLError

import pytest

from aira.ats import (
    DETAIL_FETCH_LIMIT,
    SOURCE_CATALOG,
    AshbyJobAdapter,
    LeverJobAdapter,
    PortalReference,
    SmartRecruitersJobAdapter,
    SourceDescriptor,
    _get_json,
    build_adapter,
    catalog_as_dicts,
    detect_portal,
    fetch_greenhouse_questions,
    find_source,
    import_job_from_url,
)
from linkedin_mcp_server.job_sources import (
    GreenhouseJobAdapter,
    JobSearchQuery,
    JobSourceCapabilities,
)

# --------------------------------------------------------------------------- #
# Fixtures (inline provider payloads)
# --------------------------------------------------------------------------- #

LEVER_UUID = "6f1f3f4e-1111-4a2b-9c3d-0123456789ab"
ASHBY_UUID = "0b7e1c2d-3e4f-4a5b-8c6d-7e8f9a0b1c2d"
ASHBY_HIDDEN_UUID = "9a8b7c6d-5e4f-4a3b-8c2d-1e0f9a8b7c6d"
ASHBY_INTERN_UUID = "1a2b3c4d-5e6f-4a7b-8c9d-0e1f2a3b4c5d"
EPOCH_2026_MS = 1_767_225_600_000  # 2026-01-01T00:00:00Z

LEVER_POSTINGS: list[Any] = [  # deliberately includes a non-dict entry
    {
        "id": LEVER_UUID,
        "text": "  Senior   AI Architect ",
        "hostedUrl": f"https://jobs.lever.co/acme/{LEVER_UUID}",
        "applyUrl": f"https://jobs.lever.co/acme/{LEVER_UUID}/apply",
        "categories": {
            "commitment": "Full-time",
            "location": "Remote - US",
            "team": "Platform",
            "department": "Engineering",
            "allLocations": ["Remote - US", "Austin, TX"],
        },
        "descriptionPlain": "Design AI systems for enterprise customers.",
        "description": "<p>Design AI systems</p>",
        "lists": [
            {
                "text": "Requirements",
                "content": "<li>Python &amp; LLMs</li><li>Kubernetes</li>",
            },
            {"text": "", "content": ""},
            "garbage",
        ],
        "additionalPlain": "Acme is an equal opportunity employer.",
        "createdAt": EPOCH_2026_MS,
        "workplaceType": "remote",
        "salaryRange": {
            "min": 180000,
            "max": 240000,
            "currency": "USD",
            "interval": "per-year-salary",
        },
        "country": "US",
    },
    {
        "id": "post-2",
        "text": "Product Designer",
        "hostedUrl": "https://jobs.lever.co/acme/post-2",
        "categories": {
            "commitment": "Contract",
            "location": "Berlin",
            "team": "Design",
        },
        "description": "<h2>About</h2><p>Craft &amp; ship interfaces</p>",
        "workplaceType": "on-site",
        "createdAt": 0,
    },
    {
        "id": "post-3",
        "text": "Intern Engineer",
        "hostedUrl": "https://jobs.lever.co/acme/post-3",
        "categories": {"commitment": "Intern", "location": "Austin, TX"},
        "workplaceType": "hybrid",
        "descriptionPlain": "AI Architect internship",
    },
    {
        "id": "post-3",
        "text": "Duplicate of post-3",
        "hostedUrl": "https://jobs.lever.co/acme/post-3",
    },
    {"text": "No ID at all"},
    "not a record",
]

ASHBY_BOARD: dict[str, Any] = {
    "apiVersion": "1",
    "jobs": [
        {
            "id": ASHBY_UUID,
            "title": " Staff  ML Engineer ",
            "department": "Engineering",
            "team": "ML Platform",
            "employmentType": "FullTime",
            "location": "San Francisco",
            "secondaryLocations": [
                {
                    "location": "New York",
                    "address": {
                        "postalAddress": {
                            "addressLocality": "New York",
                            "addressRegion": "New York",
                            "addressCountry": "United States",
                        }
                    },
                }
            ],
            "publishedAt": "2026-02-03T12:00:00.000Z",
            "isListed": True,
            "isRemote": True,
            "address": {
                "postalAddress": {
                    "addressLocality": "San Francisco",
                    "addressRegion": "California",
                    "addressCountry": "United States",
                }
            },
            "jobUrl": f"https://jobs.ashbyhq.com/acme/{ASHBY_UUID}",
            "applyUrl": f"https://jobs.ashbyhq.com/acme/{ASHBY_UUID}/application",
            "descriptionHtml": "<p>Build ML infra</p>",
            "descriptionPlain": "Build ML infra for training clusters.",
            "compensation": {
                "compensationTierSummary": "$200K - $260K + Offers Equity",
                "scrapeableCompensationSalarySummary": "$200K - $260K",
                "summaryComponents": [
                    {
                        "compensationType": "EquityPercentage",
                        "minValue": 0.1,
                        "maxValue": 0.5,
                        "currencyCode": None,
                        "interval": "NONE",
                    },
                    {
                        "compensationType": "Salary",
                        "minValue": 260000,
                        "maxValue": 200000,
                        "currencyCode": "usd",
                        "interval": "1 YEAR",
                    },
                ],
            },
        },
        {
            "id": ASHBY_HIDDEN_UUID,
            "title": "Hidden ML Engineer",
            "isListed": False,
            "isRemote": False,
            "employmentType": "Contract",
            "location": "Austin",
            "jobUrl": f"https://jobs.ashbyhq.com/acme/{ASHBY_HIDDEN_UUID}",
            "descriptionHtml": "<p>Secret ML work</p>",
        },
        {
            "id": ASHBY_INTERN_UUID,
            "title": "ML Intern",
            "department": "Engineering",
            "employmentType": "Intern",
            "location": "",
            "address": {
                "postalAddress": {
                    "addressLocality": "Austin",
                    "addressRegion": "TX",
                    "addressCountry": "US",
                }
            },
            "isListed": True,
            "isRemote": False,
            "descriptionHtml": "<p>Learn ML &amp; infra</p>",
            "compensation": {"summaryComponents": []},
        },
        {"title": "Missing ID"},
        42,
    ],
}

LEVER_LIST_URL = "https://api.lever.co/v0/postings/acme?mode=json"
ASHBY_BOARD_URL = (
    "https://api.ashbyhq.com/posting-api/job-board/acme?includeCompensation=true"
)
SR_BASE = "https://api.smartrecruiters.com/v1/companies/Acme/postings"


def _sr_record(
    index: int,
    *,
    remote: bool = False,
    employment: str = "Full-time",
    level: str = "Mid-Senior level",
    city: str = "Berlin",
    full_location: bool = True,
) -> dict[str, Any]:
    posting_id = f"74399900{index:06d}"
    location: dict[str, Any] = {
        "city": city,
        "region": "BE",
        "country": "de",
        "remote": remote,
    }
    if full_location:
        location["fullLocation"] = f"{city}, Germany"
    return {
        "id": posting_id,
        "name": f"Platform Engineer {index}",
        "uuid": f"uuid-{index}",
        "refNumber": f"REF{index}",
        "company": {"identifier": "Acme", "name": "Acme Inc"},
        "releasedDate": "2026-03-01T08:00:00.000Z",
        "location": location,
        "industry": {"label": "Software"},
        "department": {"label": "Engineering"},
        "function": {"label": "Engineering"},
        "typeOfEmployment": {"id": "permanent", "label": employment},
        "experienceLevel": {"id": "mid_senior_level", "label": level},
        "ref": f"{SR_BASE}/{posting_id}",
    }


def _sr_detail(record: dict[str, Any]) -> dict[str, Any]:
    return {
        **record,
        "applyUrl": f"https://jobs.smartrecruiters.com/Acme/{record['id']}-platform-engineer",
        "postingUrl": f"https://jobs.smartrecruiters.com/Acme/{record['id']}-platform-engineer",
        "jobAd": {
            "sections": {
                "companyDescription": {
                    "title": "Company Description",
                    "text": "<p>Acme builds &amp; ships</p>",
                },
                "jobDescription": {
                    "title": "Job Description",
                    "text": "<ul><li>Own the platform</li></ul>",
                },
                "qualifications": {
                    "title": "Qualifications",
                    "text": "<p>Python, Kubernetes</p>",
                },
                "additionalInformation": {"title": "", "text": "<p>Hybrid work</p>"},
            }
        },
    }


class _Recorder:
    """Serves canned JSON by URL and records every request."""

    def __init__(
        self,
        responses: dict[str, Any] | None = None,
        fallback: Callable[[str], Any] | None = None,
    ) -> None:
        self.responses = dict(responses or {})
        self.fallback = fallback
        self.calls: list[str] = []

    def __call__(self, url: str) -> Any:
        self.calls.append(url)
        if url in self.responses:
            payload = self.responses[url]
        elif self.fallback is not None:
            payload = self.fallback(url)
        else:
            raise AssertionError(f"Unexpected URL requested: {url}")
        if isinstance(payload, Exception):
            raise payload
        return payload


# --------------------------------------------------------------------------- #
# HTTP transport
# --------------------------------------------------------------------------- #


class _FakeResponse:
    def __init__(self, body: bytes) -> None:
        self._body = body

    def read(self) -> bytes:
        return self._body

    def __enter__(self) -> _FakeResponse:
        return self

    def __exit__(self, *exc: object) -> bool:
        return False


def _fake_opener(
    outcomes: list[Any], seen: list[tuple[Any, ...]]
) -> Callable[..., Any]:
    def open_url(request: Any, timeout: float) -> _FakeResponse:
        seen.append(
            (
                request.full_url,
                timeout,
                request.get_header("User-agent"),
                request.get_header("Accept"),
            )
        )
        outcome = outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return _FakeResponse(outcome)

    return open_url


SECRET_URL = "https://api.example.com/v1/postings?api_key=secret-value&q=engineer"


def _http_error(code: int) -> HTTPError:
    return HTTPError(SECRET_URL, code, "status", Message(), None)


def test_get_json_sends_headers_and_decodes_body():
    seen: list[tuple[Any, ...]] = []
    sleeps: list[float] = []
    opener = _fake_opener([b'{"ok": true}'], seen)

    payload = _get_json(SECRET_URL, timeout=7, sleeper=sleeps.append, opener=opener)

    assert payload == {"ok": True}
    assert seen == [(SECRET_URL, 7, "Aira/1.0", "application/json")]
    assert sleeps == []


def test_get_json_fails_fast_on_client_errors_without_leaking_query():
    seen: list[tuple[Any, ...]] = []
    sleeps: list[float] = []
    opener = _fake_opener([_http_error(404)], seen)

    with pytest.raises(RuntimeError) as excinfo:
        _get_json(SECRET_URL, sleeper=sleeps.append, opener=opener)

    message = str(excinfo.value)
    assert message == "api.example.com returned HTTP 404 (not found)."
    assert "secret-value" not in message
    assert len(seen) == 1
    assert sleeps == []


def test_get_json_retries_rate_limits_with_backoff_then_succeeds():
    seen: list[tuple[Any, ...]] = []
    sleeps: list[float] = []
    opener = _fake_opener([_http_error(429), _http_error(503), b"[1, 2]"], seen)

    payload = _get_json(SECRET_URL, sleeper=sleeps.append, opener=opener)

    assert payload == [1, 2]
    assert len(seen) == 3
    assert sleeps == [0.5, 1.0]


def test_get_json_gives_up_after_three_attempts():
    seen: list[tuple[Any, ...]] = []
    sleeps: list[float] = []
    opener = _fake_opener([_http_error(429)] * 3, seen)

    with pytest.raises(RuntimeError, match="HTTP 429 \\(rate limited\\)"):
        _get_json(SECRET_URL, sleeper=sleeps.append, opener=opener)

    assert len(seen) == 3
    assert sleeps == [0.5, 1.0]


def test_get_json_retries_network_errors_and_reports_timeouts():
    seen: list[tuple[Any, ...]] = []
    sleeps: list[float] = []
    opener = _fake_opener([URLError("refused")] * 3, seen)
    with pytest.raises(RuntimeError, match="Could not reach api.example.com"):
        _get_json(SECRET_URL, sleeper=sleeps.append, opener=opener)
    assert sleeps == [0.5, 1.0]

    timeouts = [URLError(TimeoutError("slow")), TimeoutError(), TimeoutError()]
    with pytest.raises(RuntimeError, match="timed out"):
        _get_json(
            SECRET_URL, sleeper=sleeps.append, opener=_fake_opener(timeouts, seen)
        )


def test_get_json_rejects_invalid_json_without_retrying():
    seen: list[tuple[Any, ...]] = []
    sleeps: list[float] = []
    opener = _fake_opener([b"<html>not json</html>"], seen)

    with pytest.raises(RuntimeError, match="not valid JSON"):
        _get_json(SECRET_URL, sleeper=sleeps.append, opener=opener)

    assert len(seen) == 1
    assert sleeps == []


def test_get_json_rejects_non_http_urls():
    with pytest.raises(ValueError, match="HTTP"):
        _get_json("ftp://example.com/postings", opener=_fake_opener([], []))


# --------------------------------------------------------------------------- #
# Lever
# --------------------------------------------------------------------------- #


def _lever(
    responses: dict[str, Any] | None = None,
) -> tuple[LeverJobAdapter, _Recorder]:
    recorder = _Recorder({LEVER_LIST_URL: LEVER_POSTINGS, **(responses or {})})
    return LeverJobAdapter("acme", recorder), recorder


def test_lever_normalizes_every_mapped_field():
    adapter, _ = _lever()

    job = adapter.normalize(LEVER_POSTINGS[0])

    assert job.source == "lever"
    assert job.external_id == LEVER_UUID
    assert job.source_url == f"https://jobs.lever.co/acme/{LEVER_UUID}"
    assert job.application_url == f"https://jobs.lever.co/acme/{LEVER_UUID}/apply"
    assert job.title == "Senior AI Architect"
    assert job.company == "acme"
    assert job.location == "Remote - US"
    assert job.workplace_type == "remote"
    assert job.employment_type == "full_time"
    assert (job.salary_min, job.salary_max, job.currency) == (180000.0, 240000.0, "USD")
    assert job.posted_at is not None
    assert job.posted_at.isoformat() == "2026-01-01T00:00:00+00:00"
    assert job.application_method == "lever_postings_api"
    assert job.description == (
        "Design AI systems for enterprise customers.\n\n"
        "## Requirements\nPython & LLMs\n\nKubernetes\n\n"
        "Acme is an equal opportunity employer."
    )
    assert job.content_fingerprint


def test_lever_falls_back_to_html_description_and_handles_sparse_records():
    adapter, _ = _lever()

    job = adapter.normalize(LEVER_POSTINGS[1])

    assert job.description == "About\n\nCraft & ship interfaces"
    assert job.application_url == job.source_url
    assert job.workplace_type == "on_site"
    assert job.employment_type == "contract"
    assert job.posted_at is None
    assert (job.salary_min, job.salary_max, job.currency) == (None, None, None)

    minimal = adapter.normalize({"id": "x1"})
    assert minimal.source_url == "https://jobs.lever.co/acme/x1"
    assert minimal.title == ""
    with pytest.raises(ValueError, match="no ID"):
        adapter.normalize({"text": "nameless"})


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("remote", "remote"),
        ("hybrid", "hybrid"),
        ("onsite", "on_site"),
        ("on-site", "on_site"),
        ("On Site", "on_site"),
        ("unknown", None),
        (None, None),
    ],
)
def test_lever_maps_workplace_types(raw, expected):
    adapter, _ = _lever()
    job = adapter.normalize({"id": "w", "workplaceType": raw})
    assert job.workplace_type == expected


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("Full-time", "full_time"),
        ("Full time", "full_time"),
        ("FULL_TIME", "full_time"),
        ("Part-time", "part_time"),
        ("Contract", "contract"),
        ("Intern", "internship"),
        ("Internship", "internship"),
        ("Temporary", "temporary"),
        ("Permanent", None),
        ("Full-time (Remote)", None),
        ("", None),
    ],
)
def test_lever_maps_commitment_to_employment_type(raw, expected):
    adapter, _ = _lever()
    job = adapter.normalize({"id": "c", "categories": {"commitment": raw}})
    assert job.employment_type == expected


def test_lever_salary_parsing_is_lenient():
    adapter, _ = _lever()

    swapped = adapter.normalize(
        {"id": "s", "salaryRange": {"min": "90,000", "max": 60000, "currency": "eur"}}
    )
    assert (swapped.salary_min, swapped.salary_max, swapped.currency) == (
        60000.0,
        90000.0,
        "EUR",
    )

    junk = adapter.normalize(
        {"id": "s2", "salaryRange": {"min": True, "max": "lots", "currency": "dollars"}}
    )
    assert (junk.salary_min, junk.salary_max, junk.currency) == (None, None, None)

    assert adapter.normalize({"id": "t", "createdAt": True}).posted_at is None
    assert adapter.normalize({"id": "t2", "createdAt": "2026"}).posted_at is None


def test_lever_search_matches_keywords_locally_including_team_and_department():
    adapter, recorder = _lever()

    result = adapter.search(JobSearchQuery("AI Architect"))

    assert recorder.calls == [LEVER_LIST_URL]
    assert [job.external_id for job in result.jobs] == [LEVER_UUID, "post-3"]
    assert result.section_errors == {}

    by_team = adapter.search(JobSearchQuery("platform engineering"))
    assert [job.external_id for job in by_team.jobs] == [LEVER_UUID]

    none = adapter.search(JobSearchQuery("architect quantum"))
    assert none.jobs == ()


def test_lever_search_applies_location_and_filters():
    adapter, _ = _lever()

    austin = adapter.search(JobSearchQuery("AI Architect", location="austin"))
    assert [job.external_id for job in austin.jobs] == [LEVER_UUID, "post-3"]

    remote = adapter.search(JobSearchQuery("AI Architect", location="Remote"))
    assert [job.external_id for job in remote.jobs] == [LEVER_UUID]

    interns = adapter.search(
        JobSearchQuery("AI Architect", filters={"job_type": "Internship"})
    )
    assert [job.external_id for job in interns.jobs] == ["post-3"]

    on_site = adapter.search(
        JobSearchQuery("designer", filters={"work_type": "on-site"})
    )
    assert [job.external_id for job in on_site.jobs] == ["post-2"]

    hybrid = adapter.search(JobSearchQuery("ai", filters={"work_type": "hybrid"}))
    assert [job.external_id for job in hybrid.jobs] == ["post-3"]


def test_lever_search_validates_filters_site_and_payload_shape():
    adapter, _ = _lever()
    with pytest.raises(ValueError, match="Unsupported Lever job filters: salary"):
        adapter.search(JobSearchQuery("engineer", filters={"salary": 1}))
    with pytest.raises(ValueError, match="Lever site"):
        LeverJobAdapter("../acme")
    with pytest.raises(ValueError, match="Lever site"):
        LeverJobAdapter("")
    broken = LeverJobAdapter("acme", lambda _url: {"postings": []})
    with pytest.raises(RuntimeError, match="invalid postings list"):
        broken.search(JobSearchQuery("engineer"))


def test_lever_fetch_job_uses_single_posting_endpoint():
    single_url = f"https://api.lever.co/v0/postings/acme/{LEVER_UUID}?mode=json"
    adapter, recorder = _lever({single_url: LEVER_POSTINGS[0]})

    job = adapter.fetch_job(LEVER_UUID)

    assert recorder.calls == [single_url]
    assert job.external_id == LEVER_UUID

    wrapped = LeverJobAdapter("acme", lambda _url: [LEVER_POSTINGS[0]])
    assert wrapped.fetch_job(LEVER_UUID).title == "Senior AI Architect"

    with pytest.raises(ValueError, match="posting ID"):
        adapter.fetch_job("../etc")
    with pytest.raises(RuntimeError, match="invalid posting response"):
        LeverJobAdapter("acme", lambda _url: "nope").fetch_job(LEVER_UUID)


def test_lever_capabilities_and_health():
    adapter, recorder = _lever({f"{LEVER_LIST_URL}&limit=1": [LEVER_POSTINGS[0]]})

    assert adapter.capabilities == JobSourceCapabilities(
        search=True,
        import_jobs=True,
        normalize=True,
        resolve_application_link=True,
        report_health=True,
    )
    assert not adapter.capabilities.submit_application

    report = adapter.health()
    assert recorder.calls == [f"{LEVER_LIST_URL}&limit=1"]
    assert report["source"] == "lever"
    assert report["ok"] is True
    assert "acme" in report["message"]
    checked_at = datetime.fromisoformat(report["checked_at"])
    assert checked_at.utcoffset() is not None

    def explode(_url: str) -> Any:
        raise RuntimeError("api.lever.co returned HTTP 503 (server error).")

    failed = LeverJobAdapter("acme", explode).health()
    assert failed["ok"] is False
    assert failed["message"] == "api.lever.co returned HTTP 503 (server error)."

    def crash(_url: str) -> Any:
        raise KeyError("unexpected")

    crashed = LeverJobAdapter("acme", crash).health()
    assert crashed["ok"] is False
    assert set(crashed) == {"source", "ok", "message", "checked_at"}


# --------------------------------------------------------------------------- #
# Ashby
# --------------------------------------------------------------------------- #


def _ashby(board: dict[str, Any] | None = None) -> tuple[AshbyJobAdapter, _Recorder]:
    recorder = _Recorder({ASHBY_BOARD_URL: board if board is not None else ASHBY_BOARD})
    return AshbyJobAdapter("acme", recorder), recorder


def test_ashby_normalizes_every_mapped_field():
    adapter, _ = _ashby()

    job = adapter.normalize(ASHBY_BOARD["jobs"][0])

    assert job.source == "ashby"
    assert job.external_id == ASHBY_UUID
    assert job.source_url == f"https://jobs.ashbyhq.com/acme/{ASHBY_UUID}"
    assert (
        job.application_url == f"https://jobs.ashbyhq.com/acme/{ASHBY_UUID}/application"
    )
    assert job.title == "Staff ML Engineer"
    assert job.company == "acme"
    assert job.location == "San Francisco"
    assert job.workplace_type == "remote"
    assert job.employment_type == "full_time"
    assert (job.salary_min, job.salary_max, job.currency) == (200000.0, 260000.0, "USD")
    assert job.description == "Build ML infra for training clusters."
    assert job.posted_at is not None
    assert job.posted_at.isoformat() == "2026-02-03T12:00:00+00:00"
    assert job.application_method == "ashby_job_board"


def test_ashby_normalizes_sparse_records_and_address_fallbacks():
    adapter, _ = _ashby()

    intern = adapter.normalize(ASHBY_BOARD["jobs"][2])
    assert intern.location == "Austin, TX, US"
    assert intern.workplace_type is None
    assert intern.employment_type == "internship"
    assert intern.description == "Learn ML & infra"
    assert (intern.salary_min, intern.salary_max, intern.currency) == (None, None, None)
    assert intern.source_url == f"https://jobs.ashbyhq.com/acme/{ASHBY_INTERN_UUID}"
    assert intern.application_url == intern.source_url

    with pytest.raises(ValueError, match="no ID"):
        adapter.normalize({"title": "nameless"})


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("FullTime", "full_time"),
        ("PartTime", "part_time"),
        ("Contract", "contract"),
        ("Intern", "internship"),
        ("Temporary", "temporary"),
        ("Volunteer", None),
        (None, None),
    ],
)
def test_ashby_maps_employment_types(raw, expected):
    adapter, _ = _ashby()
    assert (
        adapter.normalize({"id": "e", "employmentType": raw}).employment_type
        == expected
    )


def test_ashby_search_skips_unlisted_jobs_and_matches_locally():
    adapter, recorder = _ashby()

    result = adapter.search(JobSearchQuery("ML"))

    assert recorder.calls == [ASHBY_BOARD_URL]
    assert [job.external_id for job in result.jobs] == [ASHBY_UUID, ASHBY_INTERN_UUID]

    by_team = adapter.search(JobSearchQuery("ml platform"))
    assert [job.external_id for job in by_team.jobs] == [ASHBY_UUID]

    new_york = adapter.search(JobSearchQuery("ML", location="new york"))
    assert [job.external_id for job in new_york.jobs] == [ASHBY_UUID]

    austin = adapter.search(JobSearchQuery("ML", location="Austin"))
    assert [job.external_id for job in austin.jobs] == [ASHBY_INTERN_UUID]

    remote = adapter.search(JobSearchQuery("ML", filters={"work_type": "remote"}))
    assert [job.external_id for job in remote.jobs] == [ASHBY_UUID]

    interns = adapter.search(JobSearchQuery("ML", filters={"job_type": "internship"}))
    assert [job.external_id for job in interns.jobs] == [ASHBY_INTERN_UUID]


def test_ashby_validates_name_filters_and_payload_shape():
    with pytest.raises(ValueError, match="job board name"):
        AshbyJobAdapter("acme/evil")
    adapter, _ = _ashby()
    with pytest.raises(ValueError, match="Unsupported Ashby job filters: department"):
        adapter.search(JobSearchQuery("ml", filters={"department": "x"}))
    with pytest.raises(RuntimeError, match="invalid job board response"):
        AshbyJobAdapter("acme", lambda _url: []).search(JobSearchQuery("ml"))
    with pytest.raises(RuntimeError, match="invalid jobs list"):
        AshbyJobAdapter("acme", lambda _url: {"jobs": {}}).search(JobSearchQuery("ml"))


def test_ashby_fetch_job_picks_matching_id_even_when_unlisted():
    adapter, recorder = _ashby()

    hidden = adapter.fetch_job(ASHBY_HIDDEN_UUID)

    assert recorder.calls == [ASHBY_BOARD_URL]
    assert hidden.title == "Hidden ML Engineer"
    assert hidden.employment_type == "contract"
    with pytest.raises(RuntimeError, match="may have closed"):
        adapter.fetch_job("00000000-0000-4000-8000-000000000000")
    with pytest.raises(ValueError, match="posting ID"):
        adapter.fetch_job("bad id")


def test_ashby_capabilities_and_health():
    adapter, _ = _ashby()
    assert adapter.capabilities.report_health
    assert adapter.capabilities.import_jobs
    assert not adapter.capabilities.read_application_status

    report = adapter.health()
    assert report["ok"] is True
    assert report["source"] == "ashby"
    # Raw records that are not explicitly unlisted count, including the one without an ID.
    assert "3 listed job(s)" in report["message"]

    failing = AshbyJobAdapter("acme", lambda _url: {"jobs": None}).health()
    assert failing["ok"] is False
    assert "invalid jobs list" in failing["message"]


# --------------------------------------------------------------------------- #
# SmartRecruiters
# --------------------------------------------------------------------------- #


def _sr_page(records: list[dict[str, Any]], offset: int, total: int) -> dict[str, Any]:
    return {"totalFound": total, "offset": offset, "limit": 100, "content": records}


def _sr_search_url(keywords: str, offset: int) -> str:
    query = keywords.replace(" ", "+")
    return f"{SR_BASE}?q={query}&limit=100&offset={offset}"


def test_smartrecruiters_paginates_and_limits_detail_fetches():
    records = [_sr_record(index) for index in range(130)]
    responses: dict[str, Any] = {
        _sr_search_url("Platform Engineer", 0): _sr_page(records[:100], 0, 130),
        _sr_search_url("Platform Engineer", 100): _sr_page(records[100:], 100, 130),
    }
    for record in records:
        responses[f"{SR_BASE}/{record['id']}"] = _sr_detail(record)
    recorder = _Recorder(responses)
    adapter = SmartRecruitersJobAdapter("Acme", recorder)

    result = adapter.search(JobSearchQuery("Platform Engineer", max_pages=3))

    assert len(result.jobs) == 130
    assert recorder.calls[:2] == [
        _sr_search_url("Platform Engineer", 0),
        _sr_search_url("Platform Engineer", 100),
    ]
    detail_calls = recorder.calls[2:]
    assert len(detail_calls) == DETAIL_FETCH_LIMIT == 25
    assert detail_calls == [f"{SR_BASE}/{record['id']}" for record in records[:25]]
    assert all(job.description for job in result.jobs[:25])
    assert all(job.description == "" for job in result.jobs[25:])
    assert result.jobs[0].application_url == (
        f"https://jobs.smartrecruiters.com/Acme/{records[0]['id']}-platform-engineer"
    )
    assert result.jobs[30].application_url == result.jobs[30].source_url
    assert result.section_errors == {
        "details": {
            "error_type": "partial",
            "fetched": 25,
            "skipped": 105,
            "failures": [],
        }
    }


def test_smartrecruiters_stops_paging_at_max_pages_or_total_found():
    records = [_sr_record(index) for index in range(100)]
    recorder = _Recorder(
        {_sr_search_url("engineer", 0): _sr_page(records, 0, 250)},
        fallback=lambda url: _sr_detail(records[int(url.rsplit("/", 1)[1][-6:])]),
    )
    adapter = SmartRecruitersJobAdapter("Acme", recorder)

    one_page = adapter.search(JobSearchQuery("engineer", max_pages=1))
    assert len(one_page.jobs) == 100
    assert recorder.calls[0] == _sr_search_url("engineer", 0)
    assert sum(call.startswith(f"{SR_BASE}?") for call in recorder.calls) == 1

    exact = _Recorder(
        {_sr_search_url("engineer", 0): _sr_page(records, 0, 100)},
        fallback=lambda url: _sr_detail(records[int(url.rsplit("/", 1)[1][-6:])]),
    )
    SmartRecruitersJobAdapter("Acme", exact).search(
        JobSearchQuery("engineer", max_pages=5)
    )
    assert sum(call.startswith(f"{SR_BASE}?") for call in exact.calls) == 1

    empty = _Recorder({_sr_search_url("nothing", 0): _sr_page([], 0, 0)})
    result = SmartRecruitersJobAdapter("Acme", empty).search(JobSearchQuery("nothing"))
    assert result.jobs == ()
    assert result.section_errors == {}


def test_smartrecruiters_keeps_list_data_when_detail_fetches_fail():
    records = [_sr_record(index) for index in range(6)]
    responses: dict[str, Any] = {_sr_search_url("engineer", 0): _sr_page(records, 0, 6)}
    for index, record in enumerate(records):
        detail_url = f"{SR_BASE}/{record['id']}"
        if index == 1:
            responses[detail_url] = RuntimeError(
                "api.smartrecruiters.com returned HTTP 404 (not found)."
            )
        else:
            responses[detail_url] = _sr_detail(record)
    recorder = _Recorder(responses)

    result = SmartRecruitersJobAdapter("Acme", recorder).search(
        JobSearchQuery("engineer")
    )

    assert len(result.jobs) == 6
    assert result.jobs[1].description == ""
    assert result.jobs[1].title == "Platform Engineer 1"
    assert result.jobs[2].description != ""
    details = result.section_errors["details"]
    assert details["fetched"] == 5
    assert details["failures"] == [
        {
            "id": records[1]["id"],
            "message": "api.smartrecruiters.com returned HTTP 404 (not found).",
        }
    ]

    always_failing = _Recorder(
        {_sr_search_url("engineer", 0): _sr_page(records, 0, 6)},
        fallback=lambda _url: RuntimeError("boom"),
    )
    result = SmartRecruitersJobAdapter("Acme", always_failing).search(
        JobSearchQuery("engineer")
    )
    assert len(result.jobs) == 6
    assert (
        len(always_failing.calls) == 1 + SmartRecruitersJobAdapter.DETAIL_FAILURE_LIMIT
    )
    assert len(result.section_errors["details"]["failures"]) == 3


def test_smartrecruiters_normalizes_every_mapped_field_from_detail():
    record = _sr_record(7, remote=True, level="Mid-Senior level")
    detail = _sr_detail(record)
    recorder = _Recorder({f"{SR_BASE}/{record['id']}": detail})
    adapter = SmartRecruitersJobAdapter("Acme", recorder)

    job = adapter.fetch_job(record["id"])

    assert recorder.calls == [f"{SR_BASE}/{record['id']}"]
    assert job.source == "smartrecruiters"
    assert job.external_id == record["id"]
    assert job.source_url == f"https://jobs.smartrecruiters.com/Acme/{record['id']}"
    assert job.application_url == detail["applyUrl"]
    assert job.title == "Platform Engineer 7"
    assert job.company == "Acme Inc"
    assert job.location == "Berlin, Germany"
    assert job.workplace_type == "remote"
    assert job.employment_type == "full_time"
    assert job.seniority == "mid-senior level"
    assert job.posted_at is not None
    assert job.posted_at.isoformat() == "2026-03-01T08:00:00+00:00"
    assert job.application_method == "smartrecruiters_posting_api"
    assert job.description == (
        "## Company Description\nAcme builds & ships\n\n"
        "## Job Description\nOwn the platform\n\n"
        "## Qualifications\nPython, Kubernetes\n\n"
        "Hybrid work"
    )


@pytest.mark.parametrize(
    ("employment", "level", "expected_type", "expected_seniority"),
    [
        ("Full-time", "Senior", "full_time", "senior"),
        ("Part-time", "Entry level", "part_time", "entry level"),
        ("Contract", "Executive", "contract", "executive"),
        ("Internship", "Associate", "internship", "associate"),
        ("Temporary", "", "temporary", None),
        ("Permanent", "Director", None, "director"),
    ],
)
def test_smartrecruiters_maps_employment_and_seniority(
    employment, level, expected_type, expected_seniority
):
    adapter = SmartRecruitersJobAdapter("Acme", lambda _url: {})
    job = adapter.normalize(_sr_record(1, employment=employment, level=level))
    assert job.employment_type == expected_type
    assert job.seniority == expected_seniority


def test_smartrecruiters_builds_location_without_full_location():
    adapter = SmartRecruitersJobAdapter("Acme", lambda _url: {})
    job = adapter.normalize(_sr_record(2, full_location=False))
    assert job.location == "Berlin, BE, DE"
    assert job.workplace_type is None
    assert job.company == "Acme Inc"

    bare = adapter.normalize({"id": "1", "name": "Bare"})
    assert bare.company == "Acme"
    assert bare.location == ""
    assert bare.application_url == bare.source_url
    with pytest.raises(ValueError, match="no ID"):
        adapter.normalize({"name": "nameless"})


def test_smartrecruiters_applies_location_and_filters_locally_only():
    records = [
        _sr_record(1, remote=True, employment="Contract"),
        _sr_record(2, city="Munich"),
        _sr_record(3, employment="Internship", city="Berlin"),
    ]
    recorder = _Recorder(
        {_sr_search_url("engineer", 0): _sr_page(records, 0, 3)},
        fallback=lambda url: _sr_detail(records[int(url[-1]) - 1]),
    )
    adapter = SmartRecruitersJobAdapter("Acme", recorder)

    berlin = adapter.search(JobSearchQuery("engineer", location="berlin"))
    assert [job.title for job in berlin.jobs] == [
        "Platform Engineer 1",
        "Platform Engineer 3",
    ]

    remote = adapter.search(JobSearchQuery("engineer", filters={"work_type": "remote"}))
    assert [job.title for job in remote.jobs] == ["Platform Engineer 1"]

    interns = adapter.search(
        JobSearchQuery("engineer", filters={"job_type": "internship"})
    )
    assert [job.title for job in interns.jobs] == ["Platform Engineer 3"]

    unrelated = adapter.search(JobSearchQuery("engineer"))
    assert len(unrelated.jobs) == 3
    assert unrelated.section_errors == {}


def test_smartrecruiters_validates_identifier_filters_and_payloads():
    with pytest.raises(ValueError, match="company identifier"):
        SmartRecruitersJobAdapter("Acme Inc")
    adapter = SmartRecruitersJobAdapter("Acme", lambda _url: {"content": []})
    with pytest.raises(
        ValueError, match="Unsupported SmartRecruiters job filters: city"
    ):
        adapter.search(JobSearchQuery("engineer", filters={"city": "Berlin"}))
    with pytest.raises(RuntimeError, match="invalid postings response"):
        SmartRecruitersJobAdapter("Acme", lambda _url: []).search(JobSearchQuery("x"))
    with pytest.raises(RuntimeError, match="invalid postings list"):
        SmartRecruitersJobAdapter("Acme", lambda _url: {"content": "x"}).search(
            JobSearchQuery("x")
        )
    with pytest.raises(RuntimeError, match="invalid posting response"):
        SmartRecruitersJobAdapter("Acme", lambda _url: []).fetch_job("743999000001")
    with pytest.raises(ValueError, match="posting ID"):
        adapter.fetch_job("../x")


def test_smartrecruiters_capabilities_and_health():
    recorder = _Recorder(
        {f"{SR_BASE}?limit=1&offset=0": _sr_page([_sr_record(1)], 0, 42)}
    )
    adapter = SmartRecruitersJobAdapter("Acme", recorder)

    assert adapter.capabilities.search and adapter.capabilities.report_health
    assert not adapter.capabilities.prepare_application

    report = adapter.health()
    assert recorder.calls == [f"{SR_BASE}?limit=1&offset=0"]
    assert report["ok"] is True
    assert "42 public posting(s)" in report["message"]

    def deny(_url: str) -> Any:
        raise RuntimeError("api.smartrecruiters.com returned HTTP 403 (access denied).")

    failed = SmartRecruitersJobAdapter("Acme", deny).health()
    assert failed == {
        "source": "smartrecruiters",
        "ok": False,
        "message": "api.smartrecruiters.com returned HTTP 403 (access denied).",
        "checked_at": failed["checked_at"],
    }


# --------------------------------------------------------------------------- #
# Catalog and factory
# --------------------------------------------------------------------------- #

EXPECTED_SOURCES = {
    "linkedin",
    "google_jobs",
    "aira_jobs",
    "greenhouse",
    "lever",
    "ashby",
    "smartrecruiters",
    "workday",
    "icims",
    "taleo",
    "oracle_careers",
    "sap_successfactors",
    "bamboohr",
    "ukg",
    "company_site",
}


def test_catalog_integrity():
    ids = [descriptor.source for descriptor in SOURCE_CATALOG]
    assert len(ids) == len(set(ids))
    assert set(ids) == EXPECTED_SOURCES
    for descriptor in SOURCE_CATALOG:
        assert descriptor.group in {"linkedin", "google", "aira", "ats", "company_site"}
        assert descriptor.status in {"available", "planned"}
        assert descriptor.label and descriptor.access_method
        assert isinstance(descriptor.capabilities, JobSourceCapabilities)
        if descriptor.status == "planned":
            assert descriptor.reason
            assert descriptor.capabilities == JobSourceCapabilities()
        else:
            assert descriptor.reason == ""
            assert descriptor.capabilities.search
        if descriptor.identifier_label:
            assert descriptor.identifier_help

    assert _source("linkedin").group == "linkedin"
    assert _source("google_jobs").group == "google"
    assert _source("company_site").group == "company_site"
    assert _source(" LEVER ").identifier_label == "Site name"
    assert find_source("nope") is None
    assert _source("workday").reason == (
        "No official public candidate-facing posting API; the career-site JSON endpoint "
        "is unofficial"
    )


def _source(name: str) -> SourceDescriptor:
    descriptor = find_source(name)
    assert descriptor is not None, name
    return descriptor


def test_catalog_as_dicts_is_json_serializable():
    rows = catalog_as_dicts()

    assert [row["source"] for row in rows] == [d.source for d in SOURCE_CATALOG]
    assert json.loads(json.dumps(rows)) == rows
    lever = next(row for row in rows if row["source"] == "lever")
    assert lever["capabilities"]["search"] is True
    assert lever["capabilities"]["submit_application"] is False
    assert set(lever) == {
        "source",
        "label",
        "group",
        "status",
        "access_method",
        "identifier_label",
        "identifier_help",
        "capabilities",
        "reason",
    }


def test_source_descriptor_validation():
    with pytest.raises(ValueError, match="Planned sources"):
        SourceDescriptor(
            "x", "X", "ats", "planned", "none", "", "", JobSourceCapabilities()
        )
    with pytest.raises(ValueError, match="group"):
        SourceDescriptor(
            "x", "X", "other", "available", "none", "", "", JobSourceCapabilities()
        )
    with pytest.raises(ValueError, match="status"):
        SourceDescriptor(
            "x", "X", "ats", "soon", "none", "", "", JobSourceCapabilities(), ""
        )


def test_build_adapter_matches_catalog_for_every_available_ats_source():
    getter = _Recorder()
    for descriptor in SOURCE_CATALOG:
        if descriptor.status != "available" or not descriptor.identifier_label:
            continue
        adapter = build_adapter(descriptor.source, "acme", json_getter=getter)
        assert adapter.source_name == descriptor.source
        assert adapter.capabilities == descriptor.capabilities

    assert isinstance(build_adapter("greenhouse", "acme"), GreenhouseJobAdapter)
    assert isinstance(build_adapter(" Lever ", "acme"), LeverJobAdapter)
    assert isinstance(build_adapter("ashby", "acme"), AshbyJobAdapter)
    assert isinstance(
        build_adapter("smartrecruiters", "Acme"), SmartRecruitersJobAdapter
    )


def test_build_adapter_rejects_planned_unknown_and_externally_configured_sources():
    for source in ("workday", "icims", "taleo", "company_site"):
        with pytest.raises(ValueError, match="is not available yet"):
            build_adapter(source, "acme")
    with pytest.raises(ValueError, match="Source Workday is not available yet."):
        build_adapter("workday", "acme")
    with pytest.raises(ValueError, match="Unknown job source"):
        build_adapter("monster")
    with pytest.raises(ValueError, match="configured elsewhere"):
        build_adapter("linkedin")
    with pytest.raises(ValueError, match="configured elsewhere"):
        build_adapter("google_jobs")
    with pytest.raises(ValueError, match="Lever site"):
        build_adapter("lever", "")


# --------------------------------------------------------------------------- #
# Portal detection
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        ("https://boards.greenhouse.io/acme", PortalReference("greenhouse", "acme")),
        ("https://boards.greenhouse.io/acme/", PortalReference("greenhouse", "acme")),
        (
            "https://boards.greenhouse.io/acme/jobs/4012345?gh_src=abc#app",
            PortalReference("greenhouse", "acme", "4012345"),
        ),
        (
            "https://job-boards.greenhouse.io/acme/jobs/4012345",
            PortalReference("greenhouse", "acme", "4012345"),
        ),
        (
            "https://boards.greenhouse.io/embed/job_app?for=acme&token=4012345",
            PortalReference("greenhouse", "acme", "4012345"),
        ),
        ("https://jobs.lever.co/acme", PortalReference("lever", "acme")),
        (
            f"https://jobs.lever.co/acme/{LEVER_UUID}",
            PortalReference("lever", "acme", LEVER_UUID),
        ),
        (
            f"https://jobs.lever.co/acme/{LEVER_UUID.upper()}/apply?lever-source=LinkedIn",
            PortalReference("lever", "acme", LEVER_UUID),
        ),
        ("https://jobs.ashbyhq.com/acme.io", PortalReference("ashby", "acme.io")),
        (
            f"https://jobs.ashbyhq.com/acme/{ASHBY_UUID}",
            PortalReference("ashby", "acme", ASHBY_UUID),
        ),
        (
            f"https://jobs.ashbyhq.com/acme/{ASHBY_UUID}/application",
            PortalReference("ashby", "acme", ASHBY_UUID),
        ),
        (
            "https://jobs.smartrecruiters.com/AcmeInc",
            PortalReference("smartrecruiters", "AcmeInc"),
        ),
        (
            "https://jobs.smartrecruiters.com/AcmeInc/743999000001-platform-engineer?dcr_ci=x",
            PortalReference("smartrecruiters", "AcmeInc", "743999000001"),
        ),
        (
            "https://jobs.smartrecruiters.com/AcmeInc/743999000001",
            PortalReference("smartrecruiters", "AcmeInc", "743999000001"),
        ),
        (
            "https://acme.wd5.myworkdayjobs.com/en-US/External/job/Austin/Engineer_R123",
            PortalReference("workday", "acme"),
        ),
        (
            "https://Acme-Corp.wd1.myworkdayjobs.com/careers",
            PortalReference("workday", "acme-corp"),
        ),
        ("https://www.linkedin.com/jobs/view/123/", None),
        ("https://example.com/careers/123", None),
        ("https://boards.greenhouse.io/", None),
        ("https://boards.greenhouse.io/acme/departments/1", None),
        ("https://boards.greenhouse.io/acme/jobs/not-numeric", None),
        ("https://boards.greenhouse.io/embed/job_app?token=4012345", None),
        ("https://jobs.lever.co/acme/not-a-uuid", None),
        ("https://jobs.lever.co/", None),
        ("https://jobs.ashbyhq.com/acme/12345", None),
        ("https://jobs.smartrecruiters.com/AcmeInc/abc-slug", None),
        ("https://myworkdayjobs.com/acme", None),
        ("https://acme.workday.com/jobs/1", None),
    ],
)
def test_detect_portal_patterns(url, expected):
    assert detect_portal(url) == expected


@pytest.mark.parametrize(
    "url",
    [
        "javascript:alert(1)",
        "boards.greenhouse.io/acme",
        "ftp://jobs.lever.co/acme",
        "",
    ],
)
def test_detect_portal_rejects_non_http_urls(url):
    with pytest.raises(ValueError, match="HTTP"):
        detect_portal(url)


# --------------------------------------------------------------------------- #
# Single posting import
# --------------------------------------------------------------------------- #

GREENHOUSE_JOB_URL = (
    "https://boards-api.greenhouse.io/v1/boards/acme/jobs/4012345?questions=true"
)
GREENHOUSE_JOB: dict[str, Any] = {
    "id": 4012345,
    "title": "AI Architect",
    "absolute_url": "https://boards.greenhouse.io/acme/jobs/4012345",
    "company_name": "Acme",
    "location": {"name": "Remote - United States"},
    "content": "<p>Design systems &amp; platforms</p>",
    "first_published": "2026-08-01T10:00:00Z",
    "questions": [
        {
            "label": "First Name",
            "required": True,
            "fields": [{"name": "first_name", "type": "input_text", "values": []}],
        },
        {
            "label": "Resume/CV",
            "required": True,
            "fields": [{"name": "resume", "type": "input_file", "values": []}],
        },
        {
            "label": "Tell us why",
            "required": False,
            "fields": [{"name": "question_1", "type": "textarea"}],
        },
        {
            "label": "Are you authorized to work in the US?",
            "required": True,
            "fields": [
                {
                    "name": "question_2",
                    "type": "multi_value_single_select",
                    "values": [
                        {"label": "Yes", "value": 1},
                        {"label": "No", "value": 0},
                    ],
                }
            ],
        },
        {
            "label": "Preferred office",
            "required": False,
            "fields": [
                {
                    "name": "question_3",
                    "type": "multi_value_single_select",
                    "values": [
                        {"label": "Austin", "value": 1},
                        {"label": "Remote", "value": 2},
                    ],
                }
            ],
        },
        {
            "label": "Languages",
            "required": False,
            "fields": [
                {
                    "name": "question_4",
                    "type": "multi_value_multi_select",
                    "values": [
                        {"label": "Python", "value": 1},
                        {"value": "go"},
                        "garbage",
                        None,
                        {"label": "", "value": ""},
                    ],
                }
            ],
        },
        {
            "label": "Mystery",
            "fields": [{"name": "question_5", "type": "input_hidden"}],
        },
        {"label": "Only a label", "fields": None, "required": "yes"},
        "not a dict",
        None,
        42,
        {"label": "", "fields": []},
        {"fields": "garbage"},
        {"label": None, "fields": [{"name": "fallback_name", "type": "input_text"}]},
    ],
}


def test_import_job_from_url_for_each_provider():
    sr_record = _sr_record(5)
    recorder = _Recorder(
        {
            GREENHOUSE_JOB_URL: GREENHOUSE_JOB,
            f"https://api.lever.co/v0/postings/acme/{LEVER_UUID}?mode=json": LEVER_POSTINGS[
                0
            ],
            ASHBY_BOARD_URL: ASHBY_BOARD,
            f"{SR_BASE}/{sr_record['id']}": _sr_detail(sr_record),
        }
    )

    greenhouse = import_job_from_url(
        "https://boards.greenhouse.io/acme/jobs/4012345?gh_src=linkedin",
        json_getter=recorder,
    )
    assert (
        greenhouse.source,
        greenhouse.external_id,
        greenhouse.title,
        greenhouse.company,
    ) == (
        "greenhouse",
        "4012345",
        "AI Architect",
        "Acme",
    )
    assert greenhouse.description == "Design systems & platforms"
    assert greenhouse.application_method == "greenhouse_job_board"

    lever = import_job_from_url(
        f"https://jobs.lever.co/acme/{LEVER_UUID}/apply", json_getter=recorder
    )
    assert (lever.source, lever.external_id, lever.title) == (
        "lever",
        LEVER_UUID,
        "Senior AI Architect",
    )

    ashby = import_job_from_url(
        f"https://jobs.ashbyhq.com/acme/{ASHBY_HIDDEN_UUID}", json_getter=recorder
    )
    assert (ashby.source, ashby.external_id, ashby.title) == (
        "ashby",
        ASHBY_HIDDEN_UUID,
        "Hidden ML Engineer",
    )

    smart = import_job_from_url(
        f"https://jobs.smartrecruiters.com/Acme/{sr_record['id']}-platform-engineer",
        json_getter=recorder,
    )
    assert (smart.source, smart.external_id, smart.title) == (
        "smartrecruiters",
        sr_record["id"],
        "Platform Engineer 5",
    )
    assert smart.description.startswith("## Company Description")

    assert recorder.calls == [
        GREENHOUSE_JOB_URL,
        f"https://api.lever.co/v0/postings/acme/{LEVER_UUID}?mode=json",
        ASHBY_BOARD_URL,
        f"{SR_BASE}/{sr_record['id']}",
    ]


def test_import_job_from_url_explains_unsupported_links():
    never = _Recorder()
    with pytest.raises(ValueError, match="not a supported job posting URL"):
        import_job_from_url(
            "https://www.linkedin.com/jobs/view/123/", json_getter=never
        )
    with pytest.raises(
        ValueError, match="whole Lever job board 'acme', not a single posting"
    ):
        import_job_from_url("https://jobs.lever.co/acme", json_getter=never)
    with pytest.raises(ValueError, match="whole Greenhouse job board"):
        import_job_from_url("https://boards.greenhouse.io/acme", json_getter=never)
    with pytest.raises(ValueError, match="Source Workday is not available yet."):
        import_job_from_url(
            "https://acme.wd5.myworkdayjobs.com/en-US/External/job/Austin/Engineer_R123",
            json_getter=never,
        )
    with pytest.raises(ValueError, match="HTTP"):
        import_job_from_url("javascript:alert(1)", json_getter=never)
    assert never.calls == []

    with pytest.raises(RuntimeError, match="invalid job response"):
        import_job_from_url(
            "https://boards.greenhouse.io/acme/jobs/4012345",
            json_getter=lambda _url: [],
        )


# --------------------------------------------------------------------------- #
# Greenhouse application questions
# --------------------------------------------------------------------------- #


def test_fetch_greenhouse_questions_normalizes_and_skips_malformed_entries():
    recorder = _Recorder({GREENHOUSE_JOB_URL: GREENHOUSE_JOB})

    questions = fetch_greenhouse_questions("acme", "4012345", json_getter=recorder)

    assert recorder.calls == [GREENHOUSE_JOB_URL]
    assert questions == [
        {
            "label": "First Name",
            "required": True,
            "type": "text",
            "options": [],
            "field_name": "first_name",
        },
        {
            "label": "Resume/CV",
            "required": True,
            "type": "file",
            "options": [],
            "field_name": "resume",
        },
        {
            "label": "Tell us why",
            "required": False,
            "type": "textarea",
            "options": [],
            "field_name": "question_1",
        },
        {
            "label": "Are you authorized to work in the US?",
            "required": True,
            "type": "boolean",
            "options": ["Yes", "No"],
            "field_name": "question_2",
        },
        {
            "label": "Preferred office",
            "required": False,
            "type": "select",
            "options": ["Austin", "Remote"],
            "field_name": "question_3",
        },
        {
            "label": "Languages",
            "required": False,
            "type": "multi_select",
            "options": ["Python", "go"],
            "field_name": "question_4",
        },
        {
            "label": "Mystery",
            "required": False,
            "type": "unknown",
            "options": [],
            "field_name": "question_5",
        },
        {
            "label": "Only a label",
            "required": True,
            "type": "unknown",
            "options": [],
            "field_name": "",
        },
        {
            "label": "fallback_name",
            "required": False,
            "type": "text",
            "options": [],
            "field_name": "fallback_name",
        },
    ]
    assert json.loads(json.dumps(questions)) == questions


def test_fetch_greenhouse_questions_handles_missing_questions_and_bad_input():
    assert (
        fetch_greenhouse_questions("acme", "1", json_getter=lambda _url: {"id": 1})
        == []
    )
    assert (
        fetch_greenhouse_questions(
            "acme", "1", json_getter=lambda _url: {"questions": "x"}
        )
        == []
    )
    with pytest.raises(ValueError, match="board token"):
        fetch_greenhouse_questions("../acme", "1", json_getter=lambda _url: {})
    with pytest.raises(ValueError, match="numeric Greenhouse job ID"):
        fetch_greenhouse_questions("acme", "abc", json_getter=lambda _url: {})
    with pytest.raises(RuntimeError, match="invalid job response"):
        fetch_greenhouse_questions("acme", "1", json_getter=lambda _url: "nope")
