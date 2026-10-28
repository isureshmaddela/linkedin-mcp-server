"""Marketing (aira/suite/marketing): posts and drafts, campaigns, tracked links, forms, the bio page, subscribers
and newsletters, in unit tests, through the single-user server and through the hosted gateway."""

from __future__ import annotations

import json
import re
import sqlite3
import urllib.error
import urllib.request
from urllib.parse import parse_qs, urlsplit

import pytest
from cloud_support import Browser, build_org, sign_in

import auto_apply_assistant as local
import test_careers
from aira import suite
from aira.suite import kit, public
from aira.suite.marketing import service, store

env = test_careers.env
api = test_careers.api
stub = test_careers.stub

M = "/api/marketing"


@pytest.fixture
def db(tmp_path):
    kit.forget_schema()
    public.set_index(public._MemoryIndex())
    connection = sqlite3.connect(tmp_path / "marketing.sqlite3")
    connection.row_factory = sqlite3.Row
    store.ensure(connection)
    yield connection
    connection.close()


@pytest.fixture
def local_index():
    public.set_index(local._public_link_index)


class Model:
    def __init__(self, reply):
        self.reply = reply

    def model(self, prompt):
        return self.reply


@pytest.fixture(autouse=True)
def no_live_model():
    """A real model may run on this machine: tests never call it."""
    previous = suite.gateway()
    suite.set_gateway(Model(None))
    yield
    suite.set_gateway(previous)


@pytest.fixture
def no_model():
    previous = suite.gateway()
    suite.set_gateway(Model(None))
    yield
    suite.set_gateway(previous)


# ---------------------------------------------------------------------------
# unit: links, drafts, campaigns
# ---------------------------------------------------------------------------


def test_utm_tags_are_added_and_replace_old_ones():
    url = service.utm_url(
        "https://shop.example/p?id=7&utm_source=old#top",
        source="instagram",
        medium="social",
        campaign="spring-sale",
    )
    parts = urlsplit(url)
    query = parse_qs(parts.query)
    assert query == {
        "id": ["7"],
        "utm_source": ["instagram"],
        "utm_medium": ["social"],
        "utm_campaign": ["spring-sale"],
    }
    assert parts.fragment == "top"
    assert service.utm_url("https://a.example/") == "https://a.example/"


def test_slugs_are_unique_and_destinations_must_be_https(db):
    first = service.create_link(
        db, {"destination": "https://glow.example/book", "title": "Book now"}
    )
    second = service.create_link(
        db, {"destination": "https://glow.example/book", "title": "Book now"}
    )
    assert first["slug"] == "book-now" and second["slug"] == "book-now-2"
    assert first["public"]["path"].startswith("/p/")
    with pytest.raises(ValueError, match="https"):
        service.create_link(db, {"destination": "http://glow.example"})
    with pytest.raises(ValueError):
        service.create_link(db, {"destination": "javascript:alert(1)"})
    edited = service.update_link(db, second["id"], {"slug": "Book Now"})
    assert edited["slug"] == "book-now-2"  # still unique


def test_campaign_utms_flow_into_links_and_roi_is_computed(db):
    campaign = service.create_campaign(
        db,
        {
            "name": "Spring Sale",
            "budget_cents": 50000,
            "spend_cents": 20000,
            "revenue_cents": 80000,
            "utm_medium": "paid",
        },
    )
    assert campaign["utm_campaign"] == "spring-sale"
    assert campaign["results"]["roi_percent"] == 300
    link = service.create_link(
        db, {"destination": "https://glow.example", "campaign_id": campaign["id"]}
    )
    assert (
        "utm_campaign=spring-sale" in link["final_url"]
        and "utm_medium=paid" in link["final_url"]
    )
    assert service.roi_percent(0, 0) is None
    assert service.roi_percent(5000, 10000) == -50
    with pytest.raises(ValueError, match="end before"):
        service.create_campaign(
            db, {"name": "X", "starts_on": "2026-10-10", "ends_on": "2026-10-01"}
        )
    with pytest.raises(ValueError):
        service.create_campaign(db, {"name": "X", "spend_cents": -5})
    assert (
        service.create_campaign(db, {"name": "Text amounts", "budget_cents": "120.50"})[
            "budget_cents"
        ]
        == 12050
    )


def test_clicks_are_counted_per_day(db):
    link = service.create_link(db, {"destination": "https://glow.example"})
    for _ in range(3):
        service.record_click(db, "link", link["id"])
    days = service.clicks_by_day(db, "link", link["id"], 7)
    assert len(days) == 7 and days[-1]["clicks"] == 3
    assert service.get_link(db, link["id"])["clicks"] == 3


