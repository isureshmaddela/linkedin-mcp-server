"""Sales: contacts at each client, deals moving through a pipeline, activities, and the forecast.

A deal's stage sets its probability (new 10%, qualified 25%, proposal 50%, negotiation 75%, won 100%, lost 0%; the
open stages' percentages can be changed in settings). The weighted pipeline of a month is the sum of the value x
probability of the open deals expected to close in it. A deal is won by hand or when the proposal linked to it is
accepted; a lost deal needs a reason. A won deal can become a project and a draft invoice, once.

Leads saved in the Leads app (the ``leads`` table, when this database has it) can be turned into a client, a
contact and a deal; the lead itself is only read.
"""

from __future__ import annotations

import sqlite3
from datetime import date
from decimal import ROUND_HALF_UP, Decimal
from typing import Any

from aira.suite import kit
from aira.suite.studio import store

APP = "studio"
STAGES = ("new", "qualified", "proposal", "negotiation", "won", "lost")
OPEN_STAGES = ("new", "qualified", "proposal", "negotiation")
DEFAULT_PROBABILITIES = {"new": 10, "qualified": 25, "proposal": 50, "negotiation": 75}
SOURCES = ("lead", "referral", "website", "other")
ACTIVITY_KINDS = ("call", "meeting", "task", "note")

SCHEMA = (
    """CREATE TABLE IF NOT EXISTS st_contacts (
        id TEXT PRIMARY KEY,
        client_id TEXT NOT NULL,
        name TEXT NOT NULL,
        role TEXT NOT NULL DEFAULT '',
        email TEXT NOT NULL DEFAULT '',
        phone TEXT NOT NULL DEFAULT '',
        is_primary INTEGER NOT NULL DEFAULT 0,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL
    )""",
    "CREATE INDEX IF NOT EXISTS st_contacts_client ON st_contacts (client_id)",
    """CREATE TABLE IF NOT EXISTS st_deals (
        id TEXT PRIMARY KEY,
        title TEXT NOT NULL,
        client_id TEXT NOT NULL,
        contact_id TEXT NOT NULL DEFAULT '',
        value_cents INTEGER NOT NULL DEFAULT 0,
        currency TEXT NOT NULL DEFAULT 'USD',
        stage TEXT NOT NULL DEFAULT 'new',
        expected_close TEXT NOT NULL DEFAULT '',
        owner_name TEXT NOT NULL DEFAULT '',
        next_step TEXT NOT NULL DEFAULT '',
        source TEXT NOT NULL DEFAULT 'other',
        lost_reason TEXT NOT NULL DEFAULT '',
        proposal_id TEXT NOT NULL DEFAULT '',
        project_id TEXT NOT NULL DEFAULT '',
        invoice_id TEXT NOT NULL DEFAULT '',
        lead_ref TEXT NOT NULL DEFAULT '',
        won_at TEXT,
        lost_at TEXT,
        stage_changed_at TEXT NOT NULL,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL
    )""",
    "CREATE INDEX IF NOT EXISTS st_deals_stage ON st_deals (stage, expected_close)",
    "CREATE INDEX IF NOT EXISTS st_deals_client ON st_deals (client_id)",
    """CREATE TABLE IF NOT EXISTS st_activities (
        id TEXT PRIMARY KEY,
        kind TEXT NOT NULL DEFAULT 'task',
        subject TEXT NOT NULL,
        notes TEXT NOT NULL DEFAULT '',
        client_id TEXT NOT NULL DEFAULT '',
        deal_id TEXT NOT NULL DEFAULT '',
        due_date TEXT NOT NULL DEFAULT '',
        done INTEGER NOT NULL DEFAULT 0,
        done_at TEXT,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL
    )""",
    "CREATE INDEX IF NOT EXISTS st_activities_due ON st_activities (done, due_date)",
)


def ensure(connection: sqlite3.Connection) -> None:
    kit.ensure(connection, "studio.sales.v1", SCHEMA)


# ---------------------------------------------------------------------------
# stage probabilities
# ---------------------------------------------------------------------------


