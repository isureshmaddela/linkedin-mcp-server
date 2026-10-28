"""Studio's rules: money on proposals and invoices, numbering, statuses, payments, time, retainers, conversions.

Money is integer cents. A line's amount is ``quantity x unit price`` rounded half up; the discount comes off the
subtotal (never below zero); tax is the document's rate (hundredths of a percent, copied from the business
profile when the document is made) on what is left, rounded half up.

An invoice stores its *state* (draft, sent, void); what people see is its *status*, worked out from the state,
the payments and the due date: draft, sent, partially_paid, paid, overdue or void. Each payment is a line of
revenue in the Business hub (source ``payment``), so a payment counts in the month it was received; deleting it
or voiding the invoice takes it back out.
"""

from __future__ import annotations

import calendar
import sqlite3
from datetime import date, timedelta
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from typing import Any, cast

from aira.suite import kit
from aira.suite.studio import store

APP = "studio"
CLIENT_STATUSES = ("lead", "active", "past")
PROPOSAL_STATUSES = ("draft", "sent", "accepted", "declined", "expired")
PROJECT_STATUSES = ("planned", "active", "on_hold", "done")
TASK_STATUSES = ("todo", "doing", "done")
INVOICE_STATUSES = ("draft", "sent", "partially_paid", "paid", "overdue", "void")
PAYMENT_METHODS = (
    "bank_transfer",
    "card",
    "cash",
    "upi",
    "paypal",
    "stripe",
    "cheque",
    "other",
)
MAX_ITEMS = 100

SETTINGS_DEFAULTS: dict[str, Any] = {
    "payment_terms_days": 14,
    "invoice_prefix": "INV-",
    "proposal_prefix": "PRO-",
    "proposal_valid_days": 30,
    "default_rate_cents": 0,
    "default_deposit_percent": 0,
    # what Studio does by itself (the background task, every few minutes)
    "auto_retainers": True,  # draft each retainer's invoice on its date
    "retainer_auto_send": False,  # ... and send it to the client (the email waits in the outbox)
    "auto_reminders": False,  # queue a friendly reminder for an overdue invoice
    "reminder_after_days": 3,  # days after the due date for the first reminder
    "reminder_every_days": 7,  # days between reminders
    "reminder_max": 3,  # reminders per invoice, at most
    "proposal_followup_days": 0,  # follow up a sent proposal with no answer after this many days (0: never)
}


# ---------------------------------------------------------------------------
# settings
# ---------------------------------------------------------------------------


def settings(connection: sqlite3.Connection) -> dict[str, Any]:
    stored = kit.get_setting(connection, APP, "settings", {}) or {}
    return {
        **SETTINGS_DEFAULTS,
        **{k: v for k, v in stored.items() if k in SETTINGS_DEFAULTS},
    }


def save_settings(
    connection: sqlite3.Connection, raw: dict[str, Any]
) -> dict[str, Any]:
    merged = {
        **settings(connection),
        **{k: v for k, v in raw.items() if k in SETTINGS_DEFAULTS},
    }
    clean = {
        "payment_terms_days": kit.integer(
            merged["payment_terms_days"],
            "the payment terms",
            default=14,
            minimum=0,
            maximum=365,
        ),
        "invoice_prefix": kit.text(
            merged["invoice_prefix"], "the invoice prefix", max_len=12
        )
        or "INV-",
        "proposal_prefix": kit.text(
            merged["proposal_prefix"], "the proposal prefix", max_len=12
        )
        or "PRO-",
        "proposal_valid_days": kit.integer(
            merged["proposal_valid_days"],
            "how long a proposal is valid",
            default=30,
            minimum=1,
            maximum=365,
        ),
        "default_rate_cents": cents_field(
            merged["default_rate_cents"], "the hourly rate"
        ),
        # the same rule as the deposit on a proposal: a percent with up to two decimals (12.5 is fine)
        "default_deposit_percent": _percent_number(
            deposit_bp(merged["default_deposit_percent"])
        ),
        "auto_retainers": kit.flag(merged["auto_retainers"]),
        "retainer_auto_send": kit.flag(merged["retainer_auto_send"]),
        "auto_reminders": kit.flag(merged["auto_reminders"]),
        "reminder_after_days": kit.integer(
            merged["reminder_after_days"],
            "the days before the first reminder",
            default=3,
            minimum=0,
            maximum=90,
        ),
        "reminder_every_days": kit.integer(
            merged["reminder_every_days"],
            "the days between reminders",
            default=7,
            minimum=1,
            maximum=90,
        ),
        "reminder_max": kit.integer(
            merged["reminder_max"],
            "the number of reminders",
            default=3,
            minimum=1,
            maximum=10,
        ),
        "proposal_followup_days": kit.integer(
            merged["proposal_followup_days"],
            "the days before a follow-up",
            default=0,
            minimum=0,
            maximum=60,
        ),
    }
    kit.set_setting(connection, APP, "settings", clean)
    connection.commit()
    return clean


# ---------------------------------------------------------------------------
# dates
# ---------------------------------------------------------------------------


def _date(value: str) -> date:
    return date.fromisoformat(value)


def add_days(day: str, days: int) -> str:
    return (_date(day) + timedelta(days=days)).isoformat()


def add_month(day: str, anchor_day: int) -> str:
    """The same day next month (the 31st becomes the month's last day; the anchor keeps it from drifting)."""
    current = _date(day)
    year, month = (
        (current.year + 1, 1)
        if current.month == 12
        else (current.year, current.month + 1)
    )
    last = calendar.monthrange(year, month)[1]
    return date(year, month, min(max(1, anchor_day), last)).isoformat()


# ---------------------------------------------------------------------------
# line items and totals
# ---------------------------------------------------------------------------


def quantity(value: Any) -> Decimal:
    text = (
        str(value if value is not None and value != "" else "1")
        .strip()
        .replace(",", "")
    )
    try:
        number = Decimal(text)
    except InvalidOperation:
        raise ValueError("A quantity must be a number like 1 or 2.5.") from None
    if not number.is_finite() or number <= 0:
        raise ValueError("A quantity must be more than zero.")
    if number > 100000:
        raise ValueError("A quantity is too large.")
    return number.quantize(Decimal("0.0001"), rounding=ROUND_HALF_UP).normalize()


def line_amount(qty: Decimal, unit_cents: int) -> int:
    return int(
        (qty * Decimal(unit_cents)).quantize(Decimal("1"), rounding=ROUND_HALF_UP)
    )


def tax_of(amount_cents: int, rate_bp: int) -> int:
    return int(
        (Decimal(amount_cents) * Decimal(rate_bp) / Decimal(10000)).quantize(
            Decimal("1"), rounding=ROUND_HALF_UP
        )
    )


def clean_items(raw: Any) -> list[dict[str, Any]]:
    """Line items from a request: ``[{description, quantity, unit_cents}]`` (or ``unit_price`` as money text)."""
    if raw in (None, ""):
        return []
    if not isinstance(raw, list):
        raise ValueError("Line items must be a list.")
    if len(raw) > MAX_ITEMS:
        raise ValueError(f"A document can have at most {MAX_ITEMS} lines.")
    items = []
    for index, entry in enumerate(raw, start=1):
        if not isinstance(entry, dict):
            raise ValueError(f"Line {index} is not a line item.")
        item = cast("dict[str, Any]", entry)
        description = kit.text(
            item.get("description"),
            f"a description for line {index}",
            max_len=300,
            required=True,
        )
        qty = quantity(item.get("quantity"))
        if item.get("unit_cents") not in (None, ""):
            unit = kit.integer(
                item.get("unit_cents"),
                f"the price on line {index}",
                minimum=0,
                maximum=10_000_000_00,
            )
        else:
            unit = kit.cents(
                item.get("unit_price"), f"the price on line {index}", default=0
            )
        items.append(
            {
                "description": description,
                "quantity": format(qty, "f"),
                "unit_cents": unit,
                "amount_cents": line_amount(qty, unit),
            }
        )
    return items


def totals(
    items: list[dict[str, Any]],
    discount_cents: int,
    tax_rate_bp: int,
    credit_cents: int = 0,
) -> dict[str, int]:
    """Subtotal, less the discount, plus tax; then less a credit (a deposit already invoiced, tax included)."""
    subtotal = sum(int(item["amount_cents"]) for item in items)
    discount = max(0, min(int(discount_cents or 0), subtotal))
    taxable = subtotal - discount
    tax = tax_of(taxable, int(tax_rate_bp or 0))
    credit = max(0, min(int(credit_cents or 0), taxable + tax))
    return {
        "subtotal_cents": subtotal,
        "discount_cents": discount,
        "tax_cents": tax,
        "credit_cents": credit,
        "total_cents": taxable + tax - credit,
    }


def _money_fields(
    connection: sqlite3.Connection,
    raw: dict[str, Any],
    current: dict[str, Any] | None,
    *,
    credit_cents: int | None = None,
) -> dict[str, Any]:
    base = current or {}
    items = (
        clean_items(raw["items"]) if "items" in raw else list(base.get("items") or [])
    )
    discount = (
        cents_field(raw.get("discount_cents"), "the discount")
        if "discount_cents" in raw
        else int(base.get("discount_cents") or 0)
    )
    if "tax_rate_bp" in raw and raw.get("tax_rate_bp") not in (None, ""):
        rate = kit.integer(
            raw.get("tax_rate_bp"), "the tax rate", minimum=0, maximum=5000
        )
    elif current is not None:
        rate = int(base.get("tax_rate_bp") or 0)
    else:
        rate = int(kit.business(connection)["tax_rate_bp"] or 0)
    credit = (
        int(base.get("credit_cents") or 0) if credit_cents is None else credit_cents
    )
    return {
        "items": items,
        "tax_rate_bp": rate,
        **totals(items, discount, rate, credit),
    }


def cents_field(value: Any, label: str, *, default: int = 0, minimum: int = 0) -> int:
    """A ``*_cents`` field: integer cents as the app sends them (``1250`` is 12.50)."""
    if value is None or value == "":
        return default
    if isinstance(value, float) and value.is_integer():
        value = int(value)
    return kit.integer(value, label, minimum=minimum, maximum=10_000_000_00)


def deposit_bp(value: Any) -> int:
    """A deposit typed as a percent ("30", "12.5", "") -> hundredths of a percent (3000, 1250, 0)."""
    text = str(value if value is not None else "").strip().rstrip("%").strip()
    if not text:
        return 0
    try:
        number = Decimal(text)
    except InvalidOperation:
        raise ValueError("The deposit must be a percent like 30.") from None
    if not number.is_finite() or number < 0 or number > 100:
        raise ValueError("The deposit must be between 0 and 100 percent.")
    return int((number * 100).quantize(Decimal("1"), rounding=ROUND_HALF_UP))


def _percent_number(bp: int) -> int | float:
    """Hundredths of a percent as the percent people type: 3000 -> 30, 1250 -> 12.5."""
    return bp // 100 if bp % 100 == 0 else bp / 100


def deposit_of(document: dict[str, Any]) -> dict[str, int]:
    """The deposit a proposal asks for: its share of the total before tax, then the same tax rate."""
    bp = int(document.get("deposit_bp") or 0)
    if bp <= 0:
        return {"deposit_bp": 0, "net_cents": 0, "tax_cents": 0, "total_cents": 0}
    taxable = int(document["subtotal_cents"]) - int(document["discount_cents"])
    net = tax_of(
        taxable, bp
    )  # tax_of is "x hundredths of a percent of", rounded half up
    tax = tax_of(net, int(document.get("tax_rate_bp") or 0))
    return {
        "deposit_bp": bp,
        "net_cents": net,
        "tax_cents": tax,
        "total_cents": net + tax,
    }


def _number(prefix: str, seq: int) -> str:
    return f"{prefix}{seq:04d}"


# ---------------------------------------------------------------------------
# clients
# ---------------------------------------------------------------------------


