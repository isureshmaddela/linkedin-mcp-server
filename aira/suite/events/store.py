"""Tables, rows and plain reads for Aira Events (prefix ``ev_``)."""

from __future__ import annotations

import sqlite3
from typing import Any

from aira.suite import kit

APP = "events"

EVENT_STATUSES = ("draft", "published", "cancelled", "ended")
ORDER_STATUSES = ("pending_payment", "confirmed", "cancelled", "refunded")
TICKET_STATUSES = ("valid", "checked_in", "cancelled")
SPONSOR_STATUSES = ("pitched", "agreed", "paid", "declined")
WAITLIST_STATUSES = ("waiting", "offered", "converted", "removed")
CATEGORIES = (
    "concert",
    "comedy",
    "theatre",
    "party",
    "festival",
    "conference",
    "workshop",
    "meetup",
    "sports",
    "screening",
    "exhibition",
    "other",
)
# Orders that hold seats: a pending order keeps its seats until it is paid or cancelled.
HOLDING = ("pending_payment", "confirmed")

SCHEMA = (
    """CREATE TABLE IF NOT EXISTS ev_events (
        id TEXT PRIMARY KEY,
        title TEXT NOT NULL,
        description TEXT NOT NULL DEFAULT '',
        category TEXT NOT NULL DEFAULT 'other',
        venue_name TEXT NOT NULL DEFAULT '',
        venue_address TEXT NOT NULL DEFAULT '',
        online_url TEXT NOT NULL DEFAULT '',
        starts_at TEXT NOT NULL,
        ends_at TEXT NOT NULL,
        cover_url TEXT NOT NULL DEFAULT '',
        status TEXT NOT NULL DEFAULT 'draft',
        capacity INTEGER NOT NULL DEFAULT 0,
        notes TEXT NOT NULL DEFAULT '',
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL,
        published_at TEXT
    )""",
    "CREATE INDEX IF NOT EXISTS ev_events_start ON ev_events (starts_at)",
    """CREATE TABLE IF NOT EXISTS ev_ticket_types (
        id TEXT PRIMARY KEY,
        event_id TEXT NOT NULL,
        name TEXT NOT NULL,
        description TEXT NOT NULL DEFAULT '',
        price_cents INTEGER NOT NULL DEFAULT 0,
        quantity INTEGER NOT NULL,
        sales_start TEXT NOT NULL DEFAULT '',
        sales_end TEXT NOT NULL DEFAULT '',
        max_per_order INTEGER NOT NULL DEFAULT 10,
        position INTEGER NOT NULL DEFAULT 0,
        active INTEGER NOT NULL DEFAULT 1,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL
    )""",
    "CREATE INDEX IF NOT EXISTS ev_ticket_types_event ON ev_ticket_types (event_id, position)",
    """CREATE TABLE IF NOT EXISTS ev_orders (
        id TEXT PRIMARY KEY,
        event_id TEXT NOT NULL,
        reference TEXT NOT NULL UNIQUE,
        buyer_name TEXT NOT NULL,
        buyer_email TEXT NOT NULL DEFAULT '',
        buyer_phone TEXT NOT NULL DEFAULT '',
        status TEXT NOT NULL,
        source TEXT NOT NULL DEFAULT 'public',
        subtotal_cents INTEGER NOT NULL DEFAULT 0,
        discount_cents INTEGER NOT NULL DEFAULT 0,
        total_cents INTEGER NOT NULL DEFAULT 0,
        promo_id TEXT NOT NULL DEFAULT '',
        promo_code TEXT NOT NULL DEFAULT '',
        notes TEXT NOT NULL DEFAULT '',
        payment_claimed_at TEXT,
        created_at TEXT NOT NULL,
        paid_at TEXT,
        cancelled_at TEXT,
        refunded_at TEXT
    )""",
    "CREATE INDEX IF NOT EXISTS ev_orders_event ON ev_orders (event_id, created_at)",
    """CREATE TABLE IF NOT EXISTS ev_tickets (
        id TEXT PRIMARY KEY,
        order_id TEXT NOT NULL,
        event_id TEXT NOT NULL,
        ticket_type_id TEXT NOT NULL,
        holder_name TEXT NOT NULL DEFAULT '',
        code TEXT NOT NULL UNIQUE,
        price_cents INTEGER NOT NULL DEFAULT 0,
        status TEXT NOT NULL DEFAULT 'valid',
        checked_in_at TEXT,
        created_at TEXT NOT NULL
    )""",
    "CREATE INDEX IF NOT EXISTS ev_tickets_event ON ev_tickets (event_id, status)",
    "CREATE INDEX IF NOT EXISTS ev_tickets_order ON ev_tickets (order_id)",
    """CREATE TABLE IF NOT EXISTS ev_promos (
        id TEXT PRIMARY KEY,
        code TEXT NOT NULL UNIQUE,
        event_id TEXT NOT NULL DEFAULT '',
        ticket_type_id TEXT NOT NULL DEFAULT '',
        kind TEXT NOT NULL,
        value INTEGER NOT NULL,
        max_uses INTEGER NOT NULL DEFAULT 0,
        used INTEGER NOT NULL DEFAULT 0,
        expires_at TEXT NOT NULL DEFAULT '',
        active INTEGER NOT NULL DEFAULT 1,
        created_at TEXT NOT NULL
    )""",
    """CREATE TABLE IF NOT EXISTS ev_waitlist (
        id TEXT PRIMARY KEY,
        event_id TEXT NOT NULL,
        name TEXT NOT NULL,
        email TEXT NOT NULL,
        phone TEXT NOT NULL DEFAULT '',
        quantity INTEGER NOT NULL DEFAULT 1,
        status TEXT NOT NULL DEFAULT 'waiting',
        created_at TEXT NOT NULL,
        offered_at TEXT
    )""",
    "CREATE INDEX IF NOT EXISTS ev_waitlist_event ON ev_waitlist (event_id, status, created_at)",
    """CREATE TABLE IF NOT EXISTS ev_sponsors (
        id TEXT PRIMARY KEY,
        event_id TEXT NOT NULL DEFAULT '',
        name TEXT NOT NULL,
        tier TEXT NOT NULL DEFAULT '',
        amount_cents INTEGER NOT NULL DEFAULT 0,
        status TEXT NOT NULL DEFAULT 'pitched',
        contact_name TEXT NOT NULL DEFAULT '',
        contact_email TEXT NOT NULL DEFAULT '',
        notes TEXT NOT NULL DEFAULT '',
        created_at TEXT NOT NULL,
        paid_at TEXT
    )""",
)


