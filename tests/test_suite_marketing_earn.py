"""Marketing: the fixes and the money features added on top of the first release.

* Bugs: drafts with no date were invisible, 30-day plans were posted at 09:00 UTC instead of the person's morning,
  the subscriber tag filter ran after the row limit, a huge amount crashed the server.
* Leads that earn: a value, follow-up dates, a reply by email, a payment request with the business's own payment
  link, "Mark paid" that records revenue once in the Business hub (and takes it off when undone), campaign ROI from
  paid leads.
* Post reminders: a background task emails the business when a scheduled post is due, once.
* The media kit: a public page with audience numbers and a rate card where brands ask for a sponsorship.

Unit tests on a scratch database, then the single-user server, then the hosted gateway.
"""

# ruff: noqa: F811
from __future__ import annotations

import json
import sqlite3
from datetime import timedelta

import pytest
from cloud_support import Browser, build_org, sign_in

import test_careers
from aira.suite import background, kit
from aira.suite.marketing import service, store
from test_suite_marketing import (  # noqa: F401 - fixtures
    _anonymous,
    db,
    local_index,
    no_live_model,
    no_model,
)

env = test_careers.env
api = test_careers.api
stub = test_careers.stub

M = "/api/marketing"


def _stamp(delta: timedelta) -> str:
    return (kit.utcnow() + delta).strftime("%Y-%m-%dT%H:%M:%SZ")


def _lead(db, **extra):
    form = service.create_form(
        db, {"title": "Book a consult", "campaign_id": extra.pop("campaign_id", "")}
    )
    service.submit(db, form["id"], {"name": "Ana Lopez", "email": "ana@example.com"})
    lead = service.list_submissions(db, form_id=form["id"])[0]
    if extra:
        lead = service.update_submission(db, lead["id"], extra)
    return lead


# ---------------------------------------------------------------------------
# bugs
# ---------------------------------------------------------------------------


def test_drafts_without_a_date_are_listed(db):
    """Regression: an AI draft or a repurposed copy has no date and showed up nowhere in the calendar."""
    draft = service.create_post(
        db, {"channel": "linkedin", "body": "A draft", "status": "draft"}
    )
    service.create_post(
        db,
        {
            "channel": "x",
            "body": "Dated",
            "status": "scheduled",
            "scheduled_at": _stamp(timedelta(days=2)),
        },
    )
    copies = service.repurpose(db, draft["id"], ["facebook"])
    waiting = service.list_posts(db, unscheduled=True)
    assert {post["id"] for post in waiting} == {draft["id"], copies[0]["id"]}
    assert [p["body"] for p in service.list_posts(db, query="dated")] == ["Dated"]


def test_the_plan_posts_at_the_persons_morning_not_utc(db):
    """Regression: plan ideas were saved at 09:00 UTC (5 am in New York). Daylight saving is respected too."""
    result = service.content_plan(
        db,
        {
            "description": "A hair studio",
            "start": "2026-10-30",
            "days": 7,
            "save": True,
            "time_zone": "America/New_York",
        },
    )
    assert result["saved"] == 7
    times = {
        p["scheduled_at"][:10]: p["scheduled_at"][11:16] for p in service.list_posts(db)
    }
    assert times["2026-10-30"] == "13:00"  # 9 am EDT
    assert times["2026-11-02"] == "14:00"  # 9 am EST, after the clocks change
    service.content_plan(
        db,
        {
            "description": "x",
            "start": "2026-12-01",
            "days": 7,
            "save": True,
            "time_zone": "Not/AZone",
            "hour": 18,
        },
    )
    assert any(
        p["scheduled_at"] == "2026-12-01T18:00:00Z" for p in service.list_posts(db)
    )
    with pytest.raises(ValueError):
        service.content_plan(db, {"description": "x", "hour": 30})


def test_the_tag_filter_finds_people_beyond_the_row_limit(db):
    """Regression: the tag filter ran after LIMIT, so tagged people further down the list were missed."""
    service.add_subscriber(db, {"email": "vip@x.example", "tags": "vip"})
    for index in range(5):
        service.add_subscriber(db, {"email": f"p{index}@x.example", "tags": "other"})
    rows = service.list_subscribers(db, tag="vip", limit=3)
    assert [row["email"] for row in rows] == ["vip@x.example"]
    assert service.list_subscribers(db, tag="vi", limit=50) == []  # exact tags only
    assert service.all_tags(db) == ["other", "vip"]