def _client_fields(
    raw: dict[str, Any], current: dict[str, Any] | None = None
) -> dict[str, Any]:
    merged = {**(current or {}), **raw}
    return {
        "name": kit.text(
            merged.get("name"), "the client's name", max_len=120, required=True
        ),
        "company": kit.text(merged.get("company"), "the company", max_len=120),
        "email": kit.email(merged.get("email")),
        "phone": kit.phone(merged.get("phone")),
        "tags": kit.dumps(kit.tags(merged.get("tags") or [])),
        "notes": kit.text(merged.get("notes"), "notes", max_len=4000, multiline=True),
        "status": kit.choice(
            merged.get("status"), "the status", CLIENT_STATUSES, default="lead"
        ),
    }


def create_client(
    connection: sqlite3.Connection, raw: dict[str, Any]
) -> dict[str, Any]:
    values = {
        "id": kit.new_id(),
        **_client_fields(raw),
        "created_at": kit.now(),
        "updated_at": kit.now(),
    }
    store.insert(connection, "st_clients", values)
    kit.log(
        connection,
        APP,
        "client",
        f"Client added: {values['name']}",
        ref_type="client",
        ref_id=values["id"],
    )
    connection.commit()
    return get_client(connection, values["id"])


def get_client(connection: sqlite3.Connection, client_id: str) -> dict[str, Any]:
    client = store.get(connection, "st_clients", client_id, "client")
    money = client_money(connection).get(
        client_id, {"billed_cents": 0, "paid_cents": 0}
    )
    return {
        **client,
        **money,
        "outstanding_cents": max(0, money["billed_cents"] - money["paid_cents"]),
    }


def update_client(
    connection: sqlite3.Connection, client_id: str, raw: dict[str, Any]
) -> dict[str, Any]:
    current = store.get(connection, "st_clients", client_id, "client")
    values = _client_fields(raw, current)
    values["updated_at"] = kit.now()
    kit.update_row(connection, "st_clients", client_id, values)
    connection.commit()
    return get_client(connection, client_id)


def delete_client(connection: sqlite3.Connection, client_id: str) -> None:
    client = store.get(connection, "st_clients", client_id, "client")
    if kit.count(
        connection, "SELECT COUNT(*) FROM st_invoices WHERE client_id = ?", (client_id,)
    ):
        raise ValueError(
            f"{client['name']} has invoices. Mark the client as past instead, so your records stay complete."
        )
    for project in store.rows(
        connection, "SELECT id FROM st_projects WHERE client_id = ?", (client_id,)
    ):
        _delete_project_rows(connection, project["id"])
    for proposal in store.rows(
        connection, "SELECT id FROM st_proposals WHERE client_id = ?", (client_id,)
    ):
        _revoke(connection, "proposal", proposal["id"])
    connection.execute("DELETE FROM st_proposals WHERE client_id = ?", (client_id,))
    connection.execute("DELETE FROM st_retainers WHERE client_id = ?", (client_id,))
    connection.execute("DELETE FROM st_contacts WHERE client_id = ?", (client_id,))
    connection.execute("DELETE FROM st_deals WHERE client_id = ?", (client_id,))
    connection.execute("DELETE FROM st_activities WHERE client_id = ?", (client_id,))
    connection.execute("DELETE FROM st_clients WHERE id = ?", (client_id,))
    kit.log(
        connection,
        APP,
        "client",
        f"Client deleted: {client['name']}",
        ref_type="client",
        ref_id=client_id,
    )
    connection.commit()


def client_money(connection: sqlite3.Connection) -> dict[str, dict[str, int]]:
    """Per client: billed (invoices sent, not void) and paid (payments on invoices that are not void)."""
    out: dict[str, dict[str, int]] = {}
    for row in kit.fetch_all(
        connection,
        "SELECT client_id, SUM(total_cents) AS billed FROM st_invoices WHERE state = 'sent' GROUP BY client_id",
    ):
        out.setdefault(row["client_id"], {"billed_cents": 0, "paid_cents": 0})[
            "billed_cents"
        ] = int(row["billed"] or 0)
    for row in kit.fetch_all(
        connection,
        """SELECT i.client_id, SUM(p.amount_cents) AS paid FROM st_payments p JOIN st_invoices i ON i.id = p.invoice_id
        WHERE i.state != 'void' GROUP BY i.client_id""",
    ):
        out.setdefault(row["client_id"], {"billed_cents": 0, "paid_cents": 0})[
            "paid_cents"
        ] = int(row["paid"] or 0)
    return out


def list_clients(
    connection: sqlite3.Connection, *, q: str = "", status: str = "", tag: str = ""
) -> list[dict[str, Any]]:
    clauses, params = [], []
    if q:
        clauses.append(
            "(name LIKE ? OR company LIKE ? OR email LIKE ? OR phone LIKE ?)"
        )
        like = f"%{q.strip()[:80]}%"
        params += [like, like, like, like]
    if status:
        clauses.append("status = ?")
        params.append(kit.choice(status, "the status", CLIENT_STATUSES))
    where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    clients = store.rows(
        connection,
        f"SELECT * FROM st_clients {where} ORDER BY updated_at DESC LIMIT 2000",
        tuple(params),
    )  # noqa: S608
    wanted = tag.strip().lower()
    if wanted:
        clients = [client for client in clients if wanted in client["tags"]]
    money = client_money(connection)
    for client in clients:
        figures = money.get(client["id"], {"billed_cents": 0, "paid_cents": 0})
        client.update(figures)
        client["outstanding_cents"] = max(
            0, figures["billed_cents"] - figures["paid_cents"]
        )
    return clients


def client_detail(
    connection: sqlite3.Connection, client_id: str, today: str
) -> dict[str, Any]:
    from aira.suite.studio import sales

    return {
        "client": get_client(connection, client_id),
        "contacts": sales.list_contacts(connection, client_id),
        "deals": sales.list_deals(connection, client_id=client_id),
        "activities": sales.list_activities(
            connection, today=today, client_id=client_id
        ),
        "proposals": [
            proposal_view(p, today)
            for p in store.rows(
                connection,
                "SELECT * FROM st_proposals WHERE client_id = ? ORDER BY created_at DESC",
                (client_id,),
            )
        ],
        "projects": store.rows(
            connection,
            "SELECT * FROM st_projects WHERE client_id = ? ORDER BY created_at DESC",
            (client_id,),
        ),
        "invoices": list_invoices(connection, today=today, client_id=client_id),
        "retainers": store.rows(
            connection,
            "SELECT * FROM st_retainers WHERE client_id = ? ORDER BY created_at DESC",
            (client_id,),
        ),
    }


def _client_name(connection: sqlite3.Connection, client_id: str) -> dict[str, str]:
    row = kit.fetch_one(
        connection,
        "SELECT name, company, email FROM st_clients WHERE id = ?",
        (client_id,),
    )
    return {
        "client_name": (row or {}).get("name", ""),
        "client_company": (row or {}).get("company", ""),
        "client_email": (row or {}).get("email", ""),
    }


def _require_client(connection: sqlite3.Connection, client_id: Any) -> str:
    cid = kit.text(client_id, "a client", max_len=64, required=True)
    store.get(connection, "st_clients", cid, "client")
    return cid


# ---------------------------------------------------------------------------
# public links (one per proposal / invoice)
# ---------------------------------------------------------------------------


def _revoke(connection: sqlite3.Connection, kind: str, ref_id: str) -> None:
    from aira.suite import public

    public.revoke(connection, APP, kind, ref_id)


# ---------------------------------------------------------------------------
# invoices
# ---------------------------------------------------------------------------


def _paid(connection: sqlite3.Connection, invoice_id: str) -> int:
    return kit.count(
        connection,
        "SELECT COALESCE(SUM(amount_cents), 0) FROM st_payments WHERE invoice_id = ?",
        (invoice_id,),
    )


def invoice_status(invoice: dict[str, Any], paid_cents: int, today: str) -> str:
    if invoice["state"] == "void":
        return "void"
    total = int(invoice["total_cents"])
    if invoice["state"] == "draft" and paid_cents == 0:
        return "draft"
    if paid_cents >= total:
        return "paid"
    if invoice["due_date"] and invoice["due_date"] < today:
        return "overdue"
    if paid_cents > 0:
        return "partially_paid"
    return "sent"


def invoice_view(
    connection: sqlite3.Connection,
    invoice: dict[str, Any],
    today: str,
    *,
    payments: bool = False,
) -> dict[str, Any]:
    paid = _paid(connection, invoice["id"])
    view = {
        **invoice,
        **_client_name(connection, invoice["client_id"]),
        "paid_cents": paid,
        "balance_cents": 0
        if invoice["state"] == "void"
        else max(0, int(invoice["total_cents"]) - paid),
        "status": invoice_status(invoice, paid, today),
    }
    if invoice["due_date"] and view["status"] == "overdue":
        view["days_overdue"] = (_date(today) - _date(invoice["due_date"])).days
    if payments:
        view["payments"] = store.rows(
            connection,
            "SELECT * FROM st_payments WHERE invoice_id = ? ORDER BY day, created_at",
            (invoice["id"],),
        )
    return view


def get_invoice(
    connection: sqlite3.Connection, invoice_id: str, today: str
) -> dict[str, Any]:
    return invoice_view(
        connection,
        store.get(connection, "st_invoices", invoice_id, "invoice"),
        today,
        payments=True,
    )


def list_invoices(
    connection: sqlite3.Connection,
    *,
    today: str,
    status: str = "",
    client_id: str = "",
    q: str = "",
) -> list[dict[str, Any]]:
    clauses, params = [], []
    if client_id:
        clauses.append("client_id = ?")
        params.append(client_id)
    if q:
        clauses.append(
            "(number LIKE ? OR client_id IN (SELECT id FROM st_clients WHERE name LIKE ? OR company LIKE ?))"
        )
        like = f"%{q.strip()[:80]}%"
        params += [like, like, like]
    where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    invoices = [
        invoice_view(connection, row, today)
        for row in store.rows(
            connection,
            f"SELECT * FROM st_invoices {where} ORDER BY seq DESC LIMIT 2000",
            tuple(params),
        )  # noqa: S608
    ]
    if status:
        if status == "unpaid":
            invoices = [
                i
                for i in invoices
                if i["status"] in ("sent", "partially_paid", "overdue")
            ]
        else:
            wanted = kit.choice(status, "the status", INVOICE_STATUSES)
            invoices = [i for i in invoices if i["status"] == wanted]
    return invoices


def create_invoice(
    connection: sqlite3.Connection,
    raw: dict[str, Any],
    *,
    today: str,
    retainer: tuple[str, str] | None = None,
    credit: tuple[int, str, str] | None = None,
    kind: str = "",
) -> dict[str, Any]:
    """A draft invoice. ``credit`` is (cents, label, deposit invoice id): what was already invoiced as a deposit,
    taken off the total. ``kind`` is "deposit" for a proposal's deposit invoice, otherwise empty."""
    client_id = _require_client(connection, raw.get("client_id"))
    terms = int(settings(connection)["payment_terms_days"])
    issue = kit.day(raw.get("issue_date")) or today
    due = kit.day(raw.get("due_date")) or add_days(issue, terms)
    if due < issue:
        raise ValueError("The due date cannot be before the issue date.")
    project_id = kit.text(raw.get("project_id"), "a project", max_len=64)
    if project_id:
        store.get(connection, "st_projects", project_id, "project")
    prefix = settings(connection)["invoice_prefix"]
    values = {
        "id": kit.new_id(),
        "client_id": client_id,
        "project_id": project_id,
        "proposal_id": kit.text(raw.get("proposal_id"), "a proposal", max_len=64),
        "retainer_id": retainer[0] if retainer else None,
        "period": retainer[1] if retainer else None,
        "issue_date": issue,
        "due_date": due,
        "state": "draft",
        "notes": kit.text(
            raw.get("notes"), "a note for the client", max_len=2000, multiline=True
        ),
        "internal_notes": kit.text(
            raw.get("internal_notes"), "internal notes", max_len=2000, multiline=True
        ),
        "customer_notices": "[]",
        "credit_label": (credit[1] if credit else "")[:120],
        "credit_invoice_id": credit[2] if credit else "",
        "kind": kind if kind in ("", "deposit") else "",
        "created_at": kit.now(),
        "updated_at": kit.now(),
    }
    money = _money_fields(
        connection, raw, None, credit_cents=credit[0] if credit else 0
    )
    if not money["items"]:
        raise ValueError("Add at least one line to the invoice.")
    values.update({**money, "items": kit.dumps(money["items"])})
    store.insert_numbered(
        connection, "st_invoices", values, lambda seq: _number(prefix, seq)
    )
    kit.log(
        connection,
        APP,
        "invoice",
        f"Invoice {values['number']} created",
        ref_type="invoice",
        ref_id=values["id"],
        amount_cents=money["total_cents"],
    )
    connection.commit()
    return get_invoice(connection, values["id"], today)


