"""Payment requests, the provider's events that change them, and the registry of what the apps let customers pay.

A **payment request** is one amount asked of one customer for one thing an app keeps (a Shop order, a booking's
deposit, a course enrolment): the provider's payment link (``provider_ref`` ``plink_...`` and its ``short_url``),
its status and, once paid, the provider's payment id. Statuses:

* ``created`` - the link is open; ``expired`` / ``cancelled`` - it closed unpaid;
* ``paid`` - the amount and currency that arrived are the ones asked, and the app was told (its ``mark_paid``);
* ``refunded`` / ``partially_refunded`` - money went back (each refund is a negative ledger line in its month);
* ``mismatch`` - something needs the business: a different amount or currency arrived, or the app could not
  record the payment (an order cancelled meanwhile). The hub shows these with the reason.

**Events** (Razorpay webhooks) are kept by their event id (``X-Razorpay-Event-Id``), so a delivery that comes again
changes nothing. The webhook is the source of truth: the customer's return to the public page only shows "Paid" at
once (after checking the return's signature and asking Razorpay).

**The registry**: each app says what can be paid (:func:`payable`): ``describe(connection, object_id)`` (the amount
due now, the currency, the customer, where the customer comes back to) and ``on_paid(connection, object_id, paid)``
(its own ``mark_paid``). :func:`public_page` lets a public page's "Pay online" button make (or reuse) the link.
"""

from __future__ import annotations

import hashlib
import json
import re
import sqlite3
from collections.abc import Callable, Iterator, Mapping
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from datetime import timedelta
from typing import Any

from aira.payments import razorpay
from aira.suite import kit, public

APP = "payments"
PROVIDER = "razorpay"
STATUSES = (
    "created",
    "paid",
    "expired",
    "cancelled",
    "refunded",
    "partially_refunded",
    "mismatch",
)
PAID_STATUSES = ("paid", "partially_refunded", "refunded")
MAX_WEBHOOK_BYTES = 256_000
# Razorpay closes a link no sooner than 15 minutes after it is made.
MIN_LINK_MINUTES = 16
WEBHOOK_KIND = "razorpay_webhook"
WEBHOOK_REF = "razorpay"
WEBHOOK_EVENTS = (
    "payment_link.paid",
    "payment_link.expired",
    "payment_link.cancelled",
    "payment.failed",
    "refund.processed",
    "refund.failed",
)

SCHEMA = (
    """CREATE TABLE IF NOT EXISTS payment_requests (
        id TEXT PRIMARY KEY,
        app TEXT NOT NULL,
        object_type TEXT NOT NULL,
        object_id TEXT NOT NULL,
        reference TEXT NOT NULL UNIQUE,
        description TEXT NOT NULL DEFAULT '',
        amount_minor INTEGER NOT NULL,
        currency TEXT NOT NULL,
        provider TEXT NOT NULL,
        provider_ref TEXT NOT NULL DEFAULT '',
        short_url TEXT NOT NULL DEFAULT '',
        mode TEXT NOT NULL DEFAULT '',
        status TEXT NOT NULL DEFAULT 'created',
        customer_name TEXT NOT NULL DEFAULT '',
        customer_email TEXT NOT NULL DEFAULT '',
        customer_phone TEXT NOT NULL DEFAULT '',
        payment_id TEXT NOT NULL DEFAULT '',
        paid_minor INTEGER NOT NULL DEFAULT 0,
        paid_currency TEXT NOT NULL DEFAULT '',
        method TEXT NOT NULL DEFAULT '',
        refunded_minor INTEGER NOT NULL DEFAULT 0,
        revenue_type TEXT NOT NULL DEFAULT '',
        revenue_id TEXT NOT NULL DEFAULT '',
        problem TEXT NOT NULL DEFAULT '',
        return_path TEXT NOT NULL DEFAULT '',
        created_by TEXT NOT NULL DEFAULT '',
        expires_at TEXT,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL,
        paid_at TEXT,
        closed_at TEXT
    )""",
    "CREATE INDEX IF NOT EXISTS payment_requests_object ON payment_requests (app, object_type, object_id, status)",
    "CREATE INDEX IF NOT EXISTS payment_requests_provider ON payment_requests (provider, provider_ref)",
    "CREATE INDEX IF NOT EXISTS payment_requests_payment ON payment_requests (payment_id)",
    "CREATE INDEX IF NOT EXISTS payment_requests_status ON payment_requests (status, created_at)",
    """CREATE TABLE IF NOT EXISTS payment_events (
        event_id TEXT PRIMARY KEY,
        provider TEXT NOT NULL,
        event TEXT NOT NULL,
        request_id TEXT NOT NULL DEFAULT '',
        outcome TEXT NOT NULL DEFAULT '',
        received_at TEXT NOT NULL
    )""",
    """CREATE TABLE IF NOT EXISTS payment_refunds (
        id TEXT PRIMARY KEY,
        request_id TEXT NOT NULL,
        provider_ref TEXT NOT NULL DEFAULT '',
        amount_minor INTEGER NOT NULL,
        currency TEXT NOT NULL,
        status TEXT NOT NULL DEFAULT 'pending',
        note TEXT NOT NULL DEFAULT '',
        problem TEXT NOT NULL DEFAULT '',
        created_by TEXT NOT NULL DEFAULT '',
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL,
        applied_at TEXT
    )""",
    "CREATE INDEX IF NOT EXISTS payment_refunds_request ON payment_refunds (request_id)",
    "CREATE INDEX IF NOT EXISTS payment_refunds_provider ON payment_refunds (provider_ref)",
)


