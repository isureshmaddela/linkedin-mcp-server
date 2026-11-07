"""One customer record (aira.suite.kit contacts, aira.suite.customers): contacts keyed by normalized email or phone,
every app's rows pointing at them (and the backfill of older rows), one timeline, merging, the Send to Sales handoff,
and a sale recorded by Marketing and by a Sales invoice counting once in revenue."""

from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import pytest

from aira import suite
from aira.suite import customers, kit, public
from aira.suite.bookings import service as bookings
from aira.suite.bookings import store as bookings_store
from aira.suite.marketing import service as marketing
from aira.suite.marketing import store as marketing_store
from aira.suite.service import desk
from aira.suite.service import store as service_store
from aira.suite.shop import service as shop
from aira.suite.shop import store as shop_store
from aira.suite.studio import sales
from aira.suite.studio import service as studio
from aira.suite.studio import store as studio_store

suite.load_apps()  # the apps' public pages (an order's page, a case's page) and the payments registry


class _NoModel:
    def model(self, prompt):
        return None


@pytest.fixture(autouse=True)
def no_model(monkeypatch):
    from aira import suite

    monkeypatch.setattr(suite, "_gateway", _NoModel())


@pytest.fixture
def db(tmp_path):
    kit.forget_schema()
    public.set_index(public._MemoryIndex())
    connection = sqlite3.connect(tmp_path / "suite.sqlite3")
    connection.row_factory = sqlite3.Row
    kit.ensure_core(connection)
    kit.save_business(
        connection,
        {"name": "Chai Co", "email": "owner@chai.example", "currency": "INR"},
    )
    connection.commit()
    yield connection
    connection.close()


def _order(db, **customer):
    shop_store.ensure(db)
    mug = shop.create_product(
        db, {"name": "Mug", "price_cents": 1200, "status": "active"}
    )
    return shop.place_order(
        db, {"items": [{"product_id": mug["id"]}], "name": "Ana Silva", **customer}
    )


def _lead(db, **values):
    marketing_store.ensure(db)
    form = marketing.create_form(
        db,
        {
            "title": "Free consultation",
            "fields": {
                "name": {"show": True},
                "email": {"show": True},
                "phone": {"show": True},
                "company": {"show": True},
                "message": {"show": True},
            },
        },
    )
    marketing.submit(db, form["id"], {"name": "Ana Silva", **values})
    return marketing.list_submissions(db)[0]


# ---------------------------------------------------------------------------
# kit.contact_for
# ---------------------------------------------------------------------------


def test_contacts_are_keyed_by_normalized_email_or_phone(db):
    first = kit.contact_for(db, email=" Ana@Example.COM ", name="Ana", source="shop")
    assert first
    assert kit.contact_for(db, email="ana@example.com", source="bookings") == first
    # a phone in another format is the same person, and joins the record that has it
    by_phone = kit.contact_for(
        db, phone="+91 98765 43210", name="Ravi", source="service"
    )
    assert kit.contact_for(db, phone="098765-43210") == by_phone
    assert kit.contact_for(db, phone="9876543210", email="ravi@example.com") == by_phone
    # ... but a phone does not pull in someone with another email (a shared family phone)
    other = kit.contact_for(
        db, phone="9876543210", email="meera@example.com", name="Meera"
    )
    assert other not in (first, by_phone)
    # nothing to key by: no contact
    assert kit.contact_for(db, name="Walk in", email="not-an-email", phone="12") == ""
    row = kit.fetch_one(db, "SELECT * FROM suite_contacts WHERE id = ?", (first,))
    assert row["email"] == "ana@example.com" and kit.loads(row["apps"]) == [
        "shop",
        "bookings",
    ]
    ravi = kit.fetch_one(db, "SELECT * FROM suite_contacts WHERE id = ?", (by_phone,))
    assert ravi["email"] == "ravi@example.com" and ravi["name"] == "Ravi"


