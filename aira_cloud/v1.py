"""The SerpApi-compatible API (``/search.json``, ``/v1/...``) for hosted organizations.

A request carries an organization's Aira key, not a session. The key resolves to its organization
through the control-plane index, and the request then runs against that organization's Aira API
workspace (history, cache, keys), shared by all of its candidates. The plan must include ``api.v1``,
the plan's per-minute limit and the key's own hourly limit apply, and live searches are metered
against the monthly allowance.
"""

from __future__ import annotations

import json
import re
from email.message import Message
from typing import TYPE_CHECKING

from aira_cloud import context, pooled
from aira_cloud.db import one
from aira_cloud.errors import CloudError, PlanRequired
from aira_cloud.policy import Rule
from aira_cloud.webio import HttpRequest, HttpResponse, Reply

if TYPE_CHECKING:
    from aira_cloud.gateway import Gateway

DEFAULT_V1_PER_MINUTE = 60


def _headers(request: HttpRequest) -> Message:
    message = Message()
    for name, value in request.headers.items():
        message[name] = value
    return message


def _error(status: int, code: str, message: str) -> HttpResponse:
    return Reply(status, {"error": message, "code": code}).to_response()


def run_v1(
    gw: Gateway,
    request: HttpRequest,
    name: str,
    match: re.Match[str],
    rule: Rule,
    correlation: str,
) -> HttpResponse:
    from aira.airaapi import compat
    from aira.airaapi import http as airaapi_http
    from aira.airaapi import store as airaapi_store
    from aira.airaapi.client import SerpApiError

    app, local = gw.app, gw.local
    entry = next((e for e in airaapi_http.AIRA_ROUTES if e.attr == name), None)
    if entry is None:
        return _error(404, "not_found", "Not found.")
    headers = _headers(request)
    plaintext = compat.extract_key(headers, request.query)
    needs_key = entry.auth == "api_key" or (
        entry.auth == "api_key_optional" and plaintext
    )
    payload = (
        request.json()
        if request.method in {"POST", "PATCH", "PUT"} and request.body
        else {}
    )

    org_id = ""
    reservation = None
    pooled_reservation = None
    try:
        if needs_key:
            limiter_key = f"v1key:{request.client_ip}"
            try:
                if not plaintext or not compat.KEY_PATTERN.fullmatch(plaintext):
                    raise SerpApiError(
                        "invalid_aira_key", compat.INVALID_KEY_MESSAGE, 401
                    )
                with app.db.read() as connection:
                    found = airaapi_store.lookup_key_index(
                        connection, compat.hash_key(plaintext)
                    )
                if not found:
                    raise SerpApiError(
                        "invalid_aira_key", compat.INVALID_KEY_MESSAGE, 401
                    )
                org_id = found
            except SerpApiError as error:
                allowed, retry = gw.auth_limiter.check(limiter_key)
                if not allowed:
                    return _error(
                        429,
                        "rate_limited",
                        f"Too many rejected keys. Try again in {retry} seconds.",
                    )
                gw.auth_limiter.hit(limiter_key)
                return Reply(
                    error.status_for(entry.auth), error.payload()
                ).to_response()
            try:
                entitlements = app.entitlements.require_state(org_id, automation=False)
                app.entitlements.require_feature(
                    org_id, "api.v1", label="The Aira API (/v1)"
                )
            except CloudError as error:
                return Reply(error.status, error.payload()).to_response()
            per_minute = (
                entitlements.limit("api_v1_per_minute") or DEFAULT_V1_PER_MINUTE
            )
            allowed, retry = gw.v1_limiter.check(f"v1:{org_id}", limit=int(per_minute))
            if not allowed:
                return Reply(
                    429,
                    {
                        "error": f"The plan allows {per_minute} requests a minute. Try again in {retry} seconds.",
                        "code": "rate_limited",
                    },
                ).to_response()
            gw.v1_limiter.hit(f"v1:{org_id}")
            if rule.kind == "v1_search":
                try:
                    reservation = app.enforcer.reserve(
                        org_id,
                        "api_searches",
                        monthly_limit_name="api_searches_per_month",
                        audience="staff",
                    )
                    pooled_reservation = pooled.reserve(app, org_id)
                except PlanRequired as error:
                    if reservation is not None:
                        app.usage.release(reservation)
                    return Reply(error.status, error.payload()).to_response()
            binding = app.workspaces.bind_org_api(org_id, correlation_id=correlation)
        else:
            # A keyless call (locations): a shared public workspace, cached and rate limited like any other.
            binding = _public_binding(gw, correlation)
        with binding as user:
            key_row = None
            if needs_key:
                with local._connect() as connection:
                    key_row = dict(
                        compat.authenticate_key(
                            connection, plaintext, airaapi_store.now_utc()
                        )
                    )
            request_object = airaapi_http.Request(
                match=match,
                payload=payload,
                query=request.query,
                user=user,
                headers=headers,
                client=request.client_ip,
                connect=local._connect,
                setting=local._setting,
                secret_store=local._airaapi_secret_store(),
                key_index=app.hosting_key_index(user["user_id"]),
                api_key=key_row,
                audit=local._audit,
            )
            outcome = entry.handler(request_object)
    except SerpApiError as error:
        if reservation is not None:
            app.usage.release(reservation)
        pooled.settle(app, org_id, pooled_reservation, credits=None, ok=False)
        return Reply(error.status_for(entry.auth), error.payload()).to_response()
    except BaseException:
        if reservation is not None:
            app.usage.release(reservation)
        pooled.settle(app, org_id, pooled_reservation, credits=None, ok=False)
        raise
    if isinstance(outcome, airaapi_http.Raw):
        extra = list(airaapi_http.extra_headers(outcome))
        if outcome.filename:
            extra.append(
                ("Content-Disposition", f'attachment; filename="{outcome.filename}"')
            )
        response = HttpResponse(
            outcome.status,
            [("Content-Type", outcome.content_type), *extra],
            outcome.body,
        )
    else:
        status, body = outcome
        response = Reply(status, body).to_response()
    if reservation is not None:
        released = response.status >= 400
        if not released:
            try:
                parsed = json.loads(response.body)
                released = (
                    bool((parsed.get("search_metadata") or {}).get("cache_hit"))
                    if isinstance(parsed, dict)
                    else False
                )
            except ValueError:
                released = False
        if released:
            app.usage.release(reservation)
        pooled.settle(
            app,
            org_id,
            pooled_reservation,
            credits=None if released else 1,
            ok=not released,
        )
    return response


def _public_binding(gw: Gateway, correlation: str):  # noqa: ANN202
    from contextlib import contextmanager

    local = gw.local

    @contextmanager
    def binding():  # noqa: ANN202
        path = gw.app.config.data_root / "public_api.sqlite3"
        user = {
            "user_id": "org-public",
            "email": "",
            "data_path": str(path),
            "linkedin_port": None,
            "created_at": 0,
            "password_hash": "",
        }
        ctx = context.TenantContext(
            org_id="", kind="org_api", correlation_id=correlation
        )
        with context.bound(ctx):
            local._bind_user(user)
            try:
                yield user
            finally:
                local._unbind_user()

    return binding()


__all__ = ["run_v1", "one"]