def probabilities(connection: sqlite3.Connection) -> dict[str, int]:
    stored = kit.get_setting(connection, APP, "probabilities", {}) or {}
    out = {
        stage: int(stored.get(stage, value))
        for stage, value in DEFAULT_PROBABILITIES.items()
    }
    return {**out, "won": 100, "lost": 0}


def save_probabilities(
    connection: sqlite3.Connection, raw: dict[str, Any]
) -> dict[str, int]:
    current = probabilities(connection)
    clean = {
        stage: kit.integer(
            raw.get(stage, current[stage]),
            f"the {stage} probability",
            minimum=0,
            maximum=100,
        )
        for stage in OPEN_STAGES
    }
    kit.set_setting(connection, APP, "probabilities", clean)
    connection.commit()
    return probabilities(connection)


def weighted(value_cents: int, percent: int) -> int:
    return int(
        (Decimal(value_cents) * Decimal(percent) / Decimal(100)).quantize(
            Decimal("1"), rounding=ROUND_HALF_UP
        )
    )


# ---------------------------------------------------------------------------
# contacts
# ---------------------------------------------------------------------------


def _client(connection: sqlite3.Connection, client_id: Any) -> dict[str, Any]:
    cid = kit.text(client_id, "a client", max_len=64, required=True)
    return store.get(connection, "st_clients", cid, "client")


def _contact_fields(
    raw: dict[str, Any], current: dict[str, Any] | None = None
) -> dict[str, Any]:
    merged = {**(current or {}), **raw}
    return {
        "name": kit.text(
            merged.get("name"), "the contact's name", max_len=120, required=True
        ),
        "role": kit.text(merged.get("role"), "the role", max_len=120),
        "email": kit.email(merged.get("email")),
        "phone": kit.phone(merged.get("phone")),
    }


def _set_primary(
    connection: sqlite3.Connection, client_id: str, contact_id: str
) -> None:
    connection.execute(
        "UPDATE st_contacts SET is_primary = 0 WHERE client_id = ?", (client_id,)
    )
    connection.execute(
        "UPDATE st_contacts SET is_primary = 1 WHERE id = ?", (contact_id,)
    )


def list_contacts(
    connection: sqlite3.Connection, client_id: str
) -> list[dict[str, Any]]:
    rows = kit.fetch_all(
        connection,
        "SELECT * FROM st_contacts WHERE client_id = ? ORDER BY is_primary DESC, name",
        (client_id,),
    )
    for row in rows:
        row["is_primary"] = bool(row["is_primary"])
    return rows


def get_contact(connection: sqlite3.Connection, contact_id: str) -> dict[str, Any]:
    row = kit.require_row(connection, "st_contacts", contact_id, "contact")
    row["is_primary"] = bool(row["is_primary"])
    return row


def add_contact(
    connection: sqlite3.Connection, client_id: str, raw: dict[str, Any]
) -> dict[str, Any]:
    client = _client(connection, client_id)
    values = {
        "id": kit.new_id(),
        "client_id": client["id"],
        **_contact_fields(raw),
        "is_primary": 0,
        "created_at": kit.now(),
        "updated_at": kit.now(),
    }
    store.insert(connection, "st_contacts", values)
    first = (
        kit.count(
            connection,
            "SELECT COUNT(*) FROM st_contacts WHERE client_id = ?",
            (client["id"],),
        )
        == 1
    )
    if first or kit.flag(raw.get("is_primary")):
        _set_primary(connection, client["id"], values["id"])
    connection.commit()
    return get_contact(connection, values["id"])


def update_contact(
    connection: sqlite3.Connection, contact_id: str, raw: dict[str, Any]
) -> dict[str, Any]:
    current = get_contact(connection, contact_id)
    values = _contact_fields(raw, current)
    values["updated_at"] = kit.now()
    kit.update_row(connection, "st_contacts", contact_id, values)
    if kit.flag(raw.get("is_primary")):
        _set_primary(connection, current["client_id"], contact_id)
    connection.commit()
    return get_contact(connection, contact_id)


