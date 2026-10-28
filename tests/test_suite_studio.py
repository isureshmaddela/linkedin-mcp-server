"""Studio (aira/suite/studio): proposals, projects, time, invoices, payments, retainers and their public pages.

Unit tests run on a scratch SQLite file; the journeys use the real single-user request handler and the hosted
gateway, with anonymous visitors on the public pages.
"""

from __future__ import annotations

import json
import sqlite3
import urllib.error
import urllib.request

import pytest
from cloud_support import Browser, build_org, sign_in

import auto_apply_assistant as local
import test_careers
from aira import suite
from aira.suite import kit, public
from aira.suite.studio import service, store

env = test_careers.env
api = test_careers.api
stub = test_careers.stub

TODAY = "2026-10-25"


class _NoModel:
    def model(self, prompt):
        return None


@pytest.fixture(autouse=True)
def no_model():
    """Never call a real writing model (Ollama may be running on this machine)."""
    previous = suite.gateway()
    suite.set_gateway(_NoModel())
    yield
    suite.set_gateway(previous)


@pytest.fixture
def db(tmp_path):
    kit.forget_schema()
    public.set_index(public._MemoryIndex())
    connection = sqlite3.connect(tmp_path / "studio.sqlite3")
    connection.row_factory = sqlite3.Row
    store.ensure(connection)
    yield connection
    connection.close()


@pytest.fixture
def local_index():
    public.set_index(local._public_link_index)


def _client(db, **extra):
    return service.create_client(
        db,
        {
            "name": "Maya Patel",
            "company": "Bloom Bakery",
            "email": "maya@bloom.example",
            **extra,
        },
    )


def _invoice(db, client, items=None, **extra):
    return service.create_invoice(
        db,
        {
            "client_id": client["id"],
            "items": items
            or [{"description": "Website", "quantity": 1, "unit_cents": 100000}],
            **extra,
        },
        today=TODAY,
    )


# ---------------------------------------------------------------------------
# money, numbering and statuses
# ---------------------------------------------------------------------------


def test_totals_apply_discount_then_tax_rounding_half_up():
    items = service.clean_items(
        [
            {
                "description": "Design",
                "quantity": "2.5",
                "unit_cents": 3333,
            },  # 8332.5 -> 8333
            {"description": "Hosting", "quantity": 3, "unit_price": "19.99"},  # 5997
        ]
    )
    assert [item["amount_cents"] for item in items] == [8333, 5997]
    figures = service.totals(items, 1330, 750)  # 14330 - 1330 = 13000; 7.5% = 975
    assert figures == {
        "subtotal_cents": 14330,
        "discount_cents": 1330,
        "tax_cents": 975,
        "credit_cents": 0,
        "total_cents": 13975,
    }
    # a deposit already invoiced (tax included) comes off last, never below zero
    assert service.totals(items, 1330, 750, 3975)["total_cents"] == 10000
    assert service.totals(items, 1330, 750, 10**9)["total_cents"] == 0
    assert (
        service.totals(items, 10**9, 750)["total_cents"] == 0
    )  # a discount never makes it negative
    assert service.tax_of(1, 5000) == 1  # 0.5 rounds up
    for bad in (
        [{"description": "", "quantity": 1}],
        [{"description": "x", "quantity": 0}],
        [{"description": "x", "quantity": "abc"}],
        "nope",
    ):
        with pytest.raises(ValueError):
            service.clean_items(bad)


def test_documents_are_numbered_and_take_tax_from_the_business(db):
    kit.save_business(db, {"name": "Northlight Studio", "tax_rate_bp": 1000})
    client = _client(db)
    first = _invoice(db, client)
    second = _invoice(db, client)
    assert (first["number"], second["number"]) == ("INV-0001", "INV-0002")
    assert first["tax_rate_bp"] == 1000 and first["total_cents"] == 110000
    assert first["due_date"] == "2026-11-08"  # 14 days of payment terms
    service.save_settings(db, {"invoice_prefix": "NL-", "payment_terms_days": 30})
    third = _invoice(db, client, tax_rate_bp=0)
    assert (
        third["number"] == "NL-0003"
        and third["total_cents"] == 100000
        and third["due_date"] == "2026-11-24"
    )
    proposal = service.create_proposal(
        db,
        {
            "client_id": client["id"],
            "title": "Rebrand",
            "items": [{"description": "Logo", "unit_cents": 50000}],
        },
        today=TODAY,
    )
    assert proposal["number"] == "PRO-0001" and proposal["valid_until"] == "2026-11-24"
    with pytest.raises(ValueError):
        service.create_invoice(
            db, {"client_id": client["id"], "items": []}, today=TODAY
        )
    with pytest.raises(LookupError):
        service.create_invoice(
            db, {"client_id": "0" * 32, "items": [{"description": "x"}]}, today=TODAY
        )


