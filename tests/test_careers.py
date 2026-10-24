"""Careers: Google Jobs search, import, alerts and intelligence reports.

Three layers, all against the stand-in SerpApi server (``tests/serpapi_stub.py``)
or a fake client, never the network, the real database or a real SerpApi key:

* pure functions (salary and posted-at parsing, job normalization, filters,
  parameter mapping);
* the service layer on a scratch migrated database (search, cache, import,
  duplicates, alerts, monitor hook, concurrent reports);
* HTTP journeys through the real request handler on a loopback port.
"""

from __future__ import annotations

import copy
import http.cookiejar
import json
import sqlite3
import threading
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from http.server import ThreadingHTTPServer
from pathlib import Path
from typing import Any

import pytest

import auto_apply_assistant as app
from aira.airaapi import careers, careers_intel, catalog, service
from aira.airaapi import client as client_module
from aira.airaapi.client import RawResponse, SerpApiClient, SerpApiError
from aira.schema import migrate
from linkedin_mcp_server.job_sources import GoogleJobsAdapter
from serpapi_stub import (
    DEFAULT_KEY,
    RICH_ENGINES,
    SerpApiStub,
    generic_body,
    load_fixture,
)

NOW = datetime(2026, 10, 22, 12, 0, 0, tzinfo=timezone.utc)
PASSWORD = "a long enough password 123"
RESUME = (
    "Jordan Example, Coffee Operations Lead. 12 years running cafes, barista training, "
    "roasting, inventory, scheduling, customer service, team leadership and food safety. "
) * 3


def fixture_body(engine: str) -> dict[str, Any]:
    text = (
        json.dumps(
            load_fixture(engine) if engine in RICH_ENGINES else generic_body(engine)
        )
        .replace("{{base}}", "http://stub")
        .replace("{{id}}", "sid")
    )
    return json.loads(text)


def jobs_record(**overrides: Any) -> dict[str, Any]:
    record = copy.deepcopy(fixture_body("google_jobs")["jobs_results"][1])
    record.update(overrides)
    return record


def add_unique_jobs(stub: SerpApiStub, count: int) -> None:
    """Make the stub append ``count`` brand-new google_jobs listings (own ids and URLs)."""

    def apply(body: dict[str, Any]) -> None:
        jobs = body.get("jobs_results")
        if not isinstance(jobs, list):
            return
        for index in range(count):
            extra = copy.deepcopy(jobs[-1])
            extra["title"] = f"Added role {index}"
            extra["job_id"] = f"AddedJobId{index}"
            extra["share_link"] = (
                f"https://www.google.com/search?q=added{index}&ibp=htl;jobs"
            )
            extra["apply_options"] = [
                {
                    "title": "Company site",
                    "link": f"https://careers.example.com/jobs/added{index}",
                }
            ]
            extra["description"] = (
                f"Added role {index}. Run the coffee bar, train baristas and manage inventory."
            )
            jobs.append(extra)

    stub._apply_variant = apply  # type: ignore[method-assign]


# ---------------------------------------------------------------------------
# pure functions
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("text", "low", "high", "currency", "period", "annual"),
    [
        ("$18-22 an hour", 18, 22, "USD", "hour", (37440, 45760)),
        ("$60K-70K a year", 60000, 70000, "USD", "year", (60000, 70000)),
        ("$120,000 – $150,000 a year", 120000, 150000, "USD", "year", (120000, 150000)),
        ("Up to $25 an hour", None, 25, "USD", "hour", (None, 52000)),
        ("From $45 an hour", 45, None, "USD", "hour", (93600, None)),
        ("£40,000 a year", 40000, 40000, "GBP", "year", (40000, 40000)),
        ("CA$80K-100K per year", 80000, 100000, "CAD", "year", (80000, 100000)),
        ("$5,000-$6,000 a month", 5000, 6000, "USD", "month", (60000, 72000)),
        ("60-70K USD annually", 60000, 70000, "USD", "year", (60000, 70000)),
        ("50,000 - 60,000 EUR/year", 50000, 60000, "EUR", "year", (50000, 60000)),
        ("$100K", 100000, 100000, "USD", None, (None, None)),
        ("€45K-55K", 45000, 55000, "EUR", None, (None, None)),
        ("$22-18 an hour", 18, 22, "USD", "hour", (37440, 45760)),
    ],
)
def test_parse_salary_reads_ranges_currencies_and_periods(
    text, low, high, currency, period, annual
):
    salary = careers.parse_salary(text)
    assert salary is not None
    assert (salary["min"], salary["max"]) == (low, high)
    assert salary["currency"] == currency and salary["period"] == period
    assert (salary["annual_min"], salary["annual_max"]) == annual
    assert salary["text"] == text


@pytest.mark.parametrize(
    "text", ["Competitive", "Full-time", "3 years experience", "2 openings"]
)
def test_parse_salary_returns_figures_only_when_the_text_has_them(text):
    salary = careers.parse_salary(text)
    assert salary is not None and salary["min"] is None and salary["max"] is None


def test_parse_salary_of_nothing_is_none():
    assert careers.parse_salary("") is None
    assert careers.parse_salary(None) is None


def test_posted_timestamp_converts_relative_ages():
    assert careers.posted_timestamp("3 days ago", NOW) == "2026-10-19T00:00:00Z"
    assert careers.posted_timestamp("1 hour ago", NOW) == "2026-10-22T11:00:00Z"
    assert careers.posted_timestamp("2 weeks ago", NOW) == "2026-10-08T00:00:00Z"
    assert careers.posted_timestamp("30+ days ago", NOW) == "2026-09-22T00:00:00Z"
    assert careers.posted_timestamp("a month ago", NOW) == "2026-09-22T00:00:00Z"
    assert careers.posted_timestamp("Just posted", NOW) == "2026-10-22T12:00:00Z"
    assert careers.posted_timestamp("whenever", NOW) is None
    assert careers.posted_timestamp("", NOW) is None


def test_normalize_job_keeps_every_documented_field():
    raw = jobs_record(
        via="via Indeed",
        detected_extensions={
            "posted_at": "3 days ago",
            "schedule_type": "Full-time",
            "salary": "$18-22 an hour",
            "work_from_home": True,
            "health_insurance": True,
            "dental_coverage": True,
            "paid_time_off": True,
        },
    )
    job = careers.normalize_job(raw, NOW)
    assert job is not None
    assert job["title"] == "Senior Barista" and job["company"] == "Joe Coffee"
    assert job["location"] == "Brooklyn, NY" and job["via"] == "via Indeed"
    assert (
        job["posted_at"] == "3 days ago"
        and job["posted_at_iso"] == "2026-10-19T00:00:00Z"
    )
    assert job["schedule_type"] == "Full-time" and job["work_from_home"] is True
    assert job["salary"]["min"] == 18 and job["salary"]["annual_max"] == 45760
    assert [o["title"] for o in job["apply_options"]] == ["LinkedIn", "Company site"]
    assert job["share_link"].startswith("https://www.google.com/search")
    assert job["thumbnail"] == "https://example.com/logos/2.png"
    assert job["google_job_id"] == raw["job_id"]
    assert job["extensions"] == raw["extensions"]
    assert job["highlights"][0]["title"] == "Qualifications"
    assert job["qualifications"] == ["1+ year of experience", "Friendly and reliable"]
    assert job["benefits"] == {
        "health_insurance": True,
        "dental_coverage": True,
        "paid_time_off": True,
    }
    assert job["importable"] is True and job["already_in_aira"] is False
    assert job["aira_job_key"] is None and job["match"] is None


def test_job_keys_match_the_jobs_tab_adapter_so_the_two_never_diverge():
    adapter = GoogleJobsAdapter("unused")
    for raw in fixture_body("google_jobs")["jobs_results"]:
        assert careers.job_key(raw) == adapter.normalize(raw).external_id
    no_id = jobs_record()
    no_id.pop("job_id")
    assert careers.job_key(no_id) == adapter.normalize(no_id).external_id


def test_salary_is_found_in_extensions_when_detected_has_none():
    raw = jobs_record(
        detected_extensions={}, extensions=["Full-time", "$60K-70K a year"]
    )
    job = careers.normalize_job(raw, NOW)
    assert job and job["salary"]["annual_min"] == 60000


def test_normalize_job_drops_unsafe_links_and_unusable_records():
    raw = jobs_record(
        thumbnail="javascript:alert(1)",
        share_link="javascript:alert(1)",
        apply_options=[
            {"title": "Bad", "link": "javascript:alert(1)"},
            {"title": "Good", "link": "https://careers.example.com/x"},
        ],
    )
    job = careers.normalize_job(raw, NOW)
    assert job and job["thumbnail"] == "" and job["share_link"] == ""
    assert job["apply_options"] == [
        {"title": "Good", "link": "https://careers.example.com/x"}
    ]
    assert careers.normalize_job({"company_name": "x"}, NOW) is None
    assert careers.normalize_job("nope", NOW) is None
    orphan = careers.normalize_job(
        jobs_record(share_link="", apply_options=[], job_id=""), NOW
    )
    assert orphan and orphan["importable"] is False


