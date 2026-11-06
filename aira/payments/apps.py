"""What each app lets customers pay online, and what happens when they do (each app's own ``mark_paid``).

Every entry has ``describe`` (the amount due now, the currency, who pays, the public page they come back to) and
``on_paid`` (the app records the payment exactly as when the business marks it paid by hand, with the provider's
payment id in the note). Public pages that show an amount due get a "Pay online" button through
:func:`aira.payments.service.public_page`: a page that is the thing itself (an order's page) pays it; a page where
the customer just made something (a newsletter subscription, a deck purchase) names it by its reference, and then
the payment page is not pre-filled with the customer's details.

Ads top-ups are not here: they are the platform's own billing (``monosage_cloud/billing*.py``).
"""

from __future__ import annotations

import sqlite3
from collections.abc import Mapping
from typing import Any

from aira.payments import service as payments
from aira.suite import kit, public


def _no(reason: str) -> dict[str, Any]:
    return {"payable": False, "reason": reason}


def _due(
    *,
    amount: int,
    currency: str,
    description: str,
    name: str = "",
    email: str = "",
    phone: str = "",
    return_path: str = "",
    expires_at: str = "",
) -> dict[str, Any]:
    return {
        "payable": amount > 0,
        "reason": "" if amount > 0 else "Nothing is owed.",
        "amount_cents": int(amount),
        "currency": currency,
        "description": description,
        "customer": {"name": name, "email": email, "phone": phone},
        "return_path": return_path,
        "expires_at": expires_at,
    }


def _home_currency(connection: sqlite3.Connection, code: Any = "") -> str:
    return str(code or kit.business(connection)["currency"])


def _ref_of(link: Mapping[str, Any], payload: Mapping[str, Any]) -> str:
    return (
        kit.text(payload.get("reference"), "a reference", max_len=40)
        .upper()
        .lstrip("#")
    )


def _owner(connection: sqlite3.Connection, profile_id: str) -> dict[str, Any]:
    """The household profile (or hosted member) that owns a life app's sale."""
    from aira.suite import life

    return life.get(connection, profile_id)


# ---------------------------------------------------------------------------
# Shop: orders (the storefront sends the customer to pay at checkout)
# ---------------------------------------------------------------------------


def _shop_describe(connection: sqlite3.Connection, order_id: str) -> dict[str, Any]:
    from aira.suite.shop import service, store

    store.ensure(connection)
    order = store.get_order(connection, order_id)
    if order["payment_status"] != "unpaid":
        return _no(
            "This order is already paid."
            if order["payment_status"] == "paid"
            else "This order was refunded."
        )
    if order["fulfilment_status"] == "cancelled":
        return _no("This order was cancelled. Place a new order instead.")
    if order["expires_at"] and kit.parse_time(order["expires_at"]) <= kit.utcnow():
        return _no(
            "The time to pay for this order has passed. Place a new order instead."
        )
    link = service.order_link(connection, order)
    return _due(
        amount=int(order["total_cents"]),
        currency=str(order["currency"]),
        description=f"Order #{order['number']}",
        name=str(order["customer_name"]),
        email=str(order["customer_email"]),
        phone=str(order["customer_phone"]),
        return_path=link["path"],
        expires_at=str(order["expires_at"] or ""),
    )


def _shop_paid(
    connection: sqlite3.Connection, order_id: str, paid: dict[str, Any]
) -> Any:
    from aira.suite.shop import service

    return service.paid_online(connection, order_id, paid)


def _shop_refunded(
    connection: sqlite3.Connection, order_id: str, refund: dict[str, Any]
) -> Any:
    from aira.suite.shop import service

    return service.refunded_online(connection, order_id, refund)


payments.payable(
    "shop",
    "order",
    label="the order",
    describe=_shop_describe,
    on_paid=_shop_paid,
    on_refund=_shop_refunded,
)
payments.public_page(
    "shop", "order", "order", lambda connection, link, payload: link["ref_id"]
)


# ---------------------------------------------------------------------------
# Bookings: the deposit first (when the service has one), then what is left
# ---------------------------------------------------------------------------


