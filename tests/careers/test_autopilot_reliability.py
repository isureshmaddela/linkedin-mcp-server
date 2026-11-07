"""Auto-apply reliability: one submission per application, lost runs, retries, and Needs you for manual work.

Unit tests drive :mod:`aira.autopilot` with the fake services of ``tests/test_autopilot.py`` and a movable clock.
The HTTP journeys start the real server with the stand-in applicant tracking system (``tests/ats_stub.py``) and let
the real form engine meet a server error after Submit and a rate-limited page in headless Chromium. The hosted
tests deliver the same queue job twice and check that the scheduler comes back for a retry.
"""

# ruff: noqa: F811
from __future__ import annotations

import json
import sqlite3
import threading
import time
from datetime import datetime, timedelta, timezone
from typing import Any, cast

import pytest
from test_autopilot import (  # noqa: F401
    NOW,
    BoardAdapter,
    Fakes,
    _bound_user,
    ats,
    db,
    jobs,
    make_plan,
    ok,
    plan_payload,
    posting,
    server,
    sign_up,
    wait_idle,
)

import auto_apply_assistant as app
from aira import autopilot, schema
from linkedin_mcp_server.job_sources import JobPosting


class Clock:
    def __init__(self, moment: datetime) -> None:
        self.moment = moment

    def __call__(self) -> datetime:
        return self.moment

    def advance(self, delta: timedelta) -> None:
        self.moment += delta


class ClockFakes(Fakes):
    """The fakes of test_autopilot with a clock the test moves (retries are due later)."""

    def __init__(self, db, jobs, outcomes=None, *, start: datetime = NOW):
        super().__init__(db, jobs, outcomes)
        self.clock = Clock(start)

    def services(self):
        services = super().services()
        services.now = self.clock
        return services


def outcome(status, error="", steps=("Opened the Lever form",)):
    return {
        "status": status,
        "route": "form",
        "ats": "lever",
        "error": error,
        "steps": list(steps),
    }


def new_run(db, fakes, job_id="lever:0", mode="apply"):
    return autopilot.create_run(
        db,
        job=fakes.jobs[job_id],
        mode=mode,
        trigger="user",
        now=fakes.clock(),
    )


# --------------------------------------------------------------------------- #
# One submission per application
# --------------------------------------------------------------------------- #


def test_a_run_is_claimed_once_and_a_second_delivery_does_nothing(db):
    fakes = ClockFakes(db, jobs(("Cloud Architect", 90)))
    run = new_run(db, fakes)
    done = autopilot.execute_run(fakes.services(), run["run_id"])
    assert done["status"] == "submitted" and done["attempts"] == 1
    # the same queue job delivered again (a worker that lost its lease) finds the run finished
    again = autopilot.execute_run(fakes.services(), run["run_id"])
    assert again["status"] == "submitted" and len(fakes.applied) == 1
    assert fakes.submitted == ["lever:0"]


def test_a_run_another_worker_is_busy_with_is_left_alone(db):
    fakes = ClockFakes(db, jobs(("Cloud Architect", 90)))
    run = new_run(db, fakes)
    autopilot.update_run(db, run["run_id"], {"status": "running"})
    left = autopilot.execute_run(fakes.services(), run["run_id"])
    assert left["status"] == "running" and fakes.applied == []
    # a run that is not waiting for anything (skipped, already applied) is never started either
    autopilot.update_run(db, run["run_id"], {"status": "skipped"})
    assert autopilot.execute_run(fakes.services(), run["run_id"])["status"] == "skipped"
    assert fakes.applied == []