def delete_contact(connection: sqlite3.Connection, contact_id: str) -> None:
    contact = get_contact(connection, contact_id)
    connection.execute(
        "UPDATE st_deals SET contact_id = '' WHERE contact_id = ?", (contact_id,)
    )
    connection.execute("DELETE FROM st_contacts WHERE id = ?", (contact_id,))
    if contact["is_primary"]:
        rest = kit.fetch_one(
            connection,
            "SELECT id FROM st_contacts WHERE client_id = ? ORDER BY created_at LIMIT 1",
            (contact["client_id"],),
        )
        if rest:
            _set_primary(connection, contact["client_id"], rest["id"])
    connection.commit()


# ---------------------------------------------------------------------------
# deals
# ---------------------------------------------------------------------------


def _deal_view(
    connection: sqlite3.Connection,
    deal: dict[str, Any],
    probs: dict[str, int] | None = None,
) -> dict[str, Any]:
    probs = probs or probabilities(connection)
    names = (
        kit.fetch_one(
            connection,
            """SELECT c.name AS client_name, c.company AS client_company, k.name AS contact_name
        FROM st_clients c LEFT JOIN st_contacts k ON k.id = ? WHERE c.id = ?""",
            (deal.get("contact_id") or "", deal["client_id"]),
        )
        or {}
    )
    probability = probs.get(deal["stage"], 0)
    return {
        **deal,
        "client_name": names.get("client_name") or "",
        "client_company": names.get("client_company") or "",
        "contact_name": names.get("contact_name") or "",
        "probability": probability,
        "weighted_cents": weighted(int(deal["value_cents"]), probability),
        "open": deal["stage"] in OPEN_STAGES,
    }


def get_deal(connection: sqlite3.Connection, deal_id: str) -> dict[str, Any]:
    return _deal_view(
        connection, kit.require_row(connection, "st_deals", deal_id, "deal")
    )


def _deal_fields(
    connection: sqlite3.Connection,
    raw: dict[str, Any],
    current: dict[str, Any] | None = None,
) -> dict[str, Any]:
    merged = {**(current or {}), **raw}
    client = _client(connection, merged.get("client_id"))
    contact_id = kit.text(merged.get("contact_id"), "a contact", max_len=64)
    if contact_id:
        contact = get_contact(connection, contact_id)
        if contact["client_id"] != client["id"]:
            raise ValueError("Choose a contact who works at this client.")
    proposal_id = kit.text(merged.get("proposal_id"), "a proposal", max_len=64)
    if proposal_id:
        proposal = store.get(connection, "st_proposals", proposal_id, "proposal")
        if proposal["client_id"] != client["id"]:
            raise ValueError("Link a proposal made for this client.")
    value = merged.get("value_cents")
    return {
        "title": kit.text(
            merged.get("title"), "the deal's name", max_len=160, required=True
        ),
        "client_id": client["id"],
        "contact_id": contact_id,
        "value_cents": kit.integer(
            value, "the value", default=0, minimum=0, maximum=10_000_000_00
        ),
        "currency": kit.currency(
            merged.get("currency") or kit.business(connection)["currency"]
        ),
        "expected_close": kit.day(
            merged.get("expected_close"), "the expected close date"
        ),
        "owner_name": kit.text(merged.get("owner_name"), "the owner", max_len=120),
        "next_step": kit.text(merged.get("next_step"), "the next step", max_len=300),
        "source": kit.choice(
            merged.get("source"), "the source", SOURCES, default="other"
        ),
        "proposal_id": proposal_id,
    }


