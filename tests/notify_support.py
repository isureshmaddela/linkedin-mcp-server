"""Helpers for the notification tests: a stub push service on 127.0.0.1 and a browser's push keys.

push = PushStub().start()           # POST /ok/<n> -> 201, /gone/<n> -> 410, /busy/<n> -> 429 once then 201
browser = Browser.new(push, "ok")   # its subscription; browser.decrypt(body) reads what the server sent
"""

from __future__ import annotations

import json
import secrets
import threading
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

from cryptography.hazmat.primitives.asymmetric import ec

from aira.notify import webpush


@dataclass
class Delivery:
    path: str
    headers: dict[str, str]
    body: bytes


@dataclass
class PushStub:
    deliveries: list[Delivery] = field(default_factory=list)
    busy_once: set[str] = field(default_factory=set)
    _server: ThreadingHTTPServer | None = None

    @property
    def host(self) -> str:
        assert self._server is not None
        return f"127.0.0.1:{self._server.server_address[1]}"

    @property
    def base(self) -> str:
        return f"http://{self.host}"

    def start(self) -> PushStub:
        stub = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args: Any) -> None:
                return

            def do_POST(self) -> None:  # noqa: N802
                length = int(self.headers.get("Content-Length") or 0)
                body = self.rfile.read(length)
                stub.deliveries.append(
                    Delivery(
                        self.path, {k.lower(): v for k, v in self.headers.items()}, body
                    )
                )
                status = 201
                if self.path.startswith("/gone/"):
                    status = 410
                elif self.path.startswith("/busy/") and self.path not in stub.busy_once:
                    stub.busy_once.add(self.path)
                    status = 429
                self.send_response(status)
                if status == 429:
                    self.send_header("Retry-After", "1")
                self.send_header("Content-Length", "0")
                self.end_headers()

        self._server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self._server.daemon_threads = True
        threading.Thread(target=self._server.serve_forever, daemon=True).start()
        webpush.EXTRA_HOSTS.add(self.host)
        return self

    def stop(self) -> None:
        if self._server is not None:
            webpush.EXTRA_HOSTS.discard(self.host)
            self._server.shutdown()
            self._server.server_close()


@dataclass
class Browser:
    """A browser's push subscription: its key pair and auth secret, as PushManager.subscribe makes them."""

    endpoint: str
    key: ec.EllipticCurvePrivateKey
    auth: str

    @classmethod
    def new(cls, push: PushStub, kind: str = "ok") -> Browser:
        return cls(
            f"{push.base}/{kind}/{secrets.token_hex(6)}",
            ec.generate_private_key(ec.SECP256R1()),
            webpush.b64url(secrets.token_bytes(16)),
        )

    @property
    def p256dh(self) -> str:
        return webpush.b64url(webpush._point(self.key.public_key()))

    def subscription(self, device: str = "Chrome on Windows") -> dict[str, Any]:
        return {
            "endpoint": self.endpoint,
            "keys": {"p256dh": self.p256dh, "auth": self.auth},
            "device": device,
        }

    def decrypt(self, body: bytes) -> dict[str, Any]:
        return json.loads(webpush.decrypt(body, self.key, self.auth))

    def received(self, push: PushStub) -> list[dict[str, Any]]:
        path = self.endpoint.removeprefix(push.base)
        return [self.decrypt(d.body) for d in push.deliveries if d.path == path]