@pytest.mark.parametrize("channel", list(service.CHANNELS))
def test_templates_respect_each_channel_limit(db, no_model, channel):
    draft = service.draft_post(
        db,
        {
            "channel": channel,
            "topic": ("spring colour trends " * 14).strip(),
            "goal": "leads",
        },
    )
    assert draft["source"] == "template"
    assert len(draft["text"]) <= service.CHANNELS[channel]["limit"]
    assert len(draft["hashtags"].split()) <= service.CHANNELS[channel]["hashtags"]


def test_model_drafts_are_cut_to_the_channel(db):
    previous = suite.gateway()
    try:
        suite.set_gateway(
            Model(
                json.dumps(
                    {"title": "Long", "text": "word " * 200, "hashtags": "#a #b #c #d"}
                )
            )
        )
        draft = service.draft_post(db, {"channel": "x", "topic": "hair"})
        assert (
            draft["source"] == "model"
            and len(draft["text"]) <= 280
            and draft["text"].endswith("…")
        )
        assert len(draft["hashtags"].split()) <= 2
    finally:
        suite.set_gateway(previous)


def test_posts_validate_length_schedule_and_publish(db, no_model):
    with pytest.raises(ValueError, match="280"):
        service.create_post(db, {"channel": "x", "body": "a" * 281})
    with pytest.raises(ValueError, match="when to post"):
        service.create_post(
            db, {"channel": "instagram", "body": "hi", "status": "scheduled"}
        )
    post = service.create_post(
        db,
        {
            "channel": "instagram",
            "body": "New colours",
            "hashtags": "hair, #Colour hair",
            "scheduled_at": "2026-11-02T10:00",
            "status": "scheduled",
        },
    )
    assert (
        post["hashtags"] == "#hair #Colour"
        and post["scheduled_at"] == "2026-11-02T10:00:00Z"
    )
    published = service.publish_post(db, post["id"], "https://instagram.com/p/abc")
    assert published["status"] == "published" and published["published_at"]
    created = service.repurpose(db, post["id"], ["x", "linkedin", "instagram"])
    assert [p["channel"] for p in created] == ["x", "linkedin"]
    assert all(p["status"] == "draft" and p["source"] == "repurpose" for p in created)


def test_the_30_day_plan_rotates_channels_and_saves_ideas(db, no_model):
    plan = service.content_plan(
        db,
        {
            "description": "A hair salon in Austin",
            "goals": "bookings",
            "channels": ["instagram", "tiktok"],
            "start": "2026-11-01",
        },
    )
    assert len(plan["ideas"]) == 30 and plan["saved"] == 0
    assert (
        plan["ideas"][0]["day"] == "2026-11-01"
        and plan["ideas"][29]["day"] == "2026-11-30"
    )
    assert {idea["channel"] for idea in plan["ideas"]} == {"instagram", "tiktok"}
    saved = service.content_plan(
        db,
        {
            "description": "A hair salon",
            "channels": ["x"],
            "start": "2026-11-01",
            "days": 7,
            "save": True,
        },
    )
    assert saved["saved"] == 7
    assert len(service.list_posts(db, status="idea")) == 7
    with pytest.raises(ValueError, match="Describe"):
        service.content_plan(db, {"description": ""})


# ---------------------------------------------------------------------------
# unit: forms, subscribers, newsletters
# ---------------------------------------------------------------------------


def _form(db, **extra):
    return service.create_form(
        db, {"title": "Free consultation", "tag": "consult", **extra}
    )


def test_forms_validate_against_their_own_fields(db):
    form = _form(
        db,
        fields={
            "name": {"show": True, "required": True},
            "email": {"show": True, "required": True},
            "phone": {"show": True},
            "message": {"show": True},
        },
    )
    with pytest.raises(ValueError, match="Enter your name"):
        service.submit(db, form["id"], {"email": "a@b.example"})
    with pytest.raises(ValueError, match="not an email"):
        service.submit(db, form["id"], {"name": "Ana", "email": "nope"})
    result = service.submit(
        db,
        form["id"],
        {
            "name": "Ana",
            "email": "ana@b.example",
            "message": "Hi",
            "company": "ignored",
        },
    )
    assert result["stored"] is True
    row = service.list_submissions(db)[0]
    assert (
        row["company"] == ""
        and row["status"] == "new"
        and row["form_title"] == "Free consultation"
    )
    # A bot that fills the hidden field is thanked and nothing is stored.
    assert (
        service.submit(
            db, form["id"], {"name": "Bot", "email": "b@b.example", "website": "spam"}
        )["stored"]
        is False
    )
    assert len(service.list_submissions(db)) == 1
    with pytest.raises(ValueError, match="email or a phone"):
        _form(db, fields={"email": {"show": False}, "phone": {"show": False}})
    service.update_form(db, form["id"], {"status": "closed"})
    with pytest.raises(ValueError, match="closed"):
        service.submit(db, form["id"], {"name": "Ana", "email": "ana@b.example"})


