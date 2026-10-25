"""Billing providers: a stand-in that behaves like a hosted checkout, and Stripe.

Both turn provider events into the same neutral events (``normalize``) and both sign
their webhooks, so the code that handles a payment is the same code in every
environment. The stand-in lets development, tests and demos run the whole
lifecycle (checkout, failure, recovery) without a payment account; Stripe is
the production provider (see ``docs/multitenant.md``).
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence

import hashlib
import hmac
import json
import time
import uuid
from typing import Any, Protocol
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from aira_cloud.config import CloudConfig, Clock
from aira_cloud.db import ControlDB, dumps, loads, one
from aira_cloud.errors import Unauthorized, Unavailable, ValidationFailed

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
    ) -> dict[str, Any]: ...

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
    ) -> dict[str, Any]: ...

    def cancel_subscription(
        self, subscription_id: str, *, at_period_end: bool
    ) -> dict[str, Any]: ...

    def portal_url(self, customer_id: str, return_url: str) -> str: ...

    def add_invoice_item(
        self, customer_id: str, amount_cents: int, description: str, currency: str
    ) -> str: ...

    def verify_webhook(self, payload: bytes, signature: str) -> dict[str, Any]: ...

    def normalize(self, event: dict[str, Any]) -> dict[str, Any] | None: ...


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
        self, customer_id: str, amount_cents: int, description: str, currency: str
    ) -> str:
        return f"ii_standin_{uuid.uuid4().hex[:12]}"

    # -- events ----------------------------------------------------------------------

    def make_event(self, event_type: str, data: dict[str, Any]) -> tuple[bytes, str]:
        """A signed event, exactly as a provider would deliver it."""
        payload = json.dumps(
            {
                "id": f"evt_standin_{uuid.uuid4().hex[:16]}",
                "type": event_type,
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
        return {"id": event["id"], "type": event["type"], **event["data"]}


# --------------------------------------------------------------------------- #
# Stripe
# --------------------------------------------------------------------------- #


class StripeProvider:
    name = "stripe"

    def __init__(self, config: CloudConfig, clock: Clock) -> None:
        self.config = config
        self.clock = clock

    def _call(
        self, method: str, path: str, params: dict[str, Any] | None = None
    ) -> dict[str, Any]:
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
        request = Request(
            url,
            data=body,
            method=method,
            headers={
                "Authorization": f"Bearer {self.config.stripe_secret_key}",
                "Content-Type": "application/x-www-form-urlencoded",
            },
        )
        try:
            with urlopen(request, timeout=20) as response:  # noqa: S310 - the base URL is operator configuration
                return json.loads(response.read().decode("utf-8"))
        except HTTPError as error:
            detail = error.read().decode("utf-8", "replace")[:300]
            raise Unavailable(
                f"The billing provider refused the request ({error.code}).",
                detail=detail,
            ) from None
        except (URLError, TimeoutError) as error:
            raise Unavailable(
                "The billing provider could not be reached.", detail=str(error)[:200]
            ) from None

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
                "product_data": {"name": f"Aira {plan_name} (per candidate seat)"},
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
                "name": f"Aira {addon['name']} add-on (per candidate seat)"
            }
        return {
            "price_data": price_data,
            "quantity": int(addon["seats"]),
            "metadata": {"app_id": str(addon["app_id"])},
        }

    def _product(self, name: str, metadata: Mapping[str, Any]) -> str:
        return str(
            self._call(
                "POST", "/v1/products", {"name": name, "metadata": dict(metadata)}
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
    ) -> dict[str, Any]:
        session = self._call(
            "POST",
            "/v1/checkout/sessions",
            {
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
            },
        )
        return {"id": session["id"], "url": session["url"]}

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
    ) -> dict[str, Any]:
        current = self._call("GET", f"/v1/subscriptions/{subscription_id}")
        existing = list(current["items"]["data"])
        # The plan item is the one without an app id; add-on items carry metadata.app_id.
        by_app = {
            str((i.get("metadata") or {}).get("app_id")): i["id"]
            for i in existing
            if (i.get("metadata") or {}).get("app_id")
        }
        plan_item = next(
            (i["id"] for i in existing if not (i.get("metadata") or {}).get("app_id")),
            existing[0]["id"],
        )
        # price_data on an existing subscription item must name a product (product_data is only
        # accepted when creating a Checkout session), so make one for the new plan.
        product = self._product(
            f"Aira {plan_name} (per candidate seat)", {"plan_id": plan_id}
        )
        price = self._price(plan_name, seats, seat_cents, currency)
        price["price_data"].pop("product_data", None)
        price["price_data"]["product"] = product
        items: list[dict[str, Any]] = [{"id": plan_item, **price}]
        wanted = {str(a["app_id"]): a for a in addons}
        for app_id, addon in wanted.items():
            addon_product = self._product(
                f"Aira {addon['name']} add-on (per candidate seat)", {"app_id": app_id}
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
                "metadata": {
                    "plan_id": plan_id,
                    "seats": seats,
                    "apps": ",".join(sorted(wanted)),
                },
            },
        )

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
        self, customer_id: str, amount_cents: int, description: str, currency: str
    ) -> str:
        return str(
            self._call(
                "POST",
                "/v1/invoiceitems",
                {
                    "customer": customer_id,
                    "amount": amount_cents,
                    "currency": currency,
                    "description": description,
                },
            )["id"]
        )

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

    def normalize(self, event: dict[str, Any]) -> dict[str, Any] | None:
        kind = event.get("type", "")
        obj = (event.get("data") or {}).get("object") or {}
        metadata = obj.get("metadata") or {}
        if kind == "checkout.session.completed":
            return {
                "id": event["id"],
                "type": "checkout.completed",
                "org_id": metadata.get("org_id") or obj.get("client_reference_id", ""),
                "plan_id": metadata.get("plan_id", ""),
                "seats": int(metadata.get("seats") or 0),
                "customer_id": obj.get("customer", ""),
                "subscription_id": obj.get("subscription", ""),
            }
        if kind in {"invoice.paid", "invoice.payment_failed"}:
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
            return {
                "id": event["id"],
                "type": "invoice.paid"
                if kind == "invoice.paid"
                else "invoice.payment_failed",
                "org_id": sub_meta.get("org_id", ""),
                "customer_id": obj.get("customer", ""),
                "subscription_id": subscription_id,
                "invoice_id": obj.get("id", ""),
                "amount_cents": int(
                    obj.get("amount_paid") or obj.get("amount_due") or 0
                ),
                "currency": obj.get("currency", "usd"),
                "period_start": period.get("start"),
                "period_end": period.get("end"),
                "hosted_url": obj.get("hosted_invoice_url", ""),
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
            return {
                "id": event["id"],
                "type": "subscription.updated"
                if kind.endswith("updated")
                else "subscription.deleted",
                "org_id": metadata.get("org_id", ""),
                "plan_id": metadata.get("plan_id", ""),
                "seats": int(metadata.get("seats") or 0),
                "subscription_id": obj.get("id", ""),
                "status": obj.get("status", ""),
                "period_start": period_start,
                "period_end": period_end,
                "cancel_at_period_end": bool(obj.get("cancel_at_period_end")),
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
