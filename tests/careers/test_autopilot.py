"""Auto-apply: plans, qualification rules, the run pipeline and the HTTP journeys.

The unit tests drive :func:`aira.autopilot.run_plan` with fake services. The
HTTP journeys start the real server with a stand-in company board (a fake
adapter whose postings point at ``tests/ats_stub.py``) and let the real form
engine fill and submit those forms in headless Chromium: search, score, apply,
hold for answers, remember answers, review mode and the one-off "Apply for me".
"""

from __future__ import annotations

import sqlite3
import threading
import time
from datetime import datetime, timedelta, timezone
from http.server import ThreadingHTTPServer

import pytest

import auto_apply_assistant as app
from aira import autopilot, schema
from ats_stub import AtsStub
from linkedin_mcp_server.job_sources import JobPosting, JobSearchResult
from test_career_copilot import PROFILE, RESUME, Client

NOW = datetime(2026, 10, 23, 12, 0, tzinfo=timezone.utc)
DESCRIPTION = (
    "Requirements:\n- 10+ years of experience with AWS, Kubernetes, and Terraform.\n"
    "- Python and microservices.\n- Enterprise architecture and stakeholder management.\n"
    "Nice to have:\n- Azure."
)


# --------------------------------------------------------------------------- #
# Plans and rules
# --------------------------------------------------------------------------- #


@pytest.fixture
def db():
    connection = sqlite3.connect(":memory:")
    connection.row_factory = sqlite3.Row
    schema.migrate(connection)
    yield connection
    connection.close()


def plan_payload(**extra):
    return {
        "name": "Architect roles",
        "positions": ["Cloud Architect", "Solution Architect"],
        "location": "Remote",
        "sources": [],
        "boards": [{"source": "lever", "identifier": "acme"}],
        "min_score": 60,
        "max_per_run": 2,
        "interval_minutes": 240,
        "mode": "apply",
        **extra,
    }


def test_plan_validation_and_defaults(db):
    plan = autopilot.create_plan(db, plan_payload(), now=NOW)
    assert plan["positions"] == ["Cloud Architect", "Solution Architect"]
    assert plan["enabled"] and plan["match_title"] and not plan["linkedin_easy_apply"]
    assert plan["next_run_at"] == "2026-10-23T12:01:00+00:00"
    assert plan["searches_per_run"] == 2 and plan["google_searches_per_month"] == 0
    google = autopilot.create_plan(
        db, plan_payload(sources=["google_jobs"], boards=[]), now=NOW
    )
    assert google["google_searches_per_month"] == 2 * 180
    for bad, message in (
        ({"positions": []}, "at least one position"),
        ({"sources": [], "boards": []}, "at least one place"),
        ({"sources": ["monster"]}, "Unknown source"),
        ({"boards": [{"source": "workday", "identifier": "x"}]}, "Greenhouse, Lever"),
        ({"boards": [{"source": "lever", "identifier": "bad id!"}]}, "identifier"),
        ({"min_score": 150}, "between 0 and 100"),
        ({"max_per_run": 0}, "between 1 and 10"),
        ({"interval_minutes": 10}, "between 60"),
        ({"mode": "yolo"}, "Mode"),
        ({"positions": [f"Role {i}" for i in range(6)]}, "at most 5"),
    ):
        with pytest.raises(ValueError, match=message):
            autopilot.validate_plan(plan_payload(**bad))
    updated = autopilot.update_plan(
        db, plan["plan_id"], {"enabled": False, "mode": "review"}, now=NOW
    )
    assert not updated["enabled"] and updated["mode"] == "review"
    resumed = autopilot.update_plan(
        db, plan["plan_id"], {"enabled": True}, now=NOW + timedelta(hours=1)
    )
    assert resumed["next_run_at"] == "2026-10-23T13:01:00+00:00"
    autopilot.delete_plan(db, plan["plan_id"])
    with pytest.raises(LookupError):
        autopilot.get_plan(db, plan["plan_id"])


@pytest.mark.parametrize(
    ("title", "positions", "expected"),
    [
        ("Principal Cloud Architect", ["Cloud Architect"], True),
        ("Senior Solutions Architect - AWS", ["Solution Architect"], True),
        (
            "Cloud Engineer",
            ["Cloud Architect"],
            True,
        ),  # half of the key words is enough
        ("Data Engineer", ["Cloud Architect"], False),
        ("Staff Software Engineer", ["Senior Software Engineer"], True),
        ("Sales Manager", ["Senior Software Engineer", "Product Manager"], True),
        ("Nurse", ["Senior Software Engineer"], False),
        ("", ["Cloud Architect"], False),
    ],
)
def test_title_matching(title, positions, expected):
    assert autopilot.title_matches(title, positions) is expected


