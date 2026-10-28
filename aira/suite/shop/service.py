"""Shop rules: product input, pricing (options, discount codes, delivery, tax), stock, orders and customers.

Prices on an order are always worked out here from the stored products, never taken from the customer's
browser. Placing an order is one transaction: stock is checked and taken, the discount use counted and the order
numbered together, so two customers cannot buy the last item twice.
"""

from __future__ import annotations

import secrets
import sqlite3
from collections.abc import Mapping, Sequence
from datetime import date, timedelta
from typing import Any

from aira.suite import kit, public
from aira.suite.shop import store
from aira.suite.shop.store import APP, amount

MAX_LINES = 50
MAX_QUANTITY = 99
MAX_IMAGES = 8
MAX_OPTIONS = 30
DELIVERY_METHODS = ("pickup", "delivery")
CODE_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
MAX_WAITING_PER_PRODUCT = 1000


# ---------------------------------------------------------------------------
# products
# ---------------------------------------------------------------------------


def _options(raw: Any) -> list[dict[str, Any]]:
    """``["S", "M"]`` or ``[{"name": "S", "stock": 4, "price_cents": 1500}]`` -> normalized options."""
    if raw in (None, ""):
        return []
    if isinstance(raw, str):
        raw = [part for part in raw.split(",")]
    if not isinstance(raw, list):
        raise ValueError("Options must be a list.")
    out: list[dict[str, Any]] = []
    seen: set[str] = set()
    for item in raw[: MAX_OPTIONS + 1]:
        entry = item if isinstance(item, Mapping) else {"name": item}
        name = kit.text(entry.get("name"), "an option name", max_len=60)
        if not name:
            continue
        if name.lower() in seen:
            raise ValueError(f"The option {name} is listed twice.")
        seen.add(name.lower())
        stock = entry.get("stock")
        price = entry.get("price_cents")
        out.append(
            {
                "name": name,
                "stock": None
                if stock in (None, "")
                else kit.integer(
                    stock, f"the stock of {name}", minimum=0, maximum=1_000_000
                ),
                "price_cents": None
                if price in (None, "")
                else amount(price, f"the price of {name}"),
            }
        )
    if len(out) > MAX_OPTIONS:
        raise ValueError(f"A product can have at most {MAX_OPTIONS} options.")
    return out


def _images(raw: Any) -> list[str]:
    if raw in (None, ""):
        return []
    items = raw if isinstance(raw, list) else str(raw).split()
    images = []
    for item in items:
        link = kit.url(item, "an image link")
        if link and not link.lower().startswith("https://"):
            raise ValueError("Image links must start with https://.")
        if link and link not in images:
            images.append(link)
    if len(images) > MAX_IMAGES:
        raise ValueError(f"A product can have at most {MAX_IMAGES} images.")
    return images


def product_values(raw: Mapping[str, Any], *, partial: bool = False) -> dict[str, Any]:
    """Validated product fields. ``partial`` keeps only the fields that were sent (an edit)."""
    values: dict[str, Any] = {}

    def has(key: str) -> bool:
        return not partial or key in raw

    if has("name"):
        values["name"] = kit.text(
            raw.get("name"), "the product name", max_len=120, required=True
        )
    if has("description"):
        values["description"] = kit.text(
            raw.get("description"), "a description", max_len=5000, multiline=True
        )
    if has("price_cents"):
        values["price_cents"] = amount(raw.get("price_cents"), "the price")
    if has("compare_at_cents"):
        values["compare_at_cents"] = amount(
            raw.get("compare_at_cents"), "the compare-at price"
        )
    if has("sku"):
        values["sku"] = kit.text(raw.get("sku"), "the SKU", max_len=60)
    if has("category"):
        values["category"] = kit.text(raw.get("category"), "a category", max_len=60)
    if has("images"):
        values["images"] = _images(raw.get("images"))
    if has("track_stock"):
        values["track_stock"] = kit.flag(raw.get("track_stock"))
    if has("stock"):
        values["stock"] = kit.integer(
            raw.get("stock"), "the stock", default=0, minimum=0, maximum=1_000_000
        )
    if has("low_stock"):
        values["low_stock"] = kit.integer(
            raw.get("low_stock"),
            "the low-stock alert",
            default=3,
            minimum=0,
            maximum=100_000,
        )
    if has("options"):
        values["options"] = _options(raw.get("options"))
    if has("status"):
        values["status"] = kit.choice(
            raw.get("status"), "status", store.PRODUCT_STATUSES, default="active"
        )
    if has("position"):
        values["position"] = kit.integer(
            raw.get("position"), "the position", default=0, minimum=0, maximum=100_000
        )
    if (
        values.get("compare_at_cents")
        and values.get("price_cents") is not None
        and values["compare_at_cents"] <= values["price_cents"]
    ):
        values["compare_at_cents"] = 0  # a "was" price only makes sense above the price
    return values


def create_product(
    connection: sqlite3.Connection, raw: Mapping[str, Any]
) -> dict[str, Any]:
    values = product_values(raw)
    product_id = store.insert_product(connection, values)
    kit.log(
        connection,
        APP,
        "product",
        f"Product added: {values['name']}",
        ref_type="product",
        ref_id=product_id,
    )
    connection.commit()
    return store.get_product(connection, product_id)


def update_product(
    connection: sqlite3.Connection,
    product_id: str,
    raw: Mapping[str, Any],
    *,
    origin: str = "",
) -> dict[str, Any]:
    current = store.get_product(connection, product_id)
    values = product_values(raw, partial=True)
    price = values.get("price_cents", current["price_cents"])
    compare_at = values.get("compare_at_cents", current["compare_at_cents"])
    if compare_at and compare_at <= price:
        values["compare_at_cents"] = 0  # a "was" price only makes sense above the price
    store.update_product(connection, product_id, values)
    connection.commit()
    updated = store.get_product(connection, product_id)
    notify_restocked(connection, updated, origin=origin)
    return updated


def delete_product(connection: sqlite3.Connection, product_id: str) -> dict[str, Any]:
    """Delete a product nobody has ordered; one with orders is archived instead (orders keep their snapshot)."""
    product = store.get_product(connection, product_id)
    ordered = kit.count(
        connection,
        "SELECT COUNT(*) FROM sh_orders WHERE items LIKE ?",
        (f'%"{product_id}"%',),
    )
    if ordered:
        store.update_product(connection, product_id, {"status": "archived"})
        connection.commit()
        return {"archived": True, "deleted": False}
    connection.execute("DELETE FROM sh_products WHERE id = ?", (product["id"],))
    # Nobody can be told it is back any more: its back-in-stock requests go too.
    connection.execute(
        "DELETE FROM sh_restock_alerts WHERE product_id = ?", (product["id"],)
    )
    connection.commit()
    return {"archived": False, "deleted": True}


def find_option(product: Mapping[str, Any], option: str) -> dict[str, Any] | None:
    for entry in product.get("options") or []:
        if entry["name"].lower() == option.strip().lower():
            return entry
    return None


def available(product: Mapping[str, Any], option: str = "") -> int | None:
    """How many can be bought now (None = not tracked, no limit)."""
    if not product.get("track_stock"):
        return None
    entry = find_option(product, option) if option else None
    if entry is not None and entry.get("stock") is not None:
        return int(entry["stock"])
    return int(product.get("stock") or 0)


def unit_price(product: Mapping[str, Any], option: str = "") -> int:
    entry = find_option(product, option) if option else None
    if entry is not None and entry.get("price_cents") is not None:
        return int(entry["price_cents"])
    return int(product["price_cents"])


def is_low(product: Mapping[str, Any]) -> bool:
    if not product.get("track_stock"):
        return False
    threshold = int(product.get("low_stock") or 0)
    option_stock = [
        entry["stock"]
        for entry in product.get("options") or []
        if entry.get("stock") is not None
    ]
    if option_stock:
        return any(int(stock) <= threshold for stock in option_stock)
    return int(product.get("stock") or 0) <= threshold