def test_normalize_results_skips_duplicates_and_reads_search_time():
    body = fixture_body("google_jobs")
    body["jobs_results"].append(copy.deepcopy(body["jobs_results"][0]))
    jobs = careers.normalize_results(body, NOW)
    assert len(jobs) == 4
    assert jobs[0]["posted_at_iso"] == "2026-10-19T00:00:00Z"
    assert careers.normalize_results(None) == []
    assert careers.normalize_results({"jobs_results": "x"}) == []


def test_normalize_filters_reads_filters_and_chips():
    body = fixture_body("google_jobs")
    groups = careers.normalize_filters(body)
    assert [g["name"] for g in groups] == ["Date posted", "Remote"]
    assert groups[0]["parameter"] == "uds"
    assert groups[0]["options"] == [
        {"text": "Date posted", "value": "uds-date-posted-token"}
    ]
    body["filters"].append(
        {
            "name": "Company type",
            "options": [
                {"name": "Startup", "parameters": {"uds": "uds-startup"}},
                {"text": "Enterprise", "uds": "uds-enterprise"},
            ],
        }
    )
    body["chips"] = [
        {
            "type": "Employment",
            "param": "employment_type",
            "options": [
                {"text": "Full-time", "value": "FULLTIME"},
                {"name": "Part-time", "value": "employment_type:PARTTIME"},
            ],
        },
        {"type": "", "options": []},
        "garbage",
    ]
    groups = careers.normalize_filters(body)
    by_name = {g["name"]: g for g in groups}
    assert by_name["Company type"]["options"][1] == {
        "text": "Enterprise",
        "value": "uds-enterprise",
    }
    assert by_name["Employment"]["parameter"] == "chips"
    assert by_name["Employment"]["options"] == [
        {"text": "Full-time", "value": "employment_type:FULLTIME"},
        {"text": "Part-time", "value": "employment_type:PARTTIME"},
    ]
    assert careers.normalize_filters(None) == []
    assert careers.normalize_filters({"filters": [{"name": "x"}, 3]}) == []


def test_next_page_token_reads_serpapi_pagination():
    assert (
        careers.next_page_token(fixture_body("google_jobs"))
        == "fixture-next-page-token"
    )
    assert careers.next_page_token({}) == ""


def test_build_job_params_maps_the_form_onto_real_parameters():
    params, applied, warnings = careers.build_job_params(
        {
            "q": "  barista  ",
            "location": "New York, NY",
            "gl": "US",
            "hl": "en-gb",
            "remote": True,
            "posted_within": "week",
            "employment_type": "Full-Time",
            "lrad": "40",
            "uds": "token",
            "next_page_token": "page2",
            "chips": "city:abc",
        }
    )
    assert params == {
        "q": "barista",
        "location": "New York, NY",
        "gl": "us",
        "hl": "en-gb",
        "chips": "city:abc,date_posted:week,employment_type:FULLTIME",
        "ltype": "1",
        "lrad": 40,
        "uds": "token",
        "next_page_token": "page2",
    }
    assert {a["filter"] for a in applied} >= {
        "remote",
        "posted_within",
        "employment_type",
        "radius_km",
        "uds",
    }
    assert len(warnings) == 1 and "deprecated" in warnings[0]
    plain, applied, warnings = careers.build_job_params({"q": "nurse"})
    assert plain == {"q": "nurse"} and applied == [] and warnings == []
    unpaged, _, _ = careers.build_job_params(
        {"q": "x", "next_page_token": "t"}, paginate=False
    )
    assert "next_page_token" not in unpaged


@pytest.mark.parametrize(
    ("payload", "message"),
    [
        ({}, "search query"),
        ({"q": "x" * 201}, "limited to 200"),
        ({"q": "x", "posted_within": "decade"}, "posted_within"),
        ({"q": "x", "employment_type": "slave"}, "employment_type"),
        ({"q": "x", "gl": "usa"}, "two-letter"),
        ({"q": "x", "gl": "1"}, "two-letter"),
        ({"q": "x", "hl": "english-united"}, "language"),
        ({"q": "x", "lrad": "far"}, "kilometres"),
        ({"q": "x", "lrad": 5000}, "between 1 and 1000"),
        ({"q": "x", "ltype": 7}, "ltype"),
        ({"q": "x", "remote": "maybe"}, "true or false"),
        ({"q": ["x"]}, "must be text"),
    ],
)
def test_build_job_params_rejects_bad_input_with_a_plain_message(payload, message):
    with pytest.raises(ValueError, match=message):
        careers.build_job_params(payload)


def test_employment_type_aliases():
    assert careers.employment_type_key("contractor") == "contract"
    assert careers.employment_type_key("Part time") == "part_time"
    assert careers.employment_type_key("INTERN") == "internship"
    assert careers.employment_type_key("") == ""


def test_salary_stats_are_computed_from_stated_figures_only():
    jobs = careers.normalize_results(fixture_body("google_jobs"), NOW)
    stats = careers_intel.salary_stats(jobs)
    assert stats["listings"] == 4 and stats["listings_with_pay"] == 2
    assert stats["annualized"] == 2 and stats["currency"] == "USD"
    assert stats["min_annual"] == 37440 and stats["max_annual"] == 70000
    assert (
        stats["median_annual"] == 53300
    )  # median of the range midpoints 41600 and 65000
    empty = careers_intel.salary_stats([])
    assert empty["median_annual"] is None and empty["listings"] == 0


def test_bound_connect_is_unavailable_for_memory_databases(tmp_path):
    assert careers.bound_connect(sqlite3.connect(":memory:")) is None
    path = tmp_path / "x.sqlite3"
    connection = sqlite3.connect(path)
    open_worker = careers.bound_connect(connection)
    assert open_worker is not None
    worker = open_worker()
    assert worker.execute("SELECT 1").fetchone()[0] == 1
    worker.close()
    connection.close()


# ---------------------------------------------------------------------------
# service layer on a scratch database
# ---------------------------------------------------------------------------


@pytest.fixture
def stub(monkeypatch):
    server = SerpApiStub().start()
    monkeypatch.setenv("AIRA_SERPAPI_BASE_URL", server.base_url)
    monkeypatch.setattr(client_module, "_sleep", lambda _seconds: None)
    yield server
    server.stop()


@pytest.fixture
def db(tmp_path):
    connection = sqlite3.connect(tmp_path / "user.sqlite3")
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA journal_mode = WAL")
    migrate(connection)
    connection.commit()
    yield connection
    connection.close()


@pytest.fixture
def client(stub):
    return SerpApiClient(DEFAULT_KEY)


class FakeGateway:
    """Records imports and scores jobs by title (80 unless overridden)."""

    def __init__(self) -> None:
        self.imported: list[Any] = []
        self.scores: dict[str, int | None] = {}

    def import_posting(self, posting):
        self.imported.append(posting)
        return {"job_id": f"google_jobs:{posting.external_id}", "title": posting.title}

    def preview_matches(self, postings):
        out = []
        for posting in postings:
            score = self.scores.get(posting.title, 80)
            out.append(
                None
                if score is None
                else {"score": score, "classification": "good_match"}
            )
        return out


@pytest.fixture
def gateway():
    previous = careers.get_gateway()
    fake = FakeGateway()
    careers.set_gateway(fake)
    yield fake
    careers.set_gateway(previous)


def test_search_jobs_normalizes_records_history_and_serves_repeats_from_cache(
    db, client, stub, gateway
):
    first = careers.search_jobs(
        db, client, {"q": "barista", "location": "New York"}, now=NOW
    )
    assert [j["title"] for j in first["jobs"]] == [
        "Barista",
        "Senior Barista",
        "Coffee Roaster",
        "Cafe Manager",
    ]
    assert first["next_page_token"] == "fixture-next-page-token"
    assert first["cache_hit"] is False and first["credits"] == 1
    assert [g["name"] for g in first["filters"]] == ["Date posted", "Remote"]
    assert first["jobs"][1]["match"] == {"score": 80, "classification": "good_match"}
    assert first["jobs"][2]["work_from_home"] is True
    assert first["warnings"] == [] and first["params"]["q"] == "barista"
    record = service.get_search(db, first["record_id"])
    assert record["source"] == "careers" and record["engine"] == "google_jobs"
    second = careers.search_jobs(
        db, client, {"q": "barista", "location": "New York"}, now=NOW
    )
    assert second["cache_hit"] is True and second["credits"] == 0
    assert stub.count("/search.json", "google_jobs") == 1
    assert [j["key"] for j in second["jobs"]] == [j["key"] for j in first["jobs"]]


