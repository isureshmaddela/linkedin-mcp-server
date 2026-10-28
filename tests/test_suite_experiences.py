"""Courses & Events (the ``experiences`` app: aira/suite/academy and aira/suite/events) across both sections:
referral partners who earn a commission, course reviews, the earnings report, the catalog page, and the fixes
found by using the app (a published course gets its page at once, a started cohort cannot be picked, the last
seat of a course goes to one student only, a free order cannot be marked paid). Unit tests, the single-user
server and the hosted gateway."""

from __future__ import annotations

import json
import sqlite3
import threading
import urllib.error
import urllib.request
from datetime import timedelta

import pytest
from cloud_support import Browser, build_org, sign_in

import auto_apply_assistant as local
import test_careers
from aira import suite
from aira.suite import kit, public
from aira.suite.academy import catalog, earnings, partners
from aira.suite.academy import service as academy
from aira.suite.academy import store as academy_store
from aira.suite.events import service as events
from aira.suite.events import store as events_store

env = test_careers.env
api = test_careers.api
stub = test_careers.stub


@pytest.fixture(autouse=True)
def no_model():
    """Templates only: the machine's own writing model must not answer in tests."""
    previous = suite.gateway()
    suite.set_gateway(suite._NoGateway())
    yield
    suite.set_gateway(previous)


@pytest.fixture
def db_path(tmp_path):
    return tmp_path / "experiences.sqlite3"


@pytest.fixture
def db(db_path):
    kit.forget_schema()
    public.set_index(public._MemoryIndex())
    connection = sqlite3.connect(db_path, timeout=10)
    connection.row_factory = sqlite3.Row
    academy_store.ensure(connection)
    events_store.ensure(connection)
    yield connection
    connection.close()


@pytest.fixture
def local_index():
    public.set_index(local._public_link_index)


def later(**delta) -> str:
    return kit.iso(kit.utcnow() + timedelta(**delta))


def course_with_lessons(db, *, title="Python Basics", price=0, **extra):
    course = academy.create_course(db, {"title": title, "price_cents": price, **extra})
    module = academy.add_module(db, course["id"], "Start")
    first = academy.add_lesson(db, module["id"], {"title": "Hello", "body": "Print."})
    second = academy.add_lesson(db, module["id"], {"title": "More", "body": "Add."})
    academy.set_status(db, course["id"], "published")
    return academy_store.course(db, course["id"]), first, second


def event_with_tickets(db, *, price=2000, quantity=10, title="Jazz Night"):
    event = events.create_event(db, {"title": title, "starts_at": later(days=5)})
    ticket = events.add_ticket_type(
        db, event["id"], {"name": "General", "price_cents": price, "quantity": quantity}
    )
    events.set_status(db, event["id"], "published")
    return events_store.get_event(db, event["id"]), ticket


def order(db, event, ticket, quantity=1, **extra):
    return events.place_order(
        db,
        event["id"],
        {
            "name": "Dee Buyer",
            "email": "dee@example.com",
            "items": [{"ticket_type_id": ticket["id"], "quantity": quantity}],
            **extra,
        },
    )["order"]


# ---------------------------------------------------------------------------
# partners: recorded on a sale, earned on payment, voided on refund, paid out
# ---------------------------------------------------------------------------


def test_partner_codes_are_made_unique_and_validated(db):
    priya = partners.create_partner(
        db, {"name": "Priya Yoga", "email": "priya@example.com"}
    )
    assert priya["code"].startswith("PRIYAYOG") and priya["commission_percent"] == 10
    club = partners.create_partner(
        db, {"name": "Campus Club", "code": "campus-1", "commission_percent": 20}
    )
    assert club["code"] == "CAMPUS-1"
    with pytest.raises(ValueError, match="already uses"):
        partners.create_partner(db, {"name": "Copy", "code": "Campus-1"})
    with pytest.raises(ValueError, match="at least 3"):
        partners.create_partner(db, {"name": "Short", "code": "ab"})
    with pytest.raises(ValueError):
        partners.create_partner(db, {"name": "Greedy", "commission_percent": 95})
    with pytest.raises(ValueError):
        partners.create_partner(db, {"name": "", "code": "NONAME"})
    updated = partners.update_partner(db, club["id"], {"commission_percent": 25})
    assert updated["commission_percent"] == 25 and updated["code"] == "CAMPUS-1"


