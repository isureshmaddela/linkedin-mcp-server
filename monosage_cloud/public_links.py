"""Public links for hosted organizations (``/p/<token>``, ``/api/public/<token>``; see aira.suite.public).

A business's customers use these pages without signing in. The token resolves to its organization through
the control-plane index (``public_links``), the organization must be active and still have the app the link
belongs to, and the request then runs against the organization's shared database. A visitor is rate
limited per address; an unknown, revoked or unsubscribed link is the same 404.

A link made inside a member's own space (monosage_cloud/spaces.py: their shop, booking page, course or site) also
records the member, and its visitors run in that member's database. Such a link answers only while the member is
an active member and the organization gives members their own apps; a removed member's links are the same 404.
"""

from __future__ import annotations

import re
from email.message import Message
from typing import TYPE_CHECKING, Any

from monosage_cloud import context
from monosage_cloud.db import one, platform_scope, set_scope
from monosage_cloud.errors import CloudError
from monosage_cloud.webio import HttpRequest, HttpResponse, Reply

if TYPE_CHECKING:
    from monosage_cloud.app import CloudApp
    from monosage_cloud.gateway import Gateway

NOT_FOUND = {"error": "This link is no longer available.", "code": "not_found"}


def _webhook_limiter() -> Any:
    from aira.security import RateLimiter

    return RateLimiter(limit=600, window_seconds=60)


_WEBHOOK_LIMITER = _webhook_limiter()


class HostedPublicIndex:
    """aira.suite.public's index: token hash -> the organization bound to the request that made the link."""

    def __init__(self, app: CloudApp) -> None:
        self.app = app

    def add(self, token_hash: str, app: str) -> None:
        bound = context.current()
        org_id = bound.org_id if bound else ""
        if not org_id:
            raise RuntimeError("A public link can only be made inside an organization.")
        # made in a member's own space: their visitors run in that member's database
        member_id = bound.member_id if bound and bound.kind == "member_apps" else ""
        with self.app.db.write() as connection:
            connection.execute(
                """INSERT INTO public_links (token_hash, org_id, app, created_at, member_id) VALUES (?, ?, ?, ?, ?)
                ON CONFLICT (token_hash) DO NOTHING""",
                (token_hash, org_id, app, self.app.clock.now(), member_id),
            )

    def remove(self, token_hash: str) -> None:
        with self.app.db.write() as connection:
            connection.execute(
                "DELETE FROM public_links WHERE token_hash = ?", (token_hash,)
            )

    def lookup(self, token: str) -> dict[str, Any] | None:
        from aira.suite import public

        with platform_scope(), self.app.db.read() as connection:
            return one(
                connection,
                "SELECT org_id, app, member_id FROM public_links WHERE token_hash = ?",
                (public.hash_token(token),),
            )

    def remove_member(self, org_id: str, member_id: str) -> int:
        """Forget every link made in a member's own space (their space is gone)."""
        if not member_id:
            return 0
        with self.app.db.write() as connection:
            return connection.execute(
                "DELETE FROM public_links WHERE org_id = ? AND member_id = ?",
                (org_id, member_id),
            ).rowcount


def _headers(request: HttpRequest) -> Message:
    message = Message()
    for name, value in request.headers.items():
        message[name] = value
    return message


def run_public_link(
    gw: Gateway, request: HttpRequest, name: str, match: re.Match[str], correlation: str
) -> tuple[HttpResponse, str]:
    """Answer a public link route; returns (response, org_id for the request log)."""
    from aira.airaapi import http as airaapi_http

    app, local = gw.app, gw.local
    entry = next((e for e in airaapi_http.AIRA_ROUTES if e.attr == name), None)
    if entry is None:
        return Reply(404, NOT_FOUND).to_response(), ""
    limiter = (
        gw.public_view_limiter if request.method == "GET" else gw.public_action_limiter
    )
    key = f"public:{request.client_ip}"
    if request.path.startswith("/api/payments/"):
        # a payment provider's signed webhooks (aira/payments): their own, larger limit, not the customers'
        limiter, key = _WEBHOOK_LIMITER, f"webhook:{request.client_ip}"
        if len(request.body or b"") > 256_000:
            return Reply(
                413, {"error": "The webhook body is too large.", "code": "too_large"}
            ).to_response(), ""
    allowed, retry = limiter.check(key)
    if not allowed:
        return (
            Reply(
                429,
                {
                    "error": f"Too many requests. Try again in {retry} seconds.",
                    "code": "rate_limited",
                },
            ).to_response(),
            "",
        )
    limiter.hit(key)
    found = app.public_index.lookup(match.group(1))
    if not found:
        return Reply(404, NOT_FOUND).to_response(), ""
    org_id = str(found["org_id"])
    member_id = str(found.get("member_id") or "")
    set_scope(org_id)
    try:
        app.entitlements.require_state(
            org_id, write=request.method != "GET", automation=False
        )
        app.entitlements.require_app(org_id, str(found["app"]), audience="candidate")
    except CloudError:
        return Reply(404, NOT_FOUND).to_response(), org_id
    if member_id and not app.workspaces.member_apps_live(org_id, member_id):
        return Reply(404, NOT_FOUND).to_response(), org_id
    payload = (
        request.json()
        if request.method in {"POST", "PATCH", "PUT"} and request.body
        else {}
    )
    try:
        binding = (
            app.workspaces.bind_member_apps(
                org_id, member_id, correlation_id=correlation
            )
            if member_id
            else app.workspaces.bind_org_api(org_id, correlation_id=correlation)
        )
        with binding as user:
            local._request_context.actor = "public:visitor"
            request_object = airaapi_http.Request(
                match=match,
                payload=payload,
                query=request.query,
                user=user,
                headers=_headers(request),
                client=request.client_ip,
                connect=local._connect,
                setting=local._setting,
                secret_store=local._airaapi_secret_store(),
                key_index=app.hosting_key_index(org_id),
                audit=local._audit,
                body=bytes(request.body or b""),  # a webhook's signature is over these bytes
            )
            outcome = entry.handler(request_object)
    except LookupError as error:
        return Reply(
            404, {"error": str(error) or NOT_FOUND["error"], "code": "not_found"}
        ).to_response(), org_id
    except PermissionError as error:
        return Reply(
            403, {"error": str(error), "code": "forbidden"}
        ).to_response(), org_id
    except ValueError as error:
        return Reply(
            400, {"error": str(error), "code": "invalid"}
        ).to_response(), org_id
    if isinstance(outcome, airaapi_http.Raw):
        return (
            HttpResponse(
                outcome.status,
                [
                    ("Content-Type", outcome.content_type),
                    *airaapi_http.extra_headers(outcome),
                ],
                outcome.body,
            ),
            org_id,
        )
    status, body = outcome
    return Reply(status, body).to_response(), org_id