def test_search_jobs_sends_the_mapped_parameters_to_serpapi(db, client, stub):
    outcome = careers.search_jobs(
        db,
        client,
        {
            "q": "barista",
            "remote": True,
            "posted_within": "3days",
            "employment_type": "part_time",
            "lrad": 25,
            "uds": "uds-remote-token",
            "next_page_token": "page-two",
        },
        now=NOW,
    )
    sent = stub.calls[-1].params
    assert sent["engine"] == "google_jobs" and sent["q"] == "barista"
    assert sent["ltype"] == "1" and sent["lrad"] == "25"
    assert sent["chips"] == "date_posted:3days,employment_type:PARTTIME"
    assert sent["uds"] == "uds-remote-token" and sent["next_page_token"] == "page-two"
    assert len(outcome["warnings"]) == 1
    assert {a["parameter"] for a in outcome["applied"]} == {
        "ltype",
        "chips",
        "lrad",
        "uds",
    }


def test_search_jobs_dry_run_costs_nothing_and_knows_the_cache(db, client, stub):
    plan = careers.search_jobs(db, client, {"q": "barista", "dry_run": True}, now=NOW)
    assert (
        plan["dry_run"] is True and plan["credits"] == 1 and plan["cache_hit"] is False
    )
    assert stub.search_calls == 0
    careers.search_jobs(db, client, {"q": "barista"}, now=NOW)
    again = careers.search_jobs(db, client, {"q": "barista", "dry_run": True}, now=NOW)
    assert again["credits"] == 0 and again["cache_hit"] is True
    assert stub.search_calls == 1


def test_search_jobs_without_a_key_reports_not_configured_but_serves_the_cache(
    db, client, stub
):
    careers.search_jobs(db, client, {"q": "barista"}, now=NOW)
    # Without a key Careers searches Aira Jobs unless Google is asked for by name.
    google = {"provider": "google"}
    cached = careers.search_jobs(db, None, {"q": "barista", **google}, now=NOW)
    assert cached["cache_hit"] is True and len(cached["jobs"]) == 4
    with pytest.raises(SerpApiError) as failure:
        careers.search_jobs(db, None, {"q": "welder", **google}, now=NOW)
    assert failure.value.code == "not_configured"


def test_search_jobs_handles_empty_results_and_api_errors(db, client, stub):
    empty = careers.search_jobs(db, client, {"q": "__empty__"}, now=NOW)
    assert empty["jobs"] == [] and "hasn't returned" in empty["note"]
    with pytest.raises(SerpApiError) as failure:
        careers.search_jobs(db, client, {"q": "__out_of_searches__"}, now=NOW)
    assert failure.value.code == "out_of_searches"
    assert failure.value.record_id
    bad = SerpApiClient("rejected_key_0123456789abcdefgh")
    with pytest.raises(SerpApiError) as failure:
        careers.search_jobs(db, bad, {"q": "other"}, now=NOW)
    assert failure.value.code == "invalid_api_key"


def test_search_jobs_can_save_the_search(db, client, stub):
    outcome = careers.search_jobs(
        db,
        client,
        {"q": "barista", "save_as": "Coffee roles", "next_page_token": "t"},
        now=NOW,
    )
    assert outcome["saved"]["name"] == "Coffee roles" and outcome["saved"]["tags"] == [
        "careers"
    ]
    assert "next_page_token" not in outcome["saved"]["params"]
    calls = stub.search_calls
    with pytest.raises(ValueError, match="120 characters"):
        careers.search_jobs(db, client, {"q": "welder", "save_as": "x" * 200}, now=NOW)
    assert stub.search_calls == calls  # a bad form never costs a search
    duplicate = careers.search_jobs(
        db, client, {"q": "barista", "save_as": "Coffee roles"}, now=NOW
    )
    assert duplicate["saved"]["name"] == "Coffee roles"


def test_shared_search_goes_through_history_and_cache(db, client, stub):
    parameters = {
        "engine": "google_jobs",
        "q": "barista",
        "api_key": DEFAULT_KEY,
        "hl": "en",
        "location": "New York",
    }
    first = careers.shared_search(db, client, parameters)
    second = careers.shared_search(db, client, parameters)
    assert first["jobs_results"] and second == first
    assert stub.count("/search.json", "google_jobs") == 1
    rows = service.list_searches(db, source="careers")
    assert rows["total"] == 2 and rows["items"][0]["cache_hit"] is True
    assert DEFAULT_KEY not in json.dumps(rows)
    empty = careers.shared_search(db, client, {**parameters, "q": "__empty__"})
    assert "error" not in empty


def test_find_existing_uses_source_record_url_and_content_rules(db):
    job = careers.normalize_job(jobs_record(), NOW)
    assert job is not None
    assert careers.find_existing(db, job) is None
    db.execute(
        "INSERT INTO jobs (job_id, title, company, location, job_url, source, external_id, "
        "content_fingerprint) VALUES ('greenhouse:1', 'Senior Barista', 'Joe Coffee', 'Brooklyn, NY', "
        "'https://boards.greenhouse.io/joe/jobs/1', 'greenhouse', '1', 'abc')"
    )
    db.execute(
        "INSERT INTO job_source_records (source, external_id, job_id, source_url, canonical_url) "
        "VALUES ('greenhouse', '1', 'greenhouse:1', 'https://careers.example.com/jobs/1001/?utm_source=x', "
        "'https://careers.example.com/jobs/1001')"
    )
    found = careers.find_existing(db, job)
    assert found == {
        "job_id": "greenhouse:1",
        "via": "url",
        "source_url": "https://careers.example.com/jobs/1001/?utm_source=x",
    }
    db.execute(
        "INSERT INTO job_source_records (source, external_id, job_id, source_url, canonical_url) "
        "VALUES ('google_jobs', ?, 'greenhouse:1', 'https://www.google.com/x', 'https://www.google.com/x')",
        (job["key"],),
    )
    assert careers.find_existing(db, job)["via"] == "source"
    posting = careers.build_posting(job)
    db.execute("DELETE FROM job_source_records")
    db.execute(
        "UPDATE jobs SET content_fingerprint = ? WHERE job_id = 'greenhouse:1'",
        (posting.content_fingerprint,),
    )
    assert careers.find_existing(db, job, posting)["via"] == "content"


def test_build_posting_rederives_everything_from_google_fields():
    job = careers.normalize_job(
        jobs_record(
            detected_extensions={
                "posted_at": "3 days ago",
                "schedule_type": "Full-time",
                "salary": "$18-22 an hour",
                "work_from_home": True,
            }
        ),
        NOW,
    )
    assert job is not None
    tampered = {
        **job,
        "key": "g-forged",
        "already_in_aira": True,
        "title": "Senior Barista",
    }
    posting = careers.build_posting(tampered)
    assert posting.source == "google_jobs"
    assert posting.external_id == careers.job_key(jobs_record())
    assert posting.external_id != "g-forged"
    assert posting.company == "Joe Coffee" and posting.workplace_type == "remote"
    assert posting.employment_type == "full_time"
    assert (posting.salary_min, posting.salary_max, posting.currency) == (
        37440,
        45760,
        "USD",
    )
    assert posting.posted_at is not None and posting.posted_at.isoformat().startswith(
        "2026-10-19"
    )
    assert posting.application_url == "https://www.linkedin.com/jobs/view/1001"
    no_description = {**job, "description": "", "salary": None}
    built = careers.build_posting(no_description)
    assert "Qualifications" in built.description and built.salary_min is None
    with pytest.raises(ValueError, match="title"):
        careers.build_posting({**job, "title": ""})
    with pytest.raises(ValueError, match="URL"):
        careers.build_posting({**job, "share_link": "", "apply_options": []})


def test_import_job_goes_through_the_gateway_and_flags_duplicates(db, gateway):
    job = careers.normalize_job(jobs_record(), NOW)
    assert job is not None
    outcome = careers.import_job(db, job)
    assert outcome["created"] is True and outcome["duplicate_of"] is None
    assert gateway.imported[0].external_id == job["key"]
    db.execute(
        "INSERT INTO job_source_records (source, external_id, job_id, source_url, canonical_url) "
        "VALUES ('greenhouse', '9', 'greenhouse:9', 'https://careers.example.com/jobs/1001/?ref=x', "
        "'https://careers.example.com/jobs/1001')"
    )
    merged = careers.import_job(db, job)
    assert merged["created"] is False and merged["duplicate_of"] == "greenhouse:9"
    assert merged["matched_by"] == "url"
    assert (
        gateway.imported[1].source_url == "https://careers.example.com/jobs/1001/?ref=x"
    )
    with pytest.raises(ValueError, match="job"):
        careers.import_job(db, "not a job")  # type: ignore[arg-type]
    careers.set_gateway(None)
    with pytest.raises(RuntimeError, match="not available"):
        careers.import_job(db, job)


# -- alerts -------------------------------------------------------------------


def make_alert(db, **overrides):
    payload = {
        "q": "barista",
        "location": "New York",
        "interval_minutes": 60,
        **overrides,
    }
    return careers.create_alert(db, payload, now=NOW)