def test_partial_payments_overdue_and_revenue_counted_once(db):
    client = _client(db)
    invoice = _invoice(db, client, due_date="2026-11-01")
    assert invoice["status"] == "draft"
    service.mark_sent(db, invoice["id"])
    assert service.get_invoice(db, invoice["id"], TODAY)["status"] == "sent"
    assert service.get_invoice(db, invoice["id"], "2026-11-02")["status"] == "overdue"
    part = service.add_payment(
        db,
        invoice["id"],
        {"amount_cents": 40000, "method": "upi", "day": "2026-10-26"},
        today=TODAY,
    )
    assert part["status"] == "partially_paid" and part["balance_cents"] == 60000
    assert (
        service.get_invoice(db, invoice["id"], "2026-11-05")["status"] == "overdue"
    )  # still owing after the due date
    with pytest.raises(ValueError, match="more than"):
        service.add_payment(db, invoice["id"], {"amount_cents": 70000}, today=TODAY)
    full = service.add_payment(
        db, invoice["id"], {}, today=TODAY
    )  # "mark paid": the balance
    assert (
        full["status"] == "paid"
        and full["balance_cents"] == 0
        and len(full["payments"]) == 2
    )
    with pytest.raises(ValueError, match="already paid"):
        service.add_payment(db, invoice["id"], {}, today=TODAY)
    assert kit.revenue(db)["total_cents"] == 100000 and kit.revenue(db)["payments"] == 2
    # Deleting a payment takes it out of revenue; voiding the invoice takes the rest out.
    balance_payment = next(p for p in full["payments"] if p["amount_cents"] == 60000)
    service.delete_payment(db, invoice["id"], balance_payment["id"], today=TODAY)
    assert kit.revenue(db)["total_cents"] == 40000
    assert service.get_invoice(db, invoice["id"], TODAY)["status"] == "partially_paid"
    voided = service.void_invoice(db, invoice["id"], today=TODAY)
    assert voided["status"] == "void" and voided["balance_cents"] == 0
    assert kit.revenue(db)["total_cents"] == 0
    with pytest.raises(ValueError):
        service.add_payment(db, invoice["id"], {}, today=TODAY)
    with pytest.raises(ValueError):
        service.delete_invoice(db, invoice["id"])  # it has a payment on record
    money = service.get_client(db, client["id"])
    assert money["paid_cents"] == 0  # payments on a void invoice are not "paid"


def test_paying_a_draft_issues_it_and_editing_cannot_go_below_paid(db):
    client = _client(db)
    invoice = _invoice(db, client)
    paid = service.add_payment(db, invoice["id"], {"amount_cents": 50000}, today=TODAY)
    assert paid["state"] == "sent" and paid["status"] == "partially_paid"
    with pytest.raises(ValueError, match="less than"):
        service.update_invoice(
            db,
            invoice["id"],
            {"items": [{"description": "Smaller", "unit_cents": 1000}]},
            today=TODAY,
        )
    with pytest.raises(ValueError, match="sent invoice"):
        service.delete_invoice(db, _sent(db, client)["id"])


def _sent(db, client):
    invoice = _invoice(db, client)
    service.mark_sent(db, invoice["id"])
    return invoice