def test_candidate_reasons():
    plan = autopilot.validate_plan(
        plan_payload(exclude_keywords=["clearance"], exclude_companies=["Evil Corp"])
    )
    good = {
        "title": "Cloud Architect",
        "company": "Acme",
        "description": DESCRIPTION,
        "stage": "discovered",
        "match_score": 80,
        "match_classification": "Strong Match",
        "match_explanation": {},
    }
    assert autopilot.candidate_reason(plan, good) == ""
    cases = {
        "archived": {"archived_at": "2026-01-01"},
        "already in your pipeline": {"stage": "applied"},
        "no description": {"description": ""},
        "title does not match": {"title": "Nurse"},
        "company Evil Corp": {"company": "Evil Corp Inc"},
        "mentions 'clearance'": {
            "description": DESCRIPTION + " Active secret clearance required."
        },
        "could not be scored": {"match_score": None},
        "has a blocker": {"match_explanation": {"hard_blockers": ["No sponsorship"]}},
        "Not Recommended": {"match_classification": "Not Recommended"},
        "score below 60": {"match_score": 59},
    }
    for expected, change in cases.items():
        assert expected in autopilot.candidate_reason(plan, {**good, **change}), (
            expected
        )
    loose = autopilot.validate_plan(plan_payload(match_title=False))
    assert autopilot.candidate_reason(loose, {**good, "title": "Nurse"}) == ""


# --------------------------------------------------------------------------- #
# The pipeline with fake services
# --------------------------------------------------------------------------- #


class Fakes:
    """In-memory jobs and an apply() that answers from a per-job script."""

    def __init__(self, db, jobs, outcomes=None):
        self.db = db
        self.jobs = {
            job["job_id"]: {"stage": "discovered", "match_explanation": {}, **job}
            for job in jobs
        }
        self.outcomes = outcomes or {}
        self.applied: list[tuple[str, dict, bool]] = []
        self.submitted: list[str] = []
        self.held: list[str] = []
        self.notes: list[tuple[str, str]] = []
        self.remaining: int | None = None
        self.is_paused = False
        self.down: dict[str, str] = {}
        self.search_calls: list[tuple[str, str, str]] = []
        self.search_error: Exception | None = None

    def connect(self):
        return _Borrowed(self.db)

    def search(self, plan, source, identifier, position):
        self.search_calls.append((source, identifier, position))
        if self.search_error:
            raise self.search_error
        return [dict(job) for job in self.jobs.values()]

    def load_and_score(self, job_id):
        return dict(self.jobs[job_id])

    def apply(self, job, run, answers, submit, plan):
        self.applied.append((job["job_id"], dict(answers), submit))
        script = self.outcomes.get(job["job_id"], "submitted")
        if callable(script):
            script = script(answers, submit)
        if isinstance(script, dict):
            return script
        if script == "submitted" and not submit:
            script = "filled"
        return {
            "status": script,
            "route": "form",
            "ats": "lever",
            "unanswered": [
                {"label": "Favourite colour", "required": True, "status": "unknown"}
            ]
            if script == "needs_review"
            else [],
            "confirmation_number": "LEV-1" if script == "submitted" else "",
            "steps": ["Opened", "Filled"],
        }

    def on_submitted(self, job, run, outcome):
        self.submitted.append(job["job_id"])
        self.db.execute(
            "INSERT INTO application_submissions (submission_id, job_id, submitted_at, method, confirmation_text, confirmation_number, actor, correlation_id, idempotency_key) VALUES (?, ?, ?, 'auto', '', ?, 'test', '', ?)",
            (
                run["run_id"],
                job["job_id"],
                NOW.isoformat(),
                outcome["confirmation_number"],
                run["run_id"],
            ),
        )
        self.jobs[job["job_id"]]["stage"] = "applied"
        if self.remaining is not None:
            self.remaining -= 1

    def on_held(self, job, run, outcome):
        self.held.append(job["job_id"])

    def services(self):
        return autopilot.Services(
            connect=self.connect,
            search=self.search,
            load_and_score=self.load_and_score,
            apply=self.apply,
            on_submitted=self.on_submitted,
            on_held=self.on_held,
            job=lambda job_id: dict(self.jobs[job_id]) if job_id in self.jobs else None,
            daily_remaining=lambda: self.remaining,
            paused=lambda: self.is_paused,
            notify=lambda title, body, link: self.notes.append((title, body)),
            now=lambda: NOW,
            unavailable=lambda source: self.down.get(source, ""),
        )


