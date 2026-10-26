"""Shop routes (``/api/shop/*``), its public pages (``storefront``, ``order``) and its hub entries.

Loaded by :func:`aira.suite.load_apps`. Every handler opens the account's database (the person's own locally, the
organization's hosted); the hosted rules come from :func:`aira_cloud.policy.suite_rules` (GET = ``suite.read``,
others = ``suite.manage``; ``HOSTED_RULES`` marks the price check as a read).
"""

from __future__ import annotations

from typing import Any

from aira.airaapi.http import Raw, Request, aira_route
from aira.suite import kit, public
from aira.suite.shop import service, store
from aira.suite.shop.store import APP

BASE = "/api/shop"
ID = "([0-9a-f]{32})"
STOREFRONT_REF = "shop"  # one storefront per shop

HOSTED_RULES = {"quote": "read"}

PRODUCT_COLUMNS = (
    "name",
    "sku",
    "category",
    "status",
    "price_cents",
    "compare_at_cents",
    "track_stock",
    "stock",
    "low_stock",
    "created_at",
)
ORDER_COLUMNS = (
    "number",
    "created_at",
    "customer_name",
    "customer_email",
    "customer_phone",
    "delivery_method",
    "subtotal_cents",
    "discount_code",
    "discount_cents",
    "delivery_cents",
    "tax_cents",
    "total_cents",
    "currency",
    "payment_status",
    "fulfilment_status",
    "source",
)
CUSTOMER_COLUMNS = (
    "name",
    "email",
    "phone",
    "orders",
    "spend_cents",
    "first_order_at",
    "last_order_at",
)


def _open(request: Request) -> Any:
    connection = request.connect()
    store.ensure(connection)
    return connection


def _csv(rows: list[dict[str, Any]], columns: tuple[str, ...], filename: str) -> Raw:
    return Raw(
        200, kit.csv_bytes(rows, columns), "text/csv; charset=utf-8", filename=filename
    )


def _order_view(connection: Any, order: dict[str, Any]) -> dict[str, Any]:
    return {**order, "link": public.link_for(connection, APP, "order", order["id"])}


# ---------------------------------------------------------------------------
# overview, settings, storefront
# ---------------------------------------------------------------------------


@aira_route("GET", f"{BASE}/overview")
def overview(request: Request) -> tuple[int, dict[str, Any]]:
    with _open(request) as connection:
        return 200, service.overview(connection)


@aira_route("GET", f"{BASE}/settings")
def settings_get(request: Request) -> tuple[int, dict[str, Any]]:
    with _open(request) as connection:
        return 200, {
            "settings": store.settings(connection),
            "business": kit.business(connection),
        }


@aira_route("POST", f"{BASE}/settings")
def settings_save(request: Request) -> tuple[int, dict[str, Any]]:
    with _open(request) as connection:
        payload = dict(request.payload)
        saved = store.save_settings(connection, payload)
        kit.log(
            connection, APP, "settings", "Shop settings updated", actor=_actor(request)
        )
        connection.commit()
    request.audit("shop.settings", "shop", "settings")
    return 200, {"settings": saved}


@aira_route("GET", f"{BASE}/storefront")
def storefront_get(request: Request) -> tuple[int, dict[str, Any]]:
    with _open(request) as connection:
        return 200, {
            "link": public.link_for(connection, APP, "storefront", STOREFRONT_REF),
            "preview": service.storefront(connection),
        }


@aira_route("POST", f"{BASE}/storefront/link")
def storefront_link(request: Request) -> tuple[int, dict[str, Any]]:
    with _open(request) as connection:
        name = kit.business(connection).get("name") or "Shop"
        link = public.create_link(
            connection, APP, "storefront", STOREFRONT_REF, title=name
        )
        kit.log(
            connection,
            APP,
            "share",
            "Storefront link created",
            ref_type="storefront",
            ref_id=STOREFRONT_REF,
        )
        connection.commit()
    request.audit("shop.storefront_link", "storefront", STOREFRONT_REF)
    return 200, {"link": link}


@aira_route("DELETE", f"{BASE}/storefront/link")
def storefront_unlink(request: Request) -> tuple[int, dict[str, Any]]:
    with _open(request) as connection:
        revoked = public.revoke(connection, APP, "storefront", STOREFRONT_REF)
    request.audit("shop.storefront_unlink", "storefront", STOREFRONT_REF)
    return 200, {"revoked": revoked}


