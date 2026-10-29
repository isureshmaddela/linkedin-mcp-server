"""Apps: the catalog every member sees, and the per-app subscriptions an organization turns on and off."""

from __future__ import annotations

from aira import apps as app_catalog
from monosage_cloud import app_access
from monosage_cloud.errors import NotFound, ValidationFailed
from monosage_cloud.rbac import ORG_ROLES
from monosage_cloud.webio import Req, cloud_route


@cloud_route("GET", r"/api/apps")
def apps_catalog(req: Req):  # noqa: ANN201
    """The app switcher: every app, whether this organization may open it, and where the local shell lives."""
    app, principal = req.app, req.principal
    entitlements = app.entitlements.resolve(principal.org_id)
    enabled = {
        a["id"]: a for a in app.billing.apps_for_member(principal.org_id, entitlements)
    }
    policy = app.tenancy.policy(principal.org_id)
    apps = []
    for entry in app_catalog.catalog():
        member = enabled.get(entry["id"], {})
        # ``enabled``: the organization has the app; ``allowed``: it has given it to this member (app_access.py)
        allowed = app_access.may_open(policy, principal, str(entry["id"]))
        apps.append(
            {
                **entry,
                "enabled": bool(member.get("enabled")),
                "reason": member.get("reason", ""),
                "switched_off": bool(member.get("switched_off")),
                "allowed": allowed,
                "allowed_reason": ""
                if allowed
                else app_access.denied_message(str(entry["id"])),
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
        # who may use each app (roles; None is everyone), and whether this person may change it
        "access": app_access.app_rows(
            app.tenancy.policy(principal.org_id),
            app.entitlements.resolve(principal.org_id),
        ),
        "roles": list(ORG_ROLES),
        "can_manage_access": app.authz.can(principal, "org.settings"),
        "can_manage_owners": app.authz.can(principal, "members.manage_owners"),
    }


@cloud_route("PUT", r"/api/org/apps/([a-z0-9_-]+)/access", writes=True)
def org_app_access(req: Req):  # noqa: ANN201
    """Who may use one app: ``{"roles": [...]}``, or ``{"roles": null}`` for everyone (``org.settings``)."""
    body = req.body()
    if "roles" not in body:
        raise ValidationFailed(
            "Send the roles that may use the app, or null for everyone."
        )
    return 200, app_access.set_app_roles(
        req.app, req.principal, _app_id(req), body["roles"]
    )


@cloud_route("GET", r"/api/org/members/([0-9a-f]{32})/apps")
def member_apps_get(req: Req):  # noqa: ANN201
    """Every app with whether this member may open it, and why (``members.manage``)."""
    return 200, app_access.member_view(req.app, req.principal, req.group())


@cloud_route("PUT", r"/api/org/members/([0-9a-f]{32})/apps", writes=True)
def member_apps_put(req: Req):  # noqa: ANN201
    """One member's exceptions: ``{"allow": [app ids], "deny": [app ids]}`` (``members.manage``)."""
    return 200, app_access.set_member_apps(
        req.app, req.principal, req.group(), req.body()
    )


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
    """Grant an app to a tenant without charge (a contract), or take the grant back; or switch an app off for this
    one organization (``disabled``) and back on (``enabled``)."""
    body = req.body()
    status = str(body.get("status", "")).strip()
    if status not in {"granted", "revoked", "disabled", "enabled"}:
        raise ValidationFailed("status must be granted, revoked, disabled or enabled.")
    return 200, req.app.billing.set_app_grant(
        req.principal,
        req.group(1),
        req.group(2),
        status,
        str(body.get("note", "")).strip(),
    )


@cloud_route(
    "POST", r"/api/platform/tenants/([0-9a-f]{32})/subscription", auth="operator"
)
def platform_subscription_set(req: Req):  # noqa: ANN201
    """The superadmin sets a tenant's plan, seats, paid months and apps (bought outside the product)."""
    body = req.body()
    apps = body.get("apps") or {}
    if not isinstance(apps, dict):
        apps = {}
    return 200, req.app.billing.operator_set_subscription(
        req.principal,
        req.group(),
        plan_id=str(body.get("plan_id") or ""),
        seats=body.get("seats") or 0,
        months=body.get("months") or 1,
        apps={str(k): bool(v) for k, v in apps.items()},
        note=str(body.get("note") or ""),
    )