class _Borrowed:
    """A context manager over the shared in-memory connection that never closes it."""

    def __init__(self, connection):
        self.connection = connection

    def __enter__(self):
        return self.connection

    def __exit__(self, *exc):
        self.connection.commit()
        return False


def jobs(*specs):
    out = []
    for index, (title, score) in enumerate(specs):
        out.append(
            {
                "job_id": f"lever:{index}",
                "title": title,
                "company": f"Company {index}",
                "description": DESCRIPTION,
                "match_score": score,
                "match_classification": "Strong Match" if score >= 70 else "Good Match",
                "application_url": f"https://jobs.lever.co/acme/{index}",
            }
        )
    return out


def test_pass_applies_to_the_best_matches_once(db):
    fakes = Fakes(
        db,
        jobs(
            ("Cloud Architect", 91),
            ("Solution Architect", 84),
            ("Cloud Architect II", 77),
            ("Nurse", 99),
            ("Cloud Architect", 40),
        ),
    )
    plan = autopilot.create_plan(db, plan_payload(), now=NOW)
    result = autopilot.run_plan(fakes.services(), plan["plan_id"], trigger="user")
    assert [job_id for job_id, _, _ in fakes.applied] == [
        "lever:0",
        "lever:1",
    ]  # best two, max 2 per run
    assert fakes.submitted == ["lever:0", "lever:1"]
    counts = result["counts"]
    assert (
        counts["found"] == 5 and counts["qualified"] == 3 and counts["submitted"] == 2
    )
    assert counts["reasons"]["title does not match the positions"] == 1
    assert counts["reasons"]["score below 60"] == 1
    assert result["summary"].startswith("5 found, 3 matched, 2 applied")
    assert any("limit of 2" in line for line in result["log"])
    assert [title for title, _ in fakes.notes] == [
        "Applied automatically",
        "Applied automatically",
    ]
    current = autopilot.get_plan(db, plan["plan_id"])
    assert (
        current["last_status"] == "ok" and current["last_summary"] == result["summary"]
    )
    # A second pass never touches the two jobs again and takes the next one.
    autopilot.run_plan(fakes.services(), plan["plan_id"])
    assert [job_id for job_id, _, _ in fakes.applied] == [
        "lever:0",
        "lever:1",
        "lever:2",
    ]
    third = autopilot.run_plan(fakes.services(), plan["plan_id"])
    assert (
        len(fakes.applied) == 3 and third["counts"]["new"] == 2
    )  # the nurse and the low score


def test_held_run_resumes_with_answers_and_remembers(db):
    fakes = Fakes(
        db,
        jobs(("Cloud Architect", 90)),
        outcomes={
            "lever:0": lambda answers, submit: (
                "submitted" if answers.get("Favourite colour") else "needs_review"
            )
        },
    )
    plan = autopilot.create_plan(db, plan_payload(), now=NOW)
    autopilot.run_plan(fakes.services(), plan["plan_id"])
    [run] = autopilot.list_runs(db)
    assert run["status"] == "needs_review" and run["open"] and fakes.held == ["lever:0"]
    assert run["unanswered"][0]["label"] == "Favourite colour"
    assert fakes.notes[-1][0] == "Auto-apply needs you"
    done = autopilot.execute_run(
        fakes.services(), run["run_id"], answers={"Favourite colour": "Green"}
    )
    assert done["status"] == "submitted" and done["answers"] == {
        "Favourite colour": "Green"
    }
    assert fakes.applied[-1] == ("lever:0", {"Favourite colour": "Green"}, True)
    autopilot.save_custom_answer(db, "Favourite colour?", "Green")
    assert autopilot.custom_answers(db) == {"Favourite colour?": "Green"}
    assert autopilot.list_custom_answers(db)[0]["label_key"] == "favourite colour"


def test_review_mode_fills_then_waits_for_the_click(db):
    fakes = Fakes(db, jobs(("Cloud Architect", 90)))
    plan = autopilot.create_plan(db, plan_payload(mode="review"), now=NOW)
    autopilot.run_plan(fakes.services(), plan["plan_id"])
    [run] = autopilot.list_runs(db, status="open")
    assert (
        run["status"] == "ready"
        and fakes.applied[-1][2] is False
        and not fakes.submitted
    )
    assert fakes.notes[-1][0] == "Ready to submit"
    done = autopilot.execute_run(fakes.services(), run["run_id"], submit=True)
    assert done["status"] == "submitted" and fakes.applied[-1][2] is True


