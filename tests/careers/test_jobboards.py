"""Aira Jobs: API's own job search over free public job boards (no SerpApi).

Board responses are recorded-shape payloads served by a fake getter, so nothing
here touches the network.
"""

from __future__ import annotations

import sqlite3
import threading
from datetime import datetime, timedelta, timezone

import pytest

import auto_apply_assistant as app
from aira.airaapi import catalog, jobboards, service, store
from aira.airaapi.client import SerpApiError
from ats_stub import AtsStub

NOW = datetime(2026, 10, 23, 12, 0, tzinfo=timezone.utc)

HIMALAYAS = {
    "comments": "",
    "jobs": [
        {
            "title": "Senior Cloud Solution Architect (OCI)",
            "excerpt": "Design cloud platforms.",
            "companyName": "Arrow Electronics",
            "employmentType": "Full Time",
            "minSalary": 126000,
            "maxSalary": 200000,
            "currency": "USD",
            "salaryPeriod": "year",
            "locationRestrictions": ["United States"],
            "description": "<p>Design <strong>cloud</strong> platforms on AWS and OCI.</p><ul><li>Kubernetes</li></ul>",
            "pubDate": int((NOW - timedelta(hours=14)).timestamp()),
            "applicationLink": "https://himalayas.app/companies/arrow/jobs/senior-cloud-solution-architect-1?utm_source=api",
            "guid": "https://himalayas.app/companies/arrow/jobs/senior-cloud-solution-architect-1",
            "companyLogo": "https://cdn.himalayas.app/arrow.png",
        },
        {
            "title": "Data Engineer",
            "companyName": "Other",
            "employmentType": "Full Time",
            "locationRestrictions": [],
            "description": "Pipelines",
            "pubDate": int(NOW.timestamp()),
            "applicationLink": "https://himalayas.app/companies/other/jobs/data-engineer-2",
            "guid": "x2",
        },
    ],
}
MUSE = {
    "results": [
        {
            "id": 12345,
            "name": "Cloud Architect",
            "contents": "<p>Lead our <b>AWS</b> architecture.</p>",
            "publication_date": (NOW - timedelta(days=2)).isoformat(),
            "locations": [{"name": "Austin, TX"}],
            "levels": [{"name": "Senior Level"}],
            "company": {"name": "GlossGenius"},
            "refs": {
                "landing_page": "https://www.themuse.com/jobs/glossgenius/cloud-architect-7d1ec3"
            },
        }
    ]
}
REMOTIVE = {
    "0-legal-notice": "Please link back.",
    "jobs": [
        {
            "id": 2091158,
            "url": "https://remotive.com/remote-jobs/software-development/cloud-architect-2091158",
            "title": "Cloud Architect",
            "company_name": "Askable",
            "job_type": "full_time",
            "publication_date": (NOW - timedelta(days=1)).isoformat(),
            "candidate_required_location": "USA",
            "salary": "$150k",
            "description": "<p>Remote cloud work.</p>",
        },
        {
            "id": 1,
            "url": "https://remotive.com/remote-jobs/x/designer-1",
            "title": "Product Designer",
            "company_name": "Y",
            "job_type": "contract",
            "publication_date": NOW.isoformat(),
            "candidate_required_location": "Worldwide",
            "description": "Design",
        },
    ],
}
JOBICY = {
    "jobs": [
        {
            "id": 156680,
            "url": "https://jobicy.com/jobs/156680-principal-cloud-architect",
            "jobTitle": "Principal Cloud Architect &amp; Lead",
            "companyName": "Initech",
            "jobType": ["full-time"],
            "jobGeo": "USA, Canada",
            "jobDescription": "<p>Cloud.</p>",
            "pubDate": "2026-10-20 09:00:00",
            "salaryMin": 180000,
            "salaryMax": 220000,
            "salaryCurrency": "USD",
            "salaryPeriod": "yearly",
        }
    ]
}
ARBEITNOW = {
    "data": [
        {
            "slug": "cloud-architect-berlin-215171",
            "company_name": "Cell Labs",
            "title": "Cloud Architect",
            "description": "<h2>Aufgaben</h2><p>Cloud</p>",
            "remote": False,
            "url": "https://www.arbeitnow.com/jobs/companies/cell-labs/cloud-architect-berlin-215171",
            "tags": [],
            "job_types": ["full time"],
            "location": "Berlin",
            "created_at": int((NOW - timedelta(days=3)).timestamp()),
        }
    ]
}


