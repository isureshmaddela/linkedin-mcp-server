"""Shop tables (``sh_*``) and row helpers. Money is integer cents; JSON columns hold lists."""

from __future__ import annotations

import sqlite3
from collections.abc import Mapping
from typing import Any

from aira.suite import kit

APP = "shop"
PRODUCT_STATUSES = ("draft", "active", "archived")
PAYMENT_STATUSES = ("unpaid", "paid", "refunded")
FULFILMENT_STATUSES = ("placed", "packed", "shipped", "delivered", "cancelled")
DISCOUNT_KINDS = ("percent", "fixed")
FIRST_ORDER_NUMBER = 1001

SCHEMA = (
    """CREATE TABLE IF NOT EXISTS sh_products (
        id TEXT PRIMARY KEY,
        name TEXT NOT NULL,
        description TEXT NOT NULL DEFAULT '',
        price_cents INTEGER NOT NULL DEFAULT 0,
        compare_at_cents INTEGER NOT NULL DEFAULT 0,
        sku TEXT NOT NULL DEFAULT '',
        category TEXT NOT NULL DEFAULT '',
        images TEXT NOT NULL DEFAULT '[]',
        track_stock INTEGER NOT NULL DEFAULT 0,
        stock INTEGER NOT NULL DEFAULT 0,
        low_stock INTEGER NOT NULL DEFAULT 3,
        options TEXT NOT NULL DEFAULT '[]',
        status TEXT NOT NULL DEFAULT 'draft',
        position INTEGER NOT NULL DEFAULT 0,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL
    )""",
    "CREATE INDEX IF NOT EXISTS sh_products_status ON sh_products (status, category)",
    """CREATE TABLE IF NOT EXISTS sh_orders (
        id TEXT PRIMARY KEY,
        number INTEGER NOT NULL UNIQUE,
        source TEXT NOT NULL DEFAULT 'storefront',
        customer_name TEXT NOT NULL DEFAULT '',
        customer_email TEXT NOT NULL DEFAULT '',
        customer_phone TEXT NOT NULL DEFAULT '',
        delivery_method TEXT NOT NULL DEFAULT 'pickup',
        address TEXT NOT NULL DEFAULT '',
        note TEXT NOT NULL DEFAULT '',
        items TEXT NOT NULL DEFAULT '[]',
        subtotal_cents INTEGER NOT NULL DEFAULT 0,
        discount_code TEXT NOT NULL DEFAULT '',
        discount_cents INTEGER NOT NULL DEFAULT 0,
        delivery_cents INTEGER NOT NULL DEFAULT 0,
        tax_cents INTEGER NOT NULL DEFAULT 0,
        total_cents INTEGER NOT NULL DEFAULT 0,
        currency TEXT NOT NULL DEFAULT 'USD',
        payment_status TEXT NOT NULL DEFAULT 'unpaid',
        fulfilment_status TEXT NOT NULL DEFAULT 'placed',
        tracking_url TEXT NOT NULL DEFAULT '',
        internal_notes TEXT NOT NULL DEFAULT '',
        history TEXT NOT NULL DEFAULT '[]',
        restocked INTEGER NOT NULL DEFAULT 0,
        customer_says_paid_at TEXT,
        paid_at TEXT,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL
    )""",
    "CREATE INDEX IF NOT EXISTS sh_orders_created ON sh_orders (created_at)",
    "CREATE INDEX IF NOT EXISTS sh_orders_email ON sh_orders (customer_email)",
    """CREATE TABLE IF NOT EXISTS sh_discounts (
        id TEXT PRIMARY KEY,
        code TEXT NOT NULL UNIQUE,
        kind TEXT NOT NULL DEFAULT 'percent',
        value INTEGER NOT NULL DEFAULT 0,
        min_order_cents INTEGER NOT NULL DEFAULT 0,
        expires_on TEXT NOT NULL DEFAULT '',
        usage_limit INTEGER NOT NULL DEFAULT 0,
        uses INTEGER NOT NULL DEFAULT 0,
        active INTEGER NOT NULL DEFAULT 1,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL
    )""",
)

