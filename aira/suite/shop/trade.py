"""Business buyers (B2B): trade accounts, the trade code they sign in with, credit and what they owe.

A business applies on the storefront (or the shop adds one) and the shop approves it with payment terms (prepay or
net 7 to 60 days), a percent off its trade prices, an optional credit limit and, for resellers, no tax. Approval
makes a trade code; the business signs in on the storefront with its email and that code to see trade prices and
order on its terms. The code is stored only as a hash: the shop can make a new one but never read an old one.

Consumers (B2C) buy as before; nothing here changes their checkout.
"""

from __future__ import annotations

import hashlib
import hmac
import secrets
import sqlite3
from collections.abc import Mapping
from datetime import date, timedelta
from typing import Any

from aira.suite import kit, public
from aira.suite.shop import store
from aira.suite.shop.store import APP, TRADE_TERMS, amount

CODE_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
CODE_GROUPS = 3  # ABCD-EFGH-JKLM: 60 bits, typed from an email
STOREFRONT_REF = "shop"
TERMS_LABELS = {
    "prepay": "Pay when ordering",
    "net7": "Net 7 (pay within 7 days)",
    "net15": "Net 15 (pay within 15 days)",
    "net30": "Net 30 (pay within 30 days)",
    "net60": "Net 60 (pay within 60 days)",
}


def terms_label(terms: str) -> str:
    return TERMS_LABELS.get(terms, terms)


def due_on(terms: str, today: str = "") -> str:
    start = date.fromisoformat(today or kit.today())
    return (start + timedelta(days=TRADE_TERMS.get(terms, 0))).isoformat()


# ---------------------------------------------------------------------------
# codes
# ---------------------------------------------------------------------------


def _new_code() -> str:
    groups = [
        "".join(secrets.choice(CODE_ALPHABET) for _ in range(4))
        for _ in range(CODE_GROUPS)
    ]
    return "-".join(groups)


def _normal(code: str) -> str:
    return "".join(ch for ch in str(code or "").upper() if ch.isalnum())


def _hash(code: str) -> str:
    return hashlib.sha256(_normal(code).encode()).hexdigest()


# ---------------------------------------------------------------------------
# accounts
# ---------------------------------------------------------------------------


def decode(row: Mapping[str, Any]) -> dict[str, Any]:
    account = dict(row)
    account.pop("code_hash", None)
    account["tax_exempt"] = bool(account.get("tax_exempt"))
    account["terms_label"] = terms_label(account.get("terms") or "prepay")
    return account


def get(connection: sqlite3.Connection, account_id: str) -> dict[str, Any]:
    return decode(
        kit.require_row(connection, "sh_trade_accounts", account_id, "trade account")
    )


def by_email(connection: sqlite3.Connection, email: str) -> dict[str, Any] | None:
    if not email:
        return None
    row = kit.fetch_one(
        connection,
        "SELECT * FROM sh_trade_accounts WHERE email = ?",
        (email.strip().lower(),),
    )
    return dict(row) if row else None


def values(raw: Mapping[str, Any], *, partial: bool = False) -> dict[str, Any]:
    """Validated account fields. ``partial`` keeps only the fields that were sent (an edit)."""
    out: dict[str, Any] = {}

    def has(key: str) -> bool:
        return not partial or key in raw

    if has("company"):
        out["company"] = kit.text(
            raw.get("company"), "the business name", max_len=120, required=True
        )
    if has("contact_name"):
        out["contact_name"] = kit.text(
            raw.get("contact_name"), "the contact name", max_len=120
        )
    if has("email"):
        address = kit.email(raw.get("email"), "the email")
        if not address:
            raise ValueError("Enter the business's email address.")
        out["email"] = address.lower()
    if has("phone"):
        out["phone"] = kit.phone(raw.get("phone"), "the phone number")
    if has("tax_id"):
        out["tax_id"] = kit.text(
            raw.get("tax_id"), "the tax or registration number", max_len=40
        )
    if has("address"):
        out["address"] = kit.text(
            raw.get("address"), "the address", max_len=500, multiline=True
        )
    if has("note"):
        out["note"] = kit.text(raw.get("note"), "a note", max_len=1000, multiline=True)
    return out