def draft_description(raw: Mapping[str, Any]) -> dict[str, Any]:
    """A product description from the writing model, or a solid template."""
    name = kit.text(raw.get("name"), "the product name", max_len=120, required=True)
    category = kit.text(raw.get("category"), "a category", max_len=60)
    notes = kit.text(
        raw.get("notes") or raw.get("description"),
        "notes",
        max_len=1000,
        multiline=True,
    )
    audience = kit.text(raw.get("audience"), "who it is for", max_len=120)
    points = [
        part.strip(" -•")
        for part in notes.replace("\n", ",").split(",")
        if part.strip(" -•")
    ][:5]
    lines = [
        f"{name}{f' for {audience}' if audience else ''}: made to be used every day and loved for years."
    ]
    if points:
        lines.append("")
        lines.extend(f"- {point[:1].upper()}{point[1:]}" for point in points)
    lines.append("")
    lines.append(
        f"A thoughtful choice{f' in our {category.lower()} range' if category else ''}. Order today: pick it up or have it delivered."
    )
    prompt = (
        'Write a short, warm product description for an online shop. Reply as JSON {"description": "..."}. '
        "Use 2 short paragraphs or one paragraph and up to 5 bullet points, plain text, no prices, no claims that are "
        "not in the notes.\n"
        f"Product: {name}\nCategory: {category or 'none'}\nFor: {audience or 'anyone'}\nNotes: {notes or 'none'}"
    )
    return kit.draft(prompt, {"description": "\n".join(lines)})


# ---------------------------------------------------------------------------
# discounts
# ---------------------------------------------------------------------------


def discount_values(raw: Mapping[str, Any], *, partial: bool = False) -> dict[str, Any]:
    values: dict[str, Any] = {}

    def has(key: str) -> bool:
        return not partial or key in raw

    if has("code"):
        code = (
            kit.text(raw.get("code"), "the code", max_len=30, required=True)
            .upper()
            .replace(" ", "")
        )
        if not code.replace("-", "").replace("_", "").isalnum():
            raise ValueError("A code can use letters, numbers, - and _ only.")
        values["code"] = code
    if has("kind"):
        values["kind"] = kit.choice(
            raw.get("kind"), "the kind", store.DISCOUNT_KINDS, default="percent"
        )
    if has("value"):
        kind = values.get("kind") or raw.get("kind") or "percent"
        if kind == "percent":
            values["value"] = kit.integer(
                raw.get("value"), "the percentage", minimum=1, maximum=100
            )
        else:
            values["value"] = amount(raw.get("value"), "the amount off")
            if values["value"] <= 0:
                raise ValueError("The amount off must be more than 0.")
    if has("min_order_cents"):
        values["min_order_cents"] = amount(
            raw.get("min_order_cents"), "the minimum order"
        )
    if has("expires_on"):
        values["expires_on"] = kit.day(raw.get("expires_on"), "the expiry date")
    if has("usage_limit"):
        values["usage_limit"] = kit.integer(
            raw.get("usage_limit"),
            "the usage limit",
            default=0,
            minimum=0,
            maximum=1_000_000,
        )
    if has("active"):
        values["active"] = 1 if kit.flag(raw.get("active", True)) else 0
    return values


def create_discount(
    connection: sqlite3.Connection, raw: Mapping[str, Any]
) -> dict[str, Any]:
    values = discount_values({"active": True, **raw})
    if store.code_taken(connection, values["code"]):
        raise ValueError(f"The code {values['code']} already exists.")
    discount_id = kit.new_id()
    stamp = kit.now()
    connection.execute(
        """INSERT INTO sh_discounts (id, code, kind, value, min_order_cents, expires_on, usage_limit, uses, active, created_at, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, 0, ?, ?, ?)""",
        (
            discount_id,
            values["code"],
            values["kind"],
            values["value"],
            values["min_order_cents"],
            values["expires_on"],
            values["usage_limit"],
            values["active"],
            stamp,
            stamp,
        ),
    )
    connection.commit()
    return store.get_discount(connection, discount_id)


def update_discount(
    connection: sqlite3.Connection, discount_id: str, raw: Mapping[str, Any]
) -> dict[str, Any]:
    current = store.get_discount(connection, discount_id)
    merged_kind = raw.get("kind", current["kind"])
    values = discount_values(
        {**raw, "kind": merged_kind} if "value" in raw else raw, partial=True
    )
    if (
        "code" in values
        and values["code"] != current["code"]
        and store.code_taken(connection, values["code"])
    ):
        raise ValueError(f"The code {values['code']} already exists.")
    if "kind" in values and "value" not in values and values["kind"] != current["kind"]:
        raise ValueError("Send the value with a new kind.")
    values["updated_at"] = kit.now()
    kit.update_row(connection, "sh_discounts", discount_id, values)
    connection.commit()
    return store.get_discount(connection, discount_id)


def discount_amount(
    discount: Mapping[str, Any] | None,
    subtotal_cents: int,
    *,
    today: str = "",
    currency: str = "USD",
) -> tuple[int, str]:
    """(cents off, problem). A problem means the code cannot be used on this order."""
    if discount is None or not discount.get("active"):
        return 0, "That discount code is not valid."
    day = today or kit.today()
    if discount.get("expires_on") and discount["expires_on"] < day:
        return 0, "That discount code has expired."
    if discount.get("usage_limit") and int(discount.get("uses") or 0) >= int(
        discount["usage_limit"]
    ):
        return 0, "That discount code has been used up."
    minimum = int(discount.get("min_order_cents") or 0)
    if subtotal_cents < minimum:
        return (
            0,
            f"That code needs an order of at least {kit.fmt_money(minimum, currency)}.",
        )
    if discount["kind"] == "percent":
        off = kit.percent_of(subtotal_cents, int(discount["value"]) * 100)
    else:
        off = int(discount["value"])
    return min(off, subtotal_cents), ""


# ---------------------------------------------------------------------------
# pricing
# ---------------------------------------------------------------------------


def _cart(items: Any) -> list[tuple[str, str, int]]:
    """Merge the requested lines: [(product_id, option, quantity)]."""
    if not isinstance(items, list) or not items:
        raise ValueError("Add at least one product to the order.")
    if len(items) > MAX_LINES:
        raise ValueError(f"An order can have at most {MAX_LINES} lines.")
    merged: dict[tuple[str, str], int] = {}
    for item in items:
        if not isinstance(item, Mapping):
            raise ValueError("Each line needs a product and a quantity.")
        product_id = str(item.get("product_id") or "").strip()
        if not product_id or len(product_id) > 64:
            raise ValueError("Each line needs a product.")
        option = kit.text(item.get("option"), "an option", max_len=60)
        quantity = kit.integer(
            item.get("quantity"),
            "the quantity",
            default=1,
            minimum=1,
            maximum=MAX_QUANTITY,
        )
        key = (product_id, option.lower())
        merged[key] = merged.get(key, 0) + quantity
        if merged[key] > MAX_QUANTITY:
            raise ValueError(f"At most {MAX_QUANTITY} of one product per order.")
    return [
        (product_id, option, quantity)
        for (product_id, option), quantity in merged.items()
    ]


