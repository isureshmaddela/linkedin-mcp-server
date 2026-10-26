"""The plan catalog and the ``Entitlements`` object every check goes through.

The numbers are the scope document's proposal, kept in data so an operator can
change them (see ``platform_admin``). ``None`` means "no limit". Enterprise
defaults are generous placeholders; a real contract sets overrides on the
organization's subscription.
"""

from __future__ import annotations

import copy
from dataclasses import dataclass, field
from typing import Any

from aira import apps as app_catalog
from aira_cloud.rbac import SCOPE_LEVELS

MB = 1024 * 1024
GB = 1024 * MB
PLAN_ORDER = ("trial", "starter", "team", "enterprise")

SUBSCRIPTION_STATES = (
    "trialing",
    "active",
    "past_due",
    "read_only",
    "cancelled",
    "deleted",
)

# Sources a plan may enable, in the order a plan adds them.
ALL_SOURCES = ("aira_jobs", "company_boards", "google_jobs", "linkedin")

_TRIAL: dict[str, Any] = {
    "candidate_seats": 3,
    "staff_seats": 2,
    "apps_per_candidate_day": 3,
    "apps_per_org_month": 50,
    "search_plans_per_candidate": 2,
    "cover_letters_per_month": 20,
    "api_searches_per_month": 0,
    "pooled_searches_per_month": 0,
    "leads_stored": 0,
    "lead_enrichments_per_month": 0,
    "lead_emails_per_month": 0,
    "lead_email_daily_cap": 0,
    "api_v1_keys": 0,
    "api_v1_per_minute": 0,
    "monitors_per_candidate": 2,
    "storage_bytes": 100 * MB,
    "retention_days": 30,
    "audit_days": 30,
    "coach_max_scope": "view",
    "support": "community",
    "features": [
        "sources.aira_jobs",
        "sources.company_boards",
        "ai.cover_letters",
        "api.console_read",
        "auth.mfa",
    ],
}
_STARTER: dict[str, Any] = {
    "candidate_seats": 25,
    "staff_seats": 3,
    "apps_per_candidate_day": 10,
    "apps_per_org_month": 1500,
    "search_plans_per_candidate": 5,
    "cover_letters_per_month": 500,
    "api_searches_per_month": 1000,
    "pooled_searches_per_month": 0,
    "leads_stored": 2000,
    "lead_enrichments_per_month": 200,
    "lead_emails_per_month": 0,
    "lead_email_daily_cap": 0,
    "api_v1_keys": 0,
    "api_v1_per_minute": 0,
    "monitors_per_candidate": 10,
    "storage_bytes": 2 * GB,
    "retention_days": 365,
    "audit_days": 365,
    "coach_max_scope": "act",
    "support": "email",
    "features": [
        "sources.aira_jobs",
        "sources.company_boards",
        "sources.google_jobs",
        "automation.submit",
        "ai.cover_letters",
        "api.console_read",
        "api.searches",
        "auth.mfa",
        "coach.act",
    ],
}
_TEAM: dict[str, Any] = {
    "candidate_seats": 250,
    "staff_seats": 25,
    "apps_per_candidate_day": 25,
    "apps_per_org_month": 15000,
    "search_plans_per_candidate": 10,
    "cover_letters_per_month": 5000,
    "api_searches_per_month": 10000,
    "pooled_searches_per_month": 2000,
    "leads_stored": 20000,
    "lead_enrichments_per_month": 2500,
    "lead_emails_per_month": 1500,
    "lead_email_daily_cap": 50,
    "api_v1_keys": 5,
    "api_v1_per_minute": 120,
    "monitors_per_candidate": 25,
    "storage_bytes": 25 * GB,
    "retention_days": 1095,
    "audit_days": 1095,
    "coach_max_scope": "approve",
    "support": "email_1bd",
    "features": [
        "sources.aira_jobs",
        "sources.company_boards",
        "sources.google_jobs",
        "sources.linkedin",
        "automation.submit",
        "ai.cover_letters",
        "api.console_read",
        "api.searches",
        "api.v1",
        "auth.mfa",
        "auth.sso",
        "coach.act",
        "coach.approve",
        "branding",
        "overage",
        "pooled_serpapi",
        "leads_outreach",
    ],
}
_ENTERPRISE: dict[str, Any] = {
    **_TEAM,
    "candidate_seats": 5000,
    "staff_seats": 500,
    "apps_per_candidate_day": 50,
    "apps_per_org_month": 250000,
    "search_plans_per_candidate": 25,
    "cover_letters_per_month": 100000,
    "api_searches_per_month": 250000,
    "pooled_searches_per_month": 50000,
    "leads_stored": 200000,
    "lead_enrichments_per_month": 50000,
    "lead_emails_per_month": 25000,
    "lead_email_daily_cap": 100,
    "api_v1_keys": 50,
    "api_v1_per_minute": 600,
    "monitors_per_candidate": 100,
    "storage_bytes": 500 * GB,
    "retention_days": 2555,
    "audit_days": 2555,
    "support": "named_contact_sla",
    "features": _TEAM["features"]
    + [
        "leads",
        "api.console",
        "auth.scim",
        "byo_model",
        "siem_export",
        "legal_hold",
        "priority_sources",
        "custom_limits",
    ],
}

