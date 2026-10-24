"""The queue, the worker pool, the gate at run start and the scheduler: tenant fairness and the real pipeline.

The pipeline tests start a stand-in company board and applicant tracking system (``tests/ats_stub.py``) and let
the real form engine fill and submit forms in headless Chromium, through a worker leasing jobs the way a
hosted deployment would.
"""

from __future__ import annotations

import threading
import time
from collections import defaultdict

import pytest
from ats_stub import AtsStub
from cloud_support import (
    CONSENTS,
    add_member,
    build_org,
    seed_workspace,
    sign_in,
    webhook,
)  # noqa: F401
from test_career_copilot import PROFILE, RESUME

import auto_apply_assistant as local
from aira_cloud.worker import Skip, Worker
from linkedin_mcp_server.job_sources import JobPosting, JobSearchResult

DESCRIPTION = (
    "Requirements:\n- 10+ years of experience with AWS, Kubernetes, and Terraform.\n- Python and microservices.\n"
    "- Enterprise architecture and stakeholder management.\nNice to have:\n- Azure."
)


# ----------------------------------------------------------------------------- the queue


def test_fair_lease_serves_the_organization_with_the_fewest_running_jobs_first(cloud):
    for index in range(6):
        cloud.queue.enqueue("org-a", "autopilot.due", candidate_id=f"a{index}")
    cloud.queue.enqueue("org-b", "autopilot.due", candidate_id="b0")
    first = cloud.queue.lease("w1", per_org_limit=3)
    second = cloud.queue.lease("w2", per_org_limit=3)
    third = cloud.queue.lease("w3", per_org_limit=3)
    assert first["org_id"] == "org-a"
    assert second["org_id"] == "org-b", (
        "organization B, with nothing running, goes before A's second job"
    )
    assert third["org_id"] == "org-a"


def test_per_organization_concurrency_cap_and_one_job_per_candidate(cloud):
    for index in range(5):
        cloud.queue.enqueue("org-a", "autopilot.due", candidate_id=f"a{index}")
    leased = [cloud.queue.lease(f"w{i}", per_org_limit=2) for i in range(4)]
    assert [bool(j) for j in leased] == [True, True, False, False], (
        "an organization never holds more than its cap"
    )


def test_one_automation_per_candidate_at_a_time(cloud):
    cloud.queue.enqueue("org-b", "autopilot.due", candidate_id="same")
    cloud.queue.enqueue("org-b", "autopilot.exec", candidate_id="same")
    assert cloud.queue.lease("w9", per_org_limit=5)["candidate_id"] == "same"
    assert cloud.queue.lease("w10", per_org_limit=5) is None, (
        "the candidate's second job waits for the first"
    )


def test_dedupe_retry_backoff_dead_letter_and_lost_workers(cloud):
    first = cloud.queue.enqueue(
        "org-a", "autopilot.due", candidate_id="c1", dedupe_key="due:c1"
    )
    assert (
        first
        and cloud.queue.enqueue(
            "org-a", "autopilot.due", candidate_id="c1", dedupe_key="due:c1"
        )
        is None
    )
    job = cloud.queue.lease("w1")
    assert (
        cloud.queue.enqueue(
            "org-a", "autopilot.due", candidate_id="c1", dedupe_key="due:c1"
        )
        is None
    ), "still running"
    assert cloud.queue.fail(job["job_id"], "boom") == "queued"
    assert cloud.queue.lease("w1") is None, "waits out the backoff"
    cloud.clock.advance(seconds=31)
    job = cloud.queue.lease("w1")
    assert job["attempts"] == 2
    cloud.queue.fail(job["job_id"], "boom")
    cloud.clock.advance(seconds=121)
    job = cloud.queue.lease("w1")
    assert cloud.queue.fail(job["job_id"], "boom") == "dead"
    assert cloud.queue.get(first)["status"] == "dead"
    # a worker that disappears: the lease expires and the job goes back
    lost = cloud.queue.enqueue("org-a", "autopilot.due", candidate_id="c2")
    cloud.queue.lease("w-lost", lease_seconds=60)
    cloud.clock.advance(seconds=61)
    assert cloud.queue.reap_expired() == 1
    assert cloud.queue.get(lost)["status"] == "queued"


