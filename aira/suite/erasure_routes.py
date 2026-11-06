"""Business hub > Privacy (``/api/suite/privacy/*``): a business erases one customer, and sees the erasures run in
this account.

A customer asks to be forgotten: the business types their email address or phone number, sees what names them
(``preview``: counts per table, never the records), and erases them across Shop, Bookings, Marketing, Service,
Events, Academy, Studio, Leads, payments and the mail it keeps (:func:`aira.erasure.erase_customer`). Orders,
invoices, tickets and enrolments stay for the books with their amounts, without the name, email, phone and
address. Every erasure leaves a receipt (counts per app); one that could not finish can be tried again.

Loaded by :func:`aira.suite.load_apps`. The background pass tries unfinished erasures again by itself
(:mod:`aira.suite.background`).
"""

from __future__ import annotations

from typing import Any

from aira import erasure
from aira.airaapi.http import Request, aira_route
from aira.suite import life

BASE = "/api/suite/privacy"
ID = "([0-9a-f]{32})"
ADULTS = "Erasing customers is for the grown-ups in your household. Ask one of them to do it."


def _grown_up(request: Request) -> None:
    with request.connect() as connection:
        life.require_grown_up(request, connection, ADULTS)


def _who(request: Request) -> dict[str, list[str]]:
    email = str(request.payload.get("email") or "").strip()
    phone = str(request.payload.get("phone") or "").strip()
    if not email and not phone:
        raise ValueError("Type the customer's email address or phone number.")
    if email and "@" not in email:
        raise ValueError(f"{email} is not an email address.")
    if phone and not erasure.phone_key(phone):
        raise ValueError(f"{phone} is not a phone number.")
    return {"emails": [email] if email else [], "phones": [phone] if phone else []}


@aira_route("POST", f"{BASE}/preview")
def preview(request: Request) -> tuple[int, dict[str, Any]]:
    """How many records in each table name this customer (sent in the body, never in the address)."""
    _grown_up(request)
    who = _who(request)
    with request.connect() as connection:
        found = erasure.preview_customer(connection, **who)
    return 200, {"found": found, "total": sum(found.values())}


@aira_route("POST", f"{BASE}/erase")
def erase(request: Request) -> tuple[int, dict[str, Any]]:
    """Erase the customer (``confirm`` must be true): the receipt says what changed in each app."""
    _grown_up(request)
    who = _who(request)
    if request.payload.get("confirm") is not True:
        raise ValueError("Confirm the erasure: it cannot be undone.")
    with request.connect() as connection:
        receipt = erasure.erase_customer(connection, actor="business", **who)
    # the audit log names the erasure and its counts, never the customer
    request.audit(
        "suite.customer_erase",
        "erasure",
        receipt["job_id"],
        {"status": receipt["status"]},
    )
    return 200, {"receipt": receipt}


@aira_route("GET", f"{BASE}/erasures")
def erasures(request: Request) -> tuple[int, dict[str, Any]]:
    """Every erasure run in this account (people removed from the household, customers), as receipts."""
    _grown_up(request)
    with request.connect() as connection:
        return 200, {"erasures": erasure.jobs(connection)}


@aira_route("POST", f"{BASE}/erasures/{ID}/retry")
def erasure_retry(request: Request) -> tuple[int, dict[str, Any]]:
    _grown_up(request)
    with request.connect() as connection:
        receipt = erasure.retry(connection, request.match.group(1), actor="business")
    request.audit(
        "suite.erasure_retry",
        "erasure",
        receipt["job_id"],
        {"status": receipt["status"]},
    )
    return 200, {"receipt": receipt}