def update_invoice(
    connection: sqlite3.Connection, invoice_id: str, raw: dict[str, Any], *, today: str
) -> dict[str, Any]:
    current = store.get(connection, "st_invoices", invoice_id, "invoice")
    if current["state"] == "void":
        raise ValueError("A void invoice cannot be changed.")
    values: dict[str, Any] = {}
    if any(key in raw for key in ("items", "discount_cents", "tax_rate_bp")):
        money = _money_fields(connection, raw, current)
        if not money["items"]:
            raise ValueError("An invoice needs at least one line.")
        paid = _paid(connection, invoice_id)
        if money["total_cents"] < paid:
            raise ValueError(
                "The new total would be less than what was already paid. Delete a payment first."
            )
        values.update({**money, "items": kit.dumps(money["items"])})
    if "client_id" in raw:
        values["client_id"] = _require_client(connection, raw["client_id"])
    for key, label in (("issue_date", "the issue date"), ("due_date", "the due date")):
        if key in raw:
            values[key] = kit.day(raw[key], label, required=True)
    if (values.get("due_date") or current["due_date"]) < (
        values.get("issue_date") or current["issue_date"]
    ):
        raise ValueError("The due date cannot be before the issue date.")
    if "notes" in raw:
        values["notes"] = kit.text(
            raw["notes"], "a note for the client", max_len=2000, multiline=True
        )
    if "internal_notes" in raw:
        values["internal_notes"] = kit.text(
            raw["internal_notes"], "internal notes", max_len=2000, multiline=True
        )
    values["updated_at"] = kit.now()
    kit.update_row(connection, "st_invoices", invoice_id, values)
    connection.commit()
    return get_invoice(connection, invoice_id, today)


def mark_sent(connection: sqlite3.Connection, invoice_id: str) -> None:
    invoice = store.get(connection, "st_invoices", invoice_id, "invoice")
    if invoice["state"] == "void":
        raise ValueError("A void invoice cannot be sent.")
    if invoice["state"] == "draft":
        kit.update_row(
            connection,
            "st_invoices",
            invoice_id,
            {"state": "sent", "sent_at": kit.now(), "updated_at": kit.now()},
        )
        kit.log(
            connection,
            APP,
            "invoice",
            f"Invoice {invoice['number']} sent",
            ref_type="invoice",
            ref_id=invoice_id,
            amount_cents=invoice["total_cents"],
        )
        connection.commit()


def _unbill_time(connection: sqlite3.Connection, invoice_id: str) -> None:
    connection.execute(
        "UPDATE st_time SET invoice_id = '' WHERE invoice_id = ?", (invoice_id,)
    )


def _give_back_deposit_credit(
    connection: sqlite3.Connection, deposit: dict[str, Any]
) -> None:
    """A deposit invoice is voided or deleted: the final invoices that took it off charge the full amount again."""
    if deposit.get("kind") != "deposit":
        return
    for final in store.rows(
        connection,
        "SELECT * FROM st_invoices WHERE credit_invoice_id = ? AND id != ? AND state != 'void' AND credit_cents > 0",
        (deposit["id"], deposit["id"]),
    ):
        money = totals(final["items"], final["discount_cents"], final["tax_rate_bp"], 0)
        kit.update_row(
            connection,
            "st_invoices",
            final["id"],
            {
                **money,
                "credit_label": "",
                "credit_invoice_id": "",
                "updated_at": kit.now(),
            },
        )
        kit.log(
            connection,
            APP,
            "invoice",
            f"Invoice {final['number']} no longer takes off deposit {deposit['number']}",
            detail="The deposit invoice was cancelled, so the full amount is due on this invoice.",
            ref_type="invoice",
            ref_id=final["id"],
            amount_cents=money["total_cents"],
        )


def _free_proposal(connection: sqlite3.Connection, invoice_id: str) -> None:
    """The final invoice of a proposal is gone (voided or deleted): its proposal can be started again."""
    connection.execute(
        "UPDATE st_proposals SET invoice_id = '' WHERE invoice_id = ?", (invoice_id,)
    )


def void_invoice(
    connection: sqlite3.Connection, invoice_id: str, *, today: str
) -> dict[str, Any]:
    invoice = store.get(connection, "st_invoices", invoice_id, "invoice")
    if invoice["state"] == "void":
        return get_invoice(connection, invoice_id, today)
    for payment in store.rows(
        connection, "SELECT id FROM st_payments WHERE invoice_id = ?", (invoice_id,)
    ):
        kit.void_revenue(connection, APP, "payment", payment["id"])
    _unbill_time(connection, invoice_id)
    kit.update_row(
        connection,
        "st_invoices",
        invoice_id,
        {"state": "void", "voided_at": kit.now(), "updated_at": kit.now()},
    )
    _give_back_deposit_credit(connection, invoice)
    _free_proposal(connection, invoice_id)
    kit.log(
        connection,
        APP,
        "invoice",
        f"Invoice {invoice['number']} voided",
        ref_type="invoice",
        ref_id=invoice_id,
    )
    connection.commit()
    return get_invoice(connection, invoice_id, today)


def delete_invoice(connection: sqlite3.Connection, invoice_id: str) -> None:
    invoice = store.get(connection, "st_invoices", invoice_id, "invoice")
    if _paid(connection, invoice_id):
        raise ValueError(
            "This invoice has payments. Void it instead, so the payments stay on record."
        )
    if invoice["state"] == "sent":
        raise ValueError("A sent invoice cannot be deleted. Void it instead.")
    _unbill_time(connection, invoice_id)
    _revoke(connection, "invoice", invoice_id)
    _give_back_deposit_credit(connection, invoice)
    _free_proposal(connection, invoice_id)
    connection.execute(
        "UPDATE st_proposals SET deposit_invoice_id = '' WHERE deposit_invoice_id = ?",
        (invoice_id,),
    )
    connection.execute(
        "UPDATE st_deals SET invoice_id = '' WHERE invoice_id = ?", (invoice_id,)
    )
    connection.execute(
        "UPDATE st_retainers SET last_invoice_id = '' WHERE last_invoice_id = ?",
        (invoice_id,),
    )
    connection.execute("DELETE FROM st_invoices WHERE id = ?", (invoice_id,))
    kit.log(
        connection,
        APP,
        "invoice",
        f"Invoice {invoice['number']} deleted",
        ref_type="invoice",
        ref_id=invoice_id,
    )
    connection.commit()


def add_payment(
    connection: sqlite3.Connection, invoice_id: str, raw: dict[str, Any], *, today: str
) -> dict[str, Any]:
    """Record money received. With no amount it is the whole balance ("mark paid")."""
    invoice = store.get(connection, "st_invoices", invoice_id, "invoice")
    if invoice["state"] == "void":
        raise ValueError("A void invoice cannot be paid.")
    balance = max(0, int(invoice["total_cents"]) - _paid(connection, invoice_id))
    if balance <= 0:
        raise ValueError("This invoice is already paid in full.")
    if raw.get("amount_cents") not in (None, ""):
        amount = cents_field(raw.get("amount_cents"), "the amount")
    else:
        amount = kit.cents(raw.get("amount"), "the amount", default=balance)
    if amount <= 0:
        raise ValueError("The amount must be more than zero.")
    if amount > balance:
        raise ValueError(
            f"The amount is more than the {kit.fmt_money(balance, kit.business(connection)['currency'])} still due."
        )
    payment = {
        "id": kit.new_id(),
        "invoice_id": invoice_id,
        "amount_cents": amount,
        "day": kit.day(raw.get("day"), "the payment date") or today,
        "method": kit.choice(
            raw.get("method"),
            "the payment method",
            PAYMENT_METHODS,
            default="bank_transfer",
        ),
        "note": kit.text(raw.get("note"), "a note", max_len=300),
        "created_at": kit.now(),
    }
    store.insert(connection, "st_payments", payment)
    if invoice["state"] == "draft":
        kit.update_row(
            connection,
            "st_invoices",
            invoice_id,
            {"state": "sent", "sent_at": kit.now()},
        )
    kit.update_row(connection, "st_invoices", invoice_id, {"updated_at": kit.now()})
    client = _client_name(connection, invoice["client_id"])
    kit.record_revenue(
        connection,
        APP,
        source_type="payment",
        source_id=payment["id"],
        amount_cents=amount,
        label=f"Invoice {invoice['number']}",
        customer=client["client_company"] or client["client_name"],
        at=f"{payment['day']}T12:00:00Z",
    )
    remaining = balance - amount
    kit.log(
        connection,
        APP,
        "payment",
        f"Payment received for {invoice['number']}"
        + ("" if remaining else " (paid in full)"),
        ref_type="invoice",
        ref_id=invoice_id,
        amount_cents=amount,
    )
    connection.commit()
    return get_invoice(connection, invoice_id, today)


def delete_payment(
    connection: sqlite3.Connection, invoice_id: str, payment_id: str, *, today: str
) -> dict[str, Any]:
    payment = store.get(connection, "st_payments", payment_id, "payment")
    if payment["invoice_id"] != invoice_id:
        raise LookupError("That payment was not found.")
    invoice = store.get(connection, "st_invoices", invoice_id, "invoice")
    connection.execute("DELETE FROM st_payments WHERE id = ?", (payment_id,))
    kit.void_revenue(connection, APP, "payment", payment_id)
    kit.log(
        connection,
        APP,
        "payment",
        f"Payment removed from {invoice['number']}",
        ref_type="invoice",
        ref_id=invoice_id,
        amount_cents=-int(payment["amount_cents"]),
    )
    connection.commit()
    return get_invoice(connection, invoice_id, today)


def add_customer_notice(
    connection: sqlite3.Connection, invoice_id: str, *, reference: str, note: str
) -> dict[str, Any]:
    invoice = store.get(connection, "st_invoices", invoice_id, "invoice")
    notices = list(invoice["customer_notices"] or [])[-19:]
    notice = {"at": kit.now(), "reference": reference, "note": note}
    notices.append(notice)
    kit.update_row(
        connection,
        "st_invoices",
        invoice_id,
        {"customer_notices": kit.dumps(notices), "updated_at": kit.now()},
    )
    kit.log(
        connection,
        APP,
        "payment_notice",
        f"Client says invoice {invoice['number']} was paid",
        detail=" ".join(
            part
            for part in (f"Reference {reference}." if reference else "", note)
            if part
        ),
        ref_type="invoice",
        ref_id=invoice_id,
        public=True,
    )
    connection.commit()
    return notice


# ---------------------------------------------------------------------------
# proposals
# ---------------------------------------------------------------------------


def proposal_view(proposal: dict[str, Any], today: str) -> dict[str, Any]:
    status = proposal["status"]
    if status == "sent" and proposal["valid_until"] and proposal["valid_until"] < today:
        status = "expired"
    return {
        **proposal,
        "status": status,
        # both made: a project or invoice deleted later can be made again with the same button
        "converted": bool(proposal["project_id"] and proposal["invoice_id"]),
    }


def get_proposal(
    connection: sqlite3.Connection, proposal_id: str, today: str
) -> dict[str, Any]:
    proposal = store.get(connection, "st_proposals", proposal_id, "proposal")
    return {
        **proposal_view(proposal, today),
        **_client_name(connection, proposal["client_id"]),
        "deposit": deposit_of(proposal),
    }


