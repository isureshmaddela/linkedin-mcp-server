"""Sales in Studio (aira/suite/studio/sales.py): contacts, deals and stages, activities, the forecast, leads.

Unit tests run on a scratch SQLite file; the journey uses the real single-user request handler; the hosted test
checks one organization's deals stay its own.
"""

from __future__ import annotations

import sqlite3
import urllib.request

import pytest
from cloud_support import build_org, sign_in

import test_careers
from aira import suite
from aira.suite import kit, public
from aira.suite.studio import sales, service, store

env = test_careers.env
api = test_careers.api
stub = test_careers.stub

TODAY = "2026-10-25"


class _NoModel:
    def model(self, prompt):
        return None


@pytest.fixture(autouse=True)
def no_model():
    previous = suite.gateway()
    suite.set_gateway(_NoModel())
    yield
    suite.set_gateway(previous)


@pytest.fixture
def db(tmp_path):
    kit.forget_schema()
    public.set_index(public._MemoryIndex())
    connection = sqlite3.connect(tmp_path / "sales.sqlite3")
    connection.row_factory = sqlite3.Row
    store.ensure(connection)
    yield connection
    connection.close()


def _client(db, name="Bloom Bakery"):
    return service.create_client(
        db, {"name": name, "company": name, "email": "hello@bloom.example"}
    )


def _deal(db, client, **extra):
    return sales.create_deal(
        db,
        {
            "title": "Shop website",
            "client_id": client["id"],
            "value_cents": 400000,
            **extra,
        },
    )


def _stamp(db, deal_id, **values):
    kit.update_row(db, "st_deals", deal_id, values)
    db.commit()


# ---------------------------------------------------------------------------
# unit
# ---------------------------------------------------------------------------


def test_contacts_have_one_primary_and_belong_to_their_client(db):
    client = _client(db)
    other = _client(db, "Corner Cafe")
    ana = sales.add_contact(db, client["id"], {"name": "Ana Ruiz", "role": "Owner"})
    assert ana["is_primary"] is True  # the first contact is the primary one
    ben = sales.add_contact(db, client["id"], {"name": "Ben Ruiz", "is_primary": True})
    people = {c["name"]: c["is_primary"] for c in sales.list_contacts(db, client["id"])}
    assert people == {"Ana Ruiz": False, "Ben Ruiz": True}
    sales.update_contact(db, ana["id"], {"is_primary": True})
    assert [
        c["name"] for c in sales.list_contacts(db, client["id"]) if c["is_primary"]
    ] == ["Ana Ruiz"]
    sales.delete_contact(db, ana["id"])
    assert (
        sales.list_contacts(db, client["id"])[0]["is_primary"] is True
    )  # Ben takes over
    with pytest.raises(ValueError, match="works at this client"):
        _deal(db, other, contact_id=ben["id"])
    with pytest.raises(ValueError):
        sales.add_contact(db, client["id"], {"name": ""})


def test_stage_probabilities_and_the_weighted_forecast(db):
    client = _client(db)
    probs = sales.probabilities(db)
    assert probs == {
        "new": 10,
        "qualified": 25,
        "proposal": 50,
        "negotiation": 75,
        "won": 100,
        "lost": 0,
    }
    a = _deal(db, client, expected_close="2026-10-30")  # 4,000 at 10% -> 400
    b = _deal(
        db,
        client,
        title="Brand kit",
        value_cents=200000,
        stage="negotiation",
        expected_close="2026-11-15",
    )
    _deal(db, client, title="Someday", value_cents=100000)  # no close date
    assert sales.get_deal(db, a["id"])["weighted_cents"] == 40000
    assert sales.get_deal(db, b["id"])["weighted_cents"] == 150000
    numbers = sales.forecast(db, today=TODAY)
    months = {m["month"]: m["weighted_cents"] for m in numbers["months"]}
    assert months["2026-10"] == 40000 and months["2026-11"] == 150000
    assert numbers["undated"]["deals"] == 1 and numbers["pipeline_cents"] == 700000
    assert numbers["weighted_cents"] == 40000 + 150000 + 10000
    sales.save_probabilities(db, {"new": 20})
    assert sales.get_deal(db, a["id"])["weighted_cents"] == 80000
    with pytest.raises(ValueError):
        sales.save_probabilities(db, {"qualified": 150})