def test_an_absurd_amount_is_refused_not_a_crash(db):
    """Regression: a budget of 10^20 cents reached SQLite and failed with a server error."""
    with pytest.raises(ValueError, match="too large"):
        service.create_campaign(db, {"name": "Big", "budget_cents": 10**20})


def test_an_older_database_gets_the_new_columns(tmp_path):
    kit.forget_schema()
    connection = sqlite3.connect(tmp_path / "old.sqlite3")
    connection.row_factory = sqlite3.Row
    kit.ensure_core(connection)
    for statement in store.SCHEMA:  # the first release's tables only
        connection.execute(statement)
    connection.execute(
        "INSERT INTO mk_submissions (id, form_id, created_at, updated_at) VALUES ('a', 'f', 'x', 'x')"
    )
    connection.commit()
    store.ensure(connection)
    row = kit.fetch_one(connection, "SELECT * FROM mk_submissions WHERE id = 'a'")
    assert (
        row["value_cents"] == 0 and row["source"] == "form" and row["paid_at"] is None
    )
    kit.forget_schema()
    store.ensure(connection)  # runs again without trouble
    connection.close()


# ---------------------------------------------------------------------------
# leads that earn
# ---------------------------------------------------------------------------


def test_a_paid_lead_counts_as_revenue_once_and_comes_off_when_undone(db):
    kit.save_business(db, {"name": "Glow", "currency": "USD"})
    lead = _lead(db, value_cents=12000)
    assert kit.revenue_lines(db, app="marketing") == []  # a value alone is not money
    with pytest.raises(ValueError, match="how much"):
        service.mark_paid(db, _lead(db)["id"], {})
    paid = service.mark_paid(db, lead["id"], {})
    assert paid["paid"] and paid["status"] == "won"
    service.mark_paid(
        db, lead["id"], {"value_cents": 15000}
    )  # again: updates, never doubles
    lines = kit.revenue_lines(db, app="marketing")
    assert len(lines) == 1 and lines[0]["amount_cents"] == 15000
    assert kit.revenue(db, app="marketing")["this_month_cents"] == 15000
    service.update_submission(db, lead["id"], {"value_cents": 9000})
    assert kit.revenue(db, app="marketing")["this_month_cents"] == 9000
    service.update_submission(db, lead["id"], {"status": "lost"})
    assert kit.revenue(db, app="marketing")["this_month_cents"] == 0
    assert not service.get_submission(db, lead["id"])["paid"]
    service.mark_paid(db, lead["id"], {})
    service.mark_unpaid(db, lead["id"])
    assert kit.revenue(db, app="marketing")["this_month_cents"] == 0
    service.mark_paid(db, lead["id"], {})
    service.delete_submission(db, lead["id"])
    assert kit.revenue(db, app="marketing")["this_month_cents"] == 0
    overview = service.overview(db)
    assert overview["earned_month_cents"] == 0


def test_campaign_roi_counts_what_its_leads_paid(db):
    campaign = service.create_campaign(
        db, {"name": "Autumn", "spend_cents": 10000, "revenue_cents": 5000}
    )
    lead = _lead(db, campaign_id=campaign["id"])
    service.mark_paid(db, lead["id"], {"value_cents": 25000})
    results = service.get_campaign(db, campaign["id"])["results"]
    assert results["paid_cents"] == 25000 and results["customers"] == 1
    assert results["revenue_total_cents"] == 30000 and results["roi_percent"] == 200
    overview = service.overview(db)
    assert overview["earned_month_cents"] == 25000 and overview["customers_month"] == 1


def test_follow_ups_due_are_listed_until_the_lead_is_settled(db):
    lead = _lead(db, follow_up_on=kit.today())
    later = _lead(
        db, follow_up_on=(kit.utcnow() + timedelta(days=3)).date().isoformat()
    )
    assert [r["id"] for r in service.list_submissions(db, due=True)] == [lead["id"]]
    overview = service.overview(db)
    assert (
        overview["follow_ups_due"] == 1 and overview["follow_ups"][0]["follow_up_due"]
    )
    service.update_submission(db, lead["id"], {"status": "lost"})
    assert service.list_submissions(db, due=True) == []
    assert later["follow_up_due"] is False
    with pytest.raises(ValueError):
        service.update_submission(db, lead["id"], {"follow_up_on": "tomorrow"})
    assert [r["id"] for r in service.list_submissions(db, query="ana lopez")][:1]