def test_the_claim_is_a_compare_and_set_so_two_workers_race_for_one_run(tmp_path):
    path = tmp_path / "race.sqlite3"
    setup = sqlite3.connect(path)
    setup.row_factory = sqlite3.Row
    schema.migrate(setup)
    fakes = ClockFakes(setup, jobs(("Cloud Architect", 90)))
    run = new_run(setup, fakes)
    setup.close()
    started = threading.Barrier(2)
    applied: list[str] = []
    lock = threading.Lock()

    def worker() -> None:
        connection = sqlite3.connect(path, timeout=10, check_same_thread=False)
        connection.row_factory = sqlite3.Row
        mine = ClockFakes(connection, jobs(("Cloud Architect", 90)))

        def slow_apply(job, run, answers, submit, plan):
            with lock:
                applied.append(threading.current_thread().name)
            time.sleep(0.2)
            return outcome("submitted")

        services = mine.services()
        services.apply = slow_apply
        started.wait()
        autopilot.execute_run(services, run["run_id"])
        connection.close()

    threads = [threading.Thread(target=worker, name=f"w{i}") for i in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(30)
    assert len(applied) == 1, "only one worker may apply for a run"


# --------------------------------------------------------------------------- #
# Lost runs
# --------------------------------------------------------------------------- #


def test_a_run_left_running_is_interrupted_and_never_sent_again(db):
    fakes = ClockFakes(db, jobs(("Cloud Architect", 90)))
    run = new_run(db, fakes)
    autopilot.update_run(
        db,
        run["run_id"],
        {"status": "running", "started_at": autopilot._iso(fakes.clock())},
    )
    pass_id = autopilot._start_pass(db, "plan-x", "schedule", fakes.clock())
    # within the time limit a hosted worker may still be busy with it
    fakes.clock.advance(timedelta(minutes=10))
    result = autopilot.recover(
        fakes.services(), stale_before=fakes.clock() - autopilot.RUN_TIME_LIMIT
    )
    assert result == {"interrupted": 0, "retried": 0}
    assert autopilot.get_run(db, run["run_id"])["status"] == "running"
    fakes.clock.advance(autopilot.RUN_TIME_LIMIT)
    result = autopilot.recover(
        fakes.services(), stale_before=fakes.clock() - autopilot.RUN_TIME_LIMIT
    )
    assert result["interrupted"] == 1
    lost = autopilot.get_run(db, run["run_id"])
    assert lost["status"] == "interrupted" and lost["open"]
    assert lost["error"] == autopilot.INTERRUPTED_MESSAGE
    assert "stopped before it finished" in lost["steps"][-1]
    assert fakes.held == ["lever:0"] and fakes.notes[-1][0] == "Auto-apply needs you"
    assert autopilot.get_pass(db, pass_id)["status"] == "interrupted"
    # nothing sends it again on its own: not a redelivered job, not a later recovery
    assert autopilot.execute_run(fakes.services(), run["run_id"])["status"] == (
        "interrupted"
    )
    fakes.clock.advance(timedelta(days=1))
    autopilot.recover(fakes.services(), stale_before=fakes.clock())
    assert fakes.applied == []
    assert [r["run_id"] for r in autopilot.list_runs(db, status="open")] == [
        run["run_id"]
    ]


def test_a_failure_after_submit_is_interrupted_not_failed_and_not_retried(db):
    fakes = ClockFakes(
        db,
        jobs(("Cloud Architect", 90), ("Solution Architect", 80)),
        outcomes={
            "lever:0": outcome(
                "failed",
                "The site answered HTTP 500 after submitting; the application was not confirmed.",
                steps=(
                    "Opened the Lever form",
                    "Clicked Submit application",
                    "The site answered HTTP 500 after submit",
                ),
            ),
            # a timeout after the click is just as unknown
            "lever:1": outcome(
                "failed",
                "The form did not respond in time: Timeout 30000ms exceeded.",
                steps=(
                    "Opened the Lever form",
                    "Clicked Submit",
                    "The browser timed out",
                ),
            ),
        },
    )
    for job_id in ("lever:0", "lever:1"):
        run = new_run(db, fakes, job_id)
        done = autopilot.execute_run(fakes.services(), run["run_id"])
        assert done["status"] == "interrupted", done
        assert done["error"].startswith(autopilot.INTERRUPTED_MESSAGE)
        assert done["next_try_at"] is None
    fakes.clock.advance(timedelta(hours=5))
    assert autopilot.recover(fakes.services(), stale_before=fakes.clock()) == {
        "interrupted": 0,
        "retried": 0,
    }
    assert len(fakes.applied) == 2


def test_review_mode_failures_never_become_interrupted(db):
    # filling without submitting cannot have sent anything
    fakes = ClockFakes(
        db,
        jobs(("Cloud Architect", 90)),
        outcomes={
            "lever:0": outcome(
                "failed",
                "Browser error: something broke",
                steps=("Opened the Lever form", "Clicked Submit"),
            )
        },
    )
    run = new_run(db, fakes, mode="review")
    assert autopilot.execute_run(fakes.services(), run["run_id"])["status"] == "failed"


# --------------------------------------------------------------------------- #
# Retries
# --------------------------------------------------------------------------- #


def test_a_passing_failure_is_tried_again_twice_then_stops(db):
    fakes = ClockFakes(
        db,
        jobs(("Cloud Architect", 90)),
        outcomes={
            "lever:0": outcome(
                "failed", "The form did not respond in time: Timeout 30000ms exceeded."
            )
        },
    )
    run = new_run(db, fakes)
    first = autopilot.execute_run(fakes.services(), run["run_id"])
    assert first["status"] == "queued" and first["attempts"] == 1
    assert first["next_try_at"] == autopilot._iso(NOW + timedelta(minutes=15))
    assert "tries again" in first["steps"][-1] and not first["open"]
    assert first["finished_at"] is None
    # not yet due: nothing happens
    fakes.clock.advance(timedelta(minutes=10))
    assert (
        autopilot.recover(fakes.services(), stale_before=fakes.clock())["retried"] == 0
    )
    fakes.clock.advance(timedelta(minutes=6))
    assert (
        autopilot.recover(fakes.services(), stale_before=fakes.clock())["retried"] == 1
    )
    second = autopilot.get_run(db, run["run_id"])
    assert second["status"] == "queued" and second["attempts"] == 2
    assert second["next_try_at"] == autopilot._iso(fakes.clock() + timedelta(hours=2))
    fakes.clock.advance(timedelta(hours=2, minutes=1))
    autopilot.recover(fakes.services(), stale_before=fakes.clock())
    third = autopilot.get_run(db, run["run_id"])
    assert third["status"] == "failed" and third["attempts"] == 3
    assert (
        third["steps"][-1] == "Stopped after 3 tries." and third["next_try_at"] is None
    )
    assert len(fakes.applied) == 3
    fakes.clock.advance(timedelta(days=1))
    autopilot.recover(fakes.services(), stale_before=fakes.clock())
    assert len(fakes.applied) == 3, "a run that used its tries is not tried again"


def test_a_retry_that_works_submits_once(db):
    calls = {"n": 0}

    def flaky(answers, submit):
        calls["n"] += 1
        if calls["n"] == 1:
            return outcome("failed", "The application page answered HTTP 503.")
        return "submitted"

    fakes = ClockFakes(db, jobs(("Cloud Architect", 90)), outcomes={"lever:0": flaky})
    run = new_run(db, fakes)
    assert autopilot.execute_run(fakes.services(), run["run_id"])["status"] == "queued"
    fakes.clock.advance(timedelta(minutes=16))
    autopilot.recover(fakes.services(), stale_before=fakes.clock())
    done = autopilot.get_run(db, run["run_id"])
    assert done["status"] == "submitted" and done["attempts"] == 2
    assert fakes.submitted == ["lever:0"]


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        (outcome("failed", "The form did not respond in time: Timeout"), "queued"),
        (outcome("failed", "The application page answered HTTP 502."), "queued"),
        (
            outcome(
                "unsupported",
                "jobs.example.com refused the automated visit (HTTP 429). Sites like this block automated applications.",
            ),
            "queued",
        ),
        (
            outcome("failed", "Chromium could not be started: browser closed"),
            "queued",
        ),
        (
            outcome("failed", "Browser error: net::ERR_CONNECTION_RESET at https://x"),
            "queued",
        ),
        (
            outcome(
                "unsupported",
                "jobs.example.com refused the automated visit (HTTP 403). Sites like this block automated applications.",
            ),
            "unsupported",
        ),
        (
            outcome("unsupported", "No application form was found on this page."),
            "unsupported",
        ),
        (
            outcome(
                "failed",
                "patchright is not installed; run 'uv sync' and 'uv run patchright install chromium'.",
            ),
            "failed",
        ),
        (outcome("failed", "The application page answered HTTP 404."), "failed"),
        # a click happened: even a timeout is not retried
        (
            outcome(
                "failed",
                "The form did not respond in time",
                steps=("Opened", "Pressed 'Next'"),
            ),
            "failed",
        ),
    ],
)
def test_which_failures_are_tried_again(db, raw, expected):
    fakes = ClockFakes(db, jobs(("Cloud Architect", 90)), outcomes={"lever:0": raw})
    run = new_run(db, fakes)
    assert autopilot.execute_run(fakes.services(), run["run_id"])["status"] == expected