def ensure(connection: sqlite3.Connection) -> None:
    kit.ensure_core(connection)
    kit.ensure(connection, "payments.v1", SCHEMA)


# ---------------------------------------------------------------------------
# the registry
# ---------------------------------------------------------------------------

DescribeFn = Callable[[sqlite3.Connection, str], dict[str, Any]]
PaidFn = Callable[[sqlite3.Connection, str, dict[str, Any]], Any]
RefundFn = Callable[[sqlite3.Connection, str, dict[str, Any]], Any]
ResolveFn = Callable[[sqlite3.Connection, dict[str, Any], dict[str, Any]], "str | None"]


@dataclass
class Payable:
    """Something an app lets customers pay. ``describe`` answers::

        {"payable": bool, "reason": "why not", "amount_cents": int, "currency": "INR",
         "description": "Order #1001", "customer": {"name", "email", "phone"},
         "return_path": "/p/<token>", "expires_at": "<iso or ''>"}

    ``on_paid`` records the payment in the app (its own ``mark_paid``) and may return
    ``{"source_type", "source_id"}``: the ledger line it wrote, which a refund offsets (default: the object).
    ``on_refund`` is told ``{"amount_cents", "full", "note"}`` after the money went back (Shop restocks)."""

    app: str
    object_type: str
    label: str
    describe: DescribeFn
    on_paid: PaidFn
    on_refund: RefundFn | None = None


REGISTRY: dict[tuple[str, str], Payable] = {}


def payable(
    app: str,
    object_type: str,
    *,
    label: str,
    describe: DescribeFn,
    on_paid: PaidFn,
    on_refund: RefundFn | None = None,
) -> Payable:
    spec = Payable(app, object_type, label, describe, on_paid, on_refund)
    REGISTRY[(app, object_type)] = spec
    return spec


def spec_for(app: str, object_type: str) -> Payable:
    found = REGISTRY.get((app, object_type))
    if found is None:
        raise LookupError(f"{app} has nothing of kind {object_type} to pay.")
    return found


# ---------------------------------------------------------------------------
# secrets and the provider client
# ---------------------------------------------------------------------------

_SECRETS: ContextVar[Any] = ContextVar("aira_payments_secret_store", default=None)
# (key_id, key_secret) -> client; tests point it at the local stub through AIRA_RAZORPAY_API_BASE instead
client_factory: Callable[[str, str], razorpay.Client] = razorpay.Client


@contextmanager
def secrets(store: Any) -> Iterator[None]:
    """Within this block, payments read the key secret from ``store`` (a public page's request has no other way
    to hand it to the apps' actions)."""
    token = _SECRETS.set(store)
    try:
        yield
    finally:
        _SECRETS.reset(token)


def _store(secret_store: Any = None) -> Any:
    return secret_store if secret_store is not None else _SECRETS.get()


def key_secret(connection: sqlite3.Connection, secret_store: Any = None) -> str:
    store = _store(secret_store)
    return str(store.get(connection, kit.RAZORPAY_KEY_SECRET) or "") if store else ""


def webhook_secret(connection: sqlite3.Connection, secret_store: Any = None) -> str:
    store = _store(secret_store)
    return (
        str(store.get(connection, kit.RAZORPAY_WEBHOOK_SECRET) or "") if store else ""
    )


def client(connection: sqlite3.Connection, secret_store: Any = None) -> razorpay.Client:
    """The business's Razorpay client, or ValueError saying what to set up first."""
    settings = kit.payment_settings(connection)
    if settings["provider"] != PROVIDER:
        raise ValueError(
            "Turn on Razorpay first (Business hub > Business profile > Payments)."
        )
    if not kit.payment_mode(settings):
        raise ValueError(
            "Enter your Razorpay key id first (Business hub > Business profile > Payments)."
        )
    secret = key_secret(connection, secret_store)
    if not secret:
        raise ValueError(
            "Save your Razorpay key secret first (Business hub > Business profile > Payments)."
        )
    return client_factory(settings["razorpay_key_id"], secret)


# ---------------------------------------------------------------------------
# requests
# ---------------------------------------------------------------------------


def get(connection: sqlite3.Connection, request_id: str) -> dict[str, Any]:
    ensure(connection)
    return kit.require_row(connection, "payment_requests", request_id, "payment")


def _open_for(
    connection: sqlite3.Connection, app: str, object_type: str, object_id: str
) -> dict[str, Any] | None:
    return kit.fetch_one(
        connection,
        "SELECT * FROM payment_requests WHERE app = ? AND object_type = ? AND object_id = ? AND status = 'created' ORDER BY created_at DESC",
        (app, object_type, object_id),
    )


