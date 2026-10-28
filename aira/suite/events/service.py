"""What Aira Events does: availability, orders that never oversell, promo codes, payments, check-in, the waitlist,
sponsors and the messages that go to ticket holders."""

from __future__ import annotations

import secrets
import sqlite3
from collections.abc import Mapping, Sequence
from datetime import datetime, timedelta
from typing import Any

from aira.suite import kit, public
from aira.suite.academy import partners
from aira.suite.events import store
from aira.suite.events.store import APP

CODE_ALPHABET = (
    "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"  # no 0/O, 1/I: read aloud at the door
)
MAX_TICKETS_PER_ORDER = 50


def _now(now: datetime | None) -> str:
    return kit.iso(now) if now else kit.now()


def _code(
    connection: sqlite3.Connection, table: str, column: str, size: int, prefix: str = ""
) -> str:
    for _ in range(50):
        raw = "".join(secrets.choice(CODE_ALPHABET) for _ in range(size))
        value = prefix + (f"{raw[:4]}-{raw[4:]}" if size == 8 else raw)
        if not connection.execute(
            f"SELECT 1 FROM {table} WHERE {column} = ?", (value,)
        ).fetchone():  # noqa: S608 - fixed names
            return value
    raise RuntimeError("Could not make a unique code.")


def money_field(
    merged: Mapping[str, Any],
    key: str,
    label: str,
    *,
    default: int = 0,
    minimum: int = 0,
) -> int:
    """``<key>_cents`` is whole cents (what the screens send); ``<key>`` is an amount like 12.50."""
    if merged.get(f"{key}_cents") not in (None, ""):
        return kit.integer(
            merged.get(f"{key}_cents"), label, minimum=minimum, maximum=1_000_000_000
        )
    return kit.cents(merged.get(key), label, default=default, minimum=minimum)


def normalize_code(value: Any) -> str:
    raw = "".join(ch for ch in str(value or "").upper() if ch.isalnum())
    return f"{raw[:4]}-{raw[4:8]}" if len(raw) == 8 else raw


# ---------------------------------------------------------------------------
# events
# ---------------------------------------------------------------------------


def phase(event: Mapping[str, Any], now: str | None = None) -> str:
    """The status a person sees: a published event whose end has passed is ``ended``."""
    moment = now or kit.now()
    if event["status"] == "published" and str(event["ends_at"]) <= moment:
        return "ended"
    return str(event["status"])


def clean_event(
    raw: Mapping[str, Any], current: Mapping[str, Any] | None = None
) -> dict[str, Any]:
    merged = {**(current or {}), **{k: v for k, v in raw.items() if v is not None}}
    starts = kit.moment(merged.get("starts_at"), "the start time", required=True)
    ends = kit.moment(merged.get("ends_at"), "the end time") or kit.iso(
        kit.parse_time(starts) + timedelta(hours=2)
    )
    if ends <= starts:
        raise ValueError("The event must end after it starts.")
    values = {
        "title": kit.text(
            merged.get("title"), "the event title", max_len=140, required=True
        ),
        "description": kit.text(
            merged.get("description"), "the description", max_len=8000, multiline=True
        ),
        "category": kit.choice(
            merged.get("category"), "the category", store.CATEGORIES, default="other"
        ),
        "venue_name": kit.text(merged.get("venue_name"), "the venue", max_len=140),
        "venue_address": kit.text(
            merged.get("venue_address"), "the address", max_len=300, multiline=True
        ),
        "online_url": kit.url(merged.get("online_url"), "the online link"),
        "starts_at": starts,
        "ends_at": ends,
        "cover_url": kit.url(merged.get("cover_url"), "the cover image link"),
        "capacity": kit.integer(
            merged.get("capacity"),
            "the capacity",
            default=0,
            minimum=0,
            maximum=1_000_000,
        ),
        "notes": kit.text(
            merged.get("notes"), "the notes", max_len=4000, multiline=True
        ),
    }
    if values["cover_url"] and not values["cover_url"].lower().startswith("https://"):
        raise ValueError("The cover image link must start with https://.")
    return values


def create_event(
    connection: sqlite3.Connection, raw: Mapping[str, Any], *, actor: str = ""
) -> dict[str, Any]:
    values = clean_event(raw)
    event_id = kit.new_id()
    now = kit.now()
    connection.execute(
        """INSERT INTO ev_events (id, title, description, category, venue_name, venue_address, online_url, starts_at,
        ends_at, cover_url, status, capacity, notes, created_at, updated_at)
        VALUES (:id, :title, :description, :category, :venue_name, :venue_address, :online_url, :starts_at, :ends_at,
        :cover_url, 'draft', :capacity, :notes, :now, :now)""",
        {**values, "id": event_id, "now": now},
    )
    kit.log(
        connection,
        APP,
        "event_created",
        f"Event created: {values['title']}",
        ref_type="event",
        ref_id=event_id,
        actor=actor,
    )
    connection.commit()
    return store.get_event(connection, event_id)


def update_event(
    connection: sqlite3.Connection, event_id: str, raw: Mapping[str, Any]
) -> dict[str, Any]:
    event = store.get_event(connection, event_id)
    values = clean_event(raw, event)
    sold = store.event_counts(connection, event_id)["sold"]
    if values["capacity"] and values["capacity"] < sold:
        raise ValueError(
            f"{sold} tickets are already sold; the capacity cannot be lower than that."
        )
    kit.update_row(
        connection, "ev_events", event_id, {**values, "updated_at": kit.now()}
    )
    if values["starts_at"] != event["starts_at"]:
        # Reminders that have not gone out follow the new start time.
        send_after = kit.iso(kit.parse_time(values["starts_at"]) - timedelta(hours=24))
        connection.execute(
            """UPDATE suite_outbox SET send_after = ? WHERE app = ? AND kind = 'event_reminder'
            AND status IN ('pending', 'approved') AND ref_type = 'order'
            AND ref_id IN (SELECT id FROM ev_orders WHERE event_id = ?)""",
            (send_after, APP, event_id),
        )
    connection.commit()
    return store.get_event(connection, event_id)


def set_status(
    connection: sqlite3.Connection, event_id: str, status: str, *, actor: str = ""
) -> dict[str, Any]:
    event = store.get_event(connection, event_id)
    status = kit.choice(status, "the status", store.EVENT_STATUSES)
    if status == "published":
        if not store.ticket_types(connection, event_id, active_only=True):
            raise ValueError("Add at least one ticket type before publishing.")
        if event["status"] == "cancelled":
            raise ValueError(
                "A cancelled event cannot be published again. Duplicate it instead."
            )
    values: dict[str, Any] = {"status": status, "updated_at": kit.now()}
    if status == "published" and not event["published_at"]:
        values["published_at"] = kit.now()
    kit.update_row(connection, "ev_events", event_id, values)
    kit.log(
        connection,
        APP,
        f"event_{status}",
        f"{event['title']} is {status}",
        ref_type="event",
        ref_id=event_id,
        actor=actor,
    )
    connection.commit()
    return store.get_event(connection, event_id)


def cancel_event(
    connection: sqlite3.Connection,
    event_id: str,
    *,
    message: str = "",
    via: Any = None,
    actor: str = "",
) -> dict[str, Any]:
    """Cancel the event and tell every ticket holder. Orders stay for the organiser to refund."""
    event = store.get_event(connection, event_id)
    if event["status"] == "cancelled":
        return {"event": event, "notified": 0}
    kit.update_row(
        connection,
        "ev_events",
        event_id,
        {"status": "cancelled", "updated_at": kit.now()},
    )
    _cancel_reminders(
        connection, [o["id"] for o in _holding_orders(connection, event_id)]
    )
    body = message.strip() or (
        f"We are sorry: {event['title']} on {_when(event)} has been cancelled. "
        "We will be in touch about your refund."
    )
    notified = announce(
        connection,
        event_id,
        subject=f"Cancelled: {event['title']}",
        body=body,
        kind="event_update",
        via=via,
    )
    kit.log(
        connection,
        APP,
        "event_cancelled",
        f"{event['title']} cancelled",
        detail=f"{notified} ticket holders told",
        ref_type="event",
        ref_id=event_id,
        actor=actor,
    )
    connection.commit()
    return {"event": store.get_event(connection, event_id), "notified": notified}