def create_deal(connection: sqlite3.Connection, raw: dict[str, Any]) -> dict[str, Any]:
    ensure(connection)
    values = _deal_fields(connection, raw)
    stage = kit.choice(raw.get("stage"), "the stage", OPEN_STAGES, default="new")
    now = kit.now()
    deal = {
        "id": kit.new_id(),
        **values,
        "stage": stage,
        "lead_ref": kit.text(raw.get("lead_ref"), "a lead", max_len=64),
        "stage_changed_at": now,
        "created_at": now,
        "updated_at": now,
    }
    store.insert(connection, "st_deals", deal)
    kit.log(
        connection,
        APP,
        "deal",
        f"Deal added: {deal['title']}",
        ref_type="deal",
        ref_id=deal["id"],
        amount_cents=int(deal["value_cents"]),
    )
    connection.commit()
    return get_deal(connection, deal["id"])


def update_deal(
    connection: sqlite3.Connection, deal_id: str, raw: dict[str, Any]
) -> dict[str, Any]:
    current = kit.require_row(connection, "st_deals", deal_id, "deal")
    values = _deal_fields(connection, raw, current)
    values["updated_at"] = kit.now()
    kit.update_row(connection, "st_deals", deal_id, values)
    connection.commit()
    if "stage" in raw and raw["stage"] != current["stage"]:
        return move_deal(
            connection,
            deal_id,
            str(raw["stage"]),
            lost_reason=str(raw.get("lost_reason") or ""),
        )
    return get_deal(connection, deal_id)


def move_deal(
    connection: sqlite3.Connection, deal_id: str, stage: str, *, lost_reason: str = ""
) -> dict[str, Any]:
    """Move a deal to a stage. Lost needs a reason; won is recorded once; reopening clears the outcome."""
    deal = kit.require_row(connection, "st_deals", deal_id, "deal")
    wanted = kit.choice(stage, "the stage", STAGES)
    if wanted == deal["stage"]:
        return get_deal(connection, deal_id)
    now = kit.now()
    values: dict[str, Any] = {
        "stage": wanted,
        "stage_changed_at": now,
        "updated_at": now,
    }
    if wanted == "lost":
        reason = kit.text(
            lost_reason, "why the deal was lost", max_len=500, required=True
        )
        values.update({"lost_reason": reason, "lost_at": now, "won_at": None})
    elif wanted == "won":
        values.update({"won_at": now, "lost_at": None, "lost_reason": ""})
    else:
        values.update({"won_at": None, "lost_at": None, "lost_reason": ""})
    kit.update_row(connection, "st_deals", deal_id, values)
    label = {"won": "won", "lost": "lost"}.get(wanted, f"moved to {wanted}")
    kit.log(
        connection,
        APP,
        "deal_won" if wanted == "won" else "deal",
        f"Deal {label}: {deal['title']}",
        detail=values.get("lost_reason", "") if wanted == "lost" else "",
        ref_type="deal",
        ref_id=deal_id,
        amount_cents=int(deal["value_cents"]) if wanted == "won" else 0,
    )
    connection.commit()
    return get_deal(connection, deal_id)


def delete_deal(connection: sqlite3.Connection, deal_id: str) -> None:
    deal = kit.require_row(connection, "st_deals", deal_id, "deal")
    connection.execute(
        "UPDATE st_activities SET deal_id = '' WHERE deal_id = ?", (deal_id,)
    )
    connection.execute("DELETE FROM st_deals WHERE id = ?", (deal_id,))
    kit.log(
        connection,
        APP,
        "deal",
        f"Deal deleted: {deal['title']}",
        ref_type="deal",
        ref_id=deal_id,
    )
    connection.commit()


def list_deals(
    connection: sqlite3.Connection,
    *,
    stage: str = "",
    client_id: str = "",
    q: str = "",
    owner: str = "",
    status: str = "",
) -> list[dict[str, Any]]:
    clauses, params = [], []
    if stage:
        clauses.append("d.stage = ?")
        params.append(kit.choice(stage, "the stage", STAGES))
    if status == "open":
        clauses.append(f"d.stage IN ({', '.join('?' for _ in OPEN_STAGES)})")
        params += list(OPEN_STAGES)
    elif status in ("won", "lost"):
        clauses.append("d.stage = ?")
        params.append(status)
    if client_id:
        clauses.append("d.client_id = ?")
        params.append(client_id)
    if owner:
        clauses.append("d.owner_name = ?")
        params.append(owner.strip()[:120])
    if q:
        clauses.append("(d.title LIKE ? OR c.name LIKE ? OR c.company LIKE ?)")
        like = f"%{q.strip()[:80]}%"
        params += [like, like, like]
    where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    rows = kit.fetch_all(
        connection,
        f"""SELECT d.* FROM st_deals d LEFT JOIN st_clients c ON c.id = d.client_id {where}
        ORDER BY CASE WHEN d.expected_close = '' THEN 1 ELSE 0 END, d.expected_close, d.updated_at DESC LIMIT 2000""",  # noqa: S608
        params,
    )
    probs = probabilities(connection)
    return [_deal_view(connection, row, probs) for row in rows]


