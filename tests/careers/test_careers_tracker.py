"""Careers: jobs added by hand, applications sent another way, offers and negotiation, the job search log,
and the fixes found by using the app end to end (analytics periods, the pipeline board, follow-ups,
the dashboard's review count, document titles).

Three layers, never the network or a real writing model: the pure tracker module, the single-user server
(``env``/``api`` from ``test_careers``) and the hosted gateway (``cloud_support``).
"""

from __future__ import annotations

import csv
import io
import sqlite3
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone

import pytest
import test_careers
from cloud_support import CONSENTS, build_org, sign_in

from aira import suite
from aira.airaapi import careers_tracker as tracker
from aira.analytics import summary, timeline
from aira.schema import migrate

env = test_careers.env
api = test_careers.api
stub = test_careers.stub
give_profile = test_careers.give_profile


class _Model:
    """A stand-in writing model: answers ``reply`` (or nothing) and remembers the prompts."""

    def __init__(self, reply: str | None = None) -> None:
        self.reply = reply
        self.prompts: list[str] = []

    def model(self, prompt: str) -> str | None:
        self.prompts.append(prompt)
        return self.reply


@pytest.fixture(autouse=True)
def no_model():
    """Never call a real writing model (Ollama may be running on this machine)."""
    previous = suite.gateway()
    fake = _Model()
    suite.set_gateway(fake)
    yield fake
    suite.set_gateway(previous)


@pytest.fixture
def db(tmp_path):
    connection = sqlite3.connect(tmp_path / "careers.sqlite3")
    connection.row_factory = sqlite3.Row
    migrate(connection)
    tracker.ensure(connection)
    connection.commit()
    yield connection
    connection.close()


def add_job(
    connection, job_id: str, title: str, company: str, stage: str = "applied"
) -> None:
    connection.execute(
        "INSERT INTO jobs (job_id, title, company, location, job_url, stage, updated_at, source, external_id) VALUES (?, ?, ?, 'Austin', ?, ?, CURRENT_TIMESTAMP, 'greenhouse', ?)",
        (job_id, title, company, f"https://example.test/{job_id}", stage, job_id),
    )


def raw_get(env, client, path):
    request = urllib.request.Request(env.base + path, method="GET")
    request.add_header("X-Requested-With", "aira")
    try:
        with client.opener.open(request, timeout=30) as response:
            return response.status, dict(response.headers), response.read()
    except urllib.error.HTTPError as error:
        return error.code, dict(error.headers), error.read()


# ---------------------------------------------------------------------------
# the tracker module
# ---------------------------------------------------------------------------


def test_offer_validation_speaks_plainly():
    with pytest.raises(ValueError, match="Enter the base pay"):
        tracker.clean_offer({})
    with pytest.raises(ValueError, match="cannot be negative"):
        tracker.clean_offer({"base_salary": 5, "bonus": -1})
    with pytest.raises(ValueError, match="three-letter code"):
        tracker.clean_offer({"base_salary": 5, "currency": "dollars"})
    with pytest.raises(ValueError, match="a year, a month or an hour"):
        tracker.clean_offer({"base_salary": 5, "pay_period": "week"})
    with pytest.raises(ValueError, match="must be a number"):
        tracker.clean_offer({"base_salary": "lots"})
    with pytest.raises(ValueError, match="must be a date"):
        tracker.clean_offer({"base_salary": 5, "deadline": "next friday"})
    clean = tracker.clean_offer({"base_salary": "$85,000", "currency": "usd"})
    assert clean["base_salary"] == 85000 and clean["currency"] == "USD"


def test_offer_totals_annualize_hourly_and_monthly_pay():
    hourly = tracker.with_totals(
        {"base_salary": 20, "pay_period": "hour", "hours_per_week": 30, "sign_on": 500}
    )
    assert hourly["annual_base"] == 20 * 30 * 52
    assert hourly["first_year_total"] == hourly["yearly_total"] + 500
    monthly = tracker.with_totals(
        {"base_salary": 5000, "pay_period": "month", "bonus": 6000, "benefits": 4000}
    )
    assert monthly["annual_base"] == 60000 and monthly["yearly_total"] == 70000


