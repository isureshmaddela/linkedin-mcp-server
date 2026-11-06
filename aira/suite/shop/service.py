"""Shop rules: product input, pricing (options, discount codes, delivery, tax), stock, orders and customers.

Prices on an order are always worked out here from the stored products, never taken from the customer's
browser. A shop sells to consumers (B2C), to businesses (B2B) or both (kit.business()["sell_to"]): a business buyer
signs in with its trade account (trade.py) and pays trade prices (a wholesale price, quantity price breaks and its
own percent off), orders at least a product's minimum quantity, and pays on its terms with a due date. Placing an order is one transaction: stock is checked and taken, the discount use counted and the order
numbered together, so two customers cannot buy the last item twice.
"""

from __future__ import annotations

import secrets
import sqlite3
from collections.abc import Mapping, Sequence
from datetime import date, timedelta
from typing import Any

from aira.suite import kit, public
from aira.suite.shop import store, trade
from aira.suite.shop.store import APP, amount

MAX_LINES = 50
MAX_QUANTITY = 99
MAX_TRADE_QUANTITY = 100_000  # a business buys by the case and the pallet
MAX_PRICE_BREAKS = 10
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


def _price_breaks(raw: Any) -> list[dict[str, int]]:
    """``[{"min_quantity": 10, "unit_cents": 900}]``: from 10, a business pays 9.00 each. Sorted, one per quantity."""
    if raw in (None, ""):
        return []
    if not isinstance(raw, list):
        raise ValueError("Price breaks must be a list.")
    found: dict[int, int] = {}
    for item in raw[: MAX_PRICE_BREAKS + 1]:
        if not isinstance(item, Mapping):
            raise ValueError("Each price break needs a quantity and a price.")
        quantity = kit.integer(
            item.get("min_quantity"),
            "the price break quantity",
            minimum=2,
            maximum=MAX_TRADE_QUANTITY,
        )
        if quantity in found:
            raise ValueError(f"There are two price breaks from {quantity}.")
        found[quantity] = amount(item.get("unit_cents"), "the price break price")
    if len(found) > MAX_PRICE_BREAKS:
        raise ValueError(f"A product can have at most {MAX_PRICE_BREAKS} price breaks.")
    return [
        {"min_quantity": quantity, "unit_cents": found[quantity]}
        for quantity in sorted(found)
    ]


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
    if has("audience"):
        values["audience"] = kit.choice(
            raw.get("audience"), "who it is for", store.AUDIENCES, default="all"
        )
    if has("wholesale_cents"):
        values["wholesale_cents"] = amount(
            raw.get("wholesale_cents"), "the trade price"
        )
    if has("min_quantity"):
        values["min_quantity"] = kit.integer(
            raw.get("min_quantity"),
            "the minimum quantity",
            default=1,
            minimum=1,
            maximum=MAX_TRADE_QUANTITY,
        )
    if has("price_breaks"):
        values["price_breaks"] = _price_breaks(raw.get("price_breaks"))
    if has("hsn_sac"):
        values["hsn_sac"] = kit.hsn_code(raw.get("hsn_sac"))
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


def business_unit_price(
    product: Mapping[str, Any],
    option: str = "",
    quantity: int = 1,
    account: Mapping[str, Any] | None = None,
) -> int:
    """What a business pays for one: the price break its quantity reaches (the quantity of this product across
    options), else the trade price, else the price; an option keeps its difference from the product's price; then
    the account's own percent off."""
    base = int(product["price_cents"])
    trade_base = int(product.get("wholesale_cents") or 0) or base
    for entry in product.get("price_breaks") or []:
        if quantity >= int(entry["min_quantity"]):
            trade_base = int(entry["unit_cents"])
    unit = max(0, trade_base + unit_price(product, option) - base)
    percent = int((account or {}).get("discount_percent") or 0)
    return unit - kit.percent_of(unit, percent * 100) if percent else unit


def buyer_problem(
    product: Mapping[str, Any], account: Mapping[str, Any] | None
) -> str:
    """Why this buyer cannot buy this product ('' = they can)."""
    audience = product.get("audience") or "all"
    if account is not None and audience == "consumers":
        return f"{product['name']} is not sold to businesses."
    if account is None and audience == "businesses":
        return f"{product['name']} is sold to business buyers only. Sign in with your trade account."
    return ""


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


