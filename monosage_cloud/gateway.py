"""The hosted API gateway.

Every request goes through one pipeline before any domain code runs:

  host check → rate limits (client, organization, member) → session → verification and MFA gates →
  route policy → subscription state → permission for the candidate in question → plan features and
  limits → organization policy → bind the tenant → run the handler → redact → meter → audit

The organization and candidate come from the session and the route policy, never from a request body.
A request that names another organization's ids is a 404.
"""

from __future__ import annotations

import json
import logging
import re
import socket
import threading
import time
from collections import defaultdict
from collections.abc import Callable
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import TYPE_CHECKING, Any
from urllib.parse import unquote, urlsplit

from aira.observability import metrics, new_correlation_id, redact_path
from aira.security import (
    SECURITY_HEADERS,
    RateLimiter,
    requires_custom_header,
    same_origin,
)
from aira.apps import app_of_path
from monosage_cloud import context, redaction, spaces
from monosage_cloud.db import clear_scope, set_scope
from monosage_cloud.errors import (
    CloudError,
    Forbidden,
    NotFound,
    PlanRequired,
    SubscriptionInactive,
    TooManyRequests,
    Unauthorized,
    ValidationFailed,
)
from monosage_cloud.gate import policy_source
from monosage_cloud.agent import request_spec
from monosage_cloud.policy import (
    LINKEDIN_NOTICE,
    POLICY,
    Rule,
    remote_assignment,
    APP_FREE_ROUTES,
)
from monosage_cloud.rbac import CANDIDATE, PERSONAL_ROLES, Principal
from monosage_cloud.webio import (
    CLOUD_ROUTES,
    OPERATOR_COOKIE,
    SESSION_COOKIE,
    Captured,
    HttpRequest,
    HttpResponse,
    Reply,
    Req,
    make_shim,
)

if TYPE_CHECKING:
    from monosage_cloud.app import CloudApp

logger = logging.getLogger("monosage_cloud.gateway")

MUTATING = {"POST", "PATCH", "PUT", "DELETE"}
KNOWN_SOURCES = {
    "linkedin",
    "google_jobs",
    "aira_jobs",
    "greenhouse",
    "lever",
    "ashby",
    "smartrecruiters",
}


def _local() -> Any:
    import auto_apply_assistant

    return auto_apply_assistant


def member_bucket(principal: Principal) -> str:
    """The per-caller rate-limit bucket: the member, or for an app acting for its organization (a
    client-credentials token, no member) that organization and app, never one bucket every organization's apps share."""
    if principal.extra.get("service") or not principal.actor_id:
        client = str((principal.extra.get("oauth") or {}).get("client_id") or "")
        return f"app:{principal.org_id}:{client}"
    return f"member:{principal.actor_id}"


def machine_key(
    kind: str, request: HttpRequest, match: re.Match[str], route_name: str
) -> str:
    """The bucket a machine caller is limited in: the provider, the OAuth client or the SCIM token, not the address."""
    import base64
    import hashlib

    if kind == "webhook":
        provider = match.group(1) if match.re.groups else route_name
        return f"webhook:{provider}"
    authorization = request.headers.get("authorization", "")
    if kind == "oauth":
        client = ""
        if authorization[:6].lower() == "basic ":
            try:
                client = (
                    base64.b64decode(authorization[6:].strip())
                    .decode("utf-8")
                    .partition(":")[0]
                )
            except (ValueError, UnicodeDecodeError):
                client = ""
        if not client:
            try:
                client = str(request.json().get("client_id") or "")
            except ValueError:
                client = ""
        return (
            f"oauth:{client[:80]}" if client else f"oauth-anonymous:{request.client_ip}"
        )
    if kind == "scim":
        token = authorization[7:].strip() if authorization[:7].lower() == "bearer " else ""
        if token:
            return f"scim:{hashlib.sha256(token.encode('utf-8', 'ignore')).hexdigest()[:24]}"
        return f"scim-anonymous:{request.client_ip}"
    return f"{kind}:{route_name}"


@dataclass
class Ctx:
    """Everything the per-kind hooks of a local route need."""

    gateway: Gateway
    request: HttpRequest
    principal: Principal
    org_id: str
    candidate_id: str
    rule: Rule
    name: str
    payload: dict[str, Any]
    match: re.Match[str]
    audience: str = "staff"
    state: dict[str, Any] = field(default_factory=dict)
    headers: list[tuple[str, str]] = field(default_factory=list)

    @property
    def app(self) -> CloudApp:
        return self.gateway.app