# ---------------------------------------------------------------------------
# products
# ---------------------------------------------------------------------------


@aira_route("GET", f"{BASE}/products")
def products_list(request: Request) -> tuple[int, dict[str, Any]] | Raw:
    with _open(request) as connection:
        status = request.q("status")
        if status:
            kit.choice(status, "status", store.PRODUCT_STATUSES)
        rows = store.list_products(
            connection, status=status, q=request.q("q"), category=request.q("category")
        )
        if request.q("format") == "csv":
            return _csv(rows, PRODUCT_COLUMNS, "products.csv")
        for row in rows:
            row["low"] = service.is_low(row)
        return 200, {"products": rows, "categories": store.categories(connection)}


@aira_route("POST", f"{BASE}/products")
def products_create(request: Request) -> tuple[int, dict[str, Any]]:
    with _open(request) as connection:
        product = service.create_product(connection, request.payload)
    request.audit("shop.product_create", "product", product["id"])
    return 201, {"product": product}


@aira_route("POST", f"{BASE}/products/draft")
def products_draft(request: Request) -> tuple[int, dict[str, Any]]:
    return 200, service.draft_description(request.payload)


@aira_route("GET", f"{BASE}/products/{ID}")
def products_get(request: Request) -> tuple[int, dict[str, Any]]:
    with _open(request) as connection:
        product = store.get_product(connection, request.match.group(1))
        product["low"] = service.is_low(product)
        return 200, {"product": product}


@aira_route("PATCH", f"{BASE}/products/{ID}")
def products_update(request: Request) -> tuple[int, dict[str, Any]]:
    with _open(request) as connection:
        product = service.update_product(
            connection, request.match.group(1), request.payload
        )
    request.audit("shop.product_update", "product", product["id"])
    return 200, {"product": product}


@aira_route("DELETE", f"{BASE}/products/{ID}")
def products_delete(request: Request) -> tuple[int, dict[str, Any]]:
    product_id = request.match.group(1)
    with _open(request) as connection:
        result = service.delete_product(connection, product_id)
    request.audit("shop.product_delete", "product", product_id, result)
    return 200, result


# ---------------------------------------------------------------------------
# orders
# ---------------------------------------------------------------------------


@aira_route("GET", f"{BASE}/orders")
def orders_list(request: Request) -> tuple[int, dict[str, Any]] | Raw:
    with _open(request) as connection:
        fulfilment = request.q("fulfilment")
        payment = request.q("payment")
        if fulfilment:
            kit.choice(fulfilment, "the status", store.FULFILMENT_STATUSES)
        if payment:
            kit.choice(payment, "the payment status", store.PAYMENT_STATUSES)
        rows = store.list_orders(
            connection,
            fulfilment=fulfilment,
            payment=payment,
            q=request.q("q"),
            open_only=request.q("open") == "1",
        )
        if request.q("format") == "csv":
            return _csv(rows, ORDER_COLUMNS, "orders.csv")
        return 200, {"orders": rows}


@aira_route("POST", f"{BASE}/quote")
def quote(request: Request) -> tuple[int, dict[str, Any]]:
    payload = request.payload
    with _open(request) as connection:
        return 200, service.price(
            connection,
            payload.get("items"),
            code=str(payload.get("discount_code") or ""),
            delivery_method=str(payload.get("delivery_method") or "pickup"),
            include_drafts=True,
        )


@aira_route("POST", f"{BASE}/orders")
def orders_create(request: Request) -> tuple[int, dict[str, Any]]:
    """An order the shop enters itself (phone, in person): open or closed, drafts allowed, optionally paid."""
    payload = request.payload
    with _open(request) as connection:
        order = service.place_order(
            connection,
            payload,
            source="manual",
            allow_closed=True,
            include_drafts=True,
            origin=request.base_url(),
        )
        if kit.flag(payload.get("mark_paid")):
            order = {
                **service.mark_paid(
                    connection,
                    order["id"],
                    note="Paid when the order was entered",
                    origin=request.base_url(),
                ),
                "link": order["link"],
            }
    request.audit("shop.order_create", "order", order["id"])
    return 201, {"order": order}


@aira_route("GET", f"{BASE}/orders/{ID}")
def orders_get(request: Request) -> tuple[int, dict[str, Any]]:
    with _open(request) as connection:
        return 200, {
            "order": _order_view(
                connection, store.get_order(connection, request.match.group(1))
            )
        }


