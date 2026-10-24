"""The platform (vendor) console API. Its own sign-in and cookie, with MFA, never a tenant's login."""

from __future__ import annotations

from aira.observability import metrics
from aira_cloud.db import many
from aira_cloud.errors import Conflict, ValidationFailed
from aira_cloud.webio import OPERATOR_COOKIE, Reply, Req, cloud_route, cookie_header


def _cookie(req: Req, token: str, max_age: int | None = None) -> str:
    return cookie_header(
        OPERATOR_COOKIE,
        token,
        max_age=req.app.config.operator_session_ttl if max_age is None else max_age,
        secure=req.app.config.cookie_secure,
    )


def _me(req: Req) -> dict:
    principal = req.principal
    return {
        "operator_id": principal.operator_id,
        "email": principal.email,
        "name": principal.name,
        "role": principal.operator_role,
        "mfa": {"enabled": principal.mfa_enabled, "verified": principal.mfa_verified},
        "support": {
            "access_id": principal.support_access_id,
            "org_id": principal.org_id,
            "role": principal.role,
        }
        if principal.via_support
        else None,
    }


@cloud_route("POST", r"/api/platform/auth/login", auth="public")
def operator_login(req: Req):  # noqa: ANN201
    body = req.body()
    limiter = req.app.gateway.auth_limiter
    try:
        result = req.app.identity.operator_login(
            str(body.get("email", "")),
            str(body.get("password", "")),
            ip=req.request.client_ip,
            user_agent=req.request.headers.get("user-agent", ""),
        )
    except Exception:
        limiter.hit(f"auth:{req.request.client_ip}")
        raise
    return Reply(
        200,
        {"ok": True, "mfa_enrolled": result["mfa_enrolled"]},
        cookies=[_cookie(req, result["token"])],
    )


@cloud_route("POST", r"/api/platform/auth/logout", auth="public")
def operator_logout(req: Req):  # noqa: ANN201
    req.app.identity.logout(req.request.cookie(OPERATOR_COOKIE))
    return Reply(200, {"ok": True}, cookies=[_cookie(req, "", 0)])


@cloud_route("GET", r"/api/platform/me", auth="operator", mfa=False)
def operator_me(req: Req):  # noqa: ANN201
    return 200, _me(req)


@cloud_route("POST", r"/api/platform/auth/mfa/begin", auth="operator", mfa=False)
def operator_mfa_begin(req: Req):  # noqa: ANN201
    return 200, req.app.identity.mfa_begin(req.principal)


@cloud_route("POST", r"/api/platform/auth/mfa/enable", auth="operator", mfa=False)
def operator_mfa_enable(req: Req):  # noqa: ANN201
    return 200, req.app.identity.mfa_enable(
        req.principal, str(req.body().get("code", ""))
    )


@cloud_route("POST", r"/api/platform/auth/mfa/verify", auth="operator", mfa=False)
def operator_mfa_verify(req: Req):  # noqa: ANN201
    return 200, req.app.identity.mfa_verify(
        req.principal, str(req.body().get("code", ""))
    )


# ----------------------------------------------------------------------------- tenants


@cloud_route("GET", r"/api/platform/tenants", auth="operator")
def tenants(req: Req):  # noqa: ANN201
    return 200, {
        "tenants": req.app.platform.list_tenants(
            req.principal,
            q=req.q("q"),
            status=req.q("status"),
            plan=req.q("plan"),
            limit=req.qint("limit", 100, 1, 500),
            offset=req.qint("offset", 0, 0, 100000),
        )
    }


@cloud_route("GET", r"/api/platform/tenants/([0-9a-f]{32})", auth="operator")
def tenant_detail(req: Req):  # noqa: ANN201
    return 200, req.app.platform.tenant_detail(req.principal, req.group())


def _reason(req: Req) -> str:
    return str(req.body().get("reason", "")).strip()


@cloud_route("POST", r"/api/platform/tenants/([0-9a-f]{32})/suspend", auth="operator")
def tenant_suspend(req: Req):  # noqa: ANN201
    req.app.platform.suspend(req.principal, req.group(), _reason(req))
    return 200, {"ok": True}


@cloud_route("POST", r"/api/platform/tenants/([0-9a-f]{32})/reinstate", auth="operator")
def tenant_reinstate(req: Req):  # noqa: ANN201
    req.app.platform.reinstate(req.principal, req.group())
    return 200, {"ok": True}


@cloud_route(
    "POST", r"/api/platform/tenants/([0-9a-f]{32})/schedule-deletion", auth="operator"
)
def tenant_schedule_deletion(req: Req):  # noqa: ANN201
    days = req.body().get("days")
    return 200, {
        "deletion_at": req.app.platform.schedule_deletion(
            req.principal, req.group(), int(days) if days else None
        )
    }