def _booking_describe(
    connection: sqlite3.Connection, booking_id: str
) -> dict[str, Any]:
    from aira.suite.bookings import service, store

    store.ensure(connection)
    booking = service.get_booking(connection, booking_id)
    if booking["status"] in ("cancelled", "no_show"):
        return _no("This booking is cancelled.")
    if booking["payment_kind"] == "package":
        return _no("This booking is paid from a package.")
    price, deposit, paid = (
        int(booking["price_cents"]),
        int(booking["deposit_cents"]),
        int(booking["paid_cents"]),
    )
    first = paid == 0 and 0 < deposit < price
    amount = deposit if first else max(0, price - paid)
    if amount <= 0:
        return _no("Nothing is owed on this booking.")
    start = kit.parse_time(booking["start_at"])
    link = service.booking_link(connection, booking_id)
    return _due(
        amount=amount,
        currency=_home_currency(connection),
        description=f"{'Deposit: ' if first else ''}{booking['service_name']} · {booking['reference']}",
        name=str(booking["customer_name"]),
        email=str(booking["customer_email"]),
        phone=str(booking["customer_phone"]),
        return_path=link["path"],
        # a deposit secures the time: its link closes when the appointment starts
        expires_at=kit.iso(start) if start > kit.utcnow() else "",
    )


def _booking_paid(
    connection: sqlite3.Connection, booking_id: str, paid: dict[str, Any]
) -> Any:
    from aira.suite.bookings import service

    booking = service.get_booking(connection, booking_id)
    price, deposit, before = (
        int(booking["price_cents"]),
        int(booking["deposit_cents"]),
        int(booking["paid_cents"]),
    )
    amount = int(paid["amount_cents"])
    if before == 0 and deposit and amount == deposit and deposit < price:
        service.mark_paid(connection, booking_id, kind="deposit")
    elif before + amount >= price:
        service.mark_paid(connection, booking_id, kind="full")
    else:
        service.mark_paid(
            connection, booking_id, kind="amount", amount_cents=before + amount
        )
    return {"source_type": "booking", "source_id": booking_id}


payments.payable(
    "bookings",
    "booking",
    label="the booking",
    describe=_booking_describe,
    on_paid=_booking_paid,
)
payments.public_page(
    "bookings", "booking", "booking", lambda connection, link, payload: link["ref_id"]
)


# ---------------------------------------------------------------------------
# Academy: a course enrolment waiting for payment
# ---------------------------------------------------------------------------


def _enrolment_describe(
    connection: sqlite3.Connection, enrolment_id: str
) -> dict[str, Any]:
    from aira.suite.academy import service, store

    store.ensure(connection)
    enrolment = store.enrolment(connection, enrolment_id)
    if enrolment["status"] != "pending_payment":
        return _no("This enrolment is not waiting for payment.")
    course = store.course(connection, enrolment["course_id"])
    return _due(
        amount=int(enrolment["price_cents"]),
        currency=_home_currency(connection),
        description=f"{course['title']} · {enrolment['reference']}",
        name=str(enrolment["name"]),
        email=str(enrolment["email"]),
        return_path=service.student_link(connection, enrolment)["path"],
    )


def _enrolment_paid(
    connection: sqlite3.Connection, enrolment_id: str, paid: dict[str, Any]
) -> Any:
    from aira.suite.academy import service

    service.mark_paid(connection, enrolment_id, paid["amount_cents"])
    return {"source_type": "enrolment", "source_id": enrolment_id}


payments.payable(
    "academy",
    "enrolment",
    label="the enrolment",
    describe=_enrolment_describe,
    on_paid=_enrolment_paid,
)
payments.public_page(
    "academy", "student", "enrolment", lambda connection, link, payload: link["ref_id"]
)


def _course_enrolment(
    connection: sqlite3.Connection, link: dict[str, Any], payload: dict[str, Any]
) -> str | None:
    reference = _ref_of(link, payload)
    row = kit.fetch_one(
        connection,
        "SELECT id FROM ac_enrolments WHERE course_id = ? AND reference = ? AND status = 'pending_payment'",
        (link["ref_id"], reference),
    )
    return row["id"] if row else None