def test_skipped_and_cancelled_jobs_are_not_retried(cloud):
    job_id = cloud.queue.enqueue("org-a", "autopilot.due", candidate_id="c1")
    job = cloud.queue.lease("w1")
    cloud.queue.skip(job["job_id"], "paused")
    assert cloud.queue.get(job_id)["status"] == "skipped"
    other = cloud.queue.enqueue("org-a", "autopilot.due", candidate_id="c9")
    assert cloud.queue.cancel_for("org-a", "c9") == 1
    assert (
        cloud.queue.get(other)["status"] == "cancelled"
        and cloud.queue.lease("w1") is None
    )


# ----------------------------------------------------------------------------- the worker pool


def test_fifty_candidates_across_five_organizations_run_at_once_without_one_tenant_delaying_another(
    cloud,
):
    """The Phase 3 gate: a long backlog in one organization does not make another wait."""
    lock = threading.Lock()
    running: dict[str, int] = defaultdict(int)
    peak: dict[str, int] = defaultdict(int)
    finished_at: dict[str, float] = {}
    started = time.monotonic()
    duration = 0.15

    def handler(app, job):
        org = job["org_id"]
        with lock:
            running[org] += 1
            peak[org] = max(peak[org], running[org])
        time.sleep(duration)
        with lock:
            running[org] -= 1
            finished_at[job["candidate_id"]] = time.monotonic() - started
        return {"ok": True}

    # one organization has a large backlog queued first; four small ones arrive after it
    for index in range(30):
        cloud.queue.enqueue("big", "autopilot.due", candidate_id=f"big-{index}")
    small = {}
    for org in ("s1", "s2", "s3", "s4"):
        small[org] = [f"{org}-{n}" for n in range(5)]
        for candidate in small[org]:
            cloud.queue.enqueue(org, "autopilot.due", candidate_id=candidate)
    worker = Worker(
        cloud,
        threads=10,
        handlers={"autopilot.due": handler},
        poll_seconds=0.02,
        run_tick=False,
    )
    worker.start()
    deadline = time.monotonic() + 60
    while (
        time.monotonic() < deadline
        and cloud.queue.stats()["by_status"].get("done", 0) < 50
    ):
        time.sleep(0.05)
    worker.stop()
    assert cloud.queue.stats()["by_status"].get("done", 0) == 50
    assert max(peak.values()) <= cloud.config.per_org_concurrency, (
        f"an organization exceeded its concurrency cap: {dict(peak)}"
    )
    big_total = max(t for c, t in finished_at.items() if c.startswith("big-"))
    for org, candidates in small.items():
        last = max(finished_at[c] for c in candidates)
        # five jobs at two at a time: about three rounds, long before the big backlog (15 rounds) is done
        assert last < big_total * 0.6, (
            f"{org} waited on another tenant: {last:.2f}s against {big_total:.2f}s"
        )
    assert len({org for org in peak}) == 5


def test_a_failing_job_is_recorded_and_retried_not_fatal(cloud):
    attempts = []

    def flaky(app, job):
        attempts.append(job["attempts"])
        if job["attempts"] < 2:
            raise RuntimeError("temporary failure")
        return {"ok": True}

    job_id = cloud.queue.enqueue("org-a", "autopilot.due", candidate_id="c1")
    worker = Worker(cloud, threads=1, handlers={"autopilot.due": flaky}, run_tick=False)
    assert worker.run_once() is True
    assert (
        cloud.queue.get(job_id)["status"] == "queued"
        and "temporary failure" in cloud.queue.get(job_id)["error"]
    )
    cloud.clock.advance(seconds=31)
    assert worker.run_once() is True
    assert cloud.queue.get(job_id)["status"] == "done" and attempts == [1, 2]
    outcomes = cloud.platform.health  # health reads the recorded outcomes
    assert outcomes is not None