def test_a_course_referral_earns_on_payment_and_is_voided_on_refund(db):
    course, _, _ = course_with_lessons(db, price=10000)
    partner = partners.create_partner(
        db, {"name": "Priya", "code": "PRIYA", "commission_percent": 20}
    )
    enrolled = academy.enrol(
        db, course["id"], {"name": "Ana", "email": "ana@example.com", "ref": "priya"}
    )["enrolment"]
    (referral,) = partners.referrals(db, partner["id"])
    assert referral["status"] == "pending" and referral["commission_cents"] == 2000
    assert (
        partners.summary(db)["pending_cents"] == 2000
        and partners.summary(db)["owed_cents"] == 0
    )
    assert referral["customer"] == "Ana" and referral["label"] == "Python Basics"

    academy.mark_paid(db, enrolled["id"])
    (referral,) = partners.referrals(db, partner["id"])
    assert referral["status"] == "earned" and referral["commission_cents"] == 2000
    academy.mark_paid(db, enrolled["id"])  # marking paid again changes nothing
    assert partners.summary(db)["owed_cents"] == 2000

    academy.refund(db, enrolled["id"])
    (referral,) = partners.referrals(db, partner["id"])
    assert referral["status"] == "void" and referral["commission_cents"] == 0
    assert partners.summary(db)["owed_cents"] == 0

    # a free course: a referral that earns nothing; unknown and switched-off codes never block a sale
    free, _, _ = course_with_lessons(db, title="Free Intro")
    academy.enrol(
        db, free["id"], {"name": "Bo", "email": "bo@example.com", "ref": "PRIYA"}
    )
    academy.enrol(
        db, free["id"], {"name": "Cy", "email": "cy@example.com", "ref": "NOBODY"}
    )
    partners.update_partner(db, partner["id"], {"active": False})
    academy.enrol(
        db, free["id"], {"name": "Di", "email": "di@example.com", "ref": "PRIYA"}
    )
    rows = partners.referrals(db, partner["id"])
    assert [(r["customer"], r["status"]) for r in rows if r["status"] != "void"] == [
        ("Bo", "earned")
    ]


def test_an_event_referral_follows_the_order_and_comps_never_count(db):
    event, ticket = event_with_tickets(db, price=2500)
    partner = partners.create_partner(
        db, {"name": "Radio Ravi", "code": "RAVI", "commission_percent": 10}
    )
    paid = order(db, event, ticket, 2, ref="ravi")
    cancelled = order(db, event, ticket, 1, ref="RAVI", email="x@example.com")
    events.place_order(
        db,
        event["id"],
        {
            "name": "Press",
            "items": [{"ticket_type_id": ticket["id"], "quantity": 1}],
            "ref": "RAVI",
        },
        source="manual",
        comp=True,
    )
    assert {r["source_id"] for r in partners.referrals(db, partner["id"])} == {
        paid["id"],
        cancelled["id"],
    }
    events.mark_paid(db, paid["id"])
    events.cancel_order(db, cancelled["id"])
    rows = {r["source_id"]: r for r in partners.referrals(db, partner["id"])}
    assert rows[paid["id"]]["status"] == "earned"
    assert rows[paid["id"]]["commission_cents"] == 500  # 10% of 2 x 25.00
    assert rows[cancelled["id"]]["status"] == "void"

    # a refund after the payout keeps the paid commission on record
    payout = partners.pay_out(db, partner["id"], "Bank transfer")
    assert payout["amount_cents"] == 500 and payout["referrals"] == 1
    events.cancel_order(db, paid["id"], refund=True)
    after = {r["source_id"]: r for r in partners.referrals(db, partner["id"])}
    assert after[paid["id"]]["status"] == "paid"
    assert after[paid["id"]]["commission_cents"] == 500