def test_proposals_are_accepted_once_expire_and_convert_once(db):
    client = _client(db)
    proposal = service.create_proposal(
        db,
        {
            "client_id": client["id"],
            "title": "Website refresh",
            "items": [
                {"description": "Design", "unit_cents": 200000},
                {"description": "Build", "quantity": 2, "unit_cents": 150000},
            ],
            "discount_cents": 50000,
            "valid_until": "2026-11-30",
        },
        today=TODAY,
    )
    assert proposal["total_cents"] == 450000
    with pytest.raises(ValueError, match="not open"):
        service.accept_proposal(
            db, proposal["id"], name="Maya Patel", visitor={}, today=TODAY
        )
    service.mark_proposal_sent(db, proposal["id"])
    assert service.get_proposal(db, proposal["id"], "2026-12-01")["status"] == "expired"
    with pytest.raises(ValueError, match="expired"):
        service.accept_proposal(
            db, proposal["id"], name="Maya Patel", visitor={}, today="2026-12-01"
        )
    with pytest.raises(ValueError, match="Only an accepted"):
        service.convert_proposal(db, proposal["id"], today=TODAY)
    accepted = service.accept_proposal(
        db,
        proposal["id"],
        name="Maya Patel",
        visitor={"client": "203.0.113.9"},
        today=TODAY,
    )
    assert (
        accepted["status"] == "accepted"
        and accepted["accepted_name"] == "Maya Patel"
        and accepted["accepted_from"] == "203.0.113.9"
    )
    with pytest.raises(ValueError, match="already accepted"):
        service.accept_proposal(
            db, proposal["id"], name="Someone", visitor={}, today=TODAY
        )
    with pytest.raises(ValueError):
        service.update_proposal(db, proposal["id"], {"title": "Changed"}, today=TODAY)
    with pytest.raises(ValueError):
        service.delete_proposal(db, proposal["id"])
    first = service.convert_proposal(db, proposal["id"], today=TODAY)
    again = service.convert_proposal(db, proposal["id"], today=TODAY)
    assert (
        first["project"]["id"] == again["project"]["id"]
        and first["invoice"]["id"] == again["invoice"]["id"]
    )
    assert (
        first["project"]["budget_cents"] == 450000
        and first["invoice"]["total_cents"] == 450000
    )
    assert (
        first["invoice"]["status"] == "draft" and first["proposal"]["converted"] is True
    )
    assert kit.count(db, "SELECT COUNT(*) FROM st_invoices") == 1


def test_billable_time_becomes_one_invoice_and_is_marked_billed(db):
    client = _client(db)
    project = service.create_project(
        db, {"client_id": client["id"], "name": "App build", "budget_cents": 500000}
    )
    service.add_time(
        db,
        project["id"],
        {"minutes": 90, "rate_cents": 8000, "note": "Wireframes", "day": "2026-10-20"},
        today=TODAY,
    )
    service.add_time(
        db,
        project["id"],
        {"hours": "0.8333", "rate_cents": 6000, "day": "2026-10-21"},
        today=TODAY,
    )  # 50 minutes
    service.add_time(
        db,
        project["id"],
        {"minutes": 30, "billable": False, "note": "Internal"},
        today=TODAY,
    )
    detail = service.get_project(db, project["id"])
    assert (
        detail["minutes"] == 170
        and detail["unbilled_minutes"] == 140
        and detail["unbilled_cents"] == 12000 + 5000
    )
    invoice = service.invoice_unbilled_time(db, project["id"], today=TODAY)
    assert len(invoice["items"]) == 2 and invoice["subtotal_cents"] == 17000
    assert service.get_project(db, project["id"])["unbilled_minutes"] == 0
    with pytest.raises(ValueError, match="no unbilled"):
        service.invoice_unbilled_time(db, project["id"], today=TODAY)
    billed = kit.fetch_one(db, "SELECT id FROM st_time WHERE invoice_id != ''")
    with pytest.raises(ValueError, match="on an invoice"):
        service.delete_time(db, billed["id"])
    service.delete_invoice(db, invoice["id"])  # a draft: the time is unbilled again
    assert service.get_project(db, project["id"])["unbilled_cents"] == 17000
    task = service.add_task(
        db, project["id"], {"title": "Ship beta", "due_date": "2026-10-30"}
    )
    assert service.update_task(db, task["id"], {"status": "done"})["status"] == "done"