def list_proposals(
    connection: sqlite3.Connection, *, today: str, status: str = "", client_id: str = ""
) -> list[dict[str, Any]]:
    clauses, params = [], []
    if client_id:
        clauses.append("client_id = ?")
        params.append(client_id)
    where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    proposals = [
        {**proposal_view(row, today), **_client_name(connection, row["client_id"])}
        for row in store.rows(
            connection,
            f"SELECT * FROM st_proposals {where} ORDER BY seq DESC LIMIT 2000",
            tuple(params),
        )  # noqa: S608
    ]
    if status:
        wanted = kit.choice(status, "the status", PROPOSAL_STATUSES)
        proposals = [p for p in proposals if p["status"] == wanted]
    return proposals


def _proposal_text(
    raw: dict[str, Any], current: dict[str, Any] | None
) -> dict[str, Any]:
    merged = {**(current or {}), **raw}
    return {
        "title": kit.text(merged.get("title"), "a title", max_len=200, required=True),
        "intro": kit.text(
            merged.get("intro"), "the introduction", max_len=6000, multiline=True
        ),
        "scope": kit.text(
            merged.get("scope"), "the scope", max_len=12000, multiline=True
        ),
    }


def create_proposal(
    connection: sqlite3.Connection, raw: dict[str, Any], *, today: str
) -> dict[str, Any]:
    client_id = _require_client(connection, raw.get("client_id"))
    config = settings(connection)
    money = _money_fields(connection, raw, None)
    money.pop("credit_cents", None)
    values = {
        "id": kit.new_id(),
        "client_id": client_id,
        **_proposal_text(raw, None),
        **money,
        "items": kit.dumps(money["items"]),
        "valid_until": kit.day(raw.get("valid_until"), "the valid-until date")
        or add_days(today, int(config["proposal_valid_days"])),
        "deposit_bp": deposit_bp(
            raw.get("deposit_percent")
            if "deposit_percent" in raw
            else config["default_deposit_percent"]
        ),
        "status": "draft",
        "created_at": kit.now(),
        "updated_at": kit.now(),
    }
    store.insert_numbered(
        connection,
        "st_proposals",
        values,
        lambda seq: _number(config["proposal_prefix"], seq),
    )
    kit.log(
        connection,
        APP,
        "proposal",
        f"Proposal {values['number']} created: {values['title']}",
        ref_type="proposal",
        ref_id=values["id"],
        amount_cents=money["total_cents"],
    )
    connection.commit()
    return get_proposal(connection, values["id"], today)


def update_proposal(
    connection: sqlite3.Connection, proposal_id: str, raw: dict[str, Any], *, today: str
) -> dict[str, Any]:
    current = store.get(connection, "st_proposals", proposal_id, "proposal")
    if current["status"] in ("accepted", "declined"):
        raise ValueError(
            f"An {current['status']} proposal cannot be changed. Make a new one instead."
        )
    values: dict[str, Any] = {**_proposal_text(raw, current)}
    if any(key in raw for key in ("items", "discount_cents", "tax_rate_bp")):
        money = _money_fields(connection, raw, current, credit_cents=0)
        money.pop("credit_cents", None)
        values.update({**money, "items": kit.dumps(money["items"])})
    if "deposit_percent" in raw:
        values["deposit_bp"] = deposit_bp(raw.get("deposit_percent"))
    if "client_id" in raw:
        values["client_id"] = _require_client(connection, raw["client_id"])
    if "valid_until" in raw:
        values["valid_until"] = kit.day(
            raw["valid_until"], "the valid-until date", required=True
        )
    values["updated_at"] = kit.now()
    kit.update_row(connection, "st_proposals", proposal_id, values)
    connection.commit()
    return get_proposal(connection, proposal_id, today)


def delete_proposal(connection: sqlite3.Connection, proposal_id: str) -> None:
    proposal = store.get(connection, "st_proposals", proposal_id, "proposal")
    if proposal["status"] == "accepted":
        raise ValueError(
            "An accepted proposal is your client's signed agreement and cannot be deleted."
        )
    _revoke(connection, "proposal", proposal_id)
    connection.execute(
        "UPDATE st_deals SET proposal_id = '' WHERE proposal_id = ?", (proposal_id,)
    )
    connection.execute("DELETE FROM st_proposals WHERE id = ?", (proposal_id,))
    kit.log(
        connection,
        APP,
        "proposal",
        f"Proposal {proposal['number']} deleted",
        ref_type="proposal",
        ref_id=proposal_id,
    )
    connection.commit()


def mark_proposal_sent(connection: sqlite3.Connection, proposal_id: str) -> None:
    proposal = store.get(connection, "st_proposals", proposal_id, "proposal")
    if proposal["status"] == "draft":
        kit.update_row(
            connection,
            "st_proposals",
            proposal_id,
            {"status": "sent", "sent_at": kit.now(), "updated_at": kit.now()},
        )
        kit.log(
            connection,
            APP,
            "proposal",
            f"Proposal {proposal['number']} sent",
            ref_type="proposal",
            ref_id=proposal_id,
            amount_cents=proposal["total_cents"],
        )
        connection.commit()


def set_proposal_status(
    connection: sqlite3.Connection,
    proposal_id: str,
    status: str,
    *,
    today: str,
    source: Any = None,
) -> dict[str, Any]:
    """The owner records an outcome by hand (agreed on a call, declined by email) or reopens a draft."""
    wanted = kit.choice(status, "the status", ("draft", "sent", "accepted", "declined"))
    proposal = store.get(connection, "st_proposals", proposal_id, "proposal")
    if proposal["status"] == "accepted" and wanted != "accepted":
        raise ValueError("An accepted proposal stays accepted.")
    if proposal["status"] == wanted == "accepted":
        return get_proposal(
            connection, proposal_id, today
        )  # already done: no second deposit invoice
    values: dict[str, Any] = {"status": wanted, "updated_at": kit.now()}
    if wanted == "accepted" and proposal["status"] != "accepted":
        values.update(
            {"accepted_at": kit.now(), "accepted_name": "Recorded by the business"}
        )
    if wanted == "declined":
        values["declined_at"] = kit.now()
    if wanted == "sent" and not proposal["sent_at"]:
        values["sent_at"] = kit.now()
    if not _change_if(connection, proposal_id, proposal["status"], values):
        fresh = store.get(connection, "st_proposals", proposal_id, "proposal")
        if fresh["status"] == wanted:
            return get_proposal(connection, proposal_id, today)
        raise ValueError(
            "Your client answered this proposal just now. Open it again to see their answer."
            if fresh["status"] in ("accepted", "declined")
            else "This proposal changed just now. Open it again and try once more."
        )
    kit.log(
        connection,
        APP,
        "proposal",
        f"Proposal {proposal['number']} marked {wanted}",
        ref_type="proposal",
        ref_id=proposal_id,
    )
    connection.commit()
    if wanted == "accepted":
        _after_accept(connection, proposal_id, today=today, source=source)
    return get_proposal(connection, proposal_id, today)


def _change_if(
    connection: sqlite3.Connection,
    proposal_id: str,
    was: str,
    values: dict[str, Any],
) -> bool:
    """Change a proposal only if its status is still ``was``. Two answers arriving together (the client accepts in
    two tabs, or while the business marks it accepted) cannot both win: the second finds the status changed."""
    columns = list(values)
    cursor = connection.execute(
        f"UPDATE st_proposals SET {', '.join(f'{column} = ?' for column in columns)} "  # noqa: S608 - fixed names
        "WHERE id = ? AND status = ?",
        (*[values[column] for column in columns], proposal_id, was),
    )
    if cursor.rowcount == 1:
        return True
    connection.commit()
    return False


def _win_linked_deals(connection: sqlite3.Connection, proposal_id: str) -> None:
    from aira.suite.studio import sales

    sales.on_proposal_accepted(connection, proposal_id)


def _open_for_answer(proposal: dict[str, Any], today: str) -> None:
    status = proposal_view(proposal, today)["status"]
    if status == "accepted":
        raise ValueError("This proposal was already accepted.")
    if status == "declined":
        raise ValueError("This proposal was declined.")
    if status == "expired":
        raise ValueError("This proposal has expired. Ask for an updated one.")
    if status != "sent":
        raise ValueError("This proposal is not open for an answer yet.")


def accept_proposal(
    connection: sqlite3.Connection,
    proposal_id: str,
    *,
    name: str,
    visitor: dict[str, Any],
    today: str,
) -> dict[str, Any]:
    proposal = store.get(connection, "st_proposals", proposal_id, "proposal")
    _open_for_answer(proposal, today)
    signer = kit.text(name, "your full name", max_len=120, required=True)
    if len(signer) < 2:
        raise ValueError("Type your full name to accept.")
    if not _change_if(
        connection,
        proposal_id,
        "sent",
        {
            "status": "accepted",
            "accepted_at": kit.now(),
            "accepted_name": signer,
            "accepted_from": str(visitor.get("client") or "")[:64],
            "updated_at": kit.now(),
        },
    ):
        _open_for_answer(
            store.get(connection, "st_proposals", proposal_id, "proposal"), today
        )
        raise ValueError("This proposal is not open for an answer yet.")
    kit.log(
        connection,
        APP,
        "proposal_accepted",
        f"{signer} accepted proposal {proposal['number']}",
        detail=proposal["title"],
        ref_type="proposal",
        ref_id=proposal_id,
        amount_cents=proposal["total_cents"],
        public=True,
    )
    connection.commit()
    _after_accept(connection, proposal_id, today=today, source=visitor)
    return get_proposal(connection, proposal_id, today)


def decline_proposal(
    connection: sqlite3.Connection, proposal_id: str, *, reason: str, today: str
) -> dict[str, Any]:
    proposal = store.get(connection, "st_proposals", proposal_id, "proposal")
    _open_for_answer(proposal, today)
    why = kit.text(reason, "a reason", max_len=1000, multiline=True)
    if not _change_if(
        connection,
        proposal_id,
        "sent",
        {
            "status": "declined",
            "declined_at": kit.now(),
            "decline_reason": why,
            "updated_at": kit.now(),
        },
    ):
        _open_for_answer(
            store.get(connection, "st_proposals", proposal_id, "proposal"), today
        )
        raise ValueError("This proposal is not open for an answer yet.")
    kit.log(
        connection,
        APP,
        "proposal_declined",
        f"Proposal {proposal['number']} was declined",
        detail=why,
        ref_type="proposal",
        ref_id=proposal_id,
        public=True,
    )
    connection.commit()
    return get_proposal(connection, proposal_id, today)


def convert_proposal(
    connection: sqlite3.Connection, proposal_id: str, *, today: str
) -> dict[str, Any]:
    """An accepted proposal becomes a project (budget = the total) and a draft invoice with the same lines.
    Converting again returns what was made the first time; a project deleted or an invoice voided or deleted since
    is made again. The deposit invoice joins the project, so the project's earnings include it."""
    proposal = store.get(connection, "st_proposals", proposal_id, "proposal")
    if proposal["status"] != "accepted":
        raise ValueError(
            "Only an accepted proposal can be turned into a project and an invoice."
        )
    project_id, invoice_id = proposal["project_id"], proposal["invoice_id"]
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
    if not project_id:
        project = create_project(
            connection,
            {
                "client_id": proposal["client_id"],
                "name": proposal["title"],
                "status": "active",
                "budget_cents": proposal["total_cents"],
                "notes": f"From proposal {proposal['number']}.",
            },
            proposal_id=proposal_id,
        )
        project_id = project["id"]
    if not invoice_id:
        credit = None
        deposit = kit.fetch_one(
            connection,
            "SELECT id, number, total_cents FROM st_invoices WHERE id = ? AND state != 'void'",
            (proposal["deposit_invoice_id"] or "",),
        )
        if deposit:
            credit = (
                int(deposit["total_cents"]),
                f"Deposit already invoiced ({deposit['number']})",
                str(deposit["id"]),
            )
        invoice = create_invoice(
            connection,
            {
                "client_id": proposal["client_id"],
                "project_id": project_id,
                "proposal_id": proposal_id,
                "items": [
                    {
                        "description": item["description"],
                        "quantity": item["quantity"],
                        "unit_cents": item["unit_cents"],
                    }
                    for item in proposal["items"]
                ],
                "discount_cents": proposal["discount_cents"],
                "tax_rate_bp": proposal["tax_rate_bp"],
                "notes": f"As agreed in proposal {proposal['number']}: {proposal['title']}.",
            },
            today=today,
            credit=credit,
        )
        invoice_id = invoice["id"]
    if proposal["deposit_invoice_id"]:
        connection.execute(
            "UPDATE st_invoices SET project_id = ? WHERE id = ? AND project_id = ''",
            (project_id, proposal["deposit_invoice_id"]),
        )
    kit.update_row(
        connection,
        "st_proposals",
        proposal_id,
        {"project_id": project_id, "invoice_id": invoice_id, "updated_at": kit.now()},
    )
    connection.commit()
    return {
        "proposal": get_proposal(connection, proposal_id, today),
        "project": get_project(connection, project_id),
        "invoice": get_invoice(connection, invoice_id, today),
    }


