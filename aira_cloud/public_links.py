"""Public links for hosted organizations (``/p/<token>``, ``/api/public/<token>``; see aira.suite.public).

A business's customers use these pages without signing in. The token resolves to its organization through
the control-plane index (``public_links``), the organization must be active and still have the app the link
belongs to, and the request then runs against the organization's shared database. A visitor is rate
limited per address; an unknown, revoked or unsubscribed link is the same 404.
"""

from __future__ import annotations

import re
from email.message import Message
from typing import TYPE_CHECKING, Any

from aira_cloud import context
from aira_cloud.db import one, platform_scope, set_scope
from aira_cloud.errors import CloudError
from aira_cloud.webio import HttpRequest, HttpResponse, Reply

if TYPE_CHECKING:
    from aira_cloud.app import CloudApp
    from aira_cloud.gateway import Gateway

NOT_FOUND = {"error": "This link is no longer available.", "code": "not_found"}


class HostedPublicIndex:
    """aira.suite.public's index: token hash -> the organization bound to the request that made the link."""

    def __init__(self, app: CloudApp) -> None:
        self.app = app

    def add(self, token_hash: str, app: str) -> None:
        bound = context.current()
        org_id = bound.org_id if bound else ""
        if not org_id:
            raise RuntimeError("A public link can only be made inside an organization.")
        with self.app.db.write() as connection:
            connection.execute(
                """INSERT INTO public_links (token_hash, org_id, app, created_at) VALUES (?, ?, ?, ?)
                ON CONFLICT (token_hash) DO NOTHING""",
                (token_hash, org_id, app, self.app.clock.now()),
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
                "SELECT org_id, app FROM public_links WHERE token_hash = ?",
                (public.hash_token(token),),
            )


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
    set_scope(org_id)
    try:
        app.entitlements.require_state(
            org_id, write=request.method != "GET", automation=False
        )
        app.entitlements.require_app(org_id, str(found["app"]), audience="candidate")
    except CloudError:
        return Reply(404, NOT_FOUND).to_response(), org_id
    payload = (
        request.json()
        if request.method in {"POST", "PATCH", "PUT"} and request.body
        else {}
    )
    try:
        with app.workspaces.bind_org_api(org_id, correlation_id=correlation) as user:
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
                key_index=app.hosting_key_index(user["user_id"]),
                audit=local._audit,
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
