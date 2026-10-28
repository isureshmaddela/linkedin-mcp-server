"""Sales (aira/suite/studio): regressions from the tester's last round. Deposits that are voided after the final
invoice exists, voided final invoices, two answers to one proposal at the same moment, what each project earned
per hour with a deposit, household profiles that are not grown-ups, reminders for clients with no email address,
decimal default deposits and the payments export's end date.

Unit tests run on a scratch SQLite file (threads get their own connections, like requests do); the journeys use
the real single-user request handler with an anonymous client on the public pages.
"""

from __future__ import annotations

import json
import sqlite3
import threading
import urllib.error
import urllib.request

import pytest

import auto_apply_assistant as local
import test_careers
from aira import suite
from aira.suite import kit, public
from aira.suite.studio import service, store
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


def _connect(path):
    connection = sqlite3.connect(path, timeout=20)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA busy_timeout = 20000")
    return connection


@pytest.fixture
def db_path(tmp_path):
    kit.forget_schema()
    public.set_index(public._MemoryIndex())
    path = tmp_path / "fixes.sqlite3"
    connection = _connect(path)
    connection.execute("PRAGMA journal_mode = WAL")
    store.ensure(connection)
    connection.close()
    return path


@pytest.fixture
def db(db_path):
    connection = _connect(db_path)
    store.ensure(connection)
    yield connection
    connection.close()


def _client(db, name="Maya Patel", email="maya@bloom.example"):
    return service.create_client(
        db, {"name": name, "company": f"{name} Co", "email": email}
    )


def _proposal(db, client, cents=1000000, deposit="30", tax_bp=1000):
    return service.create_proposal(
        db,
        {
            "client_id": client["id"],
            "title": "Website refresh",
            "items": [{"description": "Build", "quantity": 1, "unit_cents": cents}],
            "tax_rate_bp": tax_bp,
            "deposit_percent": deposit,
        },
        today=TODAY,
    )


def _accepted(db, client, **extra):
    proposal = _proposal(db, client, **extra)
    service.share_proposal(db, proposal["id"], today=TODAY)
    return service.accept_proposal(
        db,
        proposal["id"],
        name="Maya Patel",
        visitor={"client": "203.0.113.9"},
        today=TODAY,
    )


def _outbox(db, kind=""):
    return [m for m in kit.outbox(db, app="studio") if not kind or m["kind"] == kind]


# ---------------------------------------------------------------------------
# voiding a deposit after the final invoice exists
# ---------------------------------------------------------------------------


def test_voiding_the_deposit_puts_it_back_on_the_final_invoice(db):
    proposal = _accepted(db, _client(db))  # $10,000 + 10% tax, 30% deposit
    deposit_id = proposal["deposit_invoice_id"]
    assert service.get_invoice(db, deposit_id, TODAY)["total_cents"] == 330000
    final = service.convert_proposal(db, proposal["id"], today=TODAY)["invoice"]
    assert final["total_cents"] == 770000 and final["credit_cents"] == 330000
    assert final["credit_invoice_id"] == deposit_id

    service.void_invoice(db, deposit_id, today=TODAY)
    # the client never paid the deposit: the final invoice used to keep taking it off ($3,300 undercharged)
    final = service.get_invoice(db, final["id"], TODAY)
    assert final["total_cents"] == 1100000 and final["credit_cents"] == 0
    assert final["credit_label"] == "" and final["balance_cents"] == 1100000
    assert any(
        "no longer takes off deposit" in row["title"]
        for row in kit.fetch_all(
            db, "SELECT title FROM suite_activity WHERE ref_id = ?", (final["id"],)
        )
    )
    # voiding it again changes nothing
    service.void_invoice(db, deposit_id, today=TODAY)
    assert service.get_invoice(db, final["id"], TODAY)["total_cents"] == 1100000


def test_a_paid_final_invoice_owes_the_deposit_again_when_the_deposit_is_voided(db):
    proposal = _accepted(db, _client(db), cents=300000, deposit="20", tax_bp=0)
    final = service.convert_proposal(db, proposal["id"], today=TODAY)["invoice"]
    assert final["total_cents"] == 240000
    service.add_payment(db, final["id"], {}, today=TODAY)
    service.void_invoice(db, proposal["deposit_invoice_id"], today=TODAY)
    final = service.get_invoice(db, final["id"], TODAY)
    assert final["status"] == "partially_paid" and final["balance_cents"] == 60000


