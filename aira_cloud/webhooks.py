"""Webhooks: an organization's apps tell other systems what happened, as signed HTTPS posts.

* An admin registers an endpoint (an https address on the public internet) and the events it wants (``*`` for
  all, ``shop.*``, or exact ones such as ``bookings.booked``). Each endpoint has a signing secret, shown once
  and kept encrypted with the organization's key.
* Events come from the apps' activity feed (aira.suite.kit.log): every line an industry or life app records is
  an event ``<app>.<kind>`` with its title and reference. Personal apps (Careers, Health, Family...) are not sent.
* A delivery is a JSON POST with ``Aira-Signature: t=<unix time>,v1=<hex HMAC-SHA256 of "<t>.<body>">`` and
  ``Aira-Event`` / ``Aira-Delivery`` headers. A 2xx answer is delivered; anything else is retried after 1, 5 and
  30 minutes and then marked failed. Redirects are not followed; the address is resolved and every resolved
  address must be public (no private, loopback or link-local hosts), and the connection goes to that address.
* Deliveries run on the job queue (``webhook.deliver``) and are listed per endpoint for 30 days.
"""

from __future__ import annotations

import hashlib
import hmac
import http.client
import ipaddress
import json
import os
import socket
import ssl
from typing import TYPE_CHECKING, Any
from urllib.parse import urlsplit

from aira import apps as app_catalog
from aira_cloud import context, crypto
from aira_cloud.db import dumps, loads, many, new_id, one, platform_scope
from aira_cloud.errors import Conflict, NotFound, ValidationFailed
from aira_cloud.oauth import PERSONAL_APPS
from aira_cloud.rbac import Principal

if TYPE_CHECKING:
    from aira_cloud.app import CloudApp

ALLOW_LOCAL_ENV = (
    "AIRA_WEBHOOKS_ALLOW_LOCAL"  # tests only: deliver to a stub on the loopback
)
RETRY_MINUTES = (1, 5, 30)
MAX_ENDPOINTS = 20
TIMEOUT = 10


def _allow_local() -> bool:
    return os.environ.get(ALLOW_LOCAL_ENV, "") == "1"


def check_url(url: Any) -> str:
    text = str(url or "").strip()
    parts = urlsplit(text)
    if parts.scheme != "https" and not (_allow_local() and parts.scheme == "http"):
        raise ValidationFailed("A webhook address starts with https://.")
    if not parts.hostname or parts.username or parts.password:
        raise ValidationFailed("That is not a usable webhook address.")
    return text


def _resolve(host: str, port: int) -> str:
    """One public address for ``host`` (every address it resolves to must be public)."""
    try:
        infos = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    except socket.gaierror as error:
        raise ConnectionError(f"Could not find {host}.") from error
    addresses = sorted({info[4][0] for info in infos})
    for address in addresses:
        ip = ipaddress.ip_address(address)
        if not ip.is_global and not (_allow_local() and ip.is_loopback):
            raise ConnectionError(f"{host} is not a public address.")
    if not addresses:
        raise ConnectionError(f"Could not find {host}.")
    return str(addresses[0])


def post(url: str, body: bytes, headers: dict[str, str]) -> int:
    """POST to the checked address (no redirects); returns the status."""
    parts = urlsplit(url)
    host = parts.hostname or ""
    port = parts.port or (443 if parts.scheme == "https" else 80)
    address = _resolve(host, port)
    path = parts.path or "/"
    if parts.query:
        path += "?" + parts.query
    if parts.scheme == "https":
        context_ = ssl.create_default_context()

        class Pinned(http.client.HTTPSConnection):
            def connect(self) -> None:
                raw = socket.create_connection((address, port), timeout=TIMEOUT)
                self.sock = context_.wrap_socket(raw, server_hostname=host)

        connection: http.client.HTTPConnection = Pinned(
            host, port, timeout=TIMEOUT, context=context_
        )
    else:
        connection = http.client.HTTPConnection(address, port, timeout=TIMEOUT)
    try:
        connection.request(
            "POST", path, body=body, headers={**headers, "Host": parts.netloc}
        )
        response = connection.getresponse()
        response.read(4096)
        return response.status
    finally:
        connection.close()


def sign(secret: str, timestamp: int, body: bytes) -> str:
    mac = hmac.new(
        secret.encode("utf-8"), f"{timestamp}.".encode() + body, hashlib.sha256
    ).hexdigest()
    return f"t={timestamp},v1={mac}"


def matches(patterns: list[str], event: str) -> bool:
    for pattern in patterns:
        if pattern in ("*", event) or (
            pattern.endswith(".*") and event.startswith(pattern[:-1])
        ):
            return True
    return False


