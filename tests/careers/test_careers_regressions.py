"""Careers: regression tests for what an independent tester found after the offers / outside-application round.

* Careers is the account owner's job search: another household profile (a child, a partner) cannot read or
  change it, and the owner's PIN is honoured.
* Applications recorded as sent another way never use up the daily limit; future dates are refused (or taken
  as now when only a few hours ahead); a past date is the day the job counts as applied.
* A declined or expired offer closes the application; it never claims to be "a higher offer".
* Jobs added by hand can be corrected; a link Aira already tracks is reported, not silently dropped; only text is
  accepted for text fields.
* The job picker and the pipeline reach jobs beyond the first page.
* The account export includes recorded offers.

Single-user server through ``env``/``api`` from ``test_careers``; hosted through ``cloud_support``. No network,
no real writing model.
"""

from __future__ import annotations

import csv
import io
import sqlite3
from datetime import datetime, timedelta, timezone

import pytest
import test_careers
from cloud_support import CONSENTS, build_org, sign_in
from life_support import Household

from aira import suite
from aira.airaapi import careers_tracker as tracker
from aira.applications import parse_submitted_at, submissions_today
from aira.schema import migrate

env = test_careers.env
api = test_careers.api
stub = test_careers.stub


class _NoModel:
    def model(self, prompt: str) -> str | None:
        return None


@pytest.fixture(autouse=True)
def no_model():
    """Never call a real writing model (Ollama may be running on this machine)."""
    previous = suite.gateway()
    suite.set_gateway(_NoModel())
    yield
    suite.set_gateway(previous)


def manual(api, title="Cashier", company="Corner Shop", **extra):
    return api.post(
        "/api/jobs/import",
        {"manual": {"title": title, "company": company, **extra}},
        expect=201,
    )["job"]


def applied_job(api, title="Cashier", company="Corner Shop"):
    job = manual(api, title, company)
    api.post(f"/api/jobs/{job['job_id']}/submit", {"outside": True, "channel": "email"})
    return job


def csv_rows(api, scope="applications"):
    status, payload = raw(api, f"/api/jobs?format=csv&scope={scope}")
    assert status == 200
    rows = list(csv.reader(io.StringIO(payload.decode("utf-8").lstrip("﻿"))))
    return [dict(zip(rows[0], row)) for row in rows[1:]]


def raw(client, path):
    import urllib.error
    import urllib.request

    request = urllib.request.Request(client.base + path, method="GET")
    request.add_header("X-Requested-With", "aira")
    try:
        with client.opener.open(request, timeout=30) as response:
            return response.status, response.read()
    except urllib.error.HTTPError as error:
        return error.code, error.read()


# ---------------------------------------------------------------------------
# household profiles: Careers is the owner's
# ---------------------------------------------------------------------------


def test_a_child_profile_cannot_read_or_change_the_owners_job_search(env, api):
    job = applied_job(api, "Secret role", "Rival Corp")
    jid = job["job_id"]
    api.ok(
        "PATCH",
        f"/api/jobs/{jid}/notes",
        {"offer": {"base_salary": 150000, "notes": "Do not tell anyone"}},
    )
    home = Household(env, api)
    kid = home.add("Kid", age_group="child")
    partner = home.add("Sam", age_group="adult")
    pinned = home.owner.post(f"/api/life/profiles/{home.owner.id}/pin", {"pin": "4321"})
    home.owner.header = pinned["header"]

    for person in (kid, partner):
        for method, path, body in (
            ("GET", "/api/jobs?view=offers", None),
            ("GET", f"/api/jobs/{jid}", None),
            ("GET", "/api/dashboard", None),
            ("GET", "/api/applications/queue", None),
            ("GET", "/api/jobs?format=csv&scope=applications", None),
            ("GET", "/api/profile", None),
            ("GET", "/api/careers/overview", None),
            ("POST", f"/api/jobs/{jid}/generate", {"kind": "negotiation"}),
            ("PATCH", f"/api/jobs/{jid}/notes", {"offer": {"base_salary": 1}}),
        ):
            status, data = person.call(method, path, body)
            assert status == 403, (person.name, method, path, status)
            assert data["code"] == "careers_owner_only", data
            assert "Rival Corp" not in str(data) and "150000" not in str(data)
        assert "Switch to" in data["error"]

    # The owner, with the PIN unlocked, still has everything and the offer is untouched.
    offers = home.owner.get("/api/jobs?view=offers")
    assert offers["comparison"]["offers"][0]["base_salary"] == 150000
    assert offers["comparison"]["offers"][0]["notes"] == "Do not tell anyone"

    # The owner's id without the PIN's unlock token is locked, like everywhere else.
    status, data = kid.api.call("GET", "/api/jobs?view=offers")  # no profile header
    assert status == 403 and data["code"] == "profile_required"
    from life_support import Person

    stale = Person(env, api, home.owner.profile, home.owner.id)
    status, data = stale.call("GET", "/api/jobs?view=offers")
    assert status == 403 and data["code"] == "profile_locked"
    # The shell loads the profile before it knows who is using the browser.
    status, _ = api.call("GET", "/api/profile")
    assert status == 200


