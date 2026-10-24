"""The worker agent: runs a candidate's LinkedIn-bound requests on the host that holds their browser session.

A candidate's LinkedIn browser profile and the MCP process that drives it live on one worker host (the
profile is stored in that tenant's volume and never shared). The web tier may run on other hosts, so a
request that needs the session (status, connect, inbox, messages, schedules, LinkedIn search) is *authorized
in the web tier* (permission, plan, policy, consent) and then forwarded to that worker's agent, which only
executes the already-authorized request inside the bound workspace.

Requests are signed (HMAC-SHA256 over timestamp, nonce and body with a secret all of the deployment's
processes share through the control plane), expire after a minute and cannot be replayed. The agent has no
sessions and no permissions logic of its own: it trusts exactly what the web tier signed, and nothing else.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import logging
import re
import secrets
import socket
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import TYPE_CHECKING, Any
from urllib.error import HTTPError, URLError
from urllib.parse import unquote
from urllib.request import Request, urlopen

from aira_cloud.db import many, one
from aira_cloud.errors import Unavailable
from aira_cloud.hooks import principal_from, principal_payload
from aira_cloud.webio import Captured, HttpRequest, HttpResponse

if TYPE_CHECKING:
    from aira_cloud.app import CloudApp
    from aira_cloud.gateway import Ctx, Gateway

logger = logging.getLogger("aira_cloud.agent")

WINDOW_SECONDS = 60
SECRET_KEY = "agent_secret"


def agent_secret(app: CloudApp) -> str:
    """The shared signing secret: configured, else generated once in the control plane (every process reads the same one)."""
    if app.config.agent_secret:
        return app.config.agent_secret
    with app.db.write() as connection:
        row = one(connection, "SELECT value FROM cloud_kv WHERE key = ?", (SECRET_KEY,))
        if row:
            return str(row["value"])
        value = secrets.token_urlsafe(32)
        connection.execute(
            "INSERT INTO cloud_kv (key, value) VALUES (?, ?)", (SECRET_KEY, value)
        )
        return value


def broker_secret(app: CloudApp) -> str:
    """The remote sign-in broker's own secret. It is never the agent secret, and empty means the broker is off."""
    return str(app.config.broker_secret or "")


def sign(
    secret: str, timestamp: int, nonce: str, body: bytes, purpose: str = "agent"
) -> str:
    """HMAC over purpose, timestamp, nonce and body: a signature made for one purpose is useless for another."""
    message = f"{purpose}.{timestamp}.{nonce}.".encode() + body
    return hmac.new(secret.encode(), message, hashlib.sha256).hexdigest()


class Verifier:
    """Checks a signed internal request: fresh, correctly signed for its purpose, and never seen before.

    Seen nonces live in the control plane (``cloud_kv``), so a request replayed against another process is
    refused too, and each nonce is kept for as long as its timestamp could still be accepted.
    """

    NONCE_PREFIX = "nonce:"

    def __init__(self, app: CloudApp) -> None:
        self.app = app

    def _secret(self, purpose: str) -> str:
        if purpose == "broker":
            return broker_secret(self.app)
        return agent_secret(self.app)

    def verify(
        self,
        headers: Any,
        body: bytes,
        purpose: str = "agent",
        secret: str | None = None,
    ) -> None:
        key = secret if secret is not None else self._secret(purpose)
        if not key:
            raise PermissionError("This kind of request is not enabled.")
        try:
            timestamp = int(headers.get("X-Aira-Agent-Timestamp", ""))
        except ValueError:
            raise PermissionError("The request has no valid timestamp.") from None
        nonce = headers.get("X-Aira-Agent-Nonce", "")
        signature = headers.get("X-Aira-Agent-Signature", "")
        now = self.app.clock.now()
        if abs(now - timestamp) > WINDOW_SECONDS or not re.fullmatch(
            r"[0-9a-f]{24}", nonce
        ):
            raise PermissionError("The request is outside the allowed window.")
        expected = sign(key, timestamp, nonce, body, purpose)
        if not hmac.compare_digest(expected, signature):
            raise PermissionError("The signature does not match.")
        # the timestamp is part of the key (zero padded), so expired nonces are pruned by plain comparison
        stale = f"{self.NONCE_PREFIX}{max(0, now - WINDOW_SECONDS):012d}"
        with self.app.db.write() as connection:
            connection.execute(
                "DELETE FROM cloud_kv WHERE key LIKE ? AND key < ?",
                (self.NONCE_PREFIX + "%", stale),
            )
            claimed = connection.execute(
                "INSERT INTO cloud_kv (key, value) VALUES (?, ?) ON CONFLICT (key) DO NOTHING",
                (
                    f"{self.NONCE_PREFIX}{timestamp:012d}:{purpose}:{nonce}",
                    str(timestamp),
                ),
            )
            if claimed.rowcount != 1:
                raise PermissionError("The request was already used.")