def draft_proposal(
    connection: sqlite3.Connection, brief: str, *, client_id: str = "", title: str = ""
) -> dict[str, Any]:
    """Words for a proposal from a short brief: the writing model, or a template that is useful on its own."""
    what = kit.text(brief, "a short brief", max_len=2000, required=True, multiline=True)
    client = (
        store.get(connection, "st_clients", client_id, "client") if client_id else {}
    )
    business = kit.business(connection)
    who = client.get("company") or client.get("name") or "your team"
    first = (client.get("name") or "").split(" ")[0] or "there"
    me = business["name"] or "we"
    heading = kit.text(title, "a title", max_len=200) or (
        what.split(".")[0][:80].strip() or "Proposal"
    )
    fallback = {
        "title": heading,
        "intro": (
            f"Hi {first},\n\nThank you for the conversation about {what.rstrip('.')}. This proposal sets out how "
            f"{me if me != 'we' else 'we'} will deliver it for {who}: what is included, how we will work together, and the "
            "investment. Accepting it online takes a minute, and we can start as soon as you do."
        ),
        "scope": (
            "1. Discovery: a kick-off call to confirm goals, audience, timeline and what success looks like.\n"
            f"2. Delivery: {what.rstrip('.')}, with a progress update every week.\n"
            "3. Review: two rounds of revisions on the agreed deliverables.\n"
            "4. Handover: final files, a short walkthrough and 14 days of support.\n\n"
            "Not included: anything not listed above, third-party costs (fonts, stock, hosting, ads spend) unless agreed in writing."
        ),
        "items": [
            {"description": "Discovery and planning", "quantity": "1", "unit_cents": 0},
            {
                "description": f"Delivery: {what.split('.')[0][:120]}",
                "quantity": "1",
                "unit_cents": 0,
            },
            {"description": "Revisions and handover", "quantity": "1", "unit_cents": 0},
        ],
    }
    prompt = (
        "You write short, warm, specific client proposals for a small agency or freelancer. Reply with JSON only, with "
        'keys "title" (string), "intro" (string, 3-5 sentences, addressed to the client by first name), "scope" '
        '(string: numbered phases, then what is not included) and "items" (list of {"description", "quantity", '
        '"unit_cents"}; unit_cents is 0 when you do not know the price). Do not invent prices or facts.\n'
        f"Business: {me}. Client: {client.get('name', '')} at {client.get('company', '')}. Brief: {what}"
    )
    drafted = kit.draft(prompt, fallback)
    try:
        items = clean_items(drafted.get("items"))
    except ValueError:
        items = clean_items(fallback["items"])
        drafted["source"] = "template"
    return {
        "title": kit.text(drafted["title"], "a title", max_len=200) or heading,
        "intro": kit.text(
            drafted["intro"], "the introduction", max_len=6000, multiline=True
        ),
        "scope": kit.text(drafted["scope"], "the scope", max_len=12000, multiline=True),
        "items": items,
        "source": drafted["source"],
    }


# ---------------------------------------------------------------------------
# projects, tasks and time
# ---------------------------------------------------------------------------


def _project_fields(
    raw: dict[str, Any], current: dict[str, Any] | None = None
) -> dict[str, Any]:
    merged = {**(current or {}), **raw}
    return {
        "name": kit.text(
            merged.get("name"), "the project name", max_len=200, required=True
        ),
        "status": kit.choice(
            merged.get("status"), "the status", PROJECT_STATUSES, default="planned"
        ),
        "budget_cents": cents_field(merged.get("budget_cents"), "the budget"),
        "deadline": kit.day(merged.get("deadline"), "the deadline"),
        "notes": kit.text(merged.get("notes"), "notes", max_len=4000, multiline=True),
    }


def create_project(
    connection: sqlite3.Connection, raw: dict[str, Any], *, proposal_id: str = ""
) -> dict[str, Any]:
    client_id = _require_client(connection, raw.get("client_id"))
    values = {
        "id": kit.new_id(),
        "client_id": client_id,
        "proposal_id": proposal_id,
        **_project_fields(raw),
        "created_at": kit.now(),
        "updated_at": kit.now(),
    }
    store.insert(connection, "st_projects", values)
    kit.log(
        connection,
        APP,
        "project",
        f"Project started: {values['name']}",
        ref_type="project",
        ref_id=values["id"],
    )
    connection.commit()
    return get_project(connection, values["id"])


def _time_totals(connection: sqlite3.Connection, project_id: str) -> dict[str, int]:
    row = (
        kit.fetch_one(
            connection,
            """SELECT COALESCE(SUM(minutes), 0) AS minutes,
        COALESCE(SUM(CASE WHEN billable = 1 THEN minutes ELSE 0 END), 0) AS billable_minutes,
        COALESCE(SUM(CASE WHEN billable = 1 AND invoice_id = '' THEN minutes ELSE 0 END), 0) AS unbilled_minutes
        FROM st_time WHERE project_id = ?""",
            (project_id,),
        )
        or {}
    )
    unbilled_cents = sum(
        _time_amount(entry["minutes"], entry["rate_cents"])
        for entry in kit.fetch_all(
            connection,
            "SELECT minutes, rate_cents FROM st_time WHERE project_id = ? AND billable = 1 AND invoice_id = ''",
            (project_id,),
        )
    )
    return {
        "minutes": int(row.get("minutes") or 0),
        "billable_minutes": int(row.get("billable_minutes") or 0),
        "unbilled_minutes": int(row.get("unbilled_minutes") or 0),
        "unbilled_cents": unbilled_cents,
    }


def _time_amount(minutes: int, rate_cents: int) -> int:
    return int(
        (Decimal(int(minutes)) * Decimal(int(rate_cents)) / Decimal(60)).quantize(
            Decimal("1"), rounding=ROUND_HALF_UP
        )
    )


def get_project(connection: sqlite3.Connection, project_id: str) -> dict[str, Any]:
    project = store.get(connection, "st_projects", project_id, "project")
    invoiced = kit.count(
        connection,
        "SELECT COALESCE(SUM(total_cents), 0) FROM st_invoices WHERE project_id = ? AND state != 'void'",
        (project_id,),
    )
    tasks = store.rows(
        connection,
        "SELECT * FROM st_tasks WHERE project_id = ? ORDER BY CASE status WHEN 'doing' THEN 0 WHEN 'todo' THEN 1 ELSE 2 END, due_date, created_at",
        (project_id,),
    )
    return {
        **project,
        **_client_name(connection, project["client_id"]),
        **_time_totals(connection, project_id),
        "invoiced_cents": invoiced,
        "tasks_open": sum(1 for task in tasks if task["status"] != "done"),
        "tasks_total": len(tasks),
    }


def project_detail(
    connection: sqlite3.Connection, project_id: str, today: str
) -> dict[str, Any]:
    return {
        "project": get_project(connection, project_id),
        "tasks": store.rows(
            connection,
            "SELECT * FROM st_tasks WHERE project_id = ? ORDER BY CASE status WHEN 'doing' THEN 0 WHEN 'todo' THEN 1 ELSE 2 END, due_date, created_at",
            (project_id,),
        ),
        "time": store.rows(
            connection,
            "SELECT * FROM st_time WHERE project_id = ? ORDER BY day DESC, created_at DESC",
            (project_id,),
        ),
        "invoices": [
            invoice_view(connection, row, today)
            for row in store.rows(
                connection,
                "SELECT * FROM st_invoices WHERE project_id = ? ORDER BY seq DESC",
                (project_id,),
            )
        ],
    }


def list_projects(
    connection: sqlite3.Connection, *, status: str = "", client_id: str = ""
) -> list[dict[str, Any]]:
    clauses, params = [], []
    if status:
        clauses.append("status = ?")
        params.append(kit.choice(status, "the status", PROJECT_STATUSES))
    if client_id:
        clauses.append("client_id = ?")
        params.append(client_id)
    where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    return [
        get_project(connection, row["id"])
        for row in kit.fetch_all(
            connection,
            f"SELECT id FROM st_projects {where} ORDER BY CASE status WHEN 'active' THEN 0 WHEN 'planned' THEN 1 WHEN 'on_hold' THEN 2 ELSE 3 END, deadline, created_at DESC LIMIT 1000",
            tuple(params),
        )  # noqa: S608
    ]


def update_project(
    connection: sqlite3.Connection, project_id: str, raw: dict[str, Any]
) -> dict[str, Any]:
    current = store.get(connection, "st_projects", project_id, "project")
    values = _project_fields(raw, current)
    if "client_id" in raw:
        values["client_id"] = _require_client(connection, raw["client_id"])
    values["updated_at"] = kit.now()
    kit.update_row(connection, "st_projects", project_id, values)
    if values["status"] != current["status"]:
        kit.log(
            connection,
            APP,
            "project",
            f"Project {values['name']} is now {values['status'].replace('_', ' ')}",
            ref_type="project",
            ref_id=project_id,
        )
    connection.commit()
    return get_project(connection, project_id)


def _delete_project_rows(connection: sqlite3.Connection, project_id: str) -> None:
    connection.execute("DELETE FROM st_tasks WHERE project_id = ?", (project_id,))
    connection.execute("DELETE FROM st_time WHERE project_id = ?", (project_id,))
    connection.execute(
        "UPDATE st_proposals SET project_id = '' WHERE project_id = ?", (project_id,)
    )
    connection.execute("DELETE FROM st_projects WHERE id = ?", (project_id,))


def delete_project(connection: sqlite3.Connection, project_id: str) -> None:
    project = store.get(connection, "st_projects", project_id, "project")
    if kit.count(
        connection,
        "SELECT COUNT(*) FROM st_invoices WHERE project_id = ? AND state != 'void'",
        (project_id,),
    ):
        raise ValueError("This project has invoices. Mark it done instead.")
    connection.execute(
        "UPDATE st_invoices SET project_id = '' WHERE project_id = ?", (project_id,)
    )
    connection.execute(
        "UPDATE st_deals SET project_id = '' WHERE project_id = ?", (project_id,)
    )
    _delete_project_rows(connection, project_id)
    kit.log(
        connection,
        APP,
        "project",
        f"Project deleted: {project['name']}",
        ref_type="project",
        ref_id=project_id,
    )
    connection.commit()


def add_task(
    connection: sqlite3.Connection, project_id: str, raw: dict[str, Any]
) -> dict[str, Any]:
    store.get(connection, "st_projects", project_id, "project")
    task = {
        "id": kit.new_id(),
        "project_id": project_id,
        "title": kit.text(raw.get("title"), "a task", max_len=300, required=True),
        "status": kit.choice(
            raw.get("status"), "the status", TASK_STATUSES, default="todo"
        ),
        "due_date": kit.day(raw.get("due_date"), "the due date"),
        "created_at": kit.now(),
        "updated_at": kit.now(),
    }
    store.insert(connection, "st_tasks", task)
    connection.commit()
    return store.get(connection, "st_tasks", task["id"], "task")


