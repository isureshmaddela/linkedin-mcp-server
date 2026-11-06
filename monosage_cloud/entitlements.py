"""Plans in the database, feature flags, the entitlement resolver and usage metering.

Entitlements are resolved from the subscription (plan version, seats, overrides,
state) plus operator feature flags, cached per organization for 60 seconds.
Counted limits live in ``usage_counters`` (per organization and month) and
``usage_daily`` (per candidate and day) and are incremented with one conditional
UPDATE, so two concurrent actions cannot both take the last unit.
"""

from __future__ import annotations

import builtins
import hashlib
import re
import threading
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any

from monosage_cloud.config import CloudConfig, Clock
from monosage_cloud.db import ControlDB, dumps, loads, many, one
from aira import apps as app_catalog
from monosage_cloud.errors import (
    AppDisabled,
    AppRequired,
    Conflict,
    NotFound,
    PlanRequired,
    SubscriptionInactive,
    ValidationFailed,
)
from monosage_cloud.plans import (
    APP_PLANS,
    APP_PRICING,
    BUILTIN_PLANS,
    COACH_SCOPES,
    DEFAULT_PLANS,
    KNOWN_FEATURES,
    LIMIT_NAMES,
    OVERAGE_RATES,
    PLAN_DESCRIPTIONS,
    STATUS_RESTRICTIONS,
    SUPPORT_LEVELS,
    TRIAL,
    Entitlements,
    PriceOverrides,
    app_price,
    app_prices,
    app_state,
    day_key,
    merge_limits,
    month_key,
    plan_for_app,
    plan_for_feature,
    plan_for_limit,
    upgrade_order,
    with_later_limits,
)

STAFF_LIMIT_ROLES = ("owner", "admin", "coach", "billing", "auditor")


# --------------------------------------------------------------------------- #
# Plan catalog
# --------------------------------------------------------------------------- #


PLAN_ID = re.compile(r"[a-z][a-z0-9_]{2,29}")
# Words the console's routes use after /api/platform/plans/, so no plan may be called that.
RESERVED_PLAN_IDS = frozenset({"versions", "order", "new", "default", "signup"})
MAX_SEAT_CENTS = 10_000_000  # $100,000 per seat per month
MAX_LIMIT = 10**13


def validate_entitlements(
    values: Any, *, allowed_features: Iterable[str] = ()
) -> dict[str, Any]:
    """A plan's entitlements as the superadmin gives them: known limits as whole numbers or ``None`` (no limit), the
    coach scope ceiling, the support level, and features from the known list (or ones the plan already has)."""
    if not isinstance(values, dict):
        raise ValidationFailed("Entitlements must be an object of limit name to value.")
    allowed = set(KNOWN_FEATURES) | set(allowed_features)
    clean: dict[str, Any] = {}
    for name, value in values.items():
        if name == "features":
            if not isinstance(value, list) or not all(
                isinstance(item, str) for item in value
            ):
                raise ValidationFailed("features is a list of feature names.")
            unknown = sorted(set(value) - allowed)
            if unknown:
                raise ValidationFailed(f"Unknown feature: {', '.join(unknown)}.")
            clean[name] = list(dict.fromkeys(value))
        elif name == "coach_max_scope":
            if value not in COACH_SCOPES:
                raise ValidationFailed(
                    f"coach_max_scope must be one of {', '.join(COACH_SCOPES)}."
                )
            clean[name] = value
        elif name == "support":
            if value not in SUPPORT_LEVELS:
                raise ValidationFailed(
                    f"support must be one of {', '.join(SUPPORT_LEVELS)}."
                )
            clean[name] = value
        elif name in LIMIT_NAMES:
            if value is not None and (
                isinstance(value, bool)
                or not isinstance(value, int)
                or not 0 <= value <= MAX_LIMIT
            ):
                raise ValidationFailed(
                    f"{name} must be a non-negative whole number or empty."
                )
            clean[name] = value
        else:
            raise ValidationFailed(f"{name} is not a plan limit.")
    return clean


def validate_pricing(values: Any) -> dict[str, Any]:
    """A plan's price: cents per candidate seat per month, minimum seats, currency, contract-only, trial days, and
    the overage rates (cents per unit)."""
    if not isinstance(values, dict):
        raise ValidationFailed("Pricing must be an object.")
    clean: dict[str, Any] = {}

    def whole(name: str, value: Any, high: int) -> int:
        if (
            isinstance(value, bool)
            or not isinstance(value, int)
            or not 0 <= value <= high
        ):
            raise ValidationFailed(f"{name} must be a whole number from 0 to {high}.")
        return value

    for name, value in values.items():
        if name == "seat_cents_month":
            clean[name] = whole(name, value, MAX_SEAT_CENTS)
        elif name == "inr_seat_paise_month":  # the Razorpay price (plans.plan_inr_paise)
            clean[name] = whole(name, value, MAX_SEAT_CENTS * 100)
        elif name == "min_seats":
            clean[name] = whole(name, value, 100_000)
        elif name == "trial_days":
            clean[name] = whole(name, value, 365)
        elif name == "currency":
            if not isinstance(value, str) or not re.fullmatch(r"[a-z]{3}", value):
                raise ValidationFailed("currency is a three-letter code such as usd.")
            clean[name] = value
        elif name == "contact_sales":
            if not isinstance(value, bool):
                raise ValidationFailed("contact_sales is true or false.")
            clean[name] = value
        elif name == "overage":
            if not isinstance(value, dict) or set(value) - set(OVERAGE_RATES):
                raise ValidationFailed(
                    f"overage rates are {', '.join(OVERAGE_RATES)} in cents."
                )
            clean[name] = {
                key: whole(f"overage {key}", rate, 100_000)
                for key, rate in value.items()
            }
        else:
            raise ValidationFailed(f"{name} is not part of a plan's price.")
    return clean


