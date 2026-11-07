"""Customers: the business's one record of each person, across every app (Business hub > Customers).

The contacts themselves live in the kit (``suite_contacts``, :func:`aira.suite.kit.contact_for`); each app's own row
(an order, a booking customer, a form lead, a subscriber, a case, a lead, a ticket order, an enrolment) points at
one with ``contact_id``. Here:

* :func:`search` and :func:`get` (the contact with its :func:`timeline`: orders, bookings, cases, form
  submissions, tickets, courses, deals, payments and emails sent, newest first);
* :func:`update` (name, company, tags, notes) and :func:`merge` (two records of the same person become one);
* :func:`send_to_sales` (the "Send to Sales" handoff): a Marketing form lead, a Service case or a contact becomes a
  deal in the Sales pipeline (Studio's ``st_deals``), once.

Everything only reads the tables an app has already made: a business without Events simply has no tickets here.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Iterable
from datetime import timedelta
from typing import Any

from aira.suite import kit

APP = "core"
HANDOFF_SOURCES = {
    ("marketing", "lead"): "Marketing form lead",
    ("service", "case"): "Service case",
    ("suite", "contact"): "Customer",
}
DUPLICATE_WINDOW = timedelta(
    days=7
)  # two payments of the same amount this close, in two apps, may be one


def _tables(connection: sqlite3.Connection) -> set[str]:
    return {
        str(row[0])
        for row in connection.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table'"
        )
    }


def _ready(connection: sqlite3.Connection) -> set[str]:
    kit.ensure_contacts(connection)
    tables = _tables(connection)
    for table in kit.CONTACT_SOURCES:
        if table in tables:
            kit.contact_columns(connection, table)
    return tables


def sync(connection: sqlite3.Connection) -> int:
    """Link every app row that is not linked yet (rows made before this feature, or by an import)."""
    _ready(connection)
    return kit.link_pending(connection)


def _view(row: dict[str, Any]) -> dict[str, Any]:
    out = dict(row)
    out["tags"] = kit.loads(out.get("tags"), []) or []
    out["apps"] = kit.loads(out.get("apps"), []) or []
    for name in ("email_consent", "sms_consent"):
        value = out.get(name)
        out[name] = None if value is None else bool(value)
    return out


# ---------------------------------------------------------------------------
# finding and reading
# ---------------------------------------------------------------------------


def search(
    connection: sqlite3.Connection, *, q: str = "", app: str = "", limit: int = 200
) -> dict[str, Any]:
    sync(connection)
    clauses, params = [], []
    text = str(q or "").strip()[:80]
    if text:
        like = f"%{text}%"
        clauses.append(
            "(name LIKE ? OR email LIKE ? OR phone LIKE ? OR company LIKE ? OR tags LIKE ?)"
        )
        params += [like, like, like, like, like]
        digits = kit.phone_key(text)
        if digits:
            clauses[-1] = (
                clauses[-1][:-1]
                + " OR id IN (SELECT contact_id FROM suite_contact_keys WHERE key = ?))"
            )
            params.append(f"p:{digits}")
    if app:
        clauses.append("apps LIKE ?")
        params.append(f'%"{kit.slug(app, max_len=40)}"%')
    where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    rows = kit.fetch_all(
        connection,
        f"SELECT * FROM suite_contacts {where} ORDER BY last_seen DESC, created_at DESC LIMIT ?",  # noqa: S608
        (*params, max(1, min(int(limit), 1000))),
    )
    total = kit.count(
        connection, f"SELECT COUNT(*) FROM suite_contacts {where}", params
    )  # noqa: S608
    apps = sorted(
        {
            name
            for row in kit.fetch_all(
                connection, "SELECT DISTINCT apps FROM suite_contacts"
            )
            for name in (kit.loads(row["apps"], []) or [])
        }
    )
    return {"contacts": [_view(row) for row in rows], "total": total, "apps": apps}


def _contact(connection: sqlite3.Connection, contact_id: str) -> dict[str, Any]:
    _ready(connection)
    return _view(kit.require_row(connection, "suite_contacts", contact_id, "customer"))


def get(connection: sqlite3.Connection, contact_id: str) -> dict[str, Any]:
    """The contact, everything that points at it, and its timeline."""
    contact = _contact(connection, contact_id)
    items = timeline(connection, contact_id)
    counted = [
        item for item in items if item["kind"] == "payment" and item.get("counts")
    ]
    totals: dict[str, int] = {}
    for item in items:
        if item["kind"] in ("payment", "refund") and item.get("counts"):
            code = str(item.get("currency") or "")
            totals[code] = totals.get(code, 0) + int(item.get("amount_cents") or 0)
    deals = [item for item in items if item["kind"] == "deal"]
    return {
        "contact": contact,
        "timeline": items,
        "keys": [
            row["key"]
            for row in kit.fetch_all(
                connection,
                "SELECT key FROM suite_contact_keys WHERE contact_id = ? ORDER BY key",
                (contact_id,),
            )
        ],
        "paid": [
            {"currency": code, "cents": cents} for code, cents in sorted(totals.items())
        ],
        "payments": len(counted),
        "possible_duplicates": sum(
            1 for item in items if item.get("possible_duplicate")
        ),
        "deals": deals,
        "sales_available": _sales_available(connection),
    }


def _sales_available(connection: sqlite3.Connection) -> bool:
    from aira import suite

    return suite.app_enabled("studio")


def _item(
    at: Any,
    app: str,
    kind: str,
    title: str,
    *,
    detail: str = "",
    href: str = "",
    amount_cents: int | None = None,
    currency: str = "",
    status: str = "",
    ref: str = "",
) -> dict[str, Any]:
    return {
        "at": str(at or ""),
        "app": app,
        "kind": kind,
        "title": title[:200],
        "detail": detail[:300],
        "href": href,
        "amount_cents": amount_cents,
        "currency": currency,
        "status": status,
        "ref": ref,
    }


def _money(cents: Any, code: str) -> str:
    return kit.fmt_money(int(cents or 0), code or "USD")


def timeline(connection: sqlite3.Connection, contact_id: str) -> list[dict[str, Any]]:
    """Everything this person did with the business, newest first. Payments are the ledger's own lines (so a payment
    that stopped counting shows as such), with ``possible_duplicate`` on two payments of the same amount in two
    apps within a week: they may be one payment recorded twice."""
    tables = _ready(connection)
    home = kit.business(connection)["currency"]
    items: list[dict[str, Any]] = []
    sources: list[
        tuple[str, str, str]
    ] = []  # (app, source_type, source_id) of the ledger lines to look up
    requests: list[
        tuple[str, str, str]
    ] = []  # (app, object_type, object_id) of payment links
    emails: set[str] = set()

    def rows(sql: str, params: Iterable[Any] = ()) -> list[dict[str, Any]]:
        return kit.fetch_all(connection, sql, tuple(params))

    if "sh_orders" in tables:
        for order in rows(
            "SELECT * FROM sh_orders WHERE contact_id = ?", (contact_id,)
        ):
            emails.add(str(order["customer_email"]).lower())
            items.append(
                _item(
                    order["created_at"],
                    "shop",
                    "order",
                    f"Order #{order['number']}",
                    detail=f"{_money(order['total_cents'], order['currency'])} · {order['payment_status']} · {order['fulfilment_status']}",
                    href=f"#/shop?tab=orders&order={order['id']}",
                    amount_cents=int(order["total_cents"]),
                    currency=str(order["currency"]),
                    status=str(order["payment_status"]),
                    ref=order["id"],
                )
            )
            sources.append(("shop", "order", order["id"]))
            requests.append(("shop", "order", order["id"]))
    if "bk_customers" in tables and "bk_bookings" in tables:
        for booking in rows(
            """SELECT b.* FROM bk_bookings b JOIN bk_customers c ON c.id = b.customer_id WHERE c.contact_id = ?""",
            (contact_id,),
        ):
            emails.add(str(booking["customer_email"]).lower())
            items.append(
                _item(
                    booking["start_at"],
                    "bookings",
                    "booking",
                    f"Booking: {booking['service_name']}",
                    detail=f"{booking['reference']} · {str(booking['status']).replace('_', ' ')}"
                    + (
                        f" · {_money(booking['price_cents'], home)}"
                        if booking["price_cents"]
                        else ""
                    ),
                    href=f"#/bookings?tab=appointments&booking={booking['id']}",
                    amount_cents=int(booking["price_cents"] or 0),
                    currency=home,
                    status=str(booking["status"]),
                    ref=booking["id"],
                )
            )
            sources.append(("bookings", "booking", booking["id"]))
            requests.append(("bookings", "booking", booking["id"]))
        if "bk_customer_packages" in tables:
            for package in rows(
                """SELECT p.* FROM bk_customer_packages p JOIN bk_customers c ON c.id = p.customer_id WHERE c.contact_id = ?""",
                (contact_id,),
            ):
                sources.append(("bookings", "package", package["id"]))
    if "mk_submissions" in tables:
        for lead in rows(
            """SELECT s.*, f.title AS form_title FROM mk_submissions s LEFT JOIN mk_forms f ON f.id = s.form_id
            WHERE s.contact_id = ?""",
            (contact_id,),
        ):
            emails.add(str(lead["email"]).lower())
            what = (
                "Sponsorship request"
                if lead.get("source") == "media_kit"
                else f"Form: {lead.get('form_title') or 'a deleted form'}"
            )
            items.append(
                _item(
                    lead["created_at"],
                    "marketing",
                    "submission",
                    what,
                    detail=f"{lead['status']}"
                    + (
                        f" · {_money(lead['value_cents'], home)}"
                        if lead.get("value_cents")
                        else ""
                    )
                    + (
                        f" · {str(lead['message'])[:120]}"
                        if lead.get("message")
                        else ""
                    ),
                    href=f"#/marketing?open=submission:{lead['id']}",
                    status=str(lead["status"]),
                    ref=lead["id"],
                )
            )
            sources.append(("marketing", "lead", lead["id"]))
            requests.append(("marketing", "lead", lead["id"]))
    if "mk_subscribers" in tables:
        for row in rows(
            "SELECT * FROM mk_subscribers WHERE contact_id = ?", (contact_id,)
        ):
            emails.add(str(row["email"]).lower())
            said = {
                "subscribed": "Subscribed to your news",
                "pending": "Signed up for news (not confirmed yet)",
                "unsubscribed": "Unsubscribed from your news",
            }.get(str(row["status"]), "On your email list")
            items.append(
                _item(
                    row.get("unsubscribed_at")
                    if row["status"] == "unsubscribed"
                    else row["created_at"],
                    "marketing",
                    "subscriber",
                    said,
                    detail=str(row["email"]),
                    href=f"#/marketing?open=subscriber:{row['id']}",
                    status=str(row["status"]),
                    ref=row["id"],
                )
            )
    if "sv_cases" in tables:
        for case in rows("SELECT * FROM sv_cases WHERE contact_id = ?", (contact_id,)):
            emails.add(str(case["customer_email"]).lower())
            items.append(
                _item(
                    case["created_at"],
                    "service",
                    "case",
                    f"Case #{case['number']}: {case['subject']}",
                    detail=f"{case['status']} · {case['channel']}",
                    href=f"#/service?open={case['id']}",
                    status=str(case["status"]),
                    ref=case["id"],
                )
            )
            if "sv_charges" in tables:
                for charge in rows(
                    "SELECT id FROM sv_charges WHERE case_id = ?", (case["id"],)
                ):
                    sources.append(("service", "case_charge", charge["id"]))
    if "leads" in tables:
        for lead in rows("SELECT * FROM leads WHERE contact_id = ?", (contact_id,)):
            emails.add(str(lead["email"]).lower())
            items.append(
                _item(
                    lead["created_at"],
                    "leads",
                    "lead",
                    f"Saved as a lead: {lead['name'] or lead['company'] or lead['email']}",
                    detail=f"{lead['status']}"
                    + (f" · {lead['title']}" if lead.get("title") else ""),
                    href=f"#/leads?tab=leads&open={lead['id']}",
                    status=str(lead["status"]),
                    ref=lead["id"],
                )
            )
            if "lead_messages" in tables:
                for message in rows(
                    "SELECT * FROM lead_messages WHERE lead_id = ? AND status = 'sent'",
                    (lead["id"],),
                ):
                    items.append(
                        _item(
                            message["sent_at"] or message["created_at"],
                            "leads",
                            "email",
                            f"Email sent: {message['subject']}",
                            detail=str(message["to_address"]),
                            status="sent",
                            ref=message["id"],
                        )
                    )
    if "ev_orders" in tables:
        for order in rows(
            """SELECT o.*, e.title AS event_title FROM ev_orders o LEFT JOIN ev_events e ON e.id = o.event_id
            WHERE o.contact_id = ?""",
            (contact_id,),
        ):
            emails.add(str(order["buyer_email"]).lower())
            items.append(
                _item(
                    order["created_at"],
                    "events",
                    "tickets",
                    f"Tickets: {order.get('event_title') or 'an event'}",
                    detail=f"{order['reference']} · {str(order['status']).replace('_', ' ')}"
                    + (
                        f" · {_money(order['total_cents'], home)}"
                        if order["total_cents"]
                        else ""
                    ),
                    href=f"#/events?open=order:{order['id']}",
                    amount_cents=int(order["total_cents"] or 0),
                    currency=home,
                    status=str(order["status"]),
                    ref=order["id"],
                )
            )
            sources.append(("events", "order", order["id"]))
            requests.append(("events", "order", order["id"]))
    if "ac_enrolments" in tables:
        for enrolment in rows(
            """SELECT e.*, c.title AS course_title FROM ac_enrolments e LEFT JOIN ac_courses c ON c.id = e.course_id
            WHERE e.contact_id = ?""",
            (contact_id,),
        ):
            emails.add(str(enrolment["email"]).lower())
            items.append(
                _item(
                    enrolment["created_at"],
                    "academy",
                    "enrolment",
                    f"Enrolled: {enrolment.get('course_title') or 'a course'}",
                    detail=f"{enrolment['reference']} · {str(enrolment['status']).replace('_', ' ')}",
                    href=f"#/academy?open=student:{enrolment['id']}",
                    status=str(enrolment["status"]),
                    ref=enrolment["id"],
                )
            )
            sources.append(("academy", "enrolment", enrolment["id"]))
            requests.append(("academy", "enrolment", enrolment["id"]))
    if "st_deals" in tables and _has_column(connection, "st_deals", "customer_id"):
        for deal in rows("SELECT * FROM st_deals WHERE customer_id = ?", (contact_id,)):
            items.append(
                _item(
                    deal["created_at"],
                    "studio",
                    "deal",
                    f"Sales deal: {deal['title']}",
                    detail=f"{deal['stage']}"
                    + (
                        f" · {_money(deal['value_cents'], deal['currency'])}"
                        if deal["value_cents"]
                        else ""
                    ),
                    href=f"#/studio?tab=deals&deal={deal['id']}",
                    amount_cents=int(deal["value_cents"] or 0),
                    currency=str(deal["currency"]),
                    status=str(deal["stage"]),
                    ref=deal["id"],
                )
            )
            if deal["invoice_id"] and "st_payments" in tables:
                for payment in rows(
                    "SELECT id FROM st_payments WHERE invoice_id = ?",
                    (deal["invoice_id"],),
                ):
                    sources.append(("studio", "payment", payment["id"]))
                requests.append(("studio", "invoice", deal["invoice_id"]))
    items.extend(_payments(connection, sources, home))
    if "payment_requests" in tables:
        items.extend(_links(connection, requests))
    contact = kit.fetch_one(
        connection, "SELECT email FROM suite_contacts WHERE id = ?", (contact_id,)
    )
    if contact and contact["email"]:
        emails.add(str(contact["email"]).lower())
    emails.discard("")
    if emails:
        marks = ", ".join("?" for _ in emails)
        for message in rows(
            f"""SELECT * FROM suite_outbox WHERE lower(recipient) IN ({marks}) AND status = 'sent'
            ORDER BY sent_at DESC LIMIT 100""",  # noqa: S608 - placeholders only
            sorted(emails),
        ):
            items.append(
                _item(
                    message["sent_at"] or message["created_at"],
                    str(message["app"]),
                    "email",
                    f"Email sent: {message['subject']}",
                    detail=str(message["recipient"]),
                    status="sent",
                    ref=message["id"],
                )
            )
    _flag_duplicates(items)
    items.sort(key=lambda item: item["at"], reverse=True)
    return items


def _has_column(connection: sqlite3.Connection, table: str, column: str) -> bool:
    return any(
        row[1] == column for row in connection.execute(f"PRAGMA table_info({table})")
    )


def _payments(
    connection: sqlite3.Connection, sources: list[tuple[str, str, str]], home: str
) -> list[dict[str, Any]]:
    """The ledger lines for these sources (payments and their refunds)."""
    if not sources:
        return []
    kit.revenue_lines(connection, limit=1)  # the ledger's newer columns
    out = []
    by_app: dict[tuple[str, str], list[str]] = {}
    for app, source_type, source_id in sources:
        by_app.setdefault((app, source_type), []).append(source_id)
    for (app, source_type), ids in by_app.items():
        for start in range(0, len(ids), 400):
            chunk = ids[start : start + 400]
            marks = ", ".join("?" for _ in chunk)
            for line in kit.fetch_all(
                connection,
                f"""SELECT * FROM suite_revenue WHERE app = ? AND source_type = ?
                AND ((kind = 'payment' AND source_id IN ({marks})) OR (kind = 'refund' AND refund_of IN ({marks})))""",  # noqa: S608
                (app, source_type, *chunk, *chunk),
            ):
                refund = line["kind"] == "refund"
                counts = not line["voided_at"]
                code = str(line["currency"] or home)
                detail = str(line["label"] or "")
                if line.get("covered_by"):
                    detail += f" · counted in {str(line['covered_by']).split(':', 1)[0]} instead"
                elif not counts:
                    detail += " · no longer counted"
                item = _item(
                    line["at"],
                    app,
                    "refund" if refund else "payment",
                    f"{'Refunded' if refund else 'Paid'} {_money(abs(int(line['amount_cents'])), code)}",
                    detail=detail,
                    href="#/hub?tab=revenue",
                    amount_cents=int(line["amount_cents"]),
                    currency=code,
                    status="counted" if counts else "not_counted",
                    ref=line["id"],
                )
                item["counts"] = counts
                out.append(item)
    return out


def _links(
    connection: sqlite3.Connection, requests: list[tuple[str, str, str]]
) -> list[dict[str, Any]]:
    out = []
    for app, object_type, object_id in requests:
        for row in kit.fetch_all(
            connection,
            "SELECT * FROM payment_requests WHERE app = ? AND object_type = ? AND object_id = ? ORDER BY created_at",
            (app, object_type, object_id),
        ):
            out.append(
                _item(
                    row["created_at"],
                    app,
                    "payment_link",
                    f"Payment link: {row['description']}",
                    detail=f"{_money(row['amount_minor'], row['currency'])} · {str(row['status']).replace('_', ' ')}",
                    status=str(row["status"]),
                    ref=row["id"],
                )
            )
    return out


def _flag_duplicates(items: list[dict[str, Any]]) -> None:
    payments = [
        item
        for item in items
        if item["kind"] == "payment" and item.get("counts") and item["at"]
    ]
    for index, first in enumerate(payments):
        for second in payments[index + 1 :]:
            if (
                first["app"] == second["app"]
                or first["amount_cents"] != second["amount_cents"]
            ):
                continue
            if first["currency"] != second["currency"]:
                continue
            try:
                apart = abs(kit.parse_time(first["at"]) - kit.parse_time(second["at"]))
            except ValueError:
                continue
            if apart <= DUPLICATE_WINDOW:
                first["possible_duplicate"] = second["possible_duplicate"] = True


# ---------------------------------------------------------------------------
# changing
# ---------------------------------------------------------------------------


def update(
    connection: sqlite3.Connection, contact_id: str, raw: dict[str, Any]
) -> dict[str, Any]:
    """The details the business keeps on the person: name, company, tags and notes. The email and phone come from
    the apps (they are how the person is recognised); a wrong one is fixed where it was typed."""
    current = _contact(connection, contact_id)
    values: dict[str, Any] = {}
    if "name" in raw:
        values["name"] = kit.text(raw.get("name"), "the name", max_len=120)
    if "company" in raw:
        values["company"] = kit.text(raw.get("company"), "the company", max_len=120)
    if "tags" in raw:
        values["tags"] = kit.dumps(kit.tags(raw.get("tags"), limit=50))
    if "notes" in raw:
        values["notes"] = kit.text(
            raw.get("notes"), "the notes", max_len=4000, multiline=True
        )
    if values:
        values["updated_at"] = kit.now()
        kit.update_row(connection, "suite_contacts", current["id"], values)
        connection.commit()
    return _contact(connection, contact_id)


def merge(
    connection: sqlite3.Connection, keep_id: str, other_id: str, *, actor: str = ""
) -> dict[str, Any]:
    """Two records of the same person become ``keep_id``: every app row, Sales deal and lookup key of ``other_id``
    moves over, blank details are filled from it, tags and apps are joined, and ``other_id`` is gone. A "no" to
    email news on either record stays a "no"."""
    tables = _ready(connection)
    if keep_id == other_id:
        raise ValueError("Pick two different customers to merge.")
    keep = _contact(connection, keep_id)
    other = _contact(connection, other_id)
    moved = 0
    for table in kit.CONTACT_SOURCES:
        if table in tables:
            moved += int(
                connection.execute(
                    f"UPDATE {table} SET contact_id = ? WHERE contact_id = ?",  # noqa: S608 - a known table
                    (keep_id, other_id),
                ).rowcount
                or 0
            )
    if "st_deals" in tables and _has_column(connection, "st_deals", "customer_id"):
        moved += int(
            connection.execute(
                "UPDATE st_deals SET customer_id = ? WHERE customer_id = ?",
                (keep_id, other_id),
            ).rowcount
            or 0
        )
    connection.execute(
        "UPDATE suite_contact_keys SET contact_id = ? WHERE contact_id = ?",
        (keep_id, other_id),
    )
    consents = [
        value
        for value in (keep["email_consent"], other["email_consent"])
        if value is not None
    ]
    sms = [
        value
        for value in (keep["sms_consent"], other["sms_consent"])
        if value is not None
    ]
    notes = "\n\n".join(part for part in (keep["notes"], other["notes"]) if part)[:4000]
    values = {
        "name": keep["name"] or other["name"],
        "email": keep["email"] or other["email"],
        "phone": keep["phone"] or other["phone"],
        "company": keep["company"] or other["company"],
        "tags": kit.dumps(sorted(set(keep["tags"]) | set(other["tags"]))[:50]),
        "apps": kit.dumps(list(dict.fromkeys([*keep["apps"], *other["apps"]]))[:20]),
        "email_consent": None if not consents else int(all(consents)),
        "sms_consent": None if not sms else int(all(sms)),
        "consent_at": max(
            str(keep.get("consent_at") or ""), str(other.get("consent_at") or "")
        )
        or None,
        "notes": notes,
        "first_seen": min(keep["first_seen"], other["first_seen"]),
        "last_seen": max(keep["last_seen"], other["last_seen"]),
        "source_app": keep["source_app"] or other["source_app"],
        "updated_at": kit.now(),
    }
    kit.update_row(connection, "suite_contacts", keep_id, values)
    connection.execute("DELETE FROM suite_contacts WHERE id = ?", (other_id,))
    kit.log(
        connection,
        APP,
        "contact_merged",
        f"Merged {other['name'] or other['email'] or other['phone']} into {values['name'] or values['email'] or values['phone']}",
        detail=f"{moved} records moved",
        ref_type="contact",
        ref_id=keep_id,
        actor=actor,
    )
    connection.commit()
    return {"contact": _contact(connection, keep_id), "moved": moved}


# ---------------------------------------------------------------------------
# Send to Sales: a lead, a case or a customer becomes a deal in the pipeline
# ---------------------------------------------------------------------------


def _handoff_source(
    connection: sqlite3.Connection, app: str, object_type: str, object_id: str
) -> dict[str, Any]:
    """Who the deal is with and what it is about, from the thing handed over."""
    tables = _ready(connection)
    if (app, object_type) == ("marketing", "lead"):
        from aira.suite.marketing import service as marketing
        from aira.suite.marketing import store as marketing_store

        marketing_store.ensure(connection)
        lead = marketing.get_submission(connection, object_id)
        what = lead.get("package") or lead.get("form_title") or "your form"
        return {
            "name": lead.get("name") or "",
            "email": lead.get("email") or "",
            "phone": lead.get("phone") or "",
            "company": lead.get("company") or "",
            "contact_id": lead.get("contact_id") or "",
            "title": f"{what}: {lead.get('company') or lead.get('name') or lead.get('email') or 'new lead'}",
            "value_cents": int(lead.get("value_cents") or 0),
            "source": "website",
            "note": str(lead.get("message") or ""),
        }
    if (app, object_type) == ("service", "case"):
        if "sv_cases" not in tables:
            raise LookupError("That case was not found.")
        case = kit.require_row(connection, "sv_cases", object_id, "case")
        return {
            "name": case["customer_name"],
            "email": case["customer_email"],
            "phone": case["customer_phone"],
            "company": "",
            "contact_id": case.get("contact_id") or "",
            "title": f"From case #{case['number']}: {case['subject']}",
            "value_cents": 0,
            "source": "other",
            "note": str(case.get("description") or ""),
        }
    if (app, object_type) == ("suite", "contact"):
        contact = _contact(connection, object_id)
        return {
            "name": contact["name"],
            "email": contact["email"],
            "phone": contact["phone"],
            "company": contact["company"],
            "contact_id": contact["id"],
            "title": f"Deal with {contact['company'] or contact['name'] or contact['email'] or contact['phone']}",
            "value_cents": 0,
            "source": "referral",
            "note": contact["notes"],
        }
    raise ValueError(
        "Only a Marketing form lead, a Service case or a customer can be sent to Sales."
    )


def send_to_sales(
    connection: sqlite3.Connection,
    app: str,
    object_type: str,
    object_id: str,
    raw: dict[str, Any] | None = None,
    *,
    actor: str = "",
) -> dict[str, Any]:
    """The "Send to Sales" handoff: the lead, case or customer becomes an open deal in the Sales pipeline, with a
    Sales client and contact for the person (an existing client with the same email is reused). Sending it again
    returns the deal made the first time. A deal from a Marketing lead carries the lead's value, and a payment its
    invoice records stands for any payment marked on the lead: the sale counts once in revenue."""
    from aira import suite
    from aira.suite.studio import sales
    from aira.suite.studio import service as studio
    from aira.suite.studio import store as studio_store

    if (app, object_type) not in HANDOFF_SOURCES:
        raise ValueError(
            "Only a Marketing form lead, a Service case or a customer can be sent to Sales."
        )
    if not suite.app_enabled("studio"):
        raise PermissionError(
            "Sales is not part of your plan. Turn on Sales to keep a pipeline of deals."
        )
    raw = raw or {}
    studio_store.ensure(connection)
    sales.ensure(connection)
    ref = (
        f"{app}:{object_type}:{object_id}" if app != "suite" else f"contact:{object_id}"
    )
    existing = sales.handoff_of(connection, ref)
    if existing:
        return {"deal": existing, "created": False}
    source = _handoff_source(connection, app, object_type, object_id)
    contact_id = source["contact_id"] or kit.contact_for(
        connection,
        email=source["email"],
        phone=source["phone"],
        name=source["name"],
        company=source["company"],
        source=app if app != "suite" else "",
    )
    email = kit.email_key(source["email"])
    client = (
        kit.fetch_one(
            connection,
            "SELECT * FROM st_clients WHERE lower(email) = ? ORDER BY created_at LIMIT 1",
            (email,),
        )
        if email
        else None
    )
    if client is None:
        client = studio.create_client(
            connection,
            {
                "name": source["company"]
                or source["name"]
                or source["email"]
                or source["phone"]
                or "New client",
                "company": source["company"],
                "email": email,
                "phone": kit.text(source["phone"], "the phone", max_len=40),
                "status": "lead",
                "notes": f"From {HANDOFF_SOURCES[(app, object_type)]}.",
            },
        )
    studio_contact = ""
    if source["name"]:
        found = kit.fetch_one(
            connection,
            "SELECT id FROM st_contacts WHERE client_id = ? AND (lower(email) = ? OR name = ?) LIMIT 1",
            (client["id"], email or "\x00", source["name"]),
        )
        if found:
            studio_contact = str(found["id"])
        else:
            try:
                studio_contact = sales.add_contact(
                    connection,
                    client["id"],
                    {"name": source["name"], "email": email, "phone": source["phone"]},
                )["id"]
            except ValueError:
                studio_contact = ""
    value = raw.get("value_cents")
    deal = sales.create_deal(
        connection,
        {
            "title": kit.text(raw.get("title"), "the deal's name", max_len=160)
            or source["title"][:160],
            "client_id": client["id"],
            "contact_id": studio_contact,
            "value_cents": source["value_cents"] if value in (None, "") else value,
            "source": source["source"],
            "next_step": kit.text(raw.get("next_step"), "the next step", max_len=200)
            or (source["note"][:200] if source["note"] else ""),
        },
    )
    connection.execute(
        "UPDATE st_deals SET customer_id = ?, handoff_ref = ? WHERE id = ?",
        (contact_id, ref, deal["id"]),
    )
    log_app = app if app != "suite" else APP
    kit.log(
        connection,
        log_app,
        "to_sales",
        f"Sent to Sales: {source['name'] or source['email'] or source['phone'] or deal['title']}",
        detail=deal["title"],
        ref_type=object_type,
        ref_id=object_id,
        actor=actor,
    )
    connection.commit()
    return {"deal": sales.get_deal(connection, deal["id"]), "created": True}


def handoff_for(
    connection: sqlite3.Connection, app: str, object_type: str, object_id: str
) -> dict[str, Any] | None:
    """The deal a lead or case was sent to Sales as, or None (none yet, or no Sales here)."""
    if "st_deals" not in _tables(connection):
        return None
    from aira.suite.studio import sales

    ref = (
        f"{app}:{object_type}:{object_id}" if app != "suite" else f"contact:{object_id}"
    )
    deal = sales.handoff_of(connection, ref)
    if not deal:
        return None
    return {"id": deal["id"], "title": deal["title"], "stage": deal["stage"]}