def test_exceptions_from_the_apply_tool_are_classified(db):
    fakes = ClockFakes(db, jobs(("Cloud Architect", 90), ("Solution Architect", 80)))
    services = fakes.services()

    def broken(job, run, answers, submit, plan):
        if job["job_id"] == "lever:0":
            raise ConnectionResetError("Connection reset by peer")
        raise KeyError("missing field")

    services.apply = broken
    reset = autopilot.execute_run(services, new_run(db, fakes, "lever:0")["run_id"])
    assert reset["status"] == "queued" and reset["error"].startswith(
        "ConnectionResetError"
    )
    bug = autopilot.execute_run(services, new_run(db, fakes, "lever:1")["run_id"])
    assert bug["status"] == "failed"


def test_pause_and_the_daily_limit_hold_retries(db):
    fakes = ClockFakes(
        db,
        jobs(("Cloud Architect", 90)),
        outcomes={"lever:0": outcome("failed", "The form did not respond in time")},
    )
    run = new_run(db, fakes)
    autopilot.execute_run(fakes.services(), run["run_id"])
    fakes.clock.advance(timedelta(minutes=20))
    fakes.is_paused = True
    assert (
        autopilot.recover(fakes.services(), stale_before=fakes.clock())["retried"] == 0
    )
    fakes.is_paused = False
    fakes.remaining = 0
    assert (
        autopilot.recover(fakes.services(), stale_before=fakes.clock())["retried"] == 0
    )
    held = autopilot.get_run(db, run["run_id"])
    assert held["status"] == "queued" and held["attempts"] == 1
    fakes.remaining = None
    assert (
        autopilot.recover(fakes.services(), stale_before=fakes.clock())["retried"] == 1
    )