class PlanCatalog:
    """The plans: the built-in ones (``plans.DEFAULT_PLANS``) and the ones the superadmin creates, each versioned.

    The superadmin's order is ``sort_order``; an archived plan (``active = 0``) takes no new subscriptions and is
    left out of :meth:`current`, while organizations already on it keep it (:meth:`get` still answers for it).
    """

    def __init__(self, db: ControlDB, clock: Clock) -> None:
        self.db = db
        self.clock = clock

    def seed(self) -> None:
        with self.db.write() as connection:
            for plan_id, plan in DEFAULT_PLANS.items():
                if one(
                    connection, "SELECT 1 AS x FROM plans WHERE plan_id = ?", (plan_id,)
                ):
                    continue
                last = one(connection, "SELECT MAX(sort_order) AS n FROM plans", ())
                order = plan["sort_order"]
                if last and last["n"] is not None:
                    order = max(order, int(last["n"]) + 1)
                connection.execute(
                    "INSERT INTO plans (plan_id, name, version, entitlements, pricing, sort_order, public, active, updated_at, description) VALUES (?, ?, 1, ?, ?, ?, ?, 1, ?, ?)",
                    (
                        plan_id,
                        plan["name"],
                        dumps(plan["entitlements"]),
                        dumps(plan["pricing"]),
                        order,
                        int(plan["public"]),
                        self.clock.now(),
                        PLAN_DESCRIPTIONS.get(plan_id, ""),
                    ),
                )
                connection.execute(
                    "INSERT INTO plan_versions (plan_id, version, entitlements, pricing, created_at, created_by) VALUES (?, 1, ?, ?, ?, 'system')",
                    (
                        plan_id,
                        dumps(plan["entitlements"]),
                        dumps(plan["pricing"]),
                        self.clock.now(),
                    ),
                )

    @staticmethod
    def _entry(row: dict[str, Any]) -> dict[str, Any]:
        plan_id = row["plan_id"]
        return {
            "plan_id": plan_id,
            "name": row["name"],
            "description": row.get("description") or PLAN_DESCRIPTIONS.get(plan_id, ""),
            "version": row["version"],
            "entitlements": loads(row["entitlements"], {}),
            "pricing": loads(row["pricing"], {}),
            "sort_order": row["sort_order"],
            "public": bool(row["public"]),
            "archived": not row["active"],
            "archived_at": row.get("archived_at"),
            "builtin": plan_id in BUILTIN_PLANS,
            "created_by": row.get("created_by")
            or ("system" if plan_id in BUILTIN_PLANS else ""),
        }

    def current(self, *, include_archived: bool = False) -> dict[str, dict[str, Any]]:
        """Every plan in the superadmin's order (archived ones only when asked)."""
        with self.db.read() as connection:
            rows = many(
                connection,
                "SELECT * FROM plans"
                + ("" if include_archived else " WHERE active = 1")
                + " ORDER BY sort_order, plan_id",
            )
        return {row["plan_id"]: self._entry(row) for row in rows}

    def order(self, *, include_archived: bool = False) -> builtins.list[str]:
        """The plan ids, built-in and created, in the superadmin's order."""
        return list(self.current(include_archived=include_archived))

    def upgrade_order(self) -> builtins.list[str]:
        """The plans an organization can be pointed to, cheapest first (``plans.upgrade_order``)."""
        return upgrade_order(self.current())

    def self_serve(self) -> builtins.list[str]:
        """The plans an owner can buy through checkout: public, priced per seat, not by contract, not archived."""
        return [
            plan_id
            for plan_id, entry in self.current().items()
            if plan_id != TRIAL
            and entry["public"]
            and not entry["pricing"].get("contact_sales")
            and int(entry["pricing"].get("seat_cents_month") or 0) > 0
        ]

    def name(self, plan_id: str) -> str:
        """The plan's display name ("All access"), for messages; the id in title case if it is unknown."""
        with self.db.read() as connection:
            row = one(
                connection, "SELECT name FROM plans WHERE plan_id = ?", (plan_id,)
            )
        return row["name"] if row else str(plan_id).replace("_", " ").title()

    def get(self, plan_id: str, version: int | None = None) -> dict[str, Any]:
        with self.db.read() as connection:
            if version is None:
                row = one(
                    connection,
                    "SELECT plan_id, name, version, entitlements, pricing, active FROM plans WHERE plan_id = ?",
                    (plan_id,),
                )
            else:
                row = one(
                    connection,
                    """SELECT v.plan_id, p.name, v.version, v.entitlements, v.pricing, p.active FROM plan_versions v
                    JOIN plans p ON p.plan_id = v.plan_id WHERE v.plan_id = ? AND v.version = ?""",
                    (plan_id, version),
                )
        if row is None:
            raise NotFound("Plan not found.")
        return {
            "plan_id": row["plan_id"],
            "name": row["name"],
            "version": row["version"],
            "entitlements": loads(row["entitlements"], {}),
            "pricing": loads(row["pricing"], {}),
            "archived": not row["active"],
        }

    def create(
        self,
        plan_id: str,
        *,
        name: str,
        description: str = "",
        entitlements: dict[str, Any] | None = None,
        pricing: dict[str, Any] | None = None,
        public: bool = True,
        based_on: str = "starter",
        by: str,
    ) -> dict[str, Any]:
        """A new plan, version 1. Every limit it does not name is copied from ``based_on`` (never "no limit" by
        accident); the features are exactly the ones given, or the base plan's when none are."""
        plan_id = str(plan_id or "").strip()
        if not PLAN_ID.fullmatch(plan_id) or plan_id in RESERVED_PLAN_IDS:
            raise ValidationFailed(
                "The plan id is 3 to 30 lowercase letters, digits or underscores, starting with a letter."
            )
        name = str(name or "").strip()
        if not 2 <= len(name) <= 60:
            raise ValidationFailed("Enter the plan's name (2 to 60 characters).")
        base = self.get(based_on)
        new_entitlements = {
            **{
                key: value
                for key, value in base["entitlements"].items()
                if key in LIMIT_NAMES
                or key in {"coach_max_scope", "support", "features"}
            },
            **validate_entitlements(entitlements or {}),
        }
        for key in LIMIT_NAMES:
            new_entitlements.setdefault(key, 0)
        new_entitlements.setdefault("coach_max_scope", "view")
        new_entitlements.setdefault("support", "email")
        new_pricing = {
            "currency": "usd",
            "seat_cents_month": 0,
            "min_seats": 0,
            "overage": {},
            "trial_days": 0,
            **validate_pricing(pricing or {}),
        }
        with self.db.write() as connection:
            if one(
                connection, "SELECT 1 AS x FROM plans WHERE plan_id = ?", (plan_id,)
            ):
                raise Conflict(
                    "A plan with that id exists already.", code="plan_exists"
                )
            last = one(connection, "SELECT MAX(sort_order) AS n FROM plans", ())
            order = int(last["n"]) + 1 if last and last["n"] is not None else 0
            connection.execute(
                """INSERT INTO plans (plan_id, name, version, entitlements, pricing, sort_order, public, active, updated_at,
                description, created_by) VALUES (?, ?, 1, ?, ?, ?, ?, 1, ?, ?, ?)""",
                (
                    plan_id,
                    name,
                    dumps(new_entitlements),
                    dumps(new_pricing),
                    order,
                    int(bool(public)),
                    self.clock.now(),
                    str(description or "").strip()[:300],
                    by,
                ),
            )
            connection.execute(
                "INSERT INTO plan_versions (plan_id, version, entitlements, pricing, created_at, created_by) VALUES (?, 1, ?, ?, ?, ?)",
                (
                    plan_id,
                    dumps(new_entitlements),
                    dumps(new_pricing),
                    self.clock.now(),
                    by,
                ),
            )
        return self.current(include_archived=True)[plan_id]

    def update(
        self,
        plan_id: str,
        *,
        entitlements: dict[str, Any] | None,
        pricing: dict[str, Any] | None,
        by: str,
    ) -> int:
        """Publish a new plan version. Existing subscriptions stay on their version until migrated."""
        current = self.get(plan_id)
        checked = validate_entitlements(
            entitlements or {},
            allowed_features=current["entitlements"].get("features") or [],
        )
        new_entitlements = {**current["entitlements"], **checked}
        new_pricing = {**current["pricing"], **validate_pricing(pricing or {})}
        version = current["version"] + 1
        with self.db.write() as connection:
            connection.execute(
                "UPDATE plans SET version = ?, entitlements = ?, pricing = ?, updated_at = ? WHERE plan_id = ?",
                (
                    version,
                    dumps(new_entitlements),
                    dumps(new_pricing),
                    self.clock.now(),
                    plan_id,
                ),
            )
            connection.execute(
                "INSERT INTO plan_versions (plan_id, version, entitlements, pricing, created_at, created_by) VALUES (?, ?, ?, ?, ?, ?)",
                (
                    plan_id,
                    version,
                    dumps(new_entitlements),
                    dumps(new_pricing),
                    self.clock.now(),
                    by,
                ),
            )
        return version

    def describe(
        self,
        plan_id: str,
        *,
        name: str | None = None,
        description: str | None = None,
        public: bool | None = None,
    ) -> None:
        """Change what a plan is called and whether it is sold on the billing page; no new version is needed."""
        self.get(plan_id)
        values: dict[str, Any] = {}
        if name is not None:
            name = str(name).strip()
            if not 2 <= len(name) <= 60:
                raise ValidationFailed("Enter the plan's name (2 to 60 characters).")
            values["name"] = name
        if description is not None:
            values["description"] = str(description).strip()[:300]
        if public is not None:
            if plan_id == TRIAL:
                raise ValidationFailed("The trial is never sold on the billing page.")
            values["public"] = int(bool(public))
        if not values:
            return
        values["updated_at"] = self.clock.now()
        sets = ", ".join(f"{column} = ?" for column in values)
        with self.db.write() as connection:
            connection.execute(
                f"UPDATE plans SET {sets} WHERE plan_id = ?",
                (*values.values(), plan_id),
            )

    def archive(self, plan_id: str, archived: bool = True) -> None:
        """Archive (no new subscriptions; existing ones keep the plan) or restore a plan. The trial stays."""
        plan = self.get(plan_id)
        if plan_id == TRIAL:
            raise ValidationFailed("The trial cannot be archived.")
        if plan["archived"] == archived:
            raise Conflict(
                "The plan is archived already."
                if archived
                else "The plan is not archived.",
                code="unchanged",
            )
        with self.db.write() as connection:
            connection.execute(
                "UPDATE plans SET active = ?, archived_at = ?, updated_at = ? WHERE plan_id = ?",
                (
                    0 if archived else 1,
                    self.clock.now() if archived else None,
                    self.clock.now(),
                    plan_id,
                ),
            )

    def set_order(self, plan_ids: builtins.list[str]) -> builtins.list[str]:
        """The superadmin's order: every plan, archived ones included, each once."""
        every = self.order(include_archived=True)
        if not isinstance(plan_ids, list) or sorted(map(str, plan_ids)) != sorted(
            every
        ):
            raise ValidationFailed("Give every plan exactly once, in the new order.")
        with self.db.write() as connection:
            for index, plan_id in enumerate(plan_ids):
                connection.execute(
                    "UPDATE plans SET sort_order = ? WHERE plan_id = ?",
                    (index, plan_id),
                )
        return self.order(include_archived=True)

    def schedule_migration(
        self, plan_id: str, to_version: int, effective_at: int, by: str
    ) -> int:
        self.get(plan_id, to_version)
        with self.db.write() as connection:
            cursor = connection.execute(
                "INSERT INTO plan_migrations (plan_id, from_version, to_version, effective_at, created_by) VALUES (?, 0, ?, ?, ?)",
                (plan_id, to_version, effective_at, by),
            )
            return int(cursor.lastrowid or 0)

    def apply_due_migrations(self) -> builtins.list[dict[str, Any]]:
        """Move subscriptions to a new plan version on the chosen date; return what moved."""
        applied: list[dict[str, Any]] = []
        now = self.clock.now()
        with self.db.write() as connection:
            due = many(
                connection,
                "SELECT * FROM plan_migrations WHERE applied_at IS NULL AND effective_at <= ? ORDER BY id",
                (now,),
            )
            for migration in due:
                moved = connection.execute(
                    "SELECT org_id FROM subscriptions WHERE plan_id = ? AND plan_version < ?",
                    (migration["plan_id"], migration["to_version"]),
                ).fetchall()
                connection.execute(
                    "UPDATE subscriptions SET plan_version = ?, updated_at = ? WHERE plan_id = ? AND plan_version < ?",
                    (
                        migration["to_version"],
                        now,
                        migration["plan_id"],
                        migration["to_version"],
                    ),
                )
                connection.execute(
                    "UPDATE plan_migrations SET applied_at = ? WHERE id = ?",
                    (now, migration["id"]),
                )
                applied.append(
                    {
                        "plan_id": migration["plan_id"],
                        "to_version": migration["to_version"],
                        "orgs": [r["org_id"] for r in moved],
                    }
                )
        return applied