def test_consent_adds_a_subscriber_and_the_owner_is_notified(db):
    kit.save_business(db, {"name": "Glow", "email": "owner@glow.example"})
    form = _form(db)
    service.submit(db, form["id"], {"name": "Ana", "email": "ana@b.example"})
    assert service.list_subscribers(db) == []
    service.submit(
        db, form["id"], {"name": "Bo", "email": "bo@b.example", "consent": True}
    )
    subscribers = service.list_subscribers(db)
    assert [s["email"] for s in subscribers] == ["bo@b.example"]
    assert subscribers[0]["tags"] == ["consult"] and subscribers[0]["consent_at"]
    notes = kit.outbox(db, app="marketing")
    assert (
        len(notes) == 2
        and notes[0]["recipient"] == "owner@glow.example"
        and notes[0]["kind"] == "form_submission"
    )
    assert service.get_form(db, form["id"])["submissions"] == 2


def test_unsubscribed_addresses_stay_unsubscribed(db):
    subscriber = service.add_subscriber(db, {"email": "c@b.example", "name": "Cy"})
    service.unsubscribe(db, subscriber["id"])
    # Import and manual edits never re-subscribe; only the person's own opt-in on a form does.
    assert service.import_subscribers(
        db, "email,name\nc@b.example,Cy\nd@b.example,Di\nnot-an-email\n"
    ) == {"added": 1, "updated": 0, "skipped": 1, "invalid": 1}
    with pytest.raises(ValueError, match="unsubscribed"):
        service.update_subscriber(db, subscriber["id"], {"status": "subscribed"})
    form = _form(db)
    service.submit(
        db, form["id"], {"name": "Cy", "email": "c@b.example", "consent": "yes"}
    )
    assert service.list_subscribers(db, query="c@b")[0]["status"] == "subscribed"


def test_newsletters_go_only_to_subscribed_addresses_with_their_own_unsubscribe_link(
    db,
):
    for address, tags in (
        ("a@x.example", "vip"),
        ("b@x.example", "vip"),
        ("c@x.example", ""),
    ):
        service.add_subscriber(db, {"email": address, "tags": tags})
    gone = service.add_subscriber(db, {"email": "gone@x.example", "tags": "vip"})
    service.unsubscribe(db, gone["id"])
    letter = service.create_newsletter(
        db, {"subject": "November news", "body": "Hello", "audience_tag": "VIP"}
    )
    assert letter["remaining"] == 2
    with pytest.raises(ValueError, match="Confirm"):
        service.queue_newsletter(
            db,
            letter["id"],
            confirm=False,
            source={"base_url": "http://127.0.0.1:8765"},
        )
    result = service.queue_newsletter(
        db, letter["id"], confirm=True, source={"base_url": "http://127.0.0.1:8765"}
    )
    assert result["queued"] == 2 and result["remaining"] == 0
    messages = kit.outbox(db, app="marketing")
    assert sorted(m["recipient"] for m in messages) == ["a@x.example", "b@x.example"]
    bodies = [m["body"] for m in messages]
    # The request's address, unless the hosted build configured its public address.
    origin = suite.public_origin() or "http://127.0.0.1:8765"
    assert all(f"Unsubscribe: {origin}/p/" in body for body in bodies)
    assert bodies[0].split("Unsubscribe: ")[1] != bodies[1].split("Unsubscribe: ")[1]
    with pytest.raises(ValueError, match="already has it"):
        service.queue_newsletter(
            db, letter["id"], confirm=True, source={"base_url": "http://x"}
        )


def test_newsletters_are_capped_per_send_and_continue(db):
    for index in range(7):
        service.add_subscriber(db, {"email": f"s{index}@x.example"})
    letter = service.create_newsletter(db, {"subject": "Hi", "body": "Body"})
    first = service.queue_newsletter(
        db, letter["id"], confirm=True, source={"base_url": "http://x"}, cap=5
    )
    assert (
        first["queued"] == 5
        and first["remaining"] == 2
        and first["newsletter"]["status"] == "partial"
    )
    second = service.queue_newsletter(
        db, letter["id"], confirm=True, source={"base_url": "http://x"}, cap=5
    )
    assert second["queued"] == 2 and second["newsletter"]["status"] == "queued"
    assert len(kit.outbox(db, app="marketing")) == 7


