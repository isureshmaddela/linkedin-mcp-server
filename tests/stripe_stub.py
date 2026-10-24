"""A stand-in Stripe API for tests (loopback only): customers, Checkout sessions, subscriptions, the billing
portal and invoice items. It requires the secret key as a Bearer token, decodes Stripe's nested form encoding
into dictionaries, and records every call so tests can assert exactly what Aira sent."""

from __future__ import annotations

import json
import re
import threading
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any
from urllib.parse import parse_qsl, urlsplit

SECRET = "sk_test_stub_key"


def decode_form(pairs: list[tuple[str, str]]) -> dict[str, Any]:
    """``a[b][0][c]=v`` → ``{"a": {"b": [{"c": "v"}]}}``."""
    root: dict[str, Any] = {}
    for name, value in pairs:
        keys = re.findall(r"[^\[\]]+", name)
        node: Any = root
        for index, key in enumerate(keys):
            last = index == len(keys) - 1
            nxt = keys[index + 1] if not last else None
            if isinstance(node, list):
                position = int(key)
                while len(node) <= position:
                    node.append({} if nxt is not None and not nxt.isdigit() else [])
                if last:
                    node[position] = value
                else:
                    node = node[position]
            else:
                if last:
                    node[key] = value
                else:
                    node = node.setdefault(
                        key, [] if nxt is not None and nxt.isdigit() else {}
                    )
    return root


class StripeStub:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []
        self.subscriptions: dict[str, dict[str, Any]] = {}
        self.fail_next: list[int] = []
        self._lock = threading.Lock()
        stub = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args) -> None:  # noqa: ANN002
                return

            def _handle(self) -> None:
                length = int(self.headers.get("Content-Length", "0") or 0)
                raw = self.rfile.read(length).decode() if length else ""
                parts = urlsplit(self.path)
                params = decode_form(
                    parse_qsl(raw or parts.query, keep_blank_values=True)
                )
                call = {
                    "method": self.command,
                    "path": parts.path,
                    "params": params,
                    "authorized": self.headers.get("Authorization")
                    == f"Bearer {SECRET}",
                }
                with stub._lock:
                    stub.calls.append(call)
                    failure = stub.fail_next.pop(0) if stub.fail_next else 0
                if not call["authorized"]:
                    return self._reply(
                        401, {"error": {"message": "Invalid API Key provided"}}
                    )
                if failure:
                    return self._reply(failure, {"error": {"message": "stub failure"}})
                path = parts.path
                if self.command == "POST" and path == "/v1/customers":
                    return self._reply(
                        200,
                        {
                            "id": "cus_" + uuid.uuid4().hex[:10],
                            "metadata": params.get("metadata", {}),
                        },
                    )
                if self.command == "POST" and path == "/v1/checkout/sessions":
                    session = "cs_test_" + uuid.uuid4().hex[:10]
                    return self._reply(
                        200,
                        {
                            "id": session,
                            "url": f"https://checkout.stripe.test/pay/{session}",
                        },
                    )
                if path == "/v1/billing_portal/sessions":
                    return self._reply(
                        200, {"url": "https://billing.stripe.test/portal"}
                    )
                if self.command == "POST" and path == "/v1/products":
                    return self._reply(
                        200,
                        {
                            "id": "prod_" + uuid.uuid4().hex[:10],
                            "name": params.get("name", ""),
                        },
                    )
                if path == "/v1/invoiceitems":
                    return self._reply(200, {"id": "ii_" + uuid.uuid4().hex[:8]})
                match = re.fullmatch(r"/v1/subscriptions/([\w]+)", path)
                if match:
                    sub = match.group(1)
                    with stub._lock:
                        record = stub.subscriptions.setdefault(
                            sub,
                            {
                                "id": sub,
                                "status": "active",
                                "items": {"data": [{"id": "si_" + sub[-6:]}]},
                            },
                        )
                        if self.command == "POST":
                            record["last_update"] = params
                            if params.get("cancel_at_period_end") == "true":
                                record["cancel_at_period_end"] = True
                        elif self.command == "DELETE":
                            record["status"] = "canceled"
                    return self._reply(200, record)
                self._reply(404, {"error": {"message": "unknown"}})

            def _reply(self, status: int, body: dict[str, Any]) -> None:
                data = json.dumps(body).encode()
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            do_GET = do_POST = do_DELETE = _handle  # noqa: N815

        self._server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)

    def start(self) -> StripeStub:
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
