"""Courses & Events (the ``experiences`` app): regressions for the problems an independent tester found.

* hosted public links (course, event, catalog, partner, student pages) keep working on a server started the normal
  way, where the gateway imports the single-user module after the hosted index is installed
* a partner code the business types by hand must be a real, switched-on partner (and not the buyer)
* a referral keeps the commission rate in force when the sale was made
* a partner whose only referrals were refunded is switched off, and the drawer knows it
* a refunded student's review no longer counts in the rating
* the catalog's event price comes from tickets on sale now (and says a free ticket sits next to paid ones)
* a partner buying with their own code earns nothing
* the partners download has money as 2000.00 and Yes/No
* a child's household profile cannot open the business's earnings or record a payout
* public pages carry a per-business scope for the saved partner code
"""

from __future__ import annotations

import csv
import io
import json
import os
import subprocess
import sys
import urllib.error
import urllib.request
from datetime import timedelta
from pathlib import Path

import pytest
from cloud_support import Browser, build_org, sign_in

import auto_apply_assistant as local
import test_careers
from aira import suite
from aira.suite import kit, public
from aira.suite.academy import catalog, household, partners
from aira.suite.academy import service as academy
from aira.suite.academy import store as academy_store
from aira.suite.events import service as events
from aira.suite.events import store as events_store

env = test_careers.env
api = test_careers.api
stub = test_careers.stub

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(autouse=True)
def no_model():
    previous = suite.gateway()
    suite.set_gateway(suite._NoGateway())
    yield
    suite.set_gateway(previous)


@pytest.fixture
def db(tmp_path):
    import sqlite3

    kit.forget_schema()
    public.set_index(public._MemoryIndex())
    connection = sqlite3.connect(tmp_path / "experiences.sqlite3", timeout=10)
    connection.row_factory = sqlite3.Row
    academy_store.ensure(connection)
    events_store.ensure(connection)
    yield connection
    connection.close()


def later(**delta) -> str:
    return kit.iso(kit.utcnow() + timedelta(**delta))


def paid_course(db, *, title="Python Basics", price=10000):
    course = academy.create_course(db, {"title": title, "price_cents": price})
    module = academy.add_module(db, course["id"], "Start")
    lesson = academy.add_lesson(db, module["id"], {"title": "Hello", "body": "Print."})
    academy.set_status(db, course["id"], "published")
    return academy_store.course(db, course["id"]), lesson


def published_event(db, ticket_types, *, title="Jazz Night"):
    event = events.create_event(db, {"title": title, "starts_at": later(days=10)})
    made = [events.add_ticket_type(db, event["id"], t) for t in ticket_types]
    events.set_status(db, event["id"], "published")
    return events_store.get_event(db, event["id"]), made


# ---------------------------------------------------------------------------
# hosted public links (the high one)
# ---------------------------------------------------------------------------


def test_set_default_index_keeps_an_index_installed_over_the_old_default():
    installed = public._MemoryIndex()
    public.set_index(installed)
    replacement = public._MemoryIndex()
    public.set_default_index(replacement)
    try:
        assert public._index is installed, "the hosted index stays current"
        assert public._default_index is replacement
        public.reset_index()
        assert public._index is replacement
    finally:
        public.set_default_index(local._public_link_index)
        public.reset_index()


def test_a_hosted_server_started_like_cmd_serve_keeps_the_organization_index(
    tmp_path,
):
    """The exact import order of ``python -m aira_cloud serve``: CloudApp, then Gateway (which imports the
    single-user module for the first time). Run in a fresh interpreter, where that import really happens."""
    script = (
        "from pathlib import Path\n"
        "from aira_cloud.app import CloudApp\n"
        "from aira_cloud.config import CloudConfig\n"
        "from aira.suite import public\n"
        f"app = CloudApp(CloudConfig(data_root=Path(r'{tmp_path / 'cloud'}'), base_url='http://127.0.0.1:8781', master_key=''))\n"
        "from aira_cloud.gateway import Gateway\n"
        "Gateway(app)\n"
        "print(type(public._index).__name__)\n"
    )
    environment = {**os.environ, "SERPAPI_API_KEY": "", "AIRA_SUITE_STRICT": "1"}
    done = subprocess.run(
        [sys.executable, "-c", script],
        cwd=ROOT,
        env=environment,
        capture_output=True,
        text=True,
        timeout=300,
        check=False,
    )
    assert done.returncode == 0, done.stderr[-2000:]
    assert done.stdout.strip().splitlines()[-1] == "HostedPublicIndex"


