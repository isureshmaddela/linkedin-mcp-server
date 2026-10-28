"""Sales (aira/suite/studio): getting paid. Deposits on proposals, what Sales does by itself (retainer invoices,
reminders for late invoices, proposal follow-ups), the earnings report, and regressions found using the app.

Unit tests run on a scratch SQLite file; the journeys use the real single-user request handler and the hosted
gateway, with an anonymous client on the public pages.
"""

from __future__ import annotations

import json
import sqlite3
import urllib.error
import urllib.request

import pytest
from cloud_support import build_org, sign_in

import auto_apply_assistant as local
import test_careers
from aira import suite
from aira.suite import background, kit, public
from aira.suite.studio import sales, service, store
from aira.suite.studio import studio_routes  # noqa: F401 - registers the routes and the background task

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
    connection = sqlite3.connect(tmp_path / "earn.sqlite3")
    connection.row_factory = sqlite3.Row
    store.ensure(connection)
    yield connection
    connection.close()


@pytest.fixture
def local_index():
    public.set_index(local._public_link_index)


def _client(db, name="Maya Patel", email="maya@bloom.example", **extra):
    return service.create_client(
        db, {"name": name, "company": f"{name} Co", "email": email, **extra}
    )


def _invoice(db, client, cents=100000, **extra):
    return service.create_invoice(
        db,
        {
            "client_id": client["id"],
            "items": [{"description": "Work", "quantity": 1, "unit_cents": cents}],
            **extra,
        },
        today=TODAY,
    )


def _proposal(db, client, **extra):
    return service.create_proposal(
        db,
        {
            "client_id": client["id"],
            "title": "Website refresh",
            "items": [{"description": "Design", "quantity": 2, "unit_cents": 100000}],
            "discount_cents": 20000,
            **extra,
        },
        today=TODAY,
    )


def _outbox(db, kind=""):
    return [m for m in kit.outbox(db, app="studio") if not kind or m["kind"] == kind]


# ---------------------------------------------------------------------------
# regressions found using the app
# ---------------------------------------------------------------------------


def test_deleting_a_linked_proposal_invoice_or_project_leaves_the_deal_usable(db):
    client = _client(db)
    proposal = _proposal(db, client)
    deal = sales.create_deal(
        db,
        {
            "title": "Website",
            "client_id": client["id"],
            "value_cents": 180000,
            "proposal_id": proposal["id"],
        },
    )
    service.delete_proposal(db, proposal["id"])
    # editing the deal used to answer "proposal not found"
    edited = sales.update_deal(db, deal["id"], {"next_step": "Call on Friday"})
    assert edited["next_step"] == "Call on Friday" and edited["proposal_id"] == ""

    sales.move_deal(db, deal["id"], "won")
    made = sales.convert_deal(db, deal["id"], today=TODAY)
    service.delete_invoice(db, made["invoice"]["id"])  # a draft can be deleted
    again = sales.convert_deal(
        db, deal["id"], today=TODAY
    )  # used to fail: invoice not found
    assert again["invoice"]["id"] != made["invoice"]["id"]
    assert again["project"]["id"] == made["project"]["id"]

    service.void_invoice(db, again["invoice"]["id"], today=TODAY)
    service.delete_project(db, again["project"]["id"])
    third = sales.convert_deal(
        db, deal["id"], today=TODAY
    )  # used to fail: project not found
    assert (
        third["invoice"]["status"] == "draft" and third["project"]["name"] == "Website"
    )


def test_a_proposal_whose_invoice_was_deleted_can_be_started_again(db):
    client = _client(db)
    proposal = _proposal(db, client)
    service.share_proposal(db, proposal["id"], today=TODAY)
    service.set_proposal_status(db, proposal["id"], "accepted", today=TODAY)
    made = service.convert_proposal(db, proposal["id"], today=TODAY)
    assert service.get_proposal(db, proposal["id"], TODAY)["converted"] is True
    service.delete_invoice(db, made["invoice"]["id"])
    # it used to stay "converted", hiding the button and offering an invoice that no longer exists
    assert service.get_proposal(db, proposal["id"], TODAY)["converted"] is False
    again = service.convert_proposal(db, proposal["id"], today=TODAY)
    assert again["project"]["id"] == made["project"]["id"]
    assert again["invoice"]["total_cents"] == made["invoice"]["total_cents"]