def test_daily_limit_pause_and_unavailable_sources(db):
    fakes = Fakes(db, jobs(("Cloud Architect", 90), ("Cloud Architect", 85)))
    plan = autopilot.create_plan(db, plan_payload(), now=NOW)
    fakes.remaining = 1
    result = autopilot.run_plan(fakes.services(), plan["plan_id"])
    assert fakes.submitted == ["lever:0"]
    assert any("Daily application limit" in line for line in result["log"])
    with pytest.raises(ValueError, match="daily application limit"):
        run = autopilot.create_run(
            db, job=fakes.jobs["lever:1"], mode="apply", trigger="user"
        )
        autopilot.execute_run(fakes.services(), run["run_id"])
    fakes.is_paused = True
    paused = autopilot.run_plan(fakes.services(), plan["plan_id"])
    assert paused["status"] == "paused" and paused["summary"] == "Paused"
    fakes.is_paused = False
    fakes.down = {"linkedin": "LinkedIn is not connected."}
    linked = autopilot.create_plan(
        db, plan_payload(sources=["linkedin"], boards=[]), now=NOW
    )
    skipped = autopilot.run_plan(fakes.services(), linked["plan_id"])
    assert skipped["status"] == "error" and "Skipped linkedin" in skipped["log"][0]
    fakes.down = {}
    fakes.search_error = RuntimeError("board offline")
    failed = autopilot.run_plan(fakes.services(), plan["plan_id"])
    assert failed["status"] == "error" and failed["counts"]["search_errors"] == 2


def test_apply_errors_are_recorded_not_raised(db):
    def boom(answers, submit):
        raise RuntimeError("browser crashed")

    fakes = Fakes(
        db,
        jobs(("Cloud Architect", 90), ("Cloud Architect", 80)),
        outcomes={"lever:0": boom, "lever:1": "unsupported"},
    )
    plan = autopilot.create_plan(db, plan_payload(), now=NOW)
    result = autopilot.run_plan(fakes.services(), plan["plan_id"])
    statuses = {run["job_id"]: run for run in autopilot.list_runs(db)}
    assert (
        statuses["lever:0"]["status"] == "failed"
        and "browser crashed" in statuses["lever:0"]["error"]
    )
    assert statuses["lever:1"]["status"] == "unsupported"
    assert result["counts"]["failed"] == 1 and result["counts"]["unsupported"] == 1


def test_claiming_due_plans(db):
    plan = autopilot.create_plan(db, plan_payload(), now=NOW)
    assert autopilot.due_plans(db, NOW) == []
    due = autopilot.due_plans(db, NOW + timedelta(minutes=2))
    assert [item["plan_id"] for item in due] == [plan["plan_id"]]
    assert autopilot.claim_plan(db, due[0], NOW + timedelta(minutes=2))
    assert not autopilot.claim_plan(
        db, due[0], NOW + timedelta(minutes=2)
    )  # a second tick loses
    assert autopilot.due_plans(db, NOW + timedelta(minutes=3)) == []


# --------------------------------------------------------------------------- #
# HTTP journeys: real server, real form engine, stand-in ATS
# --------------------------------------------------------------------------- #


@pytest.fixture
def ats():
    stub = AtsStub().start()
    yield stub
    stub.stop()


class BoardAdapter:
    """Stands in for a company board: returns postings that apply on the ATS stub."""

    postings: list[JobPosting] = []

    def __init__(self, source, identifier, **_kwargs):
        self.source = source

    def search(self, query):
        wanted = query.keywords.casefold().split()[-1]
        return JobSearchResult(
            jobs=tuple(p for p in self.postings if wanted in p.title.casefold())
        )


def posting(
    stub, number: int, title: str, *, ats: str = "lever", variant: str = ""
) -> JobPosting:
    return JobPosting(
        source=ats,
        external_id=f"acme-{number}",
        source_url=f"https://jobs.lever.co/acme/{number}",
        application_url=stub.form_url(ats, variant) if variant else stub.form_url(ats),
        title=title,
        company=f"Acme {number}",
        location="Remote, United States",
        workplace_type="remote",
        description=DESCRIPTION,
    )