def test_without_an_owner_pin_a_request_without_a_profile_is_the_owner(env, api):
    applied_job(api)
    home = Household(env, api)
    kid = home.add("Kid", age_group="child")
    # anyone may pick the owner's face without a PIN, so a header-less request is the owner's
    assert api.get("/api/jobs?view=pipeline")["total"] == 1
    assert home.owner.get("/api/jobs?view=pipeline")["total"] == 1
    status, data = kid.call("GET", "/api/jobs?view=pipeline")
    assert status == 403 and data["code"] == "careers_owner_only"
    # the everyday apps still work for the child
    assert kid.get("/api/life/me")["profile"]["name"] == "Kid"


def test_a_household_of_one_needs_no_profile_header(env, api):
    Household(env, api)  # makes the owner's profile
    applied_job(api)
    assert api.get("/api/dashboard")["counts"]["applied"] == 1


# ---------------------------------------------------------------------------
# the daily limit and submission dates
# ---------------------------------------------------------------------------


def test_outside_applications_do_not_use_the_daily_limit(env, api):
    api.post("/api/settings", {"daily_application_limit": 2})
    a, b, c = (manual(api, f"Job {n}", "Co") for n in "ABC")
    for job in (a, b):
        api.post(
            f"/api/jobs/{job['job_id']}/submit", {"outside": True, "channel": "email"}
        )
    queue = api.get("/api/applications/queue")
    assert queue["submitted_today"] == 0 and queue["remaining_today"] == 2
    result = api.post(f"/api/jobs/{c['job_id']}/submit", {"confirmation_number": "X1"})
    assert result["submission"]["method"] == "manual"
    assert api.get("/api/applications/queue")["submitted_today"] == 1
    sent = api.get(f"/api/jobs/{a['job_id']}")["job"]["submission"]
    assert sent["method"] == "outside"
    assert {row["How you applied"] for row in csv_rows(api)} >= {"Applied by email"}


def test_future_submission_dates_are_refused(env, api):
    one, two, three = (manual(api, f"Role {n}", "Co") for n in range(3))
    status, body = api.call(
        "POST",
        f"/api/jobs/{one['job_id']}/submit",
        {"submitted_at": "2062-10-27T09:00"},
    )
    assert status == 400 and "future" in body["error"]
    status, body = api.call(
        "POST",
        f"/api/jobs/{two['job_id']}/submit",
        {"outside": True, "channel": "email", "submitted_at": "2099-01-01T12:00:00Z"},
    )
    assert status == 400 and "future" in body["error"]
    status, body = api.call(
        "POST",
        f"/api/jobs/{two['job_id']}/submit",
        {"outside": True, "channel": "email", "submitted_at": "1970-01-01T12:00:00Z"},
    )
    assert status == 400 and "year" in body["error"]
    # "today at noon" typed in the morning is a few hours ahead: taken as now
    soon = (datetime.now(timezone.utc) + timedelta(hours=3)).isoformat()
    result = api.post(
        f"/api/jobs/{three['job_id']}/submit",
        {"outside": True, "channel": "email", "submitted_at": soon},
    )
    stored = datetime.fromisoformat(result["submission"]["submitted_at"])
    assert stored <= datetime.now(timezone.utc) + timedelta(minutes=1)
    assert api.get("/api/applications/queue")["submitted_today"] == 0
    assert all(row["Date applied"] < "2030" for row in csv_rows(api))