def terms_values(
    raw: Mapping[str, Any], *, partial: bool = False, default_terms: str = "prepay"
) -> dict[str, Any]:
    """What only the shop sets: terms, the percent off, the credit limit, tax and the status."""
    out: dict[str, Any] = {}

    def has(key: str) -> bool:
        return not partial or key in raw

    if has("terms"):
        out["terms"] = kit.choice(
            raw.get("terms"),
            "the payment terms",
            tuple(TRADE_TERMS),
            default=default_terms,
        )
    if has("discount_percent"):
        out["discount_percent"] = kit.integer(
            raw.get("discount_percent"),
            "the percent off",
            default=0,
            minimum=0,
            maximum=90,
        )
    if has("credit_limit_cents"):
        out["credit_limit_cents"] = amount(
            raw.get("credit_limit_cents"), "the credit limit"
        )
    if has("tax_exempt"):
        out["tax_exempt"] = 1 if kit.flag(raw.get("tax_exempt")) else 0
    if "status" in raw:
        out["status"] = kit.choice(
            raw.get("status"), "the status", store.TRADE_STATUSES
        )
    return out


def _insert(connection: sqlite3.Connection, row: dict[str, Any]) -> str:
    account_id = kit.new_id()
    stamp = kit.now()
    row = {**row, "id": account_id, "created_at": stamp, "updated_at": stamp}
    columns = ", ".join(row)
    marks = ", ".join(f":{key}" for key in row)
    connection.execute(
        f"INSERT INTO sh_trade_accounts ({columns}) VALUES ({marks})", row
    )  # noqa: S608 - keys are ours
    return account_id


def apply(
    connection: sqlite3.Connection, raw: Mapping[str, Any], *, origin: str = ""
) -> dict[str, Any]:
    """A business asks for a trade account on the storefront.

    The answer never says whether the email already has an account (that would tell strangers who buys here). With
    automatic approval a new business gets its code at once; otherwise the shop approves it and the code is emailed."""
    shop = store.settings(connection)
    fields = values(raw)
    if by_email(connection, fields["email"]) is not None:
        return {"status": "received"}
    automatic = shop["trade_approval"] == "auto"
    account_id = _insert(
        connection,
        {
            **fields,
            "status": "pending",
            "terms": shop["trade_default_terms"] if automatic else "prepay",
            "source": "storefront",
        },
    )
    kit.log(
        connection,
        APP,
        "trade",
        f"Trade account request: {fields['company']}",
        ref_type="trade_account",
        ref_id=account_id,
        public=True,
    )
    connection.commit()
    if automatic:
        approved = approve(connection, account_id, origin=origin, send=False)
        return {"status": "approved", "code": approved["code"]}
    return {"status": "received"}


def create(
    connection: sqlite3.Connection, raw: Mapping[str, Any], *, origin: str = ""
) -> dict[str, Any]:
    """The shop adds a business it already knows: approved at once, with the code to pass on."""
    shop = store.settings(connection)
    fields = values(raw)
    if by_email(connection, fields["email"]) is not None:
        raise ValueError("A trade account with that email already exists.")
    terms = terms_values(raw, default_terms=shop["trade_default_terms"])
    terms.pop("status", None)
    account_id = _insert(
        connection, {**fields, **terms, "status": "pending", "source": "manual"}
    )
    connection.commit()
    return approve(
        connection, account_id, origin=origin, send=kit.flag(raw.get("send"))
    )