@cloud_route(
    "POST", r"/api/platform/tenants/([0-9a-f]{32})/cancel-deletion", auth="operator"
)
def tenant_cancel_deletion(req: Req):  # noqa: ANN201
    req.app.platform.cancel_deletion(req.principal, req.group())
    return 200, {"ok": True}


@cloud_route(
    "POST", r"/api/platform/tenants/([0-9a-f]{32})/rate-limit", auth="operator"
)
def tenant_rate_limit(req: Req):  # noqa: ANN201
    body = req.body()
    req.app.platform.rate_limit_tenant(
        req.principal,
        req.group(),
        int(body.get("per_minute", 0)),
        int(body.get("hours", 24)),
    )
    return 200, {"ok": True}


@cloud_route(
    "DELETE", r"/api/platform/tenants/([0-9a-f]{32})/rate-limit", auth="operator"
)
def tenant_rate_limit_clear(req: Req):  # noqa: ANN201
    req.app.platform.clear_rate_limit(req.principal, req.group())
    return 200, {"ok": True}


@cloud_route(
    "POST", r"/api/platform/tenants/([0-9a-f]{32})/extend-trial", auth="operator"
)
def tenant_extend_trial(req: Req):  # noqa: ANN201
    return 200, {
        "trial_end": req.app.billing.extend_trial(
            req.principal, req.group(), int(req.body().get("days", 0))
        )
    }


@cloud_route("GET", r"/api/platform/tenants/([0-9a-f]{32})/limits", auth="operator")
def tenant_limits(req: Req):  # noqa: ANN201
    """The plan's limits, this organization's overrides, and what applies (Enterprise contracts)."""
    return 200, req.app.billing.custom_limits(req.principal, req.group())


@cloud_route("PUT", r"/api/platform/tenants/([0-9a-f]{32})/limits", auth="operator")
def tenant_limits_set(req: Req):  # noqa: ANN201
    """Override plan limits for one organization; ``replace`` drops overrides not listed. Needs a reason."""
    body = req.body()
    return 200, req.app.billing.set_custom_limits(
        req.principal,
        req.group(),
        body.get("limits"),  # type: ignore[arg-type]
        _reason(req),
        replace=bool(body.get("replace")),
    )


@cloud_route("DELETE", r"/api/platform/tenants/([0-9a-f]{32})/limits", auth="operator")
def tenant_limits_clear(req: Req):  # noqa: ANN201
    """Remove every override: the organization goes back to its plan's limits."""
    return 200, req.app.billing.set_custom_limits(
        req.principal,
        req.group(),
        {},
        req.q("reason") or _reason(req),  # a DELETE carries its reason in the query
        replace=True,
    )


@cloud_route("POST", r"/api/platform/tenants/([0-9a-f]{32})/credit", auth="operator")
def tenant_credit(req: Req):  # noqa: ANN201
    body = req.body()
    req.app.billing.issue_credit(
        req.principal, req.group(), int(body.get("amount_cents", 0)), _reason(req)
    )
    return 200, {"ok": True}


@cloud_route(
    "POST", r"/api/platform/tenants/([0-9a-f]{32})/activate-enterprise", auth="operator"
)
def tenant_enterprise(req: Req):  # noqa: ANN201
    body = req.body()
    overrides = body.get("overrides") if isinstance(body.get("overrides"), dict) else {}
    return 200, req.app.billing.activate_enterprise(
        req.principal,
        req.group(),
        seats=int(body.get("seats", 0)),
        overrides=overrides,
        period_days=int(body.get("period_days", 365)),
        invoice_cents=int(body.get("invoice_cents", 0)),
        reference=str(body.get("reference", "")),
    )


@cloud_route(
    "POST", r"/api/platform/tenants/([0-9a-f]{32})/invoice-paid", auth="operator"
)
def tenant_invoice_paid(req: Req):  # noqa: ANN201
    req.app.billing.mark_invoice_paid(
        req.principal, req.group(), str(req.body().get("invoice_id", ""))
    )
    return 200, {"ok": True}


@cloud_route("POST", r"/api/platform/tenants/([0-9a-f]{32})/reconcile", auth="operator")
def tenant_reconcile(req: Req):  # noqa: ANN201
    return 200, req.app.billing.reconcile(req.principal, req.group())