def test_compare_ranks_offers_never_mixes_currencies_and_checks_the_expectation(db):
    add_job(db, "a", "Engineer", "Acme")
    add_job(db, "b", "Engineer", "Globex")
    add_job(db, "c", "Engineer", "Initech")
    add_job(db, "d", "Engineer", "Umbrella")
    tracker.save_offer(db, "a", {"base_salary": 100000, "sign_on": 10000})
    tracker.save_offer(db, "b", {"base_salary": 105000})
    tracker.save_offer(db, "c", {"base_salary": 9000000, "currency": "INR"})
    tracker.save_offer(
        db, "d", {"base_salary": 200000, "status": "declined"}
    )  # a declined offer is never "best"
    result = tracker.compare(
        db,
        {
            "application_salary_expectation_min": "104000",
            "application_salary_currency": "USD",
        },
    )
    by_id = {o["job_id"]: o for o in result["offers"]}
    assert result["best_job_id"] == "a"  # 110,000 first-year pay beats 105,000
    assert by_id["b"]["vs_best"] == -5000
    assert by_id["c"]["vs_best"] is None and by_id["c"]["vs_expectation"] is None
    assert (
        by_id["a"]["meets_expectation"] is False
        and by_id["a"]["vs_expectation"] == -4000
    )
    assert by_id["b"]["meets_expectation"] is True
    assert by_id["d"]["best"] is False


def test_suggested_ask_uses_only_facts_and_explains_itself(db):
    add_job(db, "a", "Engineer", "Acme")
    add_job(db, "b", "Engineer", "Globex")
    a = tracker.save_offer(db, "a", {"base_salary": 100000})
    b = tracker.save_offer(db, "b", {"base_salary": 115000})
    ask, reasons = tracker.suggest_ask(a, {"max": None, "currency": None}, [a, b])
    assert ask == 115000 and any("Globex" in r for r in reasons)
    ask, reasons = tracker.suggest_ask(b, {"max": 130000, "currency": "USD"}, [a, b])
    assert ask == 130000 and any("looking for up to" in r for r in reasons)
    hourly = tracker.with_totals(
        {"base_salary": 18, "pay_period": "hour", "currency": "USD"}
    )
    ask, _ = tracker.suggest_ask(hourly, {"max": None, "currency": None}, [])
    assert ask == 19.5  # 8% more, to the half unit


def test_negotiation_email_keeps_the_numbers_and_ignores_a_model_that_drops_them(db):
    add_job(db, "a", "Barista", "Blue Bottle")
    offer = tracker.save_offer(
        db, "a", {"base_salary": 40000, "deadline": "2026-11-10"}
    )
    job = {"title": "Barista", "company": "Blue Bottle"}
    wanted = {"max": None, "currency": None}
    plain = tracker.negotiation_draft(
        job, offer, name="Pat Example", wanted=wanted, others=[offer]
    )
    assert plain["source"] == "template" and plain["ask"] == 43000
    assert "USD 43,000 a year" in plain["body"] and "before 2026-11-10" in plain["body"]
    assert (
        plain["body"].endswith("Pat Example") and "another offer" not in plain["body"]
    )

    def good(prompt, fallback):
        assert "Keep every number" in prompt
        return {
            "subject": "Barista offer",
            "body": "Could we make it 43,000? Thanks, Pat",
            "source": "model",
        }

    def bad(prompt, fallback):
        return {"subject": "Hi", "body": "Could you pay more?", "source": "model"}

    assert (
        tracker.negotiation_draft(
            job, offer, name="Pat", wanted=wanted, others=[], writer=good
        )["source"]
        == "model"
    )
    assert (
        tracker.negotiation_draft(
            job, offer, name="Pat", wanted=wanted, others=[], writer=bad
        )["source"]
        == "template"
    )
    custom = tracker.negotiation_draft(
        job, offer, name="Pat", wanted=wanted, others=[], ask="45000"
    )
    assert custom["ask"] == 45000 and custom["suggested_ask"] == 43000