def ensure(connection: sqlite3.Connection) -> None:
    kit.ensure_core(connection)
    kit.ensure(connection, "events.v1", SCHEMA)


# ---------------------------------------------------------------------------
# reads
# ---------------------------------------------------------------------------


def get_event(connection: sqlite3.Connection, event_id: str) -> dict[str, Any]:
    return kit.require_row(connection, "ev_events", event_id, "event")


def get_ticket_type(connection: sqlite3.Connection, type_id: str) -> dict[str, Any]:
    return kit.require_row(connection, "ev_ticket_types", type_id, "ticket type")


def get_order(connection: sqlite3.Connection, order_id: str) -> dict[str, Any]:
    return kit.require_row(connection, "ev_orders", order_id, "order")


def ticket_types(
    connection: sqlite3.Connection, event_id: str, *, active_only: bool = False
) -> list[dict[str, Any]]:
    sql = "SELECT * FROM ev_ticket_types WHERE event_id = ?"
    if active_only:
        sql += " AND active = 1"
    return kit.fetch_all(
        connection, sql + " ORDER BY position, created_at", (event_id,)
    )


def sold_by_type(connection: sqlite3.Connection, event_id: str) -> dict[str, int]:
    """Seats taken per ticket type: tickets that are not cancelled, in orders that hold seats."""
    rows = connection.execute(
        f"""SELECT t.ticket_type_id, COUNT(*) FROM ev_tickets t JOIN ev_orders o ON o.id = t.order_id
        WHERE t.event_id = ? AND t.status != 'cancelled' AND o.status IN ({",".join("?" * len(HOLDING))})
        GROUP BY t.ticket_type_id""",
        (event_id, *HOLDING),
    ).fetchall()
    return {row[0]: int(row[1]) for row in rows}


def tickets_of_order(
    connection: sqlite3.Connection, order_id: str
) -> list[dict[str, Any]]:
    return kit.fetch_all(
        connection,
        """SELECT t.*, tt.name AS type_name FROM ev_tickets t LEFT JOIN ev_ticket_types tt ON tt.id = t.ticket_type_id
        WHERE t.order_id = ? ORDER BY t.rowid""",
        (order_id,),
    )


def event_counts(connection: sqlite3.Connection, event_id: str) -> dict[str, int]:
    row = connection.execute(
        f"""SELECT
            COUNT(*) AS sold,
            SUM(CASE WHEN t.status = 'checked_in' THEN 1 ELSE 0 END) AS checked_in,
            SUM(CASE WHEN o.status = 'pending_payment' THEN 1 ELSE 0 END) AS pending
        FROM ev_tickets t JOIN ev_orders o ON o.id = t.order_id
        WHERE t.event_id = ? AND t.status != 'cancelled' AND o.status IN ({",".join("?" * len(HOLDING))})""",
        (event_id, *HOLDING),
    ).fetchone()
    revenue = connection.execute(
        "SELECT COALESCE(SUM(total_cents), 0) FROM ev_orders WHERE event_id = ? AND status = 'confirmed' AND paid_at IS NOT NULL",
        (event_id,),
    ).fetchone()[0]
    orders = connection.execute(
        f"SELECT COUNT(*) FROM ev_orders WHERE event_id = ? AND status IN ({','.join('?' * len(HOLDING))})",
        (event_id, *HOLDING),
    ).fetchone()[0]
    waiting = connection.execute(
        "SELECT COUNT(*) FROM ev_waitlist WHERE event_id = ? AND status = 'waiting'",
        (event_id,),
    ).fetchone()[0]
    return {
        "sold": int(row[0] or 0),
        "checked_in": int(row[1] or 0),
        "pending": int(row[2] or 0),
        "revenue_cents": int(revenue or 0),
        "orders": int(orders or 0),
        "waitlist": int(waiting or 0),
    }