def price(
    connection: sqlite3.Connection,
    items: Any,
    *,
    code: str = "",
    delivery_method: str = "pickup",
    settings: Mapping[str, Any] | None = None,
    business: Mapping[str, Any] | None = None,
    include_drafts: bool = False,
    today: str = "",
) -> dict[str, Any]:
    """The order as the shop prices it. Raises ValueError for anything that cannot be bought."""
    shop = settings if settings is not None else store.settings(connection)
    profile = business if business is not None else kit.business(connection)
    method = kit.choice(
        delivery_method, "the delivery method", DELIVERY_METHODS, default="pickup"
    )
    if method == "pickup" and not shop["pickup_enabled"]:
        raise ValueError("This shop delivers only; choose delivery.")
    if method == "delivery" and not shop["delivery_enabled"]:
        raise ValueError("This shop offers pickup only.")
    lines = []
    subtotal = 0
    for product_id, option, quantity in _cart(items):
        row = kit.fetch_one(
            connection, "SELECT * FROM sh_products WHERE id = ?", (product_id,)
        )
        product = store.decode_product(row) if row else None
        allowed = ("active", "draft") if include_drafts else ("active",)
        if product is None or product["status"] not in allowed:
            raise ValueError(
                "A product in the cart is no longer available. Remove it and try again."
            )
        entry = None
        if product["options"]:
            entry = find_option(product, option) if option else None
            if entry is None:
                raise ValueError(f"Choose an option for {product['name']}.")
        elif option:
            raise ValueError(f"{product['name']} has no options.")
        option_name = entry["name"] if entry else ""
        left = available(product, option_name)
        label = f"{product['name']}{f' ({option_name})' if option_name else ''}"
        if left is not None and left < quantity:
            raise ValueError(
                f"Only {left} left of {label}." if left else f"{label} is sold out."
            )
        unit = unit_price(product, option_name)
        line_cents = unit * quantity
        subtotal += line_cents
        lines.append(
            {
                "product_id": product["id"],
                "name": product["name"],
                "option": option_name,
                "sku": product["sku"],
                "unit_cents": unit,
                "quantity": quantity,
                "line_cents": line_cents,
            }
        )
    discount_code = (code or "").strip().upper()
    discount = None
    referrer = None
    discount_cents = 0
    if discount_code:
        discount = store.discount_by_code(connection, discount_code)
        if discount is None and shop.get("referrals_enabled"):
            referrer = store.referrer_by_code(connection, discount_code)
        if referrer is not None:
            # A friend's referral code: a percent off a new customer's first order (checked when it is placed).
            discount_cents = kit.percent_of(
                subtotal, int(shop.get("referral_friend_percent") or 0) * 100
            )
        else:
            discount_cents, problem = discount_amount(
                discount,
                subtotal,
                today=today,
                currency=str(profile.get("currency") or "USD"),
            )
            if problem:
                raise ValueError(problem)
    goods = subtotal - discount_cents
    delivery_cents = 0
    free_over = int(shop["free_delivery_over_cents"] or 0)
    if method == "delivery":
        delivery_cents = int(shop["delivery_fee_cents"] or 0)
        if free_over and goods >= free_over:
            delivery_cents = 0
    tax_cents = kit.percent_of(goods, int(profile.get("tax_rate_bp") or 0))
    return {
        "lines": lines,
        "subtotal_cents": subtotal,
        "discount_code": discount_code if discount or referrer else "",
        "discount_id": discount["id"] if discount else "",
        "referral": referrer is not None,
        "referrer_id": referrer["id"] if referrer else "",
        "discount_cents": discount_cents,
        "delivery_method": method,
        "delivery_cents": delivery_cents,
        "free_delivery_over_cents": free_over,
        "tax_label": profile.get("tax_label") or "Tax",
        "tax_cents": tax_cents,
        "total_cents": goods + delivery_cents + tax_cents,
        "currency": profile.get("currency") or "USD",
    }


# ---------------------------------------------------------------------------
# orders
# ---------------------------------------------------------------------------


def _take_stock(
    connection: sqlite3.Connection, line: Mapping[str, Any], sign: int
) -> None:
    """Take (sign -1) or give back (sign +1) stock for one line, inside the caller's transaction."""
    row = kit.fetch_one(
        connection, "SELECT * FROM sh_products WHERE id = ?", (line["product_id"],)
    )
    if row is None:
        return
    product = store.decode_product(row)
    if not product["track_stock"]:
        return
    quantity = int(line["quantity"])
    entry = (
        find_option(product, line.get("option") or "") if line.get("option") else None
    )
    if entry is not None and entry.get("stock") is not None:
        options = product["options"]
        for option in options:
            if option["name"] == entry["name"]:
                if sign < 0 and int(option["stock"]) < quantity:
                    raise ValueError(
                        f"Only {option['stock']} left of {product['name']} ({entry['name']})."
                    )
                option["stock"] = int(option["stock"]) + sign * quantity
        connection.execute(
            "UPDATE sh_products SET options = ?, updated_at = ? WHERE id = ?",
            (kit.dumps(options), kit.now(), product["id"]),
        )
        return
    if sign < 0:
        changed = connection.execute(
            "UPDATE sh_products SET stock = stock - ?, updated_at = ? WHERE id = ? AND stock >= ?",
            (quantity, kit.now(), product["id"], quantity),
        ).rowcount
        if not changed:
            raise ValueError(f"Only {product['stock']} left of {product['name']}.")
    else:
        connection.execute(
            "UPDATE sh_products SET stock = stock + ?, updated_at = ? WHERE id = ?",
            (quantity, kit.now(), product["id"]),
        )


def _history(order: Mapping[str, Any], status: str, note: str = "") -> str:
    entries = list(order.get("history") or [])
    entries.append({"status": status, "at": kit.now(), "note": note[:300]})
    return kit.dumps(entries[-100:])


def customer_values(raw: Mapping[str, Any], method: str) -> dict[str, Any]:
    name = kit.text(
        raw.get("name") or raw.get("customer_name"),
        "your name",
        max_len=120,
        required=True,
    )
    email_address = kit.email(
        raw.get("email") or raw.get("customer_email"), "your email"
    )
    phone_number = kit.phone(
        raw.get("phone") or raw.get("customer_phone"), "your phone number"
    )
    if not email_address and not phone_number:
        raise ValueError(
            "Enter an email address or a phone number so the shop can reach you."
        )
    address = kit.text(
        raw.get("address"), "the delivery address", max_len=500, multiline=True
    )
    if method == "delivery" and not address:
        raise ValueError("Enter the delivery address.")
    return {
        "customer_name": name,
        "customer_email": email_address,
        "customer_phone": phone_number,
        "address": address if method == "delivery" else "",
        "note": kit.text(raw.get("note"), "a note", max_len=1000, multiline=True),
    }