def test_manual_job_validation():
    with pytest.raises(ValueError, match="job title"):
        tracker.manual_job({"company": "Acme"})
    with pytest.raises(ValueError, match="company"):
        tracker.manual_job({"title": "Cook"})
    with pytest.raises(ValueError, match="http"):
        tracker.manual_job(
            {"title": "Cook", "company": "Diner", "url": "javascript:alert(1)"}
        )
    with pytest.raises(ValueError, match="lowest pay"):
        tracker.manual_job(
            {"title": "Cook", "company": "Diner", "salary_min": 9, "salary_max": 5}
        )
    fields = tracker.manual_job({"title": " Line  cook ", "company": "Diner"})
    assert fields["title"] == "Line cook" and fields["source"] == "manual"
    assert not tracker.has_posting(fields["source_url"])
    assert tracker.has_posting("https://jobs.example/1")


def test_job_search_log_is_a_safe_spreadsheet(db):
    add_job(db, "a", "=HYPERLINK(1)", "Acme", stage="applied")
    add_job(db, "b", "Planner", "Globex", stage="discovered")
    db.execute(
        "INSERT INTO application_submissions (submission_id, job_id, submitted_at, method, confirmation_text, confirmation_number, actor, correlation_id, idempotency_key) VALUES ('s1', 'a', '2026-10-20T10:00:00+00:00', 'manual', 'Applied in person. Gave CV to Sam', '', 'me', 'c', 'job:a')"
    )
    tracker.save_offer(db, "a", {"base_salary": 50000})
    rows = list(csv.reader(io.StringIO(tracker.job_search_log(db).lstrip("﻿"))))
    assert (
        rows[0][0] == "Date applied" and len(rows) == 2
    )  # discovered jobs are not applications
    row = dict(zip(rows[0], rows[1]))
    assert row["Job title"] == "'=HYPERLINK(1)"  # never a formula
    assert (
        row["Date applied"] == "2026-10-20"
        and row["How you applied"] == "Applied in person"
    )
    assert row["Offer (first-year pay)"] == "USD 50,000"
    every = list(
        csv.reader(io.StringIO(tracker.job_search_log(db, scope="all").lstrip("﻿")))
    )
    assert len(every) == 3
    with pytest.raises(ValueError):
        tracker.job_search_log(db, scope="nope")


# ---------------------------------------------------------------------------
# analytics fixes (found in the browser: a saved job and a booked interview counted 0)
# ---------------------------------------------------------------------------


def test_analytics_counts_jobs_saved_today_and_interviews_booked_for_later(db):
    add_job(db, "a", "Engineer", "Acme", stage="saved")
    db.execute(
        "UPDATE jobs SET saved = 1, saved_at = ? WHERE job_id = 'a'",
        (datetime.now(timezone.utc).isoformat(timespec="seconds"),),
    )
    later = (datetime.now(timezone.utc) + timedelta(days=7)).isoformat(
        timespec="minutes"
    )
    db.execute(
        "INSERT INTO interviews (interview_id, job_id, interview_type, start_at, end_at, timezone, status, created_at) VALUES ('i1', 'a', 'recruiter_screen', ?, ?, 'UTC', 'scheduled', CURRENT_TIMESTAMP)",
        (later, later),
    )
    data = summary(db, days=30)
    assert data["metrics"]["jobs_saved"] == 1
    assert data["metrics"]["interviews_scheduled"] == 1
    assert data["metrics"]["interviews_upcoming"] == 1


def test_analytics_timeline_has_one_point_per_day(db):
    points = timeline(db, days=7)
    assert len(points) == 8 and all(p["discovered"] == 0 for p in points)
    days = [p["day"] for p in points]
    assert (
        days == sorted(days)
        and days[-1] == datetime.now(timezone.utc).date().isoformat()
    )


# ---------------------------------------------------------------------------
# single-user server journeys
# ---------------------------------------------------------------------------