def test_a_pass_reports_retries_in_its_summary(db):
    fakes = ClockFakes(
        db,
        jobs(("Cloud Architect", 90)),
        outcomes={"lever:0": outcome("failed", "The form did not respond in time")},
    )
    plan = autopilot.create_plan(db, plan_payload(), now=NOW)
    record = autopilot.run_plan(fakes.services(), plan["plan_id"])
    assert "1 to try again later" in record["summary"]
    assert any("will be tried again later" in line for line in record["log"])
    # the next pass does not start a second run for the job: the retry owns it
    fakes.clock.advance(timedelta(hours=5))
    autopilot.run_plan(fakes.services(), plan["plan_id"])
    assert len(autopilot.list_runs(db)) == 1


# --------------------------------------------------------------------------- #
# Needs you, Try again and I applied
# --------------------------------------------------------------------------- #


def test_needs_you_lists_recent_jobs_to_apply_to_on_the_site(db):
    real_now = datetime.now(timezone.utc)
    fakes = ClockFakes(
        db,
        jobs(
            ("Cloud Architect", 90),
            ("Solution Architect", 88),
            ("Platform Architect", 86),
            ("Data Architect", 84),
        ),
        outcomes={
            job_id: outcome(
                "unsupported",
                "No application form was found on this page.",
            )
            for job_id in ("lever:0", "lever:1", "lever:2", "lever:3")
        },
        start=real_now,
    )
    runs = {}
    for job_id in ("lever:0", "lever:1", "lever:2", "lever:3"):
        runs[job_id] = autopilot.execute_run(
            fakes.services(), new_run(db, fakes, job_id)["run_id"]
        )
        assert runs[job_id]["status"] == "unsupported" and runs[job_id]["open"]
    # an old one has faded into the activity list
    old = autopilot._iso(real_now - timedelta(days=autopilot.NEEDS_YOU_DAYS + 5))
    db.execute(
        "UPDATE application_runs SET created_at = ?, finished_at = ? WHERE run_id = ?",
        (old, old, runs["lever:1"]["run_id"]),
    )
    # one was applied to meanwhile
    db.execute(
        "INSERT INTO application_submissions (submission_id, job_id, submitted_at, method, confirmation_text, confirmation_number, actor, correlation_id, idempotency_key) VALUES ('s2', 'lever:2', ?, 'outside', '', '', 'test', '', 'k2')",
        (real_now.isoformat(),),
    )
    # one was replaced by a newer run
    fakes.clock.advance(timedelta(minutes=1))
    newer = new_run(db, fakes, "lever:3")
    autopilot.update_run(db, newer["run_id"], {"status": "skipped"})
    db.commit()
    listed = [run["job_id"] for run in autopilot.list_runs(db, status="open")]
    assert listed == ["lever:0"]
    assert not autopilot.get_run(db, runs["lever:1"]["run_id"])["open"]


