"""Careers > Applications: one count of every application, manual and automatic (aira/apply_summary.py).

Each job counts once, in its state now: submitted, in process (queued or filling), needs you (held), failed,
and interviews (submitted and reached an interview). The split says who sent it: you, or Monosage.
"""

# ruff: noqa: F811
from __future__ import annotations

import sqlite3
import uuid

import pytest

from aira import apply_summary, schema
from test_career_copilot import import_job, register, server  # noqa: F401


@pytest.fixture
def db():
    connection = sqlite3.connect(":memory:")
    connection.row_factory = sqlite3.Row
    schema.migrate(connection)
    yield connection
    connection.close()


def add_job(db, job_id: str, stage: str = "discovered", *, archived: bool = False):
    db.execute(
        "INSERT INTO jobs (job_id, external_id, title, company, job_url, stage, updated_at, archived_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (
            job_id,
            job_id,
            f"Role {job_id}",
            "Acme",
            f"https://example.com/{job_id}",
            stage,
            "2026-10-20T10:00:00+00:00",
            "2026-10-21T10:00:00+00:00" if archived else None,
        ),
    )


def submit(db, job_id: str, method: str, at: str = "2026-10-22T10:00") -> None:
    db.execute(
        "INSERT INTO application_submissions (submission_id, job_id, submitted_at, method, idempotency_key) VALUES (?, ?, ?, ?, ?)",
        (uuid.uuid4().hex, job_id, at, method, f"job:{job_id}"),
    )


def run(
    db, job_id: str, status: str, at: str = "2026-10-22T09:00:00+00:00", error: str = ""
) -> None:
    db.execute(
        "INSERT INTO application_runs (run_id, job_id, mode, trigger, status, created_at, error) VALUES (?, ?, 'apply', 'schedule', ?, ?, ?)",
        (uuid.uuid4().hex, job_id, status, at, error),
    )


def interview(db, job_id: str, status: str = "scheduled") -> None:
    db.execute(
        "INSERT INTO interviews (interview_id, job_id, start_at, end_at, status) VALUES (?, ?, ?, ?, ?)",
        (uuid.uuid4().hex, job_id, "2030-11-20T10:00", "2030-11-20T11:00", status),
    )


def seed(db) -> None:
    # Submitted by you, and by you another way; one of them reached an interview.
    add_job(db, "m1", "applied")
    submit(db, "m1", "manual")
    add_job(db, "m2", "recruiter_screen")
    submit(db, "m2", "outside")
    # Submitted automatically; an interview is booked.
    add_job(db, "a1", "applied")
    run(db, "a1", "submitted")
    submit(db, "a1", "auto")
    interview(db, "a1")
    # A cancelled interview does not count.
    add_job(db, "a2", "applied")
    run(db, "a2", "submitted")
    submit(db, "a2", "auto")
    interview(db, "a2", "cancelled")
    # In process: Auto-apply queued and filling; you opened one and have not recorded it.
    add_job(db, "q1", "needs_review")
    run(db, "q1", "queued")
    add_job(db, "q2", "applying")
    run(db, "q2", "running")
    add_job(db, "o1", "applying")
    # Needs you: held for answers, or filled and waiting for your click.
    add_job(db, "h1", "needs_review")
    run(db, "h1", "needs_review")
    add_job(db, "h2", "needs_review")
    run(db, "h2", "ready")
    # Failed: the form failed, or is not supported; you marked one failed by hand.
    add_job(db, "f1", "discovered")
    run(db, "f1", "failed", error="No success marker")
    add_job(db, "f2", "discovered")
    run(db, "f2", "unsupported")
    add_job(db, "f3", "failed")
    # A failed auto run, then you applied yourself: it is submitted by you, not failed.
    add_job(db, "r1", "applied")
    run(db, "r1", "failed")
    submit(db, "r1", "manual")
    # Not applications: skipped, already applied, merely saved, archived.
    add_job(db, "s1", "discovered")
    run(db, "s1", "skipped")
    add_job(db, "s2", "discovered")
    run(db, "s2", "already_applied")
    add_job(db, "s3", "saved")
    add_job(db, "x1", "applied", archived=True)
    submit(db, "x1", "manual")