def view(connection: sqlite3.Connection, row: Mapping[str, Any]) -> dict[str, Any]:
    """A request as the business sees it (amounts in the apps' cents, its refunds)."""
    refunds = kit.fetch_all(
        connection,
        "SELECT * FROM payment_refunds WHERE request_id = ? ORDER BY created_at",
        (row["id"],),
    )
    currency = str(row["currency"])
    return {
        "id": row["id"],
        "app": row["app"],
        "object_type": row["object_type"],
        "object_id": row["object_id"],
        "reference": row["reference"],
        "description": row["description"],
        "amount_cents": int(row["amount_minor"]),
        "currency": currency,
        "provider": row["provider"],
        "provider_ref": row["provider_ref"],
        "short_url": row["short_url"],
        "mode": row["mode"],
        "status": row["status"],
        "customer_name": row["customer_name"],
        "customer_email": row["customer_email"],
        "payment_id": row["payment_id"],
        "paid_cents": razorpay.from_minor_units(
            int(row["paid_minor"] or 0), row["paid_currency"] or currency
        ),
        "paid_currency": row["paid_currency"],
        "method": row["method"],
        "refunded_cents": int(row["refunded_minor"] or 0),
        "refundable_cents": refundable(connection, row),
        "problem": row["problem"],
        "expires_at": row["expires_at"],
        "created_at": row["created_at"],
        "paid_at": row["paid_at"],
        "closed_at": row["closed_at"],
        "refunds": [
            {
                "id": refund["id"],
                "provider_ref": refund["provider_ref"],
                "amount_cents": int(refund["amount_minor"]),
                "currency": refund["currency"],
                "status": refund["status"],
                "note": refund["note"],
                "problem": refund["problem"],
                "created_at": refund["created_at"],
            }
            for refund in refunds
        ],
    }


def requests_for(
    connection: sqlite3.Connection, app: str, object_type: str, object_id: str
) -> list[dict[str, Any]]:
    ensure(connection)
    return [
        view(connection, row)
        for row in kit.fetch_all(
            connection,
            "SELECT * FROM payment_requests WHERE app = ? AND object_type = ? AND object_id = ? ORDER BY created_at DESC LIMIT 20",
            (app, object_type, object_id),
        )
    ]


def list_requests(
    connection: sqlite3.Connection, *, status: str = "", app: str = "", limit: int = 100
) -> list[dict[str, Any]]:
    ensure(connection)
    clauses, params = [], []
    if status == "attention":
        clauses.append("(status = 'mismatch' OR problem != '')")
    elif status:
        clauses.append("status = ?")
        params.append(kit.choice(status, "the status", STATUSES))
    if app:
        clauses.append("app = ?")
        params.append(app)
    where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    rows = kit.fetch_all(
        connection,
        f"SELECT * FROM payment_requests {where} ORDER BY created_at DESC LIMIT ?",  # noqa: S608 - fixed clauses
        (*params, max(1, min(int(limit), 500))),
    )
    return [view(connection, row) for row in rows]


def counts(connection: sqlite3.Connection) -> dict[str, int]:
    ensure(connection)
    out = {status: 0 for status in STATUSES}
    for row in connection.execute(
        "SELECT status, COUNT(*) FROM payment_requests GROUP BY status"
    ):
        out[row[0]] = int(row[1])
    out["attention"] = kit.count(
        connection,
        "SELECT COUNT(*) FROM payment_requests WHERE status = 'mismatch' OR problem != ''",
    )
    return out


