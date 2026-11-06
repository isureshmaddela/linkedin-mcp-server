"""A stand-in Razorpay API for the business-payments tests (loopback only; the billing tests have their own).

It checks HTTP Basic auth against one key id and key secret, keeps payment links, payments, orders and refunds in
memory, records every call, and can play the customer: :meth:`RazorpayStub.pay` pays a link (as Razorpay would
after UPI or a card) and :meth:`RazorpayStub.webhook` builds a signed webhook delivery for it. Point the client at it
with ``AIRA_RAZORPAY_API_BASE=<stub.base>``.
"""

from __future__ import annotations

import base64
import json
import secrets
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any
from urllib.parse import parse_qs, urlsplit

from aira.payments import razorpay

KEY_ID = "rzp_test_StubKey1234"
KEY_SECRET = "stub_key_secret_value"
WEBHOOK_SECRET = "stub_webhook_secret"


def _id(prefix: str) -> str:
    return prefix + secrets.token_hex(7)


class RazorpayStub:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []
        self.links: dict[str, dict[str, Any]] = {}
        self.payments: dict[str, dict[str, Any]] = {}
        self.refunds: dict[str, dict[str, Any]] = {}
        self.orders: dict[str, dict[str, Any]] = {}
        self.fail_next: list[int] = []
        self.refund_status = "processed"
        self._lock = threading.Lock()
        stub = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args: Any) -> None:
                return

            def _reply(self, status: int, body: dict[str, Any]) -> None:
                data = json.dumps(body).encode()
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def _handle(self) -> None:
                length = int(self.headers.get("Content-Length", "0") or 0)
                raw = self.rfile.read(length) if length else b""
                parts = urlsplit(self.path)
                expected = (
                    "Basic "
                    + base64.b64encode(f"{KEY_ID}:{KEY_SECRET}".encode()).decode()
                )
                body = json.loads(raw) if raw else {}
                call = {
                    "method": self.command,
                    "path": parts.path,
                    "query": parse_qs(parts.query),
                    "body": body,
                    "authorized": self.headers.get("Authorization") == expected,
                }
                with stub._lock:
                    stub.calls.append(call)
                    failure = stub.fail_next.pop(0) if stub.fail_next else 0
                if not call["authorized"]:
                    return self._reply(
                        401,
                        {
                            "error": {
                                "code": "BAD_REQUEST_ERROR",
                                "description": "Authentication failed",
                            }
                        },
                    )
                if failure:
                    return self._reply(
                        failure,
                        {
                            "error": {
                                "code": "SERVER_ERROR",
                                "description": "stub failure",
                            }
                        },
                    )
                status, answer = stub.route(
                    self.command, parts.path.removeprefix("/v1"), body
                )
                self._reply(status, answer)

            do_GET = _handle
            do_POST = _handle

        self._server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self._thread = threading.Thread(
            target=self._server.serve_forever,
            kwargs={"poll_interval": 0.05},
            daemon=True,
        )

    # -- lifecycle ---------------------------------------------------------------

    def start(self) -> RazorpayStub:
        self._thread.start()
        return self

    def stop(self) -> None:
        self._server.shutdown()
        self._server.server_close()

    @property
    def base(self) -> str:
        return f"http://127.0.0.1:{self._server.server_address[1]}/v1"

    def calls_to(self, method: str, path_start: str) -> list[dict[str, Any]]:
        return [
            c
            for c in self.calls
            if c["method"] == method
            and c["path"].removeprefix("/v1").startswith(path_start)
        ]

    # -- the API -----------------------------------------------------------------

    def route(
        self, method: str, path: str, body: dict[str, Any]
    ) -> tuple[int, dict[str, Any]]:
        def bad(text: str, field: str = "") -> tuple[int, dict[str, Any]]:
            return 400, {
                "error": {
                    "code": "BAD_REQUEST_ERROR",
                    "description": text,
                    "field": field,
                }
            }

        if method == "POST" and path == "/payment_links":
            if not isinstance(body.get("amount"), int) or body["amount"] < 100:
                return bad("The amount must be atleast INR 1.00", "amount")
            reference = body.get("reference_id") or ""
            if reference and any(
                link["reference_id"] == reference for link in self.links.values()
            ):
                return bad(
                    "Payment Link with given reference id already exists",
                    "reference_id",
                )
            if body.get("expire_by") and body["expire_by"] < time.time() + 15 * 60:
                return bad(
                    "expire_by should be at least 15 minutes after current time",
                    "expire_by",
                )
            link_id = _id("plink_")
            link = {
                "id": link_id,
                "entity": "payment_link",
                "amount": body["amount"],
                "amount_paid": 0,
                "currency": body.get("currency", "INR"),
                "reference_id": reference,
                "description": body.get("description", ""),
                "customer": body.get("customer", {}),
                "notify": body.get("notify", {}),
                "notes": body.get("notes", {}),
                "callback_url": body.get("callback_url", ""),
                "callback_method": body.get("callback_method", ""),
                "expire_by": body.get("expire_by", 0),
                "status": "created",
                "short_url": f"https://rzp.io/i/{link_id[6:]}",
                "payments": None,
            }
            self.links[link_id] = link
            return 200, link
        if path == "/payment_links" and method == "GET":
            return 200, {"payment_links": list(self.links.values())[:1]}
        if path.startswith("/payment_links/"):
            link_id = path.split("/")[2]
            link = self.links.get(link_id)
            if link is None:
                return bad("The id provided does not exist")
            if path.endswith("/cancel") and method == "POST":
                if link["status"] != "created":
                    return bad(
                        f"Payment link cannot be cancelled as it is already {link['status']}"
                    )
                link["status"] = "cancelled"
            return 200, link
        if method == "POST" and path == "/orders":
            order_id = _id("order_")
            self.orders[order_id] = {
                "id": order_id,
                "entity": "order",
                "status": "created",
                **body,
            }
            return 200, self.orders[order_id]
        if method == "GET" and path.startswith("/orders/"):
            found = self.orders.get(path.split("/")[2])
            return (200, found) if found else bad("The id provided does not exist")
        if path.startswith("/payments/"):
            payment_id = path.split("/")[2]
            payment = self.payments.get(payment_id)
            if payment is None:
                return bad("The id provided does not exist")
            if method == "POST" and path.endswith("/refund"):
                amount = int(body.get("amount") or payment["amount"])
                left = payment["amount"] - payment["amount_refunded"]
                if amount > left:
                    return bad(
                        "The refund amount provided is greater than amount captured",
                        "amount",
                    )
                refund = {
                    "id": _id("rfnd_"),
                    "entity": "refund",
                    "amount": amount,
                    "currency": payment["currency"],
                    "payment_id": payment_id,
                    "notes": body.get("notes", {}),
                    "receipt": body.get("receipt"),
                    "status": self.refund_status,
                    "speed_requested": body.get("speed", "normal"),
                }
                self.refunds[refund["id"]] = refund
                payment["amount_refunded"] += amount
                return 200, refund
            return 200, payment
        return 404, {
            "error": {
                "code": "BAD_REQUEST_ERROR",
                "description": "The requested URL was not found on the server.",
            }
        }

    # -- playing the customer ------------------------------------------------------

    def pay(
        self,
        link_id: str,
        *,
        amount: int | None = None,
        currency: str | None = None,
        method: str = "upi",
    ) -> dict[str, Any]:
        """The customer pays the link: a captured payment (``amount``/``currency`` default to the link's)."""
        link = self.links[link_id]
        payment = {
            "id": _id("pay_"),
            "entity": "payment",
            "amount": link["amount"] if amount is None else amount,
            "currency": currency or link["currency"],
            "status": "captured",
            "order_id": _id("order_"),
            "method": method,
            "amount_refunded": 0,
            "email": link["customer"].get("email", ""),
            "contact": link["customer"].get("contact", ""),
            "notes": {},
        }
        self.payments[payment["id"]] = payment
        link["status"] = "paid"
        link["amount_paid"] = payment["amount"]
        return payment

    def webhook(
        self,
        event: str,
        *,
        link_id: str = "",
        payment: dict[str, Any] | None = None,
        refund: dict[str, Any] | None = None,
        secret: str = WEBHOOK_SECRET,
    ) -> tuple[bytes, dict[str, str]]:
        """A signed webhook delivery: (raw body, headers)."""
        payload: dict[str, Any] = {}
        contains = []
        if link_id:
            payload["payment_link"] = {"entity": self.links[link_id]}
            contains.append("payment_link")
        if payment is not None:
            payload["payment"] = {"entity": payment}
            contains.append("payment")
        if refund is not None:
            payload["refund"] = {"entity": refund}
            contains.append("refund")
        body = json.dumps(
            {
                "entity": "event",
                "account_id": "acc_stub",
                "event": event,
                "contains": contains,
                "payload": payload,
                "created_at": int(time.time()),
            },
            separators=(",", ":"),
        ).encode()
        headers = {
            "Content-Type": "application/json",
            "X-Razorpay-Signature": razorpay.webhook_signature(body, secret),
            "X-Razorpay-Event-Id": _id("evt_"),
        }
        return body, headers

    def return_params(
        self,
        link_id: str,
        payment_id: str,
        *,
        status: str = "paid",
        key_secret: str = KEY_SECRET,
    ) -> dict[str, str]:
        """The query parameters Razorpay adds to callback_url when the customer comes back."""
        link = self.links[link_id]
        return {
            "razorpay_payment_id": payment_id,
            "razorpay_payment_link_id": link_id,
            "razorpay_payment_link_reference_id": link["reference_id"],
            "razorpay_payment_link_status": status,
            "razorpay_signature": razorpay.callback_signature(
                payment_link_id=link_id,
                reference_id=link["reference_id"],
                status=status,
                payment_id=payment_id,
                key_secret=key_secret,
            ),
        }