def test_try_again_and_i_applied_on_waiting_runs(db):
    fakes = ClockFakes(db, jobs(("Cloud Architect", 90)))
    run = new_run(db, fakes)
    autopilot.update_run(db, run["run_id"], {"status": "interrupted"})
    queued = autopilot.retry_run(db, run["run_id"])
    assert queued["status"] == "queued" and queued["next_try_at"] is None
    with pytest.raises(ValueError, match="interrupted or failed"):
        autopilot.retry_run(db, run["run_id"])
    autopilot.update_run(db, run["run_id"], {"status": "unsupported"})
    closed = autopilot.mark_applied_by_person(
        db, run["run_id"], applied_on="2026-10-20", now=NOW
    )
    assert closed["status"] == "already_applied"
    assert closed["error"] == "You applied on the site on 2026-10-20."
    assert not closed["open"]
    with pytest.raises(ValueError, match="cannot be marked"):
        autopilot.mark_applied_by_person(db, run["run_id"], applied_on="", now=NOW)


# --------------------------------------------------------------------------- #
# Schema
# --------------------------------------------------------------------------- #


def test_migration_21_adds_the_retry_columns_to_an_existing_database(tmp_path):
    path = tmp_path / "old.sqlite3"
    connection = sqlite3.connect(path)
    # a database as migration 20 left it: the runs table without attempts and next_try_at
    connection.execute(
        "CREATE TABLE schema_migrations (version INTEGER PRIMARY KEY, name TEXT NOT NULL, applied_at TEXT NOT NULL)"
    )
    for item in schema.MIGRATIONS:
        if item.version <= 20:
            item.apply(connection)
            connection.execute(
                "INSERT INTO schema_migrations VALUES (?, ?, ?)",
                (item.version, item.name, NOW.isoformat()),
            )
    connection.execute("DROP INDEX IF EXISTS idx_application_runs_retry")
    connection.execute("ALTER TABLE application_runs DROP COLUMN next_try_at")
    connection.execute("ALTER TABLE application_runs DROP COLUMN attempts")
    connection.execute(
        "INSERT INTO application_runs (run_id, job_id, mode, trigger, status, created_at) VALUES ('r1', 'j1', 'apply', 'user', 'failed', ?)",
        (NOW.isoformat(),),
    )
    connection.commit()
    applied = schema.migrate(connection)
    assert applied[0] == 21 and applied == sorted(
        applied
    )  # 21 first; later migrations may follow
    columns = {
        row[1] for row in connection.execute("PRAGMA table_info(application_runs)")
    }
    assert {"attempts", "next_try_at"} <= columns
    assert connection.execute(
        "SELECT attempts, next_try_at FROM application_runs WHERE run_id = 'r1'"
    ).fetchone() == (0, None)
    assert schema.schema_version(connection) == schema.LATEST_VERSION
    assert schema.migrate(connection) == []
    connection.close()


# --------------------------------------------------------------------------- #
# HTTP journeys (the real server, form engine and stand-in ATS)
# --------------------------------------------------------------------------- #


def _workspace_execute(sql: str, args=()):
    with app._connect() as connection:
        connection.execute(sql, args)
        connection.commit()


