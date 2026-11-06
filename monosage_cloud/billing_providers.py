"""Billing providers: a stand-in that behaves like a hosted checkout, Stripe and Razorpay.

All of them turn provider events into the same neutral events (``normalize``) and all of them sign
their webhooks, so the code that handles a payment is the same code in every
environment. The stand-in lets development, tests and demos run the whole
lifecycle (checkout, failure, recovery) without a payment account; Stripe and
Razorpay (plan subscriptions in rupees, see ``docs/billing-razorpay.md``) are the
production providers (see ``docs/multitenant.md``).

Neutral events (``normalize``) carry ``id``, ``type`` and, when the provider says, ``created`` (the provider's time
of the event, used to ignore an older event delivered after a newer one). Types: ``checkout.completed``,
``invoice.paid``, ``invoice.payment_failed``, ``invoice.voided``, ``payment.refunded``, ``subscription.updated``,
``subscription.update_expired`` and ``subscription.deleted``.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence

import base64
import hashlib
import hmac
import json
import logging
import time
import uuid
from typing import Any, Protocol
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from monosage_cloud.config import CloudConfig, Clock
from monosage_cloud.db import ControlDB, dumps, loads, one
from monosage_cloud.errors import Unauthorized, Unavailable, ValidationFailed

logger = logging.getLogger("monosage_cloud.billing_providers")

WEBHOOK_TOLERANCE_SECONDS = 300


class BillingProvider(Protocol):
    name: str

    def create_customer(self, org_id: str, name: str, email: str) -> str: ...

    def create_checkout(
        self,
        *,
        org_id: str,
        customer_id: str,
        plan_id: str,
        plan_name: str,
        seats: int,
        seat_cents: int,
        currency: str,
        success_url: str,
        cancel_url: str,
        checkout_id: str = "",
    ) -> dict[str, Any]: ...

    def expire_checkout(self, session_id: str) -> None:
        """Close a checkout nobody finished, so it cannot be paid any more (a newer one replaced it)."""
        ...

    def update_subscription(
        self,
        subscription_id: str,
        *,
        plan_id: str,
        plan_name: str,
        seats: int,
        seat_cents: int,
        currency: str,
        prorate: bool,
        addons: Sequence[Mapping[str, Any]] = (),
        confirm_payment: bool = False,
        at_cycle_end: bool | None = None,
    ) -> dict[str, Any]:
        """Change the plan, seats and add-ons. ``confirm_payment`` (an upgrade): the change waits for the prorated
        payment; the answer carries ``pending``, ``invoice_id`` and ``match`` (what the confirming event shows)."""
        ...

    def cancel_subscription(
        self, subscription_id: str, *, at_period_end: bool
    ) -> dict[str, Any]: ...

    def portal_url(self, customer_id: str, return_url: str) -> str: ...

    def add_invoice_item(
        self,
        customer_id: str,
        amount_cents: int,
        description: str,
        currency: str,
        *,
        subscription_id: str = "",
        idempotency_key: str = "",
        metadata: Mapping[str, Any] | None = None,
    ) -> str:
        """One charge added to the next invoice of the subscription (overage)."""
        ...

    def charge_now(
        self,
        customer_id: str,
        amount_cents: int,
        description: str,
        currency: str,
        *,
        subscription_id: str = "",
        idempotency_key: str = "",
        metadata: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Bill a one-off amount (an ad top-up) as soon as the provider can: ``{id, item_id, status}`` where status is
        ``paid``, ``open`` (payment attempted, not confirmed yet) or ``scheduled`` (charged with the next cycle). The
        money counts only when ``invoice.paid`` for ``id`` arrives."""
        ...

    def verify_webhook(self, payload: bytes, signature: str) -> dict[str, Any]: ...

    def normalize(self, event: dict[str, Any]) -> dict[str, Any] | None: ...


def _kv_get(db: ControlDB | None, key: str) -> Any:
    if db is None:
        return None
    with db.read() as connection:
        row = one(connection, "SELECT value FROM cloud_kv WHERE key = ?", (key,))
    return loads(row["value"], None) if row else None


def _kv_put(db: ControlDB | None, key: str, value: Any) -> None:
    if db is None:
        return
    with db.write() as connection:
        connection.execute(
            "INSERT INTO cloud_kv (key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (key, dumps(value)),
        )


def sign(secret: str, payload: bytes, timestamp: int) -> str:
    digest = hmac.new(
        secret.encode("utf-8"), f"{timestamp}.".encode() + payload, hashlib.sha256
    ).hexdigest()
    return f"t={timestamp},v1={digest}"


def check_signature(secret: str, payload: bytes, header: str, now: int) -> None:
    """Stripe's scheme: ``t=<unix>,v1=<hmac_sha256(secret, "<t>." + payload)>`` within five minutes."""
    try:
        parts = dict(item.split("=", 1) for item in header.split(","))
        timestamp = int(parts["t"])
        candidates = [
            value
            for key, value in (item.split("=", 1) for item in header.split(","))
            if key == "v1"
        ]
    except (KeyError, ValueError):
        raise Unauthorized(
            "The webhook signature is malformed.", code="bad_signature"
        ) from None
    if abs(now - timestamp) > WEBHOOK_TOLERANCE_SECONDS:
        raise Unauthorized(
            "The webhook timestamp is outside the allowed window.",
            code="stale_signature",
        )
    expected = hmac.new(
        secret.encode("utf-8"), f"{timestamp}.".encode() + payload, hashlib.sha256
    ).hexdigest()
    if not any(hmac.compare_digest(expected, candidate) for candidate in candidates):
        raise Unauthorized(
            "The webhook signature does not match.", code="bad_signature"
        )


# --------------------------------------------------------------------------- #
# Stand-in
# --------------------------------------------------------------------------- #