def test_a_final_invoice_made_before_this_fix_finds_its_deposit(db_path, db):
    proposal = _accepted(db, _client(db))
    final = service.convert_proposal(db, proposal["id"], today=TODAY)["invoice"]
    # an older Studio did not remember which deposit was taken off
    db.execute(
        "UPDATE st_invoices SET credit_invoice_id = '' WHERE id = ?", (final["id"],)
    )
    db.commit()
    kit.forget_schema()
    store.ensure(db)
    assert (
        service.get_invoice(db, final["id"], TODAY)["credit_invoice_id"]
        == proposal["deposit_invoice_id"]
    )
    service.void_invoice(db, proposal["deposit_invoice_id"], today=TODAY)
    assert service.get_invoice(db, final["id"], TODAY)["total_cents"] == 1100000


# ---------------------------------------------------------------------------
# a voided final invoice
# ---------------------------------------------------------------------------


def test_a_proposal_whose_final_invoice_was_voided_can_be_started_again(db):
    proposal = _accepted(db, _client(db))
    first = service.convert_proposal(db, proposal["id"], today=TODAY)
    service.share_invoice(db, first["invoice"]["id"], today=TODAY)
    service.void_invoice(db, first["invoice"]["id"], today=TODAY)
    # it used to stay "converted" and converting again answered the void invoice
    assert service.get_proposal(db, proposal["id"], TODAY)["converted"] is False
    again = service.convert_proposal(db, proposal["id"], today=TODAY)
    assert again["invoice"]["id"] != first["invoice"]["id"]
    assert again["invoice"]["status"] == "draft"
    assert again["project"]["id"] == first["project"]["id"]  # the same project
    assert again["invoice"]["credit_cents"] == 330000  # the deposit is still taken off
    assert service.get_proposal(db, proposal["id"], TODAY)["converted"] is True


def test_an_old_proposal_still_pointing_at_a_void_invoice_is_freed(db):
    proposal = _accepted(db, _client(db))
    first = service.convert_proposal(db, proposal["id"], today=TODAY)
    service.void_invoice(db, first["invoice"]["id"], today=TODAY)
    db.execute(  # what an older Studio left behind
        "UPDATE st_proposals SET invoice_id = ? WHERE id = ?",
        (first["invoice"]["id"], proposal["id"]),
    )
    db.commit()
    again = service.convert_proposal(db, proposal["id"], today=TODAY)
    assert again["invoice"]["status"] == "draft"
    db.execute(
        "UPDATE st_proposals SET invoice_id = ? WHERE id = ?",
        (first["invoice"]["id"], proposal["id"]),
    )
    db.commit()
    kit.forget_schema()
    store.ensure(db)  # the one-time repair
    assert service.get_proposal(db, proposal["id"], TODAY)["converted"] is False


# ---------------------------------------------------------------------------
# two answers at the same moment
# ---------------------------------------------------------------------------