_PRICING = {
    "trial": {"currency": "usd", "seat_cents_month": 0, "min_seats": 0, "overage": {}},
    "starter": {
        "currency": "usd",
        "seat_cents_month": 1400,
        "min_seats": 5,
        "overage": {},
    },
    "team": {
        "currency": "usd",
        "seat_cents_month": 1100,
        "min_seats": 25,
        "overage": {
            "application_cents": 10,
            "search_cents": 2,
            "cover_letter_cents": 3,
            "pooled_search_cents": 4,
            "api_search_cents": 2,
        },
    },
    "enterprise": {
        "currency": "usd",
        "seat_cents_month": 0,
        "min_seats": 0,
        "contact_sales": True,
        "overage": {
            "application_cents": 8,
            "search_cents": 2,
            "cover_letter_cents": 2,
            "pooled_search_cents": 3,
            "api_search_cents": 2,
        },
    },
}

DEFAULT_PLANS: dict[str, dict[str, Any]] = {
    "trial": {
        "name": "Trial",
        "entitlements": _TRIAL,
        "pricing": _PRICING["trial"],
        "sort_order": 0,
        "public": False,
    },
    "starter": {
        "name": "Starter",
        "entitlements": _STARTER,
        "pricing": _PRICING["starter"],
        "sort_order": 1,
        "public": True,
    },
    "team": {
        "name": "Team",
        "entitlements": _TEAM,
        "pricing": _PRICING["team"],
        "sort_order": 2,
        "public": True,
    },
    "enterprise": {
        "name": "Enterprise",
        "entitlements": _ENTERPRISE,
        "pricing": _PRICING["enterprise"],
        "sort_order": 3,
        "public": True,
    },
}

# Apps sold per candidate seat per month, in cents: 0 = included in the plan, None = not available on it.
# An app that is not listed (Careers) is part of every plan; the trial has every app.
APP_PRICING: dict[str, dict[str, int | None]] = {
    "api": {"starter": None, "team": 300, "enterprise": 0},
    # Business apps (aira/apps.py INDUSTRY_APPS: Sales, Marketing with Leads, Service, Commerce, Courses & Events),
    # bought per seat, all in Enterprise.
    **{
        app.id: {"starter": 500, "team": 400, "enterprise": 0}
        for app in app_catalog.INDUSTRY_APPS
    },
    # Life apps (everyday use by every member): cheaper per seat, all in Enterprise and the trial.
    **{
        app.id: {"starter": 200, "team": 150, "enterprise": 0}
        for app in app_catalog.LIFE_APPS
    },
}
APP_STATES = ("included", "addon", "unavailable")
APP_PLANS = ("starter", "team", "enterprise")
# An operator's price changes over APP_PRICING (aira_cloud.entitlements.AppSettings): app -> plan -> cents or None.
PriceOverrides = dict[str, dict[str, int | None]]