# --------------------------------------------------------------------------- #
# Feature flags
# --------------------------------------------------------------------------- #


class FlagService:
    """Operator flags: per plan, per tenant, by percentage, or a global kill switch.

    Precedence is organization, then plan. A global flag that is switched *off* is
    a kill switch: it overrides everything, so a risky source can be disabled for
    every tenant without a deploy and rolled back the same way.
    """

    SCOPES = ("global", "plan", "org")

    def __init__(self, db: ControlDB, clock: Clock) -> None:
        self.db = db
        self.clock = clock
        self._lock = threading.Lock()
        self._cache: tuple[int, list[dict[str, Any]]] | None = None

    def _rows(self) -> builtins.list[dict[str, Any]]:
        now = self.clock.now()
        with self._lock:
            if self._cache and self._cache[0] > now:
                return self._cache[1]
        with self.db.read() as connection:
            rows = many(connection, "SELECT * FROM feature_flags")
        with self._lock:
            self._cache = (now + 5, rows)
        return rows

    def invalidate(self) -> None:
        with self._lock:
            self._cache = None

    def set(
        self,
        flag: str,
        scope_type: str,
        scope_id: str,
        enabled: bool,
        *,
        percent: int = 100,
        by: str = "",
    ) -> None:
        if scope_type not in self.SCOPES:
            raise ValidationFailed("Flag scope must be global, plan or org.")
        if not 0 <= percent <= 100:
            raise ValidationFailed("Percent must be between 0 and 100.")
        if not flag or len(flag) > 80:
            raise ValidationFailed("Enter a flag name.")
        scope_id = "" if scope_type == "global" else scope_id
        with self.db.write() as connection:
            connection.execute(
                """INSERT INTO feature_flags (flag, scope_type, scope_id, enabled, percent, updated_by, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?) ON CONFLICT(flag, scope_type, scope_id) DO UPDATE SET
                enabled = excluded.enabled, percent = excluded.percent, updated_by = excluded.updated_by, updated_at = excluded.updated_at""",
                (
                    flag,
                    scope_type,
                    scope_id,
                    int(enabled),
                    percent,
                    by,
                    self.clock.now(),
                ),
            )
        self.invalidate()

    def delete(self, flag: str, scope_type: str, scope_id: str = "") -> bool:
        with self.db.write() as connection:
            removed = connection.execute(
                "DELETE FROM feature_flags WHERE flag = ? AND scope_type = ? AND scope_id = ?",
                (flag, scope_type, "" if scope_type == "global" else scope_id),
            ).rowcount
        self.invalidate()
        return removed > 0

    def list(self) -> builtins.list[dict[str, Any]]:
        return sorted(
            self._rows(),
            key=lambda row: (row["flag"], row["scope_type"], row["scope_id"]),
        )

    @staticmethod
    def _in_rollout(flag: str, org_id: str, percent: int) -> bool:
        if percent >= 100:
            return True
        if percent <= 0:
            return False
        bucket = (
            int(hashlib.sha256(f"{flag}:{org_id}".encode()).hexdigest()[:8], 16) % 100
        )
        return bucket < percent

    def killed(self, flag: str) -> bool:
        return any(
            r["flag"] == flag and r["scope_type"] == "global" and not r["enabled"]
            for r in self._rows()
        )

    def evaluate(self, flag: str, org_id: str, plan_id: str) -> bool | None:
        """True or False when a flag decides for this organization, None when none applies."""
        rows = [r for r in self._rows() if r["flag"] == flag]
        if any(r["scope_type"] == "global" and not r["enabled"] for r in rows):
            return False
        for scope_type, scope_id in (
            ("org", org_id),
            ("plan", plan_id),
            ("global", ""),
        ):
            for row in rows:
                if row["scope_type"] == scope_type and row["scope_id"] == scope_id:
                    if not row["enabled"]:
                        return False
                    return self._in_rollout(flag, org_id, int(row["percent"]))
        return None

    def flag_names(self) -> builtins.set[str]:
        return {row["flag"] for row in self._rows()}