def test_won_and_lost_and_the_numbers_they_feed(db):
    client = _client(db)
    won = _deal(db, client)
    lost = _deal(db, client, title="Logo", value_cents=50000)
    with pytest.raises(ValueError, match="why the deal was lost"):
        sales.move_deal(db, lost["id"], "lost")
    sales.move_deal(db, lost["id"], "lost", lost_reason="Chose a cheaper studio")
    moved = sales.move_deal(db, won["id"], "won")
    assert moved["stage"] == "won" and moved["won_at"] and moved["probability"] == 100
    _stamp(
        db, won["id"], created_at="2026-10-05T09:00:00Z", won_at="2026-10-20T09:00:00Z"
    )
    numbers = sales.forecast(db, today=TODAY)
    assert (
        numbers["won_this_month_cents"] == 400000
        and numbers["won_this_quarter_cents"] == 400000
    )
    assert (
        numbers["win_rate"] == 50
        and numbers["average_won_cents"] == 400000
        and numbers["cycle_days"] == 15
    )
    # reopening clears the outcome
    reopened = sales.move_deal(db, lost["id"], "qualified")
    assert reopened["lost_reason"] == "" and reopened["lost_at"] is None
    with pytest.raises(ValueError):
        sales.move_deal(db, won["id"], "closed")
    # a won deal becomes a project and a draft invoice once
    made = sales.convert_deal(db, won["id"], today=TODAY)
    again = sales.convert_deal(db, won["id"], today=TODAY)
    assert (
        made["invoice"]["id"] == again["invoice"]["id"]
        and made["invoice"]["total_cents"] == 400000
    )
    assert made["project"]["budget_cents"] == 400000
    with pytest.raises(ValueError, match="Only a won deal"):
        sales.convert_deal(db, reopened["id"], today=TODAY)
    board = {column["stage"]: column for column in sales.board(db)["columns"]}
    assert board["won"]["count"] == 1 and board["qualified"]["count"] == 1


def test_accepting_the_linked_proposal_wins_the_deal(db):
    client = _client(db)
    proposal = service.create_proposal(
        db,
        {
            "client_id": client["id"],
            "title": "Website",
            "items": [{"description": "Build", "unit_cents": 300000}],
            "valid_until": "2026-12-31",
        },
        today=TODAY,
    )
    deal = _deal(db, client, proposal_id=proposal["id"], stage="proposal")
    other_client = _client(db, "Corner Cafe")
    with pytest.raises(ValueError, match="for this client"):
        _deal(db, other_client, proposal_id=proposal["id"])
    service.mark_proposal_sent(db, proposal["id"])
    service.accept_proposal(
        db, proposal["id"], name="Ana Ruiz", visitor={}, today=TODAY
    )
    assert sales.get_deal(db, deal["id"])["stage"] == "won"
    made = sales.convert_deal(db, deal["id"], today=TODAY)
    assert (
        made["invoice"]["proposal_id"] == proposal["id"]
    )  # through the proposal's own conversion
    # an owner recording the acceptance by hand does the same
    second = service.create_proposal(
        db,
        {
            "client_id": client["id"],
            "title": "Menu",
            "items": [{"description": "Menu", "unit_cents": 10000}],
        },
        today=TODAY,
    )
    deal2 = _deal(db, client, title="Menu boards", proposal_id=second["id"])
    service.set_proposal_status(db, second["id"], "accepted", today=TODAY)
    assert sales.get_deal(db, deal2["id"])["stage"] == "won"