def update(
    connection: sqlite3.Connection, account_id: str, raw: Mapping[str, Any]
) -> dict[str, Any]:
    current = get(connection, account_id)
    row = {**values(raw, partial=True), **terms_values(raw, partial=True)}
    if "email" in row and row["email"] != current["email"]:
        other = by_email(connection, row["email"])
        if other is not None and other["id"] != account_id:
            raise ValueError("Another trade account has that email.")
    if row.get("status") == "approved" and current["status"] != "approved":
        raise ValueError("Use Approve, which makes the trade code.")
    if row:
        row["updated_at"] = kit.now()
        kit.update_row(connection, "sh_trade_accounts", account_id, row)
        connection.commit()
    return get(connection, account_id)


def approve(
    connection: sqlite3.Connection,
    account_id: str,
    *,
    origin: str = "",
    send: bool = True,
) -> dict[str, Any]:
    """Approve (or re-approve) an account with a fresh trade code. The code is returned once, here."""
    account = get(connection, account_id)
    code = _new_code()
    stamp = kit.now()
    connection.execute(
        "UPDATE sh_trade_accounts SET status = 'approved', code_hash = ?, approved_at = COALESCE(approved_at, ?), updated_at = ? WHERE id = ?",
        (_hash(code), stamp, stamp, account_id),
    )
    kit.log(
        connection,
        APP,
        "trade",
        f"Trade account approved: {account['company']}",
        ref_type="trade_account",
        ref_id=account_id,
    )
    connection.commit()
    approved = get(connection, account_id)
    if send:
        message(connection, approved, "trade_approved", code=code, origin=origin)
        connection.commit()
    return {"account": approved, "code": code}


def new_code(
    connection: sqlite3.Connection, account_id: str, *, origin: str = ""
) -> dict[str, Any]:
    """A business lost its code: a new one replaces it (the old one stops working)."""
    account = get(connection, account_id)
    if account["status"] != "approved":
        raise ValueError("Approve the account first.")
    code = _new_code()
    connection.execute(
        "UPDATE sh_trade_accounts SET code_hash = ?, updated_at = ? WHERE id = ?",
        (_hash(code), kit.now(), account_id),
    )
    connection.commit()
    message(connection, account, "trade_approved", code=code, origin=origin)
    connection.commit()
    return {"account": get(connection, account_id), "code": code}


def decline(connection: sqlite3.Connection, account_id: str) -> dict[str, Any]:
    get(connection, account_id)
    connection.execute(
        "UPDATE sh_trade_accounts SET status = 'declined', code_hash = '', updated_at = ? WHERE id = ?",
        (kit.now(), account_id),
    )
    connection.commit()
    return get(connection, account_id)


def delete(connection: sqlite3.Connection, account_id: str) -> dict[str, Any]:
    """Delete an account that never ordered; one with orders is paused instead (its orders keep the business)."""
    get(connection, account_id)
    ordered = kit.count(
        connection,
        "SELECT COUNT(*) FROM sh_orders WHERE trade_account_id = ?",
        (account_id,),
    )
    if ordered:
        connection.execute(
            "UPDATE sh_trade_accounts SET status = 'paused', code_hash = '', updated_at = ? WHERE id = ?",
            (kit.now(), account_id),
        )
        connection.commit()
        return {"deleted": False, "paused": True}
    connection.execute("DELETE FROM sh_trade_accounts WHERE id = ?", (account_id,))
    connection.commit()
    return {"deleted": True, "paused": False}


def verify(
    connection: sqlite3.Connection, email: Any, code: Any
) -> dict[str, Any] | None:
    """The approved account this email and trade code open, or None (the same answer for every mismatch)."""
    address = str(email or "").strip().lower()[:254]
    if not address or not _normal(code):
        return None
    row = by_email(connection, address)
    expected = (row or {}).get("code_hash") or "0" * 64
    matches = hmac.compare_digest(expected, _hash(str(code)))
    if row is None or not matches or row["status"] != "approved":
        return None
    return decode(row)


def sign_in(connection: sqlite3.Connection, email: Any, code: Any) -> dict[str, Any]:
    account = verify(connection, email, code)
    if account is None:
        raise PermissionError(
            "That email and trade code do not match an approved trade account."
        )
    return account