def test_worker_heartbeat_registers_capacity_for_platform_health(cloud):
    worker = Worker(cloud, threads=3, handlers={}, run_tick=False, worker_id="w-test")
    cloud.queue.register_worker("w-test", "apply", "host", 3, 1)
    stats = cloud.queue.stats()
    assert stats["worker_capacity"] == 3 and stats["worker_busy"] == 1
    cloud.clock.advance(seconds=120)
    assert cloud.queue.stats()["workers"] == [], (
        "a worker that stops reporting drops out"
    )
    del worker


# ----------------------------------------------------------------------------- the gate at run start


def test_the_gate_is_checked_when_the_job_runs_not_only_when_it_was_scheduled(cloud):
    org = build_org(cloud, plan="team")
    candidate_id = org.candidate.member_id
    cloud.tenancy.record_consent(org.candidate, CONSENTS)
    ran = []

    def handler(app, job):
        block = app.gate.automation_block(job["org_id"], job["candidate_id"])
        if block:
            raise Skip(block[1])
        ran.append(job["job_id"])

    worker = Worker(
        cloud, threads=1, handlers={"autopilot.due": handler}, run_tick=False
    )
    cases = []
    # 1. queued while fine, then the organization is paused before a worker gets to it
    job1 = cloud.queue.enqueue(org.org_id, "autopilot.due", candidate_id=candidate_id)
    cloud.tenancy.pause_org(org.owner, "audit week")
    worker.run_once()
    cases.append(cloud.queue.get(job1))
    cloud.tenancy.resume_org(org.owner)
    # 2. the candidate's consent is withdrawn
    job2 = cloud.queue.enqueue(org.org_id, "autopilot.due", candidate_id=candidate_id)
    cloud.tenancy.revoke_consent(org.candidate, "automation")
    worker.run_once()
    cases.append(cloud.queue.get(job2))
    cloud.tenancy.record_consent(org.candidate, ["automation"])
    # 3. payment fails
    job3 = cloud.queue.enqueue(org.org_id, "autopilot.due", candidate_id=candidate_id)
    webhook(cloud, "invoice.payment_failed", org_id=org.org_id)
    worker.run_once()
    cases.append(cloud.queue.get(job3))
    assert [c["status"] for c in cases] == ["skipped", "skipped", "skipped"]
    assert (
        "paused" in cases[0]["error"].lower()
        and "accepted" in cases[1]["error"].lower()
        and "subscription" in cases[2]["error"].lower()
    )
    assert ran == []


def test_a_downgrade_takes_effect_within_a_minute_for_queued_work(cloud):
    org = build_org(cloud, plan="team")
    for index in range(5):
        add_member(cloud, org.owner, f"extra{index}@acme.test", "candidate")
    cloud.tenancy.record_consent(org.candidate, CONSENTS)
    assert cloud.gate.automation_block(org.org_id, org.candidate.member_id) is None
    cloud.billing.change_plan(org.owner, "starter", 5)
    cloud.clock.advance(days=31)
    cloud.billing.process_due()
    cloud.clock.advance(
        seconds=61
    )  # the entitlement cache is 60 s; a downgrade is effective by then even without invalidation
    block = cloud.gate.automation_block(org.org_id, org.candidate.member_id)
    assert block and block[0] == "over_limits"


# ----------------------------------------------------------------------------- the scheduler