def update_task(
    connection: sqlite3.Connection, task_id: str, raw: dict[str, Any]
) -> dict[str, Any]:
    current = store.get(connection, "st_tasks", task_id, "task")
    merged = {**current, **raw}
    values = {
        "title": kit.text(merged.get("title"), "a task", max_len=300, required=True),
        "status": kit.choice(
            merged.get("status"), "the status", TASK_STATUSES, default="todo"
        ),
        "due_date": kit.day(merged.get("due_date"), "the due date"),
        "updated_at": kit.now(),
    }
    kit.update_row(connection, "st_tasks", task_id, values)
    if values["status"] == "done" and current["status"] != "done":
        kit.log(
            connection,
            APP,
            "task",
            f"Task done: {values['title']}",
            ref_type="project",
            ref_id=current["project_id"],
        )
    connection.commit()
    return store.get(connection, "st_tasks", task_id, "task")


def delete_task(connection: sqlite3.Connection, task_id: str) -> None:
    store.get(connection, "st_tasks", task_id, "task")
    connection.execute("DELETE FROM st_tasks WHERE id = ?", (task_id,))
    connection.commit()


def add_time(
    connection: sqlite3.Connection, project_id: str, raw: dict[str, Any], *, today: str
) -> dict[str, Any]:
    store.get(connection, "st_projects", project_id, "project")
    if raw.get("minutes") in (None, "") and raw.get("hours") not in (None, ""):
        try:
            typed = Decimal(str(raw["hours"]).strip().replace(",", "."))
            if not typed.is_finite():
                raise InvalidOperation
            minutes = int((typed * 60).quantize(Decimal("1"), rounding=ROUND_HALF_UP))
        except InvalidOperation:
            raise ValueError("Hours must be a number like 1.5.") from None
    else:
        minutes = kit.integer(
            raw.get("minutes"), "the minutes", minimum=1, maximum=24 * 60
        )
    if not 1 <= minutes <= 24 * 60:
        raise ValueError("A time entry is between 1 minute and 24 hours.")
    rate = raw.get("rate_cents")
    entry = {
        "id": kit.new_id(),
        "project_id": project_id,
        "day": kit.day(raw.get("day"), "the date") or today,
        "minutes": minutes,
        "note": kit.text(raw.get("note"), "a note", max_len=300),
        "billable": 1 if kit.flag(raw.get("billable", True)) else 0,
        "rate_cents": cents_field(
            rate,
            "the hourly rate",
            default=int(settings(connection)["default_rate_cents"]),
        ),
        "invoice_id": "",
        "created_at": kit.now(),
    }
    store.insert(connection, "st_time", entry)
    connection.commit()
    return store.get(connection, "st_time", entry["id"], "time entry")


def delete_time(connection: sqlite3.Connection, entry_id: str) -> None:
    entry = store.get(connection, "st_time", entry_id, "time entry")
    if entry["invoice_id"]:
        raise ValueError(
            "This time is on an invoice. Void or delete the invoice first."
        )
    connection.execute("DELETE FROM st_time WHERE id = ?", (entry_id,))
    connection.commit()


def invoice_unbilled_time(
    connection: sqlite3.Connection, project_id: str, *, today: str
) -> dict[str, Any]:
    """Billable time not yet invoiced becomes one invoice (a line per entry) and is marked as billed."""
    project = store.get(connection, "st_projects", project_id, "project")
    entries = store.rows(
        connection,
        "SELECT * FROM st_time WHERE project_id = ? AND billable = 1 AND invoice_id = '' ORDER BY day, created_at",
        (project_id,),
    )
    if not entries:
        raise ValueError("There is no unbilled billable time on this project.")
    items = []
    for entry in entries:
        hours = (
            (Decimal(entry["minutes"]) / Decimal(60))
            .quantize(Decimal("0.0001"), rounding=ROUND_HALF_UP)
            .normalize()
        )
        label = f"{entry['day']} {entry['note'] or project['name']}".strip()
        items.append(
            {
                "description": label[:300],
                "quantity": format(hours, "f"),
                "unit_cents": entry["rate_cents"],
            }
        )
    invoice = create_invoice(
        connection,
        {
            "client_id": project["client_id"],
            "project_id": project_id,
            "items": items,
            "notes": f"Time on {project['name']}.",
        },
        today=today,
    )
    connection.execute(
        f"UPDATE st_time SET invoice_id = ? WHERE id IN ({', '.join('?' for _ in entries)})",  # noqa: S608 - placeholders only
        (invoice["id"], *[entry["id"] for entry in entries]),
    )
    connection.commit()
    return get_invoice(connection, invoice["id"], today)


# ---------------------------------------------------------------------------
# retainers
# ---------------------------------------------------------------------------


def _retainer_fields(
    raw: dict[str, Any], current: dict[str, Any] | None = None
) -> dict[str, Any]:
    merged = {**(current or {}), **raw}
    if merged.get("amount_cents") in (None, ""):
        raise ValueError("Enter the monthly amount.")
    return {
        "title": kit.text(merged.get("title"), "a title", max_len=200, required=True),
        "amount_cents": cents_field(
            merged.get("amount_cents"), "the amount", minimum=1
        ),
        "active": 1 if kit.flag(merged.get("active", True)) else 0,
    }


def create_retainer(
    connection: sqlite3.Connection, raw: dict[str, Any], *, today: str
) -> dict[str, Any]:
    client_id = _require_client(connection, raw.get("client_id"))
    start = kit.day(raw.get("next_date"), "the first invoice date") or today
    values = {
        "id": kit.new_id(),
        "client_id": client_id,
        **_retainer_fields(raw),
        "interval": kit.choice(
            raw.get("interval"), "the interval", ("monthly",), default="monthly"
        ),
        "anchor_day": _date(start).day,
        "next_date": start,
        "last_invoice_id": "",
        "created_at": kit.now(),
        "updated_at": kit.now(),
    }
    store.insert(connection, "st_retainers", values)
    kit.log(
        connection,
        APP,
        "retainer",
        f"Retainer set up: {values['title']}",
        ref_type="retainer",
        ref_id=str(values["id"]),
        amount_cents=int(values["amount_cents"]),
    )
    connection.commit()
    return get_retainer(connection, values["id"])


def get_retainer(connection: sqlite3.Connection, retainer_id: str) -> dict[str, Any]:
    retainer = store.get(connection, "st_retainers", retainer_id, "retainer")
    return {**retainer, **_client_name(connection, retainer["client_id"])}


def list_retainers(connection: sqlite3.Connection) -> list[dict[str, Any]]:
    return [
        get_retainer(connection, row["id"])
        for row in kit.fetch_all(
            connection, "SELECT id FROM st_retainers ORDER BY active DESC, next_date"
        )
    ]


def update_retainer(
    connection: sqlite3.Connection,
    retainer_id: str,
    raw: dict[str, Any],
    *,
    today: str = "",
) -> dict[str, Any]:
    current = store.get(connection, "st_retainers", retainer_id, "retainer")
    values = _retainer_fields(raw, current)
    if "next_date" in raw:
        values["next_date"] = kit.day(
            raw["next_date"], "the next invoice date", required=True
        )
        values["anchor_day"] = _date(values["next_date"]).day
    elif today and values["active"] and not current["active"]:
        # Resuming skips the months it was paused: the next invoice is the first date from today on.
        next_date = current["next_date"]
        while next_date < today:
            next_date = add_month(next_date, int(current["anchor_day"]))
        values["next_date"] = next_date
    values["updated_at"] = kit.now()
    kit.update_row(connection, "st_retainers", retainer_id, values)
    connection.commit()
    return get_retainer(connection, retainer_id)


def delete_retainer(connection: sqlite3.Connection, retainer_id: str) -> None:
    store.get(connection, "st_retainers", retainer_id, "retainer")
    connection.execute("DELETE FROM st_retainers WHERE id = ?", (retainer_id,))
    connection.commit()


def run_retainers(
    connection: sqlite3.Connection, *, today: str
) -> list[dict[str, Any]]:
    """One invoice per retainer per month that has come due (catching up on missed months). Running it again
    makes nothing new: each (retainer, month) has at most one invoice."""
    created = []
    for retainer in store.rows(
        connection,
        "SELECT * FROM st_retainers WHERE active = 1 AND next_date <= ?",
        (today,),
    ):
        next_date = retainer["next_date"]
        guard = 0
        while next_date <= today and guard < 24:
            guard += 1
            period = next_date[:7]
            exists = kit.fetch_one(
                connection,
                "SELECT id FROM st_invoices WHERE retainer_id = ? AND period = ?",
                (retainer["id"], period),
            )
            if not exists:
                invoice = create_invoice(
                    connection,
                    {
                        "client_id": retainer["client_id"],
                        "issue_date": next_date,
                        "items": [
                            {
                                "description": f"{retainer['title']} ({period})",
                                "quantity": "1",
                                "unit_cents": retainer["amount_cents"],
                            }
                        ],
                        "notes": f"Monthly retainer for {period}.",
                    },
                    today=today,
                    retainer=(retainer["id"], period),
                )
                created.append(invoice)
                kit.update_row(
                    connection,
                    "st_retainers",
                    retainer["id"],
                    {"last_invoice_id": invoice["id"]},
                )
            next_date = add_month(next_date, int(retainer["anchor_day"]))
        kit.update_row(
            connection,
            "st_retainers",
            retainer["id"],
            {"next_date": next_date, "updated_at": kit.now()},
        )
    connection.commit()
    return created


# ---------------------------------------------------------------------------
# sending: the client page, the email in the outbox, reminders and follow-ups
# ---------------------------------------------------------------------------


def share_invoice(
    connection: sqlite3.Connection, invoice_id: str, *, today: str
) -> dict[str, Any]:
    """The invoice's client page (made once). Sharing it issues the invoice: a draft becomes sent."""
    from aira.suite import public

    invoice = get_invoice(connection, invoice_id, today)
    if invoice["status"] == "void":
        raise ValueError("A void invoice cannot be shared.")
    link = public.create_link(
        connection, APP, "invoice", invoice_id, title=f"Invoice {invoice['number']}"
    )
    mark_sent(connection, invoice_id)
    return link


def email_invoice(
    connection: sqlite3.Connection,
    invoice_id: str,
    *,
    today: str,
    reminder: bool,
    source: Any = None,
    to: str = "",
) -> dict[str, Any]:
    """Share the invoice and put an email (or a reminder) with its link in the outbox. ``message`` is None when
    the client has no email address."""
    if reminder:
        current = get_invoice(connection, invoice_id, today)
        if current["status"] not in ("sent", "partially_paid", "overdue"):
            raise ValueError("Only an unpaid invoice that was sent can get a reminder.")
    link = share_invoice(connection, invoice_id, today=today)
    invoice = get_invoice(connection, invoice_id, today)
    recipient = kit.email(to) or invoice["client_email"]
    message = None
    if recipient:
        subject, body = invoice_email(
            connection, invoice, kit.public_url(link["path"], source), reminder=reminder
        )
        message = kit.queue_email(
            connection,
            APP,
            "invoice_reminder" if reminder else "invoice",
            to=recipient,
            subject=subject,
            body=body,
            ref_type="invoice",
            ref_id=invoice_id,
        )
        if reminder:
            kit.update_row(
                connection,
                "st_invoices",
                invoice_id,
                {
                    "reminded_at": kit.now(),
                    "reminder_count": int(invoice.get("reminder_count") or 0) + 1,
                },
            )
        connection.commit()
    return {"link": link, "message": message}


def share_proposal(
    connection: sqlite3.Connection, proposal_id: str, *, today: str
) -> dict[str, Any]:
    """The proposal's client page (made once). Sharing it sends the proposal: a draft becomes sent."""
    from aira.suite import public

    proposal = get_proposal(connection, proposal_id, today)
    if proposal["status"] == "declined":
        raise ValueError("This proposal was declined. Make a new one to share.")
    link = public.create_link(
        connection,
        APP,
        "proposal",
        proposal_id,
        title=f"Proposal {proposal['number']}: {proposal['title']}",
    )
    mark_proposal_sent(connection, proposal_id)
    return link