def test_hosted_pages_open_after_the_single_user_module_sets_its_default(cloud):
    kit.forget_schema()
    # what importing auto_apply_assistant after hooks.install does on a real server
    public.set_default_index(local._public_link_index)
    assert public._index is cloud.public_index
    org = build_org(cloud)
    owner = sign_in(cloud, org.owner)
    cloud.billing.subscribe_app(org.owner, "experiences")
    course = owner.call(
        "POST", "/api/academy/courses", {"title": "Yoga", "price_cents": 0}
    )[1]["course"]
    module = owner.call(
        "POST", f"/api/academy/courses/{course['id']}/modules", {"title": "M"}
    )[1]["module"]
    owner.call(
        "POST",
        f"/api/academy/modules/{module['id']}/lessons",
        {"title": "L", "body": "b"},
    )
    owner.call(
        "POST", f"/api/academy/courses/{course['id']}/status", {"status": "published"}
    )
    link = owner.call("GET", f"/api/academy/courses/{course['id']}")[1]["link"]
    assert cloud.public_index.lookup(link["token"])["org_id"] == org.org_id
    visitor = Browser(cloud)
    status, page = visitor.call("GET", f"/api/public/{link['token']}")
    assert status == 200 and page["page"]["course"]["title"] == "Yoga"
    status, enrolled = visitor.call(
        "POST",
        f"/api/public/{link['token']}/enrol",
        {"name": "Ana", "email": "ana@example.com"},
    )
    assert status == 201
    student = enrolled["student_path"].removeprefix("/p/")
    assert visitor.call("GET", f"/api/public/{student}")[0] == 200
    catalog_link = owner.call("POST", "/api/academy/catalog/link", {})[1]["link"]
    assert visitor.call("GET", f"/api/public/{catalog_link['token']}")[0] == 200


# ---------------------------------------------------------------------------
# partners
# ---------------------------------------------------------------------------


def test_a_partner_code_typed_by_the_business_must_be_a_real_partner(db):
    course, _ = paid_course(db)
    priya = partners.create_partner(
        db, {"name": "Priya", "code": "PRIYA", "email": "priya@p.test"}
    )
    with pytest.raises(ValueError, match="No partner has the code PRIYAA"):
        academy.enrol(
            db,
            course["id"],
            {"name": "Typo Ref", "email": "t@example.com", "ref": "PRIYAA"},
            source="manual",
        )
    assert academy_store.seats_taken(db, course["id"]) == 0, "nothing was enrolled"
    with pytest.raises(ValueError, match="own email"):
        academy.enrol(
            db,
            course["id"],
            {"name": "Priya", "email": "PRIYA@p.test", "ref": "priya"},
            source="manual",
        )
    enrolled = academy.enrol(
        db,
        course["id"],
        {"name": "Right Ref", "email": "r@example.com", "ref": "priya"},
        source="manual",
    )
    assert enrolled["enrolment"]["status"] == "pending_payment"
    assert len(partners.referrals(db, priya["id"])) == 1

    event, (seat,) = published_event(
        db, [{"name": "Seat", "price_cents": 1500, "quantity": 10}]
    )
    items = [{"ticket_type_id": seat["id"], "quantity": 1}]
    with pytest.raises(ValueError, match="PRIYAX"):
        events.place_order(
            db,
            event["id"],
            {"name": "Door", "items": items, "ref": "PRIYAX"},
            source="manual",
        )
    assert events_store.sold_by_type(db, event["id"]) == {}
    partners.update_partner(db, priya["id"], {"active": False})
    with pytest.raises(ValueError, match="switched off"):
        events.place_order(
            db,
            event["id"],
            {"name": "Door", "items": items, "ref": "PRIYA"},
            source="manual",
        )
    # an empty code, complimentary tickets and a customer's own link are never blocked
    events.place_order(
        db, event["id"], {"name": "Door", "items": items}, source="manual"
    )
    events.place_order(
        db,
        event["id"],
        {"name": "Guest", "items": items, "ref": "NOPE"},
        source="manual",
        comp=True,
    )
    academy.enrol(
        db, course["id"], {"name": "Visitor", "email": "v@example.com", "ref": "PRIYAA"}
    )