def test_alert_creation_builds_a_saved_search_and_a_monitor(db):
    alert = make_alert(
        db, name="NYC baristas", remote=True, auto_import=True, min_score=70
    )
    assert alert["name"] == "NYC baristas" and alert["interval_minutes"] == 60
    assert (
        alert["auto_import"] is True
        and alert["min_score"] == 70
        and alert["notify"] is True
    )
    assert alert["estimated_monthly_searches"] == 720 and alert["baselined"] is False
    assert alert["warnings"] and alert["params"]["ltype"] == "1"
    saved = service.list_saved(db)[0]
    assert saved["tags"] == ["careers", "alert"] and saved["engine"] == "google_jobs"
    monitor = service.list_monitors(db)[0]
    assert monitor["saved_id"] == saved["id"] and monitor["notify_on_change"] is False
    assert [a["id"] for a in careers.list_alerts(db)] == [alert["id"]]


def test_alert_validation_and_limits(db):
    with pytest.raises(ValueError, match="search query"):
        careers.create_alert(db, {}, now=NOW)
    with pytest.raises(ValueError, match="every 60 minutes"):
        make_alert(db, interval_minutes=30)
    with pytest.raises(ValueError, match="whole number"):
        make_alert(db, interval_minutes="often")
    with pytest.raises(ValueError, match="min_score"):
        make_alert(db, min_score=101)
    assert service.list_saved(db) == [] and service.list_monitors(db) == []
    for index in range(careers.MAX_ALERTS):
        make_alert(db, q=f"role {index}")
    with pytest.raises(ValueError, match="up to 10 job alerts"):
        make_alert(db, q="one too many")


def test_a_failed_monitor_creation_leaves_no_orphan_saved_search(db, monkeypatch):
    def refuse(*_args, **_kwargs):
        raise ValueError("no monitors today")

    monkeypatch.setattr(service, "create_monitor", refuse)
    with pytest.raises(ValueError, match="no monitors"):
        make_alert(db)
    assert service.list_saved(db) == [] and careers.list_alerts(db) == []


def run_alert(db, client, alert, notices):
    return careers.run_alert(
        db,
        client,
        alert["id"],
        notify=lambda title, body, link: notices.append((title, body, link)),
    )


def test_an_alert_lists_only_new_jobs_notifies_and_never_floods_the_first_run(
    db, client, stub, gateway
):
    alert = make_alert(db)
    notices: list[tuple[str, str, str]] = []
    baseline = run_alert(db, client, alert, notices)
    assert baseline["summary"].startswith("Baseline captured (4 current jobs)")
    assert baseline["alert"]["new_jobs"] == [] and notices == []
    assert baseline["alert"]["baselined"] is True and baseline["credits"] == 1
    quiet = run_alert(db, client, alert, notices)
    assert quiet["summary"] == "No new jobs (4 current)" and notices == []

    add_unique_jobs(stub, 2)
    found = run_alert(db, client, alert, notices)
    assert found["found"] == 2 and found["summary"] == "2 new jobs of 6"
    detail = found["alert"]
    assert [j["title"] for j in detail["new_jobs"]] == ["Added role 1", "Added role 0"]
    first = detail["new_jobs"][0]
    assert first["match"] == {"score": 80, "classification": "good_match"}
    assert first["importable"] is True and first["found_at"] and first["record_id"]
    assert detail["new_count"] == 2 and detail["last_found"] == 2
    assert len(notices) == 1
    title, body, link = notices[0]
    assert title == "2 new jobs: barista in New York"
    assert "Added role" in body and link == f"#/careers?tab=alerts&alert={alert['id']}"
    assert gateway.imported == []  # auto-import is off

    again = run_alert(db, client, alert, notices)
    assert again["found"] == 0 and len(notices) == 1  # the same jobs are not new twice
    assert service.list_monitors(db)[0]["last_change_summary"] == again["summary"]


def test_dismissing_clears_the_new_badge_without_forgetting_the_jobs(
    db, client, stub, gateway
):
    alert = make_alert(db)
    run_alert(db, client, alert, [])
    add_unique_jobs(stub, 2)
    detail = run_alert(db, client, alert, [])["alert"]
    keys = [job["key"] for job in detail["new_jobs"]]
    one = careers.dismiss_alert_jobs(db, alert["id"], keys[:1])
    assert one["dismissed"] == 1 and one["alert"]["new_count"] == 1
    everything = careers.dismiss_alert_jobs(db, alert["id"], None)
    assert everything["dismissed"] == 1 and everything["alert"]["new_count"] == 0
    assert careers.alert_detail(db, alert["id"])["new_jobs"] == []
    kept = careers.alert_detail(db, alert["id"], include_dismissed=True)
    assert len(kept["new_jobs"]) == 2 and all(j["dismissed"] for j in kept["new_jobs"])
    assert run_alert(db, client, alert, [])["found"] == 0
    with pytest.raises(LookupError):
        careers.dismiss_alert_jobs(db, "0" * 32, None)


def test_auto_import_saves_only_jobs_that_reach_the_threshold_and_never_applies(
    db, client, stub, gateway
):
    gateway.scores = {"Added role 0": 90, "Added role 1": 40, "Added role 2": None}
    alert = make_alert(db, auto_import=True, min_score=75)
    notices: list[tuple[str, str, str]] = []
    run_alert(db, client, alert, notices)
    add_unique_jobs(stub, 3)
    outcome = run_alert(db, client, alert, notices)
    assert outcome["found"] == 3 and outcome["imported"] == 1
    assert [p.title for p in gateway.imported] == ["Added role 0"]
    assert "1 imported" in outcome["summary"]
    by_title = {j["title"]: j for j in outcome["alert"]["new_jobs"]}
    assert by_title["Added role 0"]["imported_job_id"].startswith("google_jobs:")
    assert by_title["Added role 0"]["already_in_aira"] is True
    assert by_title["Added role 1"]["imported_job_id"] is None
    assert by_title["Added role 1"]["match"]["score"] == 40
    assert (
        by_title["Added role 2"]["match"] is None
    )  # unscorable: listed, never imported
    assert "1 imported into Jobs" in notices[-1][1]


def test_alert_notifications_can_be_turned_off(db, client, stub, gateway):
    alert = make_alert(db, notify=False)
    notices: list[tuple[str, str, str]] = []
    run_alert(db, client, alert, notices)
    add_unique_jobs(stub, 1)
    assert run_alert(db, client, alert, notices)["found"] == 1 and notices == []


def test_alert_update_delete_and_orphan_handling(db, client, gateway):
    alert = make_alert(db)
    changed = careers.update_alert(
        db,
        alert["id"],
        {
            "name": "Renamed",
            "interval_minutes": 180,
            "enabled": False,
            "auto_import": True,
            "min_score": 60,
            "notify": False,
        },
        now=NOW,
    )
    assert changed["name"] == "Renamed" and changed["interval_minutes"] == 180
    assert changed["enabled"] is False and changed["auto_import"] is True
    assert changed["min_score"] == 60 and changed["notify"] is False
    with pytest.raises(ValueError, match="Send name"):
        careers.update_alert(db, alert["id"], {})
    # The monitor was deleted from the Aira API console: the alert lists as off and can be revived.
    db.execute("DELETE FROM airaapi_monitors")
    db.commit()
    listed = careers.list_alerts(db)[0]
    assert listed["monitor_id"] is None and listed["enabled"] is False
    with pytest.raises(ValueError, match="no monitor"):
        careers.run_alert(db, None, alert["id"])
    revived = careers.update_alert(db, alert["id"], {"enabled": True}, now=NOW)
    assert revived["monitor_id"] and revived["enabled"] is True
    # The saved search was deleted from the console: the alert disappears with it.
    saved_id = service.list_saved(db)[0]["id"]
    service.delete_saved(db, saved_id)
    assert careers.list_alerts(db) == []
    assert db.execute("SELECT COUNT(*) FROM careers_alerts").fetchone()[0] == 0
    second = make_alert(db, q="barista two")
    careers.delete_alert(db, second["id"])
    assert service.list_saved(db) == [] and service.list_monitors(db) == []
    with pytest.raises(LookupError):
        careers.delete_alert(db, second["id"])


def test_plain_monitors_still_use_the_generic_change_notification(db, client, stub):
    saved = service.create_saved(
        db,
        {"name": "Plain", "engine": "google_jobs", "params": {"q": "barista"}},
        now=NOW,
    )
    monitor = service.create_monitor(
        db, {"saved_id": saved["id"], "interval_minutes": 60}, now=NOW
    )
    notices: list[tuple[str, str, str]] = []
    notify = lambda title, body, link: notices.append((title, body, link))  # noqa: E731
    service.run_monitor(db, client, monitor["id"], notify=notify)
    stub.variant = 1
    outcome = service.run_monitor(db, client, monitor["id"], notify=notify)
    assert outcome["summary"] and notices and notices[0][0] == "Monitor changed: Plain"


def test_a_broken_monitor_hook_does_not_break_the_monitor(
    db, client, stub, monkeypatch
):
    def broken(*_args):
        raise RuntimeError("hook bug")

    monkeypatch.setattr(service, "MONITOR_HOOKS", [broken])
    saved = service.create_saved(
        db,
        {"name": "Plain", "engine": "google_jobs", "params": {"q": "barista"}},
        now=NOW,
    )
    monitor = service.create_monitor(
        db, {"saved_id": saved["id"], "interval_minutes": 60}, now=NOW
    )
    outcome = service.run_monitor(db, client, monitor["id"])
    assert outcome["record"]["status"] == "ok"