def test_scheduler_enqueues_due_plans_once_and_skips_organizations_the_gate_refuses(
    cloud,
):
    one = build_org(cloud, "Due Org", plan="team", slug_email="due.test")
    two = build_org(cloud, "Paused Org", plan="team", slug_email="paused.test")
    for org in (one, two):
        cloud.tenancy.record_consent(org.candidate, CONSENTS)
        seed_workspace(
            cloud, org.org_id, org.candidate.member_id, job_title="Cloud Architect"
        )
        with cloud.workspaces.bind(org.org_id, org.candidate.member_id, kind="system"):
            with local._connect() as connection:
                from aira import autopilot

                plan = autopilot.create_plan(
                    connection,
                    {
                        "name": "P",
                        "positions": ["Architect"],
                        "sources": ["aira_jobs"],
                        "boards": [],
                        "mode": "review",
                        "min_score": 50,
                        "max_per_run": 1,
                        "interval_minutes": 240,
                    },
                )
                connection.execute(
                    "UPDATE autopilot_plans SET next_run_at = '2000-01-01T00:00:00+00:00' WHERE plan_id = ?",
                    (plan["plan_id"],),
                )
    cloud.tenancy.pause_org(two.owner, "paused for the test")
    counts = cloud.scheduler.enqueue_due()
    assert counts["autopilot.due"] == 1 and counts["skipped"] >= 1
    jobs = [cloud.queue.active_for(o.org_id, o.candidate.member_id) for o in (one, two)]
    assert jobs[0] and jobs[0]["kind"] == "autopilot.due" and jobs[1] is None
    cloud.clock.advance(seconds=61)
    assert cloud.scheduler.enqueue_due()["autopilot.due"] == 0, (
        "the same candidate is not queued twice"
    )


def test_tick_runs_billing_and_scheduling_once_across_replicas(cloud):
    first = cloud.lifecycle.tick()
    second = cloud.lifecycle.tick()
    assert "billing" in first and second == {"skipped": True}, (
        "a second replica within the lease does nothing"
    )
    cloud.clock.advance(seconds=25)
    assert "billing" in cloud.lifecycle.tick()


# ----------------------------------------------------------------------------- the real pipeline (Chromium)


class BoardAdapter:
    postings: list[JobPosting] = []

    def __init__(self, source, identifier, **_kwargs):
        self.source = source

    def search(self, query):
        wanted = query.keywords.casefold().split()[-1]
        return JobSearchResult(
            jobs=tuple(p for p in self.postings if wanted in p.title.casefold())
        )


def posting(stub, number, title, ats="lever", variant=""):
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
def ats():
    stub = AtsStub().start()
    yield stub
    stub.stop()


@pytest.fixture
def board(monkeypatch):
    monkeypatch.setattr(local, "build_adapter", BoardAdapter)
    monkeypatch.setattr(local, "_ollama_available", lambda: False)
    monkeypatch.setattr(BoardAdapter, "postings", [])
    return BoardAdapter