def test_parse_submitted_at_normalises_to_utc_and_refuses_the_future():
    now = datetime(2026, 10, 27, 8, 0, tzinfo=timezone.utc)
    assert parse_submitted_at("2026-10-27T10:30+05:30", now=now) == datetime(
        2026, 10, 27, 5, 0, tzinfo=timezone.utc
    )
    assert (
        parse_submitted_at("2026-10-27T20:00", now=now) == now
    )  # local time read as UTC
    with pytest.raises(ValueError, match="future"):
        parse_submitted_at("2026-10-28T23:00Z", now=now)
    with pytest.raises(ValueError, match="ISO"):
        parse_submitted_at("next tuesday", now=now)


def test_only_todays_aira_applications_count_against_the_limit(tmp_path):
    connection = sqlite3.connect(tmp_path / "limit.sqlite3")
    connection.row_factory = sqlite3.Row
    migrate(connection)
    now = datetime(2026, 10, 27, 15, 0, tzinfo=timezone.utc)
    rows = (
        ("a", "2026-10-27T09:00+00:00", "manual"),
        ("b", "2026-10-27T10:00+00:00", "auto"),
        ("c", "2026-10-27T11:00+00:00", "outside"),  # sent another way
        ("d", "2062-10-27T09:00+00:00", "manual"),  # an old typo stored before the fix
        ("e", "2026-10-26T23:59+00:00", "manual"),  # yesterday
    )
    for key, when, method in rows:
        connection.execute(
            "INSERT INTO application_submissions (submission_id, job_id, submitted_at, method, idempotency_key) VALUES (?, ?, ?, ?, ?)",
            (key, key, when, method, key),
        )
    assert submissions_today(connection, now=now) == 2
    connection.close()


def test_an_application_dated_in_the_past_counts_on_that_day(env, api):
    job = manual(api, "Baker", "Corner Bakery")
    jid = job["job_id"]
    sent = (datetime.now(timezone.utc) - timedelta(days=56)).replace(
        hour=12, minute=0, second=0, microsecond=0
    )
    api.post(
        f"/api/jobs/{jid}/submit",
        {"outside": True, "channel": "in_person", "submitted_at": sent.isoformat()},
    )
    detail = api.get(f"/api/jobs/{jid}")["job"]
    assert detail["applied_at"].startswith(sent.date().isoformat())
    applied = [e for e in detail["events"] if e["new_status"] == "applied"]
    assert applied[0]["occurred_at"] == int(sent.timestamp())
    # Analytics: not in the last 30 days, but in the last 90.
    assert api.get("/api/analytics?days=30")["metrics"]["applications_submitted"] == 0
    assert api.get("/api/analytics?days=90")["metrics"]["applications_submitted"] == 1
    # It was sent 8 weeks ago: the follow-up is due now, not a week from today.
    task = next(t for t in detail["tasks"] if t["kind"] == "follow_up")
    due = datetime.fromisoformat(task["due_at"])
    assert due <= datetime.now(timezone.utc) + timedelta(minutes=1)

    recent = manual(api, "Cook", "Diner")
    two_days = datetime.now(timezone.utc) - timedelta(days=2)
    api.post(
        f"/api/jobs/{recent['job_id']}/submit",
        {"outside": True, "channel": "email", "submitted_at": two_days.isoformat()},
    )
    task = next(
        t
        for t in api.get(f"/api/jobs/{recent['job_id']}")["job"]["tasks"]
        if t["kind"] == "follow_up"
    )
    due = datetime.fromisoformat(task["due_at"])
    assert abs((due - (two_days + timedelta(days=7))).total_seconds()) < 120


# ---------------------------------------------------------------------------
# offers that were declined or expired
# ---------------------------------------------------------------------------