def test_a_job_added_by_hand_is_tracked_like_any_other(env, api):
    status, body = api.call("POST", "/api/jobs/import", {"manual": {"title": "Cook"}})
    assert status == 400 and "company" in body["error"]
    created = api.post(
        "/api/jobs/import",
        {
            "manual": {
                "title": "Line cook",
                "company": "Corner Diner",
                "location": "Austin",
                "description": "Prep, grill and keep the kitchen clean. Food safety, inventory.",
                "salary_min": "30000",
                "salary_max": "36000",
                "currency": "usd",
            }
        },
        expect=201,
    )
    job = created["job"]
    assert created["source"] == "manual" and job["job_id"].startswith("manual:")
    detail = api.get(f"/api/jobs/{job['job_id']}")["job"]
    assert detail["has_posting"] is False and detail["sources"] == ["manual"]
    assert detail["salary_min"] == 30000 and detail["currency"] == "USD"
    listed = api.get("/api/jobs?view=all&source=manual")
    assert [j["title"] for j in listed["jobs"]] == ["Line cook"]
    with_link = api.post(
        "/api/jobs/import",
        {
            "manual": {
                "title": "Baker",
                "company": "Bakery",
                "url": "https://bakery.example/jobs/1",
            }
        },
        expect=201,
    )
    assert (
        api.get(f"/api/jobs/{with_link['job']['job_id']}")["job"]["has_posting"] is True
    )


def test_an_application_sent_another_way_is_recorded_logged_and_exported(env, api):
    job = api.post(
        "/api/jobs/import",
        {"manual": {"title": "Cashier", "company": "Corner Shop"}},
        expect=201,
    )["job"]
    status, body = api.call(
        "POST", f"/api/jobs/{job['job_id']}/submit", {"outside": True, "channel": "fax"}
    )
    assert status == 400 and "how you applied" in body["error"]
    recorded = api.post(
        f"/api/jobs/{job['job_id']}/submit",
        {
            "outside": True,
            "channel": "in_person",
            "submitted_at": "2026-10-20T12:00:00Z",
            "confirmation_text": "Gave my CV to Sam",
            "idempotency_key": f"job:{job['job_id']}",
        },
    )
    assert recorded["job"]["stage"] == "applied"  # straight from Discovered
    assert (
        recorded["submission"]["confirmation_text"]
        == "Applied in person. Gave my CV to Sam"
    )
    events = api.get(f"/api/jobs/{job['job_id']}/events")["events"]
    applied = [e for e in events if e["new_status"] == "applied"]
    assert len(applied) == 1 and applied[0]["previous_status"] == "discovered"
    # dated the day it was sent, not the day it was written down
    assert applied[0]["occurred_at"] == int(
        datetime(2026, 10, 20, 12, tzinfo=timezone.utc).timestamp()
    )
    again = api.post(
        f"/api/jobs/{job['job_id']}/submit",
        {
            "outside": True,
            "channel": "in_person",
            "idempotency_key": f"job:{job['job_id']}",
        },
    )
    assert again["submission"]["duplicate"] is True
    tasks = api.get("/api/tasks")["tasks"]
    assert any(t["kind"] == "follow_up" and t["job_id"] == job["job_id"] for t in tasks)

    status, headers, payload = raw_get(
        env, api, "/api/jobs?format=csv&scope=applications"
    )
    assert status == 200 and headers["Content-Type"].startswith("text/csv")
    assert "attachment" in headers["Content-Disposition"]
    rows = list(csv.reader(io.StringIO(payload.decode("utf-8").lstrip("﻿"))))
    row = dict(zip(rows[0], rows[1]))
    assert (
        row["Company"] == "Corner Shop"
        and row["How you applied"] == "Applied in person"
    )
    assert row["Date applied"] == "2026-10-20" and row["Link"] == ""
    status, _headers, payload = raw_get(
        env, api, "/api/jobs?format=csv&scope=everything"
    )
    assert status == 400


def test_recording_a_submission_from_ready_to_apply_moves_the_job_to_applied(env, api):
    """Regression: the submission was stored, then the move ready_to_apply -> applied failed with a 400."""
    give_profile(api)
    job = api.post(
        "/api/jobs/import",
        {
            "manual": {
                "title": "Barista",
                "company": "Blue Bottle",
                "description": "Coffee, customer service.",
            }
        },
        expect=201,
    )["job"]
    jid = job["job_id"]
    api.ok(
        "PATCH",
        f"/api/jobs/{jid}/documents",
        {"resume": "Tailored resume", "cover_letter": "Dear team"},
    )
    api.ok(
        "PATCH", f"/api/jobs/{jid}/stage", {"stage": "ready_to_apply", "reason": "test"}
    )
    result = api.post(
        f"/api/jobs/{jid}/submit",
        {"confirmation_number": "REQ-1", "idempotency_key": f"job:{jid}"},
    )
    assert result["job"]["stage"] == "applied"
    assert api.get("/api/analytics?days=30")["metrics"]["applications_submitted"] == 1


