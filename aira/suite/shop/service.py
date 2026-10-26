"""Shop rules: product input, pricing (options, discount codes, delivery, tax), stock, orders and customers.

Prices on an order are always worked out here from the stored products, never taken from the customer's
browser. Placing an order is one transaction: stock is checked and taken, the discount use counted and the order
numbered together, so two customers cannot buy the last item twice.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Mapping, Sequence
from typing import Any

from aira.suite import kit, public
from aira.suite.shop import store
from aira.suite.shop.store import APP, amount

MAX_LINES = 50
MAX_QUANTITY = 99
MAX_IMAGES = 8
MAX_OPTIONS = 30
DELIVERY_METHODS = ("pickup", "delivery")


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
    connection: sqlite3.Connection, product_id: str, raw: Mapping[str, Any]
) -> dict[str, Any]:
    current = store.get_product(connection, product_id)
    values = product_values(raw, partial=True)
    price = values.get("price_cents", current["price_cents"])
    if values.get("compare_at_cents") is None and "compare_at_cents" not in values:
        if current["compare_at_cents"] and current["compare_at_cents"] <= price:
            values["compare_at_cents"] = 0
    store.update_product(connection, product_id, values)
    connection.commit()
    return store.get_product(connection, product_id)


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
    if store.discount_by_code(connection, values["code"]):
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
        and store.discount_by_code(connection, values["code"])
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
    discount_cents = 0
    if discount_code:
        discount = store.discount_by_code(connection, discount_code)
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
        "discount_code": discount_code if discount else "",
        "discount_id": discount["id"] if discount else "",
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
    kit.log(
        connection,
        APP,
        "cancel",
        f"Order #{order['number']} cancelled",
        ref_type="order",
        ref_id=order_id,
    )
    connection.commit()
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
        body = "\n\n".join(
            part
            for part in (
                f"Hi {first},",
                f"We received your payment of {kit.fmt_money(int(order['total_cents']), order['currency'])} for order #{number}. Thank you!",
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
    return {
        "revenue": kit.revenue(connection, app=APP),
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
        "has_discounts": kit.count(
            connection, "SELECT COUNT(*) FROM sh_discounts WHERE active = 1"
        )
        > 0,
    }


def public_order(order: Mapping[str, Any]) -> dict[str, Any]:
    """An order as its customer sees it from their link (no internal notes)."""
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
    }