def test_a_reply_goes_to_the_outbox_and_marks_the_lead_contacted(db):
    kit.save_business(db, {"name": "Glow"})
    lead = _lead(db)
    template = service.reply_template(db, lead)
    assert template["body"].startswith("Hi Ana,") and "Glow" in template["body"]
    result = service.reply_to_lead(
        db, lead["id"], {"subject": "Hello", "body": "When suits you?"}
    )
    assert result["lead"]["status"] == "contacted" and result["lead"]["contacted_at"]
    message = kit.outbox(db, app="marketing")[0]
    assert message["kind"] == "lead_reply" and message["recipient"] == "ana@example.com"
    assert message["status"] == "pending"  # a person approves it first
    with pytest.raises(ValueError):
        service.reply_to_lead(db, lead["id"], {"subject": "", "body": "x"})
    form = service.create_form(
        db,
        {
            "title": "Call me",
            "fields": {
                "email": {"show": False},
                "phone": {"show": True, "required": True},
            },
        },
    )
    service.submit(db, form["id"], {"name": "Bo", "phone": "555 0101"})
    phone_only = service.list_submissions(db, form_id=form["id"])[0]
    with pytest.raises(ValueError, match="no email"):
        service.reply_to_lead(db, phone_only["id"], {"subject": "a", "body": "b"})


def test_a_payment_request_uses_the_businesss_own_payment_link(db):
    lead = _lead(db)
    with pytest.raises(ValueError, match="payment link"):
        service.payment_request(db, lead["id"], {"value_cents": 5000})
    kit.save_business(
        db,
        {"name": "Glow", "payment_link": "https://pay.example/glow", "currency": "EUR"},
    )
    with pytest.raises(ValueError, match="amount"):
        service.payment_request(db, lead["id"], {})
    result = service.payment_request(
        db, lead["id"], {"value_cents": 5000, "for": "Balayage"}
    )
    assert result["lead"]["status"] == "won" and result["lead"]["value_cents"] == 5000
    assert not result["lead"]["paid"]  # asking is not being paid
    message = next(
        m for m in kit.outbox(db, app="marketing") if m["kind"] == "payment_request"
    )
    assert "https://pay.example/glow" in message["body"] and "€50.00" in message["body"]
    assert "Balayage" in message["subject"]
    assert kit.revenue(db, app="marketing")["this_month_cents"] == 0


def test_the_leads_csv_has_the_value_and_the_source(db):
    lead = _lead(db, value_cents=4550)
    service.mark_paid(db, lead["id"], {})
    rows = service.list_submissions(db)
    data = kit.csv_bytes(rows, service.SUBMISSION_COLUMNS).decode()
    assert "source_label" in data.splitlines()[0] and ",45.50," in data


# ---------------------------------------------------------------------------
# post reminders
# ---------------------------------------------------------------------------


def test_a_due_post_is_reminded_once_by_email(db):
    kit.save_business(db, {"name": "Glow", "email": "owner@glow.example"})
    due = service.create_post(
        db,
        {
            "channel": "instagram",
            "title": "Autumn",
            "body": "Autumn colours",
            "hashtags": "#hair",
            "status": "scheduled",
            "scheduled_at": _stamp(timedelta(minutes=-5)),
        },
    )
    service.create_post(  # in the future: not yet
        db,
        {
            "channel": "x",
            "body": "Later",
            "status": "scheduled",
            "scheduled_at": _stamp(timedelta(hours=3)),
        },
    )
    service.create_post(  # a week late: listed, but no email after so long
        db,
        {
            "channel": "x",
            "body": "Old",
            "status": "scheduled",
            "scheduled_at": _stamp(timedelta(days=-7)),
        },
    )
    assert [p["body"] for p in service.overview(db)["due_posts"]] == [
        "Old",
        "Autumn colours",
    ]
    assert service.remind_due_posts(db) == 1
    assert service.remind_due_posts(db) == 0  # once
    message = kit.outbox(db, app="marketing")[0]
    assert (
        message["kind"] == "post_reminder"
        and message["recipient"] == "owner@glow.example"
    )
    assert "Instagram" in message["subject"] and "#hair" in message["body"]
    service.update_post(db, due["id"], {"scheduled_at": _stamp(timedelta(minutes=-1))})
    assert service.remind_due_posts(db) == 1  # moved: reminded again at its new time
    service.publish_post(db, due["id"], "https://instagram.com/p/1")
    assert [p["body"] for p in service.overview(db)["due_posts"]] == ["Old"]