def test_activities_due_today_and_overdue(db):
    client = _client(db)
    deal = _deal(db, client)
    call = sales.add_activity(
        db,
        {
            "kind": "call",
            "subject": "Call Ana",
            "deal_id": deal["id"],
            "due_date": TODAY,
        },
        today=TODAY,
    )
    assert call["client_id"] == client["id"] and call["due_today"] is True
    late = sales.add_activity(
        db,
        {
            "kind": "task",
            "subject": "Send quote",
            "client_id": client["id"],
            "due_date": "2026-10-20",
        },
        today=TODAY,
    )
    sales.add_activity(
        db,
        {"kind": "meeting", "subject": "Kick-off", "due_date": "2026-11-02"},
        today=TODAY,
    )
    note = sales.add_activity(
        db, {"kind": "note", "subject": "Likes green"}, today=TODAY
    )
    assert note["done"] is True  # a note is a record, not a to-do
    today = sales.list_activities(db, today=TODAY, when="today")
    assert [a["subject"] for a in today] == ["Send quote", "Call Ana"] and today[0][
        "overdue"
    ] is True
    sales.update_activity(db, late["id"], {"done": True}, today=TODAY)
    assert [
        a["subject"] for a in sales.list_activities(db, today=TODAY, when="today")
    ] == ["Call Ana"]
    assert len(sales.list_activities(db, today=TODAY, deal_id=deal["id"])) == 1
    with pytest.raises(ValueError):
        sales.add_activity(db, {"subject": ""}, today=TODAY)
    overview = service.overview(db, today=TODAY)
    assert [a["subject"] for a in overview["activities_today"]] == ["Call Ana"]
    assert overview["sales"]["open_deals"] == 1


def test_leads_become_clients_contacts_and_deals(db):
    assert sales.importable_leads(db) == {"available": False, "leads": []}
    with pytest.raises(LookupError, match="Save some in Leads"):
        sales.import_lead(db, "a" * 32, {})
    from aira.leads import store as lead_store

    lead_store.ensure_schema(db)
    lead_id = "b" * 32
    db.execute(
        """INSERT INTO leads (id, list_id, name, title, company, email, website, created_at, updated_at)
        VALUES (?, 'l1', 'Ravi Kumar', 'Founder', 'Spice Route', 'ravi@spice.example', 'https://spice.example', ?, ?)""",
        (lead_id, kit.now(), kit.now()),
    )
    db.commit()
    listed = sales.importable_leads(db)
    assert (
        listed["available"]
        and listed["leads"][0]["company"] == "Spice Route"
        and not listed["leads"][0]["deal_id"]
    )
    deal = sales.import_lead(db, lead_id, {"value_cents": 250000})
    assert (
        deal["source"] == "lead"
        and deal["client_name"] == "Spice Route"
        and deal["contact_name"] == "Ravi Kumar"
    )
    assert deal["title"] == "Deal with Spice Route" and deal["value_cents"] == 250000
    assert sales.import_lead(db, lead_id, {})["id"] == deal["id"]  # once
    assert sales.importable_leads(db)["leads"][0]["deal_id"] == deal["id"]
    assert kit.count(db, "SELECT COUNT(*) FROM leads") == 1  # the lead is only read


def test_checklist_and_hub_cards(db):
    client = _client(db)
    deal = _deal(db, client)
    sales.move_deal(db, deal["id"], "won")
    steps = {step["id"]: step["done"] for step in service.checklist(db)}
    assert steps["deal"] is True
    cards = {
        card["label"]: card for card in service.summary(db, today=kit.today())["cards"]
    }
    assert cards["Won this month"]["value"] == "$4,000.00"
    assert cards["Pipeline value"]["value"] == "$0.00"
    # deleting the client takes its deals, contacts and activities with it
    sales.add_contact(db, client["id"], {"name": "Ana"})
    sales.add_activity(db, {"subject": "Call", "client_id": client["id"]}, today=TODAY)
    service.delete_client(db, client["id"])
    for table in ("st_deals", "st_contacts", "st_activities"):
        assert kit.count(db, f"SELECT COUNT(*) FROM {table}") == 0, table  # noqa: S608


# ---------------------------------------------------------------------------
# the single-user build
# ---------------------------------------------------------------------------