class Gateway:
    def __init__(self, app: CloudApp) -> None:
        self.app = app
        app.gateway = self  # type: ignore[attr-defined]
        local = _local()
        self.local = local
        self.shim_cls = make_shim(local.RequestHandler)
        self.ip_limiter = RateLimiter(
            limit=app.config.ip_requests_per_minute, window_seconds=60
        )
        # Every page load fetches about 80 scripts and styles (cheap, revalidated by ETag): they count here, so
        # opening a dozen apps in a minute does not spend the allowance the API calls need.
        self.static_limiter = RateLimiter(
            limit=app.config.ip_requests_per_minute * 10, window_seconds=60
        )
        self.auth_limiter = RateLimiter(
            limit=30, window_seconds=300, lockout_seconds=300
        )
        # Signed and credentialed machine callers (``cloud_route(machine=...)``: billing webhooks, SCIM, the OAuth
        # token endpoint, the signed internal callbacks) never share the sign-in limiter: a renewal burst from the
        # billing provider must not lock it out, and a person's failed sign-ins must not stop SCIM. They are limited
        # per provider, client or token, and rejected credentials count per address on a limiter of their own.
        self.machine_limiter = RateLimiter(
            limit=app.config.machine_requests_per_minute, window_seconds=60
        )
        self.machine_failure_limiter = RateLimiter(
            limit=app.config.machine_failures_per_window,
            window_seconds=300,
            lockout_seconds=300,
        )
        self.org_limiter = RateLimiter(
            limit=app.config.org_requests_per_minute, window_seconds=60
        )
        self.member_limiter = RateLimiter(
            limit=app.config.member_requests_per_minute, window_seconds=60
        )
        self.v1_limiter = RateLimiter(limit=10_000, window_seconds=60)
        # Customers on a business's public pages: generous for reading, tight for actions.
        self.public_view_limiter = RateLimiter(limit=300, window_seconds=60)
        self.public_action_limiter = RateLimiter(limit=60, window_seconds=60)
        self.agents = app.agents
        self.server: HostedServer | None = None  # set by make_server
        self.stats: dict[str, dict[str, int]] = defaultdict(
            lambda: {"requests": 0, "errors": 0, "limited": 0}
        )
        self._stats_lock = threading.Lock()
        from monosage_cloud import route_hooks

        self.hooks = route_hooks.HOOKS
        import monosage_cloud.routes  # noqa: F401  (registers the control-plane routes)

    # ------------------------------------------------------------------ entry point

    def handle(self, request: HttpRequest) -> HttpResponse:
        correlation = request.headers.get("x-request-id", "")
        if not re.fullmatch(r"[A-Za-z0-9-]{4,64}", correlation or ""):
            correlation = new_correlation_id()
        started = time.monotonic()
        route_name, org_id = "unmatched", ""
        try:
            response, route_name, org_id = self._handle(request, correlation)
        except CloudError as error:
            if isinstance(error, TooManyRequests):
                pass
            response = self._error(error.status, error.payload())
        except ValueError as error:
            response = self._error(400, {"error": str(error), "code": "invalid"})
        except LookupError as error:
            response = self._error(
                404, {"error": str(error) or "Not found.", "code": "not_found"}
            )
        except PermissionError as error:
            response = self._error(
                403, {"error": str(error) or "Not allowed.", "code": "forbidden"}
            )
        except RuntimeError as error:
            logger.warning(
                "Upstream failure: %s", error, extra={"correlation_id": correlation}
            )
            response = self._error(502, {"error": str(error), "code": "upstream"})
        except Exception as error:  # noqa: BLE001 - never leak internals; the correlation id finds the log line
            logger.exception("Request failed", extra={"correlation_id": correlation})
            response = self._error(
                500,
                {
                    "error": f"Something went wrong: {type(error).__name__}. Quote request {correlation} to support.",
                    "code": "internal",
                },
            )
        finally:
            clear_scope()  # the request's tenant scope (set once the caller is known) ends with it
        duration = (time.monotonic() - started) * 1000
        for name, value in SECURITY_HEADERS:
            if not response.header(name):
                response.headers.append((name, value))
        response.headers.append(("X-Request-ID", correlation))
        if self.app.config.cookie_secure or (
            self.app.config.trust_proxy
            and request.headers.get("x-forwarded-proto") == "https"
        ):
            response.headers.append(
                ("Strict-Transport-Security", "max-age=31536000; includeSubDomains")
            )
        metrics.record_request(route_name, response.status, duration)
        self._count(org_id, response.status)
        logger.info(
            "%s %s -> %s",
            request.method,
            redact_path(request.path),  # capability links (exports, invitations, public pages) never reach a log
            response.status,
            extra={
                "correlation_id": correlation,
                "org_id": org_id,
                "duration_ms": round(duration, 1),
            },
        )
        return response

    def _error(self, status: int, body: dict[str, Any]) -> HttpResponse:
        return Reply(status, body).to_response()

    def _count(self, org_id: str, status: int) -> None:
        if not org_id:
            return
        with self._stats_lock:
            entry = self.stats[org_id]
            entry["requests"] += 1
            if status >= 500:
                entry["errors"] += 1
            if status == 429:
                entry["limited"] += 1

    # ------------------------------------------------------------------ pipeline

    def _handle(
        self, request: HttpRequest, correlation: str
    ) -> tuple[HttpResponse, str, str]:
        config = self.app.config
        host = request.headers.get("host", "")
        # Orchestrator probes address the pod by IP; the two probe endpoints reveal nothing and use no host name.
        probe = request.method == "GET" and request.path in {
            "/api/health",
            "/api/ready",
            "/metrics",  # a scraper addresses the pod by IP too; the endpoint needs its bearer token
        }
        if not probe and not config.host_allowed(host):
            raise Forbidden(
                "This host is not served by this deployment.", code="bad_host"
            )
        if (
            probe
            and request.path == "/api/ready"
            and self.server is not None
            and self.server.draining.is_set()
        ):
            # SIGTERM: the orchestrator stops sending traffic while in-flight requests finish
            return (
                self._error(503, {"status": "draining", "code": "draining"}),
                "ready",
                "",
            )
        self.app.host_guard.beat()  # this node still uses the data directory (app.HostGuard)
        limiter = (
            self.static_limiter
            if request.method == "GET" and request.path.startswith("/static/")
            else self.ip_limiter
        )
        allowed, retry = limiter.check(request.client_ip)
        if not allowed:
            raise TooManyRequests(
                f"Too many requests. Try again in {retry} seconds.", retry_after=retry
            )
        limiter.hit(request.client_ip)

        cloud = self._match_cloud(request)
        local_match = None if cloud else self._match_local(request)
        if cloud is None and local_match is None:
            return (
                self._error(404, {"error": "Not found.", "code": "not_found"}),
                "unmatched",
                "",
            )
        if (
            request.method in MUTATING
            and (cloud is None or cloud[0].csrf)
            and not self._origin_ok(request)
        ):
            raise Forbidden(
                "Cross-origin requests are not allowed.", code="cross_origin"
            )

        if cloud is not None:
            route, match = cloud
            return self._run_cloud(request, route, match, correlation)
        assert (
            local_match is not None
        )  # one of the two matched, or we returned the 404 above
        name, match = local_match
        return self._run_local(request, name, match, correlation)

    def _origin_ok(self, request: HttpRequest) -> bool:
        origin = request.headers.get("origin")
        host = request.headers.get("host", "")
        fetch_site = request.headers.get("sec-fetch-site", "")
        if origin == "null" and fetch_site == "same-origin":
            # The app sends Referrer-Policy: no-referrer, so a browser labels its own same-origin form posts "Origin: null".
            # Sec-Fetch-Site is set by the browser and cannot be forged by a page; "same-origin" is the truth about the sender.
            origin = None
        if not same_origin(origin, host):
            return False
        if fetch_site and fetch_site not in {"same-origin", "none"}:
            return False
        return (
            origin is not None
            or requires_custom_header(
                {"X-Requested-With": request.headers.get("x-requested-with", "")}
            )
            or not fetch_site
        )

    def _match_cloud(self, request: HttpRequest):  # noqa: ANN202
        path = unquote(request.path)
        for route in CLOUD_ROUTES:
            if route.method == request.method:
                match = route.pattern.fullmatch(path)
                if match:
                    return route, match
        return None

    def _match_local(self, request: HttpRequest):  # noqa: ANN202
        path = unquote(request.path)
        for method, pattern, name, _auth in self.local.ROUTES:
            if method == request.method:
                match = pattern.fullmatch(path)
                if match:
                    return name, match
        return None

    # ------------------------------------------------------------------ principals

    def principal_for(self, request: HttpRequest, *, kind: str) -> Principal | None:
        identity = self.app.identity
        if kind == "operator":
            return identity.principal_for_token(
                request.cookie(OPERATOR_COOKIE), kind="operator"
            )
        member = identity.principal_for_token(
            request.cookie(SESSION_COOKIE), kind="member"
        )
        if member is not None:
            return member
        # An app or a script with an OAuth access token or a personal access token (monosage_cloud/oauth.py).
        authorization = request.headers.get("authorization", "")
        if authorization[:7].lower() == "bearer ":
            token = authorization[7:].strip()
            principal = self.app.oauth.principal(token)
            if principal is None:
                raise Unauthorized(
                    "The access token is not valid or has expired.",
                    code="invalid_token",
                )
            return principal
        operator = identity.principal_for_token(
            request.cookie(OPERATOR_COOKIE), kind="operator"
        )
        if operator is not None and operator.via_support:
            return operator
        return None

    def check_session_gates(
        self, principal: Principal, *, verified: bool, mfa: bool
    ) -> None:
        if principal.is_operator:
            if mfa and not principal.mfa_verified:
                raise Unauthorized(
                    "Operators sign in with a second factor.", code="mfa_required"
                )
            return
        if verified and not principal.extra.get("email_verified", True):
            raise Forbidden(
                "Verify your email address to continue.", code="email_unverified"
            )
        if mfa:
            if principal.mfa_enabled and not principal.mfa_verified:
                raise Unauthorized(
                    "Enter your second factor to continue.", code="mfa_required"
                )
            if not principal.mfa_enabled and self.app.identity.mfa_required_for(
                principal
            ):
                raise Forbidden(
                    "Your role requires multi-factor authentication. Set it up to continue.",
                    code="mfa_enrollment_required",
                )

    def rate_check(self, principal: Principal | None, org_id: str) -> None:
        if not org_id:
            return
        limit = self.app.config.org_requests_per_minute
        try:
            org = self.app.tenancy.org(org_id)
            if (
                org.get("rate_limited_until")
                and org["rate_limited_until"] > self.app.clock.now()
                and org.get("rate_limit_per_minute") is not None
            ):
                limit = max(1, int(org["rate_limit_per_minute"]))
        except NotFound:
            pass
        allowed, retry = self.org_limiter.check(f"org:{org_id}", limit=limit)
        if not allowed:
            raise TooManyRequests(
                f"This organization is sending too many requests. Try again in {retry} seconds.",
                retry_after=retry,
            )
        self.org_limiter.hit(f"org:{org_id}")
        if principal is not None:
            key = member_bucket(principal)
            allowed, retry = self.member_limiter.check(key)
            if not allowed:
                raise TooManyRequests(
                    f"Too many requests from you. Try again in {retry} seconds.",
                    retry_after=retry,
                )
            self.member_limiter.hit(key)

    # ------------------------------------------------------------------ control-plane routes

    def _run_cloud(
        self, request: HttpRequest, route, match: re.Match[str], correlation: str
    ):  # noqa: ANN001, ANN202
        principal: Principal | None = None
        org_id = ""
        if route.auth in {"member", "operator"}:
            principal = self.principal_for(
                request, kind="operator" if route.auth == "operator" else "member"
            )
            if principal is None:
                raise Unauthorized("Sign in to continue.")
            if (
                route.auth == "member"
                and principal.is_operator
                and not principal.via_support
            ):
                raise Unauthorized("Sign in to continue.")
            self.check_session_gates(principal, verified=route.verified, mfa=route.mfa)
            self.app.oauth.require_scope(principal, request.path, request.method)
            if route.auth == "member" and request.path.startswith("/api/me/"):
                principal = self._own_workspace(request, principal)
            org_id = principal.org_id
            if org_id:
                if route.auth == "member":
                    set_scope(
                        org_id
                    )  # operator routes act on the operator's own rows: platform scope
                self.rate_check(principal, org_id)
                self.app.entitlements.require_state(org_id, write=route.writes)
                if route.auth == "member":
                    self._require_app_for(
                        request.path,
                        route.name,
                        org_id,
                        "candidate" if principal.role == CANDIDATE else "staff",
                        principal,
                    )
        elif route.machine:
            self._machine_check(request, route, match)
        elif route.auth == "public" and route.method in MUTATING:
            allowed, retry = self.auth_limiter.check(f"auth:{request.client_ip}")
            if not allowed:
                raise TooManyRequests(
                    f"Too many attempts. Try again in {retry // 60 + 1} minutes.",
                    retry_after=retry,
                )
        payload: dict[str, Any] = {}
        if request.method in {"POST", "PATCH", "PUT"} and request.body:
            payload = request.json()
        req = Req(self.app, request, match, principal, correlation, payload)
        try:
            result = route.handler(req)
        except (CloudError, PermissionError) as error:
            if route.machine and getattr(error, "status", 403) in {401, 403}:
                self.machine_failure_limiter.hit(f"machine-fail:{request.client_ip}")
            raise
        if isinstance(result, Reply):
            response = result.to_response()
        elif isinstance(result, HttpResponse):
            response = result
        else:
            status, body = result
            response = Reply(status, body).to_response()
        if route.machine and response.status in {401, 403}:
            # a rejected signature, client secret or SCIM token: guessing is limited per address
            self.machine_failure_limiter.hit(f"machine-fail:{request.client_ip}")
        return response, route.name, org_id

    def _machine_check(self, request: HttpRequest, route, match: re.Match[str]) -> None:  # noqa: ANN001
        """The limits of a signed machine route: per caller (not per address), and per address for rejections."""
        allowed, retry = self.machine_failure_limiter.check(
            f"machine-fail:{request.client_ip}"
        )
        if not allowed:
            raise TooManyRequests(
                f"Too many rejected credentials. Try again in {retry} seconds.",
                retry_after=retry,
            )
        key = machine_key(route.machine, request, match, route.name)
        allowed, retry = self.machine_limiter.check(key)
        if not allowed:
            raise TooManyRequests(
                f"Too many requests. Try again in {retry} seconds.", retry_after=retry
            )
        self.machine_limiter.hit(key)

    # ------------------------------------------------------------------ the local build's routes

    def _run_local(
        self, request: HttpRequest, name: str, match: re.Match[str], correlation: str
    ):  # noqa: ANN202
        rule = POLICY.get(name, Rule("deny"))
        if rule.scope == "deny":
            raise Forbidden(
                "This is not available in the hosted product.", code="not_available"
            )
        if rule.scope == "public":
            return self._execute_public(request, name, match), name, ""
        if rule.scope == "apikey":
            from monosage_cloud.v1 import run_v1

            return run_v1(self, request, name, match, rule, correlation), name, ""
        if rule.scope == "public_link":
            from monosage_cloud.public_links import run_public_link

            response, org_id = run_public_link(self, request, name, match, correlation)
            return response, name, org_id
        if rule.scope == "operator":
            principal = self.principal_for(request, kind="operator")
            if principal is None:
                raise Unauthorized("Sign in to continue.")
            self.check_session_gates(principal, verified=False, mfa=True)
            self.app.authz.require(principal, rule.permission)
            return (
                self._execute_shim(
                    request, name, match, {}, user={}, correlation=correlation
                ),
                name,
                "",
            )

        principal = self.principal_for(request, kind="member")
        if principal is None:
            raise Unauthorized("Sign in to continue.")
        self.check_session_gates(principal, verified=True, mfa=True)
        self.app.oauth.require_scope(principal, request.path, request.method)
        if rule.scope == "candidate":
            principal = self._own_workspace(request, principal)
        org_id = principal.org_id
        set_scope(org_id)
        self.rate_check(principal, org_id)
        audience = "candidate" if principal.role == CANDIDATE else "staff"
        payload: dict[str, Any] = (
            request.json()
            if request.method in {"POST", "PATCH", "PUT"} and request.body
            else {}
        )
        entitlements = self.app.entitlements.require_state(org_id, write=rule.write)
        self._require_app_for(request.path, name, org_id, audience, principal)

        candidate_id = ""
        space = ""
        if rule.scope == "candidate":
            candidate_id = self._target_candidate(request, principal)
            self.app.authz.require(
                principal, rule.permission, candidate_id=candidate_id
            )
            if rule.write:
                self._require_active_workspace(org_id, candidate_id)
        else:
            policy = self.app.tenancy.policy(org_id)
            space = spaces.choose(request.headers, principal, policy, request.path)
            # In a member's own space they are its owner; in the organization's apps a member it named works as its
            # staff do. Everyone else needs the organization's suite permissions.
            if not (
                space == spaces.MEMBER
                or (space == spaces.COMPANY and spaces.named_member(principal, policy))
            ):
                self.app.authz.require(principal, rule.permission)
        if rule.feature:
            self.app.entitlements.require_feature(
                org_id,
                rule.feature,
                audience=audience,
                label=rule.feature.split(".")[-1].replace("_", " ").title(),
            )
        if rule.linkedin:
            self.require_linkedin(org_id, candidate_id, audience)
        del entitlements

        ctx = Ctx(
            self,
            request,
            principal,
            org_id,
            candidate_id,
            rule,
            name,
            payload,
            match,
            audience,
        )
        hook = self.hooks.get(rule.kind)
        if hook and hook.pre:
            hook.pre(ctx)
        sensitive_ok = rule.scope != "candidate" or self.app.authz.can_see_sensitive(
            principal, candidate_id
        )
        transform: Callable[[int, dict[str, Any]], dict[str, Any]] | None = None
        if rule.kind in {"profile_read", "runs_read"} and not sensitive_ok:
            kind = rule.kind

            def transform(status: int, body: dict[str, Any]) -> dict[str, Any]:
                if isinstance(body, dict) and status < 400:
                    return redaction.redact(kind, body)
                return body

        response: HttpResponse | None = None
        failure: BaseException | None = None
        try:
            if rule.scope == "candidate":
                acting = principal.member_id != candidate_id
                label = principal.label
                if acting:
                    label = f"{principal.label} for {self.app.tenancy.member(org_id, candidate_id)['email']}"
                with self.app.workspaces.bind(
                    org_id,
                    candidate_id,
                    principal=principal,
                    correlation_id=correlation,
                    audience=audience,
                    acting_label=label,
                ) as user:
                    ctx.state["user"] = user
                    if hook and hook.inside:
                        hook.inside(ctx)
                    response = self._execute_candidate(
                        ctx, match, user, correlation, transform, label
                    )
                if acting:
                    response.headers.append(
                        (
                            "X-Aira-Acting-For",
                            self.app.tenancy.member(org_id, candidate_id)["email"],
                        )
                    )
            else:
                binding = (
                    self.app.workspaces.bind_member_apps(
                        org_id,
                        principal.member_id,
                        principal=principal,
                        correlation_id=correlation,
                    )
                    if space == spaces.MEMBER
                    else self.app.workspaces.bind_org_api(
                        org_id, principal=principal, correlation_id=correlation
                    )
                )
                with binding as user:
                    ctx.state["user"] = user
                    if hook and hook.inside:
                        hook.inside(ctx)
                    response = self._execute_shim(
                        request,
                        name,
                        match,
                        ctx.payload,
                        user=user,
                        correlation=correlation,
                        transform=transform,
                    )
        except BaseException as error:
            failure = error
            raise
        finally:
            if hook and hook.post:
                try:
                    hook.post(ctx, response, failure)
                except Exception:  # noqa: BLE001 - metering and audit must not mask the handler's own outcome
                    logger.exception("Post hook %s failed", rule.kind)
        status = response.status if response else 500
        for header in ctx.headers:
            response.headers.append(header)
        if rule.write and status < 400:
            # a plan, schedule or monitor may have changed: the scheduler reads this workspace on its next tick
            self.app.scheduler.touch(
                org_id, candidate_id if rule.scope == "candidate" else ""
            )
        if rule.audit or (rule.audit is None and rule.write):
            self.app.audit.record(
                org_id,
                f"{'candidate' if rule.scope == 'candidate' else 'integrations'}.{name.removeprefix('api_').removeprefix('airaapi__routes__')}",
                actor=principal,
                candidate_id=candidate_id,
                target_type="route",
                target_id=name,
                detail={
                    "method": request.method,
                    "status": status,
                    **ctx.state.get("audit", {}),
                },
                source="api",
                correlation_id=correlation,
            )
        return response, name, org_id

    def _execute_candidate(
        self,
        ctx: Ctx,
        match: re.Match[str],
        user: dict[str, Any],
        correlation: str,
        transform: Any,
        label: str,
    ) -> HttpResponse:
        """Run the handler here, or on the worker host that holds the candidate's LinkedIn session."""
        assign = (
            ctx.state[
                "remote_assign"
            ]  # a hook that read the workspace decided (a saved Sales Navigator search)
            if "remote_assign" in ctx.state
            else remote_assignment(ctx.name, ctx.rule, ctx.payload)
        )
        target = (
            self.agents.target(ctx.org_id, ctx.candidate_id, assign=bool(assign))
            if assign is not None
            else ""
        )
        if not target:
            return self._execute_shim(
                ctx.request,
                ctx.name,
                match,
                ctx.payload,
                user=user,
                correlation=correlation,
                transform=transform,
            )
        response = self.agents.invoke(target, request_spec(ctx, label))
        if (
            transform is not None
            and response.status < 400
            and response.header("Content-Type").startswith("application/json")
        ):
            try:
                body = transform(response.status, json.loads(response.body))
                response = HttpResponse(
                    response.status,
                    response.headers,
                    json.dumps(body, ensure_ascii=False).encode("utf-8"),
                )
            except ValueError:
                pass
        return response

    def _execute_public(
        self, request: HttpRequest, name: str, match: re.Match[str]
    ) -> HttpResponse:
        return self._execute_shim(request, name, match, {}, user=None, correlation="")

    def _execute_shim(
        self,
        request: HttpRequest,
        name: str,
        match: re.Match[str],
        payload: dict[str, Any],
        *,
        user: dict[str, Any] | None,
        correlation: str,
        transform: Callable[[int, dict[str, Any]], dict[str, Any]] | None = None,
    ) -> HttpResponse:
        captured = Captured()
        local_request = HttpRequest(
            request.method,
            request.path,
            request.query,
            {**request.headers, "host": "127.0.0.1"},
            request.body,
            request.client_ip,
        )
        shim = self.shim_cls(local_request, captured, transform)
        getattr(shim, name)(match, payload, request.query, user)
        shim.finish_capture()
        return HttpResponse(captured.status, list(captured.headers), captured.body)

    # ------------------------------------------------------------------ helpers the hooks use

    def _require_app_for(
        self,
        path: str,
        route_name: str,
        org_id: str,
        audience: str,
        principal: Principal | None = None,
    ) -> None:
        """A route an app owns is refused with ``402 app_required`` until the organization has that app, and with
        ``403 app_not_allowed`` when the organization has not given it to this member (app_access.py)."""
        owner = app_of_path(path)
        if owner is None or route_name in APP_FREE_ROUTES:
            return
        if owner.feature:
            self.app.entitlements.require_app(org_id, owner.id, audience=audience)
        from monosage_cloud import app_access

        app_access.require(self.app.tenancy.policy(org_id), principal, owner.id)

    def _own_workspace(self, request: HttpRequest, principal: Principal) -> Principal:
        """An owner or admin who names their own workspace acts as its candidate for this request.

        Everything a candidate route checks (consent, privacy, the candidate's own switches) then applies to them
        in their own workspace, while their organization role is unchanged everywhere else.
        """
        if principal.is_operator or principal.role not in PERSONAL_ROLES:
            return principal
        explicit = request.headers.get("x-aira-candidate", "") or request.q("candidate")
        if explicit != principal.member_id:
            return principal
        if not self.app.tenancy.has_personal_workspace(
            principal.org_id, principal.member_id
        ):
            raise NotFound(
                "Start your own workspace first.", code="personal_workspace_missing"
            )
        return principal.as_own_candidate()

    def _target_candidate(self, request: HttpRequest, principal: Principal) -> str:
        explicit = request.headers.get("x-aira-candidate", "") or request.q("candidate")
        if principal.role == CANDIDATE and not principal.is_operator:
            candidate_id = explicit or principal.member_id
            if candidate_id != principal.member_id:
                self.app.authz.candidate(
                    principal.org_id, candidate_id
                )  # 404 if it is not in this organization
                raise Forbidden(
                    "You can only act on your own workspace.", code="not_your_workspace"
                )
            return candidate_id
        if not explicit:
            raise ValidationFailed(
                "Choose a candidate: send the X-Aira-Candidate header.",
                code="candidate_required",
            )
        if not re.fullmatch(r"[0-9a-f]{32}", explicit):
            raise NotFound("Candidate not found.")
        return explicit

    def _require_active_workspace(self, org_id: str, candidate_id: str) -> None:
        workspace = self.app.workspaces.row(org_id, candidate_id)
        if workspace["status"] != "active":
            raise SubscriptionInactive(
                "This candidate's workspace is archived and read-only.",
                code="workspace_archived",
            )

    def require_linkedin(self, org_id: str, candidate_id: str, audience: str) -> None:
        self.app.entitlements.require_feature(
            org_id, "sources.linkedin", audience=audience, label="LinkedIn"
        )
        reason = self.app.gate.source_block(org_id, "linkedin", candidate_id)
        if reason:
            raise Forbidden(reason[0].upper() + reason[1:], code="linkedin_off")

    def check_source(
        self, org_id: str, candidate_id: str, source: str, audience: str
    ) -> None:
        source = str(source or "").strip().lower()
        if source not in KNOWN_SOURCES:
            return  # the local code answers "Unknown job source." itself
        name = policy_source(source)
        self.app.entitlements.require_feature(
            org_id,
            f"sources.{name}",
            audience=audience,
            label=name.replace("_", " ").title(),
        )
        reason = self.app.gate.source_block(org_id, source, candidate_id)
        if reason:
            raise Forbidden(reason[0].upper() + reason[1:], code="source_blocked")