payments.public_page("academy", "course", "enrolment", _course_enrolment, prefill=False)


# ---------------------------------------------------------------------------
# Events: a ticket order waiting for payment
# ---------------------------------------------------------------------------


def _ticket_describe(connection: sqlite3.Connection, order_id: str) -> dict[str, Any]:
    from aira.suite.events import store

    store.ensure(connection)
    order = store.get_order(connection, order_id)
    if order["status"] != "pending_payment":
        return _no("This order is not waiting for payment.")
    event = store.get_event(connection, order["event_id"])
    link = public.create_link(
        connection, "events", "tickets", order_id, title=f"Tickets {order['reference']}"
    )
    return _due(
        amount=int(order["total_cents"]),
        currency=_home_currency(connection),
        description=f"{event['title']} · {order['reference']}",
        name=str(order["buyer_name"]),
        email=str(order["buyer_email"]),
        phone=str(order["buyer_phone"]),
        return_path=link["path"],
    )


def _ticket_paid(
    connection: sqlite3.Connection, order_id: str, paid: dict[str, Any]
) -> Any:
    from aira.suite.events import service

    service.mark_paid(connection, order_id, actor="Razorpay")
    return {"source_type": "order", "source_id": order_id}


payments.payable(
    "events",
    "order",
    label="the ticket order",
    describe=_ticket_describe,
    on_paid=_ticket_paid,
)
payments.public_page(
    "events", "tickets", "order", lambda connection, link, payload: link["ref_id"]
)


# ---------------------------------------------------------------------------
# Marketing: a won lead the business asks to pay (a package, a quote)
# ---------------------------------------------------------------------------


def _lead_describe(
    connection: sqlite3.Connection, submission_id: str
) -> dict[str, Any]:
    from aira.suite.marketing import service, store

    store.ensure(connection)
    lead = service.get_submission(connection, submission_id)
    if lead.get("paid_at"):
        return _no("This customer has paid already.")
    return _due(
        amount=int(lead.get("value_cents") or 0),
        currency=_home_currency(connection),
        description=str(lead.get("package") or lead.get("form_title") or "Your order"),
        name=str(lead.get("name") or ""),
        email=str(lead.get("email") or ""),
        phone=str(lead.get("phone") or ""),
    )


def _lead_paid(
    connection: sqlite3.Connection, submission_id: str, paid: dict[str, Any]
) -> Any:
    from aira.suite.marketing import service

    service.mark_paid(connection, submission_id, {"value_cents": paid["amount_cents"]})
    return {"source_type": "lead", "source_id": submission_id}


payments.payable(
    "marketing", "lead", label="the lead", describe=_lead_describe, on_paid=_lead_paid
)


# ---------------------------------------------------------------------------
# Studio (Sales): an invoice the client pays, all of what is still due
# ---------------------------------------------------------------------------


def _invoice_describe(
    connection: sqlite3.Connection, invoice_id: str
) -> dict[str, Any]:
    from aira.suite.studio import service, store

    store.ensure(connection)
    invoice = service.get_invoice(connection, invoice_id, kit.today())
    if invoice["status"] == "void":
        return _no("This invoice was voided.")
    if invoice["status"] == "draft":
        return _no("Send this invoice before asking for payment.")
    if int(invoice["balance_cents"]) <= 0:
        return _no("This invoice is paid.")
    client = (
        kit.fetch_one(
            connection,
            "SELECT name, company, email, phone FROM st_clients WHERE id = ?",
            (invoice["client_id"],),
        )
        or {}
    )
    link = public.link_for(connection, "studio", "invoice", invoice_id) or public.create_link(
        connection, "studio", "invoice", invoice_id, title=f"Invoice {invoice['number']}"
    )
    return _due(
        amount=int(invoice["balance_cents"]),
        currency=_home_currency(connection),
        description=f"Invoice {invoice['number']}",
        name=str(client.get("name") or client.get("company") or ""),
        email=str(client.get("email") or ""),
        phone=str(client.get("phone") or ""),
        return_path=link["path"],
    )