def test_offers_are_recorded_compared_negotiated_and_removed(env, api, no_model):
    give_profile(api)
    jobs = []
    for title, company in (("Barista", "Blue Bottle"), ("Shift lead", "Joe Coffee")):
        job = api.post(
            "/api/jobs/import",
            {"manual": {"title": title, "company": company}},
            expect=201,
        )["job"]
        api.post(
            f"/api/jobs/{job['job_id']}/submit", {"outside": True, "channel": "email"}
        )
        jobs.append(job["job_id"])
    a, b = jobs
    status, body = api.call(
        "PATCH", f"/api/jobs/{a}/notes", {"offer": {"base_salary": -1}}
    )
    assert status == 400 and "negative" in body["error"]
    status, body = api.call("PATCH", f"/api/jobs/{a}/notes", {"offer": "lots"})
    assert status == 400
    status, body = api.call("POST", f"/api/jobs/{a}/generate", {"kind": "negotiation"})
    assert status == 400 and "Record the offer first" in body["error"]

    saved = api.ok(
        "PATCH",
        f"/api/jobs/{a}/notes",
        {
            "offer": {
                "base_salary": "20",
                "pay_period": "hour",
                "hours_per_week": "40",
                "sign_on": "500",
            }
        },
    )["job"]
    assert saved["stage"] == "offer"  # an offer moves the application to Offer
    assert saved["offer"]["annual_base"] == 41600
    api.ok(
        "PATCH",
        f"/api/jobs/{b}/notes",
        {"offer": {"base_salary": "45000", "status": "negotiating"}},
    )

    listing = api.get("/api/jobs?view=offers&page_size=50")
    assert {j["job_id"] for j in listing["jobs"]} == {a, b}
    comparison = listing["comparison"]
    assert comparison["best_job_id"] == b and comparison["offers"][0]["job_id"] == b

    no_model.reply = None  # no writing model: the template is used
    draft = api.post(
        f"/api/jobs/{a}/generate", {"kind": "negotiation", "mention_other_offer": True}
    )["draft"]
    assert draft["source"] == "template" and draft["pay_period"] == "hour"
    assert "another offer with a higher base salary" in draft["body"]
    assert any("Joe Coffee" in reason for reason in draft["reasons"])
    assert no_model.prompts  # the model was asked, and its empty answer ignored
    no_model.reply = '{"subject": "About the offer", "body": "Could we agree on USD 22 an hour? Thank you."}'
    polished = api.post(
        f"/api/jobs/{a}/generate", {"kind": "negotiation", "ask": "22"}
    )["draft"]
    assert polished["source"] == "model" and polished["ask"] == 22

    detail = api.get(f"/api/jobs/{a}")["job"]
    assert detail["offer"]["sign_on"] == 500
    accepted = api.ok(
        "PATCH",
        f"/api/jobs/{a}/notes",
        {"offer": {**detail["offer"], "status": "accepted"}},
    )
    assert accepted["job"]["stage"] == "accepted"
    removed = api.ok("PATCH", f"/api/jobs/{b}/notes", {"offer": None})
    assert removed["job"]["offer"] is None
    assert [
        o["job_id"] for o in api.get("/api/jobs?view=offers")["comparison"]["offers"]
    ] == [a]


def test_clearing_a_follow_up_removes_its_reminder(env, api):
    job = api.post(
        "/api/jobs/import",
        {"manual": {"title": "Cook", "company": "Diner"}},
        expect=201,
    )["job"]
    jid = job["job_id"]
    later = (datetime.now(timezone.utc) + timedelta(days=3)).isoformat(
        timespec="minutes"
    )
    api.ok(
        "PATCH", f"/api/jobs/{jid}/notes", {"notes": "Call Sam", "follow_up_at": later}
    )
    assert [t["job_id"] for t in api.get("/api/dashboard")["tasks"]] == [jid]
    api.ok("PATCH", f"/api/jobs/{jid}/notes", {"notes": "Call Sam", "follow_up_at": ""})
    assert api.get("/api/dashboard")["tasks"] == []