# =============================================================================== HTTP server


def client_address(peer: str, forwarded_for: str, config: Any) -> str:
    """The caller's address for rate limits and lockouts.

    Behind trusted proxies each one appends the address it saw, so only the entries they added
    (the last ``trusted_proxy_hops``) can be believed; anything further left is client-supplied.
    """
    if not config.trust_proxy:
        return peer
    hops = [h.strip() for h in forwarded_for.split(",") if h.strip()]
    if not hops:
        return peer
    return hops[max(0, len(hops) - max(1, int(config.trusted_proxy_hops)))]


class HostedServer(ThreadingHTTPServer):
    """The standard-library threaded server with the limits a public tier needs.

    * at most ``max_connections`` connections at once (one thread each): past that a connection gets ``503`` at
      once instead of a thread, and near the cap answers close their connection so idle keep-alives free up;
    * a socket timeout on every connection (``HostedHandler.timeout``), so a slow or silent client cannot hold one;
    * a listen backlog of ``backlog`` (the standard library's default is 5);
    * bodies over ``large_body_bytes`` share ``large_body_slots``, so a burst of uploads cannot hold gigabytes;
    * :meth:`drain` for SIGTERM: readiness answers 503, answers close their connections, then the server stops
      accepting and waits for what is in flight.
    """

    daemon_threads = True
    allow_reuse_address = True

    def __init__(
        self,
        address: tuple[str, int],
        handler: type[BaseHTTPRequestHandler],
        *,
        max_connections: int = 512,
        backlog: int = 128,
        large_body_slots: int = 8,
    ) -> None:
        self.request_queue_size = max(5, int(backlog))  # read by server_activate, inside __init__
        self.max_connections = max(1, int(max_connections))
        self.connection_slots = threading.BoundedSemaphore(self.max_connections)
        self.large_bodies = threading.BoundedSemaphore(max(1, int(large_body_slots)))
        self.draining = threading.Event()
        self.drained = threading.Event()
        self.refused = 0
        self._active = 0
        self._active_lock = threading.Condition()
        self._connections: dict[Any, bool] = {}  # socket -> answering a request now
        super().__init__(address, handler)

    # -- accounting ----------------------------------------------------------------------------

    @property
    def active(self) -> int:
        with self._active_lock:
            return self._active

    def near_capacity(self) -> bool:
        return self.active >= max(1, int(self.max_connections * 0.8))

    def process_request(self, request: Any, client_address: Any) -> None:
        if self.draining.is_set() or not self.connection_slots.acquire(blocking=False):
            self.refused += 1
            self._refuse(request)
            return
        with self._active_lock:
            self._active += 1
            self._connections[request] = False
        try:
            super().process_request(request, client_address)
        except Exception:
            self._finished(request)
            raise

    def process_request_thread(self, request: Any, client_address: Any) -> None:
        try:
            super().process_request_thread(request, client_address)
        finally:
            self._finished(request)

    def _finished(self, request: Any) -> None:
        with self._active_lock:
            if self._connections.pop(request, None) is None:
                return
            self._active -= 1
            self._active_lock.notify_all()
        self.connection_slots.release()

    def mark(self, request: Any, busy: bool) -> None:
        with self._active_lock:
            if request in self._connections:
                self._connections[request] = busy

    def _refuse(self, request: Any) -> None:
        import ssl

        if not isinstance(request, ssl.SSLSocket):  # no handshake yet: just close a TLS connection
            body = b'{"error":"The server is busy. Try again in a moment.","code":"busy"}'
            try:
                request.settimeout(2)
                request.sendall(
                    b"HTTP/1.1 503 Service Unavailable\r\nContent-Type: application/json\r\n"
                    b"Retry-After: 2\r\nConnection: close\r\nContent-Length: "
                    + str(len(body)).encode()
                    + b"\r\n\r\n"
                    + body
                )
            except OSError:
                pass
        self.shutdown_request(request)

    def handle_error(self, request: Any, client_address: Any) -> None:
        logger.debug("Connection error from %s", client_address[0], exc_info=True)

    # -- SIGTERM ---------------------------------------------------------------------------------

    def drain(self, timeout: float, *, grace: float = 0.0) -> bool:
        """Stop gracefully: True when every in-flight request finished within ``timeout`` seconds.

        For ``grace`` seconds the server keeps accepting (the orchestrator is still taking the pod out of the
        service) while readiness answers 503 and every answer closes its connection; then it stops accepting,
        closes idle keep-alive connections and waits for the requests still running.
        """
        self.draining.set()
        if grace > 0:
            time.sleep(grace)
        self.shutdown()  # serve_forever returns; no new connections are taken
        with self._active_lock:
            idle = [sock for sock, busy in self._connections.items() if not busy]
        for sock in idle:
            try:
                sock.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
        deadline = time.monotonic() + max(0.0, timeout)
        with self._active_lock:
            while self._active > 0:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    break
                self._active_lock.wait(remaining)
            finished = self._active == 0
        self.drained.set()
        if not finished:
            logger.warning(
                "Stopped with %d requests still running after %ss", self.active, timeout
            )
        return finished