def test_the_alert_hook_is_registered_once(db):
    before = len(service.MONITOR_HOOKS)
    service.register_monitor_hook(careers.process_monitor_run)
    assert len(service.MONITOR_HOOKS) == before
    assert careers.process_monitor_run in service.MONITOR_HOOKS


# -- reports ------------------------------------------------------------------


class FakeClient:
    """A client with latency that counts how many searches are in flight at once."""

    def __init__(self, delay: float = 0.15) -> None:
        self.delay = delay
        self.lock = threading.Lock()
        self.active = 0
        self.max_active = 0
        self.calls: list[tuple[str, dict[str, Any]]] = []

    def search(self, engine, params, options):
        with self.lock:
            self.active += 1
            self.max_active = max(self.max_active, self.active)
            self.calls.append((engine, dict(params)))
        try:
            time.sleep(self.delay)
            body = fixture_body(engine)
            body["search_metadata"]["id"] = f"sid{len(self.calls)}"
            return RawResponse(200, body, None, f"sid{len(self.calls)}", 5)
        finally:
            with self.lock:
                self.active -= 1


def run(db, client, kind, payload, **kwargs):
    spec = careers_intel.build_spec(kind, payload)
    return careers_intel.run_report(db, client, spec, now=NOW, **kwargs)


def test_every_report_kind_has_a_plan_with_only_valid_engine_calls():
    shapes = careers_intel.playbooks()
    assert [p["id"] for p in shapes] == list(careers_intel.KINDS)
    for shape in shapes:
        assert shape["sections"] and shape["max_credits"] == len(shape["sections"])
        assert set(shape["engines"]) <= set(catalog.engines())


def test_company_intel_report_runs_five_sections_and_extracts_real_fields(
    db, client, stub
):
    report = run(
        db,
        client,
        "company-intel",
        {"company": "Blue Bottle", "role": "Barista", "location": "New York"},
    )
    assert report["kind"] == "company-intel" and len(report["sections"]) == 5
    assert (
        report["credits"] == 5 and report["cache_hits"] == 0 and report["errors"] == 0
    )
    sections = {s["id"]: s for s in report["sections"]}
    assert set(sections) == {"overview", "news", "jobs", "employees", "offices"}
    assert all(s["status"] == "ok" and s["record_id"] for s in sections.values())
    overview = sections["overview"]["items"]
    assert (
        overview[0]["meta"]["kind"] == "knowledge" and overview[0]["title"] == "Coffee"
    )
    assert any(item["meta"]["kind"] == "question" for item in overview)
    news = sections["news"]["items"][0]
    assert news["title"] == "Coffee prices climb as harvests lag"
    assert news["meta"]["source"] == "Reuters" and news["url"].startswith(
        "https://news.example.com"
    )
    jobs = sections["jobs"]
    assert (
        jobs["stats"]["listings_with_pay"] == 2
        and jobs["stats"]["median_annual"] == 53300
    )
    assert "median 53,300" in jobs["summary"] and "computed" in jobs["summary"]
    assert sections["jobs"]["params"] == {
        "q": "Blue Bottle Barista",
        "location": "New York",
    }
    assert sections["offices"]["items"][0]["title"] == "Fixture Coffee Roasters"
    assert sections["employees"]["items"]
    for section in report["sections"]:
        assert service.get_search(db, section["record_id"])["source"] == "intel"
    # The same report again is free: every section is a cache hit.
    repeat = run(
        db,
        client,
        "company-intel",
        {"company": "Blue Bottle", "role": "Barista", "location": "New York"},
    )
    assert repeat["credits"] == 0 and repeat["cache_hits"] == 5
    assert stub.search_calls == 5
    listed = careers_intel.list_reports(db)
    assert [r["id"] for r in listed] == [repeat["id"], report["id"]]
    assert careers_intel.get_report(db, report["id"])["title"] == report["title"]


def test_sections_run_concurrently_but_never_more_than_four_at_once(db):
    fake = FakeClient(delay=0.2)
    started = time.monotonic()
    report = run(db, fake, "company-intel", {"company": "Acme"})
    elapsed = time.monotonic() - started
    assert report["credits"] == 5
    assert 2 <= fake.max_active <= 4
    assert elapsed < 5 * 0.2  # five sequential calls would take at least a second


def test_a_failing_section_does_not_stop_the_others(db, client, stub):
    report = run(
        db, client, "salary", {"role": "Barista", "company": "Globex __server_error__"}
    )
    by_id = {s["id"]: s for s in report["sections"]}
    assert set(by_id) == {"estimates", "market", "company_pay", "community"}
    assert by_id["estimates"]["status"] == "ok" and by_id["market"]["status"] == "ok"
    assert by_id["company_pay"]["status"] == "error" and by_id["company_pay"]["error"]
    assert by_id["company_pay"]["code"] and by_id["company_pay"]["record_id"]
    assert report["errors"] == 2 and report["credits"] == 2
    assert careers_intel.list_reports(db)[0]["errors"] == 2


def test_a_report_where_nothing_works_raises_the_setup_error(db, stub):
    bad = SerpApiClient("rejected_key_0123456789abcdefgh")
    with pytest.raises(SerpApiError) as failure:
        run(db, bad, "learn", {"skill": "python"})
    assert failure.value.code == "invalid_api_key"
    assert careers_intel.list_reports(db) == []
    with pytest.raises(SerpApiError) as failure:
        run(db, None, "learn", {"skill": "python"})
    assert failure.value.code == "not_configured"


def test_an_empty_engine_response_is_an_empty_section_not_a_failure(db, client):
    report = run(db, client, "learn", {"skill": "__empty__"})
    assert {s["status"] for s in report["sections"]} == {"empty"}
    assert all("hasn't returned" in s["summary"] for s in report["sections"])
    assert report["errors"] == 0


def test_reports_share_cached_sections_across_kinds(db, client, stub):
    run(db, client, "company-intel", {"company": "Acme"})
    prep = run(db, client, "interview-prep", {"company": "Acme", "role": "Barista"})
    news = next(s for s in prep["sections"] if s["id"] == "news")
    assert news["cache_hit"] is True and news["credits"] == 0
    assert prep["cache_hits"] == 1 and prep["credits"] == 4
    assert stub.count("/search.json", "google_news") == 1


def test_estimate_returns_the_plan_and_cost_without_calling_serpapi(db, client, stub):
    spec = careers_intel.build_spec(
        "interview-prep", {"company": "Acme", "role": "Barista"}
    )
    plan = careers_intel.estimate(db, spec, service.Limits(), NOW)
    assert plan["dry_run"] is True and plan["credits"] == 5 and plan["cache_hits"] == 0
    assert [row["engine"] for row in plan["plan"]] == [
        "google",
        "google_forums",
        "youtube",
        "google_news",
        "google_jobs",
    ]
    assert stub.search_calls == 0
    run(db, client, "company-intel", {"company": "Acme"})
    after = careers_intel.estimate(db, spec, service.Limits(), NOW)
    assert after["cache_hits"] == 1 and after["credits"] == 4
    assert next(r for r in after["plan"] if r["id"] == "news")["cache_hit"] is True


def test_sections_can_be_chosen_to_lower_the_cost(db, client, stub):
    report = run(
        db, client, "company-intel", {"company": "Acme", "sections": ["news", "jobs"]}
    )
    assert [s["id"] for s in report["sections"]] == ["news", "jobs"] and report[
        "credits"
    ] == 2
    with pytest.raises(ValueError, match="Unknown section 'bogus'"):
        careers_intel.build_spec(
            "company-intel", {"company": "Acme", "sections": ["bogus"]}
        )
    with pytest.raises(ValueError, match="at least one"):
        careers_intel.build_spec("company-intel", {"company": "Acme", "sections": []})
    with pytest.raises(ValueError, match="list of section ids"):
        careers_intel.build_spec(
            "company-intel", {"company": "Acme", "sections": "news"}
        )


def test_trends_report_computes_series_statistics_from_the_response(db, client, stub):
    report = run(
        db,
        client,
        "trends",
        {"terms": ["python", "rust"], "geo": "us", "window": "90d"},
    )
    by_id = {s["id"]: s for s in report["sections"]}
    assert set(by_id) == {"interest", "related_queries", "related_topics", "news"}
    sent = [c.params for c in stub.calls if c.engine == "google_trends"]
    assert {p["q"] for p in sent} == {"python,rust", "python"}
    assert all(p["geo"] == "US" and p["date"] == "today 3-m" for p in sent)
    assert {p["data_type"] for p in sent} == {
        "TIMESERIES",
        "RELATED_QUERIES",
        "RELATED_TOPICS",
    }
    series = by_id["interest"]["items"][0]
    assert series["title"] == "python" and series["meta"]["peak"] == 71
    assert series["meta"]["latest"] == 71 and series["meta"]["average"] == 55
    assert series["meta"]["points"] == [41, 47, 52, 58, 63, 71]
    assert series["meta"]["change_pct"] == 73  # (71 - 41) / 41, last vs first sample
    assert "computed" in series["subtitle"]
    related = by_id["related_queries"]["items"]
    assert (
        related[0]["meta"]["group"] == "rising" and related[0]["title"] == "python 3.14"
    )
    assert by_id["related_queries"]["summary"] == "2 rising, 2 top"