# Added later: customers asking to be told when a product is back, and the "share and earn" referral programme.
SCHEMA_V2 = (
    """CREATE TABLE IF NOT EXISTS sh_restock_alerts (
        id TEXT PRIMARY KEY,
        product_id TEXT NOT NULL,
        option TEXT NOT NULL DEFAULT '',
        email TEXT NOT NULL,
        name TEXT NOT NULL DEFAULT '',
        created_at TEXT NOT NULL,
        notified_at TEXT,
        UNIQUE (product_id, option, email)
    )""",
    "CREATE INDEX IF NOT EXISTS sh_restock_alerts_waiting ON sh_restock_alerts (product_id, notified_at)",
    """CREATE TABLE IF NOT EXISTS sh_referrers (
        id TEXT PRIMARY KEY,
        code TEXT NOT NULL UNIQUE,
        email TEXT NOT NULL UNIQUE,
        name TEXT NOT NULL DEFAULT '',
        created_at TEXT NOT NULL
    )""",
    """CREATE TABLE IF NOT EXISTS sh_referral_rewards (
        id TEXT PRIMARY KEY,
        referrer_id TEXT NOT NULL,
        order_id TEXT NOT NULL UNIQUE,
        friend_name TEXT NOT NULL DEFAULT '',
        reward_code TEXT NOT NULL,
        amount_cents INTEGER NOT NULL DEFAULT 0,
        created_at TEXT NOT NULL,
        voided_at TEXT
    )""",
    "CREATE INDEX IF NOT EXISTS sh_referral_rewards_referrer ON sh_referral_rewards (referrer_id)",
)


def ensure(connection: sqlite3.Connection) -> None:
    kit.ensure_core(connection)
    kit.ensure(connection, "shop.v1", SCHEMA)
    kit.ensure(connection, "shop.v2", SCHEMA_V2)


# ---------------------------------------------------------------------------
# products
# ---------------------------------------------------------------------------


def decode_product(row: Mapping[str, Any]) -> dict[str, Any]:
    product = dict(row)
    product["images"] = kit.loads(product.get("images"), []) or []
    product["options"] = kit.loads(product.get("options"), []) or []
    product["track_stock"] = bool(product.get("track_stock"))
    return product


def list_products(
    connection: sqlite3.Connection,
    *,
    status: str = "",
    q: str = "",
    category: str = "",
    limit: int = 500,
) -> list[dict[str, Any]]:
    clauses, params = [], []
    if status:
        clauses.append("status = ?")
        params.append(status)
    if category:
        clauses.append("category = ?")
        params.append(category)
    if q:
        clauses.append(
            "(name LIKE ? OR sku LIKE ? OR description LIKE ? OR category LIKE ?)"
        )
        like = f"%{q}%"
        params.extend([like, like, like, like])
    where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    rows = kit.fetch_all(
        connection,
        f"SELECT * FROM sh_products {where} ORDER BY position, created_at DESC LIMIT ?",  # noqa: S608
        (*params, max(1, min(limit, 2000))),
    )
    return [decode_product(row) for row in rows]


def get_product(connection: sqlite3.Connection, product_id: str) -> dict[str, Any]:
    return decode_product(
        kit.require_row(connection, "sh_products", product_id, "product")
    )


def insert_product(connection: sqlite3.Connection, values: Mapping[str, Any]) -> str:
    product_id = kit.new_id()
    stamp = kit.now()
    row = {**values, "id": product_id, "created_at": stamp, "updated_at": stamp}
    row["images"] = kit.dumps(row.get("images") or [])
    row["options"] = kit.dumps(row.get("options") or [])
    row["track_stock"] = 1 if row.get("track_stock") else 0
    columns = ", ".join(row)
    marks = ", ".join(f":{key}" for key in row)
    connection.execute(f"INSERT INTO sh_products ({columns}) VALUES ({marks})", row)  # noqa: S608 - keys are ours
    return product_id


def update_product(
    connection: sqlite3.Connection, product_id: str, values: Mapping[str, Any]
) -> None:
    row = dict(values)
    if "images" in row:
        row["images"] = kit.dumps(row["images"] or [])
    if "options" in row:
        row["options"] = kit.dumps(row["options"] or [])
    if "track_stock" in row:
        row["track_stock"] = 1 if row["track_stock"] else 0
    row["updated_at"] = kit.now()
    kit.update_row(connection, "sh_products", product_id, row)


