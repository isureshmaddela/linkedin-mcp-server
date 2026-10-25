"""Apps: the catalog every member sees, and the per-app subscriptions an organization turns on and off."""

from __future__ import annotations

from aira import apps as app_catalog
from aira_cloud.errors import NotFound, ValidationFailed
from aira_cloud.webio import Req, cloud_route


@cloud_route("GET", r"/api/apps")
def apps_catalog(req: Req):  # noqa: ANN201
    """The app switcher: every app, whether this organization may open it, and where the local shell lives."""
    app, principal = req.app, req.principal
    entitlements = app.entitlements.resolve(principal.org_id)
    enabled = {
        a["id"]: a for a in app.billing.apps_for_member(principal.org_id, entitlements)
    }
    apps = []
    for entry in app_catalog.catalog():
        member = enabled.get(entry["id"], {})
        apps.append(
            {
                **entry,
                "enabled": bool(member.get("enabled")),
                "reason": member.get("reason", ""),
            }
        )
    return 200, {
        "apps": apps,
        "account_url": app.config.base_url,
        "local_url": app.config.local_app_url,
        "plan_id": entitlements.plan_id,
        "installation": "hosted",
    }


@cloud_route("GET", r"/api/org/apps")
def org_apps(req: Req):  # noqa: ANN201
    """The Apps page: each app with its price on this plan, its status and what it costs this organization."""
    app, principal = req.app, req.principal
    if not app.authz.can(principal, "billing.manage"):
        app.authz.require(principal, "usage.read")
    summary = app.billing.summary(principal.org_id)
    return 200, {
        "apps": summary["apps"],
        "plan_id": summary["plan_id"],
        "plan_name": summary["plan_name"],
        "status": summary["status"],
        "seats": summary["candidate_seats"],
        "period_end": summary["period_end"],
        "monthly_cents": summary["monthly_cents"],
        "addons_monthly_cents": summary["addons_monthly_cents"],
        "currency": "usd",
        "can_manage": app.authz.can(principal, "billing.manage"),
    }


def _app_id(req: Req) -> str:
    app_id = req.group()
    if app_id not in app_catalog.BY_ID:
        raise NotFound("App not found.")
    return app_id


@cloud_route("POST", r"/api/org/apps/([a-z0-9_-]+)/quote")
def org_app_quote(req: Req):  # noqa: ANN201
    req.app.authz.require(req.principal, "billing.manage")
    return 200, req.app.billing.app_quote(req.principal.org_id, _app_id(req))


@cloud_route("POST", r"/api/org/apps/([a-z0-9_-]+)/subscribe", writes=True)
def org_app_subscribe(req: Req):  # noqa: ANN201
    return 200, req.app.billing.subscribe_app(req.principal, _app_id(req))


@cloud_route("POST", r"/api/org/apps/([a-z0-9_-]+)/unsubscribe", writes=True)
def org_app_unsubscribe(req: Req):  # noqa: ANN201
    return 200, req.app.billing.unsubscribe_app(req.principal, _app_id(req))


@cloud_route("POST", r"/api/org/apps/([a-z0-9_-]+)/keep", writes=True)
def org_app_keep(req: Req):  # noqa: ANN201
    return 200, req.app.billing.keep_app(req.principal, _app_id(req))


@cloud_route("GET", r"/api/platform/tenants/([0-9a-f]{32})/apps", auth="operator")
def tenant_apps(req: Req):  # noqa: ANN201
    return 200, req.app.billing.tenant_apps(req.principal, req.group())


@cloud_route(
    "PUT", r"/api/platform/tenants/([0-9a-f]{32})/apps/([a-z0-9_-]+)", auth="operator"
)
def tenant_app_grant(req: Req):  # noqa: ANN201
    """Grant an app to a tenant without charge (a contract), or take the grant back."""
    body = req.body()
    status = str(body.get("status", "")).strip()
    if status not in {"granted", "revoked"}:
        raise ValidationFailed("status must be granted or revoked.")
    return 200, req.app.billing.set_app_grant(
        req.principal,
        req.group(1),
        req.group(2),
        status,
        str(body.get("note", "")).strip(),
    )