def test_travel_reports_for_flights_and_hotels(db, client, stub):
    flights = run(
        db,
        client,
        "travel",
        {
            "kind": "flights",
            "departure_id": "AUS",
            "arrival_id": "JFK",
            "outbound_date": "2026-12-01",
            "return_date": "2026-12-04",
            "currency": "USD",
        },
    )
    section = flights["sections"][0]
    assert flights["title"] == "Flights AUS to JFK" and section["status"] == "ok"
    assert stub.calls[-1].params["type"] == "1"
    top = section["items"][0]
    assert top["title"] == "Delta · AUS to JFK" and top["meta"]["price"] == 289
    assert top["meta"]["stops"] == 0 and top["meta"]["group"] == "best"
    assert (
        "289 USD" in top["subtitle"]
        and "nonstop" in top["subtitle"]
        and "07:00 to 11:30" in top["subtitle"]
    )
    assert section["price_insights"]["lowest_price"] == 289
    assert "rates these prices typical" in section["summary"]
    one_way = run(
        db,
        client,
        "travel",
        {
            "kind": "flights",
            "departure_id": "AUS",
            "arrival_id": "SFO",
            "outbound_date": "2026-12-02",
        },
    )
    assert stub.calls[-1].params["type"] == "2" and one_way["credits"] == 1
    hotels = run(
        db,
        client,
        "travel",
        {
            "kind": "hotels",
            "q": "Austin",
            "check_in_date": "2026-12-01",
            "check_out_date": "2026-12-03",
            "adults": 1,
        },
    )
    stay = hotels["sections"][0]["items"][0]
    assert stay["title"] == "Fixture Grand Hotel" and stay["meta"]["price"] == 189
    assert (
        "$189 per night" in stay["subtitle"]
        and stay["thumbnail"] == "https://example.com/hotel-0.jpg"
    )


@pytest.mark.parametrize(
    ("kind", "payload", "message"),
    [
        ("company-intel", {}, "a company"),
        ("company-intel", {"company": "x" * 121}, "limited to 120"),
        ("salary", {}, "a role"),
        ("trends", {"terms": []}, "at least one term"),
        ("trends", {"terms": ["a", "b", "c", "d", "e", "f"]}, "at most 5"),
        ("trends", {"terms": ["a,b"]}, "no commas"),
        ("trends", {"terms": ["a"], "window": "decade"}, "window must be"),
        ("trends", {"terms": ["a"], "geo": "United"}, "geo must be"),
        ("trends", {"terms": "x", "geo": "ZZZ"}, "geo must be"),
        ("interview-prep", {"company": "Acme"}, "the role"),
        ("travel", {"kind": "trains"}, "flights or hotels"),
        ("travel", {"kind": "flights", "departure_id": "AUS"}, "arrival_id"),
        (
            "travel",
            {"kind": "hotels", "q": "Austin", "check_in_date": "2026-12-01"},
            "check_out_date",
        ),
        (
            "travel",
            {
                "kind": "hotels",
                "q": "A",
                "check_in_date": "soon",
                "check_out_date": "2026-12-03",
            },
            "check_in_date",
        ),
        (
            "travel",
            {
                "kind": "hotels",
                "q": "A",
                "check_in_date": "2026-12-01",
                "check_out_date": "2026-12-03",
                "bogus": 1,
            },
            "Unknown parameter",
        ),
        ("learn", {"skill": "python", "level": "wizard"}, "level must be"),
    ],
)
def test_report_forms_are_validated_before_anything_is_called(kind, payload, message):
    with pytest.raises(ValueError, match=message):
        careers_intel.build_spec(kind, payload)
    with pytest.raises(LookupError):
        careers_intel.build_spec("horoscope", {})


def test_stored_reports_are_pruned_and_deletable(db, client, monkeypatch):
    monkeypatch.setattr(careers_intel, "MAX_REPORTS", 2)
    ids = []
    for skill in ("python", "rust", "go"):
        ids.append(
            run(db, client, "learn", {"skill": skill, "sections": ["advice"]})["id"]
        )
    assert [r["id"] for r in careers_intel.list_reports(db)] == [ids[2], ids[1]]
    assert careers_intel.list_reports(db, kind="salary") == []
    with pytest.raises(ValueError, match="kind must be"):
        careers_intel.list_reports(db, kind="nope")
    careers_intel.delete_report(db, ids[1])
    with pytest.raises(LookupError):
        careers_intel.get_report(db, ids[1])
    with pytest.raises(LookupError):
        careers_intel.delete_report(db, ids[1])


def test_overview_summarises_saved_searches_alerts_reports_and_usage(
    db, client, gateway
):
    careers.search_jobs(db, client, {"q": "barista", "save_as": "Coffee"}, now=NOW)
    alert = make_alert(db)
    run(db, client, "learn", {"skill": "python"})
    data = careers_intel.overview(
        db, configured=True, key={"source": "app", "last4": "cdef"}, now=NOW
    )
    assert data["configured"] is True and data["key"]["last4"] == "cdef"
    assert [s["name"] for s in data["saved_searches"]] == ["Coffee"]
    assert [a["id"] for a in data["alerts"]] == [alert["id"]]
    assert data["recent_intel"][0]["kind"] == "learn"
    assert len(data["playbooks"]) == 6
    assert (
        data["usage_30d"]["jobs"]["searches"] == 1
        and data["usage_30d"]["jobs"]["credits"] == 1
    )
    assert data["usage_30d"]["reports"]["searches"] == 4


# ---------------------------------------------------------------------------
# HTTP journeys
# ---------------------------------------------------------------------------


class Api:
    def __init__(self, base: str, transcript: list[bytes]) -> None:
        self.base = base
        self.transcript = transcript
        self.opener = urllib.request.build_opener(
            urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar())
        )

    def call(self, method, path, body=None):
        data = json.dumps(body).encode() if body is not None else None
        request = urllib.request.Request(self.base + path, data=data, method=method)
        request.add_header("Content-Type", "application/json")
        request.add_header("X-Requested-With", "aira")
        request.add_header("Origin", self.base)
        try:
            with self.opener.open(request, timeout=60) as response:
                status, payload = response.status, response.read()
        except urllib.error.HTTPError as error:
            status, payload = error.code, error.read()
        self.transcript.append(payload)
        return status, (json.loads(payload.decode("utf-8")) if payload else {})

    def ok(self, method, path, body=None, expect=200):
        status, payload = self.call(method, path, body)
        assert status == expect, (method, path, status, payload)
        return payload

    def post(self, path, body=None, expect=200):
        return self.ok("POST", path, {} if body is None else body, expect)

    def get(self, path, expect=200):
        return self.ok("GET", path, None, expect)


class Env:
    def __init__(
        self, tmp_path: Path, stub: SerpApiStub, base: str, transcript
    ) -> None:
        self.tmp_path = tmp_path
        self.stub = stub
        self.base = base
        self.transcript = transcript

    def api(self) -> Api:
        return Api(self.base, self.transcript)

    def user(self) -> dict[str, Any]:
        with sqlite3.connect(self.tmp_path / "accounts.sqlite3") as connection:
            connection.row_factory = sqlite3.Row
            return dict(connection.execute("SELECT * FROM users").fetchone())

    def user_db(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.user()["data_path"])
        connection.row_factory = sqlite3.Row
        return connection

    def database_bytes(self) -> bytes:
        blob = b""
        for path in self.tmp_path.rglob("*"):
            if path.is_file() and path.suffix in {
                ".sqlite3",
                ".sqlite3-wal",
                ".sqlite3-shm",
            }:
                blob += path.read_bytes()
        return blob


@pytest.fixture
def env(tmp_path, monkeypatch, stub):
    monkeypatch.delenv("SERPAPI_API_KEY", raising=False)
    monkeypatch.delenv("AIRA_SECRET_KEY", raising=False)
    monkeypatch.setattr(app, "DB_PATH", tmp_path / "applications.sqlite3")
    monkeypatch.setattr(app, "AUTH_DB_PATH", tmp_path / "accounts.sqlite3")
    monkeypatch.setattr(app, "USER_DB_ROOT", tmp_path / "accounts")
    monkeypatch.setattr(app, "_airaapi_key_cache", {})
    monkeypatch.setattr(
        app,
        "_login_limiter",
        app.RateLimiter(limit=50, window_seconds=600, lockout_seconds=900),
    )
    monkeypatch.setattr(
        app, "_api_limiter", app.RateLimiter(limit=5000, window_seconds=60)
    )
    monkeypatch.setattr(app, "_ollama_available", lambda: False)
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), app.RequestHandler)
    thread = threading.Thread(
        target=lambda: httpd.serve_forever(poll_interval=0.05), daemon=True
    )
    thread.start()
    transcript: list[bytes] = []
    try:
        yield Env(
            tmp_path, stub, f"http://127.0.0.1:{httpd.server_address[1]}", transcript
        )
    finally:
        httpd.shutdown()
        httpd.server_close()