def test_dashboard_counts_each_job_waiting_for_review_once(env, api):
    give_profile(api)
    job = api.post(
        "/api/jobs/import",
        {
            "manual": {
                "title": "Barista",
                "company": "Blue Bottle",
                "description": "Coffee and customer service.",
            }
        },
        expect=201,
    )["job"]
    api.post(f"/api/jobs/{job['job_id']}/prepare", {}, expect=201)
    assert api.get(f"/api/jobs/{job['job_id']}")["job"]["stage"] == "needs_review"
    assert api.get("/api/dashboard")["counts"]["needs_review"] == 1


def test_the_pipeline_view_holds_every_application_and_documents_name_their_job(
    env, api
):
    applied = api.post(
        "/api/jobs/import",
        {"manual": {"title": "Cook", "company": "Diner"}},
        expect=201,
    )["job"]
    api.post(
        f"/api/jobs/{applied['job_id']}/submit", {"outside": True, "channel": "email"}
    )
    for index in range(3):
        api.post(
            "/api/jobs/import",
            {"manual": {"title": f"Role {index}", "company": "Other"}},
            expect=201,
        )
    pipeline = api.get("/api/jobs?view=pipeline&page_size=2")
    assert [j["job_id"] for j in pipeline["jobs"]] == [applied["job_id"]] and pipeline[
        "total"
    ] == 1
    import base64

    api.post(
        "/api/documents",
        {
            "filename": "cv.txt",
            "content_base64": base64.b64encode(b"my cv").decode(),
            "kind": "other",
            "job_id": applied["job_id"],
        },
        expect=201,
    )
    docs = api.get("/api/documents")["documents"]
    assert docs[0]["job_title"] == "Cook · Diner"


# ---------------------------------------------------------------------------
# hosted: the same features in an organization's candidate workspace
# ---------------------------------------------------------------------------


def test_hosted_candidate_adds_a_job_records_an_offer_and_exports_the_log(cloud):
    org = build_org(cloud)
    other = build_org(cloud, "Other Org", slug_email="other.test")
    cloud.tenancy.record_consent(org.candidate, CONSENTS)
    cloud.tenancy.record_consent(other.candidate, CONSENTS)
    me = sign_in(cloud, org.candidate)
    status, created = me.call(
        "POST", "/api/jobs/import", {"manual": {"title": "Cook", "company": "Diner"}}
    )
    assert status == 201, created
    jid = created["job"]["job_id"]
    status, body = me.call(
        "POST", f"/api/jobs/{jid}/submit", {"outside": True, "channel": "in_person"}
    )
    assert status == 200 and body["job"]["stage"] == "applied", body
    status, body = me.call(
        "PATCH", f"/api/jobs/{jid}/notes", {"offer": {"base_salary": 50000}}
    )
    assert status == 200 and body["job"]["stage"] == "offer", body
    status, listing = me.call("GET", "/api/jobs?view=offers")
    assert status == 200 and listing["comparison"]["best_job_id"] == jid
    status, draft = me.call(
        "POST", f"/api/jobs/{jid}/generate", {"kind": "negotiation"}
    )
    assert status == 200 and draft["draft"]["ask"] == 54000, draft
    status, payload = me.call("GET", "/api/jobs?format=csv", raw=True)
    assert (
        status == 200
        and b"Diner" in payload
        and me.header("Content-Type").startswith("text/csv")
    )

    # Another organization's candidate never sees it.
    stranger = sign_in(cloud, other.candidate)
    status, listing = stranger.call("GET", "/api/jobs?view=offers")
    assert (
        status == 200
        and listing["jobs"] == []
        and listing["comparison"]["offers"] == []
    )
    status, _ = stranger.call("GET", f"/api/jobs/{jid}")
    assert status == 404
    status, payload = stranger.call("GET", "/api/jobs?format=csv", raw=True)
    assert status == 200 and b"Diner" not in payload