def test_consent_follows_the_marketing_list(db):
    marketing_store.ensure(db)
    form = marketing.create_form(db, {"title": "News"})
    marketing.submit(
        db, form["id"], {"name": "Sam", "email": "sam@example.com", "consent": True}
    )
    contact = kit.fetch_one(
        db, "SELECT * FROM suite_contacts WHERE email = 'sam@example.com'"
    )
    assert contact["email_consent"] is None  # double opt-in: pending says nothing yet
    subscriber = marketing.list_subscribers(db)[0]
    assert (
        subscriber["status"] == "pending" and subscriber["contact_id"] == contact["id"]
    )
    marketing.confirm_subscription(db, subscriber["id"])
    assert (
        kit.fetch_one(
            db,
            "SELECT email_consent FROM suite_contacts WHERE id = ?",
            (contact["id"],),
        )["email_consent"]
        == 1
    )
    marketing.unsubscribe(db, subscriber["id"])
    assert (
        kit.fetch_one(
            db,
            "SELECT email_consent FROM suite_contacts WHERE id = ?",
            (contact["id"],),
        )["email_consent"]
        == 0
    )


def test_older_rows_are_backfilled_once(db):
    """A database from before contacts: the rows get their column and their contact the first time the app opens."""
    shop_store.ensure(db)
    marketing_store.ensure(db)
    _order(db, email="ana@example.com", phone="555 0100")
    db.execute("DROP INDEX IF EXISTS sh_orders_contact")
    db.execute("ALTER TABLE sh_orders DROP COLUMN contact_id")
    db.execute("DELETE FROM suite_contacts")
    db.execute("DELETE FROM suite_contact_keys")
    db.execute(
        """INSERT INTO mk_submissions (id, form_id, name, email, created_at, updated_at, contact_id)
        VALUES ('a' || hex(randomblob(15)), 'f', 'Ana', 'ANA@example.com', '2026-01-01T00:00:00Z', '2026-01-01T00:00:00Z', NULL)"""
    )
    db.execute(
        """INSERT INTO mk_submissions (id, form_id, name, created_at, updated_at, contact_id)
        VALUES ('b' || hex(randomblob(15)), 'f', 'Nobody', '2026-01-01T00:00:00Z', '2026-01-01T00:00:00Z', NULL)"""
    )
    db.commit()
    kit.forget_schema()
    shop_store.ensure(db)
    marketing_store.ensure(db)
    contacts = kit.fetch_all(db, "SELECT * FROM suite_contacts")
    assert len(contacts) == 1 and contacts[0]["first_seen"] == "2026-01-01T00:00:00Z"
    linked = kit.fetch_all(db, "SELECT contact_id FROM mk_submissions ORDER BY name")
    assert [row["contact_id"] for row in linked] == [
        contacts[0]["id"],
        "",
    ]  # '' = nothing to link by
    assert (
        kit.fetch_one(db, "SELECT contact_id FROM sh_orders")["contact_id"]
        == contacts[0]["id"]
    )
    assert kit.link_pending(db) == 0  # each row once


# ---------------------------------------------------------------------------
# the timeline, merging
# ---------------------------------------------------------------------------


def test_one_timeline_across_the_apps(db):
    order = _order(db, email="ana@example.com", phone="+91 98765 43210")
    shop.mark_paid(db, order["id"])
    lead = _lead(db, email="ana@example.com", message="A workshop for my team")
    service_store.ensure(db)
    case = desk.create_case(
        db, {"subject": "Where is my mug?", "email": "ana@example.com"}, channel="web"
    )
    bookings_store.ensure(db)
    staff = bookings_store.create_staff(
        db,
        {
            "name": "Ben",
            "hours": [
                {"weekday": d, "start": "09:00", "end": "17:00"} for d in range(7)
            ],
        },
    )
    cut = bookings_store.create_service(
        db, {"name": "Tasting", "minutes": 30, "price_cents": 50000}
    )
    day = datetime.now(ZoneInfo("UTC")).date() + timedelta(days=5)
    bookings.book(
        db,
        service_id=cut["id"],
        staff_id=staff["id"],
        start=f"{day.isoformat()}T10:00:00+00:00",
        customer={"name": "Ana S", "phone": "9876543210"},
    )
    db.execute(
        "UPDATE suite_outbox SET status = 'sent', sent_at = ? WHERE recipient = 'ana@example.com'",
        (kit.now(),),
    )
    db.commit()
    contact_id = (
        order["contact_id"]
        if "contact_id" in order
        else kit.fetch_one(
            db, "SELECT contact_id FROM sh_orders WHERE id = ?", (order["id"],)
        )["contact_id"]
    )
    assert lead["contact_id"] == contact_id
    assert case["contact_id"] == contact_id
    page = customers.get(db, contact_id)
    kinds = {item["kind"] for item in page["timeline"]}
    assert {"order", "submission", "case", "booking", "payment", "email"} <= kinds
    assert page["paid"] == [{"currency": "INR", "cents": order["total_cents"]}]
    assert page["contact"]["apps"] == ["shop", "marketing", "service", "bookings"]
    found = customers.search(db, q="98765")
    assert [row["id"] for row in found["contacts"]] == [contact_id]
    assert customers.search(db, app="bookings")["total"] == 1