@aira_route("PATCH", f"{BASE}/orders/{ID}")
def orders_update(request: Request) -> tuple[int, dict[str, Any]]:
    with _open(request) as connection:
        order = service.update_order(
            connection, request.match.group(1), request.payload
        )
        view = _order_view(connection, order)
    request.audit("shop.order_update", "order", order["id"])
    return 200, {"order": view}


def _order_action(request: Request, action: str) -> tuple[int, dict[str, Any]]:
    order_id = request.match.group(1)
    payload = request.payload
    note = kit.text(payload.get("note"), "a note", max_len=300)
    with _open(request) as connection:
        if action == "paid":
            order = service.mark_paid(
                connection, order_id, note=note, origin=request.base_url()
            )
        elif action == "unpaid":
            order = service.mark_unpaid(connection, order_id)
        elif action == "refund":
            order = service.refund(connection, order_id, note=note)
        elif action == "cancel":
            order = service.cancel(connection, order_id, note=note)
        else:
            order = service.set_fulfilment(
                connection,
                order_id,
                str(payload.get("status") or ""),
                tracking_url=str(payload.get("tracking_url") or ""),
                note=note,
                origin=request.base_url(),
            )
        view = _order_view(connection, order)
    request.audit(f"shop.order_{action}", "order", order_id)
    return 200, {"order": view}


@aira_route("POST", f"{BASE}/orders/{ID}/paid")
def orders_paid(request: Request) -> tuple[int, dict[str, Any]]:
    return _order_action(request, "paid")


@aira_route("POST", f"{BASE}/orders/{ID}/unpaid")
def orders_unpaid(request: Request) -> tuple[int, dict[str, Any]]:
    return _order_action(request, "unpaid")


@aira_route("POST", f"{BASE}/orders/{ID}/refund")
def orders_refund(request: Request) -> tuple[int, dict[str, Any]]:
    return _order_action(request, "refund")


@aira_route("POST", f"{BASE}/orders/{ID}/cancel")
def orders_cancel(request: Request) -> tuple[int, dict[str, Any]]:
    return _order_action(request, "cancel")


@aira_route("POST", f"{BASE}/orders/{ID}/fulfil")
def orders_fulfil(request: Request) -> tuple[int, dict[str, Any]]:
    return _order_action(request, "fulfil")


@aira_route("POST", f"{BASE}/orders/{ID}/link")
def orders_link(request: Request) -> tuple[int, dict[str, Any]]:
    with _open(request) as connection:
        link = service.order_link(
            connection, store.get_order(connection, request.match.group(1))
        )
    return 200, {"link": link}


@aira_route("DELETE", f"{BASE}/orders/{ID}/link")
def orders_unlink(request: Request) -> tuple[int, dict[str, Any]]:
    with _open(request) as connection:
        store.get_order(connection, request.match.group(1))
        return 200, {
            "revoked": public.revoke(connection, APP, "order", request.match.group(1))
        }


# ---------------------------------------------------------------------------
# discounts, customers
# ---------------------------------------------------------------------------


@aira_route("GET", f"{BASE}/discounts")
def discounts_list(request: Request) -> tuple[int, dict[str, Any]]:
    with _open(request) as connection:
        return 200, {"discounts": store.list_discounts(connection)}


@aira_route("POST", f"{BASE}/discounts")
def discounts_create(request: Request) -> tuple[int, dict[str, Any]]:
    with _open(request) as connection:
        discount = service.create_discount(connection, request.payload)
    request.audit("shop.discount_create", "discount", discount["id"])
    return 201, {"discount": discount}


@aira_route("PATCH", f"{BASE}/discounts/{ID}")
def discounts_update(request: Request) -> tuple[int, dict[str, Any]]:
    with _open(request) as connection:
        discount = service.update_discount(
            connection, request.match.group(1), request.payload
        )
    request.audit("shop.discount_update", "discount", discount["id"])
    return 200, {"discount": discount}


@aira_route("DELETE", f"{BASE}/discounts/{ID}")
def discounts_delete(request: Request) -> tuple[int, dict[str, Any]]:
    discount_id = request.match.group(1)
    with _open(request) as connection:
        store.get_discount(connection, discount_id)
        connection.execute("DELETE FROM sh_discounts WHERE id = ?", (discount_id,))
        connection.commit()
    request.audit("shop.discount_delete", "discount", discount_id)
    return 200, {"deleted": True}