@pytest.fixture
def api(env):
    client = env.api()
    status, body = client.call(
        "POST",
        "/api/auth/register",
        {"email": "careers@example.com", "password": PASSWORD},
    )
    assert status == 201, body
    return client


@pytest.fixture
def connected(env, api):
    api.post("/api/airaapi/key", {"api_key": DEFAULT_KEY})
    return api


def give_profile(api: Api) -> None:
    api.post(
        "/api/profile",
        {
            "resume": RESUME,
            "profile_url": "https://www.linkedin.com/in/jordan-example/",
            "resume_name": "resume.txt",
            "application_profile": {
                "first_name": "Jordan",
                "last_name": "Example",
                "email": "jordan@example.com",
                "phone": "+1 555 010 2030",
                "city": "New York",
                "state": "NY",
                "linkedin_url": "https://www.linkedin.com/in/jordan-example/",
                "years_of_experience": "12",
                "work_authorization": "authorized",
                "sponsorship_required": "no",
            },
            "matching_preferences": {
                "locations": ["New York", "Remote"],
                "verified_skills": ["barista training", "inventory", "roasting"],
            },
        },
    )


def test_every_careers_route_needs_a_session(env):
    anonymous = env.api()
    routes = [
        ("GET", "/api/careers/overview"),
        ("POST", "/api/careers/jobs/search"),
        ("POST", "/api/careers/jobs/import"),
        ("POST", "/api/careers/company-intel"),
        ("POST", "/api/careers/salary"),
        ("POST", "/api/careers/trends"),
        ("POST", "/api/careers/interview-prep"),
        ("POST", "/api/careers/travel"),
        ("POST", "/api/careers/learn"),
        ("GET", "/api/careers/reports"),
        ("GET", "/api/careers/alerts"),
        ("POST", "/api/careers/alerts"),
        ("DELETE", f"/api/careers/alerts/{'0' * 32}"),
    ]
    for method, path in routes:
        status, body = anonymous.call(method, path, {} if method == "POST" else None)
        assert status == 401 and body == {"error": "Sign in to continue."}, path
    assert env.stub.search_calls == 0


def test_without_a_key_the_workspace_explains_setup_and_dry_runs_still_work(env, api):
    overview = api.get("/api/careers/overview")
    assert overview["configured"] is False and overview["key"]["source"] is None
    assert len(overview["playbooks"]) == 6 and overview["alerts"] == []
    status, body = api.call(
        "POST", "/api/careers/jobs/search", {"q": "barista", "provider": "google"}
    )
    assert status == 409 and body["code"] == "not_configured"
    plan = api.post(
        "/api/careers/jobs/search",
        {"q": "barista", "provider": "google", "dry_run": True},
    )
    assert plan["credits"] == 1 and plan["configured"] is False
    free = api.post("/api/careers/jobs/search", {"q": "barista", "dry_run": True})
    assert free["provider"] == "aira" and free["credits"] == 0
    plan = api.post("/api/careers/company-intel", {"company": "Acme", "dry_run": True})
    assert (
        plan["credits"] == 5 and plan["configured"] is False and len(plan["plan"]) == 5
    )
    status, body = api.call("POST", "/api/careers/learn", {"skill": "python"})
    assert status == 409 and body["code"] == "not_configured"
    assert env.stub.search_calls == 0


def test_bad_forms_are_400s_and_unknown_things_404s(connected):
    for path, body in (
        ("/api/careers/jobs/search", {}),
        ("/api/careers/jobs/search", {"q": "x", "posted_within": "decade"}),
        ("/api/careers/jobs/import", {}),
        ("/api/careers/jobs/import", {"job": {"title": ""}}),
        ("/api/careers/company-intel", {}),
        ("/api/careers/trends", {"terms": []}),
        ("/api/careers/alerts", {"q": "x", "interval_minutes": 1}),
    ):
        status, payload = connected.call("POST", path, body)
        assert status == 400 and payload["error"], (path, payload)
    for method, path in (
        ("GET", f"/api/careers/reports/{'0' * 32}"),
        ("GET", f"/api/careers/alerts/{'0' * 32}"),
        ("DELETE", f"/api/careers/alerts/{'0' * 32}"),
        ("POST", f"/api/careers/alerts/{'0' * 32}/run"),
    ):
        status, _ = connected.call(method, path, {} if method == "POST" else None)
        assert status == 404, path


def test_search_then_import_is_duplicate_safe_and_scored(env, connected):
    give_profile(connected)
    found = connected.post(
        "/api/careers/jobs/search", {"q": "barista", "location": "New York"}
    )
    assert len(found["jobs"]) == 4 and found["credits"] == 1
    assert all(j["already_in_aira"] is False for j in found["jobs"])
    assert all(
        j["match"] and 0 <= j["match"]["score"] <= 100
        for j in found["jobs"]
        if j["match"]
    )
    manager = next(j for j in found["jobs"] if j["title"] == "Cafe Manager")
    assert manager["salary"]["annual_min"] == 60000
    created = connected.post("/api/careers/jobs/import", {"job": manager}, expect=201)
    assert created["created"] is True and created["duplicate_of"] is None
    row = created["job"]
    assert row["job_id"].startswith("google_jobs:") and row["source"] == "google_jobs"
    assert row["title"] == "Cafe Manager" and row["company"] == "Gregorys Coffee"
    assert (row["salary_min"], row["salary_max"], row["currency"]) == (
        60000,
        70000,
        "USD",
    )
    assert row["match_score"] is not None and row["posted_at"].startswith("2026-10-19")
    assert row["application_url"] == "https://www.linkedin.com/jobs/view/1003"
    assert row["workplace_type"] == ""
    again = connected.post("/api/careers/jobs/import", {"job": manager})
    assert again["created"] is False and again["duplicate_of"] == row["job_id"]
    assert again["matched_by"] == "source"
    jobs = connected.get("/api/jobs?view=google_jobs")
    assert jobs["total"] == 1
    refreshed = connected.post(
        "/api/careers/jobs/search", {"q": "barista", "location": "New York"}
    )
    assert refreshed["cache_hit"] is True and env.stub.search_calls == 1
    flagged = {j["title"]: j for j in refreshed["jobs"]}
    assert flagged["Cafe Manager"]["already_in_aira"] is True
    assert flagged["Cafe Manager"]["aira_job_key"] == row["job_id"]
    assert flagged["Barista"]["already_in_aira"] is False


def test_importing_a_job_already_known_from_another_portal_never_adds_a_row(
    env, connected
):
    found = connected.post("/api/careers/jobs/search", {"q": "barista"})
    barista = next(j for j in found["jobs"] if j["title"] == "Barista")
    user = env.user()
    app._bind_user(user)
    try:
        existing = app._upsert_job(
            "4242",
            source="greenhouse",
            source_url="https://careers.example.com/jobs/1000/?utm_source=newsletter",
            title="Barista",
            company="Blue Bottle Coffee",
            location="New York, NY",
            description="Greenhouse copy of the same posting",
        )
    finally:
        app._unbind_user()
    flagged = connected.post("/api/careers/jobs/search", {"q": "barista"})
    assert {j["title"]: j["already_in_aira"] for j in flagged["jobs"]}[
        "Barista"
    ] is True
    outcome = connected.post("/api/careers/jobs/import", {"job": barista})
    assert outcome["created"] is False and outcome["matched_by"] == "url"
    assert outcome["duplicate_of"] == existing["job_id"] == outcome["job"]["job_id"]
    with env.user_db() as connection:
        assert connection.execute("SELECT COUNT(*) FROM jobs").fetchone()[0] == 1
        sources = {
            row[0]
            for row in connection.execute(
                "SELECT source FROM job_source_records WHERE job_id = ?",
                (existing["job_id"],),
            )
        }
    assert sources == {"greenhouse", "google_jobs"}


def test_the_jobs_tab_google_source_shares_history_and_cache_with_careers(
    env, connected
):
    first = connected.post(
        "/api/jobs/search",
        {
            "source": "google_jobs",
            "keywords": "barista",
            "location": "New York",
            "max_pages": 1,
        },
    )
    assert first["count"] == 4 and first["source"] == "google_jobs"
    assert env.stub.count("/search.json", "google_jobs") == 1
    history = connected.get("/api/airaapi/searches?source=careers")
    assert history["total"] == 1 and history["items"][0]["label"].startswith("Jobs tab")
    shared = connected.post(
        "/api/careers/jobs/search", {"q": "barista", "location": "New York", "hl": "en"}
    )
    assert (
        shared["cache_hit"] is True
        and env.stub.count("/search.json", "google_jobs") == 1
    )
    again = connected.post(
        "/api/jobs/search",
        {
            "source": "google_jobs",
            "keywords": "barista",
            "location": "New York",
            "max_pages": 1,
        },
    )
    assert again["count"] == 4 and env.stub.count("/search.json", "google_jobs") == 1
    jobs = connected.get("/api/jobs?view=google_jobs")
    assert jobs["total"] == 4
    keys = {j["external_id"] for j in jobs["jobs"]}
    assert keys == {
        j["key"] for j in shared["jobs"]
    }  # one record per posting, whichever way it was found


