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
import threading
import time
from collections import defaultdict
from collections.abc import Callable
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import TYPE_CHECKING, Any
from urllib.parse import unquote, urlsplit

from aira.observability import metrics, new_correlation_id
from aira.security import (
    SECURITY_HEADERS,
    RateLimiter,
    requires_custom_header,
    same_origin,
)
from aira.apps import app_of_path
from aira_cloud import context, redaction
from aira_cloud.db import clear_scope, set_scope
from aira_cloud.errors import (
    CloudError,
    Forbidden,
    NotFound,
    PlanRequired,
    SubscriptionInactive,
    TooManyRequests,
    Unauthorized,
    ValidationFailed,
)
from aira_cloud.gate import policy_source
from aira_cloud.agent import request_spec
from aira_cloud.policy import (
    LINKEDIN_NOTICE,
    POLICY,
    Rule,
    remote_assignment,
    APP_FREE_ROUTES,
)
from aira_cloud.rbac import CANDIDATE, Principal
from aira_cloud.webio import (
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
    from aira_cloud.app import CloudApp

logger = logging.getLogger("aira_cloud.gateway")

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
        self.auth_limiter = RateLimiter(
            limit=30, window_seconds=300, lockout_seconds=300
        )
        self.org_limiter = RateLimiter(
            limit=app.config.org_requests_per_minute, window_seconds=60
        )
        self.member_limiter = RateLimiter(
            limit=app.config.member_requests_per_minute, window_seconds=60
        )
        self.v1_limiter = RateLimiter(limit=10_000, window_seconds=60)
        self.agents = app.agents
        self.stats: dict[str, dict[str, int]] = defaultdict(
            lambda: {"requests": 0, "errors": 0, "limited": 0}
        )
        self._stats_lock = threading.Lock()
        from aira_cloud import route_hooks

        self.hooks = route_hooks.HOOKS
        import aira_cloud.routes  # noqa: F401  (registers the control-plane routes)

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
            request.path,
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
        }
        if not probe and not config.host_allowed(host):
            raise Forbidden(
                "This host is not served by this deployment.", code="bad_host"
            )
        allowed, retry = self.ip_limiter.check(request.client_ip)
        if not allowed:
            raise TooManyRequests(
                f"Too many requests. Try again in {retry} seconds.", retry_after=retry
            )
        self.ip_limiter.hit(request.client_ip)

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
            key = f"member:{principal.actor_id}"
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
                    )
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
        result = route.handler(req)
        if isinstance(result, Reply):
            return result.to_response(), route.name, org_id
        if isinstance(result, HttpResponse):
            return result, route.name, org_id
        status, body = result
        return Reply(status, body).to_response(), route.name, org_id

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
            from aira_cloud.v1 import run_v1

            return run_v1(self, request, name, match, rule, correlation), name, ""
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
        self._require_app_for(request.path, name, org_id, audience)

        candidate_id = ""
        if rule.scope == "candidate":
            candidate_id = self._target_candidate(request, principal)
            self.app.authz.require(
                principal, rule.permission, candidate_id=candidate_id
            )
            if rule.write:
                self._require_active_workspace(org_id, candidate_id)
        else:
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
                with self.app.workspaces.bind_org_api(
                    org_id, principal=principal, correlation_id=correlation
                ) as user:
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
        self, path: str, route_name: str, org_id: str, audience: str
    ) -> None:
        """A route an app owns is refused with ``402 app_required`` until the organization has that app."""
        owner = app_of_path(path)
        if owner is None or not owner.feature or route_name in APP_FREE_ROUTES:
            return
        self.app.entitlements.require_app(org_id, owner.id, audience=audience)

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


class HostedHandler(BaseHTTPRequestHandler):
    server_version = "AiraCloud/1.0"
    protocol_version = "HTTP/1.1"
    gateway: Gateway

    def log_message(self, format: str, *args: Any) -> None:  # noqa: A002 - the gateway logs structured lines itself
        return

    def _serve(self) -> None:
        length = int(self.headers.get("Content-Length", "0") or 0)
        if length > 25_000_000:
            self._write(
                HttpResponse(
                    413,
                    [("Content-Type", "application/json")],
                    b'{"error":"The request body is too large.","code":"too_large"}',
                )
            )
            self.close_connection = True
            return
        body = self.rfile.read(length) if length else b""
        headers = {k.lower(): v for k, v in self.headers.items()}
        config = self.gateway.app.config
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
        self._write(self.gateway.handle(request))

    def _write(self, response: HttpResponse) -> None:
        self.send_response(response.status)
        sent = {name.lower() for name, _ in response.headers}
        for name, value in response.headers:
            self.send_header(name, value)
        if "content-length" not in sent:
            self.send_header("Content-Length", str(len(response.body)))
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(response.body)

    do_GET = do_POST = do_PATCH = do_PUT = do_DELETE = do_HEAD = _serve  # noqa: N815


def make_server(
    gateway: Gateway, host: str | None = None, port: int | None = None
) -> ThreadingHTTPServer:
    config = gateway.app.config

    class Handler(HostedHandler):
        pass

    Handler.gateway = gateway
    server = ThreadingHTTPServer(
        (host or config.host, config.port if port is None else port), Handler
    )
    server.daemon_threads = True
    if config.tls_cert and config.tls_key:
        import ssl

        context_ = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        context_.minimum_version = ssl.TLSVersion.TLSv1_2
        context_.load_cert_chain(config.tls_cert, config.tls_key)
        server.socket = context_.wrap_socket(server.socket, server_side=True)
    return server


__all__ = [
    "Gateway",
    "HostedHandler",
    "make_server",
    "Ctx",
    "LINKEDIN_NOTICE",
    "json",
    "context",
    "PlanRequired",
]