@aira_route("GET", f"{BASE}/customers")
def customers_list(request: Request) -> tuple[int, dict[str, Any]] | Raw:
    with _open(request) as connection:
        rows = service.customers(connection, q=request.q("q"))
        if request.q("format") == "csv":
            return _csv(rows, CUSTOMER_COLUMNS, "customers.csv")
        return 200, {"customers": rows}


# ---------------------------------------------------------------------------
# public pages
# ---------------------------------------------------------------------------


@public.page(APP, "storefront")
def storefront_page(connection: Any, link: dict[str, Any]) -> dict[str, Any]:
    store.ensure(connection)
    return service.storefront(connection)


def _quote_view(quote: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in quote.items() if key != "discount_id"}


@public.action(APP, "storefront", "quote")
def storefront_quote(
    connection: Any,
    link: dict[str, Any],
    payload: dict[str, Any],
    visitor: dict[str, Any],
) -> tuple[int, dict[str, Any]]:
    store.ensure(connection)
    quote_data = service.price(
        connection,
        payload.get("items"),
        code=str(payload.get("discount_code") or "")[:30],
        delivery_method=str(payload.get("delivery_method") or "pickup"),
    )
    return 200, {"quote": _quote_view(quote_data)}


@public.action(APP, "storefront", "place_order")
def storefront_order(
    connection: Any,
    link: dict[str, Any],
    payload: dict[str, Any],
    visitor: dict[str, Any],
) -> tuple[int, dict[str, Any]]:
    store.ensure(connection)
    allowed = {
        "items",
        "discount_code",
        "delivery_method",
        "name",
        "email",
        "phone",
        "address",
        "note",
    }
    order = service.place_order(
        connection,
        {k: v for k, v in payload.items() if k in allowed},
        source="storefront",
        origin=str(visitor.get("base_url") or ""),
    )
    return 201, {"order": service.public_order(order), "link": order["link"]["path"]}


@public.page(APP, "order")
def order_page(connection: Any, link: dict[str, Any]) -> dict[str, Any]:
    store.ensure(connection)
    return service.public_order(store.get_order(connection, link["ref_id"]))


@public.action(APP, "order", "notify_paid")
def order_notify_paid(
    connection: Any,
    link: dict[str, Any],
    payload: dict[str, Any],
    visitor: dict[str, Any],
) -> tuple[int, dict[str, Any]]:
    store.ensure(connection)
    order = service.customer_says_paid(connection, link["ref_id"], payload)
    return 200, {"order": service.public_order(order)}


# ---------------------------------------------------------------------------
# the Business hub
# ---------------------------------------------------------------------------


@kit.checklist(APP)
def _checklist(connection: Any) -> list[dict[str, Any]]:
    store.ensure(connection)
    return [
        kit.step(
            "business",
            "Add your business details",
            bool(kit.business(connection)["name"]),
            "Name, contact and how customers pay",
        ),
        kit.step(
            "product",
            "Add your first product",
            kit.count(
                connection, "SELECT COUNT(*) FROM sh_products WHERE status = 'active'"
            )
            > 0,
            "Price, photo and stock",
        ),
        kit.step(
            "share",
            "Share your storefront",
            public.link_for(connection, APP, "storefront", STOREFRONT_REF) is not None,
            "Create the link and post it",
        ),
        kit.step(
            "order",
            "Get your first order",
            kit.count(connection, "SELECT COUNT(*) FROM sh_orders") > 0,
            "Send the link to a friend",
        ),
        kit.step(
            "paid",
            "Mark your first order paid",
            kit.count(
                connection,
                "SELECT COUNT(*) FROM sh_orders WHERE payment_status = 'paid'",
            )
            > 0,
            "Revenue shows up in the hub",
        ),
    ]


@kit.summary(APP)
def _summary(connection: Any) -> dict[str, Any]:
    store.ensure(connection)
    data = service.overview(connection)
    code = data["revenue"]["currency"]
    return {
        "cards": [
            {
                "label": "Revenue this month",
                "value": kit.fmt_money(data["revenue"]["this_month_cents"], code),
            },
            {
                "label": "Orders to fulfil",
                "value": data["to_fulfil"],
                "tone": "warn" if data["to_fulfil"] else "",
            },
            {
                "label": "Low stock",
                "value": len(data["low_stock"]),
                "tone": "bad" if data["low_stock"] else "",
            },
        ]
    }


def _actor(request: Request) -> str:
    return str((request.user or {}).get("email") or "")