def categories(
    connection: sqlite3.Connection, *, active_only: bool = False
) -> list[str]:
    where = "WHERE category != ''" + (" AND status = 'active'" if active_only else "")
    rows = connection.execute(
        f"SELECT DISTINCT category FROM sh_products {where} ORDER BY category"  # noqa: S608
    ).fetchall()
    return [row[0] for row in rows]


# ---------------------------------------------------------------------------
# orders
# ---------------------------------------------------------------------------


def decode_order(row: Mapping[str, Any]) -> dict[str, Any]:
    order = dict(row)
    order["items"] = kit.loads(order.get("items"), []) or []
    order["history"] = kit.loads(order.get("history"), []) or []
    order["restocked"] = bool(order.get("restocked"))
    return order


def get_order(connection: sqlite3.Connection, order_id: str) -> dict[str, Any]:
    return decode_order(kit.require_row(connection, "sh_orders", order_id, "order"))


def list_orders(
    connection: sqlite3.Connection,
    *,
    fulfilment: str = "",
    payment: str = "",
    q: str = "",
    open_only: bool = False,
    limit: int = 500,
) -> list[dict[str, Any]]:
    clauses, params = [], []
    if fulfilment:
        clauses.append("fulfilment_status = ?")
        params.append(fulfilment)
    if payment:
        clauses.append("payment_status = ?")
        params.append(payment)
    if open_only:
        clauses.append("fulfilment_status IN ('placed', 'packed')")
    if q:
        text = q.lstrip("#")
        clauses.append(
            "(customer_name LIKE ? OR customer_email LIKE ? OR customer_phone LIKE ? OR CAST(number AS TEXT) = ?)"
        )
        like = f"%{q}%"
        params.extend([like, like, like, text])
    where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    rows = kit.fetch_all(
        connection,
        f"SELECT * FROM sh_orders {where} ORDER BY number DESC LIMIT ?",  # noqa: S608
        (*params, max(1, min(limit, 5000))),
    )
    return [decode_order(row) for row in rows]


def next_number(connection: sqlite3.Connection) -> int:
    row = connection.execute("SELECT MAX(number) FROM sh_orders").fetchone()
    current = int(row[0]) if row and row[0] is not None else FIRST_ORDER_NUMBER - 1
    return current + 1


# ---------------------------------------------------------------------------
# discounts
# ---------------------------------------------------------------------------


def decode_discount(row: Mapping[str, Any]) -> dict[str, Any]:
    discount = dict(row)
    discount["active"] = bool(discount.get("active"))
    return discount


def list_discounts(connection: sqlite3.Connection) -> list[dict[str, Any]]:
    return [
        decode_discount(row)
        for row in kit.fetch_all(
            connection, "SELECT * FROM sh_discounts ORDER BY created_at DESC"
        )
    ]


def get_discount(connection: sqlite3.Connection, discount_id: str) -> dict[str, Any]:
    return decode_discount(
        kit.require_row(connection, "sh_discounts", discount_id, "discount code")
    )


def code_taken(connection: sqlite3.Connection, code: str) -> bool:
    """A discount code and a referral code share one namespace (customers type either at checkout)."""
    text = code.strip().upper()
    return bool(
        kit.count(
            connection, "SELECT COUNT(*) FROM sh_discounts WHERE code = ?", (text,)
        )
        or kit.count(
            connection, "SELECT COUNT(*) FROM sh_referrers WHERE code = ?", (text,)
        )
    )


def referrer_by_code(
    connection: sqlite3.Connection, code: str
) -> dict[str, Any] | None:
    return kit.fetch_one(
        connection, "SELECT * FROM sh_referrers WHERE code = ?", (code.strip().upper(),)
    )


def referrer_by_email(
    connection: sqlite3.Connection, email: str
) -> dict[str, Any] | None:
    if not email:
        return None
    return kit.fetch_one(
        connection,
        "SELECT * FROM sh_referrers WHERE email = ?",
        (email.strip().lower(),),
    )