def _invoice_paid(
    connection: sqlite3.Connection, invoice_id: str, paid: dict[str, Any]
) -> Any:
    from aira.suite.studio import service

    method = str(paid.get("method") or "").lower()
    service.add_payment(
        connection,
        invoice_id,
        {
            "amount_cents": int(paid["amount_cents"]),
            "method": method if method in service.PAYMENT_METHODS else "other",
            "note": str(paid.get("note") or "")[:300],
        },
        today=kit.today(),
    )
    payment = kit.fetch_one(
        connection,
        "SELECT id FROM st_payments WHERE invoice_id = ? ORDER BY created_at DESC, rowid DESC LIMIT 1",
        (invoice_id,),
    )
    return {"source_type": "payment", "source_id": (payment or {}).get("id") or invoice_id}


payments.payable(
    "studio",
    "invoice",
    label="the invoice",
    describe=_invoice_describe,
    on_paid=_invoice_paid,
)
payments.public_page(
    "studio", "invoice", "invoice", lambda connection, link, payload: link["ref_id"]
)


# ---------------------------------------------------------------------------
# Sites: a website project the client pays for
# ---------------------------------------------------------------------------


def _site_describe(connection: sqlite3.Connection, project_id: str) -> dict[str, Any]:
    from aira.suite.sites import store

    store.ensure(connection)
    row = store.project(connection, project_id)
    if row.get("paid_at"):
        return _no("The client has paid for this website already.")
    return _due(
        amount=int(row.get("price_cents") or 0),
        currency=_home_currency(connection),
        description=f"Website: {row['name']}",
        name=str(row.get("client") or ""),
    )


def _site_paid(
    connection: sqlite3.Connection, project_id: str, paid: dict[str, Any]
) -> Any:
    from aira.suite.sites import service

    service.mark_paid(connection, project_id, True)
    return {"source_type": "site", "source_id": project_id}


payments.payable(
    "sites", "project", label="the website", describe=_site_describe, on_paid=_site_paid
)


# ---------------------------------------------------------------------------
# Learn: a deck sale (the buyer gets the access code once it is paid)
# ---------------------------------------------------------------------------


def _deck_sale_describe(connection: sqlite3.Connection, sale_id: str) -> dict[str, Any]:
    row = kit.fetch_one(connection, "SELECT * FROM ln_sales WHERE id = ?", (sale_id,))
    if not row:
        raise LookupError("That sale was not found.")
    if row["status"] != "pending":
        return _no("This purchase is not waiting for payment.")
    deck = (
        kit.fetch_one(
            connection, "SELECT title FROM ln_decks WHERE id = ?", (row["deck_id"],)
        )
        or {}
    )
    link = public.link_for(connection, "learn", "deck", row["deck_id"])
    return _due(
        amount=int(row["amount_cents"]),
        currency=_home_currency(connection, row["currency"]),
        description=f"{deck.get('title') or 'Deck'} · {row['reference']}",
        name=str(row["buyer_name"]),
        email=str(row["buyer_email"]),
        return_path=link["path"] if link else "",
    )


def _deck_sale_paid(
    connection: sqlite3.Connection, sale_id: str, paid: dict[str, Any]
) -> Any:
    from aira.suite.learn import sales

    row = kit.fetch_one(
        connection, "SELECT owner_id FROM ln_sales WHERE id = ?", (sale_id,)
    )
    sales.mark_paid(
        connection, _owner(connection, str((row or {}).get("owner_id") or "")), sale_id
    )
    return {"source_type": "deck_sale", "source_id": sale_id}


def _deck_sale_by_reference(
    connection: sqlite3.Connection, link: dict[str, Any], payload: dict[str, Any]
) -> str | None:
    row = kit.fetch_one(
        connection,
        "SELECT id FROM ln_sales WHERE deck_id = ? AND reference = ? AND status = 'pending'",
        (link["ref_id"], _ref_of(link, payload)),
    )
    return row["id"] if row else None