class Boards:
    """A fake getter: answers each board's URL with its payload and counts the calls."""

    def __init__(self, fail: set[str] | None = None):
        self.calls: list[str] = []
        self.fail = fail or set()

    def __call__(self, url: str):
        self.calls.append(url)
        board = jobboards.board_of(url) or ("himalayas" if "himalayas" in url else "")
        if board in self.fail:
            raise SerpApiError("upstream_error", f"{board} is down", 503)
        if "himalayas" in url:
            return HIMALAYAS
        if "themuse" in url:
            return MUSE
        if "remotive" in url:
            return REMOTIVE
        if "jobicy" in url:
            return JOBICY
        if "arbeitnow" in url:
            return ARBEITNOW
        raise AssertionError(url)


@pytest.fixture
def db():
    connection = sqlite3.connect(":memory:")
    store.ensure_schema(connection)
    yield connection
    connection.close()


# -- the board client ---------------------------------------------------------------


def test_every_board_is_normalized_and_credited():
    boards = Boards()
    body = jobboards.search({"q": "cloud architect"}, getter=boards, now=NOW)
    by_board = {item["aira_source"]: item for item in body["jobs_results"]}
    assert set(by_board) == {"himalayas", "themuse", "remotive", "jobicy", "arbeitnow"}
    hima = by_board["himalayas"]
    assert hima["via"] == "via Himalayas" and hima["attribution"].startswith(
        "Jobs from Himalayas"
    )
    assert (
        hima["share_link"].startswith("https://himalayas.app/")
        and hima["apply_options"][0]["link"] == hima["share_link"]
    )
    assert hima["detected_extensions"] == {
        "posted_at": "14 hours ago",
        "schedule_type": "Full-time",
        "work_from_home": True,
        "salary": "$126K–$200K a year",
    }
    assert "<" not in hima["description"] and "- Kubernetes" in hima["description"]
    assert by_board["jobicy"]["title"] == "Principal Cloud Architect & Lead"
    assert by_board["arbeitnow"]["location"] == "Berlin" and not by_board["arbeitnow"][
        "detected_extensions"
    ].get("work_from_home")
    assert all(
        item["job_id"].startswith(item["aira_source"] + "-")
        for item in body["jobs_results"]
    )
    # the posting that does not match the query is dropped, and results are newest first
    assert not any(
        item["title"] in ("Data Engineer", "Product Designer")
        for item in body["jobs_results"]
    )
    stamps = [item["posted_at_iso"] for item in body["jobs_results"]]
    assert stamps == sorted(stamps, reverse=True)
    assert [b["results"] for b in body["search_information"]["boards"]] == [
        1,
        1,
        1,
        1,
        1,
    ]


@pytest.mark.parametrize(
    ("params", "expected"),
    [
        (
            {"q": "cloud architect", "remote_only": True},
            {"himalayas", "remotive", "jobicy"},
        ),
        (
            {"q": "cloud architect", "location": "Remote"},
            {"himalayas", "remotive", "jobicy"},
        ),
        ({"q": "cloud architect", "location": "Berlin, Germany"}, {"arbeitnow"}),
        (
            {"q": "cloud architect", "location": "Austin, TX"},
            {"himalayas", "themuse", "remotive", "jobicy"},
        ),
        ({"q": "cloud architect", "boards": "remotive,jobicy"}, {"remotive", "jobicy"}),
        (
            {"q": "cloud architect", "employment_type": "contract"},
            {"themuse"},
        ),  # only boards that do not say stay
    ],
)
def test_filters(params, expected):
    body = jobboards.search(params, getter=Boards(), now=NOW)
    assert {item["aira_source"] for item in body["jobs_results"]} == expected


def test_query_validation():
    for params, message in (
        ({}, "Enter what to search for"),
        ({"q": "x", "boards": "monster"}, "Unknown board"),
        ({"q": "x", "employment_type": "gig"}, "employment_type"),
        ({"q": "x", "num": "many"}, "num"),
    ):
        with pytest.raises(ValueError, match=message):
            jobboards.parse_query(params)
    assert jobboards.parse_query({"q": "x", "num": 999})["num"] == 100
    assert jobboards.country_code("Austin, TX") == "US"
    assert jobboards.country_code("Munich, Germany") == "DE"
    assert jobboards.country_code("Remote") == ""


def test_board_fetches_are_cached_per_board(db):
    boards = Boards()
    jobboards.search({"q": "cloud architect"}, connection=db, getter=boards, now=NOW)
    first = len(boards.calls)
    # A different query reuses the Remotive and Arbeitnow feeds (filtered locally).
    jobboards.search(
        {"q": "product designer"},
        connection=db,
        getter=boards,
        now=NOW + timedelta(minutes=5),
    )
    again = boards.calls[first:]
    assert not any("remotive" in url or "arbeitnow" in url for url in again)
    # Seven hours later the Remotive cache (6 h) has expired; Himalayas' (12 h) has not.
    later = len(boards.calls)
    jobboards.search(
        {"q": "cloud architect"},
        connection=db,
        getter=boards,
        now=NOW + timedelta(hours=7),
    )
    refetched = boards.calls[later:]
    assert any("remotive" in url for url in refetched) and not any(
        "himalayas" in url for url in refetched
    )


