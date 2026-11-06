"""Each app's payment entry (aira/payments/apps.py): what is due, and that a paid link runs the app's own mark_paid.

Razorpay is the local stand-in (tests/razorpay_payments_stub.py)."""

from __future__ import annotations

import sqlite3
from datetime import timedelta

import pytest
from razorpay_payments_stub import KEY_ID, KEY_SECRET, WEBHOOK_SECRET, RazorpayStub
from test_payments_razorpay import Secrets, deliver

from aira.payments import apps as _apps  # noqa: F401
from aira.payments import razorpay
from aira.payments import service as payments
from aira.suite import kit, public


@pytest.fixture(autouse=True)
def no_model(monkeypatch):
    from aira import suite

    class NoModel:
        def model(self, prompt):
            return None

    monkeypatch.setattr(suite, "_gateway", NoModel())


@pytest.fixture
def db(tmp_path):
    kit.forget_schema()
    public.set_index(public._MemoryIndex())
    connection = sqlite3.connect(tmp_path / "apps.sqlite3")
    connection.row_factory = sqlite3.Row
    kit.ensure_core(connection)
    kit.save_business(
        connection, {"name": "Learn Co", "currency": "INR", "email": "hi@learn.example"}
    )
    yield connection
    connection.close()
    public.reset_index()


@pytest.fixture
def rzp(db, monkeypatch):
    server = RazorpayStub().start()
    monkeypatch.setenv(razorpay.API_BASE_ENV, server.base)
    secrets = Secrets()
    secrets.put(db, kit.RAZORPAY_KEY_SECRET, KEY_SECRET)
    secrets.put(db, kit.RAZORPAY_WEBHOOK_SECRET, WEBHOOK_SECRET)
    kit.save_payment_settings(
        db,
        {"provider": "razorpay", "razorpay_key_id": KEY_ID, "key_secret_saved": True},
    )
    with payments.secrets(secrets):
        yield server, secrets
    server.stop()


def pay_through(db, rzp, app, object_type, object_id):
    stub, secrets = rzp
    made = payments.create_request(db, app, object_type, object_id)
    payment = stub.pay(made["provider_ref"])
    outcome = deliver(
        db,
        secrets,
        *stub.webhook(
            "payment_link.paid", link_id=made["provider_ref"], payment=payment
        ),
    )
    assert outcome["outcome"] == "paid", (
        outcome,
        payments.get(db, made["id"])["problem"],
    )
    return made


def test_an_academy_enrolment_unlocks_when_paid(db, rzp):
    from aira.suite.academy import service, store

    store.ensure(db)
    course = service.create_course(db, {"title": "Python Basics", "price_cents": 99900})
    module = service.add_module(db, course["id"], "Start")
    service.add_lesson(
        db, module["id"], {"title": "Hello", "body": "Print things.", "minutes": 5}
    )
    service.set_status(db, course["id"], "published")
    result = service.enrol(
        db, course["id"], {"name": "Ana", "email": "ana@example.com"}
    )
    enrolment = result["enrolment"]
    assert enrolment["status"] == "pending_payment"
    made = pay_through(db, rzp, "academy", "enrolment", enrolment["id"])
    assert made["amount_cents"] == 99900 and made["description"].startswith(
        "Python Basics"
    )
    assert store.enrolment(db, enrolment["id"])["status"] == "active"
    assert kit.revenue(db, app="academy")["total_cents"] == 99900
    with pytest.raises(ValueError, match="not waiting"):
        payments.create_request(db, "academy", "enrolment", enrolment["id"])
    # the course page finds a pending enrolment by its reference (and does not pre-fill whose it is)
    other = service.enrol(db, course["id"], {"name": "Bo", "email": "bo@example.com"})[
        "enrolment"
    ]
    link = public.create_link(db, "academy", "course", course["id"], title="Course")
    status, body = public.act(
        db,
        link["token"],
        "pay_online",
        {"reference": other["reference"]},
        {"client": "x"},
    )
    assert status == 200 and body["url"].startswith("https://rzp.io/")
    assert "customer" not in rzp[0].calls_to("POST", "/payment_links")[-1]["body"]
    with pytest.raises(LookupError):
        public.act(
            db, link["token"], "pay_online", {"reference": "AC-NOPE"}, {"client": "x"}
        )