def onboard(app, org, who):
    """A candidate who has done what onboarding asks: consent, profile, resume, confirmed answers."""
    app.tenancy.record_consent(who, CONSENTS)
    browser = sign_in(app, who)
    status, profile = browser.call(
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
    assert status == 200 and profile["completeness"]["ready"], profile
    for field, value, sensitivity in (
        ("work_authorization", "Yes", "sensitive"),
        ("sponsorship_required", "No", "sensitive"),
        ("legal_attestation", "Yes", "sensitive"),
        ("how_did_you_hear", "LinkedIn", "public"),
    ):
        status, data = browser.call(
            "POST",
            "/api/profile/answers",
            {
                "canonical_field": field,
                "value": value,
                "confirmed": True,
                "sensitivity": sensitivity,
            },
        )
        assert status == 200, data
    return browser


def make_plan(browser, **extra):
    body = {
        "name": "Architect roles",
        "positions": ["Cloud Architect"],
        "boards": [{"source": "lever", "identifier": "acme"}],
        "min_score": 30,
        "max_per_run": 3,
        "mode": "apply",
        **extra,
    }
    status, data = browser.call("POST", "/api/autopilot/plans", body)
    assert status in (200, 201), data
    return data["plan"]


def run_jobs(app, limit=20):
    worker = Worker(app, threads=1, run_tick=False)
    return worker.drain(limit)


def test_a_coach_with_approve_scope_runs_a_plan_that_applies_in_a_worker(
    cloud, ats, board
):
    org = build_org(cloud, plan="team")
    cloud.tenancy.update_policy(org.owner, {"submission_mode": "auto"})
    candidate_id = org.candidate.member_id
    candidate = onboard(cloud, org, org.candidate)
    board.postings = [
        posting(ats, 1, "Principal Cloud Architect"),
        posting(ats, 2, "Nurse Practitioner"),
    ]
    plan = make_plan(candidate)
    cloud.tenancy.assign_coach(org.owner, org.coach.member_id, candidate_id, "approve")
    coach = sign_in(cloud, org.coach)
    status, body = coach.call(
        "POST",
        f"/api/autopilot/plans/{plan['plan_id']}/run",
        {},
        as_candidate=candidate_id,
    )
    assert status == 202 and body["queued"] is True
    status, body = coach.call(
        "POST",
        f"/api/autopilot/plans/{plan['plan_id']}/run",
        {},
        as_candidate=candidate_id,
    )
    assert status == 400 and "busy" in body["error"], (
        "one automation per candidate at a time"
    )
    status, overview = candidate.call("GET", "/api/autopilot")
    assert overview["status"]["running"]["detail"] == "Waiting for a worker"
    assert run_jobs(cloud) == 1
    status, overview = candidate.call("GET", "/api/autopilot")
    [run] = overview["recent"]
    assert (
        run["status"] == "submitted"
        and run["ats"] == "lever"
        and run["confirmation_number"].startswith("LV-")
    ), run
    assert run["screenshot_document_id"] and run["evidence_document_id"], (
        "every submission stores a screenshot and the confirmation"
    )
    assert len(ats.submissions) == 1 and ats.submissions[0].fields["email"] == [
        PROFILE["email"]
    ]
    # metering, audit and the dashboard follow the submission
    assert (
        cloud.usage.used(org.org_id, "applications") == 1
        and cloud.usage.used_today(org.org_id, candidate_id, "applications") == 1
    )
    submitted = [
        r
        for r in cloud.audit.list(org.org_id, candidate_id=candidate_id, limit=50)
        if r["action"] == "application.submit"
    ]
    assert (
        submitted
        and submitted[0]["actor_id"] == org.coach.member_id
        and submitted[0]["actor_role"] == "coach"
    )
    assert (
        cloud.usage.used(org.org_id, "searches") >= 1
        and cloud.usage.used(org.org_id, "browser_seconds") >= 1
    )
    owner = sign_in(cloud, org.owner)
    dashboard = owner.call("GET", "/api/org/dashboard")[1]
    assert dashboard["plan_usage"]["applications"]["used"] == 1
    assert dashboard["automation_today"]["submitted"] == 1
    assert (
        dashboard["pipeline"]["stages"][
            [s["stage"] for s in dashboard["pipeline"]["stages"]].index("applying")
        ]["count"]
        == 1
    )
    # the evidence opens for the candidate, decrypted, and is stored encrypted at rest
    status, document = candidate.call(
        "GET", f"/api/documents/{run['screenshot_document_id']}/download", raw=True
    )
    assert status == 200 and len(document) > 1000
    # a second run applies to nothing new
    candidate.call("POST", f"/api/autopilot/plans/{plan['plan_id']}/run", {})
    run_jobs(cloud)
    assert len(ats.submissions) == 1


def test_default_policy_fills_the_form_and_waits_for_a_person_to_submit(
    cloud, ats, board
):
    org = build_org(cloud, plan="team")  # policy default: review before submit
    candidate_id = org.candidate.member_id
    candidate = onboard(cloud, org, org.candidate)
    board.postings = [posting(ats, 1, "Principal Cloud Architect")]
    plan = make_plan(candidate, mode="apply")
    candidate.call("POST", f"/api/autopilot/plans/{plan['plan_id']}/run", {})
    run_jobs(cloud)
    status, overview = candidate.call("GET", "/api/autopilot")
    [run] = overview["recent"]
    assert run["status"] == "ready", (
        f"the organization requires review, so the candidate's 'apply' plan stops at the filled form: {run.get('error')} {run.get('steps')}"
    )
    assert (
        len(ats.submissions) == 0 and cloud.usage.used(org.org_id, "applications") == 0
    )
    # the candidate approves: that single click submits, and it is counted
    status, body = candidate.call(
        "POST", f"/api/autopilot/runs/{run['run_id']}/submit", {}
    )
    assert status == 202
    run_jobs(cloud)
    assert len(ats.submissions) == 1, "the candidate's click is the submission"
    status, after = candidate.call("GET", f"/api/autopilot/runs/{run['run_id']}")
    assert after["run"]["status"] == "submitted" and after["run"]["confirmation_number"]
    assert (
        cloud.usage.used(org.org_id, "applications") == 1
        and cloud.usage.used_today(org.org_id, candidate_id, "applications") == 1
    )
    submitted = [
        r
        for r in cloud.audit.list(org.org_id, candidate_id=candidate_id, limit=50)
        if r["action"] == "application.submit"
    ]
    assert len(submitted) == 1 and submitted[0]["actor_id"] == candidate_id


def test_monthly_limit_stops_applying_and_nothing_extra_is_sent(cloud, ats, board):
    org = build_org(cloud, plan="team")
    cloud.tenancy.update_policy(org.owner, {"submission_mode": "auto"})
    with cloud.db.write() as connection:
        connection.execute(
            "UPDATE subscriptions SET overrides = ? WHERE org_id = ?",
            ('{"apps_per_org_month": 1}', org.org_id),
        )
    cloud.entitlements.invalidate(org.org_id)
    candidate = onboard(cloud, org, org.candidate)
    board.postings = [
        posting(ats, 1, "Principal Cloud Architect"),
        posting(ats, 2, "Senior Cloud Architect"),
    ]
    plan = make_plan(candidate, max_per_run=3)
    candidate.call("POST", f"/api/autopilot/plans/{plan['plan_id']}/run", {})
    run_jobs(cloud)
    assert (
        len(ats.submissions) == 1 and cloud.usage.used(org.org_id, "applications") == 1
    )
    status, overview = candidate.call("GET", "/api/autopilot")
    log = " ".join(overview["passes"][0]["log"]) if overview["passes"] else ""
    assert "limit" in log.lower(), log


def test_a_held_question_is_answered_once_and_remembered_for_the_next_form(
    cloud, ats, board
):
    org = build_org(cloud, plan="team")
    cloud.tenancy.update_policy(org.owner, {"submission_mode": "auto"})
    candidate_id = org.candidate.member_id
    candidate = onboard(cloud, org, org.candidate)
    board.postings = [
        posting(ats, 1, "Principal Cloud Architect", variant="unknown_required")
    ]
    plan = make_plan(candidate)
    cloud.tenancy.assign_coach(org.owner, org.coach.member_id, candidate_id, "act")
    candidate.call("POST", f"/api/autopilot/plans/{plan['plan_id']}/run", {})
    run_jobs(cloud)
    status, overview = candidate.call("GET", "/api/autopilot")
    [run] = overview["recent"]
    assert run["status"] == "needs_review" and run["unanswered"], run
    label = run["unanswered"][0]["label"]
    # the held question shows in the candidate's and the assigned coach's queues
    coach = sign_in(cloud, org.coach)
    for browser in (candidate, coach):
        queue = browser.call("GET", "/api/org/queue")[1]["queue"]
        assert len(queue) == 1 and label in queue[0]["questions"]
    status, body = coach.call(
        "POST",
        f"/api/autopilot/runs/{run['run_id']}/answer",
        {"answers": {label: "Because of the platform scale."}, "remember": True},
        as_candidate=candidate_id,
    )
    assert status == 202
    run_jobs(cloud)
    assert len(ats.submissions) == 1
    assert candidate.call("GET", "/api/org/queue")[1]["queue"] == []
    # the answer was remembered: the same question on the next form needs no one
    board.postings = [
        posting(ats, 3, "Staff Cloud Architect", variant="unknown_required")
    ]
    candidate.call("POST", f"/api/autopilot/plans/{plan['plan_id']}/run", {})
    run_jobs(cloud)
    assert len(ats.submissions) == 2, (
        "the remembered answer filled the same question on the next form"
    )
    held = [r for r in cloud.audit.list(org.org_id, action="held.answer")]
    assert held and held[0]["actor_role"] == "coach"


def test_a_run_now_job_is_gated_again_when_a_worker_picks_it_up(cloud):
    org = build_org(cloud, plan="team")
    candidate_id = org.candidate.member_id
    cloud.tenancy.record_consent(org.candidate, CONSENTS)
    worker = Worker(cloud, threads=1, run_tick=False)  # the real handlers
    job = cloud.queue.enqueue(
        org.org_id,
        "autopilot.exec",
        candidate_id=candidate_id,
        payload={"spec": {"action": "run"}, "principal": {}},
    )
    cloud.tenancy.pause_org(
        org.owner, "audit week"
    )  # after it was queued, before a worker took it
    worker.run_once()
    row = cloud.queue.get(job)
    assert row["status"] == "skipped" and "paused" in row["error"].lower()


def test_a_worker_that_lost_its_lease_cannot_close_another_workers_run(cloud):
    org = build_org(cloud, plan="team")
    queue = cloud.queue
    job_id = queue.enqueue(
        org.org_id, "autopilot.due", candidate_id=org.candidate.member_id
    )
    first = queue.lease("worker-a", ["autopilot.due"])
    assert first and first["job_id"] == job_id
    # worker A stalls past its lease: the reaper hands the job back and worker B takes it
    cloud.clock.advance(seconds=cloud.config.lease_seconds + 5)
    assert queue.reap_expired() == 1
    cloud.clock.advance(seconds=120)  # past the retry back-off
    second = queue.lease("worker-b", ["autopilot.due"])
    assert second and second["job_id"] == job_id
    # worker A wakes up: nothing it reports touches B's lease
    assert queue.heartbeat(job_id, worker_id="worker-a") is False
    assert queue.fail(job_id, "A failed late", worker_id="worker-a") == "lost"
    queue.skip(job_id, "A was gated late", worker_id="worker-a")
    queue.complete(job_id, "A finished late", worker_id="worker-a")
    held = queue.get(job_id)
    assert held["status"] == "leased" and held["worker_id"] == "worker-b"
    # the real owner can renew and finish
    assert queue.heartbeat(job_id, worker_id="worker-b") is True
    queue.complete(job_id, "done", worker_id="worker-b")
    assert queue.get(job_id)["status"] == "done"


def test_a_long_running_handler_keeps_its_lease(cloud, monkeypatch):
    import time as _time

    org = build_org(cloud, plan="team")
    cloud.config.lease_seconds = 3  # renewed about every second
    renewals: list[str | None] = []
    original = cloud.queue.heartbeat

    def counting(job_id, **kwargs):
        renewals.append(kwargs.get("worker_id"))
        return original(job_id, **kwargs)

    monkeypatch.setattr(cloud.queue, "heartbeat", counting)

    def slow(app, job):
        _time.sleep(2.6)
        return "ok"

    worker = Worker(cloud, threads=1, handlers={"autopilot.due": slow}, run_tick=False)
    job = cloud.queue.enqueue(
        org.org_id, "autopilot.due", candidate_id=org.candidate.member_id
    )
    worker.run_once()
    assert cloud.queue.get(job)["status"] == "done"
    assert len(renewals) >= 2 and set(renewals) == {worker.worker_id}