def test_a_declined_offer_closes_the_application_and_is_never_the_best(env, api):
    best = applied_job(api, "Analyst", "Open Co")
    declined = applied_job(api, "Analyst", "Big Co")
    api.ok(
        "PATCH",
        f"/api/jobs/{best['job_id']}/notes",
        {"offer": {"base_salary": 60000}},
    )
    body = api.ok(
        "PATCH",
        f"/api/jobs/{declined['job_id']}/notes",
        {"offer": {"base_salary": 99580, "status": "declined"}},
    )
    assert body["job"]["stage"] == "withdrawn"
    events = api.get(f"/api/jobs/{declined['job_id']}/events")["events"]
    assert [e["new_status"] for e in events][-2:] == ["offer", "withdrawn"]
    assert events[-1]["reason"] == "Offer declined"
    comparison = api.get("/api/jobs?view=offers")["comparison"]
    by_job = {o["job_id"]: o for o in comparison["offers"]}
    assert comparison["best_job_id"] == best["job_id"]
    assert by_job[declined["job_id"]]["vs_best"] is None
    assert by_job[declined["job_id"]]["open"] is False
    statuses = {row["Company"]: row["Status"] for row in csv_rows(api)}
    assert statuses == {"Open Co": "Offer", "Big Co": "Offer declined"}
    # The pipeline shows it as closed.
    pipeline = api.get("/api/jobs?view=pipeline")["jobs"]
    assert {j["job_id"]: j["stage"] for j in pipeline}[
        declined["job_id"]
    ] == "withdrawn"
    # Opening the offer again (the employer came back) moves it back to Offer.
    body = api.ok(
        "PATCH",
        f"/api/jobs/{declined['job_id']}/notes",
        {"offer": {"base_salary": 99580, "status": "negotiating"}},
    )
    assert body["job"]["stage"] == "offer"
    # An expired offer closes it too.
    body = api.ok(
        "PATCH",
        f"/api/jobs/{declined['job_id']}/notes",
        {"offer": {"base_salary": 99580, "status": "expired"}},
    )
    assert body["job"]["stage"] == "withdrawn"
    assert api.get(f"/api/jobs/{declined['job_id']}/events")["events"][-1][
        "reason"
    ] == ("Offer expired")


def test_an_accepted_job_stays_accepted_when_its_offer_is_marked_expired(env, api):
    job = applied_job(api)
    api.ok(
        "PATCH",
        f"/api/jobs/{job['job_id']}/notes",
        {"offer": {"base_salary": 40000, "status": "accepted"}},
    )
    body = api.ok(
        "PATCH",
        f"/api/jobs/{job['job_id']}/notes",
        {"offer": {"base_salary": 40000, "status": "expired"}},
    )
    assert body["job"]["stage"] == "accepted"


def test_the_negotiation_email_never_mentions_a_declined_or_expired_offer():
    offer = {
        "job_id": "a",
        "currency": "USD",
        "base_salary": 50000,
        "status": "considering",
    }
    others = [
        offer,
        {"job_id": "b", "currency": "USD", "base_salary": 90000, "status": "declined"},
        {"job_id": "c", "currency": "USD", "base_salary": 95000, "status": "expired"},
    ]
    draft = tracker.negotiation_draft(
        {"title": "Analyst", "company": "Open Co"},
        offer,
        name="Ana",
        wanted={},
        others=others,
        mention_other_offer=True,
    )
    assert "another offer" not in draft["body"]
    assert draft["ask"] == 54000
    others.append(
        {
            "job_id": "d",
            "currency": "USD",
            "base_salary": 70000,
            "status": "negotiating",
        }
    )
    draft = tracker.negotiation_draft(
        {"title": "Analyst", "company": "Open Co"},
        offer,
        name="Ana",
        wanted={},
        others=others,
        mention_other_offer=True,
    )
    assert "I have another offer with a higher base salary" in draft["body"]


def test_negotiation_through_the_api_ignores_a_declined_higher_offer(env, api):
    a = applied_job(api, "Analyst", "Open Co")
    b = applied_job(api, "Analyst", "Big Co")
    api.ok("PATCH", f"/api/jobs/{a['job_id']}/notes", {"offer": {"base_salary": 50000}})
    api.ok(
        "PATCH",
        f"/api/jobs/{b['job_id']}/notes",
        {"offer": {"base_salary": 90000, "status": "declined"}},
    )
    draft = api.post(
        f"/api/jobs/{a['job_id']}/generate",
        {"kind": "negotiation", "mention_other_offer": True, "polish": False},
    )["draft"]
    assert "another offer" not in draft["body"]