class StandInProvider:
    """A provider that lives in this process: hosted-checkout page, signed webhooks, no money moves."""

    name = "standin"

    def __init__(
        self, db: ControlDB, clock: Clock, config: CloudConfig, secret: str
    ) -> None:
        self.db = db
        self.clock = clock
        self.config = config
        self.secret = secret

    def _put(self, key: str, value: dict[str, Any]) -> None:
        with self.db.write() as connection:
            connection.execute(
                "INSERT INTO cloud_kv (key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                (key, dumps(value)),
            )

    def _get(self, key: str) -> dict[str, Any] | None:
        with self.db.read() as connection:
            row = one(connection, "SELECT value FROM cloud_kv WHERE key = ?", (key,))
        return loads(row["value"], None) if row else None

    def create_customer(self, org_id: str, name: str, email: str) -> str:
        return f"cus_standin_{org_id[:12]}"

    def create_checkout(
        self,
        *,
        org_id: str,
        customer_id: str,
        plan_id: str,
        plan_name: str,
        seats: int,
        seat_cents: int,
        currency: str,
        success_url: str,
        cancel_url: str,
        checkout_id: str = "",
    ) -> dict[str, Any]:
        session_id = f"cs_standin_{uuid.uuid4().hex[:20]}"
        self._put(
            f"standin:checkout:{session_id}",
            {
                "org_id": org_id,
                "customer_id": customer_id,
                "plan_id": plan_id,
                "plan_name": plan_name,
                "seats": seats,
                "amount_cents": seats * seat_cents,
                "currency": currency,
                "success_url": success_url,
                "cancel_url": cancel_url,
                "status": "open",
            },
        )
        return {
            "id": session_id,
            "url": f"{self.config.base_url}/billing/standin/checkout/{session_id}",
        }

    def checkout_session(self, session_id: str) -> dict[str, Any] | None:
        return self._get(f"standin:checkout:{session_id}")

    def mark_checkout(self, session_id: str, status: str) -> None:
        session = self._get(f"standin:checkout:{session_id}")
        if session:
            session["status"] = status
            self._put(f"standin:checkout:{session_id}", session)

    def expire_checkout(self, session_id: str) -> None:
        session = self._get(f"standin:checkout:{session_id}")
        if session and session.get("status") == "open":
            self.mark_checkout(session_id, "expired")

    def update_subscription(
        self,
        subscription_id: str,
        *,
        plan_id: str,
        plan_name: str,
        seats: int,
        seat_cents: int,
        currency: str,
        prorate: bool,
        addons: Sequence[Mapping[str, Any]] = (),
        confirm_payment: bool = False,
        at_cycle_end: bool | None = None,
    ) -> dict[str, Any]:
        lines = [
            {
                "app_id": str(a["app_id"]),
                "seats": int(a["seats"]),
                "amount_cents": int(a["seats"]) * int(a["seat_cents"]),
            }
            for a in addons
        ]
        record = {
            "id": subscription_id,
            "plan_id": plan_id,
            "seats": seats,
            "amount_cents": seats * seat_cents + sum(a["amount_cents"] for a in lines),
            "addons": lines,
            "prorate": prorate,
        }
        self._put(f"standin:subscription:{subscription_id}", record)
        if confirm_payment:
            # the prorated charge counts once Billing delivers this invoice's invoice.paid (no money moves)
            return {
                **record,
                "pending": True,
                "invoice_id": f"in_standin_{uuid.uuid4().hex[:16]}",
            }
        return record

    def subscription_record(self, subscription_id: str) -> dict[str, Any] | None:
        """What the stand-in last applied to a subscription (tests and the reconcile report read it)."""
        return self._get(f"standin:subscription:{subscription_id}")

    def cancel_subscription(
        self, subscription_id: str, *, at_period_end: bool
    ) -> dict[str, Any]:
        return {"id": subscription_id, "cancel_at_period_end": at_period_end}

    def portal_url(self, customer_id: str, return_url: str) -> str:
        return f"{self.config.base_url}/billing/standin/portal/{customer_id}"

    def add_invoice_item(
        self,
        customer_id: str,
        amount_cents: int,
        description: str,
        currency: str,
        *,
        subscription_id: str = "",
        idempotency_key: str = "",
        metadata: Mapping[str, Any] | None = None,
    ) -> str:
        return f"ii_standin_{uuid.uuid4().hex[:12]}"

    def charge_now(
        self,
        customer_id: str,
        amount_cents: int,
        description: str,
        currency: str,
        *,
        subscription_id: str = "",
        idempotency_key: str = "",
        metadata: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        """An invoice "charged" at once; Billing delivers its invoice.paid (the stand-in's card always works)."""
        return {
            "id": f"in_standin_{uuid.uuid4().hex[:16]}",
            "item_id": f"ii_standin_{uuid.uuid4().hex[:12]}",
            "status": "open",
        }

    # -- events ----------------------------------------------------------------------

    def make_event(self, event_type: str, data: dict[str, Any]) -> tuple[bytes, str]:
        """A signed event, exactly as a provider would deliver it."""
        payload = json.dumps(
            {
                "id": f"evt_standin_{uuid.uuid4().hex[:16]}",
                "type": event_type,
                "created": self.clock.now(),
                "data": data,
            }
        ).encode()
        return payload, sign(self.secret, payload, self.clock.now())

    def verify_webhook(self, payload: bytes, signature: str) -> dict[str, Any]:
        check_signature(self.secret, payload, signature, self.clock.now())
        try:
            return json.loads(payload)
        except ValueError:
            raise ValidationFailed("The webhook body is not JSON.") from None

    def normalize(self, event: dict[str, Any]) -> dict[str, Any] | None:
        return {
            "id": event["id"],
            "type": event["type"],
            "created": event.get("created"),
            **event["data"],
        }


# --------------------------------------------------------------------------- #
# Stripe
# --------------------------------------------------------------------------- #


class StripeProvider:
    name = "stripe"

    def __init__(
        self, config: CloudConfig, clock: Clock, db: ControlDB | None = None
    ) -> None:
        self.config = config
        self.clock = clock
        self.db = db  # caches the Price objects upgrades use, and which product is which plan
        self._prices: dict[str, str] = {}

    def _call(
        self,
        method: str,
        path: str,
        params: dict[str, Any] | None = None,
        *,
        idempotency_key: str = "",
    ) -> dict[str, Any]:
        """One API request. Every POST carries an ``Idempotency-Key`` (the caller's, derived from what it bills, or
        a fresh one), so the single retry after a dropped connection cannot create a second object; every request
        names the pinned ``Stripe-Version``."""
        if not self.config.stripe_secret_key:
            raise Unavailable(
                "Billing is not configured. Set STRIPE_SECRET_KEY.",
                code="billing_not_configured",
            )
        body = (
            urlencode(_flatten(params or {})).encode()
            if method != "GET" and params
            else None
        )
        url = f"{self.config.stripe_api_base.rstrip('/')}{path}"
        if method == "GET" and params:
            url += "?" + urlencode(_flatten(params))
        headers = {
            "Authorization": f"Bearer {self.config.stripe_secret_key}",
            "Content-Type": "application/x-www-form-urlencoded",
        }
        if self.config.stripe_api_version:
            headers["Stripe-Version"] = self.config.stripe_api_version
        if method == "POST":
            headers["Idempotency-Key"] = (
                idempotency_key or f"aira-{uuid.uuid4().hex}"
            )[:255]
        for attempt in (1, 2):
            request = Request(url, data=body, method=method, headers=headers)
            try:
                with urlopen(request, timeout=20) as response:  # noqa: S310 - the base URL is operator configuration
                    return json.loads(response.read().decode("utf-8"))
            except HTTPError as error:
                detail = error.read().decode("utf-8", "replace")[:300]
                raise Unavailable(
                    f"The billing provider refused the request ({error.code}).",
                    detail=detail,
                    provider_status=error.code,
                ) from None
            except (URLError, TimeoutError) as error:
                if attempt == 1 and method in {"POST", "GET"}:
                    continue  # the same Idempotency-Key: Stripe answers with the first result
                raise Unavailable(
                    "The billing provider could not be reached.",
                    detail=str(error)[:200],
                ) from None
        raise Unavailable("The billing provider could not be reached.")

    def create_customer(self, org_id: str, name: str, email: str) -> str:
        return str(
            self._call(
                "POST",
                "/v1/customers",
                {"name": name, "email": email, "metadata": {"org_id": org_id}},
            )["id"]
        )

    def _price(
        self, plan_name: str, seats: int, seat_cents: int, currency: str
    ) -> dict[str, Any]:
        return {
            "price_data": {
                "currency": currency,
                "unit_amount": seat_cents,
                "recurring": {"interval": "month"},
                "product_data": {"name": f"Monosage {plan_name} (per candidate seat)"},
            },
            "quantity": seats,
        }

    def _addon_item(
        self, addon: Mapping[str, Any], currency: str, product: str | None = None
    ) -> dict[str, Any]:
        """One subscription item per app add-on; ``metadata.app_id`` is how a later update finds it."""
        price_data: dict[str, Any] = {
            "currency": currency,
            "unit_amount": int(addon["seat_cents"]),
            "recurring": {"interval": "month"},
        }
        if product:
            price_data["product"] = product
        else:
            price_data["product_data"] = {
                "name": f"Monosage {addon['name']} add-on (per candidate seat)"
            }
        return {
            "price_data": price_data,
            "quantity": int(addon["seats"]),
            "metadata": {"app_id": str(addon["app_id"])},
        }

    def _product(self, name: str, metadata: Mapping[str, Any]) -> str:
        product = str(
            self._call(
                "POST", "/v1/products", {"name": name, "metadata": dict(metadata)}
            )["id"]
        )
        _kv_put(self.db, f"stripe:product:{product}", dict(metadata))
        return product

    def _price_id(
        self, name: str, metadata: Mapping[str, Any], cents: int, currency: str
    ) -> str:
        """A Price object (pending updates accept ``items[].price``, not ``price_data``), made once per amount."""
        key = f"stripe:price:{dumps(dict(metadata))}:{int(cents)}:{currency}"
        cached = self._prices.get(key) or _kv_get(self.db, key)
        if cached:
            self._prices[key] = str(cached)
            return str(cached)
        product = self._product(name, metadata)
        price = str(
            self._call(
                "POST",
                "/v1/prices",
                {
                    "currency": currency,
                    "unit_amount": int(cents),
                    "recurring": {"interval": "month"},
                    "product": product,
                    "metadata": dict(metadata),
                },
                idempotency_key=f"aira-price-{hashlib.sha256(key.encode()).hexdigest()[:40]}",
            )["id"]
        )
        self._prices[key] = price
        _kv_put(self.db, key, price)
        return price

    def create_checkout(
        self,
        *,
        org_id: str,
        customer_id: str,
        plan_id: str,
        plan_name: str,
        seats: int,
        seat_cents: int,
        currency: str,
        success_url: str,
        cancel_url: str,
        checkout_id: str = "",
    ) -> dict[str, Any]:
        params: dict[str, Any] = {
            "mode": "subscription",
            "customer": customer_id,
            "success_url": success_url,
            "cancel_url": cancel_url,
            "client_reference_id": org_id,
            "line_items": [self._price(plan_name, seats, seat_cents, currency)],
            "metadata": {"org_id": org_id, "plan_id": plan_id, "seats": seats},
            "subscription_data": {
                "metadata": {"org_id": org_id, "plan_id": plan_id, "seats": seats}
            },
        }
        if checkout_id:
            params["metadata"]["checkout_id"] = checkout_id
        if self.config.stripe_tax:
            # Stripe Tax works out the tax from the customer's address and collects business tax IDs (VAT, GST)
            params["automatic_tax"] = {"enabled": True}
            params["tax_id_collection"] = {"enabled": True}
            params["customer_update"] = {"address": "auto", "name": "auto"}
        session = self._call(
            "POST",
            "/v1/checkout/sessions",
            params,
            idempotency_key=f"aira-checkout-{checkout_id}" if checkout_id else "",
        )
        return {"id": session["id"], "url": session["url"]}

    def expire_checkout(self, session_id: str) -> None:
        self._call(
            "POST",
            f"/v1/checkout/sessions/{session_id}/expire",
            idempotency_key=f"aira-expire-{session_id}",
        )

    def update_subscription(
        self,
        subscription_id: str,
        *,
        plan_id: str,
        plan_name: str,
        seats: int,
        seat_cents: int,
        currency: str,
        prorate: bool,
        addons: Sequence[Mapping[str, Any]] = (),
        confirm_payment: bool = False,
        at_cycle_end: bool | None = None,
    ) -> dict[str, Any]:
        current = self._call("GET", f"/v1/subscriptions/{subscription_id}")
        existing = list(current["items"]["data"])

        def item_app(item: Mapping[str, Any]) -> str:
            price = item.get("price") if isinstance(item.get("price"), dict) else {}
            return str(
                (item.get("metadata") or {}).get("app_id")
                or ((price or {}).get("metadata") or {}).get("app_id")
                or ""
            )

        # The plan item is the one without an app id; add-on items carry metadata.app_id (on the item, or on the
        # Price an upgrade made).
        by_app = {item_app(i): i["id"] for i in existing if item_app(i)}
        plan_item = next(
            (i["id"] for i in existing if not item_app(i)), existing[0]["id"]
        )
        wanted = {str(a["app_id"]): a for a in addons}
        metadata = {
            "plan_id": plan_id,
            "seats": seats,
            "apps": ",".join(sorted(wanted)),
        }
        if confirm_payment:
            return self._pending_update(
                subscription_id,
                plan_item,
                by_app,
                plan_id=plan_id,
                plan_name=plan_name,
                seats=seats,
                seat_cents=seat_cents,
                currency=currency,
                wanted=wanted,
                metadata=metadata,
            )
        # price_data on an existing subscription item must name a product (product_data is only
        # accepted when creating a Checkout session), so make one for the new plan.
        product = self._product(
            f"Monosage {plan_name} (per candidate seat)", {"plan_id": plan_id}
        )
        price = self._price(plan_name, seats, seat_cents, currency)
        price["price_data"].pop("product_data", None)
        price["price_data"]["product"] = product
        items: list[dict[str, Any]] = [{"id": plan_item, **price}]
        for app_id, addon in wanted.items():
            addon_product = self._product(
                f"Monosage {addon['name']} add-on (per candidate seat)", {"app_id": app_id}
            )
            entry = self._addon_item(addon, currency, addon_product)
            if app_id in by_app:
                entry["id"] = by_app[app_id]
            items.append(entry)
        for app_id, item_id in by_app.items():
            if app_id not in wanted:
                items.append({"id": item_id, "deleted": "true"})
        return self._call(
            "POST",
            f"/v1/subscriptions/{subscription_id}",
            {
                "items": items,
                "proration_behavior": "create_prorations" if prorate else "none",
                "metadata": metadata,
            },
        )

    def _pending_update(
        self,
        subscription_id: str,
        plan_item: str,
        by_app: Mapping[str, str],
        *,
        plan_id: str,
        plan_name: str,
        seats: int,
        seat_cents: int,
        currency: str,
        wanted: Mapping[str, Mapping[str, Any]],
        metadata: Mapping[str, Any],
    ) -> dict[str, Any]:
        """An upgrade that applies only when its prorated invoice is paid (``payment_behavior=pending_if_incomplete``
        with ``proration_behavior=always_invoice``). Pending updates take ``items[].price`` and ``quantity`` only, so
        the items use Price objects, and an add-on the new plan drops is removed after the payment (Billing does)."""
        items: list[dict[str, Any]] = [
            {
                "id": plan_item,
                "price": self._price_id(
                    f"Monosage {plan_name} (per candidate seat)",
                    {"plan_id": plan_id},
                    seat_cents,
                    currency,
                ),
                "quantity": seats,
            }
        ]
        for app_id, addon in wanted.items():
            entry: dict[str, Any] = {
                "price": self._price_id(
                    f"Monosage {addon['name']} add-on (per candidate seat)",
                    {"app_id": app_id},
                    int(addon["seat_cents"]),
                    currency,
                ),
                "quantity": int(addon["seats"]),
            }
            if app_id in by_app:
                entry["id"] = by_app[app_id]
            items.append(entry)
        answer = self._call(
            "POST",
            f"/v1/subscriptions/{subscription_id}",
            {
                "items": items,
                "payment_behavior": "pending_if_incomplete",
                "proration_behavior": "always_invoice",
                "metadata": dict(metadata),
                "expand": ["latest_invoice"],
            },
        )
        invoice = answer.get("latest_invoice")
        invoice_id = (
            str(invoice.get("id") or "")
            if isinstance(invoice, dict)
            else str(invoice or "")
        )
        return {
            **answer,
            "pending": True,
            "invoice_id": invoice_id,
            "match": {"plan_id": plan_id, "seats": seats},
        }

    def cancel_subscription(
        self, subscription_id: str, *, at_period_end: bool
    ) -> dict[str, Any]:
        if at_period_end:
            return self._call(
                "POST",
                f"/v1/subscriptions/{subscription_id}",
                {"cancel_at_period_end": "true"},
            )
        return self._call("DELETE", f"/v1/subscriptions/{subscription_id}")

    def portal_url(self, customer_id: str, return_url: str) -> str:
        return str(
            self._call(
                "POST",
                "/v1/billing_portal/sessions",
                {"customer": customer_id, "return_url": return_url},
            )["url"]
        )

    def add_invoice_item(
        self,
        customer_id: str,
        amount_cents: int,
        description: str,
        currency: str,
        *,
        subscription_id: str = "",
        idempotency_key: str = "",
        metadata: Mapping[str, Any] | None = None,
    ) -> str:
        params: dict[str, Any] = {
            "customer": customer_id,
            "amount": amount_cents,
            "currency": currency,
            "description": description,
        }
        if metadata:
            params["metadata"] = dict(metadata)
        return str(
            self._call(
                "POST", "/v1/invoiceitems", params, idempotency_key=idempotency_key
            )["id"]
        )

    def charge_now(
        self,
        customer_id: str,
        amount_cents: int,
        description: str,
        currency: str,
        *,
        subscription_id: str = "",
        idempotency_key: str = "",
        metadata: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        """A one-off invoice with this one line, finalized and paid now with the customer's default payment method.
        A declined card leaves it open (Stripe retries it); the money counts on ``invoice.paid``."""
        key = idempotency_key or f"aira-charge-{uuid.uuid4().hex}"
        meta = dict(metadata or {})
        invoice = self._call(
            "POST",
            "/v1/invoices",
            {
                "customer": customer_id,
                "collection_method": "charge_automatically",
                "auto_advance": "true",
                "pending_invoice_items_behavior": "exclude",
                "description": description,
                "metadata": meta,
            },
            idempotency_key=f"{key}-invoice",
        )
        invoice_id = str(invoice["id"])
        item = self._call(
            "POST",
            "/v1/invoiceitems",
            {
                "customer": customer_id,
                "invoice": invoice_id,
                "amount": amount_cents,
                "currency": currency,
                "description": description,
                "metadata": meta,
            },
            idempotency_key=f"{key}-item",
        )
        status = "open"
        try:
            paid = self._call(
                "POST", f"/v1/invoices/{invoice_id}/pay", idempotency_key=f"{key}-pay"
            )
            status = "paid" if paid.get("status") == "paid" else "open"
        except Unavailable as error:  # declined or needs authentication: the invoice stays open and is retried
            logger.info("Top-up invoice %s is not paid yet: %s", invoice_id, error)
        return {"id": invoice_id, "item_id": str(item.get("id") or ""), "status": status}

    def verify_webhook(self, payload: bytes, signature: str) -> dict[str, Any]:
        if not self.config.stripe_webhook_secret:
            raise Unavailable(
                "The webhook secret is not configured.", code="billing_not_configured"
            )
        check_signature(
            self.config.stripe_webhook_secret, payload, signature, self.clock.now()
        )
        try:
            return json.loads(payload)
        except ValueError:
            raise ValidationFailed("The webhook body is not JSON.") from None

    def _plan_of_price(self, price: Mapping[str, Any]) -> str:
        """Which plan a subscription item's Price is: its metadata (prices Aira made, or set on portal prices by the
        operator), its lookup key (``monosage_<plan>`` or the plan id), or the product Aira made for the plan."""
        if not isinstance(price, Mapping):
            return ""
        meta = price.get("metadata") or {}
        if meta.get("plan_id"):
            return str(meta["plan_id"])
        lookup = str(price.get("lookup_key") or "")
        if lookup:
            return lookup.removeprefix("monosage_")
        product = price.get("product")
        product_id = (
            str(product.get("id") or "")
            if isinstance(product, Mapping)
            else str(product or "")
        )
        if product_id:
            known = _kv_get(self.db, f"stripe:product:{product_id}") or {}
            return str(known.get("plan_id") or "")
        return ""

    def normalize(self, event: dict[str, Any]) -> dict[str, Any] | None:
        kind = event.get("type", "")
        obj = (event.get("data") or {}).get("object") or {}
        metadata = obj.get("metadata") or {}
        created = event.get("created")
        if kind == "checkout.session.completed":
            return {
                "id": event["id"],
                "type": "checkout.completed",
                "created": created,
                "org_id": metadata.get("org_id") or obj.get("client_reference_id", ""),
                "plan_id": metadata.get("plan_id", ""),
                "seats": int(metadata.get("seats") or 0),
                "customer_id": obj.get("customer", ""),
                "subscription_id": obj.get("subscription", ""),
                "session_id": obj.get("id", ""),
            }
        if kind in {
            "invoice.paid",
            "invoice.payment_failed",
            "invoice.voided",
            "invoice.marked_uncollectible",
        }:
            lines = ((obj.get("lines") or {}).get("data")) or [{}]
            period = lines[0].get("period") or {}
            # Newer API versions nest the subscription under parent.subscription_details.
            details = (obj.get("subscription_details") or {}) or (
                (obj.get("parent") or {}).get("subscription_details") or {}
            )
            sub_meta = details.get("metadata") or metadata
            subscription_id = (
                obj.get("subscription") or details.get("subscription") or ""
            )
            payment = obj.get("payment_intent") or obj.get("charge") or ""
            return {
                "id": event["id"],
                "type": {
                    "invoice.paid": "invoice.paid",
                    "invoice.payment_failed": "invoice.payment_failed",
                }.get(kind, "invoice.voided"),
                "created": created,
                "org_id": sub_meta.get("org_id") or metadata.get("org_id", ""),
                "customer_id": obj.get("customer", ""),
                "subscription_id": subscription_id,
                "invoice_id": obj.get("id", ""),
                "payment_id": payment.get("id", "")
                if isinstance(payment, dict)
                else str(payment or ""),
                "billing_reason": obj.get("billing_reason", ""),
                "kind": metadata.get("kind", ""),
                "amount_cents": int(
                    obj.get("amount_paid") or obj.get("amount_due") or 0
                ),
                "currency": obj.get("currency", "usd"),
                "period_start": period.get("start"),
                "period_end": period.get("end"),
                "hosted_url": obj.get("hosted_invoice_url", ""),
            }
        if kind in {"charge.refunded", "charge.dispute.created"}:
            payment = obj.get("payment_intent") or ""
            return {
                "id": event["id"],
                "type": "payment.refunded",
                "created": created,
                "org_id": metadata.get("org_id", ""),
                "customer_id": obj.get("customer", ""),
                "invoice_id": obj.get("invoice") or "",
                "payment_id": payment.get("id", "")
                if isinstance(payment, dict)
                else str(payment or ""),
                "amount_cents": int(obj.get("amount_refunded") or obj.get("amount") or 0),
                "currency": str(obj.get("currency") or "usd").lower(),
            }
        if kind == "customer.subscription.pending_update_expired":
            return {
                "id": event["id"],
                "type": "subscription.update_expired",
                "created": created,
                "org_id": metadata.get("org_id", ""),
                "subscription_id": obj.get("id", ""),
            }
        if kind in {"customer.subscription.updated", "customer.subscription.deleted"}:
            items = ((obj.get("items") or {}).get("data")) or [{}]
            # Newer API versions carry the billing period on the items, older ones on the subscription.
            period_start = obj.get("current_period_start") or items[0].get(
                "current_period_start"
            )
            period_end = obj.get("current_period_end") or items[0].get(
                "current_period_end"
            )

            def app_of(item: Mapping[str, Any]) -> str:
                price = item.get("price") if isinstance(item.get("price"), dict) else {}
                return str(
                    (item.get("metadata") or {}).get("app_id")
                    or ((price or {}).get("metadata") or {}).get("app_id")
                    or ""
                )

            plan_item = next((i for i in items if i and not app_of(i)), {}) or {}
            price_plan = self._plan_of_price(plan_item.get("price") or {})
            quantity = plan_item.get("quantity")
            return {
                "id": event["id"],
                "type": "subscription.updated"
                if kind.endswith("updated")
                else "subscription.deleted",
                "created": created,
                "org_id": metadata.get("org_id", ""),
                # a change made in the customer portal shows on the plan item's price and quantity, not in metadata
                "plan_id": price_plan or metadata.get("plan_id", ""),
                "seats": int(quantity or metadata.get("seats") or 0),
                "subscription_id": obj.get("id", ""),
                "status": obj.get("status", ""),
                "period_start": period_start,
                "period_end": period_end,
                "cancel_at_period_end": bool(obj.get("cancel_at_period_end")),
                "pending_update": bool(obj.get("pending_update")),
            }
        return None


# --------------------------------------------------------------------------- #
# Razorpay (plan subscriptions in rupees)
# --------------------------------------------------------------------------- #


class RazorpayProvider:
    """Razorpay Subscriptions: one Razorpay plan per (Monosage plan, add-ons, rupee price), a subscription per
    organization whose ``quantity`` is the seats, and add-ons on the subscription for one-off charges (overage, ad
    top-ups), charged with the next cycle. Razorpay has no customer portal; the in-app billing page stands in.

    Amounts arrive in US cents (the catalog's currency) and leave in paise: plans and the shipped app prices have
    explicit rupee prices (``plans.INR_SEAT_PAISE``), anything else uses the configured fixed rate.
    """

    name = "razorpay"

    def __init__(
        self, config: CloudConfig, clock: Clock, db: ControlDB | None = None
    ) -> None:
        self.config = config
        self.clock = clock
        self.db = db  # caches the Razorpay plan made for each price

    def _call(
        self, method: str, path: str, body: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        if not (self.config.razorpay_key_id and self.config.razorpay_key_secret):
            raise Unavailable(
                "Billing is not configured. Set AIRA_CLOUD_RAZORPAY_KEY_ID and AIRA_CLOUD_RAZORPAY_KEY_SECRET.",
                code="billing_not_configured",
            )
        token = base64.b64encode(
            f"{self.config.razorpay_key_id}:{self.config.razorpay_key_secret}".encode()
        ).decode("ascii")
        data = json.dumps(body).encode() if body is not None else None
        request = Request(
            f"{self.config.razorpay_api_base.rstrip('/')}{path}",
            data=data,
            method=method,
            headers={
                "Authorization": f"Basic {token}",
                "Content-Type": "application/json",
            },
        )
        try:
            with urlopen(request, timeout=20) as response:  # noqa: S310 - the base URL is operator configuration
                return json.loads(response.read().decode("utf-8") or "{}")
        except HTTPError as error:
            detail = error.read().decode("utf-8", "replace")[:300]
            raise Unavailable(
                f"The billing provider refused the request ({error.code}).",
                detail=detail,
                provider_status=error.code,
            ) from None
        except (URLError, TimeoutError) as error:
            raise Unavailable(
                "The billing provider could not be reached.", detail=str(error)[:200]
            ) from None

    # -- prices --------------------------------------------------------------------------

    def _paise(self, amount: int, currency: str) -> int:
        from monosage_cloud.plans import inr_paise

        if (currency or "").lower() == "inr":
            return int(amount)
        return inr_paise(int(amount), self.config.razorpay_inr_per_usd)

    def unit_paise(
        self, plan_id: str, seat_cents: int, addons: Sequence[Mapping[str, Any]] = ()
    ) -> int:
        """What one seat costs a month in paise: the plan's rupee price plus each add-on's."""
        from monosage_cloud.plans import DEFAULT_PLANS, app_inr_paise, plan_inr_paise

        rate = self.config.razorpay_inr_per_usd
        pricing = DEFAULT_PLANS.get(plan_id, {}).get("pricing", {})
        total = plan_inr_paise(plan_id, pricing, int(seat_cents), rate)
        for addon in addons:
            total += app_inr_paise(int(addon["seat_cents"]), rate)
        return total

    def plan_ref(self, razorpay_plan_id: str) -> dict[str, Any]:
        """Which Monosage plan (and add-ons) a Razorpay plan stands for."""
        return _kv_get(self.db, f"razorpay:planref:{razorpay_plan_id}") or {}

    def _plan(
        self,
        plan_id: str,
        plan_name: str,
        seat_cents: int,
        addons: Sequence[Mapping[str, Any]] = (),
    ) -> str:
        apps = ",".join(sorted(str(a["app_id"]) for a in addons))
        paise = self.unit_paise(plan_id, seat_cents, addons)
        key = f"razorpay:plan:{plan_id}:{apps}:{paise}"
        cached = _kv_get(self.db, key)
        if cached:
            return str(cached)
        label = f"Monosage {plan_name}" + (f" + {apps}" if apps else "")
        made = self._call(
            "POST",
            "/v1/plans",
            {
                "period": "monthly",
                "interval": 1,
                "item": {
                    "name": f"{label} (per candidate seat)"[:100],
                    "amount": paise,
                    "currency": "INR",
                    "description": f"{label}, per candidate seat per month"[:250],
                },
                "notes": {"plan_id": plan_id, "apps": apps},
            },
        )
        razorpay_plan = str(made["id"])
        _kv_put(self.db, key, razorpay_plan)
        _kv_put(
            self.db,
            f"razorpay:planref:{razorpay_plan}",
            {"plan_id": plan_id, "apps": apps, "paise": paise},
        )
        return razorpay_plan

    # -- the protocol --------------------------------------------------------------------

    def create_customer(self, org_id: str, name: str, email: str) -> str:
        return str(
            self._call(
                "POST",
                "/v1/customers",
                {
                    "name": name[:50] or "Monosage customer",
                    "email": email,
                    "fail_existing": "0",  # the same email answers with the customer Razorpay already has
                    "notes": {"org_id": org_id},
                },
            )["id"]
        )

    def create_checkout(
        self,
        *,
        org_id: str,
        customer_id: str,
        plan_id: str,
        plan_name: str,
        seats: int,
        seat_cents: int,
        currency: str,
        success_url: str,
        cancel_url: str,
        checkout_id: str = "",
    ) -> dict[str, Any]:
        """A subscription the customer authorises on Razorpay's hosted page (``short_url``); it is the checkout."""
        subscription = self._call(
            "POST",
            "/v1/subscriptions",
            {
                "plan_id": self._plan(plan_id, plan_name, seat_cents),
                "total_count": int(self.config.razorpay_total_count),
                "quantity": int(seats),
                "customer_notify": True,  # Razorpay sends the customer its own emails
                "notes": {
                    "org_id": org_id,
                    "plan_id": plan_id,
                    "seats": str(seats),
                    "customer_id": customer_id,
                    "checkout_id": checkout_id,
                },
            },
        )
        return {"id": str(subscription["id"]), "url": str(subscription["short_url"])}

    def expire_checkout(self, session_id: str) -> None:
        self._call(
            "POST",
            f"/v1/subscriptions/{session_id}/cancel",
            {"cancel_at_cycle_end": False},
        )

    def update_subscription(
        self,
        subscription_id: str,
        *,
        plan_id: str,
        plan_name: str,
        seats: int,
        seat_cents: int,
        currency: str,
        prorate: bool,
        addons: Sequence[Mapping[str, Any]] = (),
        confirm_payment: bool = False,
        at_cycle_end: bool | None = None,
    ) -> dict[str, Any]:
        """``PATCH`` the plan (one Razorpay plan carries the add-ons too) and quantity. An upgrade applies now and is
        confirmed by ``subscription.updated``; Razorpay does not prorate, so Billing adds the prorated difference as
        an add-on charged with the next cycle."""
        razorpay_plan = self._plan(plan_id, plan_name, seat_cents, addons)
        later = (not prorate) if at_cycle_end is None else bool(at_cycle_end)
        answer = self._call(
            "PATCH",
            f"/v1/subscriptions/{subscription_id}",
            {
                "plan_id": razorpay_plan,
                "quantity": int(seats),
                "schedule_change_at": "cycle_end" if later else "now",
                "customer_notify": True,  # Razorpay sends the customer its own emails
            },
        )
        if confirm_payment:
            return {
                **answer,
                "pending": True,
                "invoice_id": "",
                "match": {"provider_plan": razorpay_plan, "seats": int(seats)},
            }
        return answer

    def cancel_subscription(
        self, subscription_id: str, *, at_period_end: bool
    ) -> dict[str, Any]:
        return self._call(
            "POST",
            f"/v1/subscriptions/{subscription_id}/cancel",
            {"cancel_at_cycle_end": bool(at_period_end)},
        )

    def portal_url(self, customer_id: str, return_url: str) -> str:
        """Razorpay has no customer portal: the in-app billing page manages the plan and the card (Razorpay's own
        emails carry the link to update the payment method)."""
        return return_url

    def add_invoice_item(
        self,
        customer_id: str,
        amount_cents: int,
        description: str,
        currency: str,
        *,
        subscription_id: str = "",
        idempotency_key: str = "",
        metadata: Mapping[str, Any] | None = None,
    ) -> str:
        if not subscription_id:
            raise Unavailable(
                "Razorpay adds one-off charges to a subscription, and this organization has none.",
                code="not_subscribed",
            )
        addon = self._call(
            "POST",
            f"/v1/subscriptions/{subscription_id}/addons",
            {
                "item": {
                    "name": description[:100],
                    "amount": self._paise(amount_cents, currency),
                    "currency": "INR",
                    "description": (idempotency_key or description)[:250],
                },
                "quantity": 1,
            },
        )
        return str(addon["id"])

    def charge_now(
        self,
        customer_id: str,
        amount_cents: int,
        description: str,
        currency: str,
        *,
        subscription_id: str = "",
        idempotency_key: str = "",
        metadata: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Razorpay charges a subscription add-on with the next cycle: the top-up counts on that charge."""
        addon = self.add_invoice_item(
            customer_id,
            amount_cents,
            description,
            currency,
            subscription_id=subscription_id,
            idempotency_key=idempotency_key,
            metadata=metadata,
        )
        return {"id": addon, "item_id": addon, "status": "scheduled"}

    def verify_webhook(self, payload: bytes, signature: str) -> dict[str, Any]:
        """``X-Razorpay-Signature`` is the hex HMAC-SHA256 of the raw body with the webhook secret."""
        secret = self.config.razorpay_webhook_secret
        if not secret:
            raise Unavailable(
                "The webhook secret is not configured.", code="billing_not_configured"
            )
        expected = hmac.new(secret.encode("utf-8"), payload, hashlib.sha256).hexdigest()
        if not signature or not hmac.compare_digest(expected, signature.strip()):
            raise Unauthorized(
                "The webhook signature does not match.", code="bad_signature"
            )
        try:
            return json.loads(payload)
        except ValueError:
            raise ValidationFailed("The webhook body is not JSON.") from None

    def normalize(self, event: dict[str, Any]) -> dict[str, Any] | None:
        kind = str(event.get("event") or "")
        payload = event.get("payload") or {}

        def entity(name: str) -> dict[str, Any]:
            return ((payload.get(name) or {}).get("entity")) or {}

        sub, payment, invoice = (
            entity("subscription"),
            entity("payment"),
            entity("invoice"),
        )
        notes = sub.get("notes") or payment.get("notes") or invoice.get("notes") or {}
        if not isinstance(notes, dict):  # Razorpay sends [] for empty notes
            notes = {}
        base = {
            "id": str(event.get("id") or ""),
            "created": event.get("created_at"),
            "org_id": str(notes.get("org_id") or ""),
            "subscription_id": str(
                sub.get("id")
                or invoice.get("subscription_id")
                or payment.get("subscription_id")
                or ""
            ),
            "customer_id": str(
                sub.get("customer_id")
                or payment.get("customer_id")
                or invoice.get("customer_id")
                or notes.get("customer_id")
                or ""
            ),
        }
        ref = self.plan_ref(str(sub.get("plan_id") or "")) if sub else {}
        plan_id = str(ref.get("plan_id") or notes.get("plan_id") or "")
        seats = int(sub.get("quantity") or notes.get("seats") or 0)
        if kind == "subscription.activated":
            # the first activation is the checkout completing; one after pending or halted is a recovered payment
            return {
                **base,
                "type": "checkout.completed",
                "plan_id": plan_id,
                "seats": seats,
                "session_id": base["subscription_id"],
                "period_start": sub.get("current_start"),
                "period_end": sub.get("current_end"),
            }
        if kind == "subscription.charged" or kind == "invoice.paid":
            return {
                **base,
                "type": "invoice.paid",
                "invoice_id": str(
                    payment.get("invoice_id") or invoice.get("id") or ""
                ),
                "payment_id": str(payment.get("id") or invoice.get("payment_id") or ""),
                "paid_at": payment.get("created_at") or invoice.get("paid_at"),
                "amount_cents": int(
                    payment.get("amount") or invoice.get("amount_paid") or 0
                ),
                "currency": str(
                    payment.get("currency") or invoice.get("currency") or "INR"
                ).lower(),
                "period_start": sub.get("current_start") or invoice.get("billing_start"),
                "period_end": sub.get("current_end") or invoice.get("billing_end"),
                "addon_ids": [
                    str(line.get("ref_id") or line.get("item_id") or "")
                    for line in invoice.get("line_items") or []
                    if isinstance(line, dict)
                    and (line.get("ref_type") == "addon" or str(line.get("ref_id") or "").startswith("ao_"))
                ],
            }
        if kind in {"subscription.pending", "subscription.halted", "payment.failed"}:
            return {
                **base,
                "type": "invoice.payment_failed",
                "invoice_id": str(payment.get("invoice_id") or ""),
                "payment_id": str(payment.get("id") or ""),
                "amount_cents": int(payment.get("amount") or 0),
                "currency": str(payment.get("currency") or "INR").lower(),
                "halted": kind == "subscription.halted",
            }
        if kind in {"subscription.cancelled", "subscription.completed"}:
            return {**base, "type": "subscription.deleted", "status": kind.split(".")[1]}
        if kind == "subscription.updated":
            return {
                **base,
                "type": "subscription.updated",
                "plan_id": plan_id,
                "provider_plan": str(sub.get("plan_id") or ""),
                "seats": seats,
                "status": str(sub.get("status") or ""),
                "period_start": sub.get("current_start"),
                "period_end": sub.get("current_end"),
                "cancel_at_period_end": None,  # Razorpay does not say; the local flag stays
            }
        if kind in {"refund.processed", "payment.refunded"}:
            refund = entity("refund")
            return {
                **base,
                "type": "payment.refunded",
                "payment_id": str(refund.get("payment_id") or payment.get("id") or ""),
                "invoice_id": str(payment.get("invoice_id") or ""),
                "amount_cents": int(refund.get("amount") or payment.get("amount_refunded") or 0),
                "currency": str(refund.get("currency") or payment.get("currency") or "INR").lower(),
            }
        return None


def _flatten(params: dict[str, Any], prefix: str = "") -> list[tuple[str, str]]:
    """Stripe's nested form encoding: ``a[b][0][c]=v``."""
    out: list[tuple[str, str]] = []
    for key, value in params.items():
        name = f"{prefix}[{key}]" if prefix else str(key)
        if isinstance(value, dict):
            out.extend(_flatten(value, name))
        elif isinstance(value, (list, tuple)):
            for index, item in enumerate(value):
                if isinstance(item, dict):
                    out.extend(_flatten(item, f"{name}[{index}]"))
                else:
                    out.append((f"{name}[{index}]", str(item)))
        elif isinstance(value, bool):
            out.append((name, "true" if value else "false"))
        elif value is not None:
            out.append((name, str(value)))
    return out


def now() -> int:
    return int(time.time())