# --------------------------------------------------------------------------- #
# App catalog controls
# --------------------------------------------------------------------------- #


class AppSettings:
    """The superadmin's controls over the app catalog: an app switched off for everyone, and per-plan seat prices.

    An app that is off is closed to every organization (the trial, Enterprise and grants too) and is not billed; the
    organizations' data and add-on choices are kept, so switching it back on restores them. Prices change
    ``plans.APP_PRICING`` per plan (cents per candidate seat per month, 0 = included, None = not offered); only the
    differences are stored. Invoices and quotes read them when they are made, so a subscriber pays a new price from
    its next billing period. Careers is part of every plan and cannot be priced or switched off.

    A plan the superadmin creates has no defaults ("not offered" for every app); what it includes or sells is stored
    here like a change to a built-in plan, so the Apps page and the Plans page edit the same thing. Resetting an app
    to the defaults keeps what the created plans say about it.
    """

    MAX_CENTS = 100_000  # $1,000 per seat per month

    def __init__(
        self, db: ControlDB, clock: Clock, catalog: PlanCatalog | None = None
    ) -> None:
        self.db = db
        self.clock = clock
        self.catalog = catalog
        self._lock = threading.Lock()
        self._cache: tuple[int, dict[str, dict[str, Any]]] | None = None

    def plan_ids(self) -> builtins.list[str]:
        """The plans an app can be priced on: every plan in the catalog but the trial (which has every app)."""
        if self.catalog is None:
            return list(APP_PLANS)
        return [p for p in self.catalog.order(include_archived=True) if p != TRIAL]

    def _rows(self) -> dict[str, dict[str, Any]]:
        now = self.clock.now()
        with self._lock:
            if self._cache and self._cache[0] > now:
                return self._cache[1]
        with self.db.read() as connection:
            rows = {
                r["app_id"]: {**r, "prices": loads(r["prices"], {})}
                for r in many(connection, "SELECT * FROM platform_apps")
            }
        with self._lock:
            self._cache = (now + 5, rows)
        return rows

    def invalidate(self) -> None:
        with self._lock:
            self._cache = None

    def enabled(self, app_id: str) -> bool:
        row = self._rows().get(app_catalog.current_id(app_id))
        return not row or bool(row["enabled"])

    def overrides(self) -> PriceOverrides:
        return {
            app_id: row["prices"]
            for app_id, row in self._rows().items()
            if row["prices"]
        }

    def state(self, plan_id: str, app_id: str) -> str:
        return app_state(plan_id, app_id, self.overrides())

    def price(self, plan_id: str, app_id: str) -> int | None:
        return app_price(plan_id, app_id, self.overrides())

    def plan_for(self, app_id: str) -> str | None:
        order = self.catalog.upgrade_order() if self.catalog is not None else None
        return plan_for_app(app_id, self.overrides(), order)

    def for_plan(self, plan_id: str) -> dict[str, dict[str, Any]]:
        """What one plan says about every app: ``state`` (included, addon, unavailable) and ``seat_cents_month``."""
        out = {}
        for app in app_catalog.APPS:
            out[app.id] = {
                "name": app.name,
                "state": self.state(plan_id, app.id),
                "seat_cents_month": self.price(plan_id, app.id),
                "priced": app.id in APP_PRICING,
                "enabled": self.enabled(app.id),
            }
        return out

    def describe(self) -> builtins.list[dict[str, Any]]:
        """Every app with its switch, its default and current prices per plan, and who changed it last."""
        rows = self._rows()
        plan_ids = self.plan_ids()
        out = []
        for app in app_catalog.APPS:
            row = rows.get(app.id) or {}
            sold = app.id in APP_PRICING
            current = app_prices(app.id, self.overrides()) if sold else {}
            defaults = dict(APP_PRICING.get(app.id, {}))
            if sold:
                for plan_id in (
                    plan_ids
                ):  # a created plan offers nothing until the superadmin says so
                    current.setdefault(plan_id, None)
                    if plan_id not in BUILTIN_PLANS:
                        defaults.setdefault(plan_id, None)
            out.append(
                {
                    "id": app.id,
                    "name": app.name,
                    "category": app.category,
                    "enabled": not row or bool(row["enabled"]),
                    "priced": sold,
                    "default_prices": defaults if sold else {},
                    "prices": current,
                    "changed_plans": sorted(
                        p for p in (row.get("prices") or {}) if p in BUILTIN_PLANS
                    ),
                    "note": row.get("note", ""),
                    "updated_by": row.get("updated_by", ""),
                    "updated_at": row.get("updated_at"),
                }
            )
        return out

    def _check_price(self, value: Any) -> None:
        if value is not None and (
            isinstance(value, bool)
            or not isinstance(value, int)
            or not 0 <= value <= self.MAX_CENTS
        ):
            raise ValidationFailed(
                "A price is whole cents per seat per month from 0 (included) to 100000, or null (not offered)."
            )

    def update(
        self,
        app_id: str,
        *,
        enabled: bool | None = None,
        prices: dict[str, Any] | None = None,
        note: str = "",
        by: str = "",
    ) -> dict[str, Any]:
        """Switch an app on or off for everyone and/or set its seat price per plan (``None`` = not offered)."""
        app_id = app_catalog.current_id(app_id)
        if app_id not in app_catalog.BY_ID:
            raise NotFound("App not found.")
        if app_id not in APP_PRICING:
            raise ValidationFailed(
                f"{app_catalog.BY_ID[app_id].name} is part of every plan; it cannot be priced or switched off."
            )
        row = self._rows().get(app_id) or {}
        changes = dict(row.get("prices") or {})
        if prices is not None:
            plan_ids = self.plan_ids()
            if not isinstance(prices, dict) or set(prices) - set(plan_ids):
                raise ValidationFailed(
                    f"Prices are given per plan: {', '.join(plan_ids)}."
                )
            for plan_id, value in prices.items():
                self._check_price(value)
                if value == APP_PRICING[app_id].get(plan_id):
                    changes.pop(plan_id, None)  # back to the default: nothing to store
                else:
                    changes[plan_id] = value
        on = bool(row["enabled"]) if row and enabled is None else enabled is not False
        self._write(app_id, on, changes, note[:300], by)
        return next(a for a in self.describe() if a["id"] == app_id)

    def _write(
        self, app_id: str, enabled: bool, prices: dict[str, Any], note: str, by: str
    ) -> None:
        with self.db.write() as connection:
            connection.execute(
                """INSERT INTO platform_apps (app_id, enabled, prices, note, updated_by, updated_at)
                VALUES (?, ?, ?, ?, ?, ?) ON CONFLICT(app_id) DO UPDATE SET enabled = excluded.enabled,
                prices = excluded.prices, note = excluded.note, updated_by = excluded.updated_by,
                updated_at = excluded.updated_at""",
                (app_id, int(enabled), dumps(prices), note, by, self.clock.now()),
            )
        self.invalidate()

    def check_plan_apps(self, apps: Any) -> dict[str, Any]:
        """``{app: 0 | cents | None | "included" | "unavailable"}`` checked, as ``{app: cents or None}``."""
        if not isinstance(apps, dict):
            raise ValidationFailed(
                "apps maps an app to 0 (included), cents (add-on) or null."
            )
        wanted: dict[str, Any] = {}
        for raw, value in apps.items():
            app_id = app_catalog.current_id(str(raw))
            if app_id not in app_catalog.BY_ID:
                raise NotFound(f"App not found: {raw}.")
            if app_id not in APP_PRICING:
                continue  # Careers: part of every plan
            if value == "included":
                value = 0
            elif value in ("unavailable", "none"):
                value = None
            self._check_price(value)
            wanted[app_id] = value
        return wanted

    def set_plan_apps(
        self, plan_id: str, apps: dict[str, Any], *, by: str = ""
    ) -> builtins.list[str]:
        """What one plan includes, sells per seat or does not offer, app by app (``0``, cents, ``None``); the apps
        whose price on the plan changed are returned. Apps not named keep what they have."""
        if plan_id == TRIAL:
            raise ValidationFailed("The trial always has every app.")
        if plan_id not in self.plan_ids():
            raise NotFound("Plan not found.")
        wanted = self.check_plan_apps(apps)
        changed = []
        rows = self._rows()
        for app_id, value in wanted.items():
            if app_price(plan_id, app_id, self.overrides()) == value:
                continue
            row = rows.get(app_id) or {}
            prices = dict(row.get("prices") or {})
            if value == APP_PRICING[app_id].get(plan_id):
                prices.pop(plan_id, None)
            else:
                prices[plan_id] = value
            self._write(
                app_id,
                not row or bool(row["enabled"]),
                prices,
                row.get("note", "") or "",
                by,
            )
            rows = self._rows()
            changed.append(app_id)
        return changed

    def reset(self, app_id: str) -> bool:
        """Back to the catalog's defaults: on, with the prices in ``plans.APP_PRICING``. What a plan the superadmin
        created says about the app is kept (those plans have no defaults to go back to)."""
        app_id = app_catalog.current_id(app_id)
        row = self._rows().get(app_id)
        if not row:
            return False
        kept = {
            p: v for p, v in (row.get("prices") or {}).items() if p not in BUILTIN_PLANS
        }
        if kept:
            if bool(row["enabled"]) and kept == row["prices"]:
                return False
            self._write(app_id, True, kept, "", row.get("updated_by", "") or "")
            return True
        with self.db.write() as connection:
            removed = connection.execute(
                "DELETE FROM platform_apps WHERE app_id = ?", (app_id,)
            ).rowcount
        self.invalidate()
        return removed > 0


