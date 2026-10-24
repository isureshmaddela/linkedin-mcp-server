"""Routes the billing and platform console screens need beyond the core API."""

from __future__ import annotations

from aira_cloud.db import many
from aira_cloud.webio import Req, cloud_route


@cloud_route("GET", r"/api/billing/trial-plan", auth="public")
def trial_plan(req: Req):  # noqa: ANN201
    """The trial's limits in the same shape as a public plan, so the comparison table can show it."""
    plan = req.app.catalog.get("trial")
    entitlements, pricing = plan["entitlements"], plan["pricing"]
    return 200, {
        "plan": {
            "plan_id": "trial",
            "name": plan["name"],
            "seat_cents_month": pricing.get("seat_cents_month", 0),
            "min_seats": pricing.get("min_seats", 0),
            "contact_sales": False,
            "currency": pricing.get("currency", "usd"),
            "overage": pricing.get("overage", {}),
            "limits": {
                key: value for key, value in entitlements.items() if key != "features"
            },
            "features": entitlements.get("features", []),
            "trial_days": req.app.config.trial_days,
        }
    }


@cloud_route("GET", r"/api/platform/plans/versions", auth="operator")
def plan_versions(req: Req):  # noqa: ANN201
    """How many subscriptions sit on each version of each plan (for scheduling a migration)."""
    req.app.authz.require(req.principal, "platform.tenants.read")
    with req.app.db.read() as connection:
        rows = many(
            connection,
            "SELECT plan_id, plan_version, COUNT(*) AS n FROM subscriptions WHERE status != 'deleted' GROUP BY plan_id, plan_version",
        )
    counts: dict[str, dict[str, int]] = {}
    for row in rows:
        counts.setdefault(row["plan_id"], {})[str(row["plan_version"])] = int(row["n"])
    return 200, {"versions": counts}


@cloud_route("GET", r"/api/platform/tenants/([0-9a-f]{32})/invoices", auth="operator")
def tenant_invoices(req: Req):  # noqa: ANN201
    """A tenant's invoices, so an operator can choose the one to mark paid."""
    from aira_cloud.db import loads

    req.app.authz.require(req.principal, "platform.tenants.read")
    req.app.tenancy.org(req.group())
    with req.app.db.read() as connection:
        rows = many(
            connection,
            "SELECT invoice_id, kind, amount_cents, currency, status, period_start, period_end, created_at, paid_at, lines FROM invoices WHERE org_id = ? ORDER BY created_at DESC LIMIT 50",
            (req.group(),),
        )
    for row in rows:
        row["lines"] = loads(row["lines"], [])
    return 200, {"invoices": rows}