@cloud_route(
    "POST", r"/api/platform/tenants/([0-9a-f]{32})/rotate-keys", auth="operator"
)
def tenant_rotate_keys(req: Req):  # noqa: ANN201
    """Queue a key rotation for the organization (a worker re-encrypts documents and secrets)."""
    req.app.authz.require(req.principal, "platform.tenants.manage")
    req.app.tenancy.org(req.group())
    job = req.app.queue.enqueue(
        req.group(), "keys.rotate", dedupe_key="keys.rotate", priority=7
    )
    req.app.platform_audit.record(
        "tenant.rotate_keys_queued",
        operator=req.principal,
        org_id=req.group(),
        detail={"job_id": job},
    )
    return 202, {"queued": bool(job), "job_id": job}


@cloud_route("POST", r"/api/platform/tenants/([0-9a-f]{32})/backup", auth="operator")
def tenant_backup(req: Req):  # noqa: ANN201
    """Download one organization's backup archive (platform admins only; recorded in the platform audit)."""
    req.app.authz.require(req.principal, "platform.tenants.manage")
    data = req.app.backup.backup_org(req.group(), operator=req.principal)
    return Reply(
        200,
        data,
        content_type="application/zip",
        filename=f"aira-backup-{req.group()[:8]}.zip",
    )


@cloud_route("GET", r"/api/platform/tenants/([0-9a-f]{32})/audit", auth="operator")
def tenant_audit(req: Req):  # noqa: ANN201
    return 200, {
        "events": req.app.platform.export_tenant_audit(req.principal, req.group())
    }


@cloud_route(
    "POST", r"/api/platform/tenants/([0-9a-f]{32})/support-request", auth="operator"
)
def tenant_support_request(req: Req):  # noqa: ANN201
    body = req.body()
    return 201, req.app.platform.request_support(
        req.principal, req.group(), _reason(req), str(body.get("role", "auditor"))
    )


# ----------------------------------------------------------------------------- plans and flags


@cloud_route("GET", r"/api/platform/plans", auth="operator")
def plans(req: Req):  # noqa: ANN201
    return 200, {"plans": req.app.platform.list_plans(req.principal)}


@cloud_route("PUT", r"/api/platform/plans/([a-z]{3,20})", auth="operator")
def plan_update(req: Req):  # noqa: ANN201
    body = req.body()
    version = req.app.platform.update_plan(
        req.principal,
        req.group(),
        entitlements=body.get("entitlements"),
        pricing=body.get("pricing"),
    )
    return 200, {"version": version}


@cloud_route("POST", r"/api/platform/plans/([a-z]{3,20})/migrate", auth="operator")
def plan_migrate(req: Req):  # noqa: ANN201
    body = req.body()
    return 200, {
        "migration": req.app.platform.schedule_migration(
            req.principal,
            req.group(),
            int(body.get("version", 0)),
            int(body.get("effective_at", 0)),
        )
    }


@cloud_route("GET", r"/api/platform/flags", auth="operator")
def flags(req: Req):  # noqa: ANN201
    req.app.authz.require(req.principal, "platform.tenants.read")
    return 200, {"flags": req.app.flags.list()}


@cloud_route("PUT", r"/api/platform/flags", auth="operator")
def flag_set(req: Req):  # noqa: ANN201
    body = req.body()
    req.app.platform.set_flag(
        req.principal,
        str(body.get("flag", "")),
        str(body.get("scope_type", "global")),
        str(body.get("scope_id", "")),
        bool(body.get("enabled", True)),
        int(body.get("percent", 100)),
    )
    return 200, {"ok": True}


@cloud_route("DELETE", r"/api/platform/flags", auth="operator")
def flag_delete(req: Req):  # noqa: ANN201
    req.app.platform.delete_flag(
        req.principal, req.q("flag"), req.q("scope_type"), req.q("scope_id")
    )
    return 200, {"ok": True}


# ----------------------------------------------------------------------------- support access


@cloud_route("GET", r"/api/platform/support", auth="operator")
def support_mine(req: Req):  # noqa: ANN201
    with req.app.db.read() as connection:
        rows = many(
            connection,
            """SELECT a.*, o.name AS org_name FROM support_access a JOIN organizations o ON o.org_id = a.org_id
            WHERE a.operator_id = ? ORDER BY a.requested_at DESC LIMIT 50""",
            (req.principal.operator_id,),
        )
    return 200, {"grants": rows}


@cloud_route("POST", r"/api/platform/support/([0-9a-f]{32})/enter", auth="operator")
def support_enter(req: Req):  # noqa: ANN201
    return 200, req.app.platform.enter_support(req.principal, req.group())


@cloud_route("POST", r"/api/platform/support/leave", auth="operator")
def support_leave(req: Req):  # noqa: ANN201
    req.app.platform.leave_support(req.principal)
    return 200, {"ok": True}