def test_http_a_server_error_after_submit_waits_in_needs_you_then_i_applied(
    server, ats, monkeypatch
):
    sign_up(server)
    monkeypatch.setattr(
        BoardAdapter,
        "postings",
        [posting(ats, 11, "Cloud Architect", variant="fail_submit")],
    )
    plan = make_plan(server)
    ok(server, "POST", f"/api/autopilot/plans/{plan['plan_id']}/run", {})
    overview = wait_idle(server)
    [held] = overview["open"]
    assert held["status"] == "interrupted", held
    assert held["error"].startswith(autopilot.INTERRUPTED_MESSAGE)
    assert not any(
        run["run_id"] == held["run_id"] for run in overview["recent"] if not run["open"]
    )
    assert "1 to check on the site" in overview["passes"][0]["summary"]
    assert ats.submissions == [], "the stand-in refused the submit"
    # the drawer and the job say the same
    run = ok(server, "GET", f"/api/autopilot/runs/{held['run_id']}")["run"]
    assert run["attempts"] == 1 and run["max_attempts"] == autopilot.MAX_ATTEMPTS
    # a date in the future or not a date is refused; then the person records it
    future = (datetime.now(timezone.utc) + timedelta(days=5)).strftime("%Y-%m-%d")
    for bad in ({"applied_on": future}, {"applied_on": "20-10-2026"}):
        status, body, _ = server.call(
            "POST", f"/api/autopilot/runs/{held['run_id']}/applied", bad
        )
        assert status == 400, body
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    done = ok(
        server,
        "POST",
        f"/api/autopilot/runs/{held['run_id']}/applied",
        {"applied_on": today},
    )
    assert done["run"]["status"] == "already_applied"
    assert done["submission"]["method"] == "manual"
    job = ok(server, "GET", f"/api/jobs/{held['job_id']}")["job"]
    assert job["submission"]["method"] == "manual" and job["stage"] == "applied"
    assert ok(server, "GET", "/api/autopilot")["open"] == []
    # recording it twice is refused (the run is closed) and adds nothing
    status, _, _ = server.call(
        "POST", f"/api/autopilot/runs/{held['run_id']}/applied", {}
    )
    assert status == 400


def test_http_a_rate_limited_job_is_tried_three_times_then_waits_for_the_person(
    server, ats, monkeypatch
):
    sign_up(server)
    limited = JobPosting(
        source="lever",
        external_id="acme-12",
        source_url="https://jobs.lever.co/acme/12",
        application_url=f"{ats.base_url}/rate-limited",
        title="Cloud Architect",
        company="Acme 12",
        location="Remote, United States",
        workplace_type="remote",
        description=posting(ats, 12, "x").description,
    )
    monkeypatch.setattr(BoardAdapter, "postings", [limited])
    plan = make_plan(server)
    ok(server, "POST", f"/api/autopilot/plans/{plan['plan_id']}/run", {})
    overview = wait_idle(server)
    [run] = overview["recent"]
    assert run["status"] == "queued" and run["next_try_at"] and run["attempts"] == 1, (
        run
    )
    assert overview["open"] == []
    user = _bound_user(server)
    try:
        for attempt in (2, 3):
            _workspace_execute(
                "UPDATE application_runs SET next_try_at = ? WHERE run_id = ?",
                ("2000-01-01T00:00:00+00:00", run["run_id"]),
            )
            assert app._try_begin_automation(user["user_id"])
            app._run_autopilot_for(user)  # what the scheduler tick starts
            app._bind_user(user)
            current = ok(server, "GET", f"/api/autopilot/runs/{run['run_id']}")["run"]
            assert current["attempts"] == attempt, current
    finally:
        app._unbind_user()
    final = ok(server, "GET", f"/api/autopilot/runs/{run['run_id']}")["run"]
    assert (
        final["status"] == "unsupported"
        and final["steps"][-1] == "Stopped after 3 tries."
    )
    [waiting] = ok(server, "GET", "/api/autopilot")["open"]
    assert waiting["run_id"] == run["run_id"]
    skipped = ok(server, "POST", f"/api/autopilot/runs/{run['run_id']}/skip", {})["run"]
    assert skipped["status"] == "skipped"
    assert ok(server, "GET", "/api/autopilot")["open"] == []