@pytest.fixture
def server(tmp_path, monkeypatch):
    monkeypatch.setattr(app, "DB_PATH", tmp_path / "applications.sqlite3")
    monkeypatch.setattr(app, "AUTH_DB_PATH", tmp_path / "accounts.sqlite3")
    monkeypatch.setattr(app, "USER_DB_ROOT", tmp_path / "accounts")
    monkeypatch.setattr(
        app,
        "_login_limiter",
        app.RateLimiter(limit=50, window_seconds=600, lockout_seconds=900),
    )
    monkeypatch.setattr(
        app, "_api_limiter", app.RateLimiter(limit=5000, window_seconds=60)
    )
    monkeypatch.setattr(app, "_ollama_available", lambda: False)
    monkeypatch.setattr(app, "build_adapter", BoardAdapter)
    monkeypatch.setattr(BoardAdapter, "postings", [])
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), app.RequestHandler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    client = Client(f"http://127.0.0.1:{httpd.server_address[1]}")
    try:
        yield client
    finally:
        httpd.shutdown()
        httpd.server_close()


def ok(client, method, path, body=None, expect=(200, 201, 202)):
    status, payload, _ = client.call(method, path, body)
    assert status in expect, (method, path, status, payload)
    return payload


def sign_up(client):
    ok(
        client,
        "POST",
        "/api/auth/register",
        {"email": "auto@example.com", "password": "a long enough password 123"},
    )
    profile = ok(
        client,
        "POST",
        "/api/profile",
        {
            "resume": RESUME,
            "profile_url": PROFILE["linkedin_url"],
            "resume_name": "resume.txt",
            "application_profile": PROFILE,
            "matching_preferences": {
                "locations": ["Remote"],
                "workplace_types": ["remote"],
                "verified_skills": ["AWS", "Kubernetes", "Terraform"],
            },
        },
    )
    assert profile["completeness"]["ready"]
    for field, value in (
        ("work_authorization", "Yes"),
        ("sponsorship_required", "No"),
        ("legal_attestation", "Yes"),
    ):
        ok(
            client,
            "POST",
            "/api/profile/answers",
            {
                "canonical_field": field,
                "value": value,
                "confirmed": True,
                "sensitivity": "sensitive",
            },
        )
    ok(
        client,
        "POST",
        "/api/profile/answers",
        {
            "canonical_field": "how_did_you_hear",
            "value": "LinkedIn",
            "confirmed": True,
            "sensitivity": "public",
        },
    )


def wait_idle(client, timeout=150):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        overview = ok(client, "GET", "/api/autopilot")
        if not overview["status"]["running"]:
            return overview
        time.sleep(0.5)
    raise AssertionError("auto-apply did not finish in time")


def make_plan(client, **extra):
    body = {
        "name": "Architect roles",
        "positions": ["Cloud Architect"],
        "boards": [{"source": "lever", "identifier": "acme"}],
        "min_score": 30,
        "max_per_run": 3,
        "mode": "apply",
        **extra,
    }
    return ok(client, "POST", "/api/autopilot/plans", body)["plan"]


def test_http_plan_searches_scores_and_submits(server, ats, monkeypatch):
    sign_up(server)
    monkeypatch.setattr(
        BoardAdapter,
        "postings",
        [
            posting(ats, 1, "Principal Cloud Architect"),
            posting(ats, 2, "Nurse Practitioner"),
        ],
    )
    overview = ok(server, "GET", "/api/autopilot")
    readiness = {item["id"]: item for item in overview["readiness"]}
    assert readiness["profile"]["ok"] and readiness["answers"]["ok"]
    assert {answer["canonical_field"] for answer in overview["answers"]} >= {
        "work_authorization",
        "sponsorship_required",
    }
    plan = make_plan(server)
    ok(server, "POST", f"/api/autopilot/plans/{plan['plan_id']}/run", {})
    overview = wait_idle(server)
    [run] = overview["recent"]
    assert run["status"] == "submitted", run
    assert (
        run["ats"] == "lever"
        and run["route"] == "form"
        and run["confirmation_number"].startswith("LV-")
    )
    assert run["screenshot_document_id"] and run["evidence_document_id"]
    assert (
        run["job"]["title"] == "Principal Cloud Architect"
        and run["job"]["stage"] == "applied"
    )
    assert len(ats.submissions) == 1
    fields = ats.submissions[0].fields
    assert fields["name"] == ["Jordan Example"] and fields["email"] == [
        "jordan@example.com"
    ]
    assert "resume" in ats.submissions[0].files
    job = ok(server, "GET", f"/api/jobs/{run['job_id']}")["job"]
    assert job["submission"]["method"] == "auto" and job["stage"] == "applied"
    assert job["auto_runs"][0]["run_id"] == run["run_id"]
    assert overview["status"]["sent_today"] == 1
    assert overview["plans"][0]["last_summary"].startswith("1 found") or overview[
        "plans"
    ][0]["last_summary"].startswith("2 found")
    assert overview["passes"][0]["counts"]["submitted"] == 1
    # A second run applies to nothing new.
    ok(server, "POST", f"/api/autopilot/plans/{plan['plan_id']}/run", {})
    wait_idle(server)
    assert len(ats.submissions) == 1