def test_reports_over_http_with_estimate_cost_and_history(env, connected):
    plan = connected.post(
        "/api/careers/salary",
        {"role": "Barista", "location": "New York", "dry_run": True},
    )
    assert plan["credits"] == 3 and plan["configured"] is True
    assert [row["id"] for row in plan["plan"]] == ["estimates", "market", "community"]
    assert env.stub.search_calls == 0
    report = connected.post(
        "/api/careers/salary", {"role": "Barista", "location": "New York"}
    )
    assert report["credits"] == 3 and report["errors"] == 0
    market = next(s for s in report["sections"] if s["id"] == "market")
    assert (
        market["stats"]["median_annual"] == 53300 and market["engine"] == "google_jobs"
    )
    record = connected.get(f"/api/airaapi/searches/{market['record_id']}")
    assert record["source"] == "intel" and record["engine"] == "google_jobs"
    listed = connected.get("/api/careers/reports?kind=salary")
    assert [r["id"] for r in listed["reports"]] == [report["id"]]
    assert connected.get(f"/api/careers/reports/{report['id']}")["id"] == report["id"]
    after = connected.post(
        "/api/careers/salary",
        {"role": "Barista", "location": "New York", "dry_run": True},
    )
    assert after["credits"] == 0 and after["cache_hits"] == 3
    overview = connected.get("/api/careers/overview")
    assert overview["recent_intel"][0]["id"] == report["id"]
    assert overview["usage_30d"]["reports"]["searches"] == 3
    connected.ok("DELETE", f"/api/careers/reports/{report['id']}")
    assert connected.get("/api/careers/reports")["reports"] == []
    for kind, body in (
        ("trends", {"terms": ["python"]}),
        ("learn", {"skill": "python", "level": "beginner"}),
        ("interview-prep", {"company": "Acme", "role": "Barista"}),
        (
            "travel",
            {
                "kind": "hotels",
                "q": "Austin",
                "check_in_date": "2026-12-01",
                "check_out_date": "2026-12-03",
            },
        ),
    ):
        made = connected.post(f"/api/careers/{kind}", body)
        assert made["kind"] == kind and made["sections"] and made["errors"] == 0


def test_job_alert_journey_with_the_scheduler_auto_import_and_notification(
    env, connected
):
    give_profile(connected)
    created = connected.post(
        "/api/careers/alerts",
        {
            "q": "barista",
            "location": "New York",
            "interval_minutes": 60,
            "auto_import": True,
            "min_score": 0,
            "name": "NYC baristas",
        },
        expect=201,
    )
    alert_id = created["id"]
    assert connected.get("/api/careers/alerts")["alerts"][0]["id"] == alert_id
    assert connected.get("/api/careers/overview")["alerts"][0]["name"] == "NYC baristas"
    monitors = connected.get("/api/airaapi/monitors")["monitors"]
    assert len(monitors) == 1 and monitors[0]["estimated_monthly_searches"] == 720
    baseline = connected.post(f"/api/careers/alerts/{alert_id}/run")
    assert (
        baseline["summary"].startswith("Baseline captured")
        and baseline["alert"]["new_jobs"] == []
    )

    add_unique_jobs(env.stub, 2)
    user = env.user()
    with env.user_db() as connection:
        connection.execute(
            "UPDATE airaapi_monitors SET next_run_at = '2000-01-01T00:00:00Z'"
        )
        connection.commit()
    app._run_airaapi_monitors_for(user)  # what the scheduler does for a due monitor
    detail = connected.get(f"/api/careers/alerts/{alert_id}")
    assert detail["new_count"] == 2 and detail["last_found"] == 2
    titles = sorted(j["title"] for j in detail["new_jobs"])
    assert titles == ["Added role 0", "Added role 1"]
    assert detail["last_imported"] == 2  # score threshold 0: both were scored and saved
    assert all(j["already_in_aira"] and j["aira_job_key"] for j in detail["new_jobs"])
    jobs = connected.get("/api/jobs?view=google_jobs")
    assert jobs["total"] == 2 and {j["title"] for j in jobs["jobs"]} == set(titles)
    notices = connected.get("/api/notifications")["notifications"]
    assert notices[0]["title"] == "2 new jobs: NYC baristas"
    assert notices[0]["link"] == f"#/careers?tab=alerts&alert={alert_id}"
    assert "2 imported into Jobs" in notices[0]["body"]
    with env.user_db() as connection:
        due = connection.execute(
            "SELECT next_run_at, last_change_summary FROM airaapi_monitors"
        ).fetchone()
    assert due["next_run_at"] > "2026" and "2 new jobs" in due["last_change_summary"]

    cleared = connected.post(f"/api/careers/alerts/{alert_id}/dismiss", {"all": True})
    assert cleared["dismissed"] == 2 and cleared["alert"]["new_count"] == 0
    connected.ok("POST", f"/api/careers/alerts/{alert_id}/dismiss", {}, expect=400)
    patched = connected.ok(
        "PATCH",
        f"/api/careers/alerts/{alert_id}",
        {"enabled": False, "auto_import": False},
    )
    assert patched["enabled"] is False and patched["auto_import"] is False
    connected.ok("DELETE", f"/api/careers/alerts/{alert_id}")
    assert connected.get("/api/careers/alerts")["alerts"] == []
    assert connected.get("/api/airaapi/monitors")["monitors"] == []
    assert connected.get("/api/airaapi/saved")["saved"] == []


def test_an_alert_below_the_threshold_lists_jobs_but_imports_nothing(env, connected):
    give_profile(connected)
    created = connected.post(
        "/api/careers/alerts",
        {"q": "barista", "interval_minutes": 60, "auto_import": True, "min_score": 100},
        expect=201,
    )
    connected.post(f"/api/careers/alerts/{created['id']}/run")
    add_unique_jobs(env.stub, 1)
    result = connected.post(f"/api/careers/alerts/{created['id']}/run")
    assert result["found"] == 1 and result["imported"] == 0
    assert connected.get("/api/jobs?view=google_jobs")["total"] == 0
    new = result["alert"]["new_jobs"][0]
    assert new["title"] == "Added role 0" and new["imported_job_id"] is None
    imported = connected.post("/api/careers/jobs/import", {"job": new}, expect=201)
    assert imported["created"] is True
    detail = connected.get(f"/api/careers/alerts/{created['id']}")
    assert detail["new_jobs"][0]["imported_job_id"] == imported["job"]["job_id"]


def test_account_export_includes_careers_data_and_secrets_never_leak(
    env, connected, caplog
):
    connected.post(
        "/api/careers/company-intel", {"company": "Acme", "sections": ["news"]}
    )
    connected.post(
        "/api/careers/alerts", {"q": "barista", "interval_minutes": 60}, expect=201
    )
    exported = connected.get("/api/account/export")
    assert len(exported["tables"]["careers_alerts"]) == 1
    assert len(exported["tables"]["careers_reports"]) == 1
    needle = DEFAULT_KEY.encode()
    assert not any(needle in body for body in env.transcript)
    assert needle not in env.database_bytes()
    assert DEFAULT_KEY not in caplog.text


def test_jobs_tab_search_and_a_careers_search_without_hl_share_one_cache_key(
    env, connected
):
    connected.post(
        "/api/jobs/search",
        {
            "source": "google_jobs",
            "keywords": "porter",
            "location": "Boston",
            "max_pages": 1,
        },
    )
    assert env.stub.count("/search.json", "google_jobs") == 1
    shared = connected.post(
        "/api/careers/jobs/search", {"q": "porter", "location": "Boston"}
    )
    assert shared["cache_hit"] is True and shared["credits"] == 0
    assert env.stub.count("/search.json", "google_jobs") == 1


def test_a_stored_google_jobs_search_lists_its_jobs_for_import(env, connected):
    found = connected.post(
        "/api/careers/jobs/search", {"q": "barista", "location": "New York"}
    )
    listed = connected.get(f"/api/careers/records/{found['record_id']}/jobs")
    assert listed["record_id"] == found["record_id"]
    assert [job["key"] for job in listed["jobs"]] == [
        job["key"] for job in found["jobs"]
    ]
    assert all(job["already_in_aira"] is False for job in listed["jobs"])
    imported = connected.post(
        "/api/careers/jobs/import", {"job": listed["jobs"][0]}, expect=201
    )
    assert imported["created"] is True
    again = connected.get(f"/api/careers/records/{found['record_id']}/jobs")
    assert again["jobs"][0]["already_in_aira"] is True


def test_only_a_google_jobs_record_can_be_listed_as_jobs(env, connected):
    record = connected.post(
        "/api/airaapi/search", {"engine": "google", "params": {"q": "coffee"}}
    )
    status, body = connected.call("GET", f"/api/careers/records/{record['id']}/jobs")
    assert status == 400 and "Google Jobs" in body["error"]