def test_resuming_a_paused_retainer_does_not_bill_the_paused_months(db):
    client = _client(db)
    retainer = service.create_retainer(
        db,
        {
            "client_id": client["id"],
            "title": "SEO",
            "amount_cents": 50000,
            "next_date": "2026-05-31",
        },
        today=TODAY,
    )
    service.update_retainer(db, retainer["id"], {"active": False}, today=TODAY)
    resumed = service.update_retainer(db, retainer["id"], {"active": True}, today=TODAY)
    assert (
        resumed["next_date"] == "2026-10-31"
    )  # the anchor day is kept, the paused months are skipped
    assert service.run_retainers(db, today=TODAY) == []


def test_hours_must_be_a_real_number(db):
    client = _client(db)
    project = service.create_project(db, {"client_id": client["id"], "name": "Logo"})
    for bad in ("NaN", "Infinity", "abc", "-1", "30"):
        with pytest.raises(ValueError):
            service.add_time(db, project["id"], {"hours": bad}, today=TODAY)
    entry = service.add_time(db, project["id"], {"hours": "1,5"}, today=TODAY)
    assert entry["minutes"] == 90


# ---------------------------------------------------------------------------
# deposits
# ---------------------------------------------------------------------------


def test_a_deposit_is_invoiced_on_acceptance_and_taken_off_the_final_invoice(db):
    kit.save_business(db, {"name": "Northlight", "tax_rate_bp": 1000})
    client = _client(db)
    proposal = _proposal(db, client, deposit_percent="30")
    # 2 x 1,000.00 - 200.00 = 1,800.00 + 10% tax = 1,980.00; the deposit is 30% of 1,800.00 plus its tax
    assert proposal["total_cents"] == 198000
    assert proposal["deposit"] == {
        "deposit_bp": 3000,
        "net_cents": 54000,
        "tax_cents": 5400,
        "total_cents": 59400,
    }
    service.share_proposal(db, proposal["id"], today=TODAY)
    _subject, body = service.proposal_email(
        db, service.get_proposal(db, proposal["id"], TODAY), "https://x/p/1"
    )
    assert "A deposit of 30% ($594.00) is due when you accept" in body

    service.accept_proposal(
        db,
        proposal["id"],
        name="Maya Patel",
        visitor={"client": "1.2.3.4"},
        today=TODAY,
    )
    accepted = service.get_proposal(db, proposal["id"], TODAY)
    deposit = service.get_invoice(db, accepted["deposit_invoice_id"], TODAY)
    assert (deposit["kind"], deposit["status"], deposit["total_cents"]) == (
        "deposit",
        "sent",
        59400,
    )
    assert (
        deposit["due_date"] == TODAY
        and "Deposit (30%)" in deposit["items"][0]["description"]
    )
    assert [m["recipient"] for m in _outbox(db, "invoice")] == ["maya@bloom.example"]
    page = service.public_proposal(db, proposal["id"], TODAY)
    assert page["deposit_invoice"]["number"] == deposit["number"]
    assert page["deposit_invoice"]["path"].startswith("/p/")
    assert page["deposit_invoice"]["balance_cents"] == 59400
    # made once
    assert (
        service.deposit_invoice(db, proposal["id"], today=TODAY)["id"] == deposit["id"]
    )

    final = service.convert_proposal(db, proposal["id"], today=TODAY)["invoice"]
    assert final["credit_cents"] == 59400 and deposit["number"] in final["credit_label"]
    assert final["total_cents"] + deposit["total_cents"] == proposal["total_cents"]
    assert service.public_invoice(db, final["id"], TODAY)["credit_cents"] == 59400
    # editing the final invoice keeps the deposit off
    edited = service.update_invoice(
        db, final["id"], {"discount_cents": 20000}, today=TODAY
    )
    assert edited["credit_cents"] == 59400 and edited["total_cents"] == 138600


def test_a_void_deposit_is_not_taken_off_and_no_deposit_means_no_invoice(db):
    client = _client(db)
    plain = _proposal(db, client)
    service.share_proposal(db, plain["id"], today=TODAY)
    service.set_proposal_status(db, plain["id"], "accepted", today=TODAY)
    assert service.get_proposal(db, plain["id"], TODAY)["deposit_invoice_id"] == ""

    service.save_settings(db, {"default_deposit_percent": 50})
    with_deposit = _proposal(db, client)
    assert with_deposit["deposit_bp"] == 5000  # the default for new proposals
    service.share_proposal(db, with_deposit["id"], today=TODAY)
    # marking it accepted by hand also sends the deposit invoice
    service.set_proposal_status(db, with_deposit["id"], "accepted", today=TODAY)
    deposit_id = service.get_proposal(db, with_deposit["id"], TODAY)[
        "deposit_invoice_id"
    ]
    assert deposit_id
    service.void_invoice(db, deposit_id, today=TODAY)
    final = service.convert_proposal(db, with_deposit["id"], today=TODAY)["invoice"]
    assert (
        final["credit_cents"] == 0
        and final["total_cents"] == with_deposit["total_cents"]
    )

    for bad in ("150", "-5", "abc"):
        with pytest.raises(ValueError):
            service.deposit_bp(bad)
    assert service.deposit_bp("12.5%") == 1250 and service.deposit_bp("") == 0