@cloud_route("POST", r"/api/platform/support/([0-9a-f]{32})/end", auth="operator")
def support_end(req: Req):  # noqa: ANN201
    req.app.platform.revoke_support(req.principal, req.group(), "ended by the operator")
    return 200, {"ok": True}


# ----------------------------------------------------------------------------- announcements, health, audit, abuse


@cloud_route("GET", r"/api/platform/announcements", auth="operator")
def announcements(req: Req):  # noqa: ANN201
    return 200, {"announcements": req.app.platform.list_announcements(req.principal)}


@cloud_route("POST", r"/api/platform/announcements", auth="operator")
def announce(req: Req):  # noqa: ANN201
    body = req.body()
    hours = body.get("hours")
    return 201, {
        "announcement_id": req.app.platform.announce(
            req.principal,
            str(body.get("title", "")),
            str(body.get("body", "")),
            scope_type=str(body.get("scope_type", "all")),
            scope_id=str(body.get("scope_id", "")),
            severity=str(body.get("severity", "info")),
            hours=int(hours) if hours else None,
        )
    }


@cloud_route("DELETE", r"/api/platform/announcements/([0-9a-f]{32})", auth="operator")
def announcement_cancel(req: Req):  # noqa: ANN201
    req.app.platform.cancel_announcement(req.principal, req.group())
    return 200, {"ok": True}


@cloud_route("GET", r"/api/platform/health", auth="operator")
def platform_health(req: Req):  # noqa: ANN201
    return 200, req.app.platform.health(req.principal)


@cloud_route("GET", r"/api/platform/metrics", auth="operator")
def platform_metrics(req: Req):  # noqa: ANN201
    req.app.authz.require(req.principal, "platform.health.read")
    with req.app.gateway._stats_lock:
        per_tenant = {
            org: dict(values) for org, values in req.app.gateway.stats.items()
        }
    return 200, {"process": metrics.snapshot(), "per_tenant": per_tenant}


@cloud_route("GET", r"/api/platform/queue", auth="operator")
def platform_queue(req: Req):  # noqa: ANN201
    req.app.authz.require(req.principal, "platform.health.read")
    return 200, req.app.queue.stats()


@cloud_route("GET", r"/api/platform/audit", auth="operator")
def platform_audit(req: Req):  # noqa: ANN201
    req.app.authz.require(req.principal, "platform.audit.read")
    return 200, {
        "events": req.app.platform_audit.list(
            org_id=req.q("org"),
            limit=req.qint("limit", 100, 1, 1000),
            before_seq=req.qint("before", 0, 0, 10**12) or None,
        )
    }


@cloud_route("GET", r"/api/platform/audit/verify", auth="operator")
def platform_audit_verify(req: Req):  # noqa: ANN201
    req.app.authz.require(req.principal, "platform.audit.read")
    return 200, req.app.platform_audit.verify()


@cloud_route("POST", r"/api/platform/sources/block", auth="operator")
def source_block(req: Req):  # noqa: ANN201
    body = req.body()
    req.app.platform.block_source(
        req.principal, str(body.get("source", "")).strip(), str(body.get("reason", ""))
    )
    return 200, {"ok": True}


@cloud_route("DELETE", r"/api/platform/sources/([a-z0-9_-]{1,40})", auth="operator")
def source_unblock(req: Req):  # noqa: ANN201
    req.app.platform.unblock_source(req.principal, req.group())
    return 200, {"ok": True}


@cloud_route("POST", r"/api/platform/dsr", auth="operator")
def data_subject_request(req: Req):  # noqa: ANN201
    body = req.body()
    return 200, req.app.lifecycle.data_subject_request(
        req.principal, str(body.get("kind", "")), str(body.get("email", ""))
    )


@cloud_route("GET", r"/api/platform/operators", auth="operator")
def operators(req: Req):  # noqa: ANN201
    return 200, {"operators": req.app.platform.list_operators(req.principal)}


@cloud_route("POST", r"/api/platform/operators", auth="operator")
def operator_create(req: Req):  # noqa: ANN201
    req.app.authz.require(req.principal, "platform.operators.manage")
    body = req.body()
    operator_id = req.app.identity.create_operator(
        str(body.get("email", "")),
        str(body.get("password", "")),
        role=str(body.get("role", "support")),
        name=str(body.get("name", "")),
        by=req.principal.email,
    )
    return 201, {"operator_id": operator_id}


@cloud_route("PATCH", r"/api/platform/operators/([0-9a-f]{32})", auth="operator")
def operator_status(req: Req):  # noqa: ANN201
    req.app.platform.set_operator_status(
        req.principal, req.group(), str(req.body().get("status", ""))
    )
    return 200, {"ok": True}


__all__ = ["Conflict", "ValidationFailed"]