# --------------------------------------------------------------------------- #
# Resolver
# --------------------------------------------------------------------------- #


class EntitlementService:
    def __init__(
        self,
        db: ControlDB,
        clock: Clock,
        config: CloudConfig,
        catalog: PlanCatalog,
        flags: FlagService,
        app_settings: AppSettings | None = None,
    ) -> None:
        self.db = db
        self.clock = clock
        self.config = config
        self.catalog = catalog
        self.flags = flags
        self.app_settings = app_settings or AppSettings(db, clock)
        self._lock = threading.Lock()
        self._cache: dict[str, tuple[int, Entitlements]] = {}

    def invalidate(self, org_id: str | None = None) -> None:
        with self._lock:
            if org_id is None:
                self._cache.clear()
            else:
                self._cache.pop(org_id, None)

    def resolve(self, org_id: str, *, fresh: bool = False) -> Entitlements:
        now = self.clock.now()
        if not fresh:
            with self._lock:
                cached = self._cache.get(org_id)
            if cached and cached[0] > now:
                return cached[1]
        with self.db.read() as connection:
            sub = one(
                connection, "SELECT * FROM subscriptions WHERE org_id = ?", (org_id,)
            )
            org = one(
                connection,
                "SELECT status FROM organizations WHERE org_id = ?",
                (org_id,),
            )
        if sub is None or org is None:
            raise NotFound("Organization not found.")
        with self.db.read() as connection:
            app_rows = many(
                connection,
                "SELECT app_id, status FROM org_apps WHERE org_id = ? AND status IN ('active', 'granted', 'disabled')",
                (org_id,),
            )
        plan = self.catalog.get(sub["plan_id"], sub["plan_version"])
        limits = merge_limits(
            with_later_limits(sub["plan_id"], plan["entitlements"]),
            loads(sub["overrides"], {}),
        )
        if (
            sub["candidate_seats"]
            and sub["candidate_seats"] > 0
            and limits.get("candidate_seats") is not None
        ):
            limits["candidate_seats"] = min(
                int(limits["candidate_seats"]), int(sub["candidate_seats"])
            )
        features = set(limits.get("features", []))
        for flag in self.flags.flag_names():
            decision = self.flags.evaluate(flag, org_id, sub["plan_id"])
            if decision is True:
                features.add(flag)
            elif decision is False:
                features.discard(flag)
        status = sub["status"]
        # rows stored under an old app id ("shop") count for the app it became (Commerce)
        subscribed = {
            app_catalog.current_id(r["app_id"])
            for r in app_rows
            if r["status"] in ("active", "granted")
        }
        disabled = {
            app_catalog.current_id(r["app_id"])
            for r in app_rows
            if r["status"] == "disabled"
        }
        apps = self._apps_for(sub["plan_id"], features, subscribed, disabled)
        for app_id in apps:  # an app that is on carries its features: the feature checks below keep working
            app = app_catalog.BY_ID[app_id]
            for feature in (app.feature, *app.extra_features):
                if feature:
                    features.add(feature)
        for app in (
            app_catalog.APPS
        ):  # an app that is off takes its features away, even from an old plan version
            if app.id not in apps:
                for feature in (app.feature, *app.extra_features):
                    if feature:
                        features.discard(feature)
        entitlements = Entitlements(
            org_id=org_id,
            plan_id=sub["plan_id"],
            plan_version=sub["plan_version"],
            status=status,
            limits=limits,
            features=frozenset(features),
            apps=frozenset(apps),
            # a trial whose end has passed is over even before the scheduler moves it on (read-only, export only)
            restrictions=STATUS_RESTRICTIONS["cancelled"]
            if status == "trialing" and sub["trial_end"] and sub["trial_end"] <= now
            else STATUS_RESTRICTIONS.get(status, frozenset()),
            period_start=sub["period_start"],
            period_end=sub["period_end"],
            trial_end=sub["trial_end"],
            suspended=org["status"] in {"suspended", "deleted"},
            over_limits=tuple(loads(sub["over_limits"], [])),
            overage_enabled=bool(sub["overage_enabled"]) and "overage" in features,
            pricing=plan["pricing"],
        )
        expires = now + self.config.entitlement_cache_seconds
        if status == "trialing" and sub["trial_end"] and sub["trial_end"] > now:
            expires = min(expires, int(sub["trial_end"]))  # the trial's end is seen at once
        with self._lock:
            self._cache[org_id] = (expires, entitlements)
        return entitlements

    def _apps_for(
        self,
        plan_id: str,
        features: builtins.set[str],
        subscribed: builtins.set[str],
        disabled: builtins.set[str] | frozenset[str] = frozenset(),
    ) -> builtins.set[str]:
        """The apps an organization may open: included by the plan, bought as add-ons, or granted by an operator,
        unless an operator has switched the app off for everyone or for this organization."""
        apps: builtins.set[str] = set()
        for app in app_catalog.APPS:
            if not self.app_settings.enabled(app.id) or app.id in disabled:
                continue
            state = self.app_settings.state(plan_id, app.id)
            if state == "included":
                apps.add(app.id)
            elif app.id in subscribed:
                apps.add(
                    app.id
                )  # bought (active) or granted; an unavailable app can still be granted
        return apps

    # -- checks ----------------------------------------------------------------

    def required_plan(self, feature: str) -> str | None:
        """The cheapest plan an organization can move to that includes ``feature`` (built-in or created)."""
        return plan_for_feature(self.catalog.current(), feature)

    def app_disabled_here(self, org_id: str, app_id: str) -> bool:
        """An operator switched this app off for this one organization."""
        with self.db.read() as connection:
            row = one(
                connection,
                "SELECT 1 AS x FROM org_apps WHERE org_id = ? AND app_id = ? AND status = 'disabled'",
                (org_id, app_catalog.current_id(app_id)),
            )
        return row is not None

    def require_app(
        self, org_id: str, app_id: str, *, audience: str = "staff"
    ) -> Entitlements:
        """The organization must have the app that owns the route, or the call is ``402 app_required``."""
        entitlements = self.resolve(org_id)
        app_id = app_catalog.current_id(app_id)
        if app_id not in app_catalog.BY_ID or entitlements.has_app(app_id):
            return entitlements
        app = app_catalog.BY_ID[app_id]
        if not self.app_settings.enabled(app_id):
            raise AppDisabled(
                f"{app.name} is switched off for now. Your data is kept and comes back when it returns.",
                app=app_id,
                name=app.name,
            )
        if self.app_disabled_here(org_id, app_id):
            raise AppDisabled(
                f"{app.name} is switched off for your organization. Your data is kept; contact support to turn it back on.",
                app=app_id,
                name=app.name,
                scope="organization",
            )
        state = self.app_settings.state(entitlements.plan_id, app_id)
        needed = self.app_settings.plan_for(app_id)
        plan_name = self.catalog.name(entitlements.plan_id)
        if audience == "candidate":
            message = f"{app.name} is not enabled for your organization."
            hint = "Ask an owner or administrator to turn it on."
        elif state == "addon":
            message = f"{app.name} is not turned on for your organization."
            hint = f"Turn it on under Apps (billed per candidate seat on your {plan_name} plan)."
        elif needed:
            message = f"{app.name} is not available on the {plan_name} plan."
            hint = f"Upgrade to {self.catalog.name(needed)} and turn it on under Apps."
        else:
            message = f"{app.name} is not available on the {plan_name} plan."
            hint = "Contact sales."
        raise AppRequired(
            message + " " + hint,
            app=app_id,
            name=app.name,
            plan_hint=hint,
            required_plan=needed if state == "unavailable" else None,
            current_plan=entitlements.plan_id,
            audience=audience,
        )

    def require_feature(
        self, org_id: str, feature: str, *, audience: str = "staff", label: str = ""
    ) -> Entitlements:
        entitlements = self.resolve(org_id)
        if feature in entitlements.features:
            return entitlements
        killed = self.flags.killed(feature)
        needed = None if killed else self.required_plan(feature)
        name = label or feature
        if killed:
            message = f"{name} is switched off for every organization right now."
        elif audience == "candidate":
            message = f"{name} is not enabled for your organization."
        elif needed:
            message = f"{name} is not part of your {self.catalog.name(entitlements.plan_id)} plan. Upgrade to {self.catalog.name(needed)} to use it."
        else:
            message = f"{name} is not part of your {self.catalog.name(entitlements.plan_id)} plan."
        raise PlanRequired(
            message,
            feature=feature,
            required_plan=needed,
            current_plan=entitlements.plan_id,
            audience=audience,
        )

    def require_state(
        self,
        org_id: str,
        *,
        write: bool = False,
        automation: bool = False,
        read: bool = True,
    ) -> Entitlements:
        entitlements = self.resolve(org_id)
        if entitlements.suspended:
            raise SubscriptionInactive(
                "This organization is suspended. Contact support.", status="suspended"
            )
        if "reads" in entitlements.restrictions and read:
            raise SubscriptionInactive(
                "This organization has been deleted.", status=entitlements.status
            )
        if write and "writes" in entitlements.restrictions:
            raise SubscriptionInactive(
                f"The subscription is {entitlements.status.replace('_', ' ')}: data is read-only until payment is restored."
                if entitlements.status != "cancelled"
                else "The subscription is cancelled: data is read-only and can be exported.",
                status=entitlements.status,
            )
        if automation and "automation" in entitlements.restrictions:
            raise SubscriptionInactive(
                f"Automation is stopped because the subscription is {entitlements.status.replace('_', ' ')}.",
                status=entitlements.status,
            )
        if automation and entitlements.over_limits:
            raise SubscriptionInactive(
                "Automation is paused until the organization is within its plan limits: "
                + ", ".join(entitlements.over_limits)
                + ".",
                status=entitlements.status,
                over_limits=list(entitlements.over_limits),
            )
        return entitlements