# ---------------------------------------------------------------------------
# jobs added by hand
# ---------------------------------------------------------------------------


def test_a_job_added_by_hand_can_be_corrected_and_described_later(env, api):
    job = manual(api, "Shift lead", "Corner Bakry")
    jid = job["job_id"]
    applied = api.post(
        f"/api/jobs/{jid}/submit", {"outside": True, "channel": "in_person"}
    )
    assert applied["job"]["stage"] == "applied"
    body = api.ok(
        "PATCH",
        f"/api/jobs/{jid}/notes",
        {
            "job": {
                "company": "Corner Bakery",
                "description": "Open the shop, bake bread, serve customers.",
                "salary_min": "30000",
                "currency": "usd",
            }
        },
    )
    assert body["job"]["company"] == "Corner Bakery"
    detail = api.get(f"/api/jobs/{jid}")["job"]
    assert detail["title"] == "Shift lead"  # unchanged fields are kept
    assert detail["description"].startswith("Open the shop")
    assert detail["salary_min"] == 30000 and detail["currency"] == "USD"
    assert detail["stage"] == "applied" and detail["submission"]  # history kept
    assert detail["has_posting"] is False
    # a link added later becomes the posting
    api.ok(
        "PATCH",
        f"/api/jobs/{jid}/notes",
        {"job": {"url": "https://bakery.example/jobs/lead"}},
    )
    detail = api.get(f"/api/jobs/{jid}")["job"]
    assert detail["has_posting"] is True
    assert detail["job_url"] == "https://bakery.example/jobs/lead"
    # and removed again
    api.ok("PATCH", f"/api/jobs/{jid}/notes", {"job": {"url": ""}})
    assert api.get(f"/api/jobs/{jid}")["job"]["has_posting"] is False


def test_editing_refuses_bad_values_and_jobs_from_a_posting(env, api):
    job = manual(api)
    jid = job["job_id"]
    for change, words in (
        ({"title": ""}, "job title"),
        ({"title": ["a"]}, "must be text"),
        ({"stage": "offer"}, "cannot be changed"),
        ({"url": "ftp://x"}, "http"),
    ):
        status, body = api.call("PATCH", f"/api/jobs/{jid}/notes", {"job": change})
        assert status == 400 and words in body["error"], (change, body)
    other = manual(api, "Barista", "Cafe", url="https://cafe.example/jobs/1")
    status, body = api.call(
        "PATCH",
        f"/api/jobs/{jid}/notes",
        {"job": {"url": "https://cafe.example/jobs/1"}},
    )
    assert status == 400 and "already tracks" in body["error"]
    with test_careers.app._connect() as connection:
        connection.execute(
            "UPDATE jobs SET source = 'greenhouse' WHERE job_id = ?", (other["job_id"],)
        )
    status, body = api.call(
        "PATCH", f"/api/jobs/{other['job_id']}/notes", {"job": {"title": "X"}}
    )
    assert status == 400 and "you added yourself" in body["error"]


def test_a_second_hand_added_job_at_a_tracked_link_says_so(env, api):
    link = "https://cafe.example/jobs/barista"
    first = manual(api, "Barista", "Blue Bottle", url=link)
    status, again = api.call(
        "POST",
        "/api/jobs/import",
        {"manual": {"title": "Totally different", "company": "Other Co", "url": link}},
    )
    assert status == 200 and again["existing"] is True
    assert again["job"]["job_id"] == first["job_id"]
    assert again["job"]["title"] == "Barista"
    # without a link every job is its own
    one = manual(api, "Cleaner", "Office")
    two = manual(api, "Cleaner", "Office")
    assert one["job_id"] != two["job_id"]


def test_hand_added_jobs_accept_only_text(env, api):
    for bad in (
        {"title": ["a"], "company": {"b": 1}},
        {"title": "Cook", "company": 12},
        {"title": "Cook", "company": "Diner", "description": {"x": 1}},
        {"title": "Cook", "company": "Diner", "url": ["https://x.example"]},
    ):
        status, body = api.call("POST", "/api/jobs/import", {"manual": bad})
        assert status == 400 and "must be text" in body["error"], (bad, body)