def test_retainers_invoice_once_per_month_and_catch_up(db):
    client = _client(db)
    retainer = service.create_retainer(
        db,
        {
            "client_id": client["id"],
            "title": "Monthly SEO",
            "amount_cents": 120000,
            "next_date": "2026-08-31",
        },
        today=TODAY,
    )
    assert retainer["anchor_day"] == 31
    created = service.run_retainers(db, today=TODAY)
    # Due on Aug 31 and Sep 30; Oct 31 is still ahead on Oct 25.
    assert [invoice["period"] for invoice in created] == ["2026-08", "2026-09"]
    assert service.run_retainers(db, today=TODAY) == []
    assert service.get_retainer(db, retainer["id"])["next_date"] == "2026-10-31"
    assert [
        invoice["period"] for invoice in service.run_retainers(db, today="2026-10-31")
    ] == ["2026-10"]
    assert service.add_month("2027-01-31", 31) == "2027-02-28"
    assert service.add_month("2026-12-15", 15) == "2027-01-15"
    service.update_retainer(db, retainer["id"], {"active": False})
    assert service.run_retainers(db, today="2027-03-01") == []


def test_retainer_dates_follow_the_anchor_day(db):
    client = _client(db)
    service.create_retainer(
        db,
        {
            "client_id": client["id"],
            "title": "Care plan",
            "amount_cents": 5000,
            "next_date": "2026-08-31",
        },
        today=TODAY,
    )
    created = service.run_retainers(db, today="2026-10-31")
    assert [invoice["issue_date"] for invoice in created] == [
        "2026-08-31",
        "2026-09-30",
        "2026-10-31",
    ]


def test_overview_checklist_and_summary(db):
    kit.save_business(db, {"name": "Northlight Studio"})
    client = _client(db)
    late = _invoice(db, client, issue_date="2026-09-01", due_date="2026-09-15")
    service.mark_sent(db, late["id"])
    service.add_payment(db, _sent(db, client)["id"], {}, today=TODAY)
    project = service.create_project(
        db,
        {
            "client_id": client["id"],
            "name": "Menu boards",
            "deadline": "2026-10-30",
            "status": "active",
        },
    )
    service.add_task(
        db, project["id"], {"title": "Print proofs", "due_date": "2026-10-20"}
    )
    data = service.overview(db, today=TODAY)
    assert (
        data["outstanding_cents"] == 100000
        and data["overdue_cents"] == 100000
        and data["overdue_count"] == 1
    )
    assert data["paid_this_month_cents"] == 100000
    assert data["top_clients"][0]["name"] == "Maya Patel"
    assert (
        data["deadlines"][0]["name"] == "Menu boards"
        and data["tasks_due"][0]["late"] is True
    )
    steps = {step["id"]: step["done"] for step in service.checklist(db)}
    assert steps == {
        "profile": True,
        "client": True,
        "deal": False,
        "proposal": False,
        "invoice": True,
        "payment": True,
    }
    cards = service.summary(db, today=TODAY)["cards"]
    assert cards[1]["tone"] == "bad" and cards[2]["value"] == "$1,000.00"
    with pytest.raises(ValueError, match="has invoices"):
        service.delete_client(db, client["id"])


def test_the_template_draft_is_useful_without_a_model(db):
    client = _client(db)
    draft = service.draft_proposal(
        db,
        "A new website for online cake orders. Mobile first.",
        client_id=client["id"],
    )
    assert draft["source"] == "template"
    assert draft["intro"].startswith("Hi Maya") and "Bloom Bakery" in draft["intro"]
    assert "Discovery" in draft["scope"] and len(draft["items"]) == 3
    with pytest.raises(ValueError):
        service.draft_proposal(db, "")


def test_public_pages_show_only_public_fields(db):
    client = _client(db, notes="Pays late, chase on day 1")
    invoice = _invoice(
        db, client, internal_notes="margin 60%", notes="Thanks for your business"
    )
    page = service.public_invoice(db, invoice["id"], TODAY)
    text = json.dumps(page)
    assert (
        "margin" not in text
        and "Pays late" not in text
        and "maya@bloom.example" not in text
    )
    assert (
        page["bill_to"] == {"name": "Maya Patel", "company": "Bloom Bakery"}
        and page["notes"] == "Thanks for your business"
    )


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