def test_http_the_local_tick_interrupts_a_run_left_by_a_restart_and_apply_for_me_replaces_it(
    server, ats, monkeypatch
):
    sign_up(server)
    monkeypatch.setattr(
        app,
        "import_job_from_url",
        lambda url, **_: posting(ats, 13, "Cloud Architect", ats="greenhouse"),
    )
    job = ok(
        server,
        "POST",
        "/api/jobs/import",
        {"url": "https://boards.greenhouse.io/acme/jobs/13"},
    )["job"]
    _bound_user(server)
    try:
        with app._connect() as connection:
            found = app._autopilot_job(job["job_id"])
            assert found is not None
            run = autopilot.create_run(
                connection, job=found, mode="apply", trigger="user"
            )
            autopilot.update_run(
                connection,
                run["run_id"],
                {
                    "status": "running",
                    "started_at": autopilot._iso(datetime.now(timezone.utc)),
                },
            )
    finally:
        app._unbind_user()
    # Monosage restarted mid-run: the next tick finds the account idle and the run still "running"
    app._run_due_autopilot()
    deadline = time.monotonic() + 20
    while time.monotonic() < deadline:
        current = ok(server, "GET", f"/api/autopilot/runs/{run['run_id']}")["run"]
        if current["status"] != "running":
            break
        time.sleep(0.2)
    assert current["status"] == "interrupted", current
    [waiting] = ok(server, "GET", "/api/autopilot")["open"]
    assert waiting["run_id"] == run["run_id"]
    # Try again needs the person: it is refused for a run that is not interrupted or failed
    status, _, _ = server.call("POST", f"/api/autopilot/runs/{'0' * 32}/retry", {})
    assert status == 404
    # Apply for me is not locked out by the interrupted run; it replaces it
    started = ok(
        server, "POST", f"/api/jobs/{job['job_id']}/auto-apply", {"mode": "apply"}
    )
    wait_idle(server)
    replaced = ok(server, "GET", f"/api/autopilot/runs/{run['run_id']}")["run"]
    assert replaced["status"] == "skipped" and "Replaced" in replaced["error"]
    fresh = ok(server, "GET", f"/api/autopilot/runs/{started['run']['run_id']}")["run"]
    assert fresh["status"] == "submitted", fresh
    assert len(ats.submissions) == 1


def test_http_try_again_runs_a_failed_application_once(server, ats, monkeypatch):
    sign_up(server)
    monkeypatch.setattr(
        app,
        "import_job_from_url",
        lambda url, **_: posting(ats, 14, "Cloud Architect", ats="lever"),
    )
    job = ok(
        server,
        "POST",
        "/api/jobs/import",
        {"url": "https://boards.greenhouse.io/acme/jobs/14"},
    )["job"]
    _bound_user(server)
    try:
        with app._connect() as connection:
            found = app._autopilot_job(job["job_id"])
            assert found is not None
            run = autopilot.create_run(
                connection, job=found, mode="apply", trigger="user"
            )
            autopilot.update_run(connection, run["run_id"], {"status": "failed"})
    finally:
        app._unbind_user()
    ok(server, "POST", f"/api/autopilot/runs/{run['run_id']}/retry", {}, expect=(202,))
    wait_idle(server)
    done = ok(server, "GET", f"/api/autopilot/runs/{run['run_id']}")["run"]
    assert done["status"] == "submitted", done
    status, body, _ = server.call(
        "POST", f"/api/autopilot/runs/{run['run_id']}/retry", {}
    )
    assert status == 400 and "interrupted or failed" in body["error"]
    assert len(ats.submissions) == 1


# --------------------------------------------------------------------------- #
# Hosted: the queue and the scheduler
# --------------------------------------------------------------------------- #