def test_a_referral_keeps_the_rate_in_force_when_the_sale_was_made(db):
    course, _ = paid_course(db)
    priya = partners.create_partner(
        db, {"name": "Priya", "code": "PRIYA", "commission_percent": 10}
    )
    enrolled = academy.enrol(
        db, course["id"], {"name": "Ana", "email": "ana@example.com", "ref": "PRIYA"}
    )["enrolment"]
    (pending,) = partners.referrals(db, priya["id"])
    assert pending["commission_cents"] == 1000 and pending["commission_percent"] == 10
    partners.update_partner(db, priya["id"], {"commission_percent": 50})
    academy.mark_paid(db, enrolled["id"])
    (earned,) = partners.referrals(db, priya["id"])
    assert earned["status"] == "earned" and earned["commission_cents"] == 1000
    # a new sale uses the new rate
    other = academy.enrol(
        db, course["id"], {"name": "Bo", "email": "bo@example.com", "ref": "PRIYA"}
    )["enrolment"]
    academy.mark_paid(db, other["id"])
    assert sorted(
        r["commission_cents"] for r in partners.referrals(db, priya["id"])
    ) == [
        1000,
        5000,
    ]


def test_a_referral_from_before_the_rate_was_kept_uses_the_partner_rate(db):
    course, _ = paid_course(db)
    priya = partners.create_partner(
        db, {"name": "Priya", "code": "PRIYA", "commission_percent": 20}
    )
    enrolled = academy.enrol(
        db, course["id"], {"name": "Ana", "email": "ana@example.com", "ref": "PRIYA"}
    )["enrolment"]
    db.execute("UPDATE xp_referrals SET commission_percent = NULL")  # an older row
    db.commit()
    academy.mark_paid(db, enrolled["id"])
    (earned,) = partners.referrals(db, priya["id"])
    assert earned["commission_cents"] == 2000


def test_a_partner_whose_only_referrals_were_refunded_is_switched_off(db):
    course, _ = paid_course(db)
    voidy = partners.create_partner(db, {"name": "Voidy", "code": "VOIDY"})
    enrolled = academy.enrol(
        db, course["id"], {"name": "Ana", "email": "ana@example.com", "ref": "VOIDY"}
    )["enrolment"]
    academy.refund(db, enrolled["id"])
    shown = partners.detail(db, voidy["id"])["partner"]
    assert shown["referrals"] == 0 and shown["history"] == 1
    assert partners.delete_partner(db, voidy["id"]) == {
        "deleted": False,
        "deactivated": True,
    }
    fresh = partners.create_partner(db, {"name": "Fresh", "code": "FRESH"})
    assert partners.detail(db, fresh["id"])["partner"]["history"] == 0
    assert partners.delete_partner(db, fresh["id"])["deleted"] is True


def test_a_partner_buying_with_their_own_code_earns_nothing(db):
    course, _ = paid_course(db)
    priya = partners.create_partner(
        db,
        {
            "name": "Priya",
            "code": "PRIYA-1",
            "email": "priya@p.test",
            "commission_percent": 20,
        },
    )
    academy.enrol(
        db,
        course["id"],
        {"name": "Self Ref", "email": "Priya@P.test", "ref": "PRIYA-1"},
    )
    assert partners.referrals(db, priya["id"]) == []
    lines = [row["title"] for row in kit.activity(db, app="academy", limit=20)]
    assert any("used their own partner code" in line for line in lines), lines
    event, (seat,) = published_event(
        db, [{"name": "Seat", "price_cents": 1500, "quantity": 10}]
    )
    events.place_order(
        db,
        event["id"],
        {
            "name": "Priya",
            "email": "priya@p.test",
            "items": [{"ticket_type_id": seat["id"], "quantity": 1}],
            "ref": "PRIYA-1",
        },
    )
    assert partners.referrals(db, priya["id"]) == []