def test_bio_links_order_and_overview_numbers(db):
    first = service.add_bio_link(
        db, {"label": "Book", "url": "https://glow.example/book", "highlight": True}
    )
    second = service.add_bio_link(
        db, {"label": "Shop", "url": "https://glow.example/shop"}
    )
    assert [link["id"] for link in service.move_bio_link(db, second["id"], "up")] == [
        second["id"],
        first["id"],
    ]
    with pytest.raises(ValueError):
        service.save_bio(db, {"avatar_url": "http://insecure.example/a.png"})
    data = service.overview(db)
    assert data["subscribers"] == 0 and data["conversion_percent"] is None
    steps = {step["id"]: step["done"] for step in service.checklist(db)}
    assert steps == {
        "profile": False,
        "post": False,
        "link": False,
        "form": False,
        "subscriber": False,
        "newsletter": False,
    }


# ---------------------------------------------------------------------------
# the single-user build
# ---------------------------------------------------------------------------


def _anonymous(env, method, path, body=None):
    data = json.dumps(body).encode() if body is not None else None
    request = urllib.request.Request(env.base + path, data=data, method=method)
    request.add_header("Content-Type", "application/json")
    request.add_header("X-Requested-With", "aira")
    request.add_header("Origin", env.base)
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            return response.status, json.loads(response.read() or b"{}")
    except urllib.error.HTTPError as error:
        return error.code, json.loads(error.read() or b"{}")


def test_a_business_captures_leads_tracks_clicks_and_sends_a_newsletter(
    env, api, local_index, no_model
):
    kit.forget_schema()
    api.post(
        "/api/suite/business", {"name": "Glow Studio", "email": "owner@glow.example"}
    )
    campaign = api.post(
        f"{M}/campaigns", {"name": "Autumn", "spend_cents": 10000}, expect=201
    )["campaign"]
    form = api.post(
        f"{M}/forms",
        {"title": "Book a consult", "campaign_id": campaign["id"], "tag": "consult"},
        expect=201,
    )["form"]
    shared = api.post(f"{M}/forms/{form['id']}/link")["public"]
    token = shared["token"]

    status, page = _anonymous(env, "GET", f"/api/public/{token}")
    assert (
        status == 200
        and page["page"]["title"] == "Book a consult"
        and "consent_text" in page["page"]
    )
    assert "submissions" not in json.dumps(page["page"])
    status, data = _anonymous(
        env,
        "POST",
        f"/api/public/{token}/submit",
        {"name": "Ana", "email": "ana@example.com", "consent": True},
    )
    assert status == 201 and data["stored"] is True
    status, data = _anonymous(
        env,
        "POST",
        f"/api/public/{token}/submit",
        {"name": "", "email": "x@example.com"},
    )
    assert status == 400

    subscribers = api.get(f"{M}/subscribers")["subscribers"]
    assert [s["email"] for s in subscribers] == ["ana@example.com"]
    leads = api.get(f"{M}/submissions")["submissions"]
    assert leads[0]["name"] == "Ana" and leads[0]["campaign_id"] == campaign["id"]
    api.ok("PATCH", f"{M}/submissions/{leads[0]['id']}", {"status": "won"})

    link = api.post(
        f"{M}/links",
        {"destination": "https://glow.example/book", "campaign_id": campaign["id"]},
        expect=201,
    )["link"]
    status, go = _anonymous(env, "GET", f"/api/public/{link['public']['token']}")
    assert (
        status == 200
        and go["page"]["url"].startswith("https://glow.example/book?")
        and "utm_campaign=autumn" in go["page"]["url"]
    )
    assert api.get(f"{M}/links/{link['id']}/clicks")["days"][-1]["clicks"] == 1

    bio_link = api.post(
        f"{M}/bio/links",
        {"label": "Book", "url": "https://glow.example/book"},
        expect=201,
    )["link"]
    api.post(f"{M}/bio", {"title": "Glow", "featured_form_id": form["id"]})
    bio = api.post(f"{M}/bio/share")["public"]
    status, page = _anonymous(env, "GET", f"/api/public/{bio['token']}")
    assert status == 200 and page["page"]["featured"]["path"] == shared["path"]
    status, clicked = _anonymous(
        env, "POST", f"/api/public/{bio['token']}/click", {"link_id": bio_link["id"]}
    )
    assert status == 200 and clicked["url"] == "https://glow.example/book"
    assert (
        _anonymous(
            env, "POST", f"/api/public/{bio['token']}/click", {"link_id": "0" * 32}
        )[0]
        == 404
    )

    api.post(f"{M}/subscribers", {"email": "bo@example.com"}, expect=201)
    letter = api.post(
        f"{M}/newsletters", {"subject": "News", "body": "Hello"}, expect=201
    )["newsletter"]
    queued = api.post(f"{M}/newsletters/{letter['id']}/queue", {"confirm": True})
    assert queued["queued"] == 2
    messages = api.get("/api/suite/outbox?app=marketing")["messages"]
    newsletter_bodies = [m["body"] for m in messages if m["kind"] == "newsletter"]
    token = re.search(
        r"Unsubscribe: \S+/p/([A-Za-z0-9_-]{40,64})", newsletter_bodies[0]
    ).group(1)
    status, page = _anonymous(env, "GET", f"/api/public/{token}")
    assert (
        status == 200
        and page["page"]["subscribed"] is True
        and "*" in page["page"]["email"]
    )
    assert _anonymous(env, "POST", f"/api/public/{token}/unsubscribe", {})[1] == {
        "subscribed": False
    }
    assert (
        api.get(f"{M}/subscribers?status=unsubscribed")["counts"]["unsubscribed"] == 1
    )

    overview = api.get(f"{M}/overview")
    assert (
        overview["subscribers"] == 1
        and overview["form_submissions"] == 1
        and overview["clicks_month"] == 2
    )
    assert overview["best_campaign"]["name"] == "Autumn"
    home = api.get("/api/suite/home")
    marketing = next(app for app in home["apps"] if app["id"] == "marketing")
    assert marketing["installed"] and marketing["cards"][0]["label"] == "Subscribers"
    assert any(row["app"] == "marketing" and row["public"] for row in home["activity"])
    with api.opener.open(
        urllib.request.Request(
            env.base + f"{M}/submissions?format=csv",
            headers={"X-Requested-With": "aira"},
        ),
        timeout=30,
    ) as response:
        csv_text = response.read().decode()
    assert (
        csv_text.startswith("created_at,source_label,form_title,name")
        and "ana@example.com" in csv_text
    )


