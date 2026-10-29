"""Spaces: whose business apps a request runs in.

With the organization's policy ``apps_space = "personal"`` every member has a space of their own for the business
apps (Studio, Marketing, Service, Commerce, Courses & Events, Sites) and the Business hub: their business profile,
customers, orders, pages and what they earn, in ``tenants/<org>/<member>/apps.sqlite3``. Only they open it; no role
in the organization reads it (an export of their account carries it, an organization export does not open it).

The organization's own apps (the *company* space, ``_org/api.sqlite3``: the organization's business, its customers
and revenue) are for owners, admins and coaches, and for the members the organization names in
``company_members``. The browser asks for them with ``X-Aira-Space: company``; without it a request runs in the
member's own space.

With ``"shared"`` (organizations made before spaces) everyone works in the organization's apps, as before. Ads stays
the organization's in both: its balance is paid through the organization's billing. The life apps (Health, Learn,
Planner, Life, AI...) were always per person and do not change.
"""

from __future__ import annotations

from typing import Any

from aira.apps import app_of_path
from monosage_cloud.errors import Forbidden
from monosage_cloud.rbac import ADMIN, COACH, OWNER, Principal

PERSONAL_APPS = ("studio", "marketing", "service", "commerce", "experiences", "sites")
HUB_PREFIX = (
    "/api/suite"  # the Business hub: business profile, email, payments, revenue
)
COMPANY_ROLES = (OWNER, ADMIN, COACH)
HEADER = "x-aira-space"
MEMBER, COMPANY = "member", "company"


def personal_route(path: str) -> str:
    """The app a route belongs to when it can run in a member's own space (``hub`` for the Business hub), else ''."""
    if path == HUB_PREFIX or path.startswith(f"{HUB_PREFIX}/"):
        return "hub"
    owner = app_of_path(path)
    return owner.id if owner is not None and owner.id in PERSONAL_APPS else ""


def mode(policy: dict[str, Any]) -> str:
    return "personal" if policy.get("apps_space") == "personal" else "shared"


def company_access(principal: Principal, policy: dict[str, Any]) -> bool:
    """May this member open the organization's own business apps?"""
    if mode(policy) == "shared":
        return True
    return principal.role in COMPANY_ROLES or principal.member_id in set(
        policy.get("company_members") or []
    )


def named_member(principal: Principal, policy: dict[str, Any]) -> bool:
    """A member the organization named to work in its apps (they get what a coach gets there)."""
    return mode(policy) == "personal" and principal.member_id in set(
        policy.get("company_members") or []
    )


def choose(
    headers: dict[str, str], principal: Principal, policy: dict[str, Any], path: str
) -> str:
    """``member`` (their own space), ``company`` (the organization's apps), or '' (not a business-app route)."""
    if not personal_route(path):
        return ""
    if mode(policy) == "shared":
        return COMPANY
    if str(headers.get(HEADER, "")).strip().lower() == COMPANY:
        if not company_access(principal, policy):
            raise Forbidden(
                "Your organization's own apps are for its owners, admins and coaches, and the members it names. "
                "Your own apps are under your name.",
                code="company_space_forbidden",
            )
        return COMPANY
    return MEMBER


def describe(principal: Principal, policy: dict[str, Any]) -> dict[str, Any]:
    """What the browser needs to show the space switcher (``/api/auth/me``)."""
    personal = mode(policy) == "personal"
    return {
        "mode": mode(policy),
        "apps": list(PERSONAL_APPS) if personal else [],
        "own": personal,
        "company": company_access(principal, policy),
    }