# ---------------------------------------------------------------------------
# what Sales does by itself
# ---------------------------------------------------------------------------


def test_retainer_invoices_are_drafted_and_sent_by_themselves(db):
    client = _client(db)
    service.create_retainer(
        db,
        {
            "client_id": client["id"],
            "title": "Care plan",
            "amount_cents": 90000,
            "next_date": "2026-10-01",
        },
        today=TODAY,
    )
    service.save_settings(db, {"retainer_auto_send": True})
    assert service.tick(db, today=TODAY)["retainer_invoices"] == 1
    invoice = service.list_invoices(db, today=TODAY)[0]
    assert invoice["status"] in ("sent", "overdue") and invoice["retainer_id"]
    assert len(_outbox(db, "invoice")) == 1
    assert service.tick(db, today=TODAY)["retainer_invoices"] == 0  # never twice

    service.save_settings(db, {"auto_retainers": False})
    assert service.tick(db, today="2026-11-02")["retainer_invoices"] == 0


def test_late_invoices_get_reminders_on_a_schedule_and_up_to_a_limit(db):
    client = _client(db)
    quiet = _client(db, name="No Email", email="")
    late = _invoice(db, client, issue_date="2026-09-01", due_date="2026-09-15")
    service.share_invoice(db, late["id"], today=TODAY)
    unreachable = _invoice(db, quiet, issue_date="2026-09-01", due_date="2026-09-15")
    service.share_invoice(db, unreachable["id"], today=TODAY)
    barely = _invoice(db, client, issue_date="2026-10-01", due_date="2026-10-23")
    service.share_invoice(db, barely["id"], today=TODAY)
    settled = _invoice(db, client, issue_date="2026-09-01", due_date="2026-09-15")
    service.add_payment(db, settled["id"], {}, today=TODAY)  # paid: never reminded

    assert service.tick(db, today=TODAY)["reminders"] == 0  # off until turned on
    service.save_settings(
        db,
        {
            "auto_reminders": True,
            "reminder_after_days": 3,
            "reminder_every_days": 7,
            "reminder_max": 2,
        },
    )
    # only `late`: `barely` is 2 days late (too soon) and `unreachable` has no email address
    assert service.tick(db, today=TODAY)["reminders"] == 1
    reminded = service.get_invoice(db, late["id"], TODAY)
    assert reminded["reminder_count"] == 1 and reminded["reminded_at"]
    assert _outbox(db, "invoice_reminder")[0]["subject"].startswith("Reminder: invoice")
    assert service.get_invoice(db, unreachable["id"], TODAY)["reminder_count"] == 0
    stamp = reminded["reminded_at"][:10]  # reminders are stamped with the real time
    soon = service.add_days(stamp, 3)
    assert service.tick(db, today=soon)["reminders"] == 1  # `barely` is late enough now
    assert service.tick(db, today=soon)["reminders"] == 0  # nothing twice in a row
    week_on = service.add_days(stamp, 8)
    assert service.tick(db, today=week_on)["reminders"] == 2  # a week on, both again
    assert service.get_invoice(db, late["id"], TODAY)["reminder_count"] == 2
    assert (
        service.tick(db, today=service.add_days(stamp, 30))["reminders"] == 0
    )  # limit
    assert len(_outbox(db, "invoice_reminder")) == 4
    assert all(
        m["status"] == "pending" for m in _outbox(db)
    )  # every email waits for approval


def test_proposals_with_no_answer_get_one_follow_up(db):
    client = _client(db)
    proposal = _proposal(db, client)
    service.share_proposal(db, proposal["id"], today=TODAY)
    sent_on = service.get_proposal(db, proposal["id"], TODAY)["sent_at"][:10]
    service.save_settings(db, {"proposal_followup_days": 5})
    assert service.tick(db, today=service.add_days(sent_on, 4))["followups"] == 0
    assert service.tick(db, today=service.add_days(sent_on, 5))["followups"] == 1
    assert service.tick(db, today=service.add_days(sent_on, 9))["followups"] == 0
    message = _outbox(db, "proposal_followup")[0]
    assert (
        message["subject"] == "Following up: Website refresh"
        and "/p/" in message["body"]
    )