def _plain(status: int, message: str, code: str) -> HttpResponse:
    return HttpResponse(
        status,
        [("Content-Type", "application/json")],
        json.dumps({"error": message, "code": code}).encode("utf-8"),
    )


class HostedHandler(BaseHTTPRequestHandler):
    server_version = "AiraCloud/1.0"
    protocol_version = "HTTP/1.1"
    gateway: Gateway
    server: HostedServer  # type: ignore[assignment]
    timeout = 75  # seconds a connection may sit without sending (make_server sets AIRA_CLOUD_HTTP_TIMEOUT)

    def log_message(self, format: str, *args: Any) -> None:  # noqa: A002 - the gateway logs structured lines itself
        return

    def setup(self) -> None:
        super().setup()
        import ssl

        if isinstance(self.connection, ssl.SSLSocket):
            self.connection.do_handshake()  # here, in this connection's thread, under its timeout

    def _serve(self) -> None:
        server = self.server
        tracked = isinstance(server, HostedServer)
        if tracked:
            server.mark(self.request, True)
        large = False
        try:
            config = self.gateway.app.config
            refusal = None
            if self.headers.get("Transfer-Encoding"):
                refusal = _plain(
                    411, "Send the body with a Content-Length.", "length_required"
                )
            raw_length = str(self.headers.get("Content-Length", "") or "0").strip()
            length = int(raw_length) if raw_length.isdigit() else -1
            if refusal is None and length < 0:
                refusal = _plain(400, "The Content-Length is not valid.", "invalid")
            if refusal is None and length > config.http_max_body_bytes:
                refusal = _plain(413, "The request body is too large.", "too_large")
            if refusal is None and tracked and length > config.http_large_body_bytes:
                large = server.large_bodies.acquire(blocking=False)
                if not large:
                    refusal = _plain(
                        503, "Too many uploads at once. Try again in a moment.", "busy"
                    )
            if refusal is not None:
                # the body is never read: close the connection so its bytes are not taken for the next request
                self.close_connection = True
                self._write(refusal)
                return
            body = self.rfile.read(length) if length else b""
            if len(body) < length:
                self.close_connection = True  # the client went away mid-body
                return
            headers = {k.lower(): v for k, v in self.headers.items()}
            client = client_address(
                self.client_address[0], headers.get("x-forwarded-for", ""), config
            )
            parts = urlsplit(self.path)
            from urllib.parse import parse_qs

            request = HttpRequest(
                self.command,
                parts.path,
                parse_qs(parts.query),
                headers,
                body,
                client,
                headers.get("host", ""),
            )
            response = self.gateway.handle(request)
            if tracked and (server.draining.is_set() or server.near_capacity()):
                self.close_connection = True
            self._write(response)
        finally:
            if large:
                server.large_bodies.release()
            if tracked:
                server.mark(self.request, False)

    def _write(self, response: HttpResponse) -> None:
        if response.stream is not None:
            if self.command != "HEAD":
                self._write_stream(response)
                return
            _end_stream(response)  # a HEAD request gets the headers only; the stream still releases what it holds
        self.send_response(response.status)
        sent = {name.lower() for name, _ in response.headers}
        for name, value in response.headers:
            self.send_header(name, value)
        if "content-length" not in sent:
            self.send_header("Content-Length", str(len(response.body)))
        if self.close_connection and "connection" not in sent:
            self.send_header("Connection", "close")
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(response.body)

    def _write_stream(self, response: HttpResponse) -> None:
        """A Server-Sent Events response: headers now, then text as it happens, until the stream ends."""
        self.close_connection = True
        self.send_response(response.status)
        for name, value in response.headers:
            if name.lower() not in {"content-length", "connection"}:
                self.send_header(name, value)
        self.send_header("Connection", "close")
        self.end_headers()
        self.wfile.flush()
        server = self.server
        draining = getattr(server, "draining", None)

        def write(text: str) -> None:
            self.wfile.write(text.encode("utf-8"))
            self.wfile.flush()

        def stop() -> bool:
            return bool(draining is not None and draining.is_set())

        try:
            assert response.stream is not None
            response.stream(write, stop)
        except OSError:
            pass  # the page went away; nothing more can be written

    do_GET = do_POST = do_PATCH = do_PUT = do_DELETE = do_HEAD = _serve  # noqa: N815