def create_request(
    connection: sqlite3.Connection,
    app: str,
    object_type: str,
    object_id: str,
    *,
    secret_store: Any = None,
    actor: str = "",
    public_visit: bool = False,
    prefill: bool = True,
) -> dict[str, Any]:
    """The open payment link for this thing, made with the provider on first use. An open link for the same amount
    is reused (a customer pressing "Pay" twice gets the same page); one for another amount is cancelled first.
    ``prefill`` False leaves the customer's name, email and phone off the payment page (a page that names the thing
    by a short reference: whoever types one must not see whose it is)."""
    ensure(connection)
    spec = spec_for(app, object_type)
    info = spec.describe(connection, object_id)
    if not info.get("payable"):
        raise ValueError(str(info.get("reason") or "There is nothing to pay here."))
    amount = int(info["amount_cents"])
    currency = kit.currency(info.get("currency"))
    if amount <= 0:
        raise ValueError("There is nothing to pay here.")
    now = kit.utcnow()
    settings = kit.payment_settings(connection)
    expires = (
        kit.parse_time(info["expires_at"])
        if info.get("expires_at")
        else now + timedelta(hours=int(settings["link_expiry_hours"]))
    )
    floor = now + timedelta(minutes=MIN_LINK_MINUTES)
    if expires < floor:
        expires = floor
    existing = _open_for(connection, app, object_type, object_id)
    if existing is not None:
        still_open = not existing["expires_at"] or kit.parse_time(
            existing["expires_at"]
        ) > now + timedelta(minutes=2)
        if (
            int(existing["amount_minor"]) == amount
            and existing["currency"] == currency
            and existing["short_url"]
            and still_open
        ):
            return view(connection, existing)
        _close_at_provider(connection, existing, secret_store)
    provider = client(connection, secret_store)
    request_id = kit.new_id()
    reference = f"MS{request_id[:18].upper()}"
    customer = info.get("customer") or {}
    callback = (
        kit.public_url(str(info["return_path"])) if info.get("return_path") else ""
    )
    description = (
        kit.text(info.get("description"), "a description", max_len=200) or spec.label
    )
    link = provider.create_payment_link(
        amount=razorpay.minor_units(amount, currency),
        currency=currency,
        reference_id=reference,
        description=description,
        customer={
            "name": customer.get("name") or "",
            "email": customer.get("email") or "",
            "contact": customer.get("phone") or "",
        }
        if prefill
        else {},
        notify={
            "sms": bool(settings["notify_customer"]),
            "email": bool(settings["notify_customer"]),
        },
        callback_url=callback,
        expire_by=int(expires.timestamp()),
        notes={
            "monosage_request": request_id,
            "app": app,
            "for": f"{object_type}:{object_id}",
        },
    )
    link_id = str(link.get("id") or "")
    short_url = str(link.get("short_url") or "")
    if not re.fullmatch(
        r"plink_[A-Za-z0-9]{4,40}", link_id
    ) or not short_url.lower().startswith("https://"):
        raise razorpay.RazorpayError("Razorpay did not send back a payment link.")
    stamp = kit.now()
    connection.execute(
        """INSERT INTO payment_requests (id, app, object_type, object_id, reference, description, amount_minor, currency,
        provider, provider_ref, short_url, mode, status, customer_name, customer_email, customer_phone, return_path,
        created_by, expires_at, created_at, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'created', ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            request_id,
            app,
            object_type,
            object_id,
            reference,
            description,
            amount,
            currency,
            PROVIDER,
            link_id,
            short_url,
            provider.mode,
            str(customer.get("name") or "")[:120],
            str(customer.get("email") or "")[:254],
            str(customer.get("phone") or "")[:30],
            str(info.get("return_path") or "")[:200],
            actor[:120] or ("customer" if public_visit else ""),
            kit.iso(expires),
            stamp,
            stamp,
        ),
    )
    kit.log(
        connection,
        app,
        "payment_link",
        f"Payment link for {description}",
        detail=f"{kit.fmt_money(amount, currency)} · Razorpay {provider.mode} mode",
        ref_type=object_type,
        ref_id=object_id,
        amount_cents=amount,
        actor=actor,
        public=public_visit,
    )
    connection.commit()
    return view(connection, get(connection, request_id))


def _close_at_provider(
    connection: sqlite3.Connection,
    row: Mapping[str, Any],
    secret_store: Any = None,
    *,
    status: str = "cancelled",
) -> None:
    """Close an open link here, and at Razorpay when it can be reached (its own expiry closes it otherwise)."""
    try:
        client(connection, secret_store).cancel_payment_link(str(row["provider_ref"]))
    except (ValueError, razorpay.RazorpayError):
        kit.suite.logger.info(
            "Could not cancel payment link %s at Razorpay",
            row["provider_ref"],
            exc_info=True,
        )
    _close(connection, row, status)


def _close(connection: sqlite3.Connection, row: Mapping[str, Any], status: str) -> bool:
    changed = connection.execute(
        "UPDATE payment_requests SET status = ?, closed_at = ?, updated_at = ? WHERE id = ? AND status = 'created'",
        (status, kit.now(), kit.now(), row["id"]),
    ).rowcount
    connection.commit()
    return bool(changed)


def cancel_request(
    connection: sqlite3.Connection, request_id: str, *, secret_store: Any = None
) -> dict[str, Any]:
    row = get(connection, request_id)
    if row["status"] != "created":
        raise ValueError("Only an open payment link can be cancelled.")
    _close_at_provider(connection, row, secret_store)
    return view(connection, get(connection, request_id))


def close_open(
    connection: sqlite3.Connection,
    app: str,
    object_type: str,
    object_id: str,
    *,
    status: str = "cancelled",
) -> int:
    """The thing was cancelled or expired in the app: its open links stop here (they expire at Razorpay on their
    own ``expire_by``; a payment that still arrives is shown to the business as a mismatch)."""
    ensure(connection)
    rows = kit.fetch_all(
        connection,
        "SELECT * FROM payment_requests WHERE app = ? AND object_type = ? AND object_id = ? AND status = 'created'",
        (app, object_type, object_id),
    )
    for row in rows:
        _close(connection, row, status)
    return len(rows)


def has_open_link(
    connection: sqlite3.Connection, app: str, object_type: str, object_id: str
) -> bool:
    ensure(connection)
    return _open_for(connection, app, object_type, object_id) is not None


# ---------------------------------------------------------------------------
# a payment arrives
# ---------------------------------------------------------------------------


def _find(
    connection: sqlite3.Connection,
    link_entity: Mapping[str, Any] | None,
    payment: Mapping[str, Any] | None,
) -> dict[str, Any] | None:
    """The request an event is about: by the payment link's id, then our id in its notes, then the reference."""
    link_entity = link_entity or {}
    payment = payment or {}
    link_id = str(link_entity.get("id") or "")
    if link_id:
        found = kit.fetch_one(
            connection,
            "SELECT * FROM payment_requests WHERE provider = ? AND provider_ref = ?",
            (PROVIDER, link_id),
        )
        if found:
            return found
    for notes in (link_entity.get("notes"), payment.get("notes")):
        if isinstance(notes, Mapping) and notes.get("monosage_request"):
            found = kit.fetch_one(
                connection,
                "SELECT * FROM payment_requests WHERE id = ?",
                (str(notes["monosage_request"])[:64],),
            )
            if found:
                return found
    reference = str(link_entity.get("reference_id") or "")
    if reference:
        return kit.fetch_one(
            connection,
            "SELECT * FROM payment_requests WHERE reference = ?",
            (reference,),
        )
    payment_id = str(payment.get("id") or "")
    if payment_id:
        return kit.fetch_one(
            connection,
            "SELECT * FROM payment_requests WHERE payment_id = ?",
            (payment_id,),
        )
    return None


def _mismatch(
    connection: sqlite3.Connection, row: Mapping[str, Any], problem: str
) -> None:
    connection.execute(
        "UPDATE payment_requests SET status = 'mismatch', problem = ?, updated_at = ? WHERE id = ?",
        (problem[:500], kit.now(), row["id"]),
    )
    kit.log(
        connection,
        str(row["app"]),
        "payment_problem",
        f"Check a payment: {row['description']}",
        detail=problem[:500],
        ref_type=str(row["object_type"]),
        ref_id=str(row["object_id"]),
        amount_cents=int(row["amount_minor"]),
    )
    connection.commit()


def apply_payment(
    connection: sqlite3.Connection, row: Mapping[str, Any], payment: Mapping[str, Any]
) -> str:
    """A payment the provider confirmed (a webhook, or a check after the customer came back). Marks the thing paid
    only when the amount and the currency are the ones asked; otherwise the request is a ``mismatch``."""
    current = get(connection, str(row["id"]))
    if current["status"] in PAID_STATUSES:
        return "already_paid"
    if current["status"] == "mismatch":
        return "mismatch"
    payment_id = str(payment.get("id") or "")
    paid_minor = int(payment.get("amount") or 0)
    paid_currency = str(payment.get("currency") or "").upper()
    method = str(payment.get("method") or "")[:30]
    expected = razorpay.minor_units(
        int(current["amount_minor"]), str(current["currency"])
    )
    state = str(payment.get("status") or "captured")
    connection.execute(
        "UPDATE payment_requests SET payment_id = ?, paid_minor = ?, paid_currency = ?, method = ?, updated_at = ? WHERE id = ?",
        (
            payment_id[:40],
            paid_minor,
            paid_currency[:3],
            method,
            kit.now(),
            current["id"],
        ),
    )
    if state not in ("captured", "paid"):
        _mismatch(
            connection,
            current,
            f"Razorpay reports this payment as {state}, not captured. Capture or refund it in the Razorpay dashboard.",
        )
        return "mismatch"
    if paid_minor != expected or paid_currency != current["currency"]:
        got = kit.fmt_money(
            razorpay.from_minor_units(paid_minor, paid_currency or "INR"),
            paid_currency or current["currency"],
        )
        asked = kit.fmt_money(int(current["amount_minor"]), str(current["currency"]))
        _mismatch(
            connection,
            current,
            f"Razorpay received {got} but this asked for {asked}. Nothing was marked paid: check it in Razorpay, then refund it or record it by hand.",
        )
        return "mismatch"
    stamp = kit.now()
    if current["status"] in ("expired", "cancelled"):
        note = "Paid after the link was closed here."
    else:
        note = ""
    connection.execute(
        "UPDATE payment_requests SET status = 'paid', paid_at = ?, closed_at = COALESCE(closed_at, ?), problem = ?, updated_at = ? WHERE id = ?",
        (stamp, stamp, note, stamp, current["id"]),
    )
    connection.commit()
    spec = REGISTRY.get((str(current["app"]), str(current["object_type"])))
    if spec is None:
        _mismatch(
            connection,
            current,
            "The app this payment was for is not available, so it was not recorded there.",
        )
        return "mismatch"
    paid = {
        "request_id": current["id"],
        "payment_id": payment_id,
        "method": method,
        "amount_cents": int(current["amount_minor"]),
        "currency": current["currency"],
        "note": f"Paid online with Razorpay{f' ({method})' if method else ''} · {payment_id}",
        "provider": PROVIDER,
    }
    try:
        outcome = spec.on_paid(connection, str(current["object_id"]), paid)
    except (ValueError, LookupError, PermissionError) as error:
        connection.rollback()
        _mismatch(
            connection,
            current,
            f"The payment arrived, but {spec.label} could not be marked paid: {error} Refund it or settle it by hand.",
        )
        return "needs_attention"
    source = outcome if isinstance(outcome, Mapping) else {}
    connection.execute(
        "UPDATE payment_requests SET revenue_type = ?, revenue_id = ? WHERE id = ?",
        (
            str(source.get("source_type") or current["object_type"])[:40],
            str(source.get("source_id") or current["object_id"])[:64],
            current["id"],
        ),
    )
    connection.commit()
    return "paid"


# ---------------------------------------------------------------------------
# webhooks
# ---------------------------------------------------------------------------


def handle_webhook(
    connection: sqlite3.Connection,
    body: bytes,
    *,
    signature: str,
    event_id: str = "",
    secret_store: Any = None,
) -> dict[str, Any]:
    """One Razorpay webhook delivery. The signature is checked before anything else is read; an event seen
    before (same event id) changes nothing. Raises PermissionError (403) for a bad signature."""
    if len(body) > MAX_WEBHOOK_BYTES:
        raise ValueError("The webhook body is too large.")
    secret = webhook_secret(connection, secret_store)
    if not secret:
        raise PermissionError(
            "Razorpay webhooks are not set up here: save the webhook secret first."
        )
    if not razorpay.verify_webhook(body, signature, secret):
        raise PermissionError("The webhook signature does not match.")
    ensure(connection)
    try:
        event = json.loads(body)
    except ValueError:
        raise ValueError("The webhook body is not JSON.") from None
    if not isinstance(event, dict):
        raise ValueError("The webhook body is not a JSON object.")
    name = str(event.get("event") or "")[:60]
    key = str(event_id or "").strip()
    if not re.fullmatch(r"[A-Za-z0-9_.:-]{1,100}", key):
        key = "body:" + hashlib.sha256(body).hexdigest()
    inserted = connection.execute(
        "INSERT OR IGNORE INTO payment_events (event_id, provider, event, outcome, received_at) VALUES (?, ?, ?, 'processing', ?)",
        (key, PROVIDER, name, kit.now()),
    ).rowcount
    connection.commit()
    if not inserted:
        return {"ok": True, "event": name, "duplicate": True}
    try:
        outcome, request_id = _dispatch(
            connection, name, event.get("payload") or {}, secret_store
        )
    except Exception:
        connection.rollback()
        # not recorded, so Razorpay's retry processes it again
        connection.execute("DELETE FROM payment_events WHERE event_id = ?", (key,))
        connection.commit()
        raise
    connection.execute(
        "UPDATE payment_events SET outcome = ?, request_id = ? WHERE event_id = ?",
        (outcome, request_id, key),
    )
    connection.commit()
    return {"ok": True, "event": name, "outcome": outcome}


def _entity(payload: Mapping[str, Any], name: str) -> dict[str, Any]:
    part = payload.get(name) if isinstance(payload, Mapping) else None
    entity = part.get("entity") if isinstance(part, Mapping) else None
    return dict(entity) if isinstance(entity, Mapping) else {}


def _dispatch(
    connection: sqlite3.Connection,
    name: str,
    payload: Mapping[str, Any],
    secret_store: Any,
) -> tuple[str, str]:
    link_entity = _entity(payload, "payment_link")
    payment = _entity(payload, "payment")
    if name == "payment_link.paid":
        row = _find(connection, link_entity, payment)
        if row is None:
            return "unmatched", ""
        if not payment:
            payment = {
                "id": "",
                "amount": link_entity.get("amount_paid"),
                "currency": link_entity.get("currency"),
                "status": "paid",
            }
        return apply_payment(connection, row, payment), str(row["id"])
    if name in ("payment_link.expired", "payment_link.cancelled"):
        row = _find(connection, link_entity, None)
        if row is None:
            return "unmatched", ""
        closed = _close(
            connection, row, "expired" if name.endswith("expired") else "cancelled"
        )
        return ("closed" if closed else "unchanged"), str(row["id"])
    if name == "payment.failed":
        row = _find(connection, None, payment)
        if row is None:
            return "unmatched", ""
        reason = str(
            payment.get("error_description")
            or payment.get("error_reason")
            or "no reason given"
        )
        connection.execute(
            "UPDATE payment_requests SET problem = ?, updated_at = ? WHERE id = ? AND status = 'created'",
            (
                f"A payment attempt failed: {reason[:200]}. The customer can try again on the same link.",
                kit.now(),
                row["id"],
            ),
        )
        kit.log(
            connection,
            str(row["app"]),
            "payment_failed",
            f"A payment attempt failed: {row['description']}",
            detail=reason[:300],
            ref_type=str(row["object_type"]),
            ref_id=str(row["object_id"]),
            public=True,
        )
        connection.commit()
        return "failed_attempt", str(row["id"])
    if name in ("refund.processed", "refund.failed", "refund.created"):
        return _refund_event(connection, name, _entity(payload, "refund"), payment)
    return "ignored", ""


# ---------------------------------------------------------------------------
# refunds
# ---------------------------------------------------------------------------


def refundable(connection: sqlite3.Connection, row: Mapping[str, Any]) -> int:
    """What can still be refunded (cents): the amount paid, less refunds made or on their way."""
    if row.get("status") not in PAID_STATUSES:
        return 0
    pending = kit.count(
        connection,
        "SELECT COALESCE(SUM(amount_minor), 0) FROM payment_refunds WHERE request_id = ? AND status = 'pending'",
        (row["id"],),
    )
    return max(0, int(row["amount_minor"]) - int(row["refunded_minor"] or 0) - pending)


def refund(
    connection: sqlite3.Connection,
    request_id: str,
    *,
    amount_cents: Any = None,
    note: str = "",
    secret_store: Any = None,
    actor: str = "",
) -> dict[str, Any]:
    """Give back all (``amount_cents`` empty) or part of a payment through the provider. The ledger gets a negative
    line in this month once the provider says the refund is processed (at once, or by the ``refund.processed``
    webhook)."""
    row = get(connection, request_id)
    left = refundable(connection, row)
    if row["status"] not in PAID_STATUSES or left <= 0:
        raise ValueError("Only a paid payment with money left on it can be refunded.")
    amount = kit.integer(
        amount_cents, "the refund amount (in cents)", default=left, minimum=1
    )
    if amount > left:
        raise ValueError(
            f"At most {kit.fmt_money(left, row['currency'])} can be refunded."
        )
    if not row["payment_id"]:
        raise ValueError("This payment has no Razorpay payment id to refund.")
    provider = client(connection, secret_store)
    refund_id = kit.new_id()
    result = provider.refund(
        str(row["payment_id"]),
        amount=razorpay.minor_units(amount, str(row["currency"])),
        notes={
            "monosage_request": row["id"],
            "monosage_refund": refund_id,
            "note": note[:200],
        },
        receipt=f"R{refund_id[:20].upper()}",
    )
    status = str(result.get("status") or "pending")
    stamp = kit.now()
    connection.execute(
        """INSERT INTO payment_refunds (id, request_id, provider_ref, amount_minor, currency, status, note, created_by, created_at, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            refund_id,
            row["id"],
            str(result.get("id") or "")[:40],
            amount,
            row["currency"],
            "processed"
            if status == "processed"
            else "failed"
            if status == "failed"
            else "pending",
            kit.text(note, "a note", max_len=300),
            actor[:120],
            stamp,
            stamp,
        ),
    )
    connection.commit()
    if status == "processed":
        _apply_refund(connection, refund_id)
    return view(connection, get(connection, request_id))


def _apply_refund(connection: sqlite3.Connection, refund_id: str) -> None:
    """The money went back: once, the ledger line, the request's totals and the app (Shop restocks)."""
    claimed = connection.execute(
        "UPDATE payment_refunds SET status = 'processed', applied_at = ?, updated_at = ? WHERE id = ? AND applied_at IS NULL",
        (kit.now(), kit.now(), refund_id),
    ).rowcount
    connection.commit()
    if not claimed:
        return
    refund_row = kit.require_row(connection, "payment_refunds", refund_id, "refund")
    row = get(connection, str(refund_row["request_id"]))
    amount = int(refund_row["amount_minor"])
    refunded = min(int(row["amount_minor"]), int(row["refunded_minor"] or 0) + amount)
    full = refunded >= int(row["amount_minor"])
    connection.execute(
        "UPDATE payment_requests SET refunded_minor = ?, status = ?, updated_at = ? WHERE id = ?",
        (refunded, "refunded" if full else "partially_refunded", kit.now(), row["id"]),
    )
    kit.void_revenue(
        connection,
        str(row["app"]),
        str(row["revenue_type"] or row["object_type"]),
        str(row["revenue_id"] or row["object_id"]),
        refund_cents=amount,
        refund_id=refund_id,
    )
    kit.log(
        connection,
        str(row["app"]),
        "refund",
        f"Refunded {kit.fmt_money(amount, str(row['currency']))}: {row['description']}",
        detail=str(refund_row["note"] or ""),
        ref_type=str(row["object_type"]),
        ref_id=str(row["object_id"]),
        amount_cents=-amount,
        actor=str(refund_row["created_by"] or ""),
    )
    connection.commit()
    spec = REGISTRY.get((str(row["app"]), str(row["object_type"])))
    if spec is not None and spec.on_refund is not None:
        try:
            spec.on_refund(
                connection,
                str(row["object_id"]),
                {
                    "amount_cents": amount,
                    "full": full,
                    "note": str(refund_row["note"] or ""),
                },
            )
        except (ValueError, LookupError) as error:
            connection.rollback()
            connection.execute(
                "UPDATE payment_requests SET problem = ? WHERE id = ?",
                (
                    f"Refunded, but {spec.label} could not be updated: {error}"[:500],
                    row["id"],
                ),
            )
        connection.commit()


def _refund_event(
    connection: sqlite3.Connection,
    name: str,
    entity: Mapping[str, Any],
    payment: Mapping[str, Any],
) -> tuple[str, str]:
    provider_ref = str(entity.get("id") or "")
    found = kit.fetch_one(
        connection,
        "SELECT * FROM payment_refunds WHERE provider_ref = ? AND provider_ref != ''",
        (provider_ref,),
    )
    notes = entity.get("notes") if isinstance(entity.get("notes"), Mapping) else {}
    if found is None and notes.get("monosage_refund"):
        found = kit.fetch_one(
            connection,
            "SELECT * FROM payment_refunds WHERE id = ?",
            (str(notes["monosage_refund"])[:64],),
        )
    if found is None:
        # a refund made in the Razorpay dashboard: find the payment it gives back
        payment_id = str(entity.get("payment_id") or payment.get("id") or "")
        row = kit.fetch_one(
            connection,
            "SELECT * FROM payment_requests WHERE payment_id = ? AND payment_id != ''",
            (payment_id,),
        )
        if row is None:
            return "unmatched", ""
        if name != "refund.processed":
            return "noted", str(row["id"])
        amount = razorpay.from_minor_units(
            int(entity.get("amount") or 0),
            str(entity.get("currency") or row["currency"]),
        )
        amount = min(
            amount, max(0, int(row["amount_minor"]) - int(row["refunded_minor"] or 0))
        )
        if amount <= 0:
            return "unchanged", str(row["id"])
        refund_id = kit.new_id()
        stamp = kit.now()
        connection.execute(
            """INSERT INTO payment_refunds (id, request_id, provider_ref, amount_minor, currency, status, note, created_by, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, 'pending', 'Refunded in the Razorpay dashboard', 'razorpay', ?, ?)""",
            (
                refund_id,
                row["id"],
                provider_ref[:40],
                amount,
                row["currency"],
                stamp,
                stamp,
            ),
        )
        connection.commit()
        _apply_refund(connection, refund_id)
        return "refunded", str(row["id"])
    if name == "refund.processed":
        _apply_refund(connection, str(found["id"]))
        return "refunded", str(found["request_id"])
    if name == "refund.failed" and not found["applied_at"]:
        connection.execute(
            "UPDATE payment_refunds SET status = 'failed', problem = ?, updated_at = ? WHERE id = ?",
            (
                "Razorpay could not process this refund. Try again or refund the customer another way.",
                kit.now(),
                found["id"],
            ),
        )
        connection.execute(
            "UPDATE payment_requests SET problem = ?, updated_at = ? WHERE id = ?",
            (
                "A refund failed at Razorpay. Try again or refund the customer another way.",
                kit.now(),
                found["request_id"],
            ),
        )
        connection.commit()
        return "refund_failed", str(found["request_id"])
    return "noted", str(found["request_id"])


# ---------------------------------------------------------------------------
# the customer comes back from the payment page
# ---------------------------------------------------------------------------

RETURN_FIELDS = (
    "razorpay_payment_id",
    "razorpay_payment_link_id",
    "razorpay_payment_link_reference_id",
    "razorpay_payment_link_status",
    "razorpay_signature",
)


def confirm_return(
    connection: sqlite3.Connection,
    params: Mapping[str, Any],
    *,
    secret_store: Any = None,
) -> dict[str, Any]:
    """Check the return's signature (the key secret), then ask Razorpay about the payment and record it if it is
    paid for the right amount. The webhook does the same independently; whichever is first wins, once."""
    ensure(connection)
    values = {field: str(params.get(field) or "")[:120] for field in RETURN_FIELDS}
    secret = key_secret(connection, secret_store)
    verified = razorpay.verify_payment_link_callback(
        payment_link_id=values["razorpay_payment_link_id"],
        reference_id=values["razorpay_payment_link_reference_id"],
        status=values["razorpay_payment_link_status"],
        payment_id=values["razorpay_payment_id"],
        signature=values["razorpay_signature"],
        key_secret=secret,
    )
    if not verified:
        return {"verified": False, "paid": False, "status": ""}
    row = kit.fetch_one(
        connection,
        "SELECT * FROM payment_requests WHERE provider = ? AND provider_ref = ?",
        (PROVIDER, values["razorpay_payment_link_id"]),
    )
    if row is None:
        return {
            "verified": True,
            "paid": values["razorpay_payment_link_status"] == "paid",
            "status": "unknown",
        }
    if values["razorpay_payment_link_status"] == "paid" and row["status"] in (
        "created",
        "expired",
        "cancelled",
    ):
        try:
            payment = client(connection, secret_store).fetch_payment(
                values["razorpay_payment_id"]
            )
            if str(payment.get("id") or "") == values["razorpay_payment_id"]:
                apply_payment(connection, row, payment)
        except (ValueError, razorpay.RazorpayError):
            kit.suite.logger.info(
                "Could not confirm a returned payment with Razorpay", exc_info=True
            )
        row = get(connection, str(row["id"]))
    return {
        "verified": True,
        # "Paid" at once from the signed return; the record follows the webhook (or the check above)
        "paid": values["razorpay_payment_link_status"] == "paid"
        and row["status"] != "mismatch",
        "status": row["status"],
        "amount_cents": int(row["amount_minor"]),
        "currency": row["currency"],
        "reference": row["reference"],
    }


# ---------------------------------------------------------------------------
# public pages: "Pay online" and the return
# ---------------------------------------------------------------------------


def public_page(
    app: str, kind: str, object_type: str, resolve: ResolveFn, *, prefill: bool = True
) -> None:
    """Give a public page (``app``/``kind``) its ``pay_online`` and ``pay_return`` actions.

    ``resolve(connection, link, payload)`` names the thing on this page the customer pays (the link's own order,
    or the enrolment its ``reference`` names), or None. ``prefill`` False for a page that names it by reference."""

    def pay_online(
        connection: sqlite3.Connection,
        link: dict[str, Any],
        payload: dict[str, Any],
        visitor: dict[str, Any],
    ) -> tuple[int, dict[str, Any]]:
        if not kit.online_ready(kit.payment_settings(connection)):
            raise ValueError(
                "This business does not take online payments yet. Use the other ways to pay shown here."
            )
        object_id = resolve(connection, link, payload)
        if not object_id:
            raise LookupError("There is nothing to pay on this page.")
        made = create_request(
            connection, app, object_type, object_id, public_visit=True, prefill=prefill
        )
        return 200, {
            "url": made["short_url"],
            "amount_cents": made["amount_cents"],
            "currency": made["currency"],
        }

    def pay_return(
        connection: sqlite3.Connection,
        link: dict[str, Any],
        payload: dict[str, Any],
        visitor: dict[str, Any],
    ) -> tuple[int, dict[str, Any]]:
        return 200, confirm_return(connection, payload)

    public.action(app, kind, "pay_online")(pay_online)
    public.action(app, kind, "pay_return")(pay_return)


def link_on_checkout(
    connection: sqlite3.Connection, app: str, object_type: str, object_id: str
) -> str:
    """At a public checkout: the payment page to send the customer to when the business takes online payments
    ('' when it does not, or when Razorpay cannot be reached: the customer still sees the other ways to pay)."""
    if not kit.online_ready(kit.payment_settings(connection)):
        return ""
    try:
        return str(
            create_request(connection, app, object_type, object_id, public_visit=True)[
                "short_url"
            ]
        )
    except (ValueError, LookupError, razorpay.RazorpayError):
        kit.suite.logger.warning(
            "Could not make a payment link at checkout", exc_info=True
        )
        return ""


# ---------------------------------------------------------------------------
# the webhook address
# ---------------------------------------------------------------------------

public.register_kind(APP, WEBHOOK_KIND)


def webhook_path(connection: sqlite3.Connection, *, create: bool = True) -> str:
    """``/api/payments/razorpay/webhook/<token>``: the token names this business's database (its own locally,
    the organization's hosted), so one server answers every business's Razorpay account."""
    link = (
        public.create_link(
            connection, APP, WEBHOOK_KIND, WEBHOOK_REF, title="Razorpay webhook"
        )
        if create
        else public.link_for(connection, APP, WEBHOOK_KIND, WEBHOOK_REF)
    )
    if not link:
        return ""
    return f"/api/payments/razorpay/webhook/{link['token']}"


def is_webhook_link(link: Mapping[str, Any]) -> bool:
    return link.get("app") == APP and link.get("kind") == WEBHOOK_KIND
