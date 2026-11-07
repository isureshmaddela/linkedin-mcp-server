"""Shop routes (``/api/shop/*``), its public pages (``storefront``, ``order``) and its hub entries.

Business buyers (B2B) have their own routes: ``/api/shop/trade-accounts`` (apply, approve, terms, codes) and
``/api/shop/receivables`` (what businesses owe); on the storefront they apply and sign in with a trade code.
Delivery zones are ``/api/shop/zones`` (delivery.py) and returns ``/api/shop/returns`` (returns.py); a customer
asks for a return on their order page (action ``request_return``).

Loaded by :func:`aira.suite.load_apps`. Every handler opens the account's database (the person's own locally, the
organization's hosted); the hosted rules come from :func:`monosage_cloud.policy.suite_rules` (GET = ``suite.read``,
others = ``suite.manage``; ``HOSTED_RULES`` marks the price check as a read).
"""

from __future__ import annotations

from typing import Any

from aira.airaapi.http import Raw, Request, aira_route
from aira.suite import background, kit, public
from aira.suite.shop import access, delivery, returns, service, store, trade
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
    "audience",
    "wholesale_cents",
    "min_quantity",
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
    "items_summary",
    "address",
    "note",
    "buyer_type",
    "company",
    "tax_id",
    "po_number",
    "terms",
    "due_on",
)
TRADE_COLUMNS = (
    "company",
    "contact_name",
    "email",
    "phone",
    "tax_id",
    "status",
    "terms",
    "discount_percent",
    "credit_limit_cents",
    "tax_exempt",
    "orders",
    "paid_cents",
    "open_cents",
    "last_order_at",
    "created_at",
)
RECEIVABLE_COLUMNS = (
    "number",
    "company",
    "po_number",
    "terms",
    "due_on",
    "days_late",
    "total_cents",
    "currency",
    "created_at",
)
REFERRER_COLUMNS = (
    "name",
    "email",
    "code",
    "friends",
    "sales_cents",
    "rewards",
    "rewards_cents",
    "rewards_used",
    "created_at",
)
ALERT_COLUMNS = ("product_name", "option", "email", "name", "created_at", "notified_at")
CUSTOMER_COLUMNS = (
    "name",
    "email",
    "phone",
    "orders",
    "spend_cents",
    "first_order_at",
    "last_order_at",
    "buyer_type",
    "company",
)


def _open(request: Request) -> Any:
    connection = access.connect(
        request
    )  # a child's or a teen's household profile is refused
    store.ensure(connection)
    return connection


def _csv(rows: list[dict[str, Any]], columns: tuple[str, ...], filename: str) -> Raw:
    return Raw(
        200, kit.csv_bytes(rows, columns), "text/csv; charset=utf-8", filename=filename
    )


def _order_view(connection: Any, order: dict[str, Any]) -> dict[str, Any]:
    from aira.payments import service as payments

    return {
        **order,
        "link": public.link_for(connection, APP, "order", order["id"]),
        # its payment links (Razorpay): open, paid, refunded, or a mismatch the shop must look at
        "payments": payments.requests_for(connection, APP, "order", order["id"]),
        "returns": returns.for_order(connection, order["id"]),
        "carrier_name": delivery.carrier_name(str(order.get("carrier") or "")),
    }


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
        shop = store.settings(connection)
        return 200, {
            "settings": shop,
            "business": kit.business(connection),
            "carriers": delivery.carriers(shop),
        }


@aira_route("POST", f"{BASE}/settings")
def settings_save(request: Request) -> tuple[int, dict[str, Any]]:
    with _open(request) as connection:
        payload = dict(request.payload)
        saved = store.save_settings(connection, payload)
        if saved["referrals_enabled"]:
            service.backfill_referrers(
                connection
            )  # customers who paid before it was on
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
        waiting = service.waiting_counts(connection)
        for row in rows:
            row["low"] = service.is_low(row)
            row["waiting"] = waiting.get(row["id"], 0)
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
        product["alerts"] = service.restock_alerts(connection, product["id"])
        return 200, {"product": product}