def test_every_application_counts_once_split_by_who_sent_it(db):
    seed(db)
    out = apply_summary.summary(db)
    assert out["counts"] == {
        "submitted": 5,
        "in_process": 3,
        "needs_you": 2,
        "failed": 3,
        "interviews": 2,
    }
    assert out["total"] == 13  # interviews are part of submitted, not added again
    assert out["by_via"]["manual"] == {
        "submitted": 3,
        "in_process": 1,
        "needs_you": 0,
        "failed": 1,
        "interviews": 1,
    }
    assert out["by_via"]["auto"] == {
        "submitted": 2,
        "in_process": 2,
        "needs_you": 2,
        "failed": 2,
        "interviews": 1,
    }
    assert out["totals_by_via"] == {"manual": 5, "auto": 8}
    assert out["attempts"]["submitted"] == 2 and out["attempts"]["failed"] == 2
    assert out["searching"] is False


def test_the_list_behind_each_count(db):
    seed(db)
    ids = lambda **kw: sorted(i["job_id"] for i in apply_summary.applications(db, **kw))  # noqa: E731
    assert ids(bucket="submitted") == ["a1", "a2", "m1", "m2", "r1"]
    assert ids(bucket="submitted", via="auto") == ["a1", "a2"]
    assert ids(bucket="in_process") == ["o1", "q1", "q2"]
    assert ids(bucket="in_process", via="manual") == ["o1"]
    assert ids(bucket="needs_you") == ["h1", "h2"]
    assert ids(bucket="failed") == ["f1", "f2", "f3"]
    assert ids(bucket="interviews") == ["a1", "m2"]
    assert len(ids()) == 13
    failed = {i["job_id"]: i for i in apply_summary.applications(db, bucket="failed")}
    assert (
        failed["f1"]["run_error"] == "No success marker"
        and failed["f1"]["via"] == "auto"
    )
    assert failed["f3"]["via"] == "manual"
    with pytest.raises(ValueError):
        apply_summary.applications(db, bucket="maybe")
    with pytest.raises(ValueError):
        apply_summary.applications(db, via="fax")


def test_an_interview_stage_reached_earlier_still_counts(db):
    add_job(db, "e1", "rejected")
    submit(db, "e1", "manual")
    db.execute(
        "INSERT INTO application_events (event_id, job_id, previous_status, new_status, reason, source, actor, occurred_at, correlation_id) VALUES (?, ?, ?, ?, '', 'user', 'user', 0, '')",
        (uuid.uuid4().hex, "e1", "applied", "technical_interview"),
    )
    out = apply_summary.summary(db)
    assert out["counts"]["submitted"] == 1 and out["counts"]["interviews"] == 1


def test_nothing_yet_is_all_zeros(db):
    out = apply_summary.summary(db)
    assert out["total"] == 0 and set(out["counts"].values()) == {0}


def test_http_summary_counts_a_recorded_application(server):
    register(server)
    job = import_job(server)
    status, body, _ = server.call("GET", "/api/applications/summary")
    assert status == 200 and body["total"] == 0 and "items" not in body
    status, body, _ = server.call(
        "POST",
        f"/api/jobs/{job['job_id']}/submit",
        {
            "outside": True,
            "channel": "email",
            "idempotency_key": f"job:{job['job_id']}",
        },
    )
    assert status == 200, body
    status, body, _ = server.call(
        "GET", "/api/applications/summary?bucket=submitted&via=manual"
    )
    assert status == 200, body
    assert (
        body["counts"]["submitted"] == 1 and body["by_via"]["manual"]["submitted"] == 1
    )
    assert [item["job_id"] for item in body["items"]] == [job["job_id"]]
    assert body["items"][0]["method"] == "outside"
    status, body, _ = server.call("GET", "/api/applications/summary?bucket=nope")
    assert status == 400