# ---------------------------------------------------------------------------
# credit and what businesses owe
# ---------------------------------------------------------------------------


def open_balance(connection: sqlite3.Connection, account_id: str) -> int:
    """What the business has ordered and not paid yet (cancelled orders do not count)."""
    row = connection.execute(
        "SELECT COALESCE(SUM(total_cents), 0) FROM sh_orders WHERE trade_account_id = ? AND payment_status = 'unpaid' AND fulfilment_status != 'cancelled'",
        (account_id,),
    ).fetchone()
    return int(row[0] or 0)


def credit(
    connection: sqlite3.Connection, account: Mapping[str, Any]
) -> dict[str, Any]:
    limit = int(account.get("credit_limit_cents") or 0)
    owed = open_balance(connection, str(account["id"]))
    return {
        "limit_cents": limit,
        "open_cents": owed,
        "available_cents": max(0, limit - owed) if limit else None,  # None = no limit
    }


def check_credit(
    connection: sqlite3.Connection, account: Mapping[str, Any], total_cents: int
) -> None:
    """Refuse an order on terms that would take the business over its credit limit (0 = no limit)."""
    if account.get("terms", "prepay") == "prepay":
        return
    limit = int(account.get("credit_limit_cents") or 0)
    if not limit:
        return
    owed = open_balance(connection, str(account["id"]))
    if owed + int(total_cents) > limit:
        code = kit.business(connection).get("currency") or "USD"
        raise ValueError(
            f"This order would take {account['company']} over its credit limit of {kit.fmt_money(limit, code)} "
            f"({kit.fmt_money(owed, code)} is still unpaid). Pay an open invoice first, or ask the shop to raise the limit."
        )


def receivables(connection: sqlite3.Connection, *, today: str = "") -> dict[str, Any]:
    """Unpaid business orders, oldest due first, with how late each is and the totals per business."""
    day = today or kit.today()
    rows = kit.fetch_all(
        connection,
        "SELECT * FROM sh_orders WHERE buyer_type = 'business' AND payment_status = 'unpaid' AND fulfilment_status != 'cancelled' ORDER BY due_on, number",
    )
    orders = []
    by_account: dict[str, dict[str, Any]] = {}
    for row in rows:
        order = store.decode_order(row)
        due = order.get("due_on") or day
        late = (date.fromisoformat(day) - date.fromisoformat(due)).days
        entry = {
            "id": order["id"],
            "number": order["number"],
            "company": order["company"],
            "trade_account_id": order["trade_account_id"],
            "po_number": order["po_number"],
            "terms": order["terms"],
            "terms_label": terms_label(order["terms"]),
            "due_on": due,
            "days_late": max(0, late),
            "overdue": late > 0,
            "total_cents": int(order["total_cents"]),
            "currency": order["currency"],
            "created_at": order["created_at"],
        }
        orders.append(entry)
        totals = by_account.setdefault(
            order["trade_account_id"] or order["company"],
            {
                "trade_account_id": order["trade_account_id"],
                "company": order["company"],
                "open_cents": 0,
                "overdue_cents": 0,
                "orders": 0,
            },
        )
        totals["open_cents"] += entry["total_cents"]
        totals["orders"] += 1
        if entry["overdue"]:
            totals["overdue_cents"] += entry["total_cents"]
    return {
        "orders": orders,
        "accounts": sorted(
            by_account.values(), key=lambda a: (-a["overdue_cents"], -a["open_cents"])
        ),
        "open_cents": sum(o["total_cents"] for o in orders),
        "overdue_cents": sum(o["total_cents"] for o in orders if o["overdue"]),
        "overdue_orders": sum(1 for o in orders if o["overdue"]),
    }