def duplicate_event(
    connection: sqlite3.Connection, event_id: str, *, days_later: int = 7
) -> dict[str, Any]:
    event = store.get_event(connection, event_id)
    shift = timedelta(
        days=kit.integer(
            days_later, "the days later", default=7, minimum=0, maximum=3650
        )
    )
    copy = create_event(
        connection,
        {
            **event,
            "title": f"{event['title']} (copy)"[:140],
            "starts_at": kit.iso(kit.parse_time(event["starts_at"]) + shift),
            "ends_at": kit.iso(kit.parse_time(event["ends_at"]) + shift),
        },
    )
    for position, ticket in enumerate(store.ticket_types(connection, event_id)):
        add_ticket_type(
            connection,
            copy["id"],
            {
                "name": ticket["name"],
                "description": ticket["description"],
                "price_cents": ticket["price_cents"],
                "quantity": ticket["quantity"],
                "max_per_order": ticket["max_per_order"],
                "position": position,
            },
        )
    return copy


def delete_event(connection: sqlite3.Connection, event_id: str) -> None:
    event = store.get_event(connection, event_id)
    if kit.count(
        connection, "SELECT COUNT(*) FROM ev_orders WHERE event_id = ?", (event_id,)
    ):
        raise ValueError("This event has orders. Cancel it instead of deleting it.")
    public.revoke(connection, APP, "event", event_id)
    for table in ("ev_ticket_types", "ev_waitlist"):
        connection.execute(f"DELETE FROM {table} WHERE event_id = ?", (event_id,))  # noqa: S608 - fixed names
    connection.execute("DELETE FROM ev_events WHERE id = ?", (event_id,))
    kit.log(
        connection,
        APP,
        "event_deleted",
        f"Event deleted: {event['title']}",
        ref_type="event",
        ref_id=event_id,
    )
    connection.commit()


def draft_description(
    event: Mapping[str, Any], business_name: str = ""
) -> dict[str, Any]:
    where = event.get("venue_name") or ("Online" if event.get("online_url") else "")
    template = {
        "description": (
            f"Join us for {event['title']}"
            + (f" at {where}" if where else "")
            + f" on {_when(event)}.\n\n"
            + "What to expect: an unforgettable "
            + str(event.get("category") or "event")
            + " with great people, great energy and memories to share.\n\n"
            + "Tickets are limited. Book now to secure your spot, and bring your friends."
            + (f"\n\nHosted by {business_name}." if business_name else "")
        ),
        "tagline": f"{event['title']}: {_when(event)}",
    }
    prompt = (
        "Write a short, exciting event description for a public ticket page and a one-line tagline. "
        "Answer as JSON with keys description (2 to 4 short paragraphs, plain text) and tagline (under 90 characters). "
        "Do not invent performers, prices or facts not given.\n"
        f"Title: {event['title']}\nCategory: {event.get('category')}\nVenue: {where}\nWhen: {_when(event)}\n"
        f"Organiser: {business_name}\nNotes from the organiser: {str(event.get('description') or '')[:1500]}"
    )
    return kit.draft(prompt, template)


def _when(event: Mapping[str, Any]) -> str:
    try:
        return kit.parse_time(str(event["starts_at"])).strftime(
            "%a %d %b %Y, %H:%M UTC"
        )
    except (KeyError, ValueError):
        return str(event.get("starts_at") or "")


# ---------------------------------------------------------------------------
# ticket types and availability
# ---------------------------------------------------------------------------


def clean_ticket_type(
    raw: Mapping[str, Any], current: Mapping[str, Any] | None = None
) -> dict[str, Any]:
    merged = {**(current or {}), **{k: v for k, v in raw.items() if v is not None}}
    values = {
        "name": kit.text(
            merged.get("name"), "the ticket name", max_len=80, required=True
        ),
        "description": kit.text(
            merged.get("description"),
            "the ticket description",
            max_len=500,
            multiline=True,
        ),
        "price_cents": money_field(merged, "price", "the price"),
        "quantity": kit.integer(
            merged.get("quantity"), "the quantity", minimum=1, maximum=1_000_000
        ),
        "sales_start": kit.moment(merged.get("sales_start"), "the sales start"),
        "sales_end": kit.moment(merged.get("sales_end"), "the sales end"),
        "max_per_order": kit.integer(
            merged.get("max_per_order"),
            "the limit per order",
            default=10,
            minimum=1,
            maximum=MAX_TICKETS_PER_ORDER,
        ),
        "position": kit.integer(
            merged.get("position"), "the position", default=0, minimum=0, maximum=1000
        ),
        "active": 1 if kit.flag(merged.get("active", True)) else 0,
    }
    if (
        values["sales_start"]
        and values["sales_end"]
        and values["sales_end"] <= values["sales_start"]
    ):
        raise ValueError("Sales must end after they start.")
    return values


def add_ticket_type(
    connection: sqlite3.Connection, event_id: str, raw: Mapping[str, Any]
) -> dict[str, Any]:
    store.get_event(connection, event_id)
    values = clean_ticket_type(raw)
    type_id = kit.new_id()
    now = kit.now()
    connection.execute(
        """INSERT INTO ev_ticket_types (id, event_id, name, description, price_cents, quantity, sales_start, sales_end,
        max_per_order, position, active, created_at, updated_at)
        VALUES (:id, :event_id, :name, :description, :price_cents, :quantity, :sales_start, :sales_end, :max_per_order,
        :position, :active, :now, :now)""",
        {**values, "id": type_id, "event_id": event_id, "now": now},
    )
    connection.commit()
    return store.get_ticket_type(connection, type_id)


def update_ticket_type(
    connection: sqlite3.Connection, type_id: str, raw: Mapping[str, Any]
) -> dict[str, Any]:
    ticket = store.get_ticket_type(connection, type_id)
    values = clean_ticket_type(raw, ticket)
    sold = store.sold_by_type(connection, ticket["event_id"]).get(type_id, 0)
    if values["quantity"] < sold:
        raise ValueError(
            f"{sold} of these are sold; the quantity cannot be lower than that."
        )
    kit.update_row(
        connection, "ev_ticket_types", type_id, {**values, "updated_at": kit.now()}
    )
    connection.commit()
    return store.get_ticket_type(connection, type_id)


def delete_ticket_type(connection: sqlite3.Connection, type_id: str) -> dict[str, Any]:
    """Delete an unsold ticket type; one that has sales is switched off instead (its tickets stay valid)."""
    ticket = store.get_ticket_type(connection, type_id)
    if kit.count(
        connection,
        "SELECT COUNT(*) FROM ev_tickets WHERE ticket_type_id = ?",
        (type_id,),
    ):
        kit.update_row(
            connection,
            "ev_ticket_types",
            type_id,
            {"active": 0, "updated_at": kit.now()},
        )
        connection.commit()
        return {
            "deleted": False,
            "deactivated": True,
            "ticket_type": store.get_ticket_type(connection, type_id),
        }
    connection.execute("DELETE FROM ev_ticket_types WHERE id = ?", (type_id,))
    connection.commit()
    return {"deleted": True, "deactivated": False, "id": ticket["id"]}