def test_one_board_down_is_reported_all_down_is_an_error():
    body = jobboards.search(
        {"q": "cloud architect"}, getter=Boards(fail={"himalayas"}), now=NOW
    )
    info = {b["id"]: b for b in body["search_information"]["boards"]}
    assert "down" in info["himalayas"]["error"] and info["remotive"]["results"] == 1
    with pytest.raises(SerpApiError, match="No job board could be searched"):
        jobboards.search(
            {"q": "cloud architect"}, getter=Boards(fail=set(jobboards.BOARDS)), now=NOW
        )


def test_apply_link_resolution():
    muse = "https://www.themuse.com/jobs/glossgenius/cloud-architect-7d1ec3"
    page = '<a href="https://job-boards.greenhouse.io/glossgenius/jobs/7765141003">Apply</a>'
    assert (
        jobboards.resolve_application_url(muse, page_getter=lambda _url: page)
        == "https://job-boards.greenhouse.io/glossgenius/jobs/7765141003"
    )
    assert (
        jobboards.resolve_application_url(
            muse, page_getter=lambda _url: "<p>no form</p>"
        )
        == ""
    )
    hima = "https://himalayas.app/companies/x/jobs/y"
    assert (
        jobboards.resolve_application_url(hima, page_getter=lambda _url: page) == ""
    )  # never opened
    other = "https://boards.greenhouse.io/acme/jobs/1"
    assert jobboards.resolve_application_url(other) == other


def test_posting_from_item_keeps_the_board_as_source():
    body = jobboards.search(
        {"q": "cloud architect", "boards": "himalayas"}, getter=Boards(), now=NOW
    )
    posting = jobboards.posting_from_item(body["jobs_results"][0])
    assert posting.source == "aira_jobs" and posting.external_id.startswith(
        "himalayas-"
    )
    assert posting.workplace_type == "remote" and posting.employment_type == "full_time"
    assert (
        "Jobs from Himalayas" in posting.description
        and posting.application_url.startswith("https://himalayas.app/")
    )


# -- the engine inside API ----------------------------------------------------------


def test_engine_runs_without_a_key_at_no_cost_and_outside_the_hourly_cap(
    db, monkeypatch
):
    monkeypatch.setattr(jobboards, "http_json", Boards())
    assert (
        catalog.engine("aira_jobs").provider == "aira"
        and catalog.credit_cost("aira_jobs") == 0
    )
    limits = service.Limits(hourly_limit=1)
    first = service.run_search(
        db, None, "aira_jobs", {"q": "cloud architect"}, limits=limits, now=NOW
    )
    assert (
        first["status"] == "ok" and first["credits"] == 0 and first["result_count"] == 5
    )
    assert first["serpapi_id"] == "" and first["main_result_key"] == "jobs_results"
    second = service.run_search(
        db, None, "aira_jobs", {"q": "product designer"}, limits=limits, now=NOW
    )
    assert (
        second["status"] == "ok"
    )  # the cap of 1 live SerpApi search per hour does not apply
    assert (
        store.count_live_since(
            db,
            store.to_iso(NOW - timedelta(hours=1)),
            exclude_engines=catalog.local_engine_ids(),
        )
        == 0
    )
    cached = service.run_search(
        db,
        None,
        "aira_jobs",
        {"q": "cloud architect"},
        limits=limits,
        now=NOW + timedelta(minutes=1),
    )
    assert cached["cache_hit"]
    with pytest.raises(SerpApiError) as failure:
        service.run_search(
            db, None, "aira_jobs", {"q": "x"}, options={"output": "html"}, now=NOW
        )
    assert failure.value.code == "bad_request"
    with pytest.raises(SerpApiError) as failure:
        service.run_search(db, None, "google_jobs", {"q": "x"}, now=NOW)
    assert failure.value.code == "not_configured"  # Google still needs the key