def _cart(items: Any, max_quantity: int = MAX_QUANTITY) -> list[tuple[str, str, int]]:
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
            maximum=max_quantity,
        )
        key = (product_id, option.lower())
        merged[key] = merged.get(key, 0) + quantity
        if merged[key] > max_quantity:
            raise ValueError(f"At most {max_quantity:,} of one product per order.")
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
    account: Mapping[str, Any] | None = None,
    customer_state: str = "",
) -> dict[str, Any]:
    """The order as the shop prices it. Raises ValueError for anything that cannot be bought.

    ``account`` is the business buyer's approved trade account: trade prices, minimum quantities, the business
    minimum order, its terms and (for a reseller) no tax. Without it the order is a consumer's.
    ``customer_state`` (a GST state code or name) picks CGST + SGST or IGST for a business that charges GST; a
    trade account's GSTIN gives its state."""
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
    retail = 0
    cart = _cart(items, MAX_TRADE_QUANTITY if account is not None else MAX_QUANTITY)
    per_product: dict[str, int] = {}
    for product_id, _option, quantity in cart:
        per_product[product_id] = per_product.get(product_id, 0) + quantity
    for product_id, option, quantity in cart:
        row = kit.fetch_one(
            connection, "SELECT * FROM sh_products WHERE id = ?", (product_id,)
        )
        product = store.decode_product(row) if row else None
        allowed = ("active", "draft") if include_drafts else ("active",)
        if product is None or product["status"] not in allowed:
            raise ValueError(
                "A product in the cart is no longer available. Remove it and try again."
            )
        problem = buyer_problem(product, account)
        if problem:
            raise ValueError(problem)
        least = int(product.get("min_quantity") or 1)
        if account is not None and per_product[product_id] < least:
            raise ValueError(
                f"The least a business can order of {product['name']} is {least:,}."
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
        retail_unit = unit_price(product, option_name)
        unit = (
            business_unit_price(product, option_name, per_product[product_id], account)
            if account is not None
            else retail_unit
        )
        line_cents = unit * quantity
        subtotal += line_cents
        retail += retail_unit * quantity
        lines.append(
            {
                "product_id": product["id"],
                "name": product["name"],
                "option": option_name,
                "sku": product["sku"],
                "hsn_sac": str(product.get("hsn_sac") or ""),
                "unit_cents": unit,
                "quantity": quantity,
                "line_cents": line_cents,
            }
        )
    if account is not None and subtotal < int(shop.get("trade_min_order_cents") or 0):
        raise ValueError(
            f"Business orders start at {kit.fmt_money(int(shop['trade_min_order_cents']), str(profile.get('currency') or 'USD'))}."
        )
    discount_code = (code or "").strip().upper()
    discount = None
    referrer = None
    discount_cents = 0
    if discount_code:
        discount = store.discount_by_code(connection, discount_code)
        if discount is None and shop.get("referrals_enabled") and account is None:
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
    tax_exempt = account is not None and bool(account.get("tax_exempt"))
    state = ""
    if kit.gst_applies(profile):
        buyer_gstin = str((account or {}).get("tax_id") or "").upper()
        state = (
            buyer_gstin[:2]
            if kit.valid_gstin(buyer_gstin)
            else kit.state_code(customer_state)
        )
    tax = kit.tax_breakdown(profile, goods, customer_state=state, exempt=tax_exempt)
    tax_cents = tax["tax_cents"]
    terms = str((account or {}).get("terms") or "prepay")
    buyer: dict[str, Any] = {"buyer_type": "consumer"}
    if account is not None:
        buyer = {
            "buyer_type": "business",
            "company": account["company"],
            "terms": terms,
            "terms_label": trade.terms_label(terms),
            "due_on": trade.due_on(terms, today),
            "savings_cents": max(0, retail - subtotal),
            "tax_exempt": tax_exempt,
        }
    return {
        **buyer,
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
        "tax_lines": tax["lines"],
        "prices_include_tax": tax["inclusive"],
        "gst": tax["gst"],
        "customer_state": tax["place_of_supply"] if tax["gst"] else "",
        "total_cents": goods + delivery_cents + tax["added_cents"],
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


def business_customer(
    account: Mapping[str, Any], raw: Mapping[str, Any], method: str
) -> dict[str, Any]:
    """A business order's contact details come from its trade account; the order adds its PO number, a delivery
    address (the account's unless another is given) and a note."""
    address = kit.text(
        raw.get("address"), "the delivery address", max_len=500, multiline=True
    ) or str(account.get("address") or "")
    if method == "delivery" and not address:
        raise ValueError("Enter the delivery address.")
    return {
        "customer_name": str(account.get("contact_name") or account["company"]),
        "customer_email": str(account["email"]),
        "customer_phone": str(account.get("phone") or ""),
        "address": address if method == "delivery" else "",
        "note": kit.text(raw.get("note"), "a note", max_len=1000, multiline=True),
        "po_number": kit.text(
            raw.get("po_number"), "the purchase-order number", max_len=60
        ),
    }


def place_order(
    connection: sqlite3.Connection,
    raw: Mapping[str, Any],
    *,
    source: str = "storefront",
    allow_closed: bool = False,
    include_drafts: bool = False,
    origin: str = "",
    account: Mapping[str, Any] | None = None,
    visitor: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Price, take stock, count the discount use and number the order in one transaction.

    ``origin`` is the address the request used, for the order link in the confirmation (kit.public_url).
    ``account`` makes it a business order: the trade account's contact details, a purchase-order number, its terms
    and due date, checked against its credit limit. ``visitor`` (a public storefront's: ``{"client"}``) caps how
    many orders one email, phone or visitor address places an hour.

    An unpaid storefront order lapses (its stock comes back, :func:`expire_unpaid`): after
    ``unpaid_expiry_online_minutes`` when the customer is sent to pay online, after ``unpaid_expiry_manual_hours``
    otherwise. Its confirmation email goes out when it is paid; one paid by hand gets its confirmation (with how to
    pay) at once, at most :data:`CONFIRMATIONS_PER_ADDRESS_DAY` a day to one address."""
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
    if account is not None:
        customer = business_customer(account, raw, method)
        if shop["trade_require_po"] and not customer["po_number"]:
            raise ValueError("Enter your purchase-order (PO) number.")
    else:
        customer = customer_values(raw, method)
    client_key = ""
    if visitor is not None:
        client_key = _client_key(str(visitor.get("client") or ""))
        _check_order_caps(connection, shop, customer, client_key)
    online = (
        source == "storefront"
        and (account is None or str(account.get("terms") or "prepay") == "prepay")
        and kit.online_ready(kit.payment_settings(connection))
    )
    expires_at = _expiry(shop, source=source, online=online, account=account)
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
            account=account,
            customer_state=str(raw.get("customer_state") or ""),
        )
        if account is not None:
            trade.check_credit(connection, account, quote["total_cents"])
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
        placed_note = (
            "Placed online" if source == "storefront" else "Entered by the shop"
        )
        if account is not None:
            placed_note += f" for {account['company']}" + (
                f" · PO {customer['po_number']}" if customer["po_number"] else ""
            )
            placed_note += f" · {quote['terms_label']}, due {quote['due_on']}"
        connection.execute(
            """INSERT INTO sh_orders (id, number, source, customer_name, customer_email, customer_phone, delivery_method,
            address, note, items, subtotal_cents, discount_code, discount_cents, delivery_cents, tax_cents, total_cents,
            currency, payment_status, fulfilment_status, history, created_at, updated_at, buyer_type, trade_account_id,
            company, tax_id, po_number, terms, due_on)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'unpaid', 'placed', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
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
                kit.dumps([{"status": "placed", "at": stamp, "note": placed_note}]),
                stamp,
                stamp,
                quote["buyer_type"],
                str((account or {}).get("id") or ""),
                str((account or {}).get("company") or ""),
                str((account or {}).get("tax_id") or ""),
                customer.get("po_number", ""),
                quote.get("terms", "prepay"),
                quote.get("due_on", ""),
            ),
        )
        connection.execute(
            """UPDATE sh_orders SET expires_at = ?, pay_online = ?, client_key = ?, customer_state = ?, tax_lines = ?,
            prices_include_tax = ? WHERE id = ?""",
            (
                expires_at if quote["total_cents"] > 0 else "",
                1 if online and quote["total_cents"] > 0 else 0,
                client_key,
                quote["customer_state"],
                kit.dumps(quote["tax_lines"]),
                1 if quote["prices_include_tax"] else 0,
                order_id,
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
        f"Order #{number} from {order['company'] or order['customer_name']}",
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
    if not order["pay_online"] and _may_confirm(connection, order):
        queue_message(
            connection,
            order,
            "order_confirmation",
            link_path=link["path"],
            origin=origin,
        )
    connection.commit()
    return {**order, "link": link}


# ---------------------------------------------------------------------------
# anonymous orders: bot checks, caps, lapsing unpaid orders
# ---------------------------------------------------------------------------

MIN_FILL_SECONDS = 3  # a person takes longer than this to fill in the order form
FORM_TOKEN_HOURS = 24
CONFIRMATIONS_PER_ADDRESS_DAY = 3
ONLINE_GRACE_MINUTES = 10  # a payment started just before the link closed still lands


def form_token(connection: sqlite3.Connection, *, moment: Any = None) -> str:
    """A signed timestamp the storefront hands its order form: the order must come back with it, and not sooner
    than :data:`MIN_FILL_SECONDS` later (a bot posting at once, or without the page, is refused)."""
    import hashlib
    import hmac

    key = kit.get_setting(connection, APP, "form_key", "")
    if not key:
        key = secrets.token_hex(32)
        kit.set_setting(connection, APP, "form_key", key)
        connection.commit()
    stamp = str(int((moment or kit.utcnow()).timestamp()))
    mac = hmac.new(key.encode(), stamp.encode(), hashlib.sha256).hexdigest()[:24]
    return f"{stamp}.{mac}"


def check_form(connection: sqlite3.Connection, payload: Mapping[str, Any]) -> None:
    """The storefront's bot checks: the hidden ``website`` field is empty and the form token is ours and old enough."""
    import hashlib
    import hmac

    if str(payload.get("website") or "").strip():
        raise ValueError("We could not take this order. Please try again.")
    token = str(payload.get("form_token") or "")
    stamp, _, mac = token.partition(".")
    key = str(kit.get_setting(connection, APP, "form_key", "") or "")
    expected = (
        hmac.new(key.encode(), stamp.encode(), hashlib.sha256).hexdigest()[:24]
        if key and stamp.isdigit()
        else ""
    )
    if not expected or not hmac.compare_digest(expected, mac):
        raise ValueError("This page has expired. Reload it and place your order again.")
    age = kit.utcnow().timestamp() - int(stamp)
    if age < MIN_FILL_SECONDS:
        raise ValueError("That was quick! Check your order and press the button again.")
    if age > FORM_TOKEN_HOURS * 3600:
        raise ValueError("This page has expired. Reload it and place your order again.")


def _client_key(client: str) -> str:
    import hashlib

    return hashlib.sha256(f"shop-visitor:{client}".encode()).hexdigest()[:24] if client else ""


def _check_order_caps(
    connection: sqlite3.Connection,
    shop: Mapping[str, Any],
    customer: Mapping[str, Any],
    client_key: str,
) -> None:
    since = kit.iso(kit.utcnow() - timedelta(hours=1))
    per_contact = int(shop["max_orders_per_contact_hour"])
    for column in ("customer_email", "customer_phone"):
        value = str(customer.get(column) or "")
        if value and kit.count(
            connection,
            f"SELECT COUNT(*) FROM sh_orders WHERE {column} = ? AND source = 'storefront' AND created_at >= ?",  # noqa: S608 - fixed names
            (value, since),
        ) >= per_contact:
            raise PermissionError(
                "Too many orders with these contact details in the last hour. Please try again later or contact the shop."
            )
    if client_key and kit.count(
        connection,
        "SELECT COUNT(*) FROM sh_orders WHERE client_key = ? AND created_at >= ?",
        (client_key, since),
    ) >= int(shop["max_orders_per_visitor_hour"]):
        raise PermissionError(
            "Too many orders from this connection in the last hour. Please try again later or contact the shop."
        )


def _expiry(
    shop: Mapping[str, Any],
    *,
    source: str,
    online: bool,
    account: Mapping[str, Any] | None,
) -> str:
    """When an unpaid storefront order lapses ('' = never: entered by the shop, or a business on credit terms)."""
    if source != "storefront":
        return ""
    if account is not None and str(account.get("terms") or "prepay") != "prepay":
        return ""
    if online:
        minutes = int(shop["unpaid_expiry_online_minutes"])
        return kit.iso(kit.utcnow() + timedelta(minutes=minutes)) if minutes else ""
    hours = int(shop["unpaid_expiry_manual_hours"])
    return kit.iso(kit.utcnow() + timedelta(hours=hours)) if hours else ""


def _may_confirm(connection: sqlite3.Connection, order: Mapping[str, Any]) -> bool:
    """A confirmation for an order paid by hand goes to one address at most a few times a day (an order form is
    otherwise a way to email anyone)."""
    address = str(order.get("customer_email") or "")
    if not address or order.get("source") != "storefront":
        return True
    since = kit.iso(kit.utcnow() - timedelta(days=1))
    sent = kit.count(
        connection,
        "SELECT COUNT(*) FROM suite_outbox WHERE app = ? AND kind = 'order_confirmation' AND recipient = ? AND created_at >= ?",
        (APP, address, since),
    )
    return sent < CONFIRMATIONS_PER_ADDRESS_DAY


def expire_unpaid(connection: sqlite3.Connection, *, moment: Any = None) -> int:
    """Lapse unpaid storefront orders past their time: they are cancelled and their stock and discount use come
    back. One sent to pay online gets a few more minutes (a payment started at the last moment still lands); one
    whose customer says they paid waits for the shop to check. Run by the background task."""
    store.ensure(connection)
    now = moment or kit.utcnow()
    rows = kit.fetch_all(
        connection,
        """SELECT id, pay_online, expires_at FROM sh_orders WHERE payment_status = 'unpaid' AND fulfilment_status != 'cancelled'
        AND expires_at != '' AND expires_at <= ? AND customer_says_paid_at IS NULL LIMIT 200""",
        (kit.iso(now),),
    )
    lapsed = 0
    for row in rows:
        if row["pay_online"] and kit.parse_time(row["expires_at"]) > now - timedelta(minutes=ONLINE_GRACE_MINUTES):
            continue
        order = cancel(
            connection,
            row["id"],
            note="Not paid in time: the stock was put back",
            link_status="expired",
        )
        connection.execute(
            "UPDATE sh_orders SET expired_at = ? WHERE id = ?", (kit.now(), order["id"])
        )
        connection.commit()
        lapsed += 1
    return lapsed


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
        "UPDATE sh_orders SET payment_status = 'paid', paid_at = ?, expires_at = '', history = ?, updated_at = ? WHERE id = ?",
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
        customer=order.get("company") or order["customer_name"],
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


def paid_online(
    connection: sqlite3.Connection, order_id: str, paid: Mapping[str, Any]
) -> dict[str, Any]:
    """A payment came in through a payment link (aira.payments). An order that lapsed meanwhile takes its stock
    again and goes on; when the stock has gone, ValueError (the business sees it, and refunds the customer)."""
    order = store.get_order(connection, order_id)
    if order["fulfilment_status"] == "cancelled" and order["expired_at"]:
        connection.commit()
        connection.execute("BEGIN IMMEDIATE")
        try:
            for line in order["items"]:
                _take_stock(connection, line, -1)
            connection.execute(
                "UPDATE sh_orders SET fulfilment_status = 'placed', restocked = 0, expired_at = '', history = ?, updated_at = ? WHERE id = ?",
                (
                    _history(order, "placed", "Paid after it lapsed: back in the queue"),
                    kit.now(),
                    order_id,
                ),
            )
            connection.commit()
        except ValueError as error:
            connection.rollback()
            raise ValueError(f"order #{order['number']} lapsed and {error}") from None
        except BaseException:
            connection.rollback()
            raise
    mark_paid(connection, order_id, note=str(paid.get("note") or ""))
    return {"source_type": "order", "source_id": order_id}


def refund(
    connection: sqlite3.Connection,
    order_id: str,
    *,
    note: str = "",
    amount_cents: Any = None,
    secret_store: Any = None,
    actor: str = "",
) -> dict[str, Any]:
    """Give money back for a paid order: all of it (``amount_cents`` empty) or part.

    Paid online, the refund goes through the payment provider (and :func:`refunded_online` follows when it is
    processed). Paid by hand, the shop has given the money back itself. Either way the ledger gets a negative line
    in this month, and a full refund puts the stock back (an order not yet shipped is cancelled too)."""
    order = store.get_order(connection, order_id)
    if order["payment_status"] != "paid":
        raise ValueError("Only a paid order can be refunded.")
    from aira.payments import service as payments

    online = next(
        (
            request
            for request in payments.requests_for(connection, APP, "order", order_id)
            if request["status"] in ("paid", "partially_refunded")
        ),
        None,
    )
    if online is not None:
        payments.refund(
            connection,
            online["id"],
            amount_cents=amount_cents,
            note=note,
            secret_store=secret_store,
            actor=actor,
        )
        return store.get_order(connection, order_id)
    left = int(order["total_cents"]) - int(order["refunded_cents"] or 0)
    amount = kit.integer(
        amount_cents, "the refund amount (in cents)", default=left, minimum=1
    )
    if amount > left:
        raise ValueError(f"At most {kit.fmt_money(left, order['currency'])} can be refunded.")
    kit.void_revenue(connection, APP, "order", order_id, refund_cents=amount)
    kit.log(
        connection,
        APP,
        "refund",
        f"Order #{order['number']} {'refunded' if amount >= left else 'partly refunded'}",
        ref_type="order",
        ref_id=order_id,
        amount_cents=-amount,
        actor=actor,
    )
    connection.commit()
    return refunded_online(
        connection,
        order_id,
        {"amount_cents": amount, "full": amount >= left, "note": note},
    )


def refunded_online(
    connection: sqlite3.Connection, order_id: str, refund_info: Mapping[str, Any]
) -> dict[str, Any]:
    """Money went back (the ledger line is written by the caller): the order's refund total, and on a full refund
    the stock, the discount use and the referral reward (an order not yet shipped is also cancelled)."""
    order = store.get_order(connection, order_id)
    amount = int(refund_info.get("amount_cents") or 0)
    refunded = min(int(order["total_cents"]), int(order["refunded_cents"] or 0) + amount)
    full = bool(refund_info.get("full")) or refunded >= int(order["total_cents"])
    note = str(refund_info.get("note") or "")
    label = f"Refunded {kit.fmt_money(amount, order['currency'])}" + (f": {note}" if note else "")
    connection.execute(
        "UPDATE sh_orders SET refunded_cents = ?, payment_status = ?, history = ?, updated_at = ? WHERE id = ?",
        (
            refunded,
            "refunded" if full else "paid",
            _history(order, "refunded" if full else "partly_refunded", label),
            kit.now(),
            order_id,
        ),
    )
    connection.commit()
    if full:
        void_referral_reward(connection, order_id)
        connection.commit()
        if not order["restocked"]:
            connection.execute("BEGIN IMMEDIATE")
            try:
                for line in order["items"]:
                    _take_stock(connection, line, +1)
                if order["discount_code"]:
                    connection.execute(
                        "UPDATE sh_discounts SET uses = MAX(0, uses - 1) WHERE code = ?",
                        (order["discount_code"],),
                    )
                cancelled = order["fulfilment_status"] in ("placed", "packed")
                connection.execute(
                    "UPDATE sh_orders SET restocked = 1, fulfilment_status = CASE WHEN ? THEN 'cancelled' ELSE fulfilment_status END WHERE id = ?",
                    (1 if cancelled else 0, order_id),
                )
                connection.commit()
            except BaseException:
                connection.rollback()
                raise
            for product_id in {line["product_id"] for line in order["items"]}:
                row = kit.fetch_one(
                    connection, "SELECT * FROM sh_products WHERE id = ?", (product_id,)
                )
                if row:
                    notify_restocked(connection, store.decode_product(row))
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
    connection: sqlite3.Connection,
    order_id: str,
    *,
    note: str = "",
    link_status: str = "cancelled",
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
    if order["payment_status"] == "unpaid":
        from aira.payments import service as payments

        # its payment link stops here (Razorpay closes it at its own expiry; a late payment shows as a mismatch)
        payments.close_open(connection, APP, "order", order_id, status=link_status)
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
    if "po_number" in raw:
        values["po_number"] = kit.text(
            raw.get("po_number"), "the purchase-order number", max_len=60
        )
    if "due_on" in raw:
        values["due_on"] = kit.day(raw.get("due_on"), "the due date")
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


def _business_terms_text(order: Mapping[str, Any]) -> str:
    parts = [f"Business: {order['company']}"]
    if order.get("po_number"):
        parts.append(f"PO number: {order['po_number']}")
    if order.get("terms") and order["terms"] != "prepay":
        parts.append(
            f"Terms: {trade.terms_label(order['terms'])}. Please pay by {order['due_on']}."
        )
    return "\n".join(parts)


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
    paying = kit.payment_settings(connection)
    if paying["provider"] != "manual":
        # no fixed payment page: Razorpay customers pay on their order page, others by the instructions
        profile = {**profile, "payment_link": ""}
        if kit.online_ready(paying) and link:
            profile["payment_link"] = f"{link} (UPI, cards or netbanking)"
    if kind == "order_confirmation":
        pay = []
        if profile.get("payment_link"):
            pay.append(f"Pay here: {profile['payment_link']}")
        if profile.get("payment_instructions"):
            pay.append(str(profile["payment_instructions"]))
        if pay:
            pay.append(f"Please use order number {number} as the reference.")
        if order.get("buyer_type") == "business":
            pay.insert(0, _business_terms_text(order))
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
                # paid online, this is the order's only confirmation: say what was ordered
                f"Your order:\n{_items_text(order)}" if order.get("pay_online") else "",
                share_text,
                f"Your order: {link}" if link else "",
                f"Thanks,\n{name}",
            )
            if part
        )
    elif kind == "payment_reminder":
        late = bool(order.get("due_on")) and order["due_on"] < kit.today()
        subject = f"{'Overdue' if late else 'Reminder'}: payment for order #{number} - {name}"
        pay = [
            f"Pay here: {profile['payment_link']}" if profile.get("payment_link") else "",
            str(profile.get("payment_instructions") or ""),
        ]
        owed = kit.fmt_money(int(order["total_cents"]), order["currency"])
        po = f" (PO {order['po_number']})" if order.get("po_number") else ""
        when = (
            f"was due on {order['due_on']}"
            if late
            else f"is due on {order['due_on'] or 'receipt'}"
        )
        body = "\n\n".join(
            part
            for part in (
                f"Hi {first},",
                f"This is a reminder that {owed} for order #{number}{po} {when}.",
                "\n".join(part for part in pay if part),
                f"Please use order number {number} as the reference.",
                f"Your order and invoice: {link}" if link else "",
                "If you have already paid, thank you, and please ignore this message.",
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
                "company": order.get("company") or "",
                "buyer_type": order.get("buyer_type") or "consumer",
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
        if order.get("buyer_type") == "business":
            entry["buyer_type"] = "business"
            entry["company"] = order.get("company") or entry["company"]
    rows = list(found.values())
    if q:
        needle = q.strip().lower()
        rows = [
            row
            for row in rows
            if needle in f"{row['name']} {row['email']} {row['phone']} {row['company']}".lower()
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
    split = {
        buyer: {"this_month_cents": 0, "orders_this_month": 0}
        for buyer in store.BUYER_TYPES
    }
    for order in live:
        side = split.get(order.get("buyer_type") or "consumer", split["consumer"])
        if order["created_at"][:7] == month:
            side["orders_this_month"] += 1
        if order["payment_status"] == "paid" and (order["paid_at"] or "")[:7] == month:
            side["this_month_cents"] += int(order["total_cents"])
    owed = trade.receivables(connection)
    return {
        "revenue": kit.revenue(connection, app=APP),
        "sell_to": kit.business(connection)["sell_to"],
        # B2C (consumers) and B2B (businesses) this month: paid sales and orders placed
        "buyers": split,
        "trade": {
            "pending": kit.count(
                connection,
                "SELECT COUNT(*) FROM sh_trade_accounts WHERE status = 'pending'",
            ),
            "approved": kit.count(
                connection,
                "SELECT COUNT(*) FROM sh_trade_accounts WHERE status = 'approved'",
            ),
            "open_cents": owed["open_cents"],
            "overdue_cents": owed["overdue_cents"],
            "overdue_orders": owed["overdue_orders"],
        },
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


def public_product(
    product: Mapping[str, Any],
    account: Mapping[str, Any] | None = None,
    *,
    hide_prices: bool = False,
) -> dict[str, Any]:
    """A product as the storefront shows it: a consumer's prices, a signed-in business's trade prices (at its
    minimum quantity, with the price breaks), or no prices (a shop that sells only to businesses, before sign-in)."""
    left = available(product)
    least = int(product.get("min_quantity") or 1) if account is not None else 1

    def shown(option: str = "") -> int | None:
        if hide_prices:
            return None
        if account is not None:
            return business_unit_price(product, option, least, account)
        return unit_price(product, option)

    options = []
    for entry in product.get("options") or []:
        option_left = available(product, entry["name"])
        options.append(
            {
                "name": entry["name"],
                "price_cents": shown(entry["name"]),
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
    business_view: dict[str, Any] = {}
    if account is not None:
        business_view = {
            "retail_cents": int(product["price_cents"]),
            "min_quantity": least,
            "price_breaks": [
                {
                    "min_quantity": int(entry["min_quantity"]),
                    "unit_cents": business_unit_price(
                        product, "", int(entry["min_quantity"]), account
                    ),
                }
                for entry in product.get("price_breaks") or []
                if int(entry["min_quantity"]) > least
            ],
        }
    return {
        **business_view,
        "id": product["id"],
        "name": product["name"],
        "description": product["description"],
        "price_cents": shown(),
        "compare_at_cents": 0
        if hide_prices or account is not None
        else int(product["compare_at_cents"] or 0),
        "category": product["category"],
        "audience": product.get("audience") or "all",
        "images": list(product["images"]),
        "options": options,
        "in_stock": in_stock,
        "left": left
        if not options
        and left is not None
        and left <= int(product.get("low_stock") or 0)
        else None,
    }


def storefront(
    connection: sqlite3.Connection, account: Mapping[str, Any] | None = None
) -> dict[str, Any]:
    """The storefront page: for a consumer, or for a signed-in business (``account``) with its trade prices."""
    shop = store.settings(connection)
    profile = kit.business(connection)
    sell_to = profile["sell_to"]
    hide = account is None and sell_to == "businesses"
    products = [
        public_product(p, account, hide_prices=hide)
        for p in store.list_products(connection, status="active")
        if not buyer_problem(p, account)
        # a shop that sells only to businesses still shows its catalogue, without prices, before sign-in
        or (hide and (p.get("audience") or "all") == "businesses")
    ]
    return {
        "sell_to": sell_to,
        "business_buyers": {
            "enabled": sell_to != "consumers",
            "only": sell_to == "businesses",
            "approval": shop["trade_approval"],
            "min_order_cents": int(shop["trade_min_order_cents"] or 0),
            "require_po": bool(shop["trade_require_po"]),
            "intro": shop["trade_intro"],
        },
        "account": trade.public_account(connection, account) if account else None,
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
        "has_discounts": (shop["referrals_enabled"] and account is None)
        or kit.count(connection, "SELECT COUNT(*) FROM sh_discounts WHERE active = 1")
        > 0,
        "referral_percent": int(shop["referral_friend_percent"] or 0)
        if shop["referrals_enabled"] and account is None
        else 0,
        # a ?ref= link counts even when friends get 0% off: the sharer still earns their reward
        "referrals": bool(shop["referrals_enabled"]) and account is None,
        # the order form comes back with this (a bot check: see check_form)
        "form_token": form_token(connection),
        "online_payments": kit.online_ready(kit.payment_settings(connection)),
        "pay_within_minutes": int(shop["unpaid_expiry_online_minutes"] or 0),
        "prices_include_tax": bool(profile["prices_include_tax"]),
        # a business that charges GST asks where the customer is (CGST + SGST, or IGST)
        "gst_states": [
            {"code": code, "name": name} for code, name in kit.GST_STATES.items()
        ]
        if kit.gst_applies(profile)
        else [],
        "home_state": str(profile.get("state_code") or "")
        if kit.gst_applies(profile)
        else "",
    }


def storefront_buyer(
    connection: sqlite3.Connection, payload: Mapping[str, Any]
) -> dict[str, Any] | None:
    """The trade account a storefront request signs in with (``trade_email`` and ``trade_code``), None for a
    consumer. Refuses a consumer where the shop sells only to businesses, and a wrong code."""
    sell_to = kit.business(connection)["sell_to"]
    email_address = payload.get("trade_email")
    if email_address or payload.get("trade_code"):
        if sell_to == "consumers":
            raise PermissionError("This shop does not sell to businesses.")
        return trade.sign_in(connection, email_address, payload.get("trade_code"))
    if sell_to == "businesses":
        raise PermissionError(
            "This shop sells to businesses only. Sign in with your trade account, or apply for one."
        )
    return None


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
                **{
                    k: line[k]
                    for k in ("name", "option", "unit_cents", "quantity", "line_cents")
                },
                "hsn_sac": line.get("hsn_sac") or "",
            }
            for line in order["items"]
        ],
        "tax_lines": list(order.get("tax_lines") or []),
        "prices_include_tax": bool(order.get("prices_include_tax")),
        "customer_state": order.get("customer_state") or "",
        "refunded_cents": int(order.get("refunded_cents") or 0),
        # an unpaid storefront order lapses (its stock goes back): the customer sees by when to pay
        "pay_by": (order.get("expires_at") or "")
        if order["payment_status"] == "unpaid"
        else "",
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
        "buyer_type": order.get("buyer_type") or "consumer",
        "company": order.get("company") or "",
        "tax_id": order.get("tax_id") or "",
        "po_number": order.get("po_number") or "",
        "terms": order.get("terms") or "prepay",
        "terms_label": trade.terms_label(order.get("terms") or "prepay"),
        "due_on": order.get("due_on") or "",
        "steps": steps,
        "history": [
            {"status": entry.get("status"), "at": entry.get("at")}
            for entry in order["history"]
            if entry.get("status") in (*steps, "paid", "cancelled", "refunded")
        ],
        "referral": dict(referral) if referral else None,
    }