def place_order(
    connection: sqlite3.Connection,
    raw: Mapping[str, Any],
    *,
    source: str = "storefront",
    allow_closed: bool = False,
    include_drafts: bool = False,
    origin: str = "",
) -> dict[str, Any]:
    """Price, take stock, count the discount use and number the order in one transaction.

    ``origin`` is the address the request used, for the order link in the confirmation (kit.public_url)."""
    store.ensure(connection)
    shop = store.settings(connection)
    profile = kit.business(connection)
    if not shop["open"] and not allow_closed:
        raise PermissionError(shop["closed_message"])
    method = kit.choice(
        raw.get("delivery_method"),
        "the delivery method",
        DELIVERY_METHODS,
        default="pickup",
    )
    customer = customer_values(raw, method)
    connection.commit()
    connection.execute("BEGIN IMMEDIATE")
    try:
        quote = price(
            connection,
            raw.get("items"),
            code=str(raw.get("discount_code") or ""),
            delivery_method=method,
            settings=shop,
            business=profile,
            include_drafts=include_drafts,
        )
        if quote["referrer_id"]:
            _check_referral(connection, quote["referrer_id"], customer)
        for line in quote["lines"]:
            _take_stock(connection, line, -1)
        if quote["discount_id"]:
            used = connection.execute(
                "UPDATE sh_discounts SET uses = uses + 1 WHERE id = ? AND (usage_limit = 0 OR uses < usage_limit)",
                (quote["discount_id"],),
            ).rowcount
            if not used:
                raise ValueError("That discount code has been used up.")
        order_id = kit.new_id()
        number = store.next_number(connection)
        stamp = kit.now()
        connection.execute(
            """INSERT INTO sh_orders (id, number, source, customer_name, customer_email, customer_phone, delivery_method,
            address, note, items, subtotal_cents, discount_code, discount_cents, delivery_cents, tax_cents, total_cents,
            currency, payment_status, fulfilment_status, history, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'unpaid', 'placed', ?, ?, ?)""",
            (
                order_id,
                number,
                source,
                customer["customer_name"],
                customer["customer_email"],
                customer["customer_phone"],
                method,
                customer["address"],
                customer["note"],
                kit.dumps(quote["lines"]),
                quote["subtotal_cents"],
                quote["discount_code"],
                quote["discount_cents"],
                quote["delivery_cents"],
                quote["tax_cents"],
                quote["total_cents"],
                quote["currency"],
                kit.dumps(
                    [
                        {
                            "status": "placed",
                            "at": stamp,
                            "note": "Placed online"
                            if source == "storefront"
                            else "Entered by the shop",
                        }
                    ]
                ),
                stamp,
                stamp,
            ),
        )
        connection.commit()
    except BaseException:
        connection.rollback()
        raise
    order = store.get_order(connection, order_id)
    kit.log(
        connection,
        APP,
        "order",
        f"Order #{number} from {order['customer_name']}",
        detail=f"{sum(line['quantity'] for line in order['items'])} items",
        ref_type="order",
        ref_id=order_id,
        amount_cents=order["total_cents"],
        public=source == "storefront",
    )
    connection.commit()
    link = public.create_link(
        connection, APP, "order", order_id, title=f"Order #{number}"
    )
    queue_message(
        connection, order, "order_confirmation", link_path=link["path"], origin=origin
    )
    connection.commit()
    return {**order, "link": link}


def order_link(
    connection: sqlite3.Connection, order: Mapping[str, Any]
) -> dict[str, Any]:
    return public.create_link(
        connection, APP, "order", order["id"], title=f"Order #{order['number']}"
    )


def mark_paid(
    connection: sqlite3.Connection, order_id: str, *, note: str = "", origin: str = ""
) -> dict[str, Any]:
    order = store.get_order(connection, order_id)
    if order["payment_status"] == "paid":
        return order
    if order["payment_status"] == "refunded":
        raise ValueError(
            "This order was refunded, so it cannot be paid again. Place a new order instead."
        )
    if order["fulfilment_status"] == "cancelled":
        raise ValueError(
            "This order is cancelled, so it cannot be marked paid. Place a new order instead."
        )
    stamp = kit.now()
    connection.execute(
        "UPDATE sh_orders SET payment_status = 'paid', paid_at = ?, history = ?, updated_at = ? WHERE id = ?",
        (stamp, _history(order, "paid", note or "Payment received"), stamp, order_id),
    )
    kit.record_revenue(
        connection,
        APP,
        source_type="order",
        source_id=order_id,
        amount_cents=int(order["total_cents"]),
        currency_code=order["currency"],
        label=f"Order #{order['number']}",
        customer=order["customer_name"],
    )
    kit.log(
        connection,
        APP,
        "paid",
        f"Order #{order['number']} paid",
        ref_type="order",
        ref_id=order_id,
        amount_cents=int(order["total_cents"]),
    )
    connection.commit()
    updated = store.get_order(connection, order_id)
    referral_on_paid(connection, updated, origin=origin)
    link = public.link_for(connection, APP, "order", order_id)
    queue_message(
        connection,
        updated,
        "payment_receipt",
        link_path=link["path"] if link else "",
        origin=origin,
    )
    connection.commit()
    return updated


def refund(
    connection: sqlite3.Connection, order_id: str, *, note: str = ""
) -> dict[str, Any]:
    order = store.get_order(connection, order_id)
    if order["payment_status"] != "paid":
        raise ValueError("Only a paid order can be refunded.")
    stamp = kit.now()
    connection.execute(
        "UPDATE sh_orders SET payment_status = 'refunded', history = ?, updated_at = ? WHERE id = ?",
        (_history(order, "refunded", note or "Refunded"), stamp, order_id),
    )
    kit.void_revenue(connection, APP, "order", order_id)
    void_referral_reward(connection, order_id)
    kit.log(
        connection,
        APP,
        "refund",
        f"Order #{order['number']} refunded",
        ref_type="order",
        ref_id=order_id,
        amount_cents=-int(order["total_cents"]),
    )
    connection.commit()
    return store.get_order(connection, order_id)


def mark_unpaid(connection: sqlite3.Connection, order_id: str) -> dict[str, Any]:
    """Undo a payment marked by mistake (the revenue line stops counting)."""
    order = store.get_order(connection, order_id)
    if order["payment_status"] != "paid":
        return order
    connection.execute(
        "UPDATE sh_orders SET payment_status = 'unpaid', paid_at = NULL, history = ?, updated_at = ? WHERE id = ?",
        (_history(order, "unpaid", "Payment unmarked"), kit.now(), order_id),
    )
    kit.void_revenue(connection, APP, "order", order_id)
    # No money arrived after all: the reward the sharer got for this order stops working (until it is paid).
    void_referral_reward(connection, order_id)
    connection.commit()
    return store.get_order(connection, order_id)


def cancel(
    connection: sqlite3.Connection, order_id: str, *, note: str = ""
) -> dict[str, Any]:
    """Cancel an order: its stock and discount use come back. A paid order stays paid until you refund it."""
    order = store.get_order(connection, order_id)
    if order["fulfilment_status"] == "cancelled":
        return order
    if order["fulfilment_status"] == "delivered":
        raise ValueError("A delivered order cannot be cancelled; refund it instead.")
    connection.commit()
    connection.execute("BEGIN IMMEDIATE")
    try:
        if not order["restocked"]:
            for line in order["items"]:
                _take_stock(connection, line, +1)
            if order["discount_code"]:
                connection.execute(
                    "UPDATE sh_discounts SET uses = MAX(0, uses - 1) WHERE code = ?",
                    (order["discount_code"],),
                )
        connection.execute(
            "UPDATE sh_orders SET fulfilment_status = 'cancelled', restocked = 1, history = ?, updated_at = ? WHERE id = ?",
            (_history(order, "cancelled", note or "Cancelled"), kit.now(), order_id),
        )
        connection.commit()
    except BaseException:
        connection.rollback()
        raise
    void_referral_reward(connection, order_id)
    kit.log(
        connection,
        APP,
        "cancel",
        f"Order #{order['number']} cancelled",
        ref_type="order",
        ref_id=order_id,
    )
    connection.commit()
    if not order["restocked"]:
        # The stock that came back may be what someone is waiting for.
        for product_id in {line["product_id"] for line in order["items"]}:
            row = kit.fetch_one(
                connection, "SELECT * FROM sh_products WHERE id = ?", (product_id,)
            )
            if row:
                notify_restocked(connection, store.decode_product(row))
    return store.get_order(connection, order_id)


def set_fulfilment(
    connection: sqlite3.Connection,
    order_id: str,
    status: str,
    *,
    tracking_url: str = "",
    note: str = "",
    origin: str = "",
) -> dict[str, Any]:
    order = store.get_order(connection, order_id)
    wanted = kit.choice(
        status, "the status", ("placed", "packed", "shipped", "delivered")
    )
    if order["fulfilment_status"] == "cancelled":
        raise ValueError("This order was cancelled.")
    tracking = (
        kit.url(tracking_url, "the tracking link")
        if tracking_url
        else order["tracking_url"]
    )
    if wanted == order["fulfilment_status"] and tracking == order["tracking_url"]:
        return order
    connection.execute(
        "UPDATE sh_orders SET fulfilment_status = ?, tracking_url = ?, history = ?, updated_at = ? WHERE id = ?",
        (wanted, tracking, _history(order, wanted, note), kit.now(), order_id),
    )
    kit.log(
        connection,
        APP,
        wanted,
        f"Order #{order['number']} {wanted}",
        ref_type="order",
        ref_id=order_id,
    )
    connection.commit()
    updated = store.get_order(connection, order_id)
    if wanted == "shipped" and order["fulfilment_status"] != "shipped":
        link = public.link_for(connection, APP, "order", order_id)
        queue_message(
            connection,
            updated,
            "order_shipped",
            link_path=link["path"] if link else "",
            origin=origin,
        )
        connection.commit()
    return updated


