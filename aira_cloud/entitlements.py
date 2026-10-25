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
import threading
from dataclasses import dataclass
from typing import Any

from aira_cloud.config import CloudConfig, Clock
from aira_cloud.db import ControlDB, dumps, loads, many, one
from aira import apps as app_catalog
from aira_cloud.errors import (
    AppRequired,
    NotFound,
    PlanRequired,
    SubscriptionInactive,
    ValidationFailed,
)
from aira_cloud.plans import (
    DEFAULT_PLANS,
    STATUS_RESTRICTIONS,
    Entitlements,
    app_state,
    day_key,
    merge_limits,
    month_key,
    plan_for_app,
    plan_for_feature,
    plan_for_limit,
    with_later_limits,
)

STAFF_LIMIT_ROLES = ("owner", "admin", "coach", "billing", "auditor")


# --------------------------------------------------------------------------- #
# Plan catalog
# --------------------------------------------------------------------------- #


class PlanCatalog:
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
                connection.execute(
                    "INSERT INTO plans (plan_id, name, version, entitlements, pricing, sort_order, public, active, updated_at) VALUES (?, ?, 1, ?, ?, ?, ?, 1, ?)",
                    (
                        plan_id,
                        plan["name"],
                        dumps(plan["entitlements"]),
                        dumps(plan["pricing"]),
                        plan["sort_order"],
                        int(plan["public"]),
                        self.clock.now(),
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

    def current(self) -> dict[str, dict[str, Any]]:
        with self.db.read() as connection:
            rows = many(
                connection, "SELECT * FROM plans WHERE active = 1 ORDER BY sort_order"
            )
        return {
            row["plan_id"]: {
                "plan_id": row["plan_id"],
                "name": row["name"],
                "version": row["version"],
                "entitlements": loads(row["entitlements"], {}),
                "pricing": loads(row["pricing"], {}),
                "sort_order": row["sort_order"],
                "public": bool(row["public"]),
            }
            for row in rows
        }

    def get(self, plan_id: str, version: int | None = None) -> dict[str, Any]:
        with self.db.read() as connection:
            if version is None:
                row = one(
                    connection,
                    "SELECT plan_id, name, version, entitlements, pricing FROM plans WHERE plan_id = ?",
                    (plan_id,),
                )
            else:
                row = one(
                    connection,
                    """SELECT v.plan_id, p.name, v.version, v.entitlements, v.pricing FROM plan_versions v
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
        }

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
        new_entitlements = {**current["entitlements"], **(entitlements or {})}
        new_pricing = {**current["pricing"], **(pricing or {})}
        for name in (
            "candidate_seats",
            "staff_seats",
            "apps_per_candidate_day",
            "apps_per_org_month",
        ):
            value = new_entitlements.get(name)
            if value is not None and (not isinstance(value, int) or value < 0):
                raise ValidationFailed(
                    f"{name} must be a non-negative whole number or empty."
                )
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
    ) -> None:
        self.db = db
        self.clock = clock
        self.config = config
        self.catalog = catalog
        self.flags = flags
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
                "SELECT app_id, status FROM org_apps WHERE org_id = ? AND status IN ('active', 'granted')",
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
        apps = self._apps_for(sub["plan_id"], features, {r["app_id"] for r in app_rows})
        for app_id in apps:  # an app that is on carries its feature: the feature checks below keep working
            feature = app_catalog.BY_ID[app_id].feature
            if feature:
                features.add(feature)
        for app in (
            app_catalog.APPS
        ):  # an app that is off takes its feature away, even from an old plan version
            if app.feature and app.id not in apps:
                features.discard(app.feature)
        entitlements = Entitlements(
            org_id=org_id,
            plan_id=sub["plan_id"],
            plan_version=sub["plan_version"],
            status=status,
            limits=limits,
            features=frozenset(features),
            apps=frozenset(apps),
            restrictions=STATUS_RESTRICTIONS.get(status, frozenset()),
            period_start=sub["period_start"],
            period_end=sub["period_end"],
            trial_end=sub["trial_end"],
            suspended=org["status"] in {"suspended", "deleted"},
            over_limits=tuple(loads(sub["over_limits"], [])),
            overage_enabled=bool(sub["overage_enabled"]) and "overage" in features,
            pricing=plan["pricing"],
        )
        with self._lock:
            self._cache[org_id] = (
                now + self.config.entitlement_cache_seconds,
                entitlements,
            )
        return entitlements

    @staticmethod
    def _apps_for(
        plan_id: str, features: builtins.set[str], subscribed: builtins.set[str]
    ) -> builtins.set[str]:
        """The apps an organization may open: included by the plan, bought as add-ons, or granted by an operator."""
        apps: builtins.set[str] = set()
        for app in app_catalog.APPS:
            state = app_state(plan_id, app.id)
            if state == "included":
                apps.add(app.id)
            elif app.id in subscribed:
                apps.add(
                    app.id
                )  # bought (active) or granted; an unavailable app can still be granted
        return apps

    # -- checks ----------------------------------------------------------------

    def required_plan(self, feature: str) -> str | None:
        return plan_for_feature(self.catalog.current(), feature)

    def require_app(
        self, org_id: str, app_id: str, *, audience: str = "staff"
    ) -> Entitlements:
        """The organization must have the app that owns the route, or the call is ``402 app_required``."""
        entitlements = self.resolve(org_id)
        if app_id not in app_catalog.BY_ID or entitlements.has_app(app_id):
            return entitlements
        app = app_catalog.BY_ID[app_id]
        state = app_state(entitlements.plan_id, app_id)
        needed = plan_for_app(app_id)
        if audience == "candidate":
            message = f"{app.name} is not enabled for your organization."
            hint = "Ask an owner or administrator to turn it on."
        elif state == "addon":
            message = f"{app.name} is not turned on for your organization."
            hint = f"Turn it on under Apps (billed per candidate seat on your {entitlements.plan_id.title()} plan)."
        elif needed:
            message = f"{app.name} is not available on the {entitlements.plan_id.title()} plan."
            hint = f"Upgrade to {needed.title()} and turn it on under Apps."
        else:
            message = f"{app.name} is not available on the {entitlements.plan_id.title()} plan."
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
            message = f"{name} is not part of your {entitlements.plan_id.title()} plan. Upgrade to {needed.title()} to use it."
        else:
            message = f"{name} is not part of your {entitlements.plan_id.title()} plan."
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


class LimitReached(Exception):
    def __init__(self, metric: str, scope: str, used: int, limit: int) -> None:
        super().__init__(f"{metric} limit reached ({scope}).")
        self.metric, self.scope, self.used, self.limit = metric, scope, used, limit


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
        reservation = Reservation(org_id, metric, amount, period, day, candidate_id)
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
            if candidate_id:
                connection.execute(
                    "INSERT INTO usage_daily (org_id, candidate_id, day, metric, value) VALUES (?, ?, ?, ?, ?) ON CONFLICT(org_id, candidate_id, day, metric) DO UPDATE SET value = usage_daily.value + excluded.value",
                    (org_id, candidate_id, day_key(now), metric, amount),
                )
        return over

    def set_gauge(self, org_id: str, metric: str, value: int) -> None:
        with self.db.write() as connection:
            connection.execute(
                "INSERT INTO usage_counters (org_id, period, metric, value) VALUES (?, ?, ?, ?) ON CONFLICT(org_id, period, metric) DO UPDATE SET value = excluded.value",
                (org_id, month_key(self.clock.now()), metric, value),
            )

    def delete_org(self, org_id: str) -> None:
        with self.db.write() as connection:
            connection.execute("DELETE FROM usage_counters WHERE org_id = ?", (org_id,))
            connection.execute("DELETE FROM usage_daily WHERE org_id = ?", (org_id,))


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
            message = f"The {entitlements.plan_id.title()} plan allows {rule}, and it has been reached."
            if needed and needed != entitlements.plan_id:
                message += f" Upgrade to {needed.title()} for a higher limit."
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