def board(connection: sqlite3.Connection) -> dict[str, Any]:
    """The pipeline as columns: each stage's deals, count, value and weighted value."""
    deals = list_deals(connection)
    probs = probabilities(connection)
    columns = []
    for stage in STAGES:
        items = [deal for deal in deals if deal["stage"] == stage]
        columns.append(
            {
                "stage": stage,
                "probability": probs[stage],
                "count": len(items),
                "value_cents": sum(int(d["value_cents"]) for d in items),
                "weighted_cents": sum(d["weighted_cents"] for d in items),
                "deals": items,
            }
        )
    return {
        "columns": columns,
        "probabilities": probs,
        "currency": kit.business(connection)["currency"],
    }


def convert_deal(
    connection: sqlite3.Connection, deal_id: str, *, today: str
) -> dict[str, Any]:
    """A won deal becomes a project and a draft invoice, once (through its accepted proposal when it has one)."""
    from aira.suite.studio import service

    deal = kit.require_row(connection, "st_deals", deal_id, "deal")
    if deal["stage"] != "won":
        raise ValueError("Only a won deal can become a project and an invoice.")
    project_id, invoice_id = deal["project_id"], deal["invoice_id"]
    # A project deleted, or an invoice deleted or voided, since: make it again.
    if project_id and not kit.fetch_one(
        connection, "SELECT id FROM st_projects WHERE id = ?", (project_id,)
    ):
        project_id = ""
    if invoice_id and not kit.fetch_one(
        connection,
        "SELECT id FROM st_invoices WHERE id = ? AND state != 'void'",
        (invoice_id,),
    ):
        invoice_id = ""
    if not project_id or not invoice_id:
        if deal["proposal_id"]:
            proposal = store.get(
                connection, "st_proposals", deal["proposal_id"], "proposal"
            )
            if proposal["status"] == "accepted":
                made = service.convert_proposal(
                    connection, deal["proposal_id"], today=today
                )
                project_id, invoice_id = made["project"]["id"], made["invoice"]["id"]
        if not project_id:
            project_id = service.create_project(
                connection,
                {
                    "client_id": deal["client_id"],
                    "name": deal["title"],
                    "status": "active",
                    "budget_cents": deal["value_cents"],
                    "notes": "From a won deal.",
                },
            )["id"]
        if not invoice_id:
            invoice_id = service.create_invoice(
                connection,
                {
                    "client_id": deal["client_id"],
                    "project_id": project_id,
                    "items": [
                        {
                            "description": deal["title"],
                            "quantity": "1",
                            "unit_cents": deal["value_cents"],
                        }
                    ],
                    "notes": f"As agreed: {deal['title']}.",
                },
                today=today,
            )["id"]
        kit.update_row(
            connection,
            "st_deals",
            deal_id,
            {
                "project_id": project_id,
                "invoice_id": invoice_id,
                "updated_at": kit.now(),
            },
        )
        connection.commit()
    return {
        "deal": get_deal(connection, deal_id),
        "project": service.get_project(connection, project_id),
        "invoice": service.get_invoice(connection, invoice_id, today),
    }