def update_order(
    connection: sqlite3.Connection, order_id: str, raw: Mapping[str, Any]
) -> dict[str, Any]:
    """Change what the shop keeps about an order: internal notes, contact details, the tracking link."""
    store.get_order(connection, order_id)
    values: dict[str, Any] = {}
    if "internal_notes" in raw:
        values["internal_notes"] = kit.text(
            raw.get("internal_notes"), "notes", max_len=4000, multiline=True
        )
    if "customer_email" in raw:
        values["customer_email"] = kit.email(raw.get("customer_email"))
    if "customer_phone" in raw:
        values["customer_phone"] = kit.phone(raw.get("customer_phone"))
    if "address" in raw:
        values["address"] = kit.text(
            raw.get("address"), "the address", max_len=500, multiline=True
        )
    if "tracking_url" in raw:
        values["tracking_url"] = kit.url(raw.get("tracking_url"), "the tracking link")
    if values:
        values["updated_at"] = kit.now()
        kit.update_row(connection, "sh_orders", order_id, values)
        connection.commit()
    return store.get_order(connection, order_id)


def customer_says_paid(
    connection: sqlite3.Connection, order_id: str, raw: Mapping[str, Any]
) -> dict[str, Any]:
    order = store.get_order(connection, order_id)
    if order["payment_status"] == "paid" or order["fulfilment_status"] == "cancelled":
        return order
    reference = kit.text(raw.get("reference"), "a payment reference", max_len=120)
    note = f"Customer says they paid{f' (reference {reference})' if reference else ''}"
    connection.execute(
        "UPDATE sh_orders SET customer_says_paid_at = ?, history = ?, updated_at = ? WHERE id = ?",
        (kit.now(), _history(order, "customer_paid", note), kit.now(), order_id),
    )
    kit.log(
        connection,
        APP,
        "customer_paid",
        f"Order #{order['number']}: {note.lower()}",
        ref_type="order",
        ref_id=order_id,
        public=True,
    )
    connection.commit()
    return store.get_order(connection, order_id)


# ---------------------------------------------------------------------------
# back in stock: customers ask to be told, and are told once when it is back
# ---------------------------------------------------------------------------


def _storefront_url(connection: sqlite3.Connection, origin: Any = "") -> str:
    link = public.link_for(connection, APP, "storefront", "shop")
    if not link:
        return ""
    source = {"base_url": origin} if isinstance(origin, str) else origin
    return kit.public_url(link["path"], source)


def ask_restock_alert(
    connection: sqlite3.Connection, raw: Mapping[str, Any]
) -> dict[str, Any]:
    """A customer on the storefront asks to be emailed when a sold-out product (or one of its options) is back."""
    product_id = str(raw.get("product_id") or "").strip()[:64]
    row = kit.fetch_one(
        connection,
        "SELECT * FROM sh_products WHERE id = ? AND status = 'active'",
        (product_id,),
    )
    if row is None:
        raise LookupError("That product is no longer in the shop.")
    product = store.decode_product(row)
    option = kit.text(raw.get("option"), "an option", max_len=60)
    if product["options"]:
        entry = find_option(product, option) if option else None
        if entry is None:
            raise ValueError(f"Choose which option of {product['name']} you want.")
        option = entry["name"]
    elif option:
        raise ValueError(f"{product['name']} has no options.")
    address = kit.email(raw.get("email"), "your email", required=True)
    name = kit.text(raw.get("name"), "your name", max_len=120)
    left = available(product, option)
    if left is None or left > 0:
        raise ValueError("Good news: it is in stock now. Add it to your cart.")
    existing = kit.fetch_one(
        connection,
        "SELECT * FROM sh_restock_alerts WHERE product_id = ? AND option = ? AND email = ?",
        (product_id, option, address),
    )
    if existing:
        connection.execute(
            "UPDATE sh_restock_alerts SET notified_at = NULL, name = COALESCE(NULLIF(?, ''), name) WHERE id = ?",
            (name, existing["id"]),
        )
        connection.commit()
        return {"waiting": True}
    waiting = kit.count(
        connection,
        "SELECT COUNT(*) FROM sh_restock_alerts WHERE product_id = ? AND notified_at IS NULL",
        (product_id,),
    )
    if waiting >= MAX_WAITING_PER_PRODUCT:
        raise ValueError(
            "The waiting list for this product is full. Please check back soon."
        )
    label = f"{product['name']}{f' ({option})' if option else ''}"
    connection.execute(
        "INSERT INTO sh_restock_alerts (id, product_id, option, email, name, created_at) VALUES (?, ?, ?, ?, ?, ?)",
        (kit.new_id(), product_id, option, address, name, kit.now()),
    )
    kit.log(
        connection,
        APP,
        "restock_alert",
        f"{name or 'A customer'} wants {label} when it is back",
        ref_type="product",
        ref_id=product_id,
        public=True,
    )
    connection.commit()
    return {"waiting": True}


def restock_alerts(
    connection: sqlite3.Connection, product_id: str = ""
) -> list[dict[str, Any]]:
    where, params = "", ()
    if product_id:
        where, params = "WHERE a.product_id = ?", (product_id,)
    return kit.fetch_all(
        connection,
        f"""SELECT a.*, p.name AS product_name FROM sh_restock_alerts a
        JOIN sh_products p ON p.id = a.product_id {where}
        ORDER BY a.notified_at IS NOT NULL, a.created_at LIMIT 2000""",  # noqa: S608
        params,
    )


def waiting_counts(connection: sqlite3.Connection) -> dict[str, int]:
    return {
        row[0]: int(row[1])
        for row in connection.execute(
            "SELECT product_id, COUNT(*) FROM sh_restock_alerts WHERE notified_at IS NULL GROUP BY product_id"
        )
    }


def delete_restock_alert(connection: sqlite3.Connection, alert_id: str) -> None:
    kit.require_row(connection, "sh_restock_alerts", alert_id, "request")
    connection.execute("DELETE FROM sh_restock_alerts WHERE id = ?", (alert_id,))
    connection.commit()


def notify_restocked(
    connection: sqlite3.Connection, product: Mapping[str, Any], *, origin: Any = ""
) -> int:
    """Email everyone waiting for something that is in stock again (once each). Returns how many were told."""
    if product.get("status") != "active":
        return 0
    waiting = kit.fetch_all(
        connection,
        "SELECT * FROM sh_restock_alerts WHERE product_id = ? AND notified_at IS NULL",
        (product["id"],),
    )
    if not waiting:
        return 0
    shop_name = kit.business(connection).get("name") or "our shop"
    url = _storefront_url(connection, origin)
    told = 0
    for alert in waiting:
        left = available(product, alert["option"])
        if left is not None and left <= 0:
            continue
        option = alert["option"]
        label = f"{product['name']}{f' ({option})' if option else ''}"
        first = str(alert["name"] or "").split(" ")[0] or "there"
        few = f", but only {left} left" if left is not None and left <= 5 else ""
        kit.queue_email(
            connection,
            APP,
            "back_in_stock",
            to=alert["email"],
            subject=f"{label} is back - {shop_name}",
            body="\n\n".join(
                part
                for part in (
                    f"Hi {first},",
                    f"Good news: {label} is back in stock{few}.",
                    f"Order it here: {url}" if url else "",
                    f"Thanks,\n{shop_name}",
                )
                if part
            ),
            ref_type="product",
            ref_id=str(product["id"]),
        )
        connection.execute(
            "UPDATE sh_restock_alerts SET notified_at = ? WHERE id = ?",
            (kit.now(), alert["id"]),
        )
        told += 1
    if told:
        kit.log(
            connection,
            APP,
            "restocked",
            f"{product['name']} is back: {told} waiting customer{'s' if told != 1 else ''} told",
            ref_type="product",
            ref_id=str(product["id"]),
        )
    connection.commit()
    return told