def test_payouts_pay_what_is_owed_once_and_email_the_partner(db):
    course, _, _ = course_with_lessons(db, price=5000)
    partner = partners.create_partner(
        db, {"name": "Priya", "email": "priya@example.com", "commission_percent": 30}
    )
    with pytest.raises(ValueError, match="Nothing is owed"):
        partners.pay_out(db, partner["id"])
    for name in ("Ana", "Bo"):
        enrolled = academy.enrol(
            db,
            course["id"],
            {"name": name, "email": f"{name}@example.com", "ref": partner["code"]},
        )["enrolment"]
        academy.mark_paid(db, enrolled["id"])
    payout = partners.pay_out(db, partner["id"], "UPI 14 Nov")
    assert payout["amount_cents"] == 3000 and payout["referrals"] == 2
    detail = partners.detail(db, partner["id"])
    assert detail["partner"]["paid_cents"] == 3000
    assert detail["partner"]["owed_cents"] == 0
    assert detail["payouts"][0]["note"] == "UPI 14 Nov"
    with pytest.raises(ValueError, match="Nothing is owed"):
        partners.pay_out(db, partner["id"])
    assert "partner_payout" in [m["kind"] for m in kit.outbox(db, app="academy")]


def test_the_partner_page_shows_links_and_earnings_but_no_customer_names(db):
    course, _, _ = course_with_lessons(db, price=4000)
    event, _ = event_with_tickets(db)
    events_link = public.create_link(db, "events", "event", event["id"])
    partner = partners.create_partner(
        db, {"name": "Priya", "email": "priya@example.com"}
    )
    academy.enrol(
        db,
        course["id"],
        {"name": "Secret Name", "email": "secret@example.com", "ref": partner["code"]},
    )
    link = partners.invite(db, partner["id"])
    assert link["kind"] == "partner"
    assert "partner_invite" in [m["kind"] for m in kit.outbox(db, app="academy")]
    page = partners.partner_page(db, partner["id"])
    assert {item["path"] for item in page["links"]} == {
        academy.course_link(db, course["id"])["path"],
        events_link["path"],
    }
    assert page["totals"]["pending_cents"] == 400
    assert (
        page["timezone"] == kit.business(db)["timezone"]
    )  # event times show in the business's zone
    assert "Secret" not in json.dumps(page) and "secret@" not in json.dumps(page)

    result = partners.delete_partner(db, partner["id"])
    assert result == {"deleted": False, "deactivated": True}
    assert public.link_for(db, "academy", "partner", partner["id"]) is None
    with pytest.raises(LookupError):
        partners.partner_page(db, partner["id"])
    spare = partners.create_partner(db, {"name": "Unused"})
    assert partners.delete_partner(db, spare["id"])["deleted"] is True


# ---------------------------------------------------------------------------
# reviews
# ---------------------------------------------------------------------------


def test_students_review_after_a_lesson_and_the_business_can_hide_one(db):
    course, first, _ = course_with_lessons(db)
    enrolled = academy.enrol(
        db, course["id"], {"name": "Ana Maria Lopez", "email": "ana@example.com"}
    )["enrolment"]
    assert academy.student_view(db, enrolled["id"])["can_review"] is False
    with pytest.raises(ValueError, match="Finish a lesson"):
        academy.submit_review(db, enrolled["id"], {"rating": 5})
    academy.complete_lesson(db, enrolled["id"], first["id"])
    with pytest.raises(ValueError):
        academy.submit_review(db, enrolled["id"], {"rating": 6})
    review = academy.submit_review(
        db, enrolled["id"], {"rating": 4, "comment": "Clear and kind."}
    )
    assert review["name"] == "Ana L." and review["status"] == "published"
    again = academy.submit_review(db, enrolled["id"], {"rating": 5, "comment": "Great"})
    assert again["id"] == review["id"] and again["rating"] == 5
    view = academy.student_view(db, enrolled["id"])
    assert view["review"]["rating"] == 5

    other = academy.enrol(db, course["id"], {"name": "Bo", "email": "bo@example.com"})[
        "enrolment"
    ]
    academy.complete_lesson(db, other["id"], first["id"])
    academy.submit_review(db, other["id"], {"rating": 2, "comment": "Too fast."})
    page = academy.course_page(db, course["id"])
    assert page["rating"] == {"average": 3.5, "count": 2}
    assert {r["name"] for r in page["reviews"]} == {"Ana L.", "Bo"}
    assert "ana@example.com" not in json.dumps(page)

    hidden = next(r for r in academy.reviews(db) if r["name"] == "Bo")
    academy.set_review_status(db, hidden["id"], "hidden")
    page = academy.course_page(db, course["id"])
    assert page["rating"] == {"average": 5.0, "count": 1}
    assert [r["name"] for r in page["reviews"]] == ["Ana L."]
    assert academy.overview(db)["rating"] == {"average": 5.0, "count": 1}
    assert academy_store.courses(db)[0]["reviews"] == 1

    academy.refund(db, other["id"])
    with pytest.raises(PermissionError):
        academy.submit_review(db, other["id"], {"rating": 1})