def test_the_background_task_leaves_accounts_that_never_used_sales_alone(tmp_path, db):
    kit.forget_schema()
    fresh = sqlite3.connect(tmp_path / "fresh.sqlite3")
    fresh.row_factory = sqlite3.Row
    kit.ensure_core(fresh)
    assert background.TASKS["studio"](fresh) == "unused"
    assert not fresh.execute(
        "SELECT 1 FROM sqlite_master WHERE name = 'st_clients'"
    ).fetchone()
    fresh.close()
    assert set(background.TASKS["studio"](db)) == {
        "retainer_invoices",
        "reminders",
        "followups",
        "no_email",
    }


def test_automation_settings_are_checked(db):
    for bad in (
        {"reminder_max": 50},
        {"reminder_every_days": 0},
        {"proposal_followup_days": -1},
        {"default_deposit_percent": 101},
    ):
        with pytest.raises(ValueError):
            service.save_settings(db, bad)
    saved = service.save_settings(db, {"auto_reminders": "true", "reminder_max": "4"})
    assert saved["auto_reminders"] is True and saved["reminder_max"] == 4
    assert saved["auto_retainers"] is True  # on unless turned off


# ---------------------------------------------------------------------------
# the earnings report
# ---------------------------------------------------------------------------


def test_the_earnings_report_ranks_clients_and_work(db):
    best = _client(db, name="Best Client")
    small = _client(db, name="Small Client")
    gone = _client(db, name="Gone Client")
    project = service.create_project(db, {"client_id": best["id"], "name": "Shop"})
    service.add_time(db, project["id"], {"hours": "10", "rate_cents": 0}, today=TODAY)
    big = _invoice(db, best, 100000, issue_date="2026-08-01", project_id=project["id"])
    service.add_payment(
        db, big["id"], {"amount_cents": 60000, "day": "2026-08-10"}, today=TODAY
    )
    service.add_payment(
        db, big["id"], {"amount_cents": 40000, "day": "2026-09-05"}, today=TODAY
    )
    part = _invoice(db, small, 50000, issue_date="2026-09-01", due_date="2026-09-15")
    service.add_payment(
        db, part["id"], {"amount_cents": 20000, "day": "2026-10-01"}, today=TODAY
    )
    voided = _invoice(db, gone, 10000)
    service.add_payment(db, voided["id"], {"day": "2026-10-02"}, today=TODAY)
    service.void_invoice(db, voided["id"], today=TODAY)
    upcoming = _invoice(db, small, 7000, due_date="2026-11-30")
    service.share_invoice(db, upcoming["id"], today=TODAY)

    report = service.reports(db, today=TODAY, months=12)
    assert [m["month"] for m in report["months"]][-3:] == [
        "2026-08",
        "2026-09",
        "2026-10",
    ]
    assert len(report["months"]) == 12
    by_month = {m["month"]: m["received_cents"] for m in report["months"]}
    assert (by_month["2026-08"], by_month["2026-09"], by_month["2026-10"]) == (
        60000,
        40000,
        20000,
    )
    assert (
        report["received_cents"] == 120000
    )  # the void invoice's payment does not count
    assert report["best_month"]["month"] == "2026-08"
    assert [c["name"] for c in report["clients"]] == ["Best Client", "Small Client"]
    assert [c["share"] for c in report["clients"]] == [83, 17]
    assert report["clients"][1]["outstanding_cents"] == 37000
    aging = {bucket["key"]: bucket for bucket in report["aging"]}
    assert aging["31_60"]["cents"] == 30000 and aging["current"]["cents"] == 7000
    assert report["outstanding_cents"] == 37000
    assert report["average_days_to_pay"] == 35  # 1 Aug to 5 Sep
    assert report["projects"][0]["name"] == "Shop"
    assert report["projects"][0]["hourly_cents"] == 10000  # 1,000.00 over 10 hours
    assert report["methods"] == [{"method": "bank_transfer", "cents": 120000}]
    assert service.reports(db, today=TODAY, months=1)["received_cents"] == 20000


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


def _csv(env, api, path):
    request = urllib.request.Request(
        env.base + path, headers={"X-Requested-With": "aira"}
    )
    with api.opener.open(request, timeout=30) as response:
        return response.read().decode("utf-8")