def list_accounts(
    connection: sqlite3.Connection, *, status: str = "", q: str = ""
) -> list[dict[str, Any]]:
    """Every trade account with its orders, what it paid and what it owes."""
    clauses, params = [], []
    if status:
        clauses.append("status = ?")
        params.append(kit.choice(status, "the status", store.TRADE_STATUSES))
    if q:
        like = f"%{q}%"
        clauses.append(
            "(company LIKE ? OR contact_name LIKE ? OR email LIKE ? OR tax_id LIKE ?)"
        )
        params.extend([like, like, like, like])
    where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    rows = kit.fetch_all(
        connection,
        f"""SELECT * FROM sh_trade_accounts {where}
        ORDER BY CASE status WHEN 'pending' THEN 0 WHEN 'approved' THEN 1 ELSE 2 END, company COLLATE NOCASE""",  # noqa: S608
        tuple(params),
    )
    stats = {
        row["trade_account_id"]: row
        for row in kit.fetch_all(
            connection,
            """SELECT trade_account_id, COUNT(*) AS orders,
            COALESCE(SUM(CASE WHEN payment_status = 'paid' THEN total_cents ELSE 0 END), 0) AS paid_cents,
            COALESCE(SUM(CASE WHEN payment_status = 'unpaid' AND fulfilment_status != 'cancelled' THEN total_cents ELSE 0 END), 0) AS open_cents,
            MAX(created_at) AS last_order_at
            FROM sh_orders WHERE trade_account_id != '' GROUP BY trade_account_id""",
        )
    }
    out = []
    for row in rows:
        account = decode(row)
        found = stats.get(account["id"]) or {}
        account.update(
            {
                "orders": int(found.get("orders") or 0),
                "paid_cents": int(found.get("paid_cents") or 0),
                "open_cents": int(found.get("open_cents") or 0),
                "last_order_at": found.get("last_order_at"),
            }
        )
        out.append(account)
    return out


def public_account(
    connection: sqlite3.Connection, account: Mapping[str, Any]
) -> dict[str, Any]:
    """What the signed-in business sees about its own account on the storefront."""
    return {
        "company": account["company"],
        "contact_name": account["contact_name"],
        "email": account["email"],
        "phone": account["phone"],
        "address": account["address"],
        "tax_id": account["tax_id"],
        "terms": account["terms"],
        "terms_label": terms_label(account["terms"]),
        "discount_percent": int(account["discount_percent"] or 0),
        "tax_exempt": bool(account.get("tax_exempt")),
        "credit": credit(connection, account),
    }


# ---------------------------------------------------------------------------
# messages
# ---------------------------------------------------------------------------


def message(
    connection: sqlite3.Connection,
    account: Mapping[str, Any],
    kind: str,
    *,
    code: str = "",
    origin: str = "",
) -> dict[str, Any] | None:
    profile = kit.business(connection)
    name = profile.get("name") or "our shop"
    first = str(account.get("contact_name") or account["company"]).split(" ")[0]
    link = public.link_for(connection, APP, "storefront", STOREFRONT_REF)
    url = kit.public_url(link["path"], {"base_url": origin}) if link else ""
    if kind != "trade_approved":
        raise ValueError(f"Unknown message {kind}.")
    subject = f"Your trade account with {name}"
    parts = [
        f"Hi {first},",
        f"{account['company']} can now buy from {name} at trade prices.",
        f"Sign in on the shop with your email ({account['email']}) and this trade code:\n{code}",
        f"Payment terms: {terms_label(account['terms'])}."
        + (
            f" Your account has {account['discount_percent']}% off trade prices."
            if account.get("discount_percent")
            else ""
        ),
        f"Shop: {url}" if url else "",
        "Keep the code private: anyone with it can order for your business.",
        f"Thanks,\n{name}",
    ]
    return kit.queue_email(
        connection,
        APP,
        kind,
        to=str(account["email"]),
        subject=subject,
        body="\n\n".join(part for part in parts if part),
        ref_type="trade_account",
        ref_id=str(account["id"]),
    )