def app_prices(
    app_id: str, overrides: PriceOverrides | None = None
) -> dict[str, int | None]:
    """Per-plan seat prices for a sold app: the defaults above with an operator's changes on top."""
    prices = dict(APP_PRICING.get(app_id, {}))
    prices.update((overrides or {}).get(app_id, {}))
    return prices


def app_state(
    plan_id: str, app_id: str, overrides: PriceOverrides | None = None
) -> str:
    """How ``plan_id`` relates to an app: ``included``, ``addon`` (bought per seat) or ``unavailable``.
    An old app id (``"shop"``) answers for the app it became (Commerce)."""
    app_id = app_catalog.current_id(app_id)
    if app_id not in app_catalog.BY_ID:
        return "unavailable"
    if plan_id == "trial" or app_id not in APP_PRICING:
        return "included"
    price = app_prices(app_id, overrides).get(plan_id)
    if price is None:
        return "unavailable"
    return "included" if price == 0 else "addon"


def app_price(
    plan_id: str, app_id: str, overrides: PriceOverrides | None = None
) -> int | None:
    """Cents per candidate seat per month for an add-on (0 when included, None when not available)."""
    app_id = app_catalog.current_id(app_id)
    state = app_state(plan_id, app_id, overrides)
    if state == "unavailable":
        return None
    if state == "included":
        return 0
    return int(app_prices(app_id, overrides)[plan_id] or 0)


def plan_for_app(app_id: str, overrides: PriceOverrides | None = None) -> str | None:
    """The cheapest paid plan on which the app can be had (bought or included)."""
    for plan_id in PLAN_ORDER[1:]:
        if app_state(plan_id, app_id, overrides) != "unavailable":
            return plan_id
    return None


# Sources → the feature that unlocks them.
SOURCE_FEATURES = {
    "aira_jobs": "sources.aira_jobs",
    "company_boards": "sources.company_boards",
    "google_jobs": "sources.google_jobs",
    "linkedin": "sources.linkedin",
}

# Status → what is stopped. Reads stay open except where stated.
STATUS_RESTRICTIONS: dict[str, frozenset[str]] = {
    "trialing": frozenset(),
    "active": frozenset(),
    "past_due": frozenset({"automation"}),
    "read_only": frozenset({"automation", "writes"}),
    "cancelled": frozenset({"automation", "writes"}),
    "deleted": frozenset({"automation", "writes", "reads"}),
}


def month_key(timestamp: int) -> str:
    import time

    return time.strftime("%Y-%m", time.gmtime(timestamp))


def day_key(timestamp: int) -> str:
    import time

    return time.strftime("%Y-%m-%d", time.gmtime(timestamp))


# Limits and rates added after the first plan versions were published. A stored plan version never
# changes, so a version without them gets the shipped default for its plan instead of "unlimited" or "free".
LATER_LIMITS = (
    "pooled_searches_per_month",
    "leads_stored",
    "lead_enrichments_per_month",
    "lead_emails_per_month",
    "lead_email_daily_cap",
)
# Features added later: a plan version published before one existed gets it when the shipped plan has it.
LATER_FEATURES = ("leads", "leads_outreach")
LATER_OVERAGE_RATES = ("api_search_cents", "pooled_search_cents")


def with_later_limits(plan_id: str, entitlements: dict[str, Any]) -> dict[str, Any]:
    shipped = DEFAULT_PLANS.get(plan_id, {}).get("entitlements", {})
    out = dict(entitlements)
    for name in LATER_LIMITS:
        if name not in out and name in shipped:
            out[name] = shipped[name]
    features = list(out.get("features") or [])
    for name in LATER_FEATURES:
        if name not in features and name in (shipped.get("features") or []):
            features.append(name)
    if "features" in out or features:
        out["features"] = features
    return out