def event_names() -> list[dict[str, str]]:
    """What can be subscribed to (the apps; each app's events are ``<app>.<kind>``)."""
    out = [{"event": "*", "label": "Everything below"}]
    for app in app_catalog.APPS:
        if app.id in PERSONAL_APPS or app.category == "core":
            continue
        for section in app.sections:
            out.append(
                {
                    "event": f"{section}.*",
                    "label": f"{app.name}: everything in {section}",
                }
            )
    return out


class Webhooks:
    def __init__(self, app: CloudApp) -> None:
        self.app = app

    @property
    def db(self):  # noqa: ANN201
        return self.app.db

    @property
    def now(self) -> int:
        return self.app.clock.now()

    def _public(self, row: dict[str, Any]) -> dict[str, Any]:
        return {
            "endpoint_id": row["endpoint_id"],
            "url": row["url"],
            "events": loads(row["events"], []),
            "description": row["description"],
            "active": bool(row["active"]),
            "created_at": row["created_at"],
        }

    def endpoints(self, actor: Principal) -> list[dict[str, Any]]:
        self.app.authz.require(actor, "integrations.read")
        with self.db.read() as connection:
            rows = many(
                connection,
                "SELECT * FROM webhook_endpoints WHERE org_id = ? AND deleted_at IS NULL ORDER BY created_at",
                (actor.org_id,),
            )
        return [self._public(row) for row in rows]

    def _events(self, raw: Any) -> list[str]:
        events = [str(e).strip() for e in (raw or []) if str(e).strip()]
        if not events:
            raise ValidationFailed("Choose at least one event.")
        for event in events:
            if event != "*" and not (
                event.replace(".", "")
                .replace("*", "")
                .replace("_", "")
                .replace("-", "")
                .isalnum()
            ):
                raise ValidationFailed(f"{event} is not an event name.")
        return events[:50]

    def create(self, actor: Principal, raw: dict[str, Any]) -> dict[str, Any]:
        self.app.authz.require(actor, "integrations.manage")
        self.app.entitlements.require_state(actor.org_id, write=True)
        url = check_url(raw.get("url"))
        events = self._events(raw.get("events"))
        if len(self.endpoints(actor)) >= MAX_ENDPOINTS:
            raise Conflict(
                f"An organization can have at most {MAX_ENDPOINTS} webhook endpoints."
            )
        endpoint_id = new_id()
        secret = "whsec_" + crypto.new_token(24)
        with self.db.write() as connection:
            connection.execute(
                """INSERT INTO webhook_endpoints (org_id, endpoint_id, url, events, description, active, created_by, created_at)
                VALUES (?, ?, ?, ?, ?, 1, ?, ?)""",
                (
                    actor.org_id,
                    endpoint_id,
                    url,
                    dumps(events),
                    str(raw.get("description") or "")[:200],
                    actor.actor_id,
                    self.now,
                ),
            )
            self.app.audit.record(
                actor.org_id,
                "webhook.create",
                actor=actor,
                target_type="webhook",
                target_id=endpoint_id,
                detail={"url": url, "events": events},
                connection=connection,
            )
        self.app.secrets.put(actor.org_id, f"webhook:{endpoint_id}", secret)
        return {**self._public(self._row(actor.org_id, endpoint_id)), "secret": secret}

    def _row(self, org_id: str, endpoint_id: str) -> dict[str, Any]:
        with self.db.read() as connection:
            row = one(
                connection,
                "SELECT * FROM webhook_endpoints WHERE org_id = ? AND endpoint_id = ? AND deleted_at IS NULL",
                (org_id, endpoint_id),
            )
        if row is None:
            raise NotFound("Webhook endpoint not found.")
        return row

    def update(
        self, actor: Principal, endpoint_id: str, raw: dict[str, Any]
    ) -> dict[str, Any]:
        self.app.authz.require(actor, "integrations.manage")
        self._row(actor.org_id, endpoint_id)
        values: dict[str, Any] = {}
        if "url" in raw:
            values["url"] = check_url(raw["url"])
        if "events" in raw:
            values["events"] = dumps(self._events(raw["events"]))
        if "active" in raw:
            values["active"] = 1 if raw["active"] else 0
        if "description" in raw:
            values["description"] = str(raw["description"] or "")[:200]
        if values:
            sets = ", ".join(f"{k} = ?" for k in values)
            with self.db.write() as connection:
                connection.execute(
                    f"UPDATE webhook_endpoints SET {sets} WHERE endpoint_id = ? AND org_id = ?",
                    (*values.values(), endpoint_id, actor.org_id),
                )  # noqa: S608 - fixed names
                self.app.audit.record(
                    actor.org_id,
                    "webhook.update",
                    actor=actor,
                    target_type="webhook",
                    target_id=endpoint_id,
                    detail={"fields": sorted(values)},
                    connection=connection,
                )
        return self._public(self._row(actor.org_id, endpoint_id))

    def delete(self, actor: Principal, endpoint_id: str) -> None:
        self.app.authz.require(actor, "integrations.manage")
        self._row(actor.org_id, endpoint_id)
        with self.db.write() as connection:
            connection.execute(
                "UPDATE webhook_endpoints SET deleted_at = ?, active = 0 WHERE endpoint_id = ? AND org_id = ?",
                (self.now, endpoint_id, actor.org_id),
            )
            self.app.audit.record(
                actor.org_id,
                "webhook.delete",
                actor=actor,
                target_type="webhook",
                target_id=endpoint_id,
                connection=connection,
            )
        self.app.secrets.delete(actor.org_id, f"webhook:{endpoint_id}")

    def rotate_secret(self, actor: Principal, endpoint_id: str) -> dict[str, Any]:
        self.app.authz.require(actor, "integrations.manage")
        self._row(actor.org_id, endpoint_id)
        secret = "whsec_" + crypto.new_token(24)
        self.app.secrets.put(actor.org_id, f"webhook:{endpoint_id}", secret)
        self.app.audit.record(
            actor.org_id,
            "webhook.secret_rotate",
            actor=actor,
            target_type="webhook",
            target_id=endpoint_id,
        )
        return {"endpoint_id": endpoint_id, "secret": secret}

    def deliveries(self, actor: Principal, endpoint_id: str) -> list[dict[str, Any]]:
        self.app.authz.require(actor, "integrations.read")
        self._row(actor.org_id, endpoint_id)
        with self.db.read() as connection:
            rows = many(
                connection,
                "SELECT delivery_id, event, status, attempts, response_status, last_error, created_at, delivered_at FROM webhook_deliveries WHERE org_id = ? AND endpoint_id = ? ORDER BY created_at DESC LIMIT 100",
                (actor.org_id, endpoint_id),
            )
        return rows

    def send_test(self, actor: Principal, endpoint_id: str) -> dict[str, Any]:
        self.app.authz.require(actor, "integrations.manage")
        row = self._row(actor.org_id, endpoint_id)
        delivery = self._enqueue(
            actor.org_id,
            row,
            "aira.test",
            {"title": "A test event from Monosage", "ref_type": "", "ref_id": ""},
        )
        return self.deliver(actor.org_id, delivery)

    # -- events -------------------------------------------------------------------------------

    def emit(self, org_id: str, app_id: str, kind: str, data: dict[str, Any]) -> int:
        """An app recorded something: queue it for every endpoint that wants it. Returns how many."""
        if not org_id or app_id in PERSONAL_APPS or app_id in ("core", "life"):
            return 0
        owner = (
            app_catalog.owner_of(app_id)
            if hasattr(app_catalog, "owner_of")
            else app_catalog.BY_ID.get(app_id)
        )
        if owner is not None and owner.id in PERSONAL_APPS:
            return 0
        event = f"{app_id}.{kind}"
        with platform_scope(), self.db.read() as connection:
            rows = many(
                connection,
                "SELECT * FROM webhook_endpoints WHERE org_id = ? AND active = 1 AND deleted_at IS NULL",
                (org_id,),
            )
        queued = 0
        for row in rows:
            if matches(loads(row["events"], []), event):
                delivery = self._enqueue(org_id, row, event, data)
                self.app.queue.enqueue(
                    org_id,
                    "webhook.deliver",
                    payload={"delivery_id": delivery},
                    priority=6,
                )
                queued += 1
        return queued

    def _enqueue(
        self, org_id: str, endpoint: dict[str, Any], event: str, data: dict[str, Any]
    ) -> str:
        delivery_id = new_id()
        body = {
            "id": delivery_id,
            "type": event,
            "created": self.now,
            "organization": org_id,
            "data": data,
        }
        with platform_scope(), self.db.write() as connection:
            connection.execute(
                """INSERT INTO webhook_deliveries (org_id, delivery_id, endpoint_id, event, payload, status, attempts, next_attempt_at, created_at)
                VALUES (?, ?, ?, ?, ?, 'pending', 0, ?, ?)""",
                (
                    org_id,
                    delivery_id,
                    endpoint["endpoint_id"],
                    event,
                    json.dumps(body, ensure_ascii=False, separators=(",", ":")),
                    self.now,
                    self.now,
                ),
            )
        return delivery_id

    def deliver(self, org_id: str, delivery_id: str) -> dict[str, Any]:
        """One attempt; schedules the next one on failure. Returns the delivery's state."""
        with platform_scope(), self.db.read() as connection:
            delivery = one(
                connection,
                "SELECT * FROM webhook_deliveries WHERE delivery_id = ? AND org_id = ?",
                (delivery_id, org_id),
            )
            endpoint = (
                one(
                    connection,
                    "SELECT * FROM webhook_endpoints WHERE endpoint_id = ? AND org_id = ?",
                    (delivery["endpoint_id"], org_id),
                )
                if delivery
                else None
            )
        if (
            delivery is None
            or endpoint is None
            or delivery["status"] in ("delivered", "failed")
        ):
            return {"status": delivery["status"] if delivery else "missing"}
        if endpoint["deleted_at"] or not endpoint["active"]:
            self._finish(delivery_id, "failed", None, "The endpoint was turned off.")
            return {"status": "failed"}
        secret = (
            self.app.secrets.get(org_id, f"webhook:{endpoint['endpoint_id']}") or ""
        )
        body = delivery["payload"].encode("utf-8")
        timestamp = self.now
        headers = {
            "Content-Type": "application/json",
            "User-Agent": "Monosage-Webhooks/1",
            "Monosage-Event": delivery["event"],
            "Monosage-Delivery": delivery_id,
            "Monosage-Signature": sign(secret, timestamp, body),
        }
        attempts = int(delivery["attempts"]) + 1
        status: int | None = None
        error = ""
        try:
            status = post(endpoint["url"], body, headers)
        except Exception as caught:  # noqa: BLE001 - every failure is recorded and retried
            error = str(caught)[:300] or type(caught).__name__
        if status is not None and 200 <= status < 300:
            self._finish(delivery_id, "delivered", status, "", attempts=attempts)
            return {
                "status": "delivered",
                "response_status": status,
                "attempts": attempts,
            }
        error = error or f"The endpoint answered {status}."
        if attempts > len(RETRY_MINUTES):
            self._finish(delivery_id, "failed", status, error, attempts=attempts)
            return {
                "status": "failed",
                "response_status": status,
                "error": error,
                "attempts": attempts,
            }
        next_at = self.now + RETRY_MINUTES[attempts - 1] * 60
        with platform_scope(), self.db.write() as connection:
            connection.execute(
                "UPDATE webhook_deliveries SET attempts = ?, response_status = ?, last_error = ?, next_attempt_at = ? WHERE delivery_id = ?",
                (attempts, status, error, next_at, delivery_id),
            )
        self.app.queue.enqueue(
            org_id,
            "webhook.deliver",
            payload={"delivery_id": delivery_id},
            run_at=next_at,
            priority=6,
            dedupe_key=f"webhook:{delivery_id}:{attempts}",
        )
        return {
            "status": "retrying",
            "response_status": status,
            "error": error,
            "attempts": attempts,
            "next_attempt_at": next_at,
        }

    def _finish(
        self,
        delivery_id: str,
        status: str,
        response_status: int | None,
        error: str,
        *,
        attempts: int | None = None,
    ) -> None:
        with platform_scope(), self.db.write() as connection:
            connection.execute(
                "UPDATE webhook_deliveries SET status = ?, response_status = ?, last_error = ?, delivered_at = ?, attempts = COALESCE(?, attempts) WHERE delivery_id = ?",
                (
                    status,
                    response_status,
                    error,
                    self.now if status == "delivered" else None,
                    attempts,
                    delivery_id,
                ),
            )


def install_listener(app: CloudApp) -> None:
    """Turn the apps' activity lines into webhook events for the organization bound to the request."""
    from aira.suite import kit

    def listener(app_id: str, kind: str, record: dict[str, Any]) -> None:
        bound = context.current()
        org_id = bound.org_id if bound else ""
        if not org_id:
            return
        try:
            app.webhooks.emit(
                org_id,
                app_id,
                kind,
                {
                    "title": record.get("title", ""),
                    "detail": record.get("detail", ""),
                    "ref_type": record.get("ref_type", ""),
                    "ref_id": record.get("ref_id", ""),
                    "amount_cents": record.get("amount_cents", 0),
                    "by_customer": bool(record.get("public")),
                },
            )
        except Exception:  # noqa: BLE001 - a webhook problem never fails the app's own work
            import logging

            logging.getLogger("aira.cloud.webhooks").exception(
                "Queuing webhook events failed"
            )

    kit.set_event_listener(listener)