# ---------------------------------------------------------------------------
# reviews, catalog, scope
# ---------------------------------------------------------------------------


def test_a_refunded_students_review_no_longer_counts(db):
    course, lesson = paid_course(db, price=0)
    ana = academy.enrol(db, course["id"], {"name": "Ana", "email": "ana@example.com"})[
        "enrolment"
    ]
    academy.complete_lesson(db, ana["id"], lesson["id"])
    academy.submit_review(db, ana["id"], {"rating": 5, "comment": "Great"})
    assert academy.course_page(db, course["id"])["rating"]["count"] == 1
    academy.refund(db, ana["id"])
    page = academy.course_page(db, course["id"])
    assert page["rating"] == {"average": None, "count": 0} and page["reviews"] == []
    assert academy_store.rating(db, course["id"])["count"] == 0
    (review,) = academy.reviews(db)
    assert review["status"] == "hidden", (
        "the business can still see it and show it again"
    )


def test_the_catalog_prices_events_from_tickets_on_sale_now(db):
    soon, _ = published_event(
        db,
        [
            {
                "name": "General",
                "price_cents": 3000,
                "quantity": 10,
                "sales_start": later(days=5),
            }
        ],
        title="Future Sale",
    )
    mixed, _ = published_event(
        db,
        [
            {"name": "Seat", "price_cents": 100000, "quantity": 10},
            {"name": "Free", "price_cents": 0, "quantity": 10},
            {
                "name": "Early bird",
                "price_cents": 500,
                "quantity": 10,
                "sales_start": later(days=2),
            },
        ],
        title="Mixed",
    )
    free, _ = published_event(
        db, [{"name": "Free", "price_cents": 0, "quantity": 10}], title="All Free"
    )
    for event in (soon, mixed, free):
        public.create_link(db, "events", "event", event["id"])
    cards = {e["title"]: e for e in catalog.page(db)["events"]}
    assert cards["Future Sale"]["from_cents"] is None
    assert cards["Future Sale"]["sales"] == "soon"
    assert (
        cards["Mixed"]["from_cents"] == 100000 and cards["Mixed"]["free_option"] is True
    )
    assert (
        cards["All Free"]["from_cents"] == 0
        and cards["All Free"]["free_option"] is False
    )
    js = (ROOT / "auto_apply_assistant" / "public-academy.js").read_text(
        encoding="utf-8"
    )
    assert "Tickets soon" in js and "free tickets too" in js


def test_public_pages_carry_a_scope_for_the_saved_partner_code(db):
    course, _ = paid_course(db)
    event, _ = published_event(
        db, [{"name": "Seat", "price_cents": 100, "quantity": 5}]
    )
    public.create_link(db, "events", "event", event["id"])
    catalog_link = catalog.create(db)
    scope = partners.ref_scope(db)
    assert len(scope) == 16 and partners.ref_scope(db) == scope
    for link in (
        public.link_for(db, "academy", "course", course["id"]),
        public.link_for(db, "events", "event", event["id"]),
        catalog_link,
    ):
        assert public.view(db, link["token"])["page"]["ref_scope"] == scope
    for name in ("public-academy.js", "public-events.js"):
        js = (ROOT / "auto_apply_assistant" / name).read_text(encoding="utf-8")
        assert 'sessionStorage.setItem("aira.ref"' not in js
        assert "aira.ref.${scope}" in js


# ---------------------------------------------------------------------------
# the single-user server: forms, the download, a child's profile
# ---------------------------------------------------------------------------