def availability(
    connection: sqlite3.Connection, event: Mapping[str, Any], now: str | None = None
) -> dict[str, Any]:
    """Seats left per ticket type and overall, and whether each type is on sale right now."""
    moment = now or kit.now()
    sold = store.sold_by_type(connection, event["id"])
    total_sold = sum(sold.values())
    capacity = int(event["capacity"] or 0)
    overall_left = max(0, capacity - total_sold) if capacity else None
    current = phase(event, moment)
    types = []
    for ticket in store.ticket_types(connection, event["id"], active_only=True):
        taken = sold.get(ticket["id"], 0)
        left = max(0, int(ticket["quantity"]) - taken)
        if overall_left is not None:
            left = min(left, overall_left)
        reason = ""
        if current != "published":
            reason = {
                "draft": "Not on sale yet",
                "cancelled": "Event cancelled",
                "ended": "Event ended",
            }.get(current, "Not on sale")
        elif ticket["sales_start"] and moment < ticket["sales_start"]:
            reason = "Sales have not started"
        elif ticket["sales_end"] and moment >= ticket["sales_end"]:
            reason = "Sales have ended"
        elif str(event["starts_at"]) <= moment and str(event["ends_at"]) <= moment:
            reason = "Event ended"
        elif left <= 0:
            reason = "Sold out"
        types.append(
            {
                **ticket,
                "sold": taken,
                "remaining": left,
                "on_sale": not reason,
                "reason": reason,
                "max_per_order": min(int(ticket["max_per_order"]), max(left, 0))
                if not reason
                else int(ticket["max_per_order"]),
            }
        )
    on_sale = any(t["on_sale"] for t in types)
    sold_out = (
        current == "published"
        and bool(types)
        and all(t["remaining"] <= 0 for t in types)
    )
    return {
        "types": types,
        "sold": total_sold,
        "capacity": capacity,
        "overall_remaining": overall_left,
        "on_sale": on_sale,
        "sold_out": sold_out,
        "phase": current,
    }


# ---------------------------------------------------------------------------
# promo codes
# ---------------------------------------------------------------------------


def clean_promo(
    raw: Mapping[str, Any], current: Mapping[str, Any] | None = None
) -> dict[str, Any]:
    merged = {**(current or {}), **{k: v for k, v in raw.items() if v is not None}}
    kind = kit.choice(
        merged.get("kind"),
        "the discount type",
        ("percent", "amount"),
        default="percent",
    )
    code = "".join(
        ch
        for ch in kit.text(
            merged.get("code"), "the code", max_len=30, required=True
        ).upper()
        if ch.isalnum() or ch in "-_"
    )
    if len(code) < 3:
        raise ValueError("The code needs at least 3 letters or digits.")
    if kind == "percent":
        value = kit.integer(
            merged.get("value"), "the percent off", minimum=1, maximum=100
        )
    else:
        value = (
            kit.integer(
                merged.get("value"),
                "the amount off (in cents)",
                minimum=1,
                maximum=1_000_000_000,
            )
            if merged.get("amount") in (None, "")
            else kit.cents(merged.get("amount"), "the amount off", minimum=1)
        )
    return {
        "code": code,
        "kind": kind,
        "value": value,
        "event_id": kit.text(merged.get("event_id"), "the event", max_len=32),
        "ticket_type_id": kit.text(
            merged.get("ticket_type_id"), "the ticket type", max_len=32
        ),
        "max_uses": kit.integer(
            merged.get("max_uses"),
            "the use limit",
            default=0,
            minimum=0,
            maximum=1_000_000,
        ),
        "expires_at": kit.moment(merged.get("expires_at"), "the expiry"),
        "active": 1 if kit.flag(merged.get("active", True)) else 0,
    }


def create_promo(
    connection: sqlite3.Connection, raw: Mapping[str, Any]
) -> dict[str, Any]:
    values = clean_promo(raw)
    if values["event_id"]:
        store.get_event(connection, values["event_id"])
    if values["ticket_type_id"]:
        ticket = store.get_ticket_type(connection, values["ticket_type_id"])
        values["event_id"] = ticket["event_id"]
    if connection.execute(
        "SELECT 1 FROM ev_promos WHERE code = ?", (values["code"],)
    ).fetchone():
        raise ValueError(f"The code {values['code']} already exists.")
    promo_id = kit.new_id()
    connection.execute(
        """INSERT INTO ev_promos (id, code, event_id, ticket_type_id, kind, value, max_uses, used, expires_at, active, created_at)
        VALUES (:id, :code, :event_id, :ticket_type_id, :kind, :value, :max_uses, 0, :expires_at, :active, :now)""",
        {**values, "id": promo_id, "now": kit.now()},
    )
    connection.commit()
    return kit.require_row(connection, "ev_promos", promo_id, "promo code")


def update_promo(
    connection: sqlite3.Connection, promo_id: str, raw: Mapping[str, Any]
) -> dict[str, Any]:
    promo = kit.require_row(connection, "ev_promos", promo_id, "promo code")
    values = clean_promo(raw, promo)
    clash = connection.execute(
        "SELECT id FROM ev_promos WHERE code = ? AND id != ?",
        (values["code"], promo_id),
    ).fetchone()
    if clash:
        raise ValueError(f"The code {values['code']} already exists.")
    kit.update_row(connection, "ev_promos", promo_id, values)
    connection.commit()
    return kit.require_row(connection, "ev_promos", promo_id, "promo code")


def find_promo(
    connection: sqlite3.Connection, code: str, event_id: str, now: str | None = None
) -> dict[str, Any]:
    """The usable promo for this event, or ValueError saying why not."""
    clean = "".join(ch for ch in str(code or "").upper() if ch.isalnum() or ch in "-_")
    promo = kit.fetch_one(
        connection, "SELECT * FROM ev_promos WHERE code = ?", (clean,)
    )
    if not promo or not promo["active"]:
        raise ValueError("That promo code is not valid.")
    if promo["event_id"] and promo["event_id"] != event_id:
        raise ValueError("That promo code is not valid for this event.")
    if promo["expires_at"] and (now or kit.now()) >= promo["expires_at"]:
        raise ValueError("That promo code has expired.")
    if promo["max_uses"] and promo["used"] >= promo["max_uses"]:
        raise ValueError("That promo code has been used up.")
    return promo


def discount_for(promo: Mapping[str, Any], lines: Sequence[Mapping[str, Any]]) -> int:
    eligible = sum(
        int(line["price_cents"]) * int(line["quantity"])
        for line in lines
        if not promo["ticket_type_id"]
        or line["ticket_type_id"] == promo["ticket_type_id"]
    )
    if eligible <= 0:
        return 0
    if promo["kind"] == "percent":
        return min(eligible, kit.percent_of(eligible, int(promo["value"]) * 100))
    return min(eligible, int(promo["value"]))


# ---------------------------------------------------------------------------
# orders
# ---------------------------------------------------------------------------


def _items(raw: Any) -> list[tuple[str, int]]:
    if not isinstance(raw, list) or not raw:
        raise ValueError("Choose at least one ticket.")
    totals: dict[str, int] = {}
    for item in raw[:20]:
        if not isinstance(item, Mapping):
            raise ValueError("Choose tickets from the list.")
        type_id = kit.text(
            item.get("ticket_type_id"), "the ticket type", max_len=32, required=True
        )
        quantity = kit.integer(
            item.get("quantity"),
            "the number of tickets",
            default=0,
            minimum=0,
            maximum=MAX_TICKETS_PER_ORDER,
        )
        if quantity:
            totals[type_id] = totals.get(type_id, 0) + quantity
    if not totals:
        raise ValueError("Choose at least one ticket.")
    if sum(totals.values()) > MAX_TICKETS_PER_ORDER:
        raise ValueError(f"An order can have at most {MAX_TICKETS_PER_ORDER} tickets.")
    return list(totals.items())