def with_later_rates(plan_id: str, overage: dict[str, Any]) -> dict[str, Any]:
    shipped = DEFAULT_PLANS.get(plan_id, {}).get("pricing", {}).get("overage", {})
    out = dict(overage)
    for name in LATER_OVERAGE_RATES:
        if name not in out and name in shipped and out:
            out[name] = shipped[name]  # only for plans that charge overage at all
    return out


def merge_limits(base: dict[str, Any], overrides: dict[str, Any]) -> dict[str, Any]:
    merged = copy.deepcopy(base)
    for key, value in (overrides or {}).items():
        if key == "features" and isinstance(value, dict):
            features = set(merged.get("features", []))
            features |= set(value.get("add", []))
            features -= set(value.get("remove", []))
            merged["features"] = sorted(features)
        else:
            merged[key] = value
    return merged


@dataclass(frozen=True)
class Entitlements:
    org_id: str
    plan_id: str
    plan_version: int
    status: str
    limits: dict[str, Any]
    features: frozenset[str]
    restrictions: frozenset[str]
    period_start: int | None = None
    period_end: int | None = None
    trial_end: int | None = None
    suspended: bool = False
    over_limits: tuple[str, ...] = ()
    overage_enabled: bool = False
    pricing: dict[str, Any] = field(default_factory=dict)
    apps: frozenset[str] = (
        frozenset()
    )  # the apps the organization may open (plan, add-ons, grants)

    def limit(self, name: str) -> Any:
        return self.limits.get(name)

    def has(self, feature: str) -> bool:
        return feature in self.features

    def has_app(self, app_id: str) -> bool:
        """``app_id`` may be an app, an old app id or a section/package (``"bookings"`` -> Commerce)."""
        return app_catalog.current_id(app_id) in self.apps

    @property
    def automation_allowed(self) -> bool:
        return (
            "automation" not in self.restrictions
            and not self.suspended
            and not self.over_limits
        )

    @property
    def read_only(self) -> bool:
        return "writes" in self.restrictions or self.suspended

    def coach_scopes(self) -> list[str]:
        cap = SCOPE_LEVELS.get(str(self.limits.get("coach_max_scope", "view")), 1)
        return [name for name, level in SCOPE_LEVELS.items() if level <= cap]

    def sources(self) -> list[str]:
        return [
            source for source in ALL_SOURCES if SOURCE_FEATURES[source] in self.features
        ]

    def as_dict(self) -> dict[str, Any]:
        return {
            "plan_id": self.plan_id,
            "plan_version": self.plan_version,
            "status": self.status,
            "limits": self.limits,
            "features": sorted(self.features),
            "restrictions": sorted(self.restrictions),
            "period_start": self.period_start,
            "period_end": self.period_end,
            "trial_end": self.trial_end,
            "suspended": self.suspended,
            "over_limits": list(self.over_limits),
            "overage_enabled": self.overage_enabled,
            "automation_allowed": self.automation_allowed,
            "sources": self.sources(),
            "coach_scopes": self.coach_scopes(),
            "apps": sorted(self.apps),
        }


def plan_for_feature(plans: dict[str, dict[str, Any]], feature: str) -> str | None:
    """The cheapest paid plan whose entitlements include the feature."""
    for plan_id in PLAN_ORDER[1:]:
        entry = plans.get(plan_id)
        if entry and feature in entry["entitlements"].get("features", []):
            return plan_id
    return None


def plan_for_limit(
    plans: dict[str, dict[str, Any]], limit: str, needed: int
) -> str | None:
    """The cheapest paid plan whose limit covers ``needed``."""
    for plan_id in PLAN_ORDER[1:]:
        entry = plans.get(plan_id)
        if not entry:
            continue
        value = entry["entitlements"].get(limit)
        if value is None or (isinstance(value, int) and value >= needed):
            return plan_id
    return None