# ---------------------------------------------------------------------------
# earnings and the catalog page
# ---------------------------------------------------------------------------


def test_earnings_rank_courses_and_events_and_keep_refunds_in_the_log(db):
    big, _, _ = course_with_lessons(db, title="Big Course", price=10000)
    small, _, _ = course_with_lessons(db, title="Small Course", price=1000)
    course_with_lessons(db, title="Unsold Course", price=500)
    event, ticket = event_with_tickets(db, price=3000)
    partner = partners.create_partner(db, {"name": "Priya", "commission_percent": 10})
    for index, course in enumerate((big, big, small)):
        enrolled = academy.enrol(
            db,
            course["id"],
            {
                "name": f"S{index}",
                "email": f"s{index}@example.com",
                "ref": partner["code"],
            },
        )["enrolment"]
        academy.mark_paid(db, enrolled["id"])
    refunded = academy.enrol(db, small["id"], {"name": "R", "email": "r@example.com"})[
        "enrolment"
    ]
    academy.mark_paid(db, refunded["id"])
    academy.refund(db, refunded["id"])
    placed = order(db, event, ticket, 2)
    events.mark_paid(db, placed["id"])
    events.save_sponsor(
        db,
        {
            "name": "Popcorn Co",
            "amount_cents": 7000,
            "status": "paid",
            "event_id": event["id"],
        },
    )

    report = earnings.report(db)
    titles = [row["title"] for row in report["items"]]
    assert titles[:3] == ["Big Course", "Jazz Night", "Small Course"]
    by_title = {row["title"]: row for row in report["items"]}
    assert by_title["Big Course"]["revenue_cents"] == 20000
    assert by_title["Big Course"]["commission_cents"] == 2000
    assert by_title["Jazz Night"]["revenue_cents"] == 13000  # 2 tickets + the sponsor
    assert by_title["Jazz Night"]["sponsor_cents"] == 7000
    assert by_title["Small Course"]["refunds"] == 1
    assert by_title["Small Course"]["refunded_cents"] == 1000
    assert report["top"][0]["title"] == "Big Course"
    assert report["lowest"][0]["title"] == "Unsold Course"
    assert not {r["id"] for r in report["top"]} & {r["id"] for r in report["lowest"]}
    totals = report["totals"]
    assert totals["total_cents"] == 20000 + 1000 + 13000
    assert totals["this_month_cents"] == totals["total_cents"]
    assert totals["commission_cents"] == 2100 and totals["kept_cents"] == 34000 - 2100
    assert any(line["refunded"] for line in report["log"])
    assert len(report["log"]) == 6
    rows = earnings.csv_rows(db)
    assert rows[0]["title"] == "Big Course" and rows[0]["revenue"] == "200.00"


def test_the_catalog_lists_only_what_customers_may_open(db):
    course, _, _ = course_with_lessons(db, price=2500)
    draft = academy.create_course(db, {"title": "Secret Draft"})
    event, _ = event_with_tickets(db, title="Open Mic")
    public.create_link(db, "events", "event", event["id"])
    past = events.create_event(
        db,
        {
            "title": "Old Show",
            "starts_at": later(days=-3),
            "ends_at": later(days=-3, hours=1),
        },
    )
    public.create_link(db, "events", "event", past["id"])
    page = catalog.page(db)
    assert [c["title"] for c in page["courses"]] == ["Python Basics"]
    assert [e["title"] for e in page["events"]] == ["Open Mic"]
    assert page["events"][0]["from_cents"] == 2000
    assert draft["title"] not in json.dumps(page)
    assert "enrol_code" not in json.dumps(page) and "notes" not in json.dumps(page)
    link = catalog.create(db)
    assert link["kind"] == "catalog" and catalog.create(db)["token"] == link["token"]
    assert course["status"] == "published"