def quote(
    connection: sqlite3.Connection,
    event: Mapping[str, Any],
    items: Any,
    promo_code: str = "",
    *,
    now: str | None = None,
    staff: bool = False,
) -> dict[str, Any]:
    """Price an order and check it can be had right now (``staff`` may sell outside the sales window)."""
    moment = now or kit.now()
    wanted = _items(items)
    state = availability(connection, event, moment)
    by_id = {t["id"]: t for t in state["types"]}
    lines = []
    for type_id, quantity in wanted:
        ticket = by_id.get(type_id)
        if ticket is None:
            raise ValueError("That ticket type is not available.")
        if not ticket["on_sale"] and not (
            staff
            and ticket["reason"]
            in ("Sales have not started", "Sales have ended", "Not on sale yet")
        ):
            raise ValueError(f"{ticket['name']}: {ticket['reason'] or 'not on sale'}.")
        if quantity > int(ticket["remaining"]):
            left = int(ticket["remaining"])
            raise ValueError(
                f"Only {left} {ticket['name']} ticket{'s' if left != 1 else ''} left."
                if left
                else f"{ticket['name']} is sold out."
            )
        if not staff and quantity > int(
            store.get_ticket_type(connection, type_id)["max_per_order"]
        ):
            raise ValueError(
                f"{ticket['name']}: at most {ticket['max_per_order']} per order."
            )
        lines.append(
            {
                "ticket_type_id": type_id,
                "name": ticket["name"],
                "quantity": quantity,
                "price_cents": int(ticket["price_cents"]),
            }
        )
    if (
        state["overall_remaining"] is not None
        and sum(q for _, q in wanted) > state["overall_remaining"]
    ):
        raise ValueError(
            f"Only {state['overall_remaining']} seats left for this event."
        )
    subtotal = sum(line["price_cents"] * line["quantity"] for line in lines)
    promo = None
    discount = 0
    if promo_code:
        promo = find_promo(connection, promo_code, event["id"], moment)
        discount = discount_for(promo, lines)
        if discount <= 0:
            raise ValueError("That promo code does not apply to these tickets.")
    return {
        "lines": lines,
        "subtotal_cents": subtotal,
        "discount_cents": discount,
        "total_cents": max(0, subtotal - discount),
        "promo": {"id": promo["id"], "code": promo["code"]} if promo else None,
        "tickets": sum(line["quantity"] for line in lines),
    }