payments.payable(
    "learn",
    "deck_sale",
    label="the deck purchase",
    describe=_deck_sale_describe,
    on_paid=_deck_sale_paid,
)
payments.public_page(
    "learn", "deck", "deck_sale", _deck_sale_by_reference, prefill=False
)


# ---------------------------------------------------------------------------
# News: a month of a paid newsletter
# ---------------------------------------------------------------------------


def _reader_describe(
    connection: sqlite3.Connection, subscriber_id: str
) -> dict[str, Any]:
    row = kit.fetch_one(
        connection, "SELECT * FROM nw_subscribers WHERE id = ?", (subscriber_id,)
    )
    if not row:
        raise LookupError("That subscriber was not found.")
    letter = (
        kit.fetch_one(
            connection, "SELECT * FROM nw_letters WHERE id = ?", (row["letter_id"],)
        )
        or {}
    )
    if row["status"] == "cancelled" or int(letter.get("price_cents") or 0) <= 0:
        return _no("This subscription has nothing to pay.")
    link = public.link_for(
        connection, "news", "newsletter", str(letter.get("id") or "")
    )
    return _due(
        amount=int(letter["price_cents"]),
        currency=_home_currency(connection, letter.get("currency")),
        description=f"{letter.get('name') or 'Newsletter'}: one month · {row['reference']}",
        name=str(row["name"]),
        email=str(row["email"]),
        return_path=link["path"] if link else "",
    )


def _reader_paid(
    connection: sqlite3.Connection, subscriber_id: str, paid: dict[str, Any]
) -> Any:
    from aira.suite.news import letter

    row = kit.fetch_one(
        connection,
        "SELECT l.owner_id FROM nw_subscribers s JOIN nw_letters l ON l.id = s.letter_id WHERE s.id = ?",
        (subscriber_id,),
    )
    letter.mark_paid(
        connection,
        _owner(connection, str((row or {}).get("owner_id") or "")),
        subscriber_id,
    )
    payment = kit.fetch_one(
        connection,
        "SELECT id FROM nw_payments WHERE subscriber_id = ? ORDER BY paid_at DESC, rowid DESC LIMIT 1",
        (subscriber_id,),
    )
    return {
        "source_type": "newsletter_payment",
        "source_id": (payment or {}).get("id") or subscriber_id,
    }


def _reader_by_reference(
    connection: sqlite3.Connection, link: dict[str, Any], payload: dict[str, Any]
) -> str | None:
    row = kit.fetch_one(
        connection,
        "SELECT id FROM nw_subscribers WHERE letter_id = ? AND reference = ? AND status != 'cancelled'",
        (link["ref_id"], _ref_of(link, payload)),
    )
    return row["id"] if row else None


payments.payable(
    "news",
    "subscriber",
    label="the subscription",
    describe=_reader_describe,
    on_paid=_reader_paid,
)
payments.public_page(
    "news", "newsletter", "subscriber", _reader_by_reference, prefill=False
)


# ---------------------------------------------------------------------------
# Stocks: a month of an analyst's paid ideas
# ---------------------------------------------------------------------------


def _follower_describe(
    connection: sqlite3.Connection, subscriber_id: str
) -> dict[str, Any]:
    row = kit.fetch_one(
        connection, "SELECT * FROM stk_subscribers WHERE id = ?", (subscriber_id,)
    )
    if not row:
        raise LookupError("That subscriber was not found.")
    profile = (
        kit.fetch_one(
            connection,
            "SELECT * FROM stk_analysts WHERE owner_id = ?",
            (row["analyst_id"],),
        )
        or {}
    )
    if row["status"] == "cancelled" or int(profile.get("price_cents") or 0) <= 0:
        return _no("This subscription has nothing to pay.")
    from aira.suite.stocks import service

    link = public.link_for(
        connection, "stocks", service.PAGE_KIND, str(row["analyst_id"])
    )
    return _due(
        amount=int(profile["price_cents"]),
        currency=_home_currency(connection, profile.get("currency")),
        description=f"Ideas by {profile.get('display_name') or 'the analyst'}: one month · {row['reference']}",
        name=str(row["name"]),
        email=str(row["email"]),
        return_path=link["path"] if link else "",
    )