@aira_route("PATCH", f"{BASE}/products/{ID}")
def products_update(request: Request) -> tuple[int, dict[str, Any]]:
    with _open(request) as connection:
        product = service.update_product(
            connection,
            request.match.group(1),
            request.payload,
            origin=request.base_url(),
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
        buyer = request.q("buyer")
        if buyer:
            kit.choice(buyer, "the buyer", store.BUYER_TYPES)
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
            buyer=buyer,
        )
        if request.q("format") == "csv":
            for row in rows:
                row["items_summary"] = "; ".join(
                    f"{line['quantity']} x {line['name']}{f' ({line["option"]})' if line.get('option') else ''}"
                    for line in row["items"]
                )
            return _csv(rows, ORDER_COLUMNS, "orders.csv")
        return 200, {"orders": rows}


def _address(payload: dict[str, Any]) -> dict[str, str]:
    """The parts of a delivery address a zone is chosen by."""
    return {
        key: str(payload.get(key) or "")[:60]
        for key in ("country", "region", "postcode")
    }


def _account_for(connection: Any, payload: dict[str, Any]) -> dict[str, Any] | None:
    """The approved trade account an order the shop enters is for (``trade_account_id``), or None."""
    account_id = str(payload.get("trade_account_id") or "")
    if not account_id:
        return None
    account = trade.get(connection, account_id)
    if account["status"] != "approved":
        raise ValueError(f"{account['company']}'s trade account is not approved.")
    return account


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
            account=_account_for(connection, payload),
            address=_address(payload),
            strict_delivery=False,
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
            account=_account_for(connection, payload),
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
    from aira.payments import service as payments

    # a cancelled order's open payment link is cancelled at Razorpay too
    with _open(request) as connection, payments.secrets(request.secret_store):
        if action == "paid":
            order = service.mark_paid(
                connection, order_id, note=note, origin=request.base_url()
            )
        elif action == "unpaid":
            order = service.mark_unpaid(connection, order_id)
        elif action == "refund":
            order = service.refund(
                connection,
                order_id,
                note=note,
                amount_cents=payload.get("amount_cents"),
                secret_store=request.secret_store,
                actor=_actor(request),
            )
        elif action == "cancel":
            order = service.cancel(connection, order_id, note=note)
        else:
            order = service.set_fulfilment(
                connection,
                order_id,
                str(payload.get("status") or ""),
                tracking_url=str(payload.get("tracking_url") or ""),
                carrier=str(payload.get("carrier") or ""),
                tracking_number=str(payload.get("tracking_number") or ""),
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


@aira_route("POST", f"{BASE}/orders/{ID}/remind")
def orders_remind(request: Request) -> tuple[int, dict[str, Any]]:
    """Email the customer a reminder to pay an unpaid order (a business invoice, usually)."""
    order_id = request.match.group(1)
    with _open(request) as connection:
        order = store.get_order(connection, order_id)
        if (
            order["payment_status"] != "unpaid"
            or order["fulfilment_status"] == "cancelled"
        ):
            raise ValueError("Only an unpaid order needs a reminder.")
        if not order["customer_email"]:
            raise ValueError("This order has no email address to remind.")
        link = public.link_for(
            connection, APP, "order", order_id
        ) or service.order_link(connection, order)
        queued = service.queue_message(
            connection,
            order,
            "payment_reminder",
            link_path=link["path"],
            origin=request.base_url(),
        )
        connection.commit()
    request.audit("shop.order_remind", "order", order_id)
    return 200, {"queued": queued is not None}


# ---------------------------------------------------------------------------
# business buyers: trade accounts and what they owe
# ---------------------------------------------------------------------------


@aira_route("GET", f"{BASE}/trade-accounts")
def trade_list(request: Request) -> tuple[int, dict[str, Any]] | Raw:
    with _open(request) as connection:
        rows = trade.list_accounts(
            connection, status=request.q("status"), q=request.q("q")[:100]
        )
        if request.q("format") == "csv":
            return _csv(rows, TRADE_COLUMNS, "trade-accounts.csv")
        return 200, {
            "accounts": rows,
            "settings": store.settings(connection),
            "sell_to": kit.business(connection)["sell_to"],
            "terms": [
                {"id": terms, "label": trade.terms_label(terms)}
                for terms in store.TRADE_TERMS
            ],
        }


@aira_route("POST", f"{BASE}/trade-accounts")
def trade_create(request: Request) -> tuple[int, dict[str, Any]]:
    """The shop adds a business it knows: approved at once; the code comes back once to pass on."""
    with _open(request) as connection:
        made = trade.create(connection, request.payload, origin=request.base_url())
    request.audit("shop.trade_create", "trade_account", made["account"]["id"])
    return 201, made


@aira_route("GET", f"{BASE}/trade-accounts/{ID}")
def trade_get(request: Request) -> tuple[int, dict[str, Any]]:
    account_id = request.match.group(1)
    with _open(request) as connection:
        account = trade.get(connection, account_id)
        orders = kit.fetch_all(
            connection,
            "SELECT * FROM sh_orders WHERE trade_account_id = ? ORDER BY number DESC LIMIT 100",
            (account_id,),
        )
        return 200, {
            "account": account,
            "credit": trade.credit(connection, account),
            "orders": [store.decode_order(row) for row in orders],
        }


@aira_route("PATCH", f"{BASE}/trade-accounts/{ID}")
def trade_update(request: Request) -> tuple[int, dict[str, Any]]:
    with _open(request) as connection:
        account = trade.update(connection, request.match.group(1), request.payload)
    request.audit("shop.trade_update", "trade_account", account["id"])
    return 200, {"account": account}


@aira_route("POST", f"{BASE}/trade-accounts/{ID}/approve")
def trade_approve(request: Request) -> tuple[int, dict[str, Any]]:
    """Approve with terms (sent along), make the trade code and email it; the code comes back once."""
    account_id = request.match.group(1)
    payload = dict(request.payload)
    payload.pop("status", None)
    with _open(request) as connection:
        if payload:
            trade.update(connection, account_id, payload)
        approved = trade.approve(
            connection,
            account_id,
            origin=request.base_url(),
            send=payload.get("send", True) is not False,
        )
    request.audit("shop.trade_approve", "trade_account", account_id)
    return 200, approved


@aira_route("POST", f"{BASE}/trade-accounts/{ID}/decline")
def trade_decline(request: Request) -> tuple[int, dict[str, Any]]:
    with _open(request) as connection:
        account = trade.decline(connection, request.match.group(1))
    request.audit("shop.trade_decline", "trade_account", account["id"])
    return 200, {"account": account}


@aira_route("POST", f"{BASE}/trade-accounts/{ID}/code")
def trade_new_code(request: Request) -> tuple[int, dict[str, Any]]:
    """A new trade code (the old one stops working), emailed and shown once."""
    with _open(request) as connection:
        made = trade.new_code(
            connection, request.match.group(1), origin=request.base_url()
        )
    request.audit("shop.trade_code", "trade_account", made["account"]["id"])
    return 200, made


@aira_route("DELETE", f"{BASE}/trade-accounts/{ID}")
def trade_delete(request: Request) -> tuple[int, dict[str, Any]]:
    account_id = request.match.group(1)
    with _open(request) as connection:
        result = trade.delete(connection, account_id)
    request.audit("shop.trade_delete", "trade_account", account_id)
    return 200, result


@aira_route("GET", f"{BASE}/receivables")
def receivables(request: Request) -> tuple[int, dict[str, Any]] | Raw:
    """Unpaid business orders, oldest due first, how late each is, and the totals per business."""
    with _open(request) as connection:
        report = trade.receivables(connection)
        if request.q("format") == "csv":
            return _csv(report["orders"], RECEIVABLE_COLUMNS, "receivables.csv")
        return 200, report


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


@aira_route("GET", f"{BASE}/alerts")
def alerts_list(request: Request) -> tuple[int, dict[str, Any]] | Raw:
    """Customers waiting for something sold out to come back (and the ones already told)."""
    with _open(request) as connection:
        rows = service.restock_alerts(connection, request.q("product_id"))
        if request.q("format") == "csv":
            return _csv(rows, ALERT_COLUMNS, "back-in-stock.csv")
        return 200, {"alerts": rows}


@aira_route("DELETE", f"{BASE}/alerts/{ID}")
def alerts_delete(request: Request) -> tuple[int, dict[str, Any]]:
    alert_id = request.match.group(1)
    with _open(request) as connection:
        service.delete_restock_alert(connection, alert_id)
    request.audit("shop.alert_delete", "restock_alert", alert_id)
    return 200, {"deleted": True}


@aira_route("GET", f"{BASE}/referrals")
def referrals_report(request: Request) -> tuple[int, dict[str, Any]] | Raw:
    """Share and earn: who shared their code, the friends they brought and the rewards they earned."""
    with _open(request) as connection:
        report = service.referrals(connection)
        if request.q("format") == "csv":
            return _csv(report["referrers"], REFERRER_COLUMNS, "referrals.csv")
        return 200, report


@aira_route("GET", f"{BASE}/customers")
def customers_list(request: Request) -> tuple[int, dict[str, Any]] | Raw:
    with _open(request) as connection:
        rows = service.customers(connection, q=request.q("q"))
        if request.q("format") == "csv":
            return _csv(rows, CUSTOMER_COLUMNS, "customers.csv")
        return 200, {"customers": rows}


@aira_route("GET", f"{BASE}/search")
def search(request: Request) -> tuple[int, dict[str, Any]]:
    """The search box across the top of Commerce: a few products and orders matching the words (name, SKU,
    category; customer name, email, phone or #number), newest orders first. Nothing for an empty query."""
    with _open(request) as connection:
        q = request.q("q")[:100]
        limit = request.qint("limit", 6, 1, 50)
        if not q:
            return 200, {"q": "", "products": [], "orders": []}
        return 200, {
            "q": q,
            "products": store.list_products(connection, q=q, limit=limit),
            "orders": store.list_orders(connection, q=q, limit=limit),
        }


# ---------------------------------------------------------------------------
# public pages
# ---------------------------------------------------------------------------


@public.page(APP, "storefront")
def storefront_page(connection: Any, link: dict[str, Any]) -> dict[str, Any]:
    store.ensure(connection)
    return service.storefront(connection)


@public.action(APP, "storefront", "trade_sign_in")
def storefront_trade_sign_in(
    connection: Any,
    link: dict[str, Any],
    payload: dict[str, Any],
    visitor: dict[str, Any],
) -> tuple[int, dict[str, Any]]:
    """A business signs in with its email and trade code: the storefront again, at its trade prices."""
    store.ensure(connection)
    account = service.storefront_buyer(
        connection,
        {
            "trade_email": payload.get("email") or "-",
            "trade_code": payload.get("code"),
        },
    )
    return 200, {"page": service.storefront(connection, account)}


@public.action(APP, "storefront", "trade_apply")
def storefront_trade_apply(
    connection: Any,
    link: dict[str, Any],
    payload: dict[str, Any],
    visitor: dict[str, Any],
) -> tuple[int, dict[str, Any]]:
    """A business asks for a trade account (the shop approves it, or it is approved at once)."""
    store.ensure(connection)
    if kit.business(connection)["sell_to"] == "consumers":
        raise PermissionError("This shop does not sell to businesses.")
    allowed = {"company", "contact_name", "email", "phone", "tax_id", "address", "note"}
    result = trade.apply(
        connection,
        {k: v for k, v in payload.items() if k in allowed},
        origin=str(visitor.get("base_url") or ""),
    )
    return 200, result


def _quote_view(quote: dict[str, Any]) -> dict[str, Any]:
    return {
        key: value
        for key, value in quote.items()
        if key not in ("discount_id", "referrer_id")
    }


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
        account=service.storefront_buyer(connection, payload),
        customer_state=str(payload.get("customer_state") or "")[:60],
        address=_address(payload),
        strict_delivery=False,
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
    service.check_form(
        connection, payload
    )  # the hidden field is empty, the form was not filled by a bot
    allowed = {
        "items",
        "discount_code",
        "delivery_method",
        "name",
        "email",
        "phone",
        "address",
        "note",
        "po_number",
        "customer_state",
        "country",
        "region",
        "postcode",
    }
    order = service.place_order(
        connection,
        {k: v for k, v in payload.items() if k in allowed},
        source="storefront",
        origin=str(visitor.get("base_url") or ""),
        account=service.storefront_buyer(connection, payload),
        visitor=visitor,
    )
    from aira.payments import service as payments

    # paying online: the customer goes straight to the payment page, and comes back to the order page
    pay_url = (
        payments.link_on_checkout(connection, APP, "order", order["id"])
        if order["pay_online"]
        else ""
    )
    return 201, {
        "order": service.public_order(order),
        "link": order["link"]["path"],
        "pay_url": pay_url,
    }


@public.action(APP, "storefront", "notify_me")
def storefront_notify_me(
    connection: Any,
    link: dict[str, Any],
    payload: dict[str, Any],
    visitor: dict[str, Any],
) -> tuple[int, dict[str, Any]]:
    """A customer asks to be emailed when a sold-out product is back."""
    store.ensure(connection)
    allowed = {"product_id", "option", "email", "name"}
    result = service.ask_restock_alert(
        connection, {k: v for k, v in payload.items() if k in allowed}
    )
    return 200, result


def _public_order(connection: Any, order_id: str) -> dict[str, Any]:
    order = store.get_order(connection, order_id)
    return {
        **service.public_order(order, service.order_referral(connection, order)),
        "returns": returns.public_view(connection, order),
    }


@public.page(APP, "order")
def order_page(connection: Any, link: dict[str, Any]) -> dict[str, Any]:
    store.ensure(connection)
    return _public_order(connection, link["ref_id"])


@public.action(APP, "order", "notify_paid")
def order_notify_paid(
    connection: Any,
    link: dict[str, Any],
    payload: dict[str, Any],
    visitor: dict[str, Any],
) -> tuple[int, dict[str, Any]]:
    store.ensure(connection)
    service.customer_says_paid(connection, link["ref_id"], payload)
    return 200, {"order": _public_order(connection, link["ref_id"])}


@public.action(APP, "order", "request_return")
def order_request_return(
    connection: Any,
    link: dict[str, Any],
    payload: dict[str, Any],
    visitor: dict[str, Any],
) -> tuple[int, dict[str, Any]]:
    """The customer asks to send items back (within the shop's return window)."""
    store.ensure(connection)
    returns.request(
        connection,
        link["ref_id"],
        {k: payload.get(k) for k in ("items", "reason", "details")},
        origin=str(visitor.get("base_url") or ""),
    )
    return 201, {"order": _public_order(connection, link["ref_id"])}


# ---------------------------------------------------------------------------
# delivery zones
# ---------------------------------------------------------------------------


def _zones_view(connection: Any) -> dict[str, Any]:
    shop = store.settings(connection)
    currency = kit.business(connection)["currency"]
    rows = [
        {**zone, "rate_text": delivery.describe(zone, currency)}
        for zone in delivery.list_zones(connection)
    ]
    fallback = delivery.default_zone(shop)
    return {
        "zones": rows,
        "default": {**fallback, "rate_text": delivery.describe(fallback, currency)},
        "rates": list(store.ZONE_RATES),
    }


@aira_route("GET", f"{BASE}/zones")
def zones_list(request: Request) -> tuple[int, dict[str, Any]]:
    with _open(request) as connection:
        return 200, _zones_view(connection)


@aira_route("POST", f"{BASE}/zones")
def zones_create(request: Request) -> tuple[int, dict[str, Any]]:
    with _open(request) as connection:
        zone = delivery.create_zone(connection, request.payload)
    request.audit("shop.zone_create", "zone", zone["id"])
    return 201, {"zone": zone}


@aira_route("PATCH", f"{BASE}/zones/{ID}")
def zones_update(request: Request) -> tuple[int, dict[str, Any]]:
    with _open(request) as connection:
        zone = delivery.update_zone(connection, request.match.group(1), request.payload)
    request.audit("shop.zone_update", "zone", zone["id"])
    return 200, {"zone": zone}


@aira_route("DELETE", f"{BASE}/zones/{ID}")
def zones_delete(request: Request) -> tuple[int, dict[str, Any]]:
    with _open(request) as connection:
        delivery.delete_zone(connection, request.match.group(1))
    request.audit("shop.zone_delete", "zone", request.match.group(1))
    return 200, {"deleted": True}


# ---------------------------------------------------------------------------
# returns
# ---------------------------------------------------------------------------


@aira_route("GET", f"{BASE}/returns")
def returns_list(request: Request) -> tuple[int, dict[str, Any]]:
    with _open(request) as connection:
        return 200, {
            "returns": returns.list_returns(connection, status=request.q("status")),
            "counts": returns.counts(connection),
        }


def _return_action(request: Request, action: str) -> tuple[int, dict[str, Any]]:
    return_id = request.match.group(1)
    payload = request.payload
    note = str(payload.get("note") or "")
    from aira.payments import service as payments

    # a refund of an order paid online goes back through Razorpay with the business's key secret
    with _open(request) as connection, payments.secrets(request.secret_store):
        if action in ("approve", "reject"):
            entry = returns.decide(
                connection,
                return_id,
                action == "approve",
                note=note,
                actor=_actor(request),
                origin=request.base_url(),
            )
        elif action == "receive":
            entry = returns.receive(
                connection,
                return_id,
                restock=kit.flag(payload.get("restock", True)),
                amount_cents=payload.get("amount_cents"),
                note=note,
                secret_store=request.secret_store,
                actor=_actor(request),
                origin=request.base_url(),
            )
        else:
            entry = returns.refund(
                connection,
                return_id,
                amount_cents=payload.get("amount_cents"),
                note=note,
                secret_store=request.secret_store,
                actor=_actor(request),
                origin=request.base_url(),
            )
        order = _order_view(connection, store.get_order(connection, entry["order_id"]))
    request.audit(f"shop.return_{action}", "return", return_id)
    return 200, {"return": entry, "order": order}


@aira_route("POST", f"{BASE}/returns/{ID}/approve")
def returns_approve(request: Request) -> tuple[int, dict[str, Any]]:
    return _return_action(request, "approve")


@aira_route("POST", f"{BASE}/returns/{ID}/reject")
def returns_reject(request: Request) -> tuple[int, dict[str, Any]]:
    return _return_action(request, "reject")


@aira_route("POST", f"{BASE}/returns/{ID}/receive")
def returns_receive(request: Request) -> tuple[int, dict[str, Any]]:
    return _return_action(request, "receive")


@aira_route("POST", f"{BASE}/returns/{ID}/refund")
def returns_refund(request: Request) -> tuple[int, dict[str, Any]]:
    return _return_action(request, "refund")


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
    summary: dict[str, Any] = {
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
    if data["trade"]["open_cents"]:
        summary["cards"].append(
            {
                "label": "Owed by businesses",
                "value": kit.fmt_money(data["trade"]["open_cents"], code),
                "tone": "bad" if data["trade"]["overdue_cents"] else "warn",
            }
        )
    if data["trade"]["pending"]:
        summary["cards"].append(
            {
                "label": "Trade accounts to approve",
                "value": data["trade"]["pending"],
                "tone": "warn",
            }
        )
    waiting = sum(item["waiting"] for item in data["wanted"])
    if waiting:
        summary["cards"].append(
            {"label": "Waiting for stock", "value": waiting, "tone": "warn"}
        )
    return summary


@background.task(APP)
def _tick(connection: Any) -> dict[str, Any]:
    """Unpaid storefront orders past their time lapse and give their stock back (P0-13)."""
    if not connection.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'sh_orders'"
    ).fetchone():
        return {"lapsed": 0}
    return {"lapsed": service.expire_unpaid(connection)}


def _actor(request: Request) -> str:
    return str((request.user or {}).get("email") or "")