def test_local_deposit_paid_from_the_proposal_page_and_the_report(
    env, api, local_index
):
    kit.forget_schema()
    api.post(
        "/api/suite/business",
        {"name": "Northlight", "payment_link": "https://pay.example/northlight"},
    )
    client = api.post(
        "/api/studio/clients",
        {"name": "Maya Patel", "email": "maya@bloom.example"},
        expect=201,
    )["client"]
    proposal = api.post(
        "/api/studio/proposals",
        {
            "client_id": client["id"],
            "title": "Shop",
            "deposit_percent": "50",
            "items": [{"description": "Build", "quantity": 1, "unit_cents": 400000}],
        },
        expect=201,
    )["proposal"]
    assert proposal["deposit"]["total_cents"] == 200000
    token = api.post(f"/api/studio/proposals/{proposal['id']}/send", {})["link"][
        "token"
    ]
    status, page = _anonymous(env, "GET", f"/api/public/{token}")
    assert status == 200 and page["page"]["deposit_cents"] == 200000
    assert page["page"]["deposit_invoice"] is None
    status, answer = _anonymous(
        env,
        "POST",
        f"/api/public/{token}/accept",
        {"name": "Maya Patel", "agree": True},
    )
    assert status == 200, answer
    deposit = answer["proposal"]["deposit_invoice"]
    assert deposit["balance_cents"] == 200000 and deposit["path"].startswith("/p/")
    status, invoice_page = _anonymous(env, "GET", "/api/public/" + deposit["path"][3:])
    assert status == 200 and invoice_page["page"]["deposit"] is True
    assert invoice_page["page"]["balance_cents"] == 200000

    detail = api.get(f"/api/studio/proposals/{proposal['id']}")["proposal"]
    paid = api.post(
        f"/api/studio/invoices/{detail['deposit_invoice_id']}/payments", {}, expect=201
    )
    assert paid["invoice"]["status"] == "paid"
    report = api.get("/api/studio/reports?months=3")
    assert (
        report["received_cents"] == 200000
        and report["clients"][0]["name"] == "Maya Patel"
    )
    csv_text = _csv(env, api, "/api/studio/payments/export")
    assert csv_text.startswith("day,invoice,client_name") and "2000.00" in csv_text
    final = api.post(f"/api/studio/proposals/{proposal['id']}/convert", {})["invoice"]
    assert final["total_cents"] == 200000 and final["credit_cents"] == 200000

    done = api.post("/api/studio/automation/run", {})["done"]
    assert set(done) == {"retainer_invoices", "reminders", "followups", "no_email"}
    status, body = api.call("POST", "/api/studio/settings", {"reminder_max": 50})
    assert status == 400 and "reminders" in body["error"]


def test_local_exports_use_amounts_and_follow_the_search(env, api):
    kit.forget_schema()
    client = api.post(
        "/api/studio/clients", {"name": "Lee Chen", "company": "Chen Law"}, expect=201
    )["client"]
    api.post(
        "/api/studio/deals",
        {"title": "Retainer", "client_id": client["id"], "value_cents": 400050},
        expect=201,
    )
    api.post(
        "/api/studio/deals",
        {"title": "Other work", "client_id": client["id"], "value_cents": 100},
        expect=201,
    )
    deals = _csv(env, api, "/api/studio/deals/export?q=retainer")
    assert "4000.50" in deals and "Other work" not in deals
    assert deals.splitlines()[0].startswith(
        "title,client_name,contact_name,stage,probability,value,weighted"
    )
    for amount in (1000, 2000):
        api.post(
            "/api/studio/invoices",
            {
                "client_id": client["id"],
                "items": [{"description": f"Work {amount}", "unit_cents": amount}],
            },
            expect=201,
        )
    invoices = _csv(env, api, "/api/studio/invoices/export?q=INV-0002")
    assert "INV-0002" in invoices and "INV-0001" not in invoices


# ---------------------------------------------------------------------------
# the hosted build
# ---------------------------------------------------------------------------


def test_hosted_report_and_automation_follow_roles(cloud):
    kit.forget_schema()
    org = build_org(cloud)
    owner = sign_in(cloud, org.owner)
    status, body = owner.call("GET", "/api/studio/reports")
    assert status == 402 and body["code"] == "app_required"
    cloud.billing.subscribe_app(org.owner, "studio")
    assert owner.call("GET", "/api/studio/reports")[0] == 200
    assert owner.call("POST", "/api/studio/automation/run", {})[0] == 200
    auditor = sign_in(cloud, org.auditor)
    assert auditor.call("GET", "/api/studio/reports")[0] == 200
    assert auditor.call("POST", "/api/studio/automation/run", {})[0] == 403
    assert sign_in(cloud, org.candidate).call("GET", "/api/studio/reports")[0] == 403