def test_local_journey_from_proposal_to_paid_invoice(env, api, local_index):
    kit.forget_schema()
    api.post(
        "/api/suite/business",
        {
            "name": "Northlight Studio",
            "email": "hello@northlight.example",
            "payment_link": "https://pay.example/northlight",
        },
    )
    client = api.post(
        "/api/studio/clients",
        {
            "name": "Maya Patel",
            "company": "Bloom Bakery",
            "email": "maya@bloom.example",
            "tags": "bakery, vip",
        },
        expect=201,
    )["client"]
    assert api.get("/api/studio/clients?tag=vip")["clients"][0]["id"] == client["id"]
    assert api.get("/api/studio/clients?q=bloom")["clients"]
    draft = api.post(
        "/api/studio/proposals/draft",
        {"brief": "Online ordering website", "client_id": client["id"]},
    )["draft"]
    created = api.post(
        "/api/studio/proposals",
        {
            "client_id": client["id"],
            "title": draft["title"],
            "intro": draft["intro"],
            "scope": draft["scope"],
            "items": [{"description": "Website", "quantity": 1, "unit_cents": 300000}],
        },
        expect=201,
    )
    proposal_id = created["proposal"]["id"]
    sent = api.post(f"/api/studio/proposals/{proposal_id}/send", {})
    assert (
        sent["message"]["kind"] == "proposal" and sent["message"]["status"] == "pending"
    )
    assert sent["proposal"]["status"] == "sent"
    token = sent["link"]["token"]
    assert sent["link"]["path"] in sent["message"]["body"]

    status, page = _anonymous(env, "GET", f"/api/public/{token}")
    assert (
        status == 200
        and page["kind"] == "proposal"
        and page["page"]["total_cents"] == 300000
    )
    assert (
        "client_email" not in json.dumps(page)
        and page["business"]["name"] == "Northlight Studio"
    )
    status, refused = _anonymous(
        env, "POST", f"/api/public/{token}/accept", {"name": "Maya Patel"}
    )
    assert status == 400
    status, accepted = _anonymous(
        env,
        "POST",
        f"/api/public/{token}/accept",
        {"name": "Maya Patel", "agree": True},
    )
    assert status == 200 and accepted["proposal"]["status"] == "accepted"
    assert (
        _anonymous(
            env, "POST", f"/api/public/{token}/decline", {"reason": "changed mind"}
        )[0]
        == 400
    )

    converted = api.post(f"/api/studio/proposals/{proposal_id}/convert", {})
    invoice_id = converted["invoice"]["id"]
    assert converted["project"]["name"] == draft["title"]
    sent_invoice = api.post(f"/api/studio/invoices/{invoice_id}/send", {})
    assert (
        sent_invoice["invoice"]["status"] == "sent"
        and sent_invoice["message"]["kind"] == "invoice"
    )
    invoice_token = sent_invoice["link"]["token"]
    status, page = _anonymous(env, "GET", f"/api/public/{invoice_token}")
    assert (
        status == 200
        and page["page"]["balance_cents"] == 300000
        and page["business"]["payment_link"] == "https://pay.example/northlight"
    )
    status, notified = _anonymous(
        env,
        "POST",
        f"/api/public/{invoice_token}/notify_paid",
        {"reference": "UTR 99812"},
    )
    assert (
        status == 200
        and notified["thanks"] is True
        and notified["invoice"]["status"] == "sent"
    )  # not marked paid
    detail = api.get(f"/api/studio/invoices/{invoice_id}")
    assert detail["invoice"]["customer_notices"][0]["reference"] == "UTR 99812"

    paid = api.post(
        f"/api/studio/invoices/{invoice_id}/payments",
        {"method": "bank_transfer"},
        expect=201,
    )
    assert paid["invoice"]["status"] == "paid"
    assert (
        _anonymous(env, "POST", f"/api/public/{invoice_token}/notify_paid", {})[0]
        == 400
    )
    home = api.get("/api/suite/home")
    assert home["revenue"]["total_cents"] == 300000 and home["revenue"]["by_app"] == {
        "studio": 300000
    }
    studio = next(app for app in home["apps"] if app["id"] == "studio")
    assert studio["installed"] and studio["progress"]["done"] >= 4
    assert any(row["public"] and "accepted" in row["title"] for row in home["activity"])
    overview = api.get("/api/studio/overview")
    assert overview["paid_this_month_cents"] == 300000
    status, body = api.call("POST", f"/api/studio/invoices/{invoice_id}/remind", {})
    assert status == 400 and "unpaid" in body["error"]
    revoked = api.ok("DELETE", f"/api/studio/invoices/{invoice_id}/link")
    assert revoked["link"] is None
    assert _anonymous(env, "GET", f"/api/public/{invoice_token}")[0] == 404