def call(api, method, path, body=None, headers=None):
    data = json.dumps(body).encode() if body is not None else None
    request = urllib.request.Request(api.base + path, data=data, method=method)
    request.add_header("Content-Type", "application/json")
    request.add_header("X-Requested-With", "aira")
    request.add_header("Origin", api.base)
    for key, value in (headers or {}).items():
        request.add_header(key, value)
    try:
        with api.opener.open(request, timeout=60) as response:
            raw = response.read()
            status = response.status
    except urllib.error.HTTPError as error:
        raw, status = error.read(), error.code
    try:
        return status, json.loads(raw)
    except ValueError:
        return status, raw


def test_the_local_build_refuses_typos_formats_the_download_and_keeps_children_out(
    env, api
):
    kit.forget_schema()
    public.set_index(local._public_link_index)
    api.post("/api/suite/business", {"name": "Blue Room School"})
    course = api.post(
        "/api/academy/courses",
        {"title": "Guitar 101", "price_cents": 200000},
        expect=201,
    )["course"]
    partner = api.post(
        "/api/academy/partners",
        {"name": "Zed", "code": "ZEDS2A", "commission_percent": 10},
        expect=201,
    )["partner"]
    status, refused = api.call(
        "POST",
        "/api/academy/students",
        {
            "course_id": course["id"],
            "name": "Typo",
            "email": "t@example.com",
            "ref": "ZEDS2B",
        },
    )
    assert status == 400 and "No partner has the code ZEDS2B" in refused["error"]
    api.post(
        "/api/academy/students",
        {
            "course_id": course["id"],
            "name": "Ana",
            "email": "ana@example.com",
            "ref": "zeds2a",
            "paid": True,
        },
        expect=201,
    )
    event = api.post(
        "/api/events/events",
        {
            "title": "Door Night",
            "starts_at": later(days=6),
            "ticket_types": [{"name": "Seat", "price_cents": 1500, "quantity": 30}],
        },
        expect=201,
    )["event"]
    api.post(f"/api/events/events/{event['id']}/status", {"status": "published"})
    seat = api.get(f"/api/events/events/{event['id']}")["event"]["ticket_types"][0]
    status, refused = api.call(
        "POST",
        "/api/events/orders",
        {
            "event_id": event["id"],
            "items": [{"ticket_type_id": seat["id"], "quantity": 1}],
            "name": "Door",
            "ref": "PRIYAX",
            "paid": True,
        },
    )
    assert status == 400 and "PRIYAX" in refused["error"]

    status, body = call(api, "GET", "/api/academy/partners?format=csv")
    assert status == 200
    (row,) = list(csv.DictReader(io.StringIO(body.decode("utf-8-sig"))))
    assert row["sales"] == "2000.00" and row["owed"] == "200.00"
    assert row["active"] == "Yes" and "sales_cents" not in row

    owner = api.get("/api/life/profiles")["profiles"][0]
    as_owner = {"X-Aira-Profile": owner["id"]}
    added = [
        call(
            api,
            "POST",
            "/api/life/profiles",
            {"name": name, "age_group": group},
            as_owner,
        )
        for name, group in (("Kid", "child"), ("Tee", "teen"))
    ]
    assert [status for status, _ in added] == [201, 201], added
    kid, teen = (body["profile"] for _, body in added)
    as_kid = {"X-Aira-Profile": kid["id"]}
    for method, path in (
        ("GET", "/api/academy/earnings"),
        ("GET", "/api/academy/students"),
        ("POST", f"/api/academy/partners/{partner['id']}/payout"),
        ("GET", "/api/events/orders"),
    ):
        status, refused = call(
            api, method, path, {} if method == "POST" else None, as_kid
        )
        assert status == 403 and refused["error"] == household.REFUSED, path
    assert (
        api.get(f"/api/academy/partners/{partner['id']}")["partner"]["paid_cents"] == 0
    )
    assert (
        call(api, "GET", "/api/academy/earnings", None, {"X-Aira-Profile": teen["id"]})[
            0
        ]
        == 200
    )
    assert api.get("/api/academy/earnings")["totals"]["total_cents"] == 200000