def on_proposal_accepted(connection: sqlite3.Connection, proposal_id: str) -> int:
    """Open deals linked to an accepted proposal are won. Returns how many moved."""
    ensure(connection)
    moved = 0
    for row in kit.fetch_all(
        connection,
        f"SELECT id FROM st_deals WHERE proposal_id = ? AND stage IN ({', '.join('?' for _ in OPEN_STAGES)})",  # noqa: S608
        (proposal_id, *OPEN_STAGES),
    ):
        move_deal(connection, row["id"], "won")
        moved += 1
    return moved


# ---------------------------------------------------------------------------
# activities
# ---------------------------------------------------------------------------


def _activity_fields(
    connection: sqlite3.Connection,
    raw: dict[str, Any],
    current: dict[str, Any] | None = None,
) -> dict[str, Any]:
    merged = {**(current or {}), **raw}
    client_id = kit.text(merged.get("client_id"), "a client", max_len=64)
    deal_id = kit.text(merged.get("deal_id"), "a deal", max_len=64)
    if deal_id:
        deal = kit.require_row(connection, "st_deals", deal_id, "deal")
        client_id = client_id or deal["client_id"]
    if client_id:
        _client(connection, client_id)
    return {
        "kind": kit.choice(
            merged.get("kind"), "the kind", ACTIVITY_KINDS, default="task"
        ),
        "subject": kit.text(
            merged.get("subject"), "what it is about", max_len=200, required=True
        ),
        "notes": kit.text(merged.get("notes"), "notes", max_len=4000, multiline=True),
        "client_id": client_id,
        "deal_id": deal_id,
        "due_date": kit.day(merged.get("due_date"), "the due date"),
    }


def _activity_view(
    connection: sqlite3.Connection, row: dict[str, Any], today: str
) -> dict[str, Any]:
    names = (
        kit.fetch_one(
            connection,
            "SELECT (SELECT name FROM st_clients WHERE id = ?) AS client_name, (SELECT title FROM st_deals WHERE id = ?) AS deal_title",
            (row["client_id"], row["deal_id"]),
        )
        or {}
    )
    done = bool(row["done"])
    return {
        **row,
        "done": done,
        "client_name": names.get("client_name") or "",
        "deal_title": names.get("deal_title") or "",
        "overdue": bool(not done and row["due_date"] and row["due_date"] < today),
        "due_today": bool(not done and row["due_date"] == today),
    }


def add_activity(
    connection: sqlite3.Connection, raw: dict[str, Any], *, today: str
) -> dict[str, Any]:
    ensure(connection)
    values = {
        "id": kit.new_id(),
        **_activity_fields(connection, raw),
        "done": 0,
        "created_at": kit.now(),
        "updated_at": kit.now(),
    }
    if values["kind"] == "note":
        values["done"] = 1
        values["done_at"] = kit.now()
    store.insert(connection, "st_activities", values)
    connection.commit()
    return get_activity(connection, values["id"], today=today)


def get_activity(
    connection: sqlite3.Connection, activity_id: str, *, today: str
) -> dict[str, Any]:
    return _activity_view(
        connection,
        kit.require_row(connection, "st_activities", activity_id, "activity"),
        today,
    )


def update_activity(
    connection: sqlite3.Connection, activity_id: str, raw: dict[str, Any], *, today: str
) -> dict[str, Any]:
    current = kit.require_row(connection, "st_activities", activity_id, "activity")
    values = _activity_fields(connection, raw, current)
    if "done" in raw:
        done = kit.flag(raw["done"])
        values["done"] = 1 if done else 0
        values["done_at"] = kit.now() if done else None
    values["updated_at"] = kit.now()
    kit.update_row(connection, "st_activities", activity_id, values)
    connection.commit()
    return get_activity(connection, activity_id, today=today)


def delete_activity(connection: sqlite3.Connection, activity_id: str) -> None:
    kit.require_row(connection, "st_activities", activity_id, "activity")
    connection.execute("DELETE FROM st_activities WHERE id = ?", (activity_id,))
    connection.commit()