class AgentClient:
    """The web tier's side: choose the worker that holds a candidate's session and forward to it."""

    def __init__(self, app: CloudApp) -> None:
        self.app = app
        self.local_url = ""  # set when this process also runs an agent (it then executes its own requests locally)

    def agents(self) -> list[dict[str, Any]]:
        with self.app.db.read() as connection:
            return many(
                connection,
                "SELECT worker_id, agent_url, capacity, active_jobs FROM worker_heartbeats WHERE agent_url != '' AND last_seen_at > ? ORDER BY active_jobs, worker_id",
                (self.app.clock.now() - 60,),
            )

    def target(self, org_id: str, candidate_id: str, *, assign: bool) -> str:
        """The agent URL that should run this candidate's request ('' = run it in this process)."""
        with self.app.db.read() as connection:
            row = one(
                connection,
                "SELECT linkedin_host FROM workspaces WHERE candidate_id = ? AND org_id = ?",
                (candidate_id, org_id),
            )
        host = (row or {}).get("linkedin_host", "")
        live = {a["agent_url"] for a in self.agents()}
        if host and host in live:
            return "" if host == self.local_url else host
        if not live:
            return ""
        if not assign:
            return ""  # nothing holds a session yet: a read has nothing to ask
        chosen = next(iter(self.agents()))["agent_url"]
        with self.app.db.write() as connection:
            connection.execute(
                "UPDATE workspaces SET linkedin_host = ? WHERE candidate_id = ? AND org_id = ?",
                (chosen, candidate_id, org_id),
            )
        return "" if chosen == self.local_url else chosen

    def invoke(self, url: str, spec: dict[str, Any]) -> HttpResponse:
        body = json.dumps(spec, ensure_ascii=False, default=str).encode("utf-8")
        timestamp, nonce = self.app.clock.now(), secrets.token_hex(12)
        request = Request(
            f"{url.rstrip('/')}/agent/v1/invoke",
            data=body,
            method="POST",
            headers={
                "Content-Type": "application/json",
                "X-Aira-Agent-Timestamp": str(timestamp),
                "X-Aira-Agent-Nonce": nonce,
                "X-Aira-Agent-Signature": sign(
                    agent_secret(self.app), timestamp, nonce, body
                ),
            },
        )
        try:
            with urlopen(request, timeout=300) as response:  # noqa: S310 - the URL comes from a registered worker
                reply = json.loads(response.read().decode("utf-8"))
        except HTTPError as error:
            detail = error.read().decode("utf-8", "replace")[:200]
            raise Unavailable(
                f"The worker that holds the LinkedIn session refused the request ({error.code}).",
                detail=detail,
            ) from None
        except (URLError, TimeoutError, OSError, ValueError) as error:
            if not self._healthy(
                url
            ):  # a slow answer is not a dead worker: other tenants stay pinned
                self._forget(url)
            raise Unavailable(
                "The worker that holds the LinkedIn session could not be reached. Try again in a minute.",
                detail=type(error).__name__,
            ) from None
        return HttpResponse(
            int(reply["status"]),
            [(k, v) for k, v in reply["headers"]],
            base64.b64decode(reply["body"]),
        )

    @staticmethod
    def _healthy(url: str) -> bool:
        try:
            with urlopen(f"{url.rstrip('/')}/agent/v1/health", timeout=3) as response:  # noqa: S310
                return bool(response.status == 200)
        except (URLError, TimeoutError, OSError, ValueError):
            return False

    def _forget(self, url: str) -> None:
        """A worker that stopped answering no longer holds the session: the next request picks another."""
        with self.app.db.write() as connection:
            connection.execute(
                "UPDATE workspaces SET linkedin_host = '' WHERE linkedin_host = ?",
                (url,),
            )
            connection.execute(
                "UPDATE worker_heartbeats SET agent_url = '' WHERE agent_url = ?",
                (url,),
            )