def test_an_event_ticket_order_is_confirmed_when_paid(db, rzp):
    from aira.suite.events import service, store

    store.ensure(db)
    event = service.create_event(
        db,
        {
            "title": "Jazz Night",
            "venue_name": "Blue Room",
            "starts_at": kit.iso(kit.utcnow() + timedelta(days=3)),
        },
    )
    ticket = service.add_ticket_type(
        db, event["id"], {"name": "General", "price_cents": 50000, "quantity": 10}
    )
    service.set_status(db, event["id"], "published")
    order = service.place_order(
        db,
        event["id"],
        {
            "name": "Ana",
            "email": "ana@example.com",
            "items": [{"ticket_type_id": ticket["id"], "quantity": 2}],
        },
    )["order"]
    assert order["status"] == "pending_payment"
    made = pay_through(db, rzp, "events", "order", order["id"])
    assert made["amount_cents"] == 100000
    assert store.get_order(db, order["id"])["status"] == "confirmed"
    assert kit.revenue(db, app="events")["total_cents"] == 100000


def test_a_marketing_lead_and_a_website_project(db, rzp):
    from aira.suite.marketing import service as marketing
    from aira.suite.marketing import store as marketing_store
    import aira.suite.sites.sites_routes  # noqa: F401 - its public pages (a paid project gets its preview link)
    from aira.suite.sites import store as sites_store

    marketing_store.ensure(db)
    form = marketing.create_form(db, {"title": "Consultation", "tag": "consult"})
    marketing.submit(db, form["id"], {"name": "Ana", "email": "ana@example.com"})
    lead = marketing.list_submissions(db)[0]
    with pytest.raises(ValueError, match="Nothing is owed"):
        payments.create_request(db, "marketing", "lead", lead["id"])
    db.execute(
        "UPDATE mk_submissions SET value_cents = 150000 WHERE id = ?", (lead["id"],)
    )
    db.commit()
    pay_through(db, rzp, "marketing", "lead", lead["id"])
    won = marketing.get_submission(db, lead["id"])
    assert won["status"] == "won" and won["paid_at"]
    assert kit.revenue(db, app="marketing")["total_cents"] == 150000

    sites_store.ensure(db)
    project_id = kit.new_id()
    db.execute(
        "INSERT INTO sb_projects (id, name, client, price_cents, created_at, updated_at) VALUES (?, 'Acme site', 'Acme Ltd', 300000, ?, ?)",
        (project_id, kit.now(), kit.now()),
    )
    db.commit()
    pay_through(db, rzp, "sites", "project", project_id)
    assert sites_store.project(db, project_id)["paid_at"]
    assert kit.revenue(db, app="sites")["total_cents"] == 300000


def test_every_wired_app_is_registered_and_ads_top_ups_are_not():
    assert set(payments.REGISTRY) == {
        ("shop", "order"),
        ("bookings", "booking"),
        ("academy", "enrolment"),
        ("events", "order"),
        ("marketing", "lead"),
        ("sites", "project"),
        ("learn", "deck_sale"),
        ("news", "subscriber"),
        ("stocks", "subscriber"),
        ("play", "entry"),
    }
    assert not any(app == "ads" for app, _ in payments.REGISTRY)
    for kind in (
        ("shop", "order"),
        ("bookings", "booking"),
        ("academy", "student"),
        ("events", "tickets"),
        ("news", "newsletter"),
        ("learn", "deck"),
    ):
        assert {"pay_online", "pay_return"} <= set(public.KINDS[kind].actions), kind