def test_the_background_pass_runs_the_reminder(db):
    from aira import suite

    suite.load_apps()
    assert "marketing" in background.TASKS
    kit.save_business(db, {"email": "owner@glow.example"})
    service.create_post(
        db,
        {
            "channel": "x",
            "body": "Now",
            "status": "scheduled",
            "scheduled_at": _stamp(timedelta(minutes=-1)),
        },
    )
    result = background.run(db, secret_store=None)
    assert result["tasks"]["marketing"] == 1


def test_without_a_business_email_a_due_post_is_only_listed(db):
    service.create_post(
        db,
        {
            "channel": "x",
            "body": "Now",
            "status": "scheduled",
            "scheduled_at": _stamp(timedelta(minutes=-1)),
        },
    )
    assert service.remind_due_posts(db) == 0
    assert kit.outbox(db, app="marketing") == []
    assert len(service.overview(db)["due_posts"]) == 1


# ---------------------------------------------------------------------------
# the media kit
# ---------------------------------------------------------------------------


def test_the_media_kit_shows_audience_and_packages_never_private_data(db):
    service.add_subscriber(db, {"email": "fan@x.example"})
    service.save_media_kit(
        db,
        {
            "headline": "Home videos",
            "audience": {"instagram": 12000, "youtube": "3400", "x": ""},
            "show_subscribers": True,
            "open": True,
        },
    )
    shown = service.create_package(
        db,
        {"name": "One reel", "price_cents": 50000, "details": "30 seconds", "days": 7},
    )
    hidden = service.create_package(db, {"name": "Old offer", "active": False})
    view = service.public_media_kit(db)
    assert view["reach"] == 12000 + 3400 + 1
    assert [item["label"] for item in view["audience"]] == [
        "Instagram",
        "YouTube",
        "Email list",
    ]
    assert [p["id"] for p in view["packages"]] == [shown["id"]]
    assert "fan@x.example" not in json.dumps(view)
    service.update_package(db, hidden["id"], {"active": True, "price_cents": 100})
    assert len(service.public_media_kit(db)["packages"]) == 2
    service.delete_package(db, hidden["id"])
    with pytest.raises(ValueError):
        service.save_media_kit(db, {"audience": {"instagram": -5}})
    with pytest.raises(ValueError):
        service.create_package(db, {"name": ""})
    service.save_media_kit(db, {"show_subscribers": False})
    assert service.public_media_kit(db)["reach"] == 15400


def test_a_brand_request_becomes_a_lead_worth_the_package(db):
    kit.save_business(db, {"name": "Glow", "email": "owner@glow.example"})
    service.save_media_kit(db, {"open": True})
    reel = service.create_package(db, {"name": "One reel", "price_cents": 50000})
    result = service.sponsorship_request(
        db,
        {
            "company": "Acme Paints",
            "name": "Jo",
            "email": "jo@acme.example",
            "package_id": reel["id"],
            "dates": "December",
        },
    )
    assert result["stored"]
    lead = service.list_submissions(db, source="media_kit")[0]
    assert lead["company"] == "Acme Paints" and lead["package"] == "One reel"
    assert lead["value_cents"] == 50000 and lead["source_label"] == "Sponsorship"
    assert lead["answer"] == "December" and lead["form_title"] == "Media kit"
    service.sponsorship_request(
        db,
        {
            "company": "Beta",
            "name": "Al",
            "email": "al@beta.example",
            "budget": "1,200",
        },
    )
    assert service.list_submissions(db, query="beta")[0]["value_cents"] == 120000
    messages = [
        m for m in kit.outbox(db, app="marketing") if m["kind"] == "sponsorship_request"
    ]
    assert len(messages) == 2 and messages[0]["recipient"] == "owner@glow.example"
    view = service.media_kit_view(db)
    assert view["requests"] == 2 and view["waiting"] == 2
    service.mark_paid(db, lead["id"], {})
    assert service.media_kit_view(db)["earned_cents"] == 50000