def discount_by_code(
    connection: sqlite3.Connection, code: str
) -> dict[str, Any] | None:
    row = kit.fetch_one(
        connection, "SELECT * FROM sh_discounts WHERE code = ?", (code.strip().upper(),)
    )
    return decode_discount(row) if row else None


# ---------------------------------------------------------------------------
# settings
# ---------------------------------------------------------------------------

SETTINGS_DEFAULTS: dict[str, Any] = {
    "open": True,
    "pickup_enabled": True,
    "pickup_instructions": "",
    "delivery_enabled": True,
    "delivery_fee_cents": 0,
    "free_delivery_over_cents": 0,  # 0 = no free-delivery threshold
    "notice": "",  # shown at the top of the storefront
    "closed_message": "We are not taking orders right now. Please check back soon.",
    # Share and earn: a customer who paid gets a code; a new customer using it gets a percent off their first
    # order, and the customer who shared it gets a one-time amount off their next order when that order is paid.
    "referrals_enabled": False,
    "referral_friend_percent": 10,
    "referral_reward_cents": 500,
    "referral_reward_days": 180,
}


def settings(connection: sqlite3.Connection) -> dict[str, Any]:
    stored = kit.get_setting(connection, APP, "settings", {}) or {}
    return {
        **SETTINGS_DEFAULTS,
        **{k: v for k, v in stored.items() if k in SETTINGS_DEFAULTS},
    }


def save_settings(
    connection: sqlite3.Connection, raw: Mapping[str, Any]
) -> dict[str, Any]:
    merged = {
        **settings(connection),
        **{k: v for k, v in raw.items() if k in SETTINGS_DEFAULTS},
    }
    clean = {
        "open": kit.flag(merged["open"]),
        "pickup_enabled": kit.flag(merged["pickup_enabled"]),
        "pickup_instructions": kit.text(
            merged["pickup_instructions"],
            "pickup instructions",
            max_len=500,
            multiline=True,
        ),
        "delivery_enabled": kit.flag(merged["delivery_enabled"]),
        "delivery_fee_cents": amount(merged["delivery_fee_cents"], "the delivery fee"),
        "free_delivery_over_cents": amount(
            merged["free_delivery_over_cents"], "the free-delivery threshold"
        ),
        "notice": kit.text(
            merged["notice"], "the store notice", max_len=300, multiline=True
        ),
        "closed_message": kit.text(
            merged["closed_message"], "the closed message", max_len=300, multiline=True
        )
        or SETTINGS_DEFAULTS["closed_message"],
    }
    clean.update(
        {
            "referrals_enabled": kit.flag(merged["referrals_enabled"]),
            "referral_friend_percent": kit.integer(
                merged["referral_friend_percent"],
                "the friend's discount",
                default=10,
                minimum=0,
                maximum=90,
            ),
            "referral_reward_cents": amount(
                merged["referral_reward_cents"], "the reward"
            ),
            "referral_reward_days": kit.integer(
                merged["referral_reward_days"],
                "how long a reward can be used",
                default=180,
                minimum=1,
                maximum=3650,
            ),
        }
    )
    if clean["referrals_enabled"] and not (
        clean["referral_friend_percent"] or clean["referral_reward_cents"]
    ):
        raise ValueError(
            "Give friends a discount, the person who shared a reward, or both."
        )
    if not clean["pickup_enabled"] and not clean["delivery_enabled"]:
        raise ValueError("Offer pickup, delivery or both.")
    kit.set_setting(connection, APP, "settings", clean)
    connection.commit()
    return clean


def amount(value: Any, label: str, *, default: int = 0) -> int:
    """Money from the API: an integer is cents (what the app sends), text like "12.50" is parsed."""
    if value is None or value == "":
        return default
    if isinstance(value, bool):
        raise ValueError(f"{label[:1].upper()}{label[1:]} must be an amount.")
    if isinstance(value, int):
        if value < 0:
            raise ValueError(f"{label[:1].upper()}{label[1:]} cannot be negative.")
        if value > 10_000_000_00:
            raise ValueError(f"{label[:1].upper()}{label[1:]} is too large.")
        return value
    if isinstance(value, float):
        if not value.is_integer():
            raise ValueError(f"{label[:1].upper()}{label[1:]} must be whole cents.")
        return amount(int(value), label)
    return kit.cents(value, label)