# ---------------------------------------------------------------------------
# share and earn: referral codes and rewards
# ---------------------------------------------------------------------------


def _new_code(connection: sqlite3.Connection, prefix: str) -> str:
    stem = "".join(ch for ch in prefix.upper() if "A" <= ch <= "Z")[:8] or "FRIEND"
    for _ in range(30):
        code = f"{stem}-" + "".join(secrets.choice(CODE_ALPHABET) for _ in range(4))
        if not store.code_taken(connection, code):
            return code
    raise RuntimeError("Could not make a code.")


def ensure_referrer(
    connection: sqlite3.Connection, *, email: str, name: str = ""
) -> dict[str, Any] | None:
    """The customer's own share code (one per email), made the first time one of their orders is paid."""
    address = (email or "").strip().lower()
    if not address:
        return None
    found = store.referrer_by_email(connection, address)
    if found:
        return found
    first = (name or address).split(" ")[0].split("@")[0]
    row = {
        "id": kit.new_id(),
        "code": _new_code(connection, first),
        "email": address,
        "name": (name or "")[:120],
        "created_at": kit.now(),
    }
    connection.execute(
        "INSERT INTO sh_referrers (id, code, email, name, created_at) VALUES (:id, :code, :email, :name, :created_at)",
        row,
    )
    return row


def backfill_referrers(connection: sqlite3.Connection) -> int:
    """Give a share code to every customer who has a paid order but none yet (they paid before share and earn
    was turned on). Returns how many codes were made."""
    rows = kit.fetch_all(
        connection,
        """SELECT customer_email, customer_name FROM sh_orders o WHERE payment_status = 'paid' AND customer_email != ''
        AND NOT EXISTS (SELECT 1 FROM sh_referrers r WHERE r.email = LOWER(o.customer_email))
        ORDER BY paid_at LIMIT 5000""",
    )
    made = 0
    for row in rows:
        if not store.referrer_by_email(connection, row["customer_email"].lower()):
            ensure_referrer(
                connection, email=row["customer_email"], name=row["customer_name"]
            )
            made += 1
    connection.commit()
    return made


def _check_referral(
    connection: sqlite3.Connection, referrer_id: str, customer: Mapping[str, Any]
) -> None:
    """A referral code is for a new customer who is not the person who shared it."""
    referrer = kit.fetch_one(
        connection, "SELECT * FROM sh_referrers WHERE id = ?", (referrer_id,)
    )
    address = customer.get("customer_email") or ""
    if not address:
        raise ValueError("Enter your email address to use a friend's code.")
    if referrer and referrer["email"] == address:
        raise ValueError("That is your own code. Share it with friends instead.")
    returning = kit.count(
        connection,
        "SELECT COUNT(*) FROM sh_orders WHERE customer_email = ? AND fulfilment_status != 'cancelled'",
        (address,),
    )
    if returning:
        raise ValueError(
            "A friend's code is for a first order. Thanks for coming back!"
        )


def referral_on_paid(
    connection: sqlite3.Connection, order: Mapping[str, Any], *, origin: Any = ""
) -> dict[str, Any] | None:
    """When an order is paid: the customer gets their own share code, and the friend who referred them earns a
    one-time reward code (once per order). Returns the reward issued, if any."""
    shop = store.settings(connection)
    if not shop["referrals_enabled"]:
        return None
    if order.get("customer_email"):
        ensure_referrer(
            connection, email=order["customer_email"], name=order["customer_name"]
        )
    code = str(order.get("discount_code") or "")
    referrer = store.referrer_by_code(connection, code) if code else None
    reward_cents = int(shop["referral_reward_cents"] or 0)
    withdrawn = kit.fetch_one(
        connection,
        "SELECT * FROM sh_referral_rewards WHERE order_id = ? AND voided_at IS NOT NULL",
        (order["id"],),
    )
    if withdrawn:
        # Marked unpaid by mistake and now paid again: the same reward code works again (sent only once).
        connection.execute(
            "UPDATE sh_discounts SET active = 1, updated_at = ? WHERE code = ? AND uses = 0",
            (kit.now(), withdrawn["reward_code"]),
        )
        connection.execute(
            "UPDATE sh_referral_rewards SET voided_at = NULL WHERE id = ?",
            (withdrawn["id"],),
        )
        connection.commit()
        return None
    already = kit.count(
        connection,
        "SELECT COUNT(*) FROM sh_referral_rewards WHERE order_id = ?",
        (order["id"],),
    )
    if referrer is None or reward_cents <= 0 or already:
        connection.commit()
        return None
    reward_code = _new_code(connection, "THANKS")
    days = int(shop["referral_reward_days"])
    expires = (date.fromisoformat(kit.today()) + timedelta(days=days)).isoformat()
    stamp = kit.now()
    connection.execute(
        """INSERT INTO sh_discounts (id, code, kind, value, min_order_cents, expires_on, usage_limit, uses, active,
        created_at, updated_at) VALUES (?, ?, 'fixed', ?, 0, ?, 1, 0, 1, ?, ?)""",
        (kit.new_id(), reward_code, reward_cents, expires, stamp, stamp),
    )
    reward = {
        "id": kit.new_id(),
        "referrer_id": referrer["id"],
        "order_id": order["id"],
        "friend_name": str(order.get("customer_name") or "")[:120],
        "reward_code": reward_code,
        "amount_cents": reward_cents,
        "created_at": stamp,
    }
    connection.execute(
        """INSERT INTO sh_referral_rewards (id, referrer_id, order_id, friend_name, reward_code, amount_cents,
        created_at) VALUES (:id, :referrer_id, :order_id, :friend_name, :reward_code, :amount_cents, :created_at)""",
        reward,
    )
    currency = str(order.get("currency") or kit.business(connection)["currency"])
    worth = kit.fmt_money(reward_cents, currency)
    friend = reward["friend_name"] or "a friend"
    kit.log(
        connection,
        APP,
        "referral_reward",
        f"{referrer['name'] or referrer['email']} earned {worth} for bringing {friend}",
        ref_type="order",
        ref_id=str(order["id"]),
        amount_cents=0,
    )
    shop_name = kit.business(connection).get("name") or "our shop"
    url = _storefront_url(connection, origin)
    first = str(referrer["name"] or "").split(" ")[0] or "there"
    kit.queue_email(
        connection,
        APP,
        "referral_reward",
        to=referrer["email"],
        subject=f"You earned {worth} at {shop_name}",
        body="\n\n".join(
            part
            for part in (
                f"Hi {first},",
                f"{friend.split(' ')[0]} just shopped with us using your code. Thank you for sharing!",
                f"Here is {worth} off your next order: use the code {reward_code} at checkout (until {expires}).",
                f"Shop here: {url}" if url else "",
                f"Keep sharing your code {referrer['code']}: you earn this every time a new friend orders.",
                f"Thanks,\n{shop_name}",
            )
            if part
        ),
        ref_type="order",
        ref_id=str(order["id"]),
    )
    connection.commit()
    return reward