def test_http_held_run_answered_once_then_remembered(server, ats, monkeypatch):
    sign_up(server)
    monkeypatch.setattr(
        BoardAdapter,
        "postings",
        [posting(ats, 3, "Cloud Architect", variant="unknown_required")],
    )
    plan = make_plan(server)
    ok(server, "POST", f"/api/autopilot/plans/{plan['plan_id']}/run", {})
    overview = wait_idle(server)
    [held] = overview["open"]
    assert held["status"] == "needs_review" and not ats.submissions
    labels = [q["label"] for q in held["unanswered"]]
    assert any("products" in label for label in labels), labels
    assert held["job"]["stage"] == "needs_review"
    answers = {
        label: "Your API and the dashboard; I would add bulk export."
        for label in labels
    }
    ok(
        server,
        "POST",
        f"/api/autopilot/runs/{held['run_id']}/answer",
        {"answers": answers, "remember": True},
    )
    overview = wait_idle(server)
    run = ok(server, "GET", f"/api/autopilot/runs/{held['run_id']}")["run"]
    assert run["status"] == "submitted", run
    assert len(ats.submissions) == 1
    assert any("products" in item["label"] for item in overview["remembered"])
    # The remembered answer fills the same question for the next job without holding it.
    monkeypatch.setattr(
        BoardAdapter,
        "postings",
        [posting(ats, 4, "Cloud Architect", variant="unknown_required")],
    )
    ok(server, "POST", f"/api/autopilot/plans/{plan['plan_id']}/run", {})
    wait_idle(server)
    assert len(ats.submissions) == 2
    assert not ok(server, "GET", "/api/autopilot")["open"]


def test_http_review_mode_and_one_off_apply(server, ats, monkeypatch):
    sign_up(server)
    monkeypatch.setattr(
        BoardAdapter, "postings", [posting(ats, 5, "Cloud Architect", ats="greenhouse")]
    )
    plan = make_plan(server, mode="review")
    ok(server, "POST", f"/api/autopilot/plans/{plan['plan_id']}/run", {})
    overview = wait_idle(server)
    [ready] = overview["open"]
    assert ready["status"] == "ready" and not ats.submissions, ready
    ok(server, "POST", f"/api/autopilot/runs/{ready['run_id']}/submit", {})
    wait_idle(server)
    assert (
        ok(server, "GET", f"/api/autopilot/runs/{ready['run_id']}")["run"]["status"]
        == "submitted"
    )
    assert len(ats.submissions) == 1
    status, body, _ = server.call(
        "POST", f"/api/autopilot/runs/{ready['run_id']}/submit", {}
    )
    assert status == 400
    # "Apply for me" on a single imported job.
    monkeypatch.setattr(
        app,
        "import_job_from_url",
        lambda url, **_: posting(ats, 6, "Cloud Architect", ats="ashby"),
    )
    job = ok(
        server,
        "POST",
        "/api/jobs/import",
        {"url": "https://boards.greenhouse.io/acme/jobs/6"},
    )["job"]
    started = ok(
        server, "POST", f"/api/jobs/{job['job_id']}/auto-apply", {"mode": "apply"}
    )
    assert started["run"]["trigger"] == "user"
    wait_idle(server)
    one_off = ok(server, "GET", f"/api/autopilot/runs/{started['run']['run_id']}")[
        "run"
    ]
    assert one_off["status"] in {"submitted", "needs_review"}, one_off
    status, body, _ = server.call(
        "POST", f"/api/jobs/{job['job_id']}/auto-apply", {"mode": "apply"}
    )
    assert status == 400