def _end_stream(response: HttpResponse) -> None:
    """Run a stream that will not be sent, so it releases its slot at once (its first write fails)."""

    def refuse(_text: str) -> None:
        raise ConnectionAbortedError("not streamed")

    try:
        if response.stream is not None:
            response.stream(refuse, lambda: True)
    except OSError:
        pass


def make_server(
    gateway: Gateway, host: str | None = None, port: int | None = None
) -> HostedServer:
    config = gateway.app.config

    class Handler(HostedHandler):
        pass

    Handler.gateway = gateway
    Handler.timeout = max(1, int(config.http_socket_timeout))
    server = HostedServer(
        (host or config.host, config.port if port is None else port),
        Handler,
        max_connections=config.http_max_connections,
        backlog=config.http_backlog,
        large_body_slots=config.http_large_body_slots,
    )
    gateway.server = server  # readiness answers 503 while it drains
    if config.tls_cert and config.tls_key:
        import ssl

        context_ = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        context_.minimum_version = ssl.TLSVersion.TLSv1_2
        context_.load_cert_chain(config.tls_cert, config.tls_key)
        server.socket = context_.wrap_socket(
            server.socket, server_side=True, do_handshake_on_connect=False
        )
    return server


__all__ = [
    "Gateway",
    "HostedHandler",
    "HostedServer",
    "make_server",
    "Ctx",
    "LINKEDIN_NOTICE",
    "json",
    "context",
    "PlanRequired",
]