def void_referral_reward(connection: sqlite3.Connection, order_id: str) -> None:
    """A refunded or cancelled friend's order takes back the reward it earned, if it was not used yet."""
    reward = kit.fetch_one(
        connection,
        "SELECT * FROM sh_referral_rewards WHERE order_id = ? AND voided_at IS NULL",
        (order_id,),
    )
    if not reward:
        return
    used = kit.count(
        connection,
        "SELECT COUNT(*) FROM sh_discounts WHERE code = ? AND uses > 0",
        (reward["reward_code"],),
    )
    if used:
        return
    connection.execute(
        "UPDATE sh_discounts SET active = 0, updated_at = ? WHERE code = ?",
        (kit.now(), reward["reward_code"]),
    )
    connection.execute(
        "UPDATE sh_referral_rewards SET voided_at = ? WHERE id = ?",
        (kit.now(), reward["id"]),
    )


def order_referral(
    connection: sqlite3.Connection, order: Mapping[str, Any]
) -> dict[str, Any] | None:
    """What the customer's order page shows about sharing: their own code, once an order of theirs was paid."""
    shop = store.settings(connection)
    if not shop["referrals_enabled"] or order.get("payment_status") != "paid":
        return None
    referrer = store.referrer_by_email(
        connection, str(order.get("customer_email") or "")
    )
    if not referrer and order.get("customer_email"):
        # Paid before share and earn was turned on: their code is made now.
        referrer = ensure_referrer(
            connection,
            email=str(order["customer_email"]),
            name=str(order.get("customer_name") or ""),
        )
        connection.commit()
    if not referrer:
        return None
    link = public.link_for(connection, APP, "storefront", "shop")
    earned = kit.count(
        connection,
        "SELECT COALESCE(SUM(amount_cents), 0) FROM sh_referral_rewards WHERE referrer_id = ? AND voided_at IS NULL",
        (referrer["id"],),
    )
    return {
        "code": referrer["code"],
        "friend_percent": int(shop["referral_friend_percent"] or 0),
        "reward_cents": int(shop["referral_reward_cents"] or 0),
        "storefront_path": link["path"] if link else "",
        "earned_cents": earned,
    }


def referrals(connection: sqlite3.Connection) -> dict[str, Any]:
    """The share-and-earn report: who shared, the friends they brought, what those friends paid, rewards earned."""
    rows = kit.fetch_all(
        connection, "SELECT * FROM sh_referrers ORDER BY created_at DESC"
    )
    report = []
    totals = {"friends": 0, "sales_cents": 0, "rewards_cents": 0, "rewards_used": 0}
    for row in rows:
        orders = kit.fetch_all(
            connection,
            "SELECT total_cents, payment_status FROM sh_orders WHERE discount_code = ? AND fulfilment_status != 'cancelled'",
            (row["code"],),
        )
        rewards = kit.fetch_all(
            connection,
            """SELECT r.amount_cents, d.uses FROM sh_referral_rewards r LEFT JOIN sh_discounts d
            ON d.code = r.reward_code WHERE r.referrer_id = ? AND r.voided_at IS NULL""",
            (row["id"],),
        )
        entry = {
            "id": row["id"],
            "name": row["name"],
            "email": row["email"],
            "code": row["code"],
            "created_at": row["created_at"],
            "friends": len(orders),
            "sales_cents": sum(
                int(o["total_cents"]) for o in orders if o["payment_status"] == "paid"
            ),
            "rewards": len(rewards),
            "rewards_cents": sum(int(r["amount_cents"]) for r in rewards),
            "rewards_used": sum(1 for r in rewards if int(r["uses"] or 0) > 0),
        }
        for key in ("friends", "sales_cents", "rewards_cents", "rewards_used"):
            totals[key] += entry[key]
        report.append(entry)
    report.sort(
        key=lambda item: (-item["sales_cents"], -item["friends"], item["name"].lower())
    )
    shop = store.settings(connection)
    return {
        "enabled": shop["referrals_enabled"],
        "friend_percent": shop["referral_friend_percent"],
        "reward_cents": shop["referral_reward_cents"],
        "reward_days": shop["referral_reward_days"],
        "referrers": report,
        "totals": {**totals, "referrers": len(report)},
    }


# ---------------------------------------------------------------------------
# messages to customers (the shared outbox)
# ---------------------------------------------------------------------------


def _items_text(order: Mapping[str, Any]) -> str:
    code = order.get("currency") or "USD"
    lines = [
        f"- {line['quantity']} x {line['name']}{f' ({line["option"]})' if line.get('option') else ''}: {kit.fmt_money(int(line['line_cents']), code)}"
        for line in order["items"]
    ]
    lines.append(f"Total: {kit.fmt_money(int(order['total_cents']), code)}")
    return "\n".join(lines)


def queue_message(
    connection: sqlite3.Connection,
    order: Mapping[str, Any],
    kind: str,
    *,
    link_path: str = "",
    origin: str = "",
) -> dict[str, Any] | None:
    if not order.get("customer_email"):
        return None
    profile = kit.business(connection)
    shop = store.settings(connection)
    name = profile.get("name") or "our shop"
    link = kit.public_url(link_path, {"base_url": origin}) if link_path else ""
    first = str(order["customer_name"]).split(" ")[0] or "there"
    number = order["number"]
    if kind == "order_confirmation":
        pay = []
        if profile.get("payment_link"):
            pay.append(f"Pay here: {profile['payment_link']}")
        if profile.get("payment_instructions"):
            pay.append(str(profile["payment_instructions"]))
        if pay:
            pay.append(f"Please use order number {number} as the reference.")
        delivery = (
            f"We will deliver to:\n{order['address']}"
            if order["delivery_method"] == "delivery"
            else f"Pickup: {shop.get('pickup_instructions') or 'we will let you know when it is ready.'}"
        )
        subject = f"Order #{number} received - {name}"
        body = "\n\n".join(
            part
            for part in (
                f"Hi {first},",
                f"Thank you for your order. Here is what you ordered:\n{_items_text(order)}",
                delivery,
                "\n".join(pay),
                f"See your order at any time: {link}" if link else "",
                f"Thanks,\n{name}",
            )
            if part
        )
    elif kind == "payment_receipt":
        subject = f"Payment received for order #{number} - {name}"
        share = order_referral(connection, order)
        share_text = ""
        if share:
            code = order.get("currency") or "USD"
            perks = []
            if share["friend_percent"]:
                perks.append(
                    f"friends get {share['friend_percent']}% off their first order"
                )
            if share["reward_cents"]:
                perks.append(
                    f"you get {kit.fmt_money(share['reward_cents'], code)} off your next order for each friend who buys"
                )
            terms = " and ".join(perks)
            share_text = f"Share and earn: give friends your code {share['code']}." + (
                f" {terms[:1].upper()}{terms[1:]}." if terms else ""
            )
        body = "\n\n".join(
            part
            for part in (
                f"Hi {first},",
                f"We received your payment of {kit.fmt_money(int(order['total_cents']), order['currency'])} for order #{number}. Thank you!",
                share_text,
                f"Your order: {link}" if link else "",
                f"Thanks,\n{name}",
            )
            if part
        )
    elif kind == "order_shipped":
        subject = f"Order #{number} is on its way - {name}"
        body = "\n\n".join(
            part
            for part in (
                f"Hi {first},",
                f"Good news: order #{number} has shipped.",
                f"Track it: {order['tracking_url']}"
                if order.get("tracking_url")
                else "",
                f"Your order: {link}" if link else "",
                f"Thanks,\n{name}",
            )
            if part
        )
    else:
        raise ValueError(f"Unknown message {kind}.")
    return kit.queue_email(
        connection,
        APP,
        kind,
        to=str(order["customer_email"]),
        subject=subject,
        body=body,
        ref_type="order",
        ref_id=str(order["id"]),
    )


# ---------------------------------------------------------------------------
# customers, overview
# ---------------------------------------------------------------------------