def test_merging_two_records_of_one_person(db):
    order = _order(db, email="ana@example.com")
    lead = _lead(db, email="ana.silva@work.example", company="Silva Ltd")
    keep = kit.fetch_one(
        db, "SELECT contact_id FROM sh_orders WHERE id = ?", (order["id"],)
    )["contact_id"]
    other = lead["contact_id"]
    assert keep != other
    marketing_store.ensure(db)
    db.execute("UPDATE suite_contacts SET email_consent = 0 WHERE id = ?", (other,))
    db.commit()
    result = customers.merge(db, keep, other)
    assert result["moved"] == 1
    merged = result["contact"]
    assert merged["company"] == "Silva Ltd" and merged["email"] == "ana@example.com"
    assert merged["email_consent"] is False  # a "no" stays a "no"
    assert (
        kit.fetch_one(
            db, "SELECT contact_id FROM mk_submissions WHERE id = ?", (lead["id"],)
        )["contact_id"]
        == keep
    )
    assert (
        kit.fetch_one(db, "SELECT 1 AS x FROM suite_contacts WHERE id = ?", (other,))
        is None
    )
    # the other address now finds the kept record
    assert kit.contact_for(db, email="ana.silva@work.example") == keep
    with pytest.raises(ValueError):
        customers.merge(db, keep, keep)


# ---------------------------------------------------------------------------
# Send to Sales, and one sale counted once
# ---------------------------------------------------------------------------


def test_a_marketing_lead_goes_to_sales_once(db):
    lead = _lead(
        db, email="ana@example.com", company="Silva Ltd", message="Need a website"
    )
    marketing.update_submission(db, lead["id"], {"value_cents": 80000})
    first = customers.send_to_sales(db, "marketing", "lead", lead["id"])
    assert first["created"] is True
    deal = first["deal"]
    assert (
        deal["stage"] == "new"
        and deal["value_cents"] == 80000
        and deal["source"] == "website"
    )
    row = kit.fetch_one(db, "SELECT * FROM st_deals WHERE id = ?", (deal["id"],))
    assert (
        row["customer_id"] == lead["contact_id"]
        and row["handoff_ref"] == f"marketing:lead:{lead['id']}"
    )
    client = studio.get_client(db, deal["client_id"])
    assert client["email"] == "ana@example.com" and client["company"] == "Silva Ltd"
    again = customers.send_to_sales(db, "marketing", "lead", lead["id"])
    assert again["created"] is False and again["deal"]["id"] == deal["id"]
    assert (
        customers.handoff_for(db, "marketing", "lead", lead["id"])["id"] == deal["id"]
    )
    timeline = customers.timeline(db, lead["contact_id"])
    assert any(item["kind"] == "deal" for item in timeline)
    # a case from the same person reuses the Sales client
    service_store.ensure(db)
    case = desk.create_case(
        db, {"subject": "A quote please", "email": "ana@example.com"}, channel="web"
    )
    from_case = customers.send_to_sales(db, "service", "case", case["id"])
    assert from_case["deal"]["client_id"] == deal["client_id"]
    with pytest.raises(ValueError):
        customers.send_to_sales(db, "shop", "order", "x")


def _paid_invoice_for(db, deal_id, amount):
    sales.move_deal(db, deal_id, "won")
    made = sales.convert_deal(db, deal_id, today=kit.today())
    invoice = made["invoice"]
    studio.mark_sent(db, invoice["id"])
    return studio.add_payment(
        db, invoice["id"], {"amount_cents": amount}, today=kit.today()
    )