def list_activities(
    connection: sqlite3.Connection,
    *,
    today: str,
    when: str = "",
    client_id: str = "",
    deal_id: str = "",
) -> list[dict[str, Any]]:
    clauses, params = [], []
    if when == "today":
        clauses.append("done = 0 AND due_date != '' AND due_date <= ?")
        params.append(today)
    elif when == "upcoming":
        clauses.append("done = 0 AND (due_date = '' OR due_date > ?)")
        params.append(today)
    elif when == "done":
        clauses.append("done = 1")
    elif when == "open":
        clauses.append("done = 0")
    if client_id:
        clauses.append("client_id = ?")
        params.append(client_id)
    if deal_id:
        clauses.append("deal_id = ?")
        params.append(deal_id)
    where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    rows = kit.fetch_all(
        connection,
        f"""SELECT * FROM st_activities {where}
        ORDER BY done, CASE WHEN due_date = '' THEN 1 ELSE 0 END, due_date, created_at DESC LIMIT 1000""",  # noqa: S608
        params,
    )
    return [_activity_view(connection, row, today) for row in rows]


# ---------------------------------------------------------------------------
# the forecast
# ---------------------------------------------------------------------------


def _quarter(day: date) -> tuple[int, int]:
    return day.year, (day.month - 1) // 3


def forecast(
    connection: sqlite3.Connection, *, today: str, months: int = 6
) -> dict[str, Any]:
    deals = list_deals(connection)
    now = date.fromisoformat(today)
    keys = []
    year, month = now.year, now.month
    for _ in range(max(1, min(months, 12))):
        keys.append(f"{year:04d}-{month:02d}")
        month += 1
        if month > 12:
            year, month = year + 1, 1
    by_month = {
        key: {"month": key, "weighted_cents": 0, "value_cents": 0, "deals": 0}
        for key in keys
    }
    undated = {"weighted_cents": 0, "value_cents": 0, "deals": 0}
    overdue = {"weighted_cents": 0, "value_cents": 0, "deals": 0}
    for deal in deals:
        if not deal["open"]:
            continue
        close = deal["expected_close"]
        bucket = (
            undated
            if not close
            else (overdue if close[:7] < keys[0] else by_month.get(close[:7]))
        )
        if bucket is None:
            continue
        bucket["weighted_cents"] += deal["weighted_cents"]
        bucket["value_cents"] += int(deal["value_cents"])
        bucket["deals"] += 1
    won = [deal for deal in deals if deal["stage"] == "won" and deal["won_at"]]
    lost = [deal for deal in deals if deal["stage"] == "lost"]
    this_month = sum(int(d["value_cents"]) for d in won if d["won_at"][:7] == today[:7])
    this_quarter = sum(
        int(d["value_cents"])
        for d in won
        if _quarter(date.fromisoformat(d["won_at"][:10])) == _quarter(now)
    )
    cycle_days = [
        (
            date.fromisoformat(d["won_at"][:10])
            - date.fromisoformat(d["created_at"][:10])
        ).days
        for d in won
    ]
    closed = len(won) + len(lost)
    open_deals = [deal for deal in deals if deal["open"]]
    return {
        "currency": kit.business(connection)["currency"],
        "months": [by_month[key] for key in keys],
        "undated": undated,
        "overdue": overdue,
        "pipeline_cents": sum(int(d["value_cents"]) for d in open_deals),
        "weighted_cents": sum(d["weighted_cents"] for d in open_deals),
        "open_deals": len(open_deals),
        "won_this_month_cents": this_month,
        "won_this_quarter_cents": this_quarter,
        "won_count": len(won),
        "lost_count": len(lost),
        "win_rate": round(len(won) * 100 / closed) if closed else None,
        "average_won_cents": round(sum(int(d["value_cents"]) for d in won) / len(won))
        if won
        else None,
        "cycle_days": round(sum(cycle_days) / len(cycle_days)) if cycle_days else None,
    }


# ---------------------------------------------------------------------------
# leads saved in the Leads app
# ---------------------------------------------------------------------------


def _has_leads(connection: sqlite3.Connection) -> bool:
    return (
        connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'leads'"
        ).fetchone()
        is not None
    )