def email_proposal(
    connection: sqlite3.Connection,
    proposal_id: str,
    *,
    today: str,
    source: Any = None,
    to: str = "",
    follow_up: bool = False,
) -> dict[str, Any]:
    link = share_proposal(connection, proposal_id, today=today)
    proposal = get_proposal(connection, proposal_id, today)
    recipient = kit.email(to) or proposal["client_email"]
    message = None
    if recipient:
        subject, body = proposal_email(
            connection,
            proposal,
            kit.public_url(link["path"], source),
            follow_up=follow_up,
        )
        message = kit.queue_email(
            connection,
            APP,
            "proposal_followup" if follow_up else "proposal",
            to=recipient,
            subject=subject,
            body=body,
            ref_type="proposal",
            ref_id=proposal_id,
        )
        if follow_up:
            kit.update_row(
                connection, "st_proposals", proposal_id, {"followed_up_at": kit.now()}
            )
        connection.commit()
    return {"link": link, "message": message}


def _percent(bp: int) -> str:
    return f"{int(bp) / 100:g}%"


def deposit_invoice(
    connection: sqlite3.Connection, proposal_id: str, *, today: str, source: Any = None
) -> dict[str, Any] | None:
    """When a proposal that asks for a deposit is accepted: an invoice for the deposit, sent to the client at once
    (its email waits in the outbox like every other). Made once; None when the proposal asks for no deposit."""
    proposal = store.get(connection, "st_proposals", proposal_id, "proposal")
    if proposal["status"] != "accepted":
        return None
    if proposal["deposit_invoice_id"] and kit.fetch_one(
        connection,
        "SELECT id FROM st_invoices WHERE id = ?",
        (proposal["deposit_invoice_id"],),
    ):
        return get_invoice(connection, proposal["deposit_invoice_id"], today)
    deposit = deposit_of(proposal)
    if deposit["net_cents"] <= 0:
        return None
    invoice = create_invoice(
        connection,
        {
            "client_id": proposal["client_id"],
            "proposal_id": proposal_id,
            "issue_date": today,
            "due_date": today,
            "items": [
                {
                    "description": f"Deposit ({_percent(deposit['deposit_bp'])}) for proposal "
                    f"{proposal['number']}: {proposal['title']}"[:300],
                    "quantity": "1",
                    "unit_cents": deposit["net_cents"],
                }
            ],
            "tax_rate_bp": proposal["tax_rate_bp"],
            "notes": f"The deposit to start the work agreed in proposal {proposal['number']}. "
            "It is taken off the final invoice.",
        },
        today=today,
        kind="deposit",
    )
    kit.update_row(
        connection,
        "st_proposals",
        proposal_id,
        {"deposit_invoice_id": invoice["id"], "updated_at": kit.now()},
    )
    connection.commit()
    email_invoice(connection, invoice["id"], today=today, reminder=False, source=source)
    return get_invoice(connection, invoice["id"], today)


def _after_accept(
    connection: sqlite3.Connection, proposal_id: str, *, today: str, source: Any = None
) -> None:
    deposit_invoice(connection, proposal_id, today=today, source=source)
    _win_linked_deals(connection, proposal_id)


def _days_since(stamp: str | None, today: str) -> int | None:
    if not stamp:
        return None
    return (_date(today) - _date(str(stamp)[:10])).days


def tick(connection: sqlite3.Connection, *, today: str) -> dict[str, int]:
    """What Studio does by itself every few minutes: draft (and send) retainer invoices on their date, remind
    clients of overdue invoices, and follow up proposals with no answer. Every email waits in the outbox for
    approval unless the business turned on automatic sending for its kind. Safe to run any number of times.
    ``no_email`` counts the late invoices and proposals that are due a reminder or follow-up but whose client has
    no email address: nothing is sent (or shared again) for them, and the business is told why."""
    config = settings(connection)
    done = {"retainer_invoices": 0, "reminders": 0, "followups": 0, "no_email": 0}
    if config["auto_retainers"]:
        for invoice in run_retainers(connection, today=today):
            done["retainer_invoices"] += 1
            if config["retainer_auto_send"]:
                email_invoice(connection, invoice["id"], today=today, reminder=False)
    if config["auto_reminders"]:
        for invoice in list_invoices(connection, today=today, status="overdue"):
            if int(invoice.get("days_overdue") or 0) < int(
                config["reminder_after_days"]
            ):
                continue
            if int(invoice["reminder_count"] or 0) >= int(config["reminder_max"]):
                continue
            since = _days_since(invoice["reminded_at"], today)
            if since is not None and since < int(config["reminder_every_days"]):
                continue
            if not invoice["client_email"]:
                done["no_email"] += 1
                continue
            sent = email_invoice(connection, invoice["id"], today=today, reminder=True)
            if sent["message"]:
                done["reminders"] += 1
    wait = int(config["proposal_followup_days"])
    if wait > 0:
        for proposal in list_proposals(connection, today=today, status="sent"):
            if proposal["followed_up_at"]:
                continue
            since = _days_since(proposal["sent_at"], today)
            if since is None or since < wait:
                continue
            if not proposal["client_email"]:
                done["no_email"] += 1
                continue
            sent = email_proposal(
                connection, proposal["id"], today=today, follow_up=True
            )
            if sent["message"]:
                done["followups"] += 1
    return done


# ---------------------------------------------------------------------------
# earnings report: what came in, who pays best, what is late, which work pays best
# ---------------------------------------------------------------------------

AGING = (
    ("current", "Not due yet", None, 0),
    ("1_30", "1 to 30 days late", 1, 30),
    ("31_60", "31 to 60 days late", 31, 60),
    ("61_90", "61 to 90 days late", 61, 90),
    ("90_plus", "More than 90 days late", 91, None),
)


def _month_keys(today: str, months: int) -> list[str]:
    year, month = int(today[:4]), int(today[5:7])
    keys = []
    for _ in range(max(1, min(int(months), 24))):
        keys.append(f"{year:04d}-{month:02d}")
        month -= 1
        if month == 0:
            year, month = year - 1, 12
    return list(reversed(keys))


def payments_received(
    connection: sqlite3.Connection, *, since: str = "", until: str = ""
) -> list[dict[str, Any]]:
    """Every payment on an invoice that is not void (newest first), with the invoice and the client."""
    clauses, params = ["i.state != 'void'"], []
    if since:
        clauses.append("p.day >= ?")
        params.append(since)
    if until:
        clauses.append("p.day <= ?")
        params.append(until)
    return kit.fetch_all(
        connection,
        f"""SELECT p.id, p.day, p.amount_cents, p.method, p.note, i.id AS invoice_id, i.number, i.project_id,
        i.client_id, c.name AS client_name, c.company AS client_company
        FROM st_payments p JOIN st_invoices i ON i.id = p.invoice_id LEFT JOIN st_clients c ON c.id = i.client_id
        WHERE {" AND ".join(clauses)} ORDER BY p.day DESC, p.created_at DESC LIMIT 20000""",  # noqa: S608
        tuple(params),
    )


def reports(
    connection: sqlite3.Connection, *, today: str, months: int = 12
) -> dict[str, Any]:
    keys = _month_keys(today, months)
    since = f"{keys[0]}-01"
    received = payments_received(connection, since=since, until=today)
    by_month: dict[str, dict[str, Any]] = {
        key: {"month": key, "received_cents": 0, "invoiced_cents": 0} for key in keys
    }
    by_method: dict[str, int] = {}
    by_client: dict[str, dict[str, Any]] = {}
    for payment in received:
        amount = int(payment["amount_cents"])
        if payment["day"][:7] in by_month:
            by_month[payment["day"][:7]]["received_cents"] += amount
        by_method[payment["method"]] = by_method.get(payment["method"], 0) + amount
        entry = by_client.setdefault(
            payment["client_id"],
            {
                "id": payment["client_id"],
                "name": payment["client_name"] or "",
                "company": payment["client_company"] or "",
                "received_cents": 0,
                "payments": 0,
            },
        )
        entry["received_cents"] += amount
        entry["payments"] += 1
    for row in kit.fetch_all(
        connection,
        """SELECT substr(issue_date, 1, 7) AS month, SUM(total_cents) AS total FROM st_invoices
        WHERE state = 'sent' AND issue_date >= ? GROUP BY substr(issue_date, 1, 7)""",
        (since,),
    ):
        if row["month"] in by_month:
            by_month[row["month"]]["invoiced_cents"] = int(row["total"] or 0)
    total_received = sum(int(p["amount_cents"]) for p in received)
    owed = client_money(connection)
    for client_id, figures in owed.items():
        if client_id in by_client:
            by_client[client_id]["outstanding_cents"] = max(
                0, figures["billed_cents"] - figures["paid_cents"]
            )
    clients = sorted(
        by_client.values(), key=lambda c: c["received_cents"], reverse=True
    )
    for client in clients:
        client.setdefault("outstanding_cents", 0)
        client["share"] = (
            round(client["received_cents"] * 100 / total_received)
            if total_received
            else 0
        )
    # what is still owed, by how late it is
    aging: dict[str, dict[str, Any]] = {
        key: {"key": key, "label": label, "cents": 0, "count": 0}
        for key, label, _, _ in AGING
    }
    invoices = list_invoices(connection, today=today)
    for invoice in invoices:
        if invoice["status"] not in ("sent", "partially_paid", "overdue"):
            continue
        late = int(invoice.get("days_overdue") or 0)
        for key, _label, low, high in AGING:
            if (low is None and late == 0) or (
                low is not None and late >= low and (high is None or late <= high)
            ):
                aging[key]["cents"] += int(invoice["balance_cents"])
                aging[key]["count"] += 1
                break
    # how long clients take to pay (invoices paid in full, by their last payment)
    waits = []
    for invoice in invoices:
        if invoice["status"] != "paid":
            continue
        last = kit.fetch_one(
            connection,
            "SELECT MAX(day) AS day FROM st_payments WHERE invoice_id = ?",
            (invoice["id"],),
        )
        if last and last.get("day"):
            waits.append(
                max(0, (_date(last["day"]) - _date(invoice["issue_date"])).days)
            )
    # which work pays best: invoiced per hour logged
    projects = []
    for row in kit.fetch_all(
        connection,
        """SELECT p.id, p.name, p.status, p.budget_cents, c.name AS client_name, c.company AS client_company,
        (SELECT COALESCE(SUM(total_cents), 0) FROM st_invoices WHERE project_id = p.id AND state != 'void') AS invoiced_cents,
        (SELECT COALESCE(SUM(minutes), 0) FROM st_time WHERE project_id = p.id) AS minutes
        FROM st_projects p LEFT JOIN st_clients c ON c.id = p.client_id""",
    ):
        invoiced, minutes = int(row["invoiced_cents"] or 0), int(row["minutes"] or 0)
        if not invoiced and not minutes:
            continue
        projects.append(
            {
                **row,
                "invoiced_cents": invoiced,
                "minutes": minutes,
                "hourly_cents": round(invoiced * 60 / minutes) if minutes else None,
            }
        )
    projects.sort(
        key=lambda p: (
            p["hourly_cents"] is None,
            -(p["hourly_cents"] or 0),
            -p["invoiced_cents"],
        )
    )
    this_year = sum(
        int(p["amount_cents"])
        for p in payments_received(connection, since=f"{today[:4]}-01-01", until=today)
    )
    months_with_income = [m for m in by_month.values() if m["received_cents"]]
    return {
        "currency": kit.business(connection)["currency"],
        "months": list(by_month.values()),
        "received_cents": total_received,
        "received_this_year_cents": this_year,
        "average_month_cents": round(total_received / len(keys)) if keys else 0,
        "best_month": max(months_with_income, key=lambda m: m["received_cents"])
        if months_with_income
        else None,
        "outstanding_cents": sum(bucket["cents"] for bucket in aging.values()),
        "aging": list(aging.values()),
        "average_days_to_pay": round(sum(waits) / len(waits)) if waits else None,
        "paid_invoices": len(waits),
        "methods": [
            {"method": method, "cents": cents}
            for method, cents in sorted(by_method.items(), key=lambda item: -item[1])
        ],
        "clients": clients,
        "projects": projects,
    }