@pytest.mark.parametrize("lead_paid_first", [True, False])
def test_a_sale_marked_paid_in_marketing_and_invoiced_in_sales_counts_once(
    db, lead_paid_first
):
    studio_store.ensure(db)
    lead = _lead(db, email="ana@example.com")
    marketing.update_submission(db, lead["id"], {"value_cents": 80000})
    deal = customers.send_to_sales(db, "marketing", "lead", lead["id"])["deal"]
    if lead_paid_first:
        marketing.mark_paid(db, lead["id"], {})
        assert kit.revenue(db)["total_cents"] == 80000
        invoice = _paid_invoice_for(db, deal["id"], 80000)
    else:
        invoice = _paid_invoice_for(db, deal["id"], 80000)
        marketing.mark_paid(
            db, lead["id"], {}
        )  # marked paid in Marketing as well, afterwards
    db.commit()
    totals = kit.revenue(db)
    assert totals["total_cents"] == 80000 and totals["payments"] == 1
    assert totals["by_app"] == {"studio": 80000}
    line = kit.fetch_one(db, "SELECT * FROM suite_revenue WHERE app = 'marketing'")
    assert line["voided_at"] and line["covered_by"].startswith("studio:payment:")
    # the invoice payment taken off: the lead's own payment counts again
    studio.delete_payment(
        db, invoice["id"], invoice["payments"][0]["id"], today=kit.today()
    )
    db.commit()
    totals = kit.revenue(db)
    assert totals["total_cents"] == 80000 and totals["by_app"] == {"marketing": 80000}
    # ... and once the lead is marked not paid either, nothing counts (and un-covering never brings it back)
    marketing.mark_unpaid(db, lead["id"])
    assert kit.revenue(db)["total_cents"] == 0


def test_two_invoice_payments_both_cover_the_lead(db):
    studio_store.ensure(db)
    lead = _lead(db, email="ana@example.com")
    marketing.update_submission(db, lead["id"], {"value_cents": 80000})
    deal = customers.send_to_sales(db, "marketing", "lead", lead["id"])["deal"]
    marketing.mark_paid(db, lead["id"], {})
    invoice = _paid_invoice_for(db, deal["id"], 30000)
    invoice = studio.add_payment(
        db, invoice["id"], {"amount_cents": 50000}, today=kit.today()
    )
    assert kit.revenue(db)["total_cents"] == 80000
    studio.delete_payment(
        db, invoice["id"], invoice["payments"][0]["id"], today=kit.today()
    )
    # the second payment still stands for the sale: the lead stays covered
    assert kit.revenue(db)["total_cents"] == 50000


def test_possible_duplicates_are_flagged_on_the_timeline(db):
    order = _order(db, email="ana@example.com")
    shop.mark_paid(db, order["id"])
    lead = _lead(db, email="ana@example.com")
    marketing.mark_paid(db, lead["id"], {"value_cents": order["total_cents"]})
    page = customers.get(db, lead["contact_id"])
    assert page["possible_duplicates"] == 2


# ---------------------------------------------------------------------------
# Leads point at their contact; a lead's sequences say when and why they stopped
# ---------------------------------------------------------------------------


def test_leads_join_the_contact_and_show_their_sequences(db):
    from aira.leads import sequences
    from aira.leads import store as leads_store

    leads_store.ensure_schema(db)
    made = leads_store.insert_lead(
        db,
        {
            "name": "Biz A",
            "first_name": "Ana",
            "company": "Biz A",
            "kind": "company",
            "source": "manual",
        },
        list_id=leads_store.default_list(db)["id"],
    )["lead"]
    assert made["contact_id"] == ""  # no email or phone yet
    leads_store.save_enrichment(
        db, made["id"], {"emails": ["ana@biza.test"]}, email="ana@biza.test"
    )
    order = _order(db, email="ana@biza.test")
    contact_id = kit.fetch_one(
        db, "SELECT contact_id FROM leads WHERE id = ?", (made["id"],)
    )["contact_id"]
    assert (
        contact_id
        and contact_id
        == kit.fetch_one(
            db, "SELECT contact_id FROM sh_orders WHERE id = ?", (order["id"],)
        )["contact_id"]
    )
    sequence = sequences.save_sequence(
        db,
        {
            "name": "Follow up",
            "steps": [{"delay_days": 0, "subject": "Hi", "body": "Hello {first_name}"}],
        },
    )
    sequences.enroll(db, sequence["id"], [made["id"]])
    [entry] = sequences.lead_enrollments(db, made["id"])
    assert (
        entry["status"] == "active"
        and entry["steps_total"] == 1
        and entry["sequence_name"] == "Follow up"
    )
    sequences.stop_for_address(db, "ana@biza.test", "bounced")
    [entry] = sequences.lead_enrollments(db, made["id"])
    assert (
        entry["status"] == "stopped"
        and entry["stop_label"] == "The email bounced"
        and entry["stopped_at"]
    )
    assert any(item["kind"] == "lead" for item in customers.timeline(db, contact_id))