# --------------------------------------------------------------------------- #
# Usage
# --------------------------------------------------------------------------- #


@dataclass
class Reservation:
    org_id: str
    metric: str
    amount: int
    period: str
    day: str
    candidate_id: str
    overage: int = 0
    released: bool = False
    hour: int = 0  # the hour bucket its overage was counted in (usage_overage)


class LimitReached(Exception):
    def __init__(self, metric: str, scope: str, used: int, limit: int) -> None:
        super().__init__(f"{metric} limit reached ({scope}).")
        self.metric, self.scope, self.used, self.limit = metric, scope, used, limit


def _count_overage(
    connection: Any, org_id: str, metric: str, now: int, units: int
) -> None:
    """The overage of one action, in the hour it happened (``UsageService.overage_between`` sums a billing period)."""
    if units > 0:
        connection.execute(
            "INSERT INTO usage_overage (org_id, metric, hour, value) VALUES (?, ?, ?, ?) ON CONFLICT(org_id, metric, hour) DO UPDATE SET value = usage_overage.value + excluded.value",
            (org_id, f"{metric}:overage", int(now) // 3600, int(units)),
        )


class UsageService:
    def __init__(self, db: ControlDB, clock: Clock) -> None:
        self.db = db
        self.clock = clock

    # -- counting -----------------------------------------------------------------

    def used(self, org_id: str, metric: str, period: str | None = None) -> int:
        with self.db.read() as connection:
            row = one(
                connection,
                "SELECT value FROM usage_counters WHERE org_id = ? AND period = ? AND metric = ?",
                (org_id, period or month_key(self.clock.now()), metric),
            )
        return int(row["value"]) if row else 0

    def used_today(self, org_id: str, candidate_id: str, metric: str) -> int:
        with self.db.read() as connection:
            row = one(
                connection,
                "SELECT value FROM usage_daily WHERE org_id = ? AND candidate_id = ? AND day = ? AND metric = ?",
                (org_id, candidate_id, day_key(self.clock.now()), metric),
            )
        return int(row["value"]) if row else 0

    def snapshot(self, org_id: str, period: str | None = None) -> dict[str, int]:
        with self.db.read() as connection:
            rows = many(
                connection,
                "SELECT metric, value FROM usage_counters WHERE org_id = ? AND period = ?",
                (org_id, period or month_key(self.clock.now())),
            )
        return {row["metric"]: int(row["value"]) for row in rows}

    def history(self, org_id: str, periods: int = 6) -> builtins.list[dict[str, Any]]:
        with self.db.read() as connection:
            rows = many(
                connection,
                "SELECT period, metric, value FROM usage_counters WHERE org_id = ? ORDER BY period DESC",
                (org_id,),
            )
        grouped: dict[str, dict[str, int]] = {}
        for row in rows:
            grouped.setdefault(row["period"], {})[row["metric"]] = int(row["value"])
        return [
            {"period": period, **values}
            for period, values in list(grouped.items())[:periods]
        ]

    def remaining_today(
        self, org_id: str, candidate_id: str, metric: str, daily_limit: int | None
    ) -> int | None:
        if daily_limit is None:
            return None
        return max(0, daily_limit - self.used_today(org_id, candidate_id, metric))

    def remaining_month(
        self, org_id: str, metric: str, limit: int | None, *, overage: bool = False
    ) -> int | None:
        if limit is None or overage:
            return None
        return max(0, limit - self.used(org_id, metric))

    # -- atomic reserve / record / release --------------------------------------------

    def reserve(
        self,
        org_id: str,
        metric: str,
        amount: int = 1,
        *,
        monthly_limit: int | None = None,
        daily_limit: int | None = None,
        candidate_id: str = "",
        allow_overage: bool = False,
    ) -> Reservation:
        """Take ``amount`` units now, or raise :class:`LimitReached`. One transaction, one conditional UPDATE each."""
        now = self.clock.now()
        period, day = month_key(now), day_key(now)
        reservation = Reservation(
            org_id, metric, amount, period, day, candidate_id, hour=now // 3600
        )
        with self.db.write(
            exclusive=False
        ) as connection:  # atomic conditional UPDATEs, no global lock
            if daily_limit is not None and candidate_id:
                connection.execute(
                    "INSERT OR IGNORE INTO usage_daily (org_id, candidate_id, day, metric, value) VALUES (?, ?, ?, ?, 0)",
                    (org_id, candidate_id, day, metric),
                )
                updated = connection.execute(
                    "UPDATE usage_daily SET value = value + ? WHERE org_id = ? AND candidate_id = ? AND day = ? AND metric = ? AND value + ? <= ?",
                    (amount, org_id, candidate_id, day, metric, amount, daily_limit),
                ).rowcount
                if updated == 0:
                    row = one(
                        connection,
                        "SELECT value FROM usage_daily WHERE org_id = ? AND candidate_id = ? AND day = ? AND metric = ?",
                        (org_id, candidate_id, day, metric),
                    )
                    raise LimitReached(
                        metric,
                        "candidate_day",
                        int(row["value"]) if row else 0,
                        daily_limit,
                    )
            elif candidate_id:
                connection.execute(
                    "INSERT INTO usage_daily (org_id, candidate_id, day, metric, value) VALUES (?, ?, ?, ?, ?) ON CONFLICT(org_id, candidate_id, day, metric) DO UPDATE SET value = usage_daily.value + excluded.value",
                    (org_id, candidate_id, day, metric, amount),
                )
            connection.execute(
                "INSERT OR IGNORE INTO usage_counters (org_id, period, metric, value) VALUES (?, ?, ?, 0)",
                (org_id, period, metric),
            )
            if monthly_limit is None:
                connection.execute(
                    "UPDATE usage_counters SET value = value + ? WHERE org_id = ? AND period = ? AND metric = ?",
                    (amount, org_id, period, metric),
                )
            else:
                updated = connection.execute(
                    "UPDATE usage_counters SET value = value + ? WHERE org_id = ? AND period = ? AND metric = ? AND value + ? <= ?",
                    (amount, org_id, period, metric, amount, monthly_limit),
                ).rowcount
                if updated == 0:
                    row = one(
                        connection,
                        "SELECT value FROM usage_counters WHERE org_id = ? AND period = ? AND metric = ?",
                        (org_id, period, metric),
                    )
                    used = int(row["value"]) if row else 0
                    if not allow_overage:
                        raise LimitReached(metric, "org_month", used, monthly_limit)
                    over = used + amount - max(monthly_limit, used)
                    connection.execute(
                        "UPDATE usage_counters SET value = value + ? WHERE org_id = ? AND period = ? AND metric = ?",
                        (amount, org_id, period, metric),
                    )
                    connection.execute(
                        "INSERT INTO usage_counters (org_id, period, metric, value) VALUES (?, ?, ?, ?) ON CONFLICT(org_id, period, metric) DO UPDATE SET value = usage_counters.value + excluded.value",
                        (org_id, period, f"{metric}:overage", over),
                    )
                    _count_overage(connection, org_id, metric, now, over)
                    reservation.overage = over
        return reservation

    def release(self, reservation: Reservation) -> None:
        """Give a reservation back (the action did not happen)."""
        if reservation.released:
            return
        reservation.released = True
        with self.db.write(exclusive=False) as connection:
            connection.execute(
                "UPDATE usage_counters SET value = MAX(0, value - ?) WHERE org_id = ? AND period = ? AND metric = ?",
                (
                    reservation.amount,
                    reservation.org_id,
                    reservation.period,
                    reservation.metric,
                ),
            )
            if reservation.overage:
                connection.execute(
                    "UPDATE usage_counters SET value = MAX(0, value - ?) WHERE org_id = ? AND period = ? AND metric = ?",
                    (
                        reservation.overage,
                        reservation.org_id,
                        reservation.period,
                        f"{reservation.metric}:overage",
                    ),
                )
                connection.execute(
                    "UPDATE usage_overage SET value = MAX(0, value - ?) WHERE org_id = ? AND metric = ? AND hour = ?",
                    (
                        reservation.overage,
                        reservation.org_id,
                        f"{reservation.metric}:overage",
                        reservation.hour,
                    ),
                )
            if reservation.candidate_id:
                connection.execute(
                    "UPDATE usage_daily SET value = MAX(0, value - ?) WHERE org_id = ? AND candidate_id = ? AND day = ? AND metric = ?",
                    (
                        reservation.amount,
                        reservation.org_id,
                        reservation.candidate_id,
                        reservation.day,
                        reservation.metric,
                    ),
                )

    def record(
        self, org_id: str, metric: str, amount: int = 1, *, candidate_id: str = ""
    ) -> None:
        """Count something that already happened (no limit check)."""
        now = self.clock.now()
        with self.db.write(exclusive=False) as connection:
            connection.execute(
                "INSERT INTO usage_counters (org_id, period, metric, value) VALUES (?, ?, ?, ?) ON CONFLICT(org_id, period, metric) DO UPDATE SET value = usage_counters.value + excluded.value",
                (org_id, month_key(now), metric, amount),
            )
            if metric.endswith(":overage"):  # overage counted directly is billed in its period too
                _count_overage(
                    connection, org_id, metric.removesuffix(":overage"), now, amount
                )
            if candidate_id:
                connection.execute(
                    "INSERT INTO usage_daily (org_id, candidate_id, day, metric, value) VALUES (?, ?, ?, ?, ?) ON CONFLICT(org_id, candidate_id, day, metric) DO UPDATE SET value = usage_daily.value + excluded.value",
                    (org_id, candidate_id, day_key(now), metric, amount),
                )

    def record_metered(
        self,
        org_id: str,
        metric: str,
        amount: int,
        *,
        monthly_limit: int | None = None,
        candidate_id: str = "",
    ) -> int:
        """Count something that already happened; the part past ``monthly_limit`` is also counted as overage.

        Returns the overage units added. ``reserve`` does this for work that has not happened yet; this is
        for work whose size is only known afterwards (a run that used several searches).
        """
        if amount <= 0:
            return 0
        now = self.clock.now()
        period = month_key(now)
        over = 0
        with self.db.write(exclusive=False) as connection:
            connection.execute(
                "INSERT OR IGNORE INTO usage_counters (org_id, period, metric, value) VALUES (?, ?, ?, 0)",
                (org_id, period, metric),
            )
            connection.execute(
                "UPDATE usage_counters SET value = value + ? WHERE org_id = ? AND period = ? AND metric = ?",
                (amount, org_id, period, metric),
            )
            if monthly_limit is not None:
                row = one(
                    connection,
                    "SELECT value FROM usage_counters WHERE org_id = ? AND period = ? AND metric = ?",
                    (org_id, period, metric),
                )
                after = int(row["value"]) if row else amount
                over = max(0, after - max(int(monthly_limit), after - amount))
                if over:
                    connection.execute(
                        "INSERT INTO usage_counters (org_id, period, metric, value) VALUES (?, ?, ?, ?) ON CONFLICT(org_id, period, metric) DO UPDATE SET value = usage_counters.value + excluded.value",
                        (org_id, period, f"{metric}:overage", over),
                    )
                    _count_overage(connection, org_id, metric, now, over)
            if candidate_id:
                connection.execute(
                    "INSERT INTO usage_daily (org_id, candidate_id, day, metric, value) VALUES (?, ?, ?, ?, ?) ON CONFLICT(org_id, candidate_id, day, metric) DO UPDATE SET value = usage_daily.value + excluded.value",
                    (org_id, candidate_id, day_key(now), metric, amount),
                )
        return over

    def overage_between(self, org_id: str, start: int, end: int) -> dict[str, int]:
        """Overage units counted from ``start`` (inclusive) to ``end`` (exclusive), by ``<metric>:overage``.

        Units are kept by the hour they happened in; an hour belongs to the period its last second is in, so
        periods that follow each other share no hour, every unit is billed in exactly one of them, and an hour is
        over before the period it belongs to closes (a unit is never added to a period already billed)."""
        first, last = int(start) // 3600, int(end) // 3600
        with self.db.read() as connection:
            rows = many(
                connection,
                "SELECT metric, SUM(value) AS units FROM usage_overage WHERE org_id = ? AND hour >= ? AND hour < ? GROUP BY metric",
                (org_id, first, last),
            )
        return {row["metric"]: int(row["units"] or 0) for row in rows}

    def delete_org(self, org_id: str) -> None:
        with self.db.write() as connection:
            connection.execute("DELETE FROM usage_counters WHERE org_id = ?", (org_id,))
            connection.execute("DELETE FROM usage_daily WHERE org_id = ?", (org_id,))
            connection.execute("DELETE FROM usage_overage WHERE org_id = ?", (org_id,))


class Enforcer:
    """Turns a limit hit into the ``402 plan_required`` the UI shows as an upgrade prompt (or an org rule)."""

    METRIC_LABELS = {
        "applications": "applications",
        "searches": "searches",
        "cover_letters": "AI cover letters",
        "api_searches": "API searches",
        "pooled_searches": "pooled SerpApi searches",
        "lead_enrichments": "lead enrichments",
        "lead_emails": "outreach emails",
    }

    def __init__(self, entitlements: EntitlementService, usage: UsageService) -> None:
        self.entitlements = entitlements
        self.usage = usage

    def reserve(
        self,
        org_id: str,
        metric: str,
        amount: int = 1,
        *,
        monthly_limit_name: str | None = None,
        daily_limit_name: str | None = None,
        candidate_id: str = "",
        audience: str = "staff",
        policy_daily_cap: int | None = None,
    ) -> Reservation:
        entitlements = self.entitlements.resolve(org_id)
        monthly = entitlements.limit(monthly_limit_name) if monthly_limit_name else None
        daily = entitlements.limit(daily_limit_name) if daily_limit_name else None
        if policy_daily_cap is not None and policy_daily_cap > 0:
            daily = policy_daily_cap if daily is None else min(daily, policy_daily_cap)
        try:
            return self.usage.reserve(
                org_id,
                metric,
                amount,
                monthly_limit=monthly,
                daily_limit=daily,
                candidate_id=candidate_id,
                allow_overage=entitlements.overage_enabled,
            )
        except LimitReached as reached:
            raise self.plan_required(entitlements, reached, audience) from None

    def plan_required(
        self, entitlements: Entitlements, reached: LimitReached, audience: str
    ) -> PlanRequired:
        label = self.METRIC_LABELS.get(reached.metric, reached.metric.replace("_", " "))
        if reached.scope == "candidate_day":
            rule = f"{reached.limit} {label} per candidate per day"
            name = "apps_per_candidate_day"
        else:
            rule = f"{reached.limit:,} {label} per month"
            name = {
                "applications": "apps_per_org_month",
                "cover_letters": "cover_letters_per_month",
                "api_searches": "api_searches_per_month",
                "pooled_searches": "pooled_searches_per_month",
                "lead_enrichments": "lead_enrichments_per_month",
                "lead_emails": "lead_emails_per_month",
            }.get(reached.metric, reached.metric)
        needed = plan_for_limit(
            self.entitlements.catalog.current(), name, reached.limit + 1
        )
        if audience == "candidate":
            message = f"Your organization's limit of {rule} has been reached. It resets automatically."
            needed = None
        else:
            catalog = self.entitlements.catalog
            message = f"The {catalog.name(entitlements.plan_id)} plan allows {rule}, and it has been reached."
            if needed and needed != entitlements.plan_id:
                message += f" Upgrade to {catalog.name(needed)} for a higher limit."
        return PlanRequired(
            message,
            limit=name,
            used=reached.used,
            limit_value=reached.limit,
            scope=reached.scope,
            required_plan=needed,
            current_plan=entitlements.plan_id,
            audience=audience,
        )
