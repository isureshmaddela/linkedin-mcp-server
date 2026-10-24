"""Metering for searches made with the platform's pooled SerpApi key.

An organization that turns on ``use_pooled_serpapi`` (Team and Enterprise) searches Google Jobs with
a key the platform pays for. Those searches are counted separately (``pooled_searches``), limited by
the plan (``pooled_searches_per_month``) and, when the organization has switched overage on, billed
per search past the limit (``pooled_search_cents`` in the plan's pricing). An organization that uses
its own key is never touched by any of this.
"""

from __future__ import annotations

import threading
from typing import TYPE_CHECKING, Any

from aira_cloud.entitlements import Reservation

if TYPE_CHECKING:
    from aira_cloud.app import CloudApp

METRIC = "pooled_searches"
LIMIT = "pooled_searches_per_month"

# SerpApi credits the Jobs tab spent on this thread since ``begin`` (the local code reports them through
# ``HOOKS.search_credits``; a cache hit reports 0). A request is one thread, so this is exact per request.
_spent = threading.local()


def begin() -> None:
    _spent.credits = 0


def note(credits: int) -> None:
    _spent.credits = getattr(_spent, "credits", 0) + max(0, int(credits))


def collect() -> int:
    credits = getattr(_spent, "credits", 0)
    _spent.credits = 0
    return int(credits)


def uses_pooled_key(app: CloudApp, org_id: str) -> bool:
    """True when a SerpApi search for this organization would be made with the pooled key."""
    if not org_id or not app.config.pooled_serpapi_key:
        return False
    if not app.tenancy.policy(org_id).get("use_pooled_serpapi"):
        return False
    if not app.entitlements.resolve(org_id).has("pooled_serpapi"):
        return False
    from aira_cloud.hooks import _org_serpapi_key

    return not _org_serpapi_key(app, org_id)  # the organization's own key always wins


def reserve(
    app: CloudApp, org_id: str, *, candidate_id: str = "", audience: str = "staff"
) -> Reservation | None:
    """Take one pooled search from the allowance (``402 plan_required`` when it is used up)."""
    if not uses_pooled_key(app, org_id):
        return None
    return app.enforcer.reserve(
        org_id,
        METRIC,
        monthly_limit_name=LIMIT,
        candidate_id=candidate_id,
        audience=audience,
    )


def settle(
    app: CloudApp,
    org_id: str,
    reservation: Reservation | None,
    *,
    credits: int | None,
    ok: bool = True,
    candidate_id: str = "",
) -> None:
    """Make the count match what the provider charged: nothing for a failure or a cache hit, ``credits`` otherwise."""
    if reservation is None:
        return
    if not ok or credits == 0:
        app.usage.release(reservation)
        return
    if credits and credits > 1:
        add(app, org_id, credits - 1, candidate_id=candidate_id)


def add(app: CloudApp, org_id: str, amount: int, *, candidate_id: str = "") -> None:
    """Count pooled searches that already happened (a worker run), pricing the part past the limit as overage."""
    if amount <= 0:
        return
    limit = app.entitlements.resolve(org_id).limit(LIMIT)
    app.usage.record_metered(
        org_id, METRIC, amount, monthly_limit=limit, candidate_id=candidate_id
    )


def exhausted(app: CloudApp, org_id: str) -> str:
    """Why pooled searches cannot be made right now ('' when they can), for work that does not reserve first."""
    if not uses_pooled_key(app, org_id):
        return ""
    entitlements = app.entitlements.resolve(org_id)
    remaining = app.usage.remaining_month(
        org_id,
        METRIC,
        entitlements.limit(LIMIT),
        overage=entitlements.overage_enabled,
    )
    if remaining is not None and remaining <= 0:
        return "your organization's pooled search allowance for this month is used up"
    return ""


def snapshot(app: CloudApp, org_id: str) -> dict[str, Any]:
    entitlements = app.entitlements.resolve(org_id)
    return {
        "used": app.usage.used(org_id, METRIC),
        "limit": entitlements.limit(LIMIT),
        "overage": app.usage.used(org_id, f"{METRIC}:overage"),
    }