# ---------------------------------------------------------------------------
# overview, checklist, summary
# ---------------------------------------------------------------------------


def overview(connection: sqlite3.Connection, *, today: str) -> dict[str, Any]:
    invoices = list_invoices(connection, today=today)
    unpaid = [
        i for i in invoices if i["status"] in ("sent", "partially_paid", "overdue")
    ]
    overdue = [i for i in unpaid if i["status"] == "overdue"]
    month = today[:7]
    paid_this_month = kit.count(
        connection,
        "SELECT COALESCE(SUM(p.amount_cents), 0) FROM st_payments p JOIN st_invoices i ON i.id = p.invoice_id WHERE i.state != 'void' AND substr(p.day, 1, 7) = ?",
        (month,),
    )
    proposals = list_proposals(connection, today=today)
    awaiting = [p for p in proposals if p["status"] == "sent"]
    to_convert = [
        p for p in proposals if p["status"] == "accepted" and not p["converted"]
    ]
    clients = list_clients(connection)
    top = sorted(
        (c for c in clients if c["paid_cents"]),
        key=lambda c: c["paid_cents"],
        reverse=True,
    )[:5]
    horizon = add_days(today, 14)
    deadlines = kit.fetch_all(
        connection,
        """SELECT p.id, p.name, p.deadline, c.name AS client_name FROM st_projects p LEFT JOIN st_clients c ON c.id = p.client_id
        WHERE p.status != 'done' AND p.deadline != '' AND p.deadline <= ? ORDER BY p.deadline LIMIT 10""",
        (horizon,),
    )
    tasks_due = kit.fetch_all(
        connection,
        """SELECT t.id, t.title, t.due_date, t.project_id, p.name AS project_name FROM st_tasks t JOIN st_projects p ON p.id = t.project_id
        WHERE t.status != 'done' AND t.due_date != '' AND t.due_date <= ? ORDER BY t.due_date LIMIT 10""",
        (horizon,),
    )
    pipeline = sum(int(p["total_cents"]) for p in awaiting)
    from aira.suite.studio import sales

    sales_part = sales.overview_part(connection, today=today)
    return {
        **sales_part,
        "currency": kit.business(connection)["currency"],
        "outstanding_cents": sum(i["balance_cents"] for i in unpaid),
        "overdue_cents": sum(i["balance_cents"] for i in overdue),
        "overdue_count": len(overdue),
        "paid_this_month_cents": paid_this_month,
        "pipeline_cents": pipeline,
        "awaiting_proposals": awaiting[:10],
        "accepted_to_convert": to_convert[:10],
        "overdue_invoices": overdue[:10],
        "top_clients": [
            {
                "id": c["id"],
                "name": c["name"],
                "company": c["company"],
                "paid_cents": c["paid_cents"],
            }
            for c in top
        ],
        "deadlines": [{**row, "late": row["deadline"] < today} for row in deadlines],
        "tasks_due": [{**row, "late": row["due_date"] < today} for row in tasks_due],
        "active_projects": kit.count(
            connection, "SELECT COUNT(*) FROM st_projects WHERE status = 'active'"
        ),
        "clients": len(clients),
    }


def checklist(connection: sqlite3.Connection) -> list[dict[str, Any]]:
    business = kit.business(connection)
    return [
        kit.step(
            "profile",
            "Add your business details",
            bool(business["name"]),
            "Name, contact and how clients pay you (Business hub).",
        ),
        kit.step(
            "client",
            "Add your first client",
            kit.count(connection, "SELECT COUNT(*) FROM st_clients") > 0,
            "Name and email are enough.",
        ),
        kit.step(
            "deal",
            "Add your first deal",
            kit.count(connection, "SELECT COUNT(*) FROM st_deals") > 0,
            "Track what you might win and when.",
        ),
        kit.step(
            "proposal",
            "Send a proposal",
            kit.count(
                connection, "SELECT COUNT(*) FROM st_proposals WHERE status != 'draft'"
            )
            > 0,
            "Draft one from a short brief.",
        ),
        kit.step(
            "invoice",
            "Send an invoice",
            kit.count(
                connection, "SELECT COUNT(*) FROM st_invoices WHERE state != 'draft'"
            )
            > 0,
            "Clients pay from the link.",
        ),
        kit.step(
            "payment",
            "Record your first payment",
            kit.count(connection, "SELECT COUNT(*) FROM st_payments") > 0,
            "Mark an invoice paid when the money arrives.",
        ),
    ]


def summary(connection: sqlite3.Connection, *, today: str) -> dict[str, Any]:
    data = overview(connection, today=today)
    code = data["currency"]
    return {
        "cards": [
            {
                "label": "Outstanding",
                "value": kit.fmt_money(data["outstanding_cents"], code),
            },
            {
                "label": "Overdue",
                "value": kit.fmt_money(data["overdue_cents"], code),
                "tone": "bad" if data["overdue_cents"] else "",
            },
            {
                "label": "Paid this month",
                "value": kit.fmt_money(data["paid_this_month_cents"], code),
                "tone": "good" if data["paid_this_month_cents"] else "",
            },
            {
                "label": "Pipeline value",
                "value": kit.fmt_money(data["sales"]["pipeline_cents"], code),
                "hint": f"{data['sales']['open_deals']} open deals",
            },
            {
                "label": "Won this month",
                "value": kit.fmt_money(data["sales"]["won_this_month_cents"], code),
                "tone": "good" if data["sales"]["won_this_month_cents"] else "",
            },
        ]
    }


# ---------------------------------------------------------------------------
# what customers see and the messages they get
# ---------------------------------------------------------------------------

PUBLIC_ITEM_KEYS = ("description", "quantity", "unit_cents", "amount_cents")


def _public_items(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [{key: item.get(key) for key in PUBLIC_ITEM_KEYS} for item in items]


def public_invoice(
    connection: sqlite3.Connection, invoice_id: str, today: str
) -> dict[str, Any]:
    view = invoice_view(
        connection, store.get(connection, "st_invoices", invoice_id, "invoice"), today
    )
    business = kit.business(connection)
    return {
        "number": view["number"],
        "issue_date": view["issue_date"],
        "due_date": view["due_date"],
        "status": view["status"],
        "bill_to": {"name": view["client_name"], "company": view["client_company"]},
        "items": _public_items(view["items"]),
        "subtotal_cents": view["subtotal_cents"],
        "discount_cents": view["discount_cents"],
        "tax_label": business["tax_label"],
        "tax_rate_bp": view["tax_rate_bp"],
        "tax_cents": view["tax_cents"],
        "credit_cents": view["credit_cents"],
        "credit_label": view["credit_label"],
        "deposit": view["kind"] == "deposit",
        "total_cents": view["total_cents"],
        "paid_cents": view["paid_cents"],
        "balance_cents": view["balance_cents"],
        "notes": view["notes"],
        "currency": business["currency"],
        "notified": bool(view["customer_notices"]),
    }


def public_proposal(
    connection: sqlite3.Connection, proposal_id: str, today: str
) -> dict[str, Any]:
    view = get_proposal(connection, proposal_id, today)
    business = kit.business(connection)
    return {
        "number": view["number"],
        "title": view["title"],
        "intro": view["intro"],
        "scope": view["scope"],
        "prepared_for": {
            "name": view["client_name"],
            "company": view["client_company"],
        },
        "items": _public_items(view["items"]),
        "subtotal_cents": view["subtotal_cents"],
        "discount_cents": view["discount_cents"],
        "tax_label": business["tax_label"],
        "tax_rate_bp": view["tax_rate_bp"],
        "tax_cents": view["tax_cents"],
        "total_cents": view["total_cents"],
        "valid_until": view["valid_until"],
        "status": view["status"],
        "accepted_name": view["accepted_name"] if view["status"] == "accepted" else "",
        "accepted_at": view["accepted_at"] if view["status"] == "accepted" else None,
        "currency": business["currency"],
        "deposit_bp": view["deposit"]["deposit_bp"],
        "deposit_cents": view["deposit"]["total_cents"],
        "deposit_invoice": _public_deposit(connection, view, today),
    }


def _public_deposit(
    connection: sqlite3.Connection, proposal: dict[str, Any], today: str
) -> dict[str, Any] | None:
    """After acceptance, the deposit invoice the client can pay now (through its own client page)."""
    from aira.suite import public

    if proposal["status"] != "accepted" or not proposal["deposit_invoice_id"]:
        return None
    row = kit.fetch_one(
        connection,
        "SELECT * FROM st_invoices WHERE id = ?",
        (proposal["deposit_invoice_id"],),
    )
    if not row:
        return None
    invoice = invoice_view(connection, store.decode(row), today)
    link = public.link_for(connection, APP, "invoice", invoice["id"])
    return {
        "number": invoice["number"],
        "status": invoice["status"],
        "total_cents": invoice["total_cents"],
        "balance_cents": invoice["balance_cents"],
        "path": link["path"] if link else "",
    }


def invoice_email(
    connection: sqlite3.Connection, invoice: dict[str, Any], url: str, *, reminder: bool
) -> tuple[str, str]:
    business = kit.business(connection)
    me = business["name"] or "us"
    code = business["currency"]
    first = (invoice["client_name"] or "").split(" ")[0] or "there"
    due = kit.fmt_money(invoice["balance_cents"], code)
    if reminder:
        late = invoice.get("days_overdue")
        subject = f"Reminder: invoice {invoice['number']} ({due}) " + (
            f"is {late} days overdue" if late else f"is due {invoice['due_date']}"
        )
        body = (
            f"Hi {first},\n\nA friendly reminder that invoice {invoice['number']} for {due} "
            + (
                f"was due on {invoice['due_date']}."
                if late
                else f"is due on {invoice['due_date']}."
            )
            + f"\n\nYou can view and pay it here:\n{url}\n\nIf you have already paid, thank you, and please ignore this message (or let us know from the page).\n\nBest regards,\n{me}"
        )
    else:
        subject = f"Invoice {invoice['number']} from {me} ({kit.fmt_money(invoice['total_cents'], code)})"
        body = (
            f"Hi {first},\n\nPlease find invoice {invoice['number']} for {kit.fmt_money(invoice['total_cents'], code)}, "
            f"due on {invoice['due_date']}.\n\nView and pay it here:\n{url}\n\nThank you for your business.\n\nBest regards,\n{me}"
        )
    return subject, body


def proposal_email(
    connection: sqlite3.Connection,
    proposal: dict[str, Any],
    url: str,
    *,
    follow_up: bool = False,
) -> tuple[str, str]:
    business = kit.business(connection)
    me = business["name"] or "us"
    first = (proposal["client_name"] or "").split(" ")[0] or "there"
    if follow_up:
        return (
            f"Following up: {proposal['title']}",
            f"Hi {first},\n\nI wanted to check whether you had a chance to look at our proposal for "
            f"{proposal['title']} ({kit.fmt_money(proposal['total_cents'], business['currency'])}). It is valid "
            f"until {proposal['valid_until']}.\n\nYou can read it and accept it online here:\n{url}\n\n"
            f"If anything should change, just reply and we will update it.\n\nBest regards,\n{me}",
        )
    subject = f"Proposal: {proposal['title']}"
    body = (
        f"Hi {first},\n\nHere is our proposal for {proposal['title']} "
        f"({kit.fmt_money(proposal['total_cents'], business['currency'])}), valid until {proposal['valid_until']}.\n\n"
        f"Read it and accept it online here:\n{url}\n\n"
        + (
            f"A deposit of {_percent(proposal['deposit']['deposit_bp'])} "
            f"({kit.fmt_money(proposal['deposit']['total_cents'], business['currency'])}) is due when you "
            "accept, and is taken off the final invoice.\n\n"
            if proposal.get("deposit", {}).get("total_cents")
            else ""
        )
        + f"Happy to answer any questions.\n\nBest regards,\n{me}"
    )
    return subject, body