def customers(connection: sqlite3.Connection, *, q: str = "") -> list[dict[str, Any]]:
    """Everyone who ordered, merged by email (or phone, or name): orders, paid spend, first and last order."""
    found: dict[str, dict[str, Any]] = {}
    for order in store.list_orders(connection, limit=5000):
        if order["fulfilment_status"] == "cancelled":
            continue
        key = (
            (
                order["customer_email"]
                or order["customer_phone"]
                or order["customer_name"]
            )
            .strip()
            .lower()
        )
        entry = found.setdefault(
            key,
            {
                "name": order["customer_name"],
                "email": order["customer_email"],
                "phone": order["customer_phone"],
                "orders": 0,
                "spend_cents": 0,
                "first_order_at": order["created_at"],
                "last_order_at": order["created_at"],
            },
        )
        entry["orders"] += 1
        if order["payment_status"] == "paid":
            entry["spend_cents"] += int(order["total_cents"])
        entry["first_order_at"] = min(entry["first_order_at"], order["created_at"])
        if order["created_at"] >= entry["last_order_at"]:
            entry["last_order_at"] = order["created_at"]
            entry["name"] = order["customer_name"] or entry["name"]
        entry["email"] = entry["email"] or order["customer_email"]
        entry["phone"] = entry["phone"] or order["customer_phone"]
    rows = list(found.values())
    if q:
        needle = q.strip().lower()
        rows = [
            row
            for row in rows
            if needle in f"{row['name']} {row['email']} {row['phone']}".lower()
        ]
    for row in rows:
        row["repeat"] = row["orders"] > 1
    rows.sort(
        key=lambda row: (-row["spend_cents"], -row["orders"], row["name"].lower())
    )
    return rows


def best_sellers(
    orders: Sequence[Mapping[str, Any]], *, limit: int = 5
) -> list[dict[str, Any]]:
    totals: dict[str, dict[str, Any]] = {}
    for order in orders:
        if order["fulfilment_status"] == "cancelled":
            continue
        for line in order["items"]:
            entry = totals.setdefault(
                line["product_id"],
                {
                    "product_id": line["product_id"],
                    "name": line["name"],
                    "quantity": 0,
                    "revenue_cents": 0,
                },
            )
            entry["quantity"] += int(line["quantity"])
            entry["revenue_cents"] += int(line["line_cents"])
    return sorted(
        totals.values(), key=lambda item: (-item["quantity"], -item["revenue_cents"])
    )[:limit]


def overview(connection: sqlite3.Connection) -> dict[str, Any]:
    orders = store.list_orders(connection, limit=5000)
    live = [order for order in orders if order["fulfilment_status"] != "cancelled"]
    paid = [order for order in live if order["payment_status"] == "paid"]
    month = kit.today()[:7]
    products = store.list_products(connection)
    low = [
        {
            "id": product["id"],
            "name": product["name"],
            "stock": product["stock"],
            "options": [o for o in product["options"] if o.get("stock") is not None],
        }
        for product in products
        if product["status"] == "active" and is_low(product)
    ]
    waiting = waiting_counts(connection)
    names = {product["id"]: product["name"] for product in products}
    wanted = sorted(
        (
            {"id": pid, "name": names[pid], "waiting": count}
            for pid, count in waiting.items()
            if pid in names
        ),
        key=lambda item: (-item["waiting"], item["name"].lower()),
    )[:5]
    return {
        "revenue": kit.revenue(connection, app=APP),
        "wanted": wanted,
        "orders_this_month": sum(
            1 for order in live if order["created_at"][:7] == month
        ),
        "to_fulfil": sum(
            1 for order in live if order["fulfilment_status"] in ("placed", "packed")
        ),
        "unpaid": sum(1 for order in live if order["payment_status"] == "unpaid"),
        "customer_says_paid": sum(
            1
            for order in live
            if order["payment_status"] == "unpaid" and order["customer_says_paid_at"]
        ),
        "average_order_cents": round(
            sum(int(o["total_cents"]) for o in paid) / len(paid)
        )
        if paid
        else 0,
        "best_sellers": best_sellers(live),
        "low_stock": low,
        "recent": orders[:8],
        "products": {
            "active": sum(1 for p in products if p["status"] == "active"),
            "total": len(products),
        },
        "storefront": public.link_for(connection, APP, "storefront", "shop"),
        "settings": store.settings(connection),
    }


# ---------------------------------------------------------------------------
# what customers see
# ---------------------------------------------------------------------------


def public_product(product: Mapping[str, Any]) -> dict[str, Any]:
    left = available(product)
    options = []
    for entry in product.get("options") or []:
        option_left = available(product, entry["name"])
        options.append(
            {
                "name": entry["name"],
                "price_cents": unit_price(product, entry["name"]),
                "available": option_left is None or option_left > 0,
                "left": option_left
                if option_left is not None
                and option_left <= int(product.get("low_stock") or 0)
                else None,
            }
        )
    if options:
        in_stock = any(option["available"] for option in options)
    else:
        in_stock = left is None or left > 0
    return {
        "id": product["id"],
        "name": product["name"],
        "description": product["description"],
        "price_cents": int(product["price_cents"]),
        "compare_at_cents": int(product["compare_at_cents"] or 0),
        "category": product["category"],
        "images": list(product["images"]),
        "options": options,
        "in_stock": in_stock,
        "left": left
        if not options
        and left is not None
        and left <= int(product.get("low_stock") or 0)
        else None,
    }


def storefront(connection: sqlite3.Connection) -> dict[str, Any]:
    shop = store.settings(connection)
    products = [
        public_product(p) for p in store.list_products(connection, status="active")
    ]
    return {
        "open": shop["open"],
        "closed_message": shop["closed_message"] if not shop["open"] else "",
        "notice": shop["notice"],
        "pickup": {
            "enabled": shop["pickup_enabled"],
            "instructions": shop["pickup_instructions"],
        },
        "delivery": {
            "enabled": shop["delivery_enabled"],
            "fee_cents": shop["delivery_fee_cents"],
            "free_over_cents": shop["free_delivery_over_cents"],
        },
        "categories": sorted({p["category"] for p in products if p["category"]}),
        "products": products,
        "has_discounts": shop["referrals_enabled"]
        or kit.count(connection, "SELECT COUNT(*) FROM sh_discounts WHERE active = 1")
        > 0,
        "referral_percent": int(shop["referral_friend_percent"] or 0)
        if shop["referrals_enabled"]
        else 0,
        # a ?ref= link counts even when friends get 0% off: the sharer still earns their reward
        "referrals": bool(shop["referrals_enabled"]),
    }


def public_order(
    order: Mapping[str, Any], referral: Mapping[str, Any] | None = None
) -> dict[str, Any]:
    """An order as its customer sees it from their link (no internal notes); ``referral`` is their share code."""
    steps = ["placed", "packed", "shipped", "delivered"]
    return {
        "number": order["number"],
        "created_at": order["created_at"],
        "customer_name": order["customer_name"],
        "delivery_method": order["delivery_method"],
        "address": order["address"],
        "items": [
            {
                k: line[k]
                for k in ("name", "option", "unit_cents", "quantity", "line_cents")
            }
            for line in order["items"]
        ],
        "subtotal_cents": order["subtotal_cents"],
        "discount_code": order["discount_code"],
        "discount_cents": order["discount_cents"],
        "delivery_cents": order["delivery_cents"],
        "tax_cents": order["tax_cents"],
        "total_cents": order["total_cents"],
        "currency": order["currency"],
        "payment_status": order["payment_status"],
        "fulfilment_status": order["fulfilment_status"],
        "tracking_url": order["tracking_url"],
        "customer_says_paid": bool(order["customer_says_paid_at"]),
        "steps": steps,
        "history": [
            {"status": entry.get("status"), "at": entry.get("at")}
            for entry in order["history"]
            if entry.get("status") in (*steps, "paid", "cancelled", "refunded")
        ],
        "referral": dict(referral) if referral else None,
    }