# ---------------------------------------------------------------------------
# helpdesk staff hear in the app and by Web Push, as well as by email
# ---------------------------------------------------------------------------


def test_a_new_case_reaches_the_team_in_the_notification_centre(db):
    from aira.notify import store as notify_store

    service_store.ensure(db)
    case = desk.create_case(
        db,
        {"subject": "Broken tap", "email": "ana@example.com", "name": "Ana"},
        channel="web",
    )
    items = kit.fetch_all(db, "SELECT * FROM nt_items WHERE category = 'helpdesk'")
    assert len(items) == 1
    item = items[0]
    assert item["person"] == "owner" and item["url"] == f"/#/service?open={case['id']}"
    # what a lock screen may show stays general; the subject is for the signed-in app
    assert "Ana" not in item["title"] + item["body"] and "Broken tap" in item["detail"]
    # the email still goes once, through the outbox (notify adds no second email)
    assert [m["kind"] for m in kit.outbox(db) if m["kind"].startswith("staff_")] == [
        "staff_new_case"
    ]
    assert notify_store.unread(db, ["owner"]) == 1
    # a live chat is urgent; each case or chat is told once
    desk.start_chat(
        db, {"name": "Bo", "email": "bo@example.com", "message": "Open today?"}
    )
    chat = kit.fetch_one(db, "SELECT * FROM nt_items WHERE title = 'New live chat'")
    assert chat["urgency"] == "high" and chat["url"] == "/#/service?tab=chat"
    desk.notify_staff(db, "new_case", case)
    assert (
        kit.count(db, "SELECT COUNT(*) FROM nt_items WHERE category = 'helpdesk'") == 2
    )


def test_a_named_staff_member_with_a_profile_gets_it(db):
    from aira.suite import life

    life.ensure(db)
    service_store.ensure(db)
    life._create(
        db,
        {"name": "Owner", "age_group": "adult", "email": "owner@chai.example"},
        owner=True,
    )
    kim = life._create(
        db, {"name": "Kim", "age_group": "adult", "email": "kim@chai.example"}
    )
    desk.create_case(
        db,
        {"subject": "A", "email": "a@example.com", "assignee": "Kim@chai.example"},
        channel="web",
    )
    item = kit.fetch_one(db, "SELECT * FROM nt_items WHERE category = 'helpdesk'")
    assert item["person"] == f"p:{kim['id']}"


# ---------------------------------------------------------------------------
# Marketing: someone who has not confirmed gets the confirmation again
# ---------------------------------------------------------------------------


def test_the_confirmation_email_can_be_sent_again_once_an_hour(db):
    marketing_store.ensure(db)
    form = marketing.create_form(db, {"title": "News"})
    marketing.submit(
        db, form["id"], {"name": "Sam", "email": "sam@example.com", "consent": True}
    )
    [subscriber] = marketing.list_subscribers(db, status="pending")

    def sent():
        return [m for m in kit.outbox(db) if m["kind"] == "newsletter_confirm"]

    assert len(sent()) == 1
    with pytest.raises(ValueError, match="less than an hour"):
        marketing.resend_confirmation(db, subscriber["id"])
    db.execute(
        "UPDATE mk_subscribers SET confirm_sent_at = ? WHERE id = ?",
        (kit.iso(datetime.now(timezone.utc) - timedelta(hours=2)), subscriber["id"]),
    )
    again = marketing.resend_confirmation(db, subscriber["id"])
    assert again["status"] == "pending" and len(sent()) == 2
    marketing.confirm_subscription(db, subscriber["id"])
    with pytest.raises(ValueError, match="not confirmed yet"):
        marketing.resend_confirmation(db, subscriber["id"])