def test_the_hosted_scheduler_comes_back_for_retries_and_lost_runs(tmp_path):
    from monosage_cloud.scheduler import Scheduler

    path = tmp_path / "workspace.sqlite3"
    connection = sqlite3.connect(path)
    connection.row_factory = sqlite3.Row
    schema.migrate(connection)
    now = datetime(2026, 11, 6, 12, 0, tzinfo=timezone.utc)
    retry_at = now + timedelta(minutes=15)
    connection.execute(
        "INSERT INTO application_runs (run_id, job_id, mode, trigger, status, created_at, next_try_at, attempts) "
        "VALUES ('r1', 'j1', 'apply', 'user', 'queued', ?, ?, 1)",
        (now.isoformat(), retry_at.isoformat()),
    )
    connection.commit()
    scheduler = Scheduler(app=cast(Any, None))  # _due reads only the workspace file
    due = scheduler._due(path, now.isoformat(timespec="seconds"))
    assert due["plans"] is False and due["next_due"] == int(retry_at.timestamp())
    later = (retry_at + timedelta(seconds=1)).isoformat(timespec="seconds")
    assert scheduler._due(path, later)["plans"] is True
    # a run running past the time limit is due too (its worker was lost)
    connection.execute(
        "UPDATE application_runs SET status = 'submitted' WHERE run_id = 'r1'"
    )
    connection.execute(
        "INSERT INTO application_runs (run_id, job_id, mode, trigger, status, created_at, started_at) "
        "VALUES ('r2', 'j2', 'apply', 'user', 'running', ?, ?)",
        (now.isoformat(), now.isoformat()),
    )
    connection.commit()
    soon = scheduler._due(
        path, (now + timedelta(minutes=5)).isoformat(timespec="seconds")
    )
    assert soon["plans"] is False
    assert soon["next_due"] == int((now + autopilot.RUN_TIME_LIMIT).timestamp()) + 1
    late = scheduler._due(
        path,
        (now + autopilot.RUN_TIME_LIMIT + timedelta(minutes=1)).isoformat(
            timespec="seconds"
        ),
    )
    assert late["plans"] is True
    connection.close()


def test_a_hosted_queue_job_delivered_twice_applies_once(cloud, monkeypatch):
    from ats_stub import AtsStub
    from cloud_support import build_org
    from test_cloud_worker import BoardAdapter as CloudBoard
    from test_cloud_worker import make_plan as cloud_plan
    from test_cloud_worker import onboard, run_jobs
    from test_cloud_worker import posting as cloud_posting

    stub = AtsStub().start()
    try:
        monkeypatch.setattr(app, "build_adapter", CloudBoard)
        monkeypatch.setattr(app, "_ollama_available", lambda: False)
        monkeypatch.setattr(
            CloudBoard,
            "postings",
            [cloud_posting(stub, 21, "Cloud Architect", variant="fail_submit")],
        )
        org: Any = build_org(cloud, plan="team")
        cloud.tenancy.update_policy(org.owner, {"submission_mode": "auto"})
        candidate_id = org.candidate.member_id
        candidate = onboard(cloud, org, org.candidate)
        plan = cloud_plan(candidate)
        status, _ = candidate.call(
            "POST", f"/api/autopilot/plans/{plan['plan_id']}/run", {}
        )
        assert status == 202
        run_jobs(cloud)
        status, overview = candidate.call("GET", "/api/autopilot")
        [held] = overview["open"]
        assert held["status"] == "interrupted", held
        # the exec job of that run delivered again (a worker lost its lease after Submit): nothing is sent
        for _ in range(2):
            cloud.queue.enqueue(
                org.org_id,
                "autopilot.exec",
                candidate_id=candidate_id,
                payload={"spec": {"action": "execute_run", "run_id": held["run_id"]}},
            )
            run_jobs(cloud)
        status, run = candidate.call("GET", f"/api/autopilot/runs/{held['run_id']}")
        assert run["run"]["status"] == "interrupted" and run["run"]["attempts"] == 1
        assert stub.submissions == []
        # Try again and I applied are answered for the organization's member like any route
        status, body = candidate.call(
            "POST", f"/api/autopilot/runs/{held['run_id']}/applied", {}
        )
        assert status == 200 and body["run"]["status"] == "already_applied", body
        status, body = candidate.call("GET", f"/api/jobs/{held['job_id']}")
        assert body["job"]["submission"]["method"] == "manual"
        assert "sk-" not in json.dumps(body)
    finally:
        stub.stop()