class AgentServer:
    """The worker's side: executes a signed, already-authorized request in the bound workspace."""

    def __init__(
        self,
        app: CloudApp,
        gateway: Gateway,
        *,
        host: str = "127.0.0.1",
        port: int = 0,
        advertise: str = "",
    ) -> None:
        self.app = app
        self.gateway = gateway
        self.verifier = Verifier(app)
        agent = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, format: str, *args: Any) -> None:  # noqa: A002, ANN401
                return

            def do_GET(self) -> None:  # noqa: N802
                if self.path == "/agent/v1/health":
                    self._reply(200, {"ok": True})
                else:
                    self._reply(404, {"error": "not found"})

            def do_POST(self) -> None:  # noqa: N802
                length = int(self.headers.get("Content-Length", "0") or 0)
                body = self.rfile.read(length) if 0 < length < 5_000_000 else b""
                if self.path != "/agent/v1/invoke":
                    return self._reply(404, {"error": "not found"})
                try:
                    agent.verify(self.headers, body)
                except PermissionError as error:
                    return self._reply(401, {"error": str(error)})
                try:
                    self._reply(200, agent.execute(json.loads(body)))
                except Exception as error:  # noqa: BLE001 - the web tier reports it; never leak a stack trace
                    logger.exception("Agent request failed")
                    self._reply(500, {"error": f"{type(error).__name__}"})

            def _reply(self, status: int, payload: dict[str, Any]) -> None:
                data = json.dumps(payload).encode()
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

        self._server = ThreadingHTTPServer((host, port), Handler)
        self._server.daemon_threads = True
        bound = self._server.server_address[1]
        self.url = (
            advertise
            or f"http://{host if host not in ('0.0.0.0', '') else socket.gethostname()}:{bound}"
        )
        self._thread = threading.Thread(
            target=self._server.serve_forever, name="agent", daemon=True
        )

    def start(self) -> AgentServer:
        self._thread.start()
        return self

    def stop(self) -> None:
        self._server.shutdown()
        self._server.server_close()

    def verify(self, headers: Any, body: bytes) -> None:
        self.verifier.verify(headers, body)

    def execute(self, spec: dict[str, Any]) -> dict[str, Any]:
        """Run one authorized request: bind the workspace, call the handler, return what it sent."""
        gateway = self.gateway
        local = gateway.local
        method, path, name = spec["method"], spec["path"], spec["route"]
        match = next(
            (
                m
                for verb, pattern, route_name, _a in local.ROUTES
                if verb == method
                and route_name == name
                and (m := pattern.fullmatch(unquote(path)))
            ),
            None,
        )
        if match is None:
            raise LookupError("Unknown route.")
        principal = principal_from(spec.get("principal"))
        captured = Captured()
        request = HttpRequest(
            method,
            path,
            spec.get("query", {}),
            {"host": "127.0.0.1", **spec.get("headers", {})},
            b"",
            spec.get("client_ip", "127.0.0.1"),
        )
        shim = gateway.shim_cls(request, captured, None)
        with self.app.workspaces.bind(
            spec["org_id"],
            spec["candidate_id"],
            principal=principal if principal and principal.email else None,
            kind="request",
            correlation_id=spec.get("correlation_id", ""),
            audience=spec.get("audience", "staff"),
            acting_label=spec.get("acting_label", ""),
        ) as user:
            getattr(shim, name)(
                match, spec.get("payload", {}), spec.get("query", {}), user
            )
        shim.finish_capture()
        return {
            "status": captured.status,
            "headers": captured.headers,
            "body": base64.b64encode(captured.body).decode("ascii"),
        }


def request_spec(ctx: Ctx, acting_label: str) -> dict[str, Any]:
    request = ctx.request
    return {
        "org_id": ctx.org_id,
        "candidate_id": ctx.candidate_id,
        "principal": principal_payload(ctx.principal),
        "route": ctx.name,
        "method": request.method,
        "path": request.path,
        "query": request.query,
        "payload": ctx.payload,
        "headers": {
            "content-type": request.headers.get("content-type", "application/json")
        },
        "client_ip": request.client_ip,
        "correlation_id": ctx.request.headers.get("x-request-id", ""),
        "audience": ctx.audience,
        "acting_label": acting_label,
    }
