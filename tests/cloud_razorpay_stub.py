"""A stand-in Razorpay API for tests (loopback only): customers, plans, subscriptions (create, fetch, update,
cancel) and subscription add-ons. It requires the key id and secret as HTTP Basic auth, reads JSON bodies, records
every call so tests can assert exactly what Monosage sent, and builds signed webhook events the way Razorpay
delivers them (``X-Razorpay-Signature``: hex HMAC-SHA256 of the raw body; ``X-Razorpay-Event-Id``)."""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import re
import threading
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any
from urllib.parse import urlsplit

KEY_ID = "rzp_test_stubkey"
KEY_SECRET = "stub_key_secret"
WEBHOOK_SECRET = "rzp_webhook_stub_secret"


def _id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:14]}"


class RazorpayStub:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []
        self.customers: dict[str, dict[str, Any]] = {}
        self.plans: dict[str, dict[str, Any]] = {}
        self.subscriptions: dict[str, dict[str, Any]] = {}
        self.addons: dict[str, dict[str, Any]] = {}
        self.fail_next: list[int] = []
        self._lock = threading.Lock()
        stub = self
        expected = "Basic " + base64.b64encode(
            f"{KEY_ID}:{KEY_SECRET}".encode()
        ).decode("ascii")

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args) -> None:  # noqa: ANN002
                return

            def _handle(self) -> None:
                length = int(self.headers.get("Content-Length", "0") or 0)
                raw = self.rfile.read(length).decode() if length else ""
                path = urlsplit(self.path).path
                try:
                    body = json.loads(raw) if raw else {}
                except ValueError:
                    return self._reply(400, {"error": {"description": "not JSON"}})
                call = {
                    "method": self.command,
                    "path": path,
                    "body": body,
                    "authorized": self.headers.get("Authorization") == expected,
                    "content_type": self.headers.get("Content-Type", ""),
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
                return self._route(path, body)

            def _route(self, path: str, body: dict[str, Any]) -> None:
                method = self.command
                with stub._lock:
                    if method == "POST" and path == "/v1/customers":
                        customer = {"id": _id("cust"), "entity": "customer", **body}
                        stub.customers[customer["id"]] = customer
                        return self._reply(200, customer)
                    if method == "POST" and path == "/v1/plans":
                        plan = {"id": _id("plan"), "entity": "plan", **body}
                        stub.plans[plan["id"]] = plan
                        return self._reply(200, plan)
                    if method == "POST" and path == "/v1/subscriptions":
                        if body.get("plan_id") not in stub.plans:
                            return self._reply(
                                400,
                                {
                                    "error": {
                                        "description": "The id provided does not exist"
                                    }
                                },
                            )
                        sub = {
                            "id": _id("sub"),
                            "entity": "subscription",
                            "status": "created",
                            "customer_id": None,
                            "current_start": None,
                            "current_end": None,
                            "paid_count": 0,
                            "short_url": f"https://rzp.test/i/{uuid.uuid4().hex[:8]}",
                            "has_scheduled_changes": False,
                            **body,
                        }
                        stub.subscriptions[sub["id"]] = sub
                        return self._reply(200, sub)
                    match = re.fullmatch(
                        r"/v1/subscriptions/(sub_\w+)(/cancel|/addons)?", path
                    )
                    if match:
                        sub = stub.subscriptions.get(match.group(1))
                        if sub is None:
                            return self._reply(
                                404, {"error": {"description": "no such subscription"}}
                            )
                        action = match.group(2) or ""
                        if method == "GET" and not action:
                            return self._reply(200, sub)
                        if method == "PATCH" and not action:
                            if (
                                body.get("plan_id")
                                and body["plan_id"] not in stub.plans
                            ):
                                return self._reply(
                                    400, {"error": {"description": "unknown plan"}}
                                )
                            if body.get("schedule_change_at") == "cycle_end":
                                sub["has_scheduled_changes"] = True
                                sub["scheduled_change"] = body
                            else:
                                sub.update(
                                    {
                                        k: body[k]
                                        for k in ("plan_id", "quantity")
                                        if k in body
                                    }
                                )
                            sub["last_update"] = body
                            return self._reply(200, sub)
                        if method == "POST" and action == "/cancel":
                            if body.get("cancel_at_cycle_end"):
                                sub["cancel_at_cycle_end"] = True
                            else:
                                sub["status"] = "cancelled"
                            return self._reply(200, sub)
                        if method == "POST" and action == "/addons":
                            addon = {
                                "id": _id("ao"),
                                "entity": "addon",
                                "subscription_id": sub["id"],
                                "invoice_id": None,
                                **body,
                            }
                            stub.addons[addon["id"]] = addon
                            return self._reply(200, addon)
                return self._reply(404, {"error": {"description": "unknown"}})

            def _reply(self, status: int, body: dict[str, Any]) -> None:
                data = json.dumps(body).encode()
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            do_GET = do_POST = do_PATCH = _handle  # noqa: N815

        self._server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)

    def start(self) -> RazorpayStub:
        self._thread.start()
        return self

    def stop(self) -> None:
        self._server.shutdown()
        self._server.server_close()

    @property
    def base_url(self) -> str:
        return f"http://127.0.0.1:{self._server.server_address[1]}"

    def find(self, method: str, path_prefix: str) -> list[dict[str, Any]]:
        with self._lock:
            return [
                c
                for c in self.calls
                if c["method"] == method and c["path"].startswith(path_prefix)
            ]

    # -- webhooks ---------------------------------------------------------------------------

    def event(
        self,
        kind: str,
        subscription_id: str,
        *,
        created_at: int,
        payment: dict[str, Any] | None = None,
        refund: dict[str, Any] | None = None,
        **changes: Any,
    ) -> tuple[bytes, str, str]:
        """A signed event about a subscription as Razorpay would send it: (body, signature, event id). ``changes``
        update the subscription first (status, current_start, current_end, plan_id, quantity, customer_id)."""
        with self._lock:
            sub = self.subscriptions[subscription_id]
            sub.update(changes)
            entity = dict(sub)
        contains = ["subscription"]
        payload: dict[str, Any] = {"subscription": {"entity": entity}}
        if payment is not None:
            contains.append("payment")
            payload["payment"] = {
                "entity": {
                    "id": _id("pay"),
                    "entity": "payment",
                    "currency": "INR",
                    "status": "captured",
                    "invoice_id": _id("inv"),
                    "customer_id": entity.get("customer_id"),
                    "created_at": created_at,
                    **payment,
                }
            }
        if refund is not None:
            contains.append("refund")
            payload["refund"] = {
                "entity": {"id": _id("rfnd"), "entity": "refund", **refund}
            }
        return self.sign(
            {
                "entity": "event",
                "account_id": "acc_stub",
                "event": kind,
                "contains": contains,
                "payload": payload,
                "created_at": created_at,
            }
        )

    def refund_event(
        self, *, payment_id: str, amount: int, created_at: int
    ) -> tuple[bytes, str, str]:
        return self.sign(
            {
                "entity": "event",
                "account_id": "acc_stub",
                "event": "refund.processed",
                "contains": ["refund"],
                "payload": {
                    "refund": {
                        "entity": {
                            "id": _id("rfnd"),
                            "entity": "refund",
                            "payment_id": payment_id,
                            "amount": amount,
                            "currency": "INR",
                        }
                    }
                },
                "created_at": created_at,
            }
        )

    @staticmethod
    def sign(
        event: dict[str, Any], secret: str = WEBHOOK_SECRET
    ) -> tuple[bytes, str, str]:
        body = json.dumps(event).encode()
        signature = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
        return body, signature, "evt_" + uuid.uuid4().hex[:14]