# ---------------------------------------------------------------------------
# regressions found by using the app
# ---------------------------------------------------------------------------


def test_publishing_a_course_makes_its_page_at_once(db):
    """Publishing used to leave the course without a page until "Create link" was pressed (events had one)."""
    course, _, _ = course_with_lessons(db)
    link = academy.course_link(db, course["id"])
    assert link and link["kind"] == "course"
    academy.set_status(db, course["id"], "draft")
    academy.set_status(db, course["id"], "published")
    assert (
        academy.course_link(db, course["id"])["token"] == link["token"]
    )  # the same link, not a new one


def test_a_public_enrolment_cannot_pick_a_cohort_that_already_started(db):
    course, _, _ = course_with_lessons(db)
    started = academy.create_cohort(
        db,
        {
            "course_id": course["id"],
            "name": "October batch",
            "starts_on": kit.iso(kit.utcnow() - timedelta(days=3))[:10],
        },
    )
    with pytest.raises(ValueError, match="already started"):
        academy.enrol(
            db,
            course["id"],
            {"name": "Ana", "email": "ana@example.com", "cohort_id": started["id"]},
        )
    # the business may still place someone in it by hand
    manual = academy.enrol(
        db,
        course["id"],
        {"name": "Ana", "email": "ana@example.com", "cohort_id": started["id"]},
        source="manual",
    )
    assert manual["enrolment"]["cohort_id"] == started["id"]