def _follower_paid(
    connection: sqlite3.Connection, subscriber_id: str, paid: dict[str, Any]
) -> Any:
    from aira.suite.stocks import service

    row = kit.fetch_one(
        connection,
        "SELECT analyst_id FROM stk_subscribers WHERE id = ?",
        (subscriber_id,),
    )
    result = service.mark_paid(
        connection,
        _owner(connection, str((row or {}).get("analyst_id") or "")),
        subscriber_id,
        {
            "months": 1,
            "amount_cents": int(paid["amount_cents"]),
            "note": paid["note"][:200],
        },
    )
    payment = kit.fetch_one(
        connection,
        "SELECT id FROM stk_payments WHERE subscriber_id = ? AND voided_at IS NULL ORDER BY created_at DESC LIMIT 1",
        (subscriber_id,),
    )
    return {
        "source_type": "subscription",
        "source_id": (payment or {}).get("id") or subscriber_id,
        "result": bool(result),
    }


def _follower_by_reference(
    connection: sqlite3.Connection, link: dict[str, Any], payload: dict[str, Any]
) -> str | None:
    row = kit.fetch_one(
        connection,
        "SELECT id FROM stk_subscribers WHERE analyst_id = ? AND reference = ? AND status != 'cancelled'",
        (link["ref_id"], _ref_of(link, payload)),
    )
    return row["id"] if row else None


payments.payable(
    "stocks",
    "subscriber",
    label="the subscription",
    describe=_follower_describe,
    on_paid=_follower_paid,
)


def _register_stocks_page() -> None:
    try:
        from aira.suite.stocks import service
    except Exception:  # noqa: BLE001 - Stocks may be left out; its page simply has no online button
        return
    payments.public_page(
        "stocks", service.PAGE_KIND, "subscriber", _follower_by_reference, prefill=False
    )


_register_stocks_page()


# ---------------------------------------------------------------------------
# Play: a guest's entry fee for a quiz night
# ---------------------------------------------------------------------------


def _entry_describe(connection: sqlite3.Connection, player_id: str) -> dict[str, Any]:
    from aira.suite.play import service

    player = kit.fetch_one(
        connection, "SELECT * FROM pl_room_players WHERE id = ?", (player_id,)
    )
    if not player:
        raise LookupError("That player is not in this game.")
    if player["profile_id"]:
        return _no("People in the household play for free.")
    extras = service.room_extras(connection, player["room_id"])
    if not extras["entry_cents"]:
        return _no("This game has no entry fee.")
    if player_id in service.paid_players(connection, player["room_id"]):
        return _no("This entry is paid already.")
    room = (
        kit.fetch_one(
            connection, "SELECT title FROM pl_rooms WHERE id = ?", (player["room_id"],)
        )
        or {}
    )
    link = public.link_for(connection, "play", "trivia_room", str(player["room_id"]))
    return _due(
        amount=int(extras["entry_cents"]),
        currency=_home_currency(connection, extras["currency"]),
        description=f"Entry: {room.get('title') or 'Quiz night'}",
        name=str(player["guest_name"]),
        return_path=link["path"] if link else "",
    )


def _entry_paid(
    connection: sqlite3.Connection, player_id: str, paid: dict[str, Any]
) -> Any:
    from aira.suite.play import service

    player = (
        kit.fetch_one(
            connection, "SELECT room_id FROM pl_room_players WHERE id = ?", (player_id,)
        )
        or {}
    )
    room = (
        kit.fetch_one(
            connection,
            "SELECT host_id FROM pl_rooms WHERE id = ?",
            (player.get("room_id") or "",),
        )
        or {}
    )
    service.mark_paid(
        connection,
        _owner(connection, str(room.get("host_id") or "")),
        str(player.get("room_id") or ""),
        player_id,
        True,
    )
    return {"source_type": "quiz_entry", "source_id": player_id}


payments.payable(
    "play",
    "entry",
    label="the entry fee",
    describe=_entry_describe,
    on_paid=_entry_paid,
)