# -- HTTP journeys: Jobs tab, Careers, /v1 and Auto-apply without a SerpApi key ----------------


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setattr(app, "DB_PATH", tmp_path / "applications.sqlite3")
    monkeypatch.setattr(app, "AUTH_DB_PATH", tmp_path / "accounts.sqlite3")
    monkeypatch.setattr(app, "USER_DB_ROOT", tmp_path / "accounts")
    monkeypatch.delenv("SERPAPI_API_KEY", raising=False)
    monkeypatch.setattr(
        app,
        "_login_limiter",
        app.RateLimiter(limit=50, window_seconds=600, lockout_seconds=900),
    )
    monkeypatch.setattr(
        app, "_api_limiter", app.RateLimiter(limit=5000, window_seconds=60)
    )
    monkeypatch.setattr(app, "_ollama_available", lambda: False)
    boards = Boards()
    monkeypatch.setattr(jobboards, "http_json", boards)
    from http.server import ThreadingHTTPServer

    from test_career_copilot import Client

    httpd = ThreadingHTTPServer(("127.0.0.1", 0), app.RequestHandler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    client = Client(f"http://127.0.0.1:{httpd.server_address[1]}")
    try:
        yield client, boards
    finally:
        httpd.shutdown()
        httpd.server_close()


def ok(client, method, path, body=None):
    status, payload, _ = client.call(method, path, body)
    assert status in (200, 201, 202), (method, path, status, payload)
    return payload


def test_http_jobs_tab_careers_and_v1_without_a_serpapi_key(env):
    client, boards = env
    from test_autopilot import sign_up

    sign_up(client)
    sources = {s["source"]: s for s in ok(client, "GET", "/api/sources")["sources"]}
    assert sources["aira_jobs"]["connected"] and not sources["google_jobs"]["connected"]
    found = ok(
        client,
        "POST",
        "/api/jobs/search",
        {
            "source": "aira_jobs",
            "keywords": "cloud architect",
            "location": "United States",
        },
    )
    assert found["count"] >= 3 and found["source"] == "aira_jobs"
    listed = ok(client, "GET", "/api/jobs?view=aira_jobs")
    assert listed["jobs"] and all(
        "aira_jobs" in (job.get("sources") or ["aira_jobs"]) for job in listed["jobs"]
    )
    job = ok(client, "GET", f"/api/jobs/{found['jobs'][0]['job_id']}")["job"]
    assert (
        job["source_records"][0]["source"] == "aira_jobs"
        and job["match_score"] is not None
    )
    history = ok(client, "GET", "/api/airaapi/searches?engine=aira_jobs")
    assert history["total"] == 1 and history["items"][0]["credits"] == 0
    # Careers: no key, so it searches Aira Jobs; an import keeps the board as the source.
    careers = ok(client, "POST", "/api/careers/jobs/search", {"q": "cloud architect"})
    assert careers["provider"] == "aira" and careers["credits"] == 0 and careers["jobs"]
    assert {b["id"] for b in careers["boards"]} == set(jobboards.BOARDS)
    pick = next(j for j in careers["jobs"] if j["aira_source"] == "jobicy")
    imported = ok(client, "POST", "/api/careers/jobs/import", {"job": pick})
    assert imported["job"]["job_id"].startswith("aira_jobs:jobicy-")
    again = ok(client, "POST", "/api/careers/jobs/import", {"job": pick})
    assert again["created"] is False
    # /v1: an Aira key answers engine=aira_jobs with no SerpApi key at all.
    key = ok(client, "POST", "/api/airaapi/keys", {"name": "script"})
    secret = key.get("secret") or key.get("api_key")
    status, body, _ = client.call(
        "GET", f"/v1/search.json?engine=aira_jobs&q=cloud+architect&api_key={secret}"
    )
    assert (
        status == 200
        and body["jobs_results"]
        and body["search_metadata"]["provider"] == "aira"
    )
    status, body, _ = client.call(
        "GET", f"/v1/search.json?engine=google_jobs&q=cloud&api_key={secret}"
    )
    assert status == 409


def test_http_auto_apply_plan_finds_jobs_on_aira_jobs_and_applies(env, monkeypatch):
    client, boards = env
    from test_autopilot import sign_up, wait_idle

    stub = AtsStub().start()
    try:
        sign_up(client)
        # The Muse page leads to the employer's Greenhouse form (here the stand-in ATS).
        monkeypatch.setattr(
            app.airaapi_jobboards,
            "resolve_application_url",
            lambda url, **_: stub.form_url("greenhouse") if "themuse" in url else "",
        )
        plan = ok(
            client,
            "POST",
            "/api/autopilot/plans",
            {
                "name": "Free boards",
                "positions": ["Cloud Architect"],
                "sources": ["aira_jobs"],
                "location": "Austin, TX",
                "min_score": 0,
                "max_per_run": 5,
                "mode": "apply",
            },
        )["plan"]
        ok(client, "POST", f"/api/autopilot/plans/{plan['plan_id']}/run", {})
        overview = wait_idle(client)
        [muse] = [run for run in overview["recent"] if run["status"] == "submitted"]
        assert muse["job"]["company"] == "GlossGenius" and muse[
            "confirmation_number"
        ].startswith("GH-")
        assert len(stub.submissions) == 1
        others = [run for run in overview["recent"] if run is not muse]
        assert len(others) == 3
        assert all(
            run["status"] == "unsupported"
            and "page, which does not lead" in run["error"]
            for run in others
        )
        assert overview["passes"][0]["counts"]["found"] >= 4
    finally:
        stub.stop()