def test_http_pause_limit_skip_and_validation(server, ats, monkeypatch):
    sign_up(server)
    status = ok(
        server,
        "POST",
        "/api/autopilot/settings",
        {"auto_apply_paused": True, "daily_application_limit": 1},
    )
    assert status["paused"] and status["daily_limit"] == 1
    monkeypatch.setattr(BoardAdapter, "postings", [posting(ats, 7, "Cloud Architect")])
    plan = make_plan(server)
    ok(server, "POST", f"/api/autopilot/plans/{plan['plan_id']}/run", {})
    overview = wait_idle(server)
    assert overview["passes"][0]["status"] == "paused" and not ats.submissions
    ok(server, "POST", "/api/autopilot/settings", {"auto_apply_paused": False})
    status_code, body, _ = server.call(
        "POST", "/api/autopilot/plans", {"positions": []}
    )
    assert status_code == 400 and "position" in body["error"]
    updated = ok(
        server,
        "PATCH",
        f"/api/autopilot/plans/{plan['plan_id']}",
        {"max_per_run": 5, "enabled": False},
    )["plan"]
    assert updated["max_per_run"] == 5 and not updated["enabled"]
    passes = ok(server, "GET", f"/api/autopilot/plans/{plan['plan_id']}/passes")[
        "passes"
    ]
    assert passes and passes[0]["summary"] == "Paused"
    ok(server, "DELETE", f"/api/autopilot/plans/{plan['plan_id']}")
    assert ok(server, "GET", "/api/autopilot")["plans"] == []
    status_code, _, _ = server.call("POST", f"/api/autopilot/runs/{'0' * 32}/skip", {})
    assert status_code == 404


def _bound_user(server):
    with app._auth_connect() as connection:
        user = dict(connection.execute("SELECT * FROM users").fetchone())
    app._bind_user(user)
    return user


def test_linkedin_easy_apply_answers_from_profile_and_retries(server, monkeypatch):
    sign_up(server)
    _bound_user(server)
    try:
        job = app._upsert_job(
            "4242",
            source="linkedin",
            source_url="https://www.linkedin.com/jobs/view/4242/",
            title="Cloud Architect",
            company="Acme",
            description=DESCRIPTION,
            easy_apply=1,
            application_method="linkedin_easy_apply",
        )
        calls = []
        question = "Years of experience"

        def fake_mcp(name, arguments):
            calls.append((name, arguments))
            if question in arguments["answers"]:
                return {
                    "status": "submitted",
                    "questions": [],
                    "unanswered": [],
                    "steps": ["Submitted"],
                    "evidence": {
                        "page_text": "Application sent",
                        "confirmation_text": "Your application was sent",
                        "url": "https://www.linkedin.com/jobs/view/4242/",
                    },
                }
            return {
                "status": "needs_review",
                "questions": [
                    {"label": question, "required": True, "status": "unknown"}
                ],
                "unanswered": [
                    {
                        "label": question,
                        "required": True,
                        "kind": "text",
                        "status": "unknown",
                    }
                ],
                "steps": ["Opened Easy Apply"],
            }

        monkeypatch.setattr(app, "_call_mcp", fake_mcp)
        decoded = app._autopilot_job(job["job_id"])
        run = {"run_id": "r1", "job_id": job["job_id"]}
        off = app._autopilot_apply(
            decoded,
            run,
            {},
            True,
            {"linkedin_easy_apply": False, "cover_letters": False},
        )
        assert (
            off["status"] == "unsupported"
            and "Easy Apply is off" in off["error"]
            and not calls
        )
        done = app._autopilot_apply(
            decoded,
            run,
            {},
            True,
            {"linkedin_easy_apply": True, "cover_letters": False},
        )
        assert done["status"] == "submitted" and done["route"] == "linkedin"
        assert len(calls) == 2 and calls[1][1]["answers"][question] == "12"
        assert calls[0][1]["job_id"] == "4242" and calls[0][1]["submit"] is True
        assert calls[0][1]["profile"]["email"] == "jordan@example.com"
        assert any("trying again" in step for step in done["steps"])
    finally:
        app._unbind_user()


def test_label_resolution_never_guesses():
    profile = {
        "years_of_experience": "12",
        "phone": "+1 555 010 2030",
        "work_authorization": "authorized",
    }
    resolved = app._resolve_labels(
        [
            "Years of experience",
            "How many years of work experience do you have with AWS?",
            "Phone",
            "Are you legally authorized to work in the United States?",
        ],
        profile,
        {},
    )
    # exact matches fill; a loose match and a sensitive question without a confirmed answer do not
    assert resolved == {"Years of experience": "12", "Phone": "+1 555 010 2030"}
    confirmed = app._resolve_labels(
        ["Are you legally authorized to work in the United States?"],
        profile,
        {"work_authorization": {"value": "Yes", "confirmed": True}},
    )
    assert confirmed == {
        "Are you legally authorized to work in the United States?": "Yes"
    }