def place_order(
    connection: sqlite3.Connection,
    event_id: str,
    raw: Mapping[str, Any],
    *,
    source: str = "public",
    comp: bool = False,
    via: Any = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Create an order with one ticket per seat. Availability is re-checked inside one write transaction, so two
    buyers can never take the same last seat. Free and complimentary orders are confirmed at once; paid orders wait
    for payment."""
    moment = _now(now)
    buyer_name = kit.text(raw.get("name"), "your name", max_len=120, required=True)
    buyer_email = kit.email(raw.get("email"), "your email", required=source == "public")
    buyer_phone = kit.phone(raw.get("phone"))
    given = raw.get("holders")
    holders = [
        kit.text(name, "a ticket holder's name", max_len=120)
        for name in (given if isinstance(given, list) else [])[:MAX_TICKETS_PER_ORDER]
    ]
    notes = (
        kit.text(raw.get("notes"), "the notes", max_len=1000, multiline=True)
        if source != "public"
        else ""
    )
    staff = source != "public"
    if staff and not comp:
        partners.check_code(connection, raw.get("ref"), buyer_email)
    connection.commit()
    connection.execute("BEGIN IMMEDIATE")
    try:
        event = store.get_event(connection, event_id)
        priced = quote(
            connection,
            event,
            raw.get("items"),
            str(raw.get("promo_code") or "").strip(),
            now=moment,
            staff=staff,
        )
        if comp:
            priced["discount_cents"] = priced["subtotal_cents"]
            priced["total_cents"] = 0
        free = priced["total_cents"] == 0
        order_id = kit.new_id()
        reference = _code(connection, "ev_orders", "reference", 6, prefix="EV-")
        connection.execute(
            """INSERT INTO ev_orders (id, event_id, reference, buyer_name, buyer_email, buyer_phone, status, source,
            subtotal_cents, discount_cents, total_cents, promo_id, promo_code, notes, created_at, paid_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, NULL)""",
            (
                order_id,
                event_id,
                reference,
                buyer_name,
                buyer_email,
                buyer_phone,
                "confirmed" if free else "pending_payment",
                "comp" if comp else source,
                priced["subtotal_cents"],
                priced["discount_cents"],
                priced["total_cents"],
                (priced["promo"] or {}).get("id", ""),
                (priced["promo"] or {}).get("code", ""),
                notes,
                moment,
            ),
        )
        seat = 0
        for line in priced["lines"]:
            for _ in range(line["quantity"]):
                connection.execute(
                    """INSERT INTO ev_tickets (id, order_id, event_id, ticket_type_id, holder_name, code, price_cents, status, created_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, 'valid', ?)""",
                    (
                        kit.new_id(),
                        order_id,
                        event_id,
                        line["ticket_type_id"],
                        holders[seat]
                        if seat < len(holders) and holders[seat]
                        else buyer_name,
                        _code(connection, "ev_tickets", "code", 8),
                        0 if comp else line["price_cents"],
                        moment,
                    ),
                )
                seat += 1
        if priced["promo"]:
            connection.execute(
                "UPDATE ev_promos SET used = used + 1 WHERE id = ?",
                (priced["promo"]["id"],),
            )
        connection.commit()
    except BaseException:
        connection.rollback()
        raise
    # Outside the seat transaction: the buyer's tickets page, messages and the activity line.
    link = public.create_link(
        connection, APP, "tickets", order_id, title=f"Tickets {reference}"
    )
    order = store.get_order(connection, order_id)
    word = "ticket" if priced["tickets"] == 1 else "tickets"
    kit.log(
        connection,
        APP,
        "order",
        f"{buyer_name} {'booked' if free else 'ordered'} {priced['tickets']} {word} for {event['title']}",
        detail="complimentary" if comp else ("" if free else "waiting for payment"),
        ref_type="order",
        ref_id=order_id,
        amount_cents=priced["total_cents"],
        public=source == "public",
    )
    if not comp:
        partners.record(
            connection,
            raw.get("ref"),
            source_type="order",
            source_id=order_id,
            item_type="event",
            item_id=event_id,
            label=event["title"],
            customer=buyer_name,
            customer_email=buyer_email,
            amount_cents=priced["total_cents"],
        )
    if free:
        _confirmation_messages(connection, event, order, link["path"], via, moment)
    else:
        _payment_request(connection, event, order, link["path"], via)
    connection.commit()
    return {"order": order, "tickets_link": link}


def _holding_orders(
    connection: sqlite3.Connection, event_id: str
) -> list[dict[str, Any]]:
    return kit.fetch_all(
        connection,
        f"SELECT * FROM ev_orders WHERE event_id = ? AND status IN ({','.join('?' * len(store.HOLDING))})",
        (event_id, *store.HOLDING),
    )


def _confirmation_messages(
    connection: sqlite3.Connection,
    event: Mapping[str, Any],
    order: Mapping[str, Any],
    tickets_path: str,
    via: Any,
    moment: str,
) -> None:
    if not order["buyer_email"]:
        return
    tickets = store.tickets_of_order(connection, order["id"])
    codes = "\n".join(
        f"  {t['code']}  {t['type_name'] or 'Ticket'}  {t['holder_name']}"
        for t in tickets
    )
    where = event["venue_name"] or (
        "Online: " + event["online_url"] if event["online_url"] else ""
    )
    kit.queue_email(
        connection,
        APP,
        "ticket",
        to=order["buyer_email"],
        subject=f"Your tickets for {event['title']}",
        body=(
            f"Hi {order['buyer_name']},\n\nYou're going to {event['title']} on {_when(event)}"
            + (f" at {where}" if where else "")
            + f".\n\nOrder {order['reference']}. Your check-in codes:\n{codes}\n\n"
            + f"Your tickets: {kit.public_url(tickets_path, via)}\n\nShow a code at the door. See you there!"
        ),
        ref_type="order",
        ref_id=order["id"],
    )
    send_after = kit.iso(kit.parse_time(event["starts_at"]) - timedelta(hours=24))
    if send_after > moment:
        kit.queue_email(
            connection,
            APP,
            "event_reminder",
            to=order["buyer_email"],
            subject=f"Tomorrow: {event['title']}",
            body=(
                f"Hi {order['buyer_name']},\n\nA reminder that {event['title']} is on {_when(event)}"
                + (f" at {where}" if where else "")
                + f".\n\nYour tickets: {kit.public_url(tickets_path, via)}\n\nSee you there!"
            ),
            ref_type="order",
            ref_id=order["id"],
            send_after=send_after,
        )


def _payment_request(
    connection: sqlite3.Connection,
    event: Mapping[str, Any],
    order: Mapping[str, Any],
    tickets_path: str,
    via: Any,
) -> None:
    if not order["buyer_email"]:
        return
    profile = kit.business(connection)
    how = []
    if profile["payment_link"]:
        how.append(f"Pay here: {profile['payment_link']}")
    if profile["payment_instructions"]:
        how.append(profile["payment_instructions"])
    kit.queue_email(
        connection,
        APP,
        "payment_request",
        to=order["buyer_email"],
        subject=f"Complete your order for {event['title']}",
        body=(
            f"Hi {order['buyer_name']},\n\nThanks for your order {order['reference']} for {event['title']}. "
            f"The total is {kit.fmt_money(order['total_cents'], profile['currency'])}.\n\n"
            + ("\n".join(how) + "\n\n" if how else "")
            + f"Use the reference {order['reference']} with your payment. Your tickets are confirmed once we receive it.\n\n"
            + f"Your order: {kit.public_url(tickets_path, via)}"
        ),
        ref_type="order",
        ref_id=order["id"],
    )


def _cancel_reminders(connection: sqlite3.Connection, order_ids: Sequence[str]) -> None:
    if not order_ids:
        return
    ids = [
        row[0]
        for row in connection.execute(
            f"""SELECT id FROM suite_outbox WHERE app = ? AND kind = 'event_reminder' AND ref_type = 'order'
            AND status IN ('pending', 'approved') AND ref_id IN ({",".join("?" * len(order_ids))})""",
            (APP, *order_ids),
        ).fetchall()
    ]
    if ids:
        kit.set_outbox_status(connection, ids, "cancelled")


def mark_paid(
    connection: sqlite3.Connection, order_id: str, *, via: Any = None, actor: str = ""
) -> dict[str, Any]:
    """The organiser received the money: confirm the order, record the revenue (once) and send the tickets."""
    order = store.get_order(connection, order_id)
    if order["status"] in ("cancelled", "refunded"):
        raise ValueError("This order was cancelled. Create a new order instead.")
    if int(order["total_cents"]) <= 0:
        raise ValueError("This order is free; there is nothing to mark paid.")
    event = store.get_event(connection, order["event_id"])
    already = bool(order["paid_at"])
    now = kit.now()
    kit.update_row(
        connection,
        "ev_orders",
        order_id,
        {"status": "confirmed", "paid_at": order["paid_at"] or now},
    )
    if int(order["total_cents"]) > 0:
        kit.record_revenue(
            connection,
            APP,
            source_type="order",
            source_id=order_id,
            amount_cents=int(order["total_cents"]),
            label=f"{event['title']} ({order['reference']})",
            customer=order["buyer_name"],
        )
        partners.earn(connection, "order", order_id, int(order["total_cents"]))
    if not already:
        kit.log(
            connection,
            APP,
            "order_paid",
            f"{order['buyer_name']} paid for {event['title']}",
            ref_type="order",
            ref_id=order_id,
            amount_cents=int(order["total_cents"]),
            actor=actor,
        )
        if order["status"] == "pending_payment":
            link = public.create_link(
                connection,
                APP,
                "tickets",
                order_id,
                title=f"Tickets {order['reference']}",
            )
            _confirmation_messages(
                connection,
                event,
                store.get_order(connection, order_id),
                link["path"],
                via,
                now,
            )
    connection.commit()
    return store.get_order(connection, order_id)


def cancel_order(
    connection: sqlite3.Connection,
    order_id: str,
    *,
    refund: bool = False,
    actor: str = "",
) -> dict[str, Any]:
    """Cancel (or refund) an order: its seats are free again, its revenue stops counting, reminders are dropped."""
    order = store.get_order(connection, order_id)
    if order["status"] in ("cancelled", "refunded"):
        return order
    if refund and not order["paid_at"]:
        raise ValueError("This order was not paid. Cancel it instead of refunding it.")
    now = kit.now()
    status = "refunded" if refund else "cancelled"
    kit.update_row(
        connection,
        "ev_orders",
        order_id,
        {
            "status": status,
            "cancelled_at": now,
            **({"refunded_at": now} if refund else {}),
        },
    )
    connection.execute(
        "UPDATE ev_tickets SET status = 'cancelled' WHERE order_id = ? AND status = 'valid'",
        (order_id,),
    )
    if order["promo_id"]:
        connection.execute(
            "UPDATE ev_promos SET used = MAX(0, used - 1) WHERE id = ?",
            (order["promo_id"],),
        )
    kit.void_revenue(connection, APP, "order", order_id)
    partners.void(connection, "order", order_id)
    _cancel_reminders(connection, [order_id])
    event = store.get_event(connection, order["event_id"])
    kit.log(
        connection,
        APP,
        f"order_{status}",
        f"Order {order['reference']} {status} ({order['buyer_name']}, {event['title']})",
        ref_type="order",
        ref_id=order_id,
        amount_cents=-int(order["total_cents"]) if refund else 0,
        actor=actor,
    )
    connection.commit()
    return store.get_order(connection, order_id)


def claim_paid(connection: sqlite3.Connection, order_id: str) -> dict[str, Any]:
    """The buyer says they have paid: the organiser sees it and confirms after checking."""
    order = store.get_order(connection, order_id)
    if order["status"] != "pending_payment":
        return order
    if not order["payment_claimed_at"]:
        kit.update_row(
            connection, "ev_orders", order_id, {"payment_claimed_at": kit.now()}
        )
        kit.log(
            connection,
            APP,
            "payment_claimed",
            f"{order['buyer_name']} says they paid order {order['reference']}",
            ref_type="order",
            ref_id=order_id,
            amount_cents=int(order["total_cents"]),
            public=True,
        )
        connection.commit()
    return store.get_order(connection, order_id)


def resend_tickets(
    connection: sqlite3.Connection, order_id: str, *, via: Any = None
) -> dict[str, Any] | None:
    order = store.get_order(connection, order_id)
    event = store.get_event(connection, order["event_id"])
    if not order["buyer_email"]:
        raise ValueError("This order has no email address.")
    link = public.create_link(
        connection, APP, "tickets", order_id, title=f"Tickets {order['reference']}"
    )
    if order["status"] == "pending_payment":
        _payment_request(connection, event, order, link["path"], via)
    elif order["status"] == "confirmed":
        tickets = store.tickets_of_order(connection, order_id)
        codes = "\n".join(
            f"  {t['code']}  {t['type_name'] or 'Ticket'}  {t['holder_name']}"
            for t in tickets
            if t["status"] != "cancelled"
        )
        kit.queue_email(
            connection,
            APP,
            "ticket",
            to=order["buyer_email"],
            subject=f"Your tickets for {event['title']}",
            body=f"Hi {order['buyer_name']},\n\nYour check-in codes for {event['title']} on {_when(event)}:\n{codes}\n\nYour tickets: {kit.public_url(link['path'], via)}",
            ref_type="order",
            ref_id=order_id,
        )
    else:
        raise ValueError("This order is cancelled.")
    connection.commit()
    return link


def list_orders(
    connection: sqlite3.Connection,
    *,
    event_id: str = "",
    status: str = "",
    q: str = "",
    limit: int = 500,
) -> list[dict[str, Any]]:
    clauses, params = [], []
    if event_id:
        clauses.append("o.event_id = ?")
        params.append(event_id)
    if status:
        clauses.append("o.status = ?")
        params.append(kit.choice(status, "the status", store.ORDER_STATUSES))
    if q:
        like = f"%{kit.text(q, 'the search', max_len=100)}%"
        clauses.append(
            "(o.buyer_name LIKE ? OR o.buyer_email LIKE ? OR o.reference LIKE ?)"
        )
        params += [like, like, like]
    where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    return kit.fetch_all(
        connection,
        f"""SELECT o.*, e.title AS event_title, e.starts_at AS event_starts_at,
        (SELECT COUNT(*) FROM ev_tickets t WHERE t.order_id = o.id AND t.status != 'cancelled') AS tickets
        FROM ev_orders o JOIN ev_events e ON e.id = o.event_id {where} ORDER BY o.created_at DESC LIMIT ?""",  # noqa: S608 - clauses are fixed
        (*params, max(1, min(limit, 5000))),
    )


# ---------------------------------------------------------------------------
# check-in
# ---------------------------------------------------------------------------


def find_attendees(
    connection: sqlite3.Connection, event_id: str, q: str = "", limit: int = 200
) -> list[dict[str, Any]]:
    params: list[Any] = [event_id, *store.HOLDING]
    extra = ""
    if q:
        text = kit.text(q, "the search", max_len=100)
        like = f"%{text}%"
        extra = " AND (t.code = ? OR t.holder_name LIKE ? OR o.buyer_name LIKE ? OR o.buyer_email LIKE ? OR o.reference LIKE ?)"
        params += [normalize_code(text), like, like, like, like]
    return kit.fetch_all(
        connection,
        f"""SELECT t.id, t.code, t.holder_name, t.status, t.checked_in_at, tt.name AS type_name, o.reference,
        o.buyer_name, o.buyer_email, o.status AS order_status
        FROM ev_tickets t JOIN ev_orders o ON o.id = t.order_id LEFT JOIN ev_ticket_types tt ON tt.id = t.ticket_type_id
        WHERE t.event_id = ? AND o.status IN ({",".join("?" * len(store.HOLDING))}) AND t.status != 'cancelled'{extra}
        ORDER BY t.holder_name, t.code LIMIT ?""",  # noqa: S608 - fixed clauses
        (*params, max(1, min(limit, 2000))),
    )


def check_in(
    connection: sqlite3.Connection, event_id: str, code_or_id: str, *, actor: str = ""
) -> dict[str, Any]:
    """Check a ticket in by its code (or id). Never twice: a second scan says when it was used."""
    value = str(code_or_id or "").strip()
    if not value:
        raise ValueError("Enter a ticket code.")
    ticket = kit.fetch_one(
        connection,
        """SELECT t.*, o.status AS order_status, o.buyer_name, o.reference, tt.name AS type_name FROM ev_tickets t
        JOIN ev_orders o ON o.id = t.order_id LEFT JOIN ev_ticket_types tt ON tt.id = t.ticket_type_id
        WHERE t.event_id = ? AND (t.code = ? OR t.id = ?)""",
        (event_id, normalize_code(value), value),
    )
    if not ticket:
        return {
            "result": "invalid",
            "message": "No ticket with that code for this event.",
        }
    if ticket["status"] == "cancelled" or ticket["order_status"] in (
        "cancelled",
        "refunded",
    ):
        return {
            "result": "cancelled",
            "message": "This ticket was cancelled.",
            "ticket": ticket,
        }
    if ticket["order_status"] == "pending_payment":
        return {
            "result": "unpaid",
            "message": f"Order {ticket['reference']} is not paid yet.",
            "ticket": ticket,
        }
    if ticket["status"] == "checked_in":
        return {
            "result": "already",
            "message": "Already checked in.",
            "checked_in_at": ticket["checked_in_at"],
            "ticket": ticket,
        }
    now = kit.now()
    updated = connection.execute(
        "UPDATE ev_tickets SET status = 'checked_in', checked_in_at = ? WHERE id = ? AND status = 'valid'",
        (now, ticket["id"]),
    ).rowcount
    if not updated:  # someone else scanned it at the same moment
        connection.commit()
        fresh = (
            kit.fetch_one(
                connection,
                "SELECT checked_in_at FROM ev_tickets WHERE id = ?",
                (ticket["id"],),
            )
            or {}
        )
        return {
            "result": "already",
            "message": "Already checked in.",
            "checked_in_at": fresh.get("checked_in_at"),
            "ticket": ticket,
        }
    kit.log(
        connection,
        APP,
        "check_in",
        f"{ticket['holder_name'] or ticket['buyer_name']} checked in",
        ref_type="ticket",
        ref_id=ticket["id"],
        actor=actor,
    )
    connection.commit()
    return {
        "result": "checked_in",
        "message": "Welcome in!",
        "checked_in_at": now,
        "ticket": {**ticket, "status": "checked_in", "checked_in_at": now},
    }


def undo_check_in(connection: sqlite3.Connection, ticket_id: str) -> dict[str, Any]:
    ticket = kit.require_row(connection, "ev_tickets", ticket_id, "ticket")
    if ticket["status"] == "checked_in":
        connection.execute(
            "UPDATE ev_tickets SET status = 'valid', checked_in_at = NULL WHERE id = ?",
            (ticket_id,),
        )
        connection.commit()
    return kit.require_row(connection, "ev_tickets", ticket_id, "ticket")


# ---------------------------------------------------------------------------
# waitlist
# ---------------------------------------------------------------------------


def join_waitlist(
    connection: sqlite3.Connection, event: Mapping[str, Any], raw: Mapping[str, Any]
) -> dict[str, Any]:
    if phase(event) != "published":
        raise ValueError("This event is not taking sign-ups.")
    email = kit.email(raw.get("email"), "your email", required=True)
    existing = kit.fetch_one(
        connection,
        "SELECT * FROM ev_waitlist WHERE event_id = ? AND email = ? AND status IN ('waiting', 'offered')",
        (event["id"], email),
    )
    if existing:
        return existing
    entry = {
        "id": kit.new_id(),
        "event_id": event["id"],
        "name": kit.text(raw.get("name"), "your name", max_len=120, required=True),
        "email": email,
        "phone": kit.phone(raw.get("phone")),
        "quantity": kit.integer(
            raw.get("quantity"),
            "the number of tickets",
            default=1,
            minimum=1,
            maximum=20,
        ),
        "now": kit.now(),
    }
    connection.execute(
        """INSERT INTO ev_waitlist (id, event_id, name, email, phone, quantity, status, created_at)
        VALUES (:id, :event_id, :name, :email, :phone, :quantity, 'waiting', :now)""",
        entry,
    )
    kit.log(
        connection,
        APP,
        "waitlist",
        f"{entry['name']} joined the waitlist for {event['title']}",
        ref_type="event",
        ref_id=event["id"],
        public=True,
    )
    connection.commit()
    return kit.require_row(connection, "ev_waitlist", entry["id"], "waitlist entry")


def offer_waitlist(
    connection: sqlite3.Connection, entry_id: str, *, via: Any = None
) -> dict[str, Any]:
    """Tell a waiting person seats are free, with the event page link (first come, first served)."""
    entry = kit.require_row(connection, "ev_waitlist", entry_id, "waitlist entry")
    if entry["status"] not in ("waiting", "offered"):
        raise ValueError("This person is no longer waiting.")
    event = store.get_event(connection, entry["event_id"])
    link = public.create_link(
        connection, APP, "event", event["id"], title=event["title"]
    )
    kit.queue_email(
        connection,
        APP,
        "waitlist_offer",
        to=entry["email"],
        subject=f"Tickets are available: {event['title']}",
        body=(
            f"Hi {entry['name']},\n\nGood news: tickets for {event['title']} on {_when(event)} are available again. "
            f"They go to whoever books first.\n\nBook now: {kit.public_url(link['path'], via)}"
        ),
        ref_type="waitlist",
        ref_id=entry_id,
    )
    kit.update_row(
        connection,
        "ev_waitlist",
        entry_id,
        {"status": "offered", "offered_at": kit.now()},
    )
    connection.commit()
    return kit.require_row(connection, "ev_waitlist", entry_id, "waitlist entry")


def set_waitlist_status(
    connection: sqlite3.Connection, entry_id: str, status: str
) -> dict[str, Any]:
    kit.require_row(connection, "ev_waitlist", entry_id, "waitlist entry")
    kit.update_row(
        connection,
        "ev_waitlist",
        entry_id,
        {"status": kit.choice(status, "the status", store.WAITLIST_STATUSES)},
    )
    connection.commit()
    return kit.require_row(connection, "ev_waitlist", entry_id, "waitlist entry")


# ---------------------------------------------------------------------------
# sponsors
# ---------------------------------------------------------------------------


def clean_sponsor(
    raw: Mapping[str, Any], current: Mapping[str, Any] | None = None
) -> dict[str, Any]:
    merged = {**(current or {}), **{k: v for k, v in raw.items() if v is not None}}
    return {
        "event_id": kit.text(merged.get("event_id"), "the event", max_len=32),
        "name": kit.text(
            merged.get("name"), "the sponsor name", max_len=140, required=True
        ),
        "tier": kit.text(merged.get("tier"), "the tier", max_len=40),
        "amount_cents": money_field(merged, "amount", "the amount"),
        "status": kit.choice(
            merged.get("status"),
            "the status",
            store.SPONSOR_STATUSES,
            default="pitched",
        ),
        "contact_name": kit.text(
            merged.get("contact_name"), "the contact", max_len=120
        ),
        "contact_email": kit.email(merged.get("contact_email"), "the contact email"),
        "notes": kit.text(
            merged.get("notes"), "the notes", max_len=2000, multiline=True
        ),
    }


def _sponsor_revenue(
    connection: sqlite3.Connection, sponsor: Mapping[str, Any]
) -> None:
    if sponsor["status"] == "paid" and int(sponsor["amount_cents"]) > 0:
        kit.record_revenue(
            connection,
            APP,
            source_type="sponsor",
            source_id=sponsor["id"],
            amount_cents=int(sponsor["amount_cents"]),
            label=f"Sponsorship: {sponsor['name']}"
            + (f" ({sponsor['tier']})" if sponsor["tier"] else ""),
            customer=sponsor["name"],
        )
    else:
        kit.void_revenue(connection, APP, "sponsor", sponsor["id"])


def save_sponsor(
    connection: sqlite3.Connection, raw: Mapping[str, Any], sponsor_id: str = ""
) -> dict[str, Any]:
    current = (
        kit.require_row(connection, "ev_sponsors", sponsor_id, "sponsor")
        if sponsor_id
        else None
    )
    values = clean_sponsor(raw, current)
    if values["event_id"]:
        store.get_event(connection, values["event_id"])
    if current is None:
        sponsor_id = kit.new_id()
        connection.execute(
            """INSERT INTO ev_sponsors (id, event_id, name, tier, amount_cents, status, contact_name, contact_email, notes, created_at)
            VALUES (:id, :event_id, :name, :tier, :amount_cents, :status, :contact_name, :contact_email, :notes, :now)""",
            {**values, "id": sponsor_id, "now": kit.now()},
        )
    else:
        kit.update_row(connection, "ev_sponsors", sponsor_id, values)
    if values["status"] == "paid" and not (current or {}).get("paid_at"):
        kit.update_row(connection, "ev_sponsors", sponsor_id, {"paid_at": kit.now()})
        kit.log(
            connection,
            APP,
            "sponsor_paid",
            f"Sponsor {values['name']} paid",
            ref_type="sponsor",
            ref_id=sponsor_id,
            amount_cents=values["amount_cents"],
        )
    sponsor = kit.require_row(connection, "ev_sponsors", sponsor_id, "sponsor")
    _sponsor_revenue(connection, sponsor)
    connection.commit()
    return sponsor


def delete_sponsor(connection: sqlite3.Connection, sponsor_id: str) -> None:
    kit.require_row(connection, "ev_sponsors", sponsor_id, "sponsor")
    kit.void_revenue(connection, APP, "sponsor", sponsor_id)
    connection.execute("DELETE FROM ev_sponsors WHERE id = ?", (sponsor_id,))
    connection.commit()


# ---------------------------------------------------------------------------
# messages to ticket holders
# ---------------------------------------------------------------------------


def announce(
    connection: sqlite3.Connection,
    event_id: str,
    *,
    subject: str,
    body: str,
    kind: str = "event_update",
    via: Any = None,
) -> int:
    """One message per buyer holding tickets (each with their own tickets link). Returns how many were prepared."""
    event = store.get_event(connection, event_id)
    subject = kit.text(subject, "a subject", max_len=200, required=True)
    body = kit.text(body, "a message", max_len=10000, required=True, multiline=True)
    seen: set[str] = set()
    count = 0
    for order in _holding_orders(connection, event_id):
        email = str(order["buyer_email"] or "").lower()
        if not email or email in seen:
            continue
        seen.add(email)
        link = public.link_for(connection, APP, "tickets", order["id"])
        footer = (
            f"\n\nYour tickets: {kit.public_url(link['path'], via)}"
            if link and kind != "post_event_thanks"
            else ""
        )
        kit.queue_email(
            connection,
            APP,
            kind,
            to=email,
            subject=subject,
            body=f"Hi {order['buyer_name']},\n\n{body}{footer}",
            ref_type="event",
            ref_id=event["id"],
        )
        count += 1
    kit.log(
        connection,
        APP,
        kind,
        f"{subject}: prepared for {count} ticket holders",
        ref_type="event",
        ref_id=event_id,
    )
    connection.commit()
    return count


def thank_attendees(
    connection: sqlite3.Connection, event_id: str, *, message: str = "", via: Any = None
) -> int:
    event = store.get_event(connection, event_id)
    body = message.strip() or (
        f"Thank you for coming to {event['title']}! We hope you had a great time. "
        "Reply to this email to tell us what you loved, and watch out for our next event."
    )
    return announce(
        connection,
        event_id,
        subject=f"Thanks for coming to {event['title']}",
        body=body,
        kind="post_event_thanks",
        via=via,
    )


# ---------------------------------------------------------------------------
# what the screens show
# ---------------------------------------------------------------------------


def event_view(
    connection: sqlite3.Connection, event: Mapping[str, Any], *, now: str | None = None
) -> dict[str, Any]:
    counts = store.event_counts(connection, event["id"])
    state = availability(connection, event, now)
    total = sum(
        int(t["quantity"])
        for t in store.ticket_types(connection, event["id"], active_only=True)
    )
    limit = min(total, int(event["capacity"])) if event["capacity"] else total
    return {
        **event,
        "phase": phase(event, now),
        "counts": counts,
        "available": limit,
        "sell_through": round(counts["sold"] * 100 / limit) if limit else 0,
        "check_in_rate": round(counts["checked_in"] * 100 / counts["sold"])
        if counts["sold"]
        else 0,
        "ticket_types": [
            {**t, "sold": store.sold_by_type(connection, event["id"]).get(t["id"], 0)}
            for t in store.ticket_types(connection, event["id"])
        ],
        "availability": state,
        "link": public.link_for(connection, APP, "event", event["id"]),
    }


def list_events(
    connection: sqlite3.Connection, *, scope: str = "", q: str = ""
) -> list[dict[str, Any]]:
    now = kit.now()
    clauses, params = [], []
    if scope == "upcoming":
        clauses.append("ends_at > ? AND status != 'cancelled'")
        params.append(now)
    elif scope == "past":
        clauses.append("ends_at <= ?")
        params.append(now)
    if q:
        clauses.append("(title LIKE ? OR venue_name LIKE ?)")
        like = f"%{kit.text(q, 'the search', max_len=100)}%"
        params += [like, like]
    where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    order = "starts_at ASC" if scope == "upcoming" else "starts_at DESC"
    rows = kit.fetch_all(
        connection,
        f"SELECT * FROM ev_events {where} ORDER BY {order} LIMIT 500",
        params,
    )  # noqa: S608 - fixed clauses
    out = []
    for event in rows:
        counts = store.event_counts(connection, event["id"])
        total = sum(
            int(t["quantity"])
            for t in store.ticket_types(connection, event["id"], active_only=True)
        )
        limit = min(total, int(event["capacity"])) if event["capacity"] else total
        out.append(
            {
                **event,
                "phase": phase(event, now),
                "counts": counts,
                "available": limit,
                "sell_through": round(counts["sold"] * 100 / limit) if limit else 0,
            }
        )
    return out


def overview(
    connection: sqlite3.Connection, *, now: datetime | None = None
) -> dict[str, Any]:
    moment = now or kit.utcnow()
    stamp = kit.iso(moment)
    month = stamp[:7]
    sold_month = kit.count(
        connection,
        f"""SELECT COUNT(*) FROM ev_tickets t JOIN ev_orders o ON o.id = t.order_id
        WHERE substr(o.created_at, 1, 7) = ? AND t.status != 'cancelled' AND o.status IN ({",".join("?" * len(store.HOLDING))})""",
        (month, *store.HOLDING),
    )
    revenue = kit.revenue(connection, app=APP, moment=moment)
    totals = connection.execute(
        """SELECT COUNT(*), SUM(CASE WHEN t.status = 'checked_in' THEN 1 ELSE 0 END) FROM ev_tickets t
        JOIN ev_orders o ON o.id = t.order_id JOIN ev_events e ON e.id = t.event_id
        WHERE e.starts_at <= ? AND t.status != 'cancelled' AND o.status = 'confirmed'""",
        (stamp,),
    ).fetchone()
    held = int(totals[0] or 0)
    pending = kit.count(
        connection, "SELECT COUNT(*) FROM ev_orders WHERE status = 'pending_payment'"
    )
    claimed = kit.count(
        connection,
        "SELECT COUNT(*) FROM ev_orders WHERE status = 'pending_payment' AND payment_claimed_at IS NOT NULL",
    )
    upcoming = list_events(connection, scope="upcoming")[:8]
    return {
        "cards": [
            {"label": "Tickets sold this month", "value": sold_month},
            {
                "label": "Revenue this month",
                "value": kit.fmt_money(
                    revenue["this_month_cents"], revenue["currency"]
                ),
                "tone": "good",
            },
            {"label": "Upcoming events", "value": len(upcoming)},
            {
                "label": "Check-in rate",
                "value": f"{round(int(totals[1] or 0) * 100 / held)}%" if held else "-",
                "hint": "At events that started",
            },
            {
                "label": "Awaiting payment",
                "value": pending,
                "hint": f"{claimed} say they paid" if claimed else "",
                "tone": "warn" if pending else "",
            },
        ],
        "revenue": revenue,
        "upcoming": upcoming,
        "pending_orders": pending,
        "claimed_orders": claimed,
        "waitlist": kit.count(
            connection, "SELECT COUNT(*) FROM ev_waitlist WHERE status = 'waiting'"
        ),
    }


def attendees_rows(
    connection: sqlite3.Connection, event_id: str
) -> list[dict[str, Any]]:
    return kit.fetch_all(
        connection,
        """SELECT t.code, t.holder_name, tt.name AS ticket_type, t.status, t.checked_in_at, t.price_cents,
        o.reference, o.buyer_name, o.buyer_email, o.buyer_phone, o.status AS order_status, o.created_at AS ordered_at
        FROM ev_tickets t JOIN ev_orders o ON o.id = t.order_id LEFT JOIN ev_ticket_types tt ON tt.id = t.ticket_type_id
        WHERE t.event_id = ? ORDER BY o.created_at, t.code""",
        (event_id,),
    )


# ---------------------------------------------------------------------------
# public views (what an anonymous visitor may see)
# ---------------------------------------------------------------------------


def public_event(
    connection: sqlite3.Connection, event: Mapping[str, Any]
) -> dict[str, Any]:
    state = availability(connection, event)
    types = [
        {
            "id": t["id"],
            "name": t["name"],
            "description": t["description"],
            "price_cents": int(t["price_cents"]),
            "on_sale": t["on_sale"],
            "reason": t["reason"],
            "max_per_order": int(t["max_per_order"]),
            # Exact numbers only when scarce: "3 left" sells; "947 left" tells competitors your sales.
            "remaining": int(t["remaining"]) if int(t["remaining"]) <= 20 else None,
        }
        for t in state["types"]
    ]
    has_promos = bool(
        kit.count(
            connection,
            "SELECT COUNT(*) FROM ev_promos WHERE active = 1 AND (event_id = '' OR event_id = ?)",
            (event["id"],),
        )
    )
    return {
        "event": {
            "title": event["title"],
            "description": event["description"],
            "category": event["category"],
            "venue_name": event["venue_name"],
            "venue_address": event["venue_address"],
            "online": bool(event["online_url"]),
            "starts_at": event["starts_at"],
            "ends_at": event["ends_at"],
            "cover_url": event["cover_url"],
            "phase": state["phase"],
        },
        "types": types,
        "on_sale": state["on_sale"],
        "sold_out": state["sold_out"],
        "waitlist_open": state["sold_out"] and state["phase"] == "published",
        "promo_codes": has_promos,
        "timezone": kit.business(connection)["timezone"],
    }


def public_order(
    connection: sqlite3.Connection, order: Mapping[str, Any]
) -> dict[str, Any]:
    event = store.get_event(connection, order["event_id"])
    tickets = store.tickets_of_order(connection, order["id"])
    return {
        "order": {
            "reference": order["reference"],
            "status": order["status"],
            "buyer_name": order["buyer_name"],
            "subtotal_cents": int(order["subtotal_cents"]),
            "discount_cents": int(order["discount_cents"]),
            "total_cents": int(order["total_cents"]),
            "promo_code": order["promo_code"],
            "payment_claimed": bool(order["payment_claimed_at"]),
            "created_at": order["created_at"],
        },
        "event": {
            "title": event["title"],
            "venue_name": event["venue_name"],
            "venue_address": event["venue_address"],
            # The online link is for ticket holders only, and only once the order is confirmed.
            "online_url": event["online_url"] if order["status"] == "confirmed" else "",
            "starts_at": event["starts_at"],
            "ends_at": event["ends_at"],
            "phase": phase(event),
        },
        "tickets": [
            {
                "code": t["code"],
                "type_name": t["type_name"] or "Ticket",
                "holder_name": t["holder_name"],
                "status": t["status"],
            }
            for t in tickets
        ],
        "timezone": kit.business(connection)["timezone"],
    }