def test_manual_values_unit():
    with pytest.raises(ValueError, match="must be text"):
        tracker.manual_job({"title": 5, "company": "Co"})
    values = tracker.manual_edit(
        {"title": "New"},
        {
            "title": "Old",
            "company": "Co",
            "job_url": tracker.MANUAL_URL_PREFIX + "abc",
            "external_id": "abc",
        },
    )
    assert (
        values["title"] == "New" and values["url"] == tracker.MANUAL_URL_PREFIX + "abc"
    )


# ---------------------------------------------------------------------------
# beyond the first page: the job picker's search and the pipeline's pages
# ---------------------------------------------------------------------------


def test_jobs_beyond_the_first_200_are_found_by_search_and_paging(env, api):
    with test_careers.app._connect() as connection:
        for n in range(205):
            connection.execute(
                """INSERT INTO jobs (job_id, source, external_id, title, company, job_url, stage, updated_at)
                VALUES (?, 'greenhouse', ?, ?, 'Co', ?, 'applied', datetime('now', ?))""",
                (
                    f"greenhouse:{n:03d}",
                    f"{n:03d}",
                    f"Job {n:03d}",
                    f"https://example.test/{n}",
                    f"-{205 - n} minutes",
                ),
            )
    first = api.get("/api/jobs?view=pipeline&page_size=200&page=1&sort=updated")
    assert first["total"] == 205 and len(first["jobs"]) == 200
    assert "greenhouse:000" not in {j["job_id"] for j in first["jobs"]}
    stages = {s["stage"]: s["count"] for s in first["facets"]["stages"]}
    assert stages["applied"] == 205  # the board's column count
    second = api.get("/api/jobs?view=pipeline&page_size=200&page=2&sort=updated")
    assert {j["job_id"] for j in second["jobs"]} >= {"greenhouse:000"}
    # the picker searches by what is typed
    found = api.get("/api/jobs?view=all&page_size=50&sort=updated&q=Job%20000")
    assert [j["job_id"] for j in found["jobs"]] == ["greenhouse:000"]
    # and a job linked earlier is read by its id
    assert api.get("/api/jobs/greenhouse:000")["job"]["title"] == "Job 000"


# ---------------------------------------------------------------------------
# the account export
# ---------------------------------------------------------------------------


def test_the_account_export_includes_recorded_offers(env, api):
    job = applied_job(api)
    api.ok(
        "PATCH",
        f"/api/jobs/{job['job_id']}/notes",
        {"offer": {"base_salary": 45000, "bonus": 2000, "notes": "Call back Friday"}},
    )
    export = api.get("/api/account/export")
    offers = export["tables"]["job_offers"]
    assert (
        offers[0]["base_salary"] == 45000 and offers[0]["notes"] == "Call back Friday"
    )


# ---------------------------------------------------------------------------
# hosted: the same fixes in a candidate's workspace
# ---------------------------------------------------------------------------


def test_hosted_candidate_edits_a_job_and_declines_an_offer(cloud):
    org = build_org(cloud)
    cloud.tenancy.record_consent(org.candidate, CONSENTS)
    me = sign_in(cloud, org.candidate)
    status, created = me.call(
        "POST", "/api/jobs/import", {"manual": {"title": "Cok", "company": "Diner"}}
    )
    assert status == 201, created
    jid = created["job"]["job_id"]
    status, body = me.call(
        "PATCH", f"/api/jobs/{jid}/notes", {"job": {"title": "Cook"}}
    )
    assert status == 200 and body["job"]["title"] == "Cook", body
    status, body = me.call(
        "POST", f"/api/jobs/{jid}/submit", {"outside": True, "channel": "in_person"}
    )
    assert status == 200 and body["submission"]["method"] == "outside", body
    status, queue = me.call("GET", "/api/applications/queue")
    assert status == 200 and queue["submitted_today"] == 0
    status, body = me.call(
        "PATCH",
        f"/api/jobs/{jid}/notes",
        {"offer": {"base_salary": 30000, "status": "declined"}},
    )
    assert status == 200 and body["job"]["stage"] == "withdrawn", body