def importable_leads(
    connection: sqlite3.Connection, *, q: str = "", limit: int = 200
) -> dict[str, Any]:
    """Leads from the Leads app that can become a client and a deal (read only; empty when there are none)."""
    if not _has_leads(connection):
        return {"available": False, "leads": []}
    clauses, params = [], []
    if q:
        clauses.append("(name LIKE ? OR company LIKE ? OR email LIKE ?)")
        like = f"%{q.strip()[:80]}%"
        params += [like, like, like]
    where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    rows = kit.fetch_all(
        connection,
        f"""SELECT id, name, first_name, last_name, title, company, email, phone, website, status, score
        FROM leads {where} ORDER BY updated_at DESC LIMIT ?""",  # noqa: S608
        (*params, max(1, min(limit, 500))),
    )
    imported = {
        row["lead_ref"]: row["id"]
        for row in kit.fetch_all(
            connection, "SELECT lead_ref, id FROM st_deals WHERE lead_ref != ''"
        )
    }
    for row in rows:
        row["deal_id"] = imported.get(row["id"], "")
    return {"available": True, "leads": rows}


def import_lead(
    connection: sqlite3.Connection, lead_id: str, raw: dict[str, Any]
) -> dict[str, Any]:
    """A lead becomes a client (its company), a contact (the person) and a new deal. Importing it again returns
    the deal made the first time."""
    from aira.suite.studio import service

    ensure(connection)
    if not _has_leads(connection):
        raise LookupError("There are no leads to add. Save some in Leads first.")
    lead = kit.fetch_one(connection, "SELECT * FROM leads WHERE id = ?", (lead_id,))
    if not lead:
        raise LookupError("That lead was not found.")
    existing = kit.fetch_one(
        connection, "SELECT id FROM st_deals WHERE lead_ref = ?", (lead_id,)
    )
    if existing:
        return get_deal(connection, existing["id"])
    person = (
        lead.get("name")
        or " ".join(p for p in (lead.get("first_name"), lead.get("last_name")) if p)
    ).strip()
    company = (lead.get("company") or "").strip()
    email = str(lead.get("email") or "")
    try:
        email = kit.email(email)
    except ValueError:
        email = ""  # a lead's address that does not check out is left off
    client = service.create_client(
        connection,
        {
            "name": company or person or "New client",
            "company": company,
            "email": email,
            "phone": "",
            "status": "lead",
            "notes": f"From Leads{(': ' + lead['website']) if lead.get('website') else ''}.",
        },
    )
    contact_id = ""
    if person:
        try:
            contact_id = add_contact(
                connection,
                client["id"],
                {
                    "name": person,
                    "role": lead.get("title") or "",
                    "email": email,
                    "phone": "",
                },
            )["id"]
        except ValueError:
            contact_id = ""
    return create_deal(
        connection,
        {
            "title": kit.text(raw.get("title"), "the deal's name", max_len=160)
            or f"Deal with {company or person or 'new client'}",
            "client_id": client["id"],
            "contact_id": contact_id,
            "value_cents": raw.get("value_cents") or 0,
            "source": "lead",
            "lead_ref": lead_id,
        },
    )


# ---------------------------------------------------------------------------
# for the overview and the hub
# ---------------------------------------------------------------------------


def overview_part(connection: sqlite3.Connection, *, today: str) -> dict[str, Any]:
    ensure(connection)
    numbers = forecast(connection, today=today)
    return {
        "sales": {
            "pipeline_cents": numbers["pipeline_cents"],
            "weighted_cents": numbers["weighted_cents"],
            "open_deals": numbers["open_deals"],
            "won_this_month_cents": numbers["won_this_month_cents"],
            "win_rate": numbers["win_rate"],
        },
        "activities_today": list_activities(connection, today=today, when="today")[:20],
        "closing_soon": [
            deal
            for deal in list_deals(connection, status="open")
            if deal["expected_close"] and deal["expected_close"][:7] <= today[:7]
        ][:10],
    }