def test_marketing_routes_need_a_session(env):
    assert _anonymous(env, "GET", f"{M}/overview")[0] == 401


# ---------------------------------------------------------------------------
# the hosted build
# ---------------------------------------------------------------------------


def test_hosted_marketing_needs_the_app_and_the_right_role(cloud):
    kit.forget_schema()
    org = build_org(cloud)
    owner = sign_in(cloud, org.owner)
    status, data = owner.call("GET", f"{M}/overview")
    assert status == 402 and data["code"] == "app_required"
    cloud.billing.subscribe_app(org.owner, "marketing")
    assert owner.call("GET", f"{M}/overview")[0] == 200
    for who in ("admin", "coach"):
        browser = sign_in(cloud, getattr(org, who))
        status, _ = browser.call(
            "POST", f"{M}/subscribers", {"email": f"{who}@fans.example"}
        )
        assert status == 201, who
    auditor = sign_in(cloud, org.auditor)
    assert auditor.call("GET", f"{M}/subscribers")[0] == 200
    assert (
        auditor.call("POST", f"{M}/subscribers", {"email": "z@fans.example"})[0] == 403
    )
    assert sign_in(cloud, org.candidate).call("GET", f"{M}/overview")[0] == 403

    status, created = owner.call("POST", f"{M}/forms", {"title": "Join the list"})
    assert status == 201
    status, shared = owner.call("POST", f"{M}/forms/{created['form']['id']}/link", {})
    token = shared["public"]["token"]
    visitor = Browser(cloud)
    status, page = visitor.call("GET", f"/api/public/{token}")
    assert status == 200 and page["page"]["title"] == "Join the list"
    status, sent = visitor.call(
        "POST",
        f"/api/public/{token}/submit",
        {"name": "Ana", "email": "ana@fans.example", "consent": True},
    )
    assert status == 201, sent
    status, listing = owner.call("GET", f"{M}/subscribers")
    assert {s["email"] for s in listing["subscribers"]} == {
        "admin@fans.example",
        "coach@fans.example",
        "ana@fans.example",
    }

    other = build_org(cloud, "Other Org", slug_email="other.test")
    cloud.billing.subscribe_app(other.owner, "marketing")
    status, theirs = sign_in(cloud, other.owner).call("GET", f"{M}/subscribers")
    assert status == 200 and theirs["subscribers"] == []