def test_local_csv_exports_and_validation(env, api):
    kit.forget_schema()
    status, body = api.call("POST", "/api/studio/clients", {"name": ""})
    assert status == 400 and "client's name" in body["error"]
    client = api.post(
        "/api/studio/clients",
        {"name": "=cmd|' /C calc'!A0", "email": "x@example.com"},
        expect=201,
    )["client"]
    status, payload = api.call("GET", f"/api/studio/clients/{client['id']}")
    assert status == 200 and payload["client"]["billed_cents"] == 0
    assert api.call("GET", "/api/studio/clients/" + "0" * 32)[0] == 404
    request = urllib.request.Request(
        env.base + "/api/studio/clients/export", headers={"X-Requested-With": "aira"}
    )
    with api.opener.open(request, timeout=30) as response:
        text = response.read().decode("utf-8")
        assert "attachment" in response.headers["Content-Disposition"]
    assert text.splitlines()[0].startswith("name,company,email")
    assert "'=cmd" in text  # a formula-looking name cannot run in a spreadsheet


# ---------------------------------------------------------------------------
# the hosted build
# ---------------------------------------------------------------------------


def test_hosted_studio_needs_the_app_and_respects_roles(cloud):
    kit.forget_schema()
    org = build_org(cloud)
    owner = sign_in(cloud, org.owner)
    status, body = owner.call("GET", "/api/studio/overview")
    assert status == 402 and body["code"] == "app_required"
    cloud.billing.subscribe_app(org.owner, "studio")
    assert owner.call("GET", "/api/studio/overview")[0] == 200
    status, made = owner.call("POST", "/api/studio/clients", {"name": "Owner's client"})
    assert status == 201, made
    for who in ("admin", "coach"):
        browser = sign_in(cloud, getattr(org, who))
        assert (
            browser.call("POST", "/api/studio/clients", {"name": f"{who} client"})[0]
            == 201
        ), who
    auditor = sign_in(cloud, org.auditor)
    status, listing = auditor.call("GET", "/api/studio/clients")
    assert status == 200 and len(listing["clients"]) == 3
    assert auditor.call("POST", "/api/studio/clients", {"name": "No"})[0] == 403
    assert sign_in(cloud, org.candidate).call("GET", "/api/studio/clients")[0] == 403


def test_hosted_public_invoice_for_an_anonymous_client(cloud):
    kit.forget_schema()
    org = build_org(cloud)
    cloud.billing.subscribe_app(org.owner, "studio")
    owner = sign_in(cloud, org.owner)
    owner.call("POST", "/api/suite/business", {"name": "Acme Agency"})
    client = owner.call(
        "POST", "/api/studio/clients", {"name": "Lee Chen", "email": "lee@example.com"}
    )[1]["client"]
    invoice = owner.call(
        "POST",
        "/api/studio/invoices",
        {
            "client_id": client["id"],
            "items": [{"description": "Audit", "unit_cents": 75000}],
        },
    )[1]["invoice"]
    status, shared = owner.call(
        "POST", f"/api/studio/invoices/{invoice['id']}/link", {}
    )
    assert status == 200 and shared["invoice"]["status"] == "sent"
    visitor = Browser(cloud)
    status, page = visitor.call("GET", f"/api/public/{shared['link']['token']}")
    assert (
        status == 200
        and page["page"]["number"] == invoice["number"]
        and page["business"]["name"] == "Acme Agency"
    )
    status, _ = visitor.call(
        "POST",
        f"/api/public/{shared['link']['token']}/notify_paid",
        {"reference": "bank 42"},
    )
    assert status == 200
    status, paid = owner.call(
        "POST", f"/api/studio/invoices/{invoice['id']}/payments", {}
    )
    assert status == 201 and paid["invoice"]["status"] == "paid"
    assert owner.call("GET", "/api/suite/home")[1]["revenue"]["total_cents"] == 75000

    other = build_org(cloud, "Other Org", slug_email="other.test")
    cloud.billing.subscribe_app(other.owner, "studio")
    other_owner = sign_in(cloud, other.owner)
    assert other_owner.call("GET", "/api/studio/clients")[1]["clients"] == []
    assert other_owner.call("GET", f"/api/studio/invoices/{invoice['id']}")[0] == 404