def test_sponsorship_requests_are_checked_on_the_server(db):
    service.save_media_kit(db, {"open": True})
    hidden = service.create_package(db, {"name": "Hidden", "active": False})
    base = {
        "company": "Acme",
        "name": "Jo",
        "email": "jo@acme.example",
        "message": "Hi",
    }
    with pytest.raises(ValueError, match="no longer offered"):
        service.sponsorship_request(db, {**base, "package_id": hidden["id"]})
    with pytest.raises(ValueError):
        service.sponsorship_request(db, {**base, "email": "not-an-email"})
    with pytest.raises(ValueError):
        service.sponsorship_request(db, {**base, "budget": "lots"})
    with pytest.raises(ValueError, match="Choose a package"):
        service.sponsorship_request(
            db, {"company": "A", "name": "B", "email": "b@a.example"}
        )
    bot = service.sponsorship_request(db, {**base, "website": "http://spam"})
    assert bot["stored"] is False and service.list_submissions(db) == []
    service.save_media_kit(db, {"open": False})
    with pytest.raises(ValueError, match="closed"):
        service.sponsorship_request(db, base)


# ---------------------------------------------------------------------------
# the single-user build
# ---------------------------------------------------------------------------


def test_a_creator_shares_a_media_kit_and_gets_paid(env, api, local_index, no_model):
    kit.forget_schema()
    api.post(
        "/api/suite/business",
        {
            "name": "Glow",
            "email": "owner@glow.example",
            "payment_link": "https://pay.example/glow",
        },
    )
    api.post(
        f"{M}/media-kit",
        {"headline": "Home videos", "audience": {"youtube": 5000}, "open": True},
    )
    reel = api.post(
        f"{M}/media-kit/packages",
        {"name": "One reel", "price_cents": 40000},
        expect=201,
    )["package"]
    shared = api.post(f"{M}/media-kit/share")["public"]
    status, page = _anonymous(env, "GET", f"/api/public/{shared['token']}")
    assert status == 200 and page["page"]["reach"] == 5000
    assert page["page"]["packages"][0]["price_cents"] == 40000
    status, sent = _anonymous(
        env,
        "POST",
        f"/api/public/{shared['token']}/request",
        {
            "company": "Acme",
            "name": "Jo",
            "email": "jo@acme.example",
            "package_id": reel["id"],
        },
    )
    assert status == 201 and sent["stored"]
    assert (
        _anonymous(
            env, "POST", f"/api/public/{shared['token']}/request", {"company": "Acme"}
        )[0]
        == 400
    )

    leads = api.get(f"{M}/submissions?source=media_kit")
    assert leads["total"] == 1
    lead = leads["submissions"][0]
    assert api.get(f"{M}/submissions/{lead['id']}/reply")["template"]["subject"]
    api.post(
        f"{M}/submissions/{lead['id']}/reply",
        {"subject": "Yes!", "body": "Let us talk."},
        expect=201,
    )
    api.post(f"{M}/submissions/{lead['id']}/payment-request", {}, expect=201)
    paid = api.post(f"{M}/submissions/{lead['id']}/paid", {})["submission"]
    assert paid["paid"] and paid["value_cents"] == 40000
    revenue = api.get("/api/suite/revenue?app=marketing")
    assert revenue["summary"]["this_month_cents"] == 40000
    assert revenue["lines"][0]["label"].startswith("One reel")
    kinds = {m["kind"] for m in api.get("/api/suite/outbox?app=marketing")["messages"]}
    assert {"sponsorship_request", "lead_reply", "payment_request"} <= kinds
    home = api.get("/api/suite/home")
    marketing = next(app for app in home["apps"] if app["id"] == "marketing")
    assert any(
        card["label"] == "Paid by leads this month" and card["value"] == "$400.00"
        for card in marketing["cards"]
    )
    api.ok("DELETE", f"{M}/submissions/{lead['id']}/paid")
    assert (
        api.get("/api/suite/revenue?app=marketing")["summary"]["this_month_cents"] == 0
    )

    api.post(
        f"{M}/posts",
        {"channel": "x", "body": "Draft only", "status": "draft"},
        expect=201,
    )
    assert [p["body"] for p in api.get(f"{M}/posts?unscheduled=1")["posts"]] == [
        "Draft only"
    ]
    api.ok("DELETE", f"{M}/media-kit/share")
    assert _anonymous(env, "GET", f"/api/public/{shared['token']}")[0] == 404