def test_concurrent_first_connections_migrate_once_and_never_leave_a_lock(tmp_path):
    """A new account's database is opened by request threads and scheduler ticks at once."""
    path = tmp_path / "fresh.sqlite3"
    errors: list[BaseException] = []
    barrier = threading.Barrier(8)

    def open_it():
        app._request_context.data_path = str(path)
        try:
            barrier.wait()
            with app._connect() as connection:
                connection.execute("SELECT COUNT(*) FROM autopilot_plans").fetchone()
        except BaseException as error:  # noqa: BLE001
            errors.append(error)

    threads = [threading.Thread(target=open_it) for _ in range(8)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert not errors, errors
    probe = sqlite3.connect(path, timeout=1)
    probe.execute(
        "BEGIN IMMEDIATE"
    )  # raises "database is locked" if a connection leaked
    probe.rollback()
    assert (
        probe.execute("SELECT MAX(version) FROM schema_migrations").fetchone()[0]
        == schema.LATEST_VERSION
    )
    assert (
        probe.execute("SELECT COUNT(*) FROM schema_migrations").fetchone()[0]
        == schema.LATEST_VERSION
    )
    probe.close()


# --------------------------------------------------------------------------- #
# Databases from early builds name a new job's stage 'found'
# --------------------------------------------------------------------------- #


def test_a_job_with_the_old_found_stage_still_qualifies():
    plan = autopilot.validate_plan(plan_payload())
    job = {
        "title": "Cloud Architect",
        "company": "Acme",
        "description": DESCRIPTION,
        "stage": "found",
        "match_score": 80,
        "match_classification": "Strong Match",
        "match_explanation": {},
    }
    assert autopilot.candidate_reason(plan, job) == "", (
        "'found' is the old name for 'discovered', not 'already in your pipeline'"
    )
    assert "already in your pipeline" in autopilot.candidate_reason(
        plan, {**job, "stage": "applying"}
    )


def _legacy_database(path):
    """A database whose jobs.stage column still defaults to 'found', as early builds created it."""
    connection = sqlite3.connect(path)
    schema.migrate(connection)
    connection.execute("PRAGMA writable_schema = ON")
    connection.execute(
        "UPDATE sqlite_master SET sql = replace(sql, \"stage TEXT NOT NULL DEFAULT 'discovered'\", "
        "\"stage TEXT NOT NULL DEFAULT 'found'\") WHERE name = 'jobs'"
    )
    connection.commit()
    connection.close()
    check = sqlite3.connect(path)
    default = [r for r in check.execute("PRAGMA table_info(jobs)") if r[1] == "stage"][
        0
    ][4]
    check.close()
    assert default == "'found'", "the fixture really is a legacy table"


def test_jobs_found_by_a_search_are_stored_as_discovered_even_in_a_legacy_database(
    tmp_path, monkeypatch
):
    path = tmp_path / "applications.sqlite3"
    _legacy_database(path)
    monkeypatch.setattr(app, "DB_PATH", path)
    monkeypatch.setattr(app._request_context, "data_path", path, raising=False)
    app._upsert_job("777001", title="Salesforce Developer")
    with app._connect() as connection:
        stage = connection.execute(
            "SELECT stage FROM jobs WHERE job_id = '777001'"
        ).fetchone()[0]
    assert stage == "discovered"


def test_the_stage_migration_renames_what_earlier_inserts_left_behind(tmp_path):
    path = tmp_path / "applications.sqlite3"
    _legacy_database(path)
    connection = sqlite3.connect(path)
    connection.execute(
        "INSERT INTO jobs (job_id, title, company, job_url, external_id) VALUES ('old-1', 'Engineer', 'Acme', 'https://example.test/1', 'e1')"
    )
    connection.execute(
        "INSERT INTO jobs (job_id, title, company, job_url, external_id, stage) VALUES ('old-2', 'Engineer', 'Acme', 'https://example.test/2', 'e2', 'applying')"
    )
    connection.commit()
    assert (
        connection.execute("SELECT stage FROM jobs WHERE job_id='old-1'").fetchone()[0]
        == "found"
    )
    stage_migration = next(
        m.version for m in schema.MIGRATIONS if m.name == "normalize_stages"
    )
    connection.execute(
        "DELETE FROM schema_migrations WHERE version >= ?", (stage_migration,)
    )
    applied = schema.migrate(connection)
    assert applied[0] == stage_migration and applied == list(
        range(stage_migration, schema.LATEST_VERSION + 1)
    )
    stages = dict(connection.execute("SELECT job_id, stage FROM jobs"))
    assert stages == {"old-1": "discovered", "old-2": "applying"}
    connection.close()