def test_local_sales_journey(env, api):
    kit.forget_schema()
    client = api.post(
        "/api/studio/clients",
        {"name": "Bloom Bakery", "company": "Bloom Bakery"},
        expect=201,
    )["client"]
    ana = api.post(
        f"/api/studio/clients/{client['id']}/contacts",
        {"name": "Ana Ruiz", "role": "Owner", "email": "ana@bloom.example"},
        expect=201,
    )["contact"]
    api.post(
        f"/api/studio/clients/{client['id']}/contacts", {"name": "Ben Ruiz"}, expect=201
    )
    detail = api.get(f"/api/studio/clients/{client['id']}")
    assert [c["name"] for c in detail["contacts"]] == ["Ana Ruiz", "Ben Ruiz"]
    deal = api.post(
        "/api/studio/deals",
        {
            "title": "Shop website",
            "client_id": client["id"],
            "contact_id": ana["id"],
            "value_cents": 400000,
            "expected_close": kit.today(),
        },
        expect=201,
    )["deal"]
    for stage in ("qualified", "proposal", "negotiation"):
        deal = api.post(f"/api/studio/deals/{deal['id']}/move", {"stage": stage})[
            "deal"
        ]
    assert deal["probability"] == 75 and deal["weighted_cents"] == 300000
    forecast = api.get("/api/studio/forecast")
    assert forecast["months"][0]["weighted_cents"] == 300000
    status, body = api.call(
        "POST", f"/api/studio/deals/{deal['id']}/move", {"stage": "lost"}
    )
    assert status == 400
    api.post(f"/api/studio/deals/{deal['id']}/move", {"stage": "won"})
    activity = api.post(
        "/api/studio/activities",
        {
            "kind": "call",
            "subject": "Thank Ana",
            "deal_id": deal["id"],
            "due_date": kit.today(),
        },
        expect=201,
    )["activity"]
    overview = api.get("/api/studio/overview")
    assert (
        overview["sales"]["won_this_month_cents"] == 400000
        and overview["activities_today"][0]["id"] == activity["id"]
    )
    board = api.get("/api/studio/deals/board")
    assert {c["stage"]: c["count"] for c in board["columns"]}["won"] == 1
    made = api.post(f"/api/studio/deals/{deal['id']}/convert")
    assert made["invoice"]["total_cents"] == 400000
    request = urllib.request.Request(env.base + "/api/studio/deals/export")
    request.add_header("X-Requested-With", "aira")
    with api.opener.open(request, timeout=30) as response:
        csv_text = response.read().decode("utf-8")
    assert csv_text.startswith("title,client_name") and "Shop website" in csv_text
    home = api.get("/api/suite/home")
    studio = next(app for app in home["apps"] if app["id"] == "studio")
    labels = {card["label"]: card["value"] for card in studio["cards"]}
    assert labels["Won this month"] == "$4,000.00"
    leads = api.get("/api/studio/leads")
    assert leads["available"] in (True, False)


# ---------------------------------------------------------------------------
# the hosted build
# ---------------------------------------------------------------------------


def test_hosted_deals_stay_with_their_organization(cloud):
    kit.forget_schema()
    org = build_org(cloud)
    cloud.billing.subscribe_app(org.owner, "studio")
    owner = sign_in(cloud, org.owner)
    client = owner.call("POST", "/api/studio/clients", {"name": "Lee Chen"})[1][
        "client"
    ]
    status, made = owner.call(
        "POST",
        "/api/studio/deals",
        {"title": "Audit", "client_id": client["id"], "value_cents": 90000},
    )
    assert status == 201, made
    deal = made["deal"]
    coach = sign_in(cloud, org.coach)
    assert (
        coach.call(
            "POST", f"/api/studio/deals/{deal['id']}/move", {"stage": "qualified"}
        )[0]
        == 200
    )
    auditor = sign_in(cloud, org.auditor)
    assert auditor.call("GET", "/api/studio/deals")[0] == 200
    assert (
        auditor.call("POST", f"/api/studio/deals/{deal['id']}/move", {"stage": "won"})[
            0
        ]
        == 403
    )
    other = build_org(cloud, "Other Org", slug_email="other.test")
    cloud.billing.subscribe_app(other.owner, "studio")
    other_owner = sign_in(cloud, other.owner)
    assert other_owner.call("GET", "/api/studio/deals")[1]["deals"] == []
    assert other_owner.call("GET", f"/api/studio/deals/{deal['id']}")[0] == 404
    assert (
        other_owner.call(
            "POST", f"/api/studio/deals/{deal['id']}/move", {"stage": "won"}
        )[0]
        == 404
    )