# ---------------------------------------------------------------------------
# the hosted build
# ---------------------------------------------------------------------------


def test_hosted_media_kit_and_paid_leads_follow_roles_and_stay_in_the_organization(
    cloud,
):
    kit.forget_schema()
    org = build_org(cloud)
    owner = sign_in(cloud, org.owner)
    assert owner.call("GET", f"{M}/media-kit")[0] == 402
    cloud.billing.subscribe_app(org.owner, "marketing")
    assert (
        owner.call(
            "POST", f"{M}/media-kit", {"open": True, "audience": {"tiktok": 900}}
        )[0]
        == 200
    )
    status, made = owner.call(
        "POST", f"{M}/media-kit/packages", {"name": "Video", "price_cents": 9900}
    )
    assert status == 201
    status, shared = owner.call("POST", f"{M}/media-kit/share", {})
    token = shared["public"]["token"]
    visitor = Browser(cloud)
    status, page = visitor.call("GET", f"/api/public/{token}")
    assert status == 200 and page["page"]["reach"] == 900
    status, _ = visitor.call(
        "POST",
        f"/api/public/{token}/request",
        {
            "company": "Acme",
            "name": "Jo",
            "email": "jo@acme.example",
            "package_id": made["package"]["id"],
        },
    )
    assert status == 201
    status, listing = owner.call("GET", f"{M}/submissions?source=media_kit")
    lead_id = listing["submissions"][0]["id"]

    auditor = sign_in(cloud, org.auditor)
    assert auditor.call("GET", f"{M}/media-kit")[0] == 200
    assert auditor.call("POST", f"{M}/submissions/{lead_id}/paid", {})[0] == 403
    assert auditor.call("POST", f"{M}/media-kit/packages", {"name": "X"})[0] == 403
    assert sign_in(cloud, org.candidate).call("GET", f"{M}/media-kit")[0] == 403
    coach = sign_in(cloud, org.coach)
    status, paid = coach.call("POST", f"{M}/submissions/{lead_id}/paid", {})
    assert status == 200 and paid["submission"]["value_cents"] == 9900

    other = build_org(cloud, "Other Org", slug_email="other.test")
    cloud.billing.subscribe_app(other.owner, "marketing")
    theirs = sign_in(cloud, other.owner)
    status, data = theirs.call("GET", f"{M}/submissions")
    assert status == 200 and data["submissions"] == []
    assert theirs.call("POST", f"{M}/submissions/{lead_id}/paid", {})[0] == 404
    status, kit_view = theirs.call("GET", f"{M}/media-kit")
    assert kit_view["packages"] == [] and kit_view["reach"] == 0


def test_form_conversion_never_shows_more_than_100_percent(db):
    """Regression: one visit that sent the form twice showed a 200% conversion."""
    assert service.conversion(2, 1) == 100
    assert service.conversion(1, 4) == 25 and service.conversion(3, 0) is None
    form = service.create_form(db, {"title": "Join"})
    db.execute("UPDATE mk_forms SET views = 1 WHERE id = ?", (form["id"],))
    for name in ("Ana", "Bo"):
        service.submit(db, form["id"], {"name": name, "email": f"{name}@x.example"})
    assert service.get_form(db, form["id"])["conversion_percent"] == 100
    assert service.overview(db)["conversion_percent"] == 100


def test_setting_a_paid_leads_value_to_nothing_takes_the_payment_off(db):
    lead = _lead(db)
    service.mark_paid(db, lead["id"], {"value_cents": 5000})
    updated = service.update_submission(db, lead["id"], {"value_cents": 0})
    assert not updated["paid"] and updated["status"] == "won"
    assert kit.revenue(db, app="marketing")["this_month_cents"] == 0