def test_two_students_never_get_the_same_last_seat(db, db_path):
    """The seat check and the insert are one write transaction (like event tickets)."""
    course, _, _ = course_with_lessons(db, capacity=1)
    results: list[str] = []
    barrier = threading.Barrier(4)

    def attempt(index):
        connection = sqlite3.connect(db_path, timeout=10)
        connection.row_factory = sqlite3.Row
        try:
            barrier.wait()
            academy.enrol(
                connection,
                course["id"],
                {"name": f"Student {index}", "email": f"s{index}@example.com"},
            )
            results.append("ok")
        except ValueError:
            results.append("refused")
        finally:
            connection.close()

    threads = [threading.Thread(target=attempt, args=(i,)) for i in range(4)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert sorted(results) == ["ok", "refused", "refused", "refused"]
    assert academy_store.seats_taken(db, course["id"]) == 1


def test_a_free_order_cannot_be_marked_paid(db):
    """It used to set a paid date and log "paid" for nothing, and then offered a refund of 0."""
    event, ticket = event_with_tickets(db, price=0)
    free = order(db, event, ticket)
    assert free["status"] == "confirmed"
    with pytest.raises(ValueError, match="free"):
        events.mark_paid(db, free["id"])
    assert events_store.get_order(db, free["id"])["paid_at"] is None


# ---------------------------------------------------------------------------
# the single-user build
# ---------------------------------------------------------------------------


def anonymous(base, method, path, body=None):
    data = json.dumps(body).encode() if body is not None else None
    request = urllib.request.Request(base + path, data=data, method=method)
    request.add_header("Content-Type", "application/json")
    request.add_header("X-Requested-With", "aira")
    request.add_header("Origin", base)
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            raw = response.read()
            return response.status, json.loads(raw) if raw[:1] in (b"{", b"[") else raw
    except urllib.error.HTTPError as error:
        raw = error.read()
        return error.code, json.loads(raw) if raw[:1] in (b"{", b"[") else raw


def raw_get(api, path):
    request = urllib.request.Request(api.base + path)
    request.add_header("X-Requested-With", "aira")
    with api.opener.open(request, timeout=30) as response:
        return response.status, response.read()


def test_a_partner_earns_from_a_course_and_an_event_in_the_local_build(
    env, api, local_index
):
    kit.forget_schema()
    api.post("/api/suite/business", {"name": "Blue Room School"})
    course = api.post(
        "/api/academy/courses", {"title": "Guitar 101", "price_cents": 6000}, expect=201
    )["course"]
    module = api.post(
        f"/api/academy/courses/{course['id']}/modules", {"title": "Chords"}, expect=201
    )["module"]
    lesson = api.post(
        f"/api/academy/modules/{module['id']}/lessons",
        {"title": "G major", "body": "Three fingers."},
        expect=201,
    )["lesson"]
    published = api.post(
        f"/api/academy/courses/{course['id']}/status", {"status": "published"}
    )
    assert published["course"]["status"] == "published"
    course_link = api.get(f"/api/academy/courses/{course['id']}")["link"]
    assert course_link, "publishing makes the course page"
    event = api.post(
        "/api/events/events",
        {
            "title": "Unplugged Night",
            "starts_at": later(days=6),
            "ticket_types": [{"name": "Seat", "price_cents": 1500, "quantity": 30}],
        },
        expect=201,
    )["event"]
    event = api.post(
        f"/api/events/events/{event['id']}/status", {"status": "published"}
    )["event"]

    partner = api.post(
        "/api/academy/partners",
        {"name": "Priya", "email": "priya@example.com", "commission_percent": 20},
        expect=201,
    )["partner"]
    status, _ = api.call(
        "POST", "/api/academy/partners", {"name": "Bad", "commission_percent": 400}
    )
    assert status == 400
    invited = api.post(f"/api/academy/partners/{partner['id']}/invite", {})
    partner_token = invited["link"]["token"]

    status, enrolled = anonymous(
        env.base,
        "POST",
        f"/api/public/{course_link['token']}/enrol",
        {"name": "Ana Student", "email": "ana@example.com", "ref": partner["code"]},
    )
    assert status == 201
    ga = anonymous(env.base, "GET", f"/api/public/{event['link']['token']}")[1]["page"][
        "types"
    ][0]
    status, placed = anonymous(
        env.base,
        "POST",
        f"/api/public/{event['link']['token']}/order",
        {
            "name": "Dee",
            "email": "dee@example.com",
            "items": [{"ticket_type_id": ga["id"], "quantity": 2}],
            "ref": partner["code"].lower(),
        },
    )
    assert status == 201

    student = api.get("/api/academy/students")["students"][0]
    api.post(f"/api/academy/students/{student['id']}/paid", {})
    order_id = api.get("/api/events/orders")["orders"][0]["id"]
    api.post(f"/api/events/orders/{order_id}/paid", {})

    listing = api.get("/api/academy/partners")
    row = listing["partners"][0]
    assert row["referrals"] == 2 and row["owed_cents"] == 1200 + 600
    assert listing["totals"]["sales_cents"] == 9000
    assert {item["type"] for item in listing["share_links"]} == {"course", "event"}
    status, csv_body = raw_get(api, "/api/academy/partners?format=csv")
    assert status == 200 and b"Priya" in csv_body

    status, page = anonymous(env.base, "GET", f"/api/public/{partner_token}")
    assert status == 200 and page["kind"] == "partner"
    assert page["page"]["totals"]["owed_cents"] == 1800
    assert "Ana Student" not in json.dumps(
        page
    ) and "dee@example.com" not in json.dumps(page)

    payout = api.post(f"/api/academy/partners/{partner['id']}/payout", {"note": "UPI"})
    assert payout["payout"]["amount_cents"] == 1800
    status, again = api.call(
        "POST", f"/api/academy/partners/{partner['id']}/payout", {}
    )
    assert status == 400

    # the student reviews the course from their own page
    student_token = enrolled["student_path"].removeprefix("/p/")
    status, refused = anonymous(
        env.base, "POST", f"/api/public/{student_token}/review", {"rating": 5}
    )
    assert status == 400
    anonymous(
        env.base,
        "POST",
        f"/api/public/{student_token}/complete",
        {"lesson_id": lesson["id"]},
    )
    status, reviewed = anonymous(
        env.base,
        "POST",
        f"/api/public/{student_token}/review",
        {"rating": 5, "comment": "Loved it"},
    )
    assert status == 200 and reviewed["review"]["rating"] == 5
    status, course_page = anonymous(
        env.base, "GET", f"/api/public/{course_link['token']}"
    )
    assert course_page["page"]["rating"]["count"] == 1
    assert course_page["page"]["reviews"][0]["name"] == "Ana S."
    review = api.get("/api/academy/reviews")["reviews"][0]
    api.post(f"/api/academy/reviews/{review['id']}/status", {"status": "hidden"})
    assert (
        api.get("/api/academy/reviews?status=hidden")["reviews"][0]["id"]
        == review["id"]
    )

    report = api.get("/api/academy/earnings")
    assert [row["title"] for row in report["items"]][:2] == [
        "Guitar 101",
        "Unplugged Night",
    ]
    assert report["totals"]["total_cents"] == 9000
    assert report["totals"]["commission_cents"] == 1800
    status, csv_body = raw_get(api, "/api/academy/earnings?format=csv")
    assert status == 200 and b"Guitar 101" in csv_body

    catalog_link = api.post("/api/academy/catalog/link", {})["link"]
    status, catalog_page = anonymous(
        env.base, "GET", f"/api/public/{catalog_link['token']}"
    )
    assert status == 200 and catalog_page["kind"] == "catalog"
    assert [c["title"] for c in catalog_page["page"]["courses"]] == ["Guitar 101"]
    assert [e["title"] for e in catalog_page["page"]["events"]] == ["Unplugged Night"]
    api.call("DELETE", "/api/academy/catalog/link")
    assert anonymous(env.base, "GET", f"/api/public/{catalog_link['token']}")[0] == 404


# ---------------------------------------------------------------------------
# the hosted build
# ---------------------------------------------------------------------------


def test_hosted_partners_earnings_and_reviews_follow_the_app_and_the_roles(cloud):
    kit.forget_schema()
    org = build_org(cloud)
    owner = sign_in(cloud, org.owner)
    for path in (
        "/api/academy/partners",
        "/api/academy/earnings",
        "/api/academy/reviews",
        "/api/academy/catalog",
    ):
        status, data = owner.call("GET", path)
        assert status == 402 and data["code"] == "app_required", path
    cloud.billing.subscribe_app(org.owner, "experiences")
    for path in (
        "/api/academy/partners",
        "/api/academy/earnings",
        "/api/academy/reviews",
        "/api/academy/catalog",
    ):
        assert owner.call("GET", path)[0] == 200, path
    status, created = owner.call(
        "POST", "/api/academy/partners", {"name": "Priya", "commission_percent": 15}
    )
    assert status == 201
    partner_id = created["partner"]["id"]
    coach = sign_in(cloud, org.coach)
    assert (
        coach.call(
            "PATCH", f"/api/academy/partners/{partner_id}", {"notes": "Met at a fair"}
        )[0]
        == 200
    )
    auditor = sign_in(cloud, org.auditor)
    assert auditor.call("GET", "/api/academy/partners")[0] == 200
    assert auditor.call("GET", "/api/academy/earnings")[0] == 200
    assert auditor.call("POST", "/api/academy/partners", {"name": "No"})[0] == 403
    assert (
        auditor.call("POST", f"/api/academy/partners/{partner_id}/payout", {})[0] == 403
    )
    candidate = sign_in(cloud, org.candidate)
    assert candidate.call("GET", "/api/academy/partners")[0] == 403
    assert candidate.call("GET", "/api/academy/earnings")[0] == 403

    link = owner.call("POST", f"/api/academy/partners/{partner_id}/link", {})[1]["link"]
    visitor = Browser(cloud)
    status, page = visitor.call("GET", f"/api/public/{link['token']}")
    assert status == 200 and page["page"]["partner"]["name"] == "Priya"
    catalog_link = owner.call("POST", "/api/academy/catalog/link", {})[1]["link"]
    assert visitor.call("GET", f"/api/public/{catalog_link['token']}")[0] == 200

    other = build_org(cloud, "Other Org", slug_email="other.test", apps=["experiences"])
    status, theirs = sign_in(cloud, other.owner).call("GET", "/api/academy/partners")
    assert status == 200 and theirs["partners"] == []
    assert (
        sign_in(cloud, other.owner).call("GET", f"/api/academy/partners/{partner_id}")[
            0
        ]
        == 404
    )