def _together(db_path, count, work):
    """Run ``work(connection)`` in ``count`` threads at once, each with its own connection, like requests."""
    barrier = threading.Barrier(count)
    results: list[tuple[str, object]] = []
    lock = threading.Lock()

    def run():
        connection = _connect(db_path)
        try:
            barrier.wait()
            outcome = ("ok", work(connection))
        except Exception as error:  # noqa: BLE001 - the test looks at every outcome
            outcome = ("error", error)
        finally:
            connection.close()
        with lock:
            results.append(outcome)

    threads = [threading.Thread(target=run) for _ in range(count)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(60)
    return results


def test_accepting_a_proposal_four_times_at_once_makes_one_deposit(db_path, db):
    client = _client(db)
    for trial in range(6):
        proposal = _proposal(db, client, cents=100000, deposit="20", tax_bp=0)
        service.share_proposal(db, proposal["id"], today=TODAY)

        def accept(connection, proposal_id=proposal["id"]):
            return service.accept_proposal(
                connection,
                proposal_id,
                name="Maya Patel",
                visitor={},
                today=TODAY,
            )

        results = _together(db_path, 4, accept)
        wins = [value for kind, value in results if kind == "ok"]
        refusals = [value for kind, value in results if kind == "error"]
        assert len(wins) == 1, (trial, results)
        assert all(
            isinstance(error, ValueError) and "already accepted" in str(error)
            for error in refusals
        ), refusals
        deposits = kit.fetch_all(
            db,
            "SELECT number FROM st_invoices WHERE proposal_id = ? AND kind = 'deposit'",
            (proposal["id"],),
        )
        assert len(deposits) == 1, (trial, deposits)
        logged = kit.count(
            db,
            "SELECT COUNT(*) FROM suite_activity WHERE kind = 'proposal_accepted' AND ref_id = ?",
            (proposal["id"],),
        )
        assert logged == 1
    assert len(_outbox(db, "invoice")) == 6  # one deposit email for each proposal
    numbers = [
        row["number"] for row in kit.fetch_all(db, "SELECT number FROM st_invoices")
    ]
    assert len(numbers) == len(set(numbers)) == 6


def test_marking_accepted_while_the_client_accepts_makes_one_deposit(db_path, db):
    proposal = _proposal(db, _client(db), cents=100000, deposit="50", tax_bp=0)
    service.share_proposal(db, proposal["id"], today=TODAY)
    works = iter(["client", "business", "client", "business"])
    lock = threading.Lock()

    def answer(connection):
        with lock:
            who = next(works)
        if who == "client":
            return service.accept_proposal(
                connection, proposal["id"], name="Maya Patel", visitor={}, today=TODAY
            )
        return service.set_proposal_status(
            connection, proposal["id"], "accepted", today=TODAY
        )

    _together(db_path, 4, answer)
    assert service.get_proposal(db, proposal["id"], TODAY)["status"] == "accepted"
    assert (
        kit.count(
            db,
            "SELECT COUNT(*) FROM st_invoices WHERE proposal_id = ? AND kind = 'deposit'",
            (proposal["id"],),
        )
        == 1
    )
    # marking an accepted proposal accepted again does nothing
    service.set_proposal_status(db, proposal["id"], "accepted", today=TODAY)
    assert len(_outbox(db, "invoice")) == 1


def test_a_decline_cannot_overwrite_an_acceptance(db):
    proposal = _accepted(db, _client(db))
    with pytest.raises(ValueError, match="already accepted"):
        service.decline_proposal(db, proposal["id"], reason="Too much", today=TODAY)
    assert service.get_proposal(db, proposal["id"], TODAY)["status"] == "accepted"


def test_invoices_made_at_the_same_moment_get_different_numbers(db_path, db):
    client = _client(db)

    def make(connection):
        return service.create_invoice(
            connection,
            {
                "client_id": client["id"],
                "items": [{"description": "Work", "quantity": 1, "unit_cents": 1000}],
            },
            today=TODAY,
        )["number"]

    results = _together(db_path, 6, make)
    numbers = [value for kind, value in results if kind == "ok"]
    assert len(numbers) == 6, results
    assert sorted(numbers) == [f"INV-{n:04d}" for n in range(1, 7)]


def test_a_database_that_already_repeated_a_number_keeps_working(tmp_path):
    kit.forget_schema()
    public.set_index(public._MemoryIndex())
    connection = _connect(tmp_path / "old.sqlite3")
    kit.ensure_core(connection)
    kit.ensure(connection, "studio.v1", store.SCHEMA)
    store._add_columns(connection)
    connection.execute("DROP INDEX st_invoices_seq")
    client = _client(connection)
    first = service.create_invoice(
        connection,
        {"client_id": client["id"], "items": [{"description": "A", "unit_cents": 100}]},
        today=TODAY,
    )
    connection.execute(  # a second INV-0001, as an older Studio could make
        "INSERT INTO st_invoices (id, number, seq, client_id, issue_date, due_date, created_at, updated_at) "
        "VALUES ('x', 'INV-0001', 1, ?, ?, ?, ?, ?)",
        (client["id"], TODAY, TODAY, TODAY, TODAY),
    )
    connection.commit()
    kit.forget_schema()
    store.ensure(connection)  # the unique index cannot be made; nothing breaks
    nxt = service.create_invoice(
        connection,
        {"client_id": client["id"], "items": [{"description": "B", "unit_cents": 100}]},
        today=TODAY,
    )
    assert first["number"] == "INV-0001" and nxt["number"] == "INV-0002"
    connection.close()


# ---------------------------------------------------------------------------
# which work pays best, with a deposit
# ---------------------------------------------------------------------------


def test_a_projects_earnings_include_its_deposit(db):
    proposal = _accepted(db, _client(db), cents=300000, deposit="20", tax_bp=0)
    project = service.convert_proposal(db, proposal["id"], today=TODAY)["project"]
    service.add_time(db, project["id"], {"hours": "4", "day": TODAY}, today=TODAY)
    row = next(
        p
        for p in service.reports(db, today=TODAY)["projects"]
        if p["id"] == project["id"]
    )
    # it used to show $2,400 and $600 an hour: the $600 deposit was left out
    assert row["invoiced_cents"] == 300000 and row["hourly_cents"] == 75000
    assert service.get_project(db, project["id"])["invoiced_cents"] == 300000
    detail = service.project_detail(db, project["id"], TODAY)
    assert {i["kind"] for i in detail["invoices"]} == {"deposit", ""}


def test_a_deposit_from_before_this_fix_joins_its_project(db):
    proposal = _accepted(db, _client(db), cents=1000000, deposit="50", tax_bp=0)
    project = service.convert_proposal(db, proposal["id"], today=TODAY)["project"]
    db.execute(
        "UPDATE st_invoices SET project_id = '' WHERE id = ?",
        (proposal["deposit_invoice_id"],),
    )
    db.commit()
    kit.forget_schema()
    store.ensure(db)
    service.add_time(db, project["id"], {"hours": "10", "day": TODAY}, today=TODAY)
    row = next(
        p
        for p in service.reports(db, today=TODAY)["projects"]
        if p["id"] == project["id"]
    )
    assert row["hourly_cents"] == 100000  # $1,000 an hour, not $500


# ---------------------------------------------------------------------------
# reminders for clients with no email address
# ---------------------------------------------------------------------------


def test_late_invoices_of_clients_with_no_email_are_counted_not_skipped(db):
    client = _client(db, name="Sam Ortiz", email="")
    invoice = service.create_invoice(
        db,
        {
            "client_id": client["id"],
            "issue_date": "2026-06-01",
            "due_date": "2026-06-15",
            "items": [{"description": "Work", "unit_cents": 20000}],
        },
        today=TODAY,
    )
    service.share_invoice(db, invoice["id"], today=TODAY)
    service.save_settings(db, {"auto_reminders": True})
    before = service.get_invoice(db, invoice["id"], TODAY)["updated_at"]
    done = service.tick(db, today=TODAY)
    # it used to say "Nothing needed doing right now." and share the invoice again on every pass
    assert done == {
        "retainer_invoices": 0,
        "reminders": 0,
        "followups": 0,
        "no_email": 1,
    }
    assert service.get_invoice(db, invoice["id"], TODAY)["updated_at"] == before
    assert not _outbox(db)

    service.save_settings(db, {"proposal_followup_days": 3})
    proposal = service.create_proposal(
        db,
        {
            "client_id": client["id"],
            "title": "Audit",
            "items": [{"description": "Audit", "unit_cents": 10000}],
        },
        today=TODAY,
    )
    service.share_proposal(db, proposal["id"], today=TODAY)
    db.execute(
        "UPDATE st_proposals SET sent_at = '2026-10-01T09:00:00Z' WHERE id = ?",
        (proposal["id"],),
    )
    db.commit()
    assert service.tick(db, today=TODAY)["no_email"] == 2


# ---------------------------------------------------------------------------
# the default deposit, like a proposal's, can have decimals
# ---------------------------------------------------------------------------


def test_the_default_deposit_takes_the_same_percents_as_a_proposal(db):
    saved = service.save_settings(db, {"default_deposit_percent": "12.5"})
    assert saved["default_deposit_percent"] == 12.5
    proposal = service.create_proposal(
        db,
        {
            "client_id": _client(db)["id"],
            "title": "Logo",
            "items": [{"description": "Logo", "unit_cents": 100000}],
        },
        today=TODAY,
    )
    assert proposal["deposit_bp"] == 1250
    assert (
        service.save_settings(db, {"default_deposit_percent": 30})[
            "default_deposit_percent"
        ]
        == 30
    )
    for bad in ("abc", "101", "-1"):
        with pytest.raises(ValueError, match="deposit"):
            service.save_settings(db, {"default_deposit_percent": bad})


# ---------------------------------------------------------------------------
# journeys: the real request handler
# ---------------------------------------------------------------------------


def _call(env, api, method, path, body=None, profile=""):
    data = json.dumps(body).encode() if body is not None else None
    request = urllib.request.Request(env.base + path, data=data, method=method)
    request.add_header("Content-Type", "application/json")
    request.add_header("X-Requested-With", "aira")
    request.add_header("Origin", env.base)
    if profile:
        request.add_header("X-Aira-Profile", profile)
    opener = api.opener if api else urllib.request.build_opener()
    try:
        with opener.open(request, timeout=30) as response:
            raw = response.read()
            kind = response.headers.get("Content-Type", "")
            return response.status, json.loads(raw) if "json" in kind else raw.decode()
    except urllib.error.HTTPError as error:
        return error.code, json.loads(error.read() or b"{}")


def test_local_only_grown_ups_in_the_household_use_sales(env, api):
    kit.forget_schema()
    public.set_index(local._public_link_index)
    client = api.post(
        "/api/studio/clients", {"name": "Lee Chen", "email": "lee@example.com"}, 201
    )["client"]
    invoice = api.post(
        "/api/studio/invoices",
        {"client_id": client["id"], "items": [{"description": "A", "unit_cents": 900}]},
        201,
    )["invoice"]
    token = api.post(f"/api/studio/invoices/{invoice['id']}/link", {})["link"]["token"]
    owner = api.get("/api/life/profiles")["profiles"][0]
    kid = api.post("/api/life/profiles", {"name": "Kid", "age_group": "child"}, 201)[
        "profile"
    ]
    teen = _call(
        env,
        api,
        "POST",
        "/api/life/profiles",
        {"name": "Teen", "age_group": "teen"},
        profile=owner["id"],
    )[1]["profile"]
    for someone in (kid, teen):
        header = _call(
            env, api, "POST", "/api/life/unlock", {"profile_id": someone["id"]}
        )[1]["header"]
        for method, path in (
            ("GET", "/api/studio/invoices"),
            ("GET", "/api/studio/overview"),
            ("GET", "/api/studio/search?q=lee"),
            ("POST", f"/api/studio/invoices/{invoice['id']}/void"),
            ("GET", "/api/studio/payments/export"),
        ):
            status, body = _call(
                env, api, method, path, {} if method == "POST" else None, header
            )
            assert status == 403, (someone["name"], path, status)
            assert "grown-ups" in body["error"]
            assert body["code"] == "studio_grown_ups"
    # the grown-up still uses Sales, and nothing was voided
    status, body = _call(
        env, api, "GET", f"/api/studio/invoices/{invoice['id']}", profile=owner["id"]
    )
    assert status == 200 and body["invoice"]["status"] == "sent"
    # the client's page does not depend on who is using Aira
    status, page = _call(env, None, "GET", f"/api/public/{token}")
    assert status == 200 and page["page"]["number"] == invoice["number"]


def test_local_payments_export_ends_on_the_day_asked(env, api):
    kit.forget_schema()
    client = api.post("/api/studio/clients", {"name": "Lee Chen"}, 201)["client"]
    for day, cents in (("2026-10-01", 1000), ("2099-01-15", 2500)):
        invoice = api.post(
            "/api/studio/invoices",
            {
                "client_id": client["id"],
                "issue_date": "2026-09-01",
                "items": [{"description": "A", "unit_cents": cents}],
            },
            201,
        )["invoice"]
        api.post(f"/api/studio/invoices/{invoice['id']}/payments", {"day": day}, 201)
    status, csv_text = _call(
        env,
        api,
        "GET",
        "/api/studio/payments/export?since=2026-01-01&until=2026-12-31",
    )
    assert status == 200 and "10.00" in csv_text and "25.00" not in csv_text
    status, everything = _call(env, api, "GET", "/api/studio/payments/export")
    assert "25.00" in everything
    status, body = _call(env, api, "GET", "/api/studio/payments/export?until=soon")
    assert status == 400
