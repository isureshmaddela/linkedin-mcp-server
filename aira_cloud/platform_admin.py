"""The platform (vendor) console: tenants, plans, flags, support access, announcements, health and abuse controls.

Operators sign in separately, with MFA, and never through a tenant's login. They touch tenant data
only through the support-access flow: the organization's owner approves a time-boxed grant (at most
24 hours), the tenant sees a banner while it is active, and every step is recorded in both the
tenant's audit log and the platform's own (kept 7 years).
"""

from __future__ import annotations

import logging
import statistics
from typing import TYPE_CHECKING, Any

from aira.apps import LEGACY_IDS, current_id
from aira_cloud.config import DAY
from aira_cloud.db import dumps, loads, many, new_id, one, platform_scope
from aira_cloud.errors import Conflict, Forbidden, NotFound, ValidationFailed
from aira_cloud.rbac import ADMIN, AUDITOR, COACH, OWNER, Principal

if TYPE_CHECKING:
    from aira_cloud.app import CloudApp

logger = logging.getLogger("aira_cloud.platform")

SUPPORT_MAX_SECONDS = 24 * 3600
SUPPORT_ROLES = (
    AUDITOR,
    ADMIN,
)  # read-only, or with administrator rights; never owner, billing or candidate
SUPPORT_REQUEST_TTL = 7 * DAY
ERROR_BUDGET = (
    0.25  # share of failed runs per tenant over 24 hours before it is flagged
)
SUCCESS_STATUSES = {"submitted", "filled", "ok"}


class PlatformAdmin:
    def __init__(self, app: CloudApp) -> None:
        self.app = app

    @property
    def db(self):  # noqa: ANN201
        return self.app.db

    @property
    def now(self) -> int:
        return self.app.clock.now()

    # ------------------------------------------------------------------ tenants

    def list_tenants(
        self,
        operator: Principal,
        *,
        q: str = "",
        status: str = "",
        plan: str = "",
        limit: int = 100,
        offset: int = 0,
    ) -> list[dict[str, Any]]:
        self.app.authz.require(operator, "platform.tenants.read")
        clauses, params = ["o.deleted_at IS NULL"], []
        if q:
            clauses.append("(o.name LIKE ? OR o.slug LIKE ?)")
            params.extend([f"%{q}%"] * 2)
        if status:
            clauses.append("s.status = ?")
            params.append(status)
        if plan:
            clauses.append("s.plan_id = ?")
            params.append(plan)
        with self.db.read() as connection:
            rows = many(
                connection,
                f"""SELECT o.org_id, o.name, o.slug, o.region, o.status AS org_status, o.paused_at, o.rate_limited_until, o.created_at,
                s.plan_id, s.status, s.candidate_seats, s.period_end, s.over_limits, s.past_due_since, s.deletion_scheduled_at
                FROM organizations o JOIN subscriptions s ON s.org_id = o.org_id WHERE {" AND ".join(clauses)}
                ORDER BY o.created_at DESC LIMIT ? OFFSET ?""",
                (*params, max(1, min(limit, 500)), max(0, offset)),
            )
        return [self._tenant_row(row) for row in rows]

    def _tenant_row(self, row: dict[str, Any]) -> dict[str, Any]:
        org_id = row["org_id"]
        seats = self.app.tenancy.seat_usage(org_id)
        usage = self.app.usage.snapshot(org_id)
        outcomes = self._org_error_rate(org_id)
        flags = []
        if row["org_status"] == "suspended":
            flags.append("suspended")
        if row["paused_at"]:
            flags.append("paused")
        if row["status"] in {"past_due", "read_only", "cancelled"}:
            flags.append(row["status"])
        if loads(row["over_limits"], []):
            flags.append("over_limits")
        if row["rate_limited_until"] and row["rate_limited_until"] > self.now:
            flags.append("rate_limited")
        if row["deletion_scheduled_at"]:
            flags.append("deletion_scheduled")
        if outcomes["over_budget"]:
            flags.append("error_budget")
        return {
            "org_id": org_id,
            "name": row["name"],
            "slug": row["slug"],
            "region": row["region"],
            "plan_id": row["plan_id"],
            "status": row["status"],
            "org_status": row["org_status"],
            "seats": {
                "candidates": seats["candidates"],
                "staff": seats["staff"],
                "purchased": row["candidate_seats"],
            },
            "applications_this_month": usage.get("applications", 0),
            "period_end": row["period_end"],
            "created_at": row["created_at"],
            "error_rate_24h": outcomes["rate"],
            "flags": flags,
        }

    def tenant_detail(self, operator: Principal, org_id: str) -> dict[str, Any]:
        self.app.authz.require(operator, "platform.tenants.read")
        org = self.app.tenancy.org(org_id)
        with self.db.read() as connection:
            row = one(
                connection,
                """SELECT o.org_id, o.name, o.slug, o.region, o.status AS org_status, o.paused_at, o.rate_limited_until, o.created_at,
                s.plan_id, s.status, s.candidate_seats, s.period_end, s.over_limits, s.past_due_since, s.deletion_scheduled_at
                FROM organizations o JOIN subscriptions s ON s.org_id = o.org_id WHERE o.org_id = ?""",
                (org_id,),
            )
            grants = many(
                connection,
                "SELECT * FROM support_access WHERE org_id = ? ORDER BY requested_at DESC LIMIT 20",
                (org_id,),
            )
        if row is None:
            raise NotFound("Organization not found.")
        return {
            **self._tenant_row(row),
            "policy": org["policy"],
            "billing": self.app.billing.summary(org_id),
            "usage": self.app.usage.snapshot(org_id),
            "support_access": grants,
            "operator_audit": self.app.platform_audit.list(org_id=org_id, limit=100),
            "queue": next(
                (r for r in self.app.queue.stats()["by_org"] if r["org_id"] == org_id),
                {"queued": 0, "running": 0},
            ),
        }

    def suspend(self, operator: Principal, org_id: str, reason: str) -> None:
        self.app.authz.require(operator, "platform.tenants.manage")
        if not reason.strip():
            raise ValidationFailed("Give a reason.")
        org = self.app.tenancy.org(org_id)
        with self.db.write() as connection:
            connection.execute(
                "UPDATE organizations SET status = 'suspended', suspended_reason = ? WHERE org_id = ?",
                (reason[:300], org_id),
            )
            connection.execute(
                "UPDATE sessions SET revoked_at = ? WHERE org_id = ? AND revoked_at IS NULL AND kind = 'member'",
                (self.now, org_id),
            )
            self.app.platform_audit.record(
                "tenant.suspend",
                operator=operator,
                org_id=org_id,
                detail={"reason": reason[:300]},
                connection=connection,
            )
            self.app.audit.record(
                org_id,
                "org.suspended",
                actor=operator,
                detail={"reason": reason[:300]},
                connection=connection,
            )
        self.app.entitlements.invalidate(org_id)
        del org

    def reinstate(self, operator: Principal, org_id: str) -> None:
        self.app.authz.require(operator, "platform.tenants.manage")
        self.app.tenancy.org(org_id)
        with self.db.write() as connection:
            connection.execute(
                "UPDATE organizations SET status = 'active', suspended_reason = '' WHERE org_id = ?",
                (org_id,),
            )
            self.app.platform_audit.record(
                "tenant.reinstate",
                operator=operator,
                org_id=org_id,
                connection=connection,
            )
            self.app.audit.record(
                org_id, "org.reinstated", actor=operator, connection=connection
            )
        self.app.entitlements.invalidate(org_id)

    def schedule_deletion(
        self, operator: Principal, org_id: str, days: int | None = None
    ) -> int:
        self.app.authz.require(operator, "platform.tenants.manage")
        self.app.tenancy.org(org_id)
        at = (
            self.now
            + int(days if days is not None else self.app.config.deletion_grace_days)
            * DAY
        )
        with self.db.write() as connection:
            connection.execute(
                "UPDATE subscriptions SET deletion_scheduled_at = ?, updated_at = ? WHERE org_id = ?",
                (at, self.now, org_id),
            )
            self.app.platform_audit.record(
                "tenant.schedule_deletion",
                operator=operator,
                org_id=org_id,
                detail={"deletion_at": at},
                connection=connection,
            )
            self.app.audit.record(
                org_id,
                "org.deletion_scheduled",
                actor=operator,
                detail={"deletion_at": at},
                connection=connection,
            )
        self.app.entitlements.invalidate(org_id)
        return at

    def cancel_deletion(self, operator: Principal, org_id: str) -> None:
        self.app.authz.require(operator, "platform.tenants.manage")
        with self.db.write() as connection:
            connection.execute(
                "UPDATE subscriptions SET deletion_scheduled_at = NULL, updated_at = ? WHERE org_id = ?",
                (self.now, org_id),
            )
            self.app.platform_audit.record(
                "tenant.cancel_deletion",
                operator=operator,
                org_id=org_id,
                connection=connection,
            )
        self.app.entitlements.invalidate(org_id)

    # ------------------------------------------------------------------ plans and flags

    def list_plans(self, operator: Principal) -> list[dict[str, Any]]:
        self.app.authz.require(operator, "platform.tenants.read")
        plans = list(self.app.catalog.current().values())
        with self.db.read() as connection:
            for plan in plans:
                row = one(
                    connection,
                    "SELECT COUNT(*) AS n FROM subscriptions WHERE plan_id = ? AND status != 'deleted'",
                    (plan["plan_id"],),
                )
                plan["subscriptions"] = int(row["n"]) if row else 0
                plan["migrations"] = many(
                    connection,
                    "SELECT * FROM plan_migrations WHERE plan_id = ? ORDER BY id DESC LIMIT 5",
                    (plan["plan_id"],),
                )
        return plans

    def update_plan(
        self,
        operator: Principal,
        plan_id: str,
        *,
        entitlements: dict[str, Any] | None = None,
        pricing: dict[str, Any] | None = None,
    ) -> int:
        """Publish a new plan version: new subscriptions use it, existing ones keep theirs until migrated."""
        self.app.authz.require(operator, "platform.plans.manage")
        version = self.app.catalog.update(
            plan_id, entitlements=entitlements, pricing=pricing, by=operator.operator_id
        )
        self.app.platform_audit.record(
            "plan.update",
            operator=operator,
            target_type="plan",
            target_id=plan_id,
            detail={
                "version": version,
                "entitlements": sorted((entitlements or {}).keys()),
                "pricing": sorted((pricing or {}).keys()),
            },
        )
        return version

    def schedule_migration(
        self, operator: Principal, plan_id: str, to_version: int, effective_at: int
    ) -> int:
        self.app.authz.require(operator, "platform.plans.manage")
        migration = self.app.catalog.schedule_migration(
            plan_id, int(to_version), int(effective_at), operator.operator_id
        )
        self.app.platform_audit.record(
            "plan.migration_scheduled",
            operator=operator,
            target_type="plan",
            target_id=plan_id,
            detail={"to_version": int(to_version), "effective_at": int(effective_at)},
        )
        return migration

    def set_flag(
        self,
        operator: Principal,
        flag: str,
        scope_type: str,
        scope_id: str,
        enabled: bool,
        percent: int = 100,
    ) -> None:
        self.app.authz.require(operator, "platform.flags.manage")
        self.app.flags.set(
            flag,
            scope_type,
            scope_id,
            enabled,
            percent=percent,
            by=operator.operator_id,
        )
        self.app.entitlements.invalidate()
        self.app.platform_audit.record(
            "flag.set",
            operator=operator,
            target_type="flag",
            target_id=flag,
            detail={
                "scope": scope_type,
                "scope_id": scope_id,
                "enabled": enabled,
                "percent": percent,
            },
        )

    def delete_flag(
        self, operator: Principal, flag: str, scope_type: str, scope_id: str = ""
    ) -> None:
        self.app.authz.require(operator, "platform.flags.manage")
        if not self.app.flags.delete(flag, scope_type, scope_id):
            raise NotFound("Flag not found.")
        self.app.entitlements.invalidate()
        self.app.platform_audit.record(
            "flag.delete",
            operator=operator,
            target_type="flag",
            target_id=flag,
            detail={"scope": scope_type, "scope_id": scope_id},
        )

    # ------------------------------------------------------------------ app catalog

    def list_apps(self, operator: Principal) -> list[dict[str, Any]]:
        """Every app with its platform-wide switch and prices, and how many organizations pay for it or were granted it."""
        self.app.authz.require(operator, "platform.tenants.read")
        apps = self.app.app_settings.describe()
        with platform_scope(), self.db.read() as connection:
            counts: dict[tuple[str, str], int] = {
                (r["app_id"], r["status"]): int(r["n"])
                for r in many(
                    connection,
                    "SELECT app_id, status, COUNT(*) AS n FROM org_apps WHERE status IN ('active', 'granted') GROUP BY app_id, status",
                )
            }
        for app in apps:
            old_ids = [app["id"], *(k for k, v in LEGACY_IDS.items() if v == app["id"])]
            app["paying"] = sum(counts.get((i, "active"), 0) for i in old_ids)
            app["granted"] = sum(counts.get((i, "granted"), 0) for i in old_ids)
        return apps

    def update_app(
        self,
        operator: Principal,
        app_id: str,
        *,
        enabled: bool | None = None,
        prices: dict[str, Any] | None = None,
        note: str = "",
    ) -> dict[str, Any]:
        """Switch an app on or off for every organization and/or change its seat price per plan."""
        self.app.authz.require(operator, "platform.plans.manage")
        before = next(
            (
                a
                for a in self.app.app_settings.describe()
                if a["id"] == current_id(app_id)
            ),
            None,
        )
        app = self.app.app_settings.update(
            app_id, enabled=enabled, prices=prices, note=note, by=operator.operator_id
        )
        billing = self.app.billing.resync_app(app["id"])
        self.app.platform_audit.record(
            "app.settings_update",
            operator=operator,
            target_type="app",
            target_id=app["id"],
            detail={
                "enabled": app["enabled"],
                "was_enabled": before["enabled"] if before else True,
                "prices": app["prices"],
                "previous_prices": before["prices"] if before else {},
                "note": note[:300],
                "billing": billing,
            },
        )
        return {**app, "billing": billing}

    def reset_app(self, operator: Principal, app_id: str) -> dict[str, Any]:
        """Back to the catalog's defaults: on for everyone, with the standard prices."""
        self.app.authz.require(operator, "platform.plans.manage")
        if not self.app.app_settings.reset(app_id):
            raise NotFound("That app already uses the defaults.")
        app = next(
            a for a in self.app.app_settings.describe() if a["id"] == current_id(app_id)
        )
        billing = self.app.billing.resync_app(app["id"])
        self.app.platform_audit.record(
            "app.settings_reset",
            operator=operator,
            target_type="app",
            target_id=app["id"],
            detail={"billing": billing},
        )
        return {**app, "billing": billing}

    # ------------------------------------------------------------------ support access

    def request_support(
        self, operator: Principal, org_id: str, reason: str, role: str = AUDITOR
    ) -> dict[str, Any]:
        self.app.authz.require(operator, "platform.support.request")
        org = self.app.tenancy.org(org_id)
        if not reason.strip():
            raise ValidationFailed("Give a reason; the organization owner sees it.")
        if role not in SUPPORT_ROLES:
            raise ValidationFailed(
                "Support access is read-only (auditor) or with administrator rights (admin)."
            )
        access_id = new_id()
        with self.db.write() as connection:
            connection.execute(
                "UPDATE support_access SET status = 'cancelled', ended_at = ?, ended_reason = 'replaced' WHERE org_id = ? AND operator_id = ? AND status = 'requested'",
                (self.now, org_id, operator.operator_id),
            )
            connection.execute(
                "INSERT INTO support_access (access_id, org_id, operator_id, reason, role, status, requested_at) VALUES (?, ?, ?, ?, ?, 'requested', ?)",
                (access_id, org_id, operator.operator_id, reason[:400], role, self.now),
            )
            self.app.platform_audit.record(
                "support.request",
                operator=operator,
                org_id=org_id,
                target_type="support_access",
                target_id=access_id,
                detail={"reason": reason[:400], "role": role},
                connection=connection,
            )
            self.app.audit.record(
                org_id,
                "support.request",
                actor=operator,
                target_type="support_access",
                target_id=access_id,
                detail={"reason": reason[:400], "role": role},
                connection=connection,
            )
        with self.db.read() as connection:
            owners = many(
                connection,
                "SELECT email FROM members WHERE org_id = ? AND role = 'owner' AND removed_at IS NULL AND status = 'active'",
                (org_id,),
            )
        for owner in owners:
            self.app.mail.send(
                org_id,
                owner["email"],
                "support_request",
                operator=operator.name or operator.email,
                org=org["name"],
                reason=reason[:400],
                link=f"{self.app.config.base_url}/#/org/support",
            )
        self.app.billing.notice(
            org_id,
            "warning",
            "Monosage support asks for access",
            reason[:200],
            "#/org/support",
        )
        return {"access_id": access_id, "status": "requested"}

    def list_support_requests(self, actor: Principal) -> list[dict[str, Any]]:
        self.app.authz.require(actor, "support.approve")
        # the owner sees which operator asked: operator names live in a platform-only table, so this one read
        # (filtered to the caller's own organization) runs with the platform role
        with platform_scope(), self.db.read() as connection:
            rows = many(
                connection,
                """SELECT a.access_id, a.reason, a.role, a.status, a.requested_at, a.decided_at, a.started_at, a.expires_at, a.ended_at, o.name AS operator_name, o.email AS operator_email
                FROM support_access a JOIN operators o ON o.operator_id = a.operator_id WHERE a.org_id = ? ORDER BY a.requested_at DESC LIMIT 50""",
                (actor.org_id,),
            )
        return rows

    def decide_support(
        self, owner: Principal, access_id: str, approve: bool, hours: int = 24
    ) -> dict[str, Any]:
        """The organization owner approves (for up to 24 hours) or denies a request."""
        self.app.authz.require(owner, "support.approve")
        hours = max(1, min(int(hours), SUPPORT_MAX_SECONDS // 3600))
        with self.db.write() as connection:
            grant = one(
                connection,
                "SELECT * FROM support_access WHERE access_id = ? AND org_id = ?",
                (access_id, owner.org_id),
            )
            if grant is None:
                raise NotFound("Request not found.")
            if (
                grant["status"] != "requested"
                or grant["requested_at"] + SUPPORT_REQUEST_TTL < self.now
            ):
                raise Conflict("This request is no longer open.")
            if approve:
                connection.execute(
                    "UPDATE support_access SET status = 'approved', decided_at = ?, decided_by = ?, started_at = ?, expires_at = ? WHERE access_id = ?",
                    (
                        self.now,
                        owner.actor_id,
                        self.now,
                        self.now + hours * 3600,
                        access_id,
                    ),
                )
            else:
                connection.execute(
                    "UPDATE support_access SET status = 'denied', decided_at = ?, decided_by = ?, ended_at = ? WHERE access_id = ?",
                    (self.now, owner.actor_id, self.now, access_id),
                )
            self.app.audit.record(
                owner.org_id,
                "support.approve" if approve else "support.deny",
                actor=owner,
                target_type="support_access",
                target_id=access_id,
                detail={"hours": hours if approve else 0},
                connection=connection,
            )
            self.app.platform_audit.record(
                "support.approved" if approve else "support.denied",
                operator_label=f"owner:{owner.email}",
                org_id=owner.org_id,
                target_type="support_access",
                target_id=access_id,
                connection=connection,
            )
        return {
            "access_id": access_id,
            "status": "approved" if approve else "denied",
            "expires_at": self.now + hours * 3600 if approve else None,
        }

    def revoke_support(
        self, actor: Principal, access_id: str, reason: str = "revoked"
    ) -> None:
        """The owner ends a grant early, or the operator leaves."""
        with self.db.write() as connection:
            grant = one(
                connection,
                "SELECT * FROM support_access WHERE access_id = ?",
                (access_id,),
            )
            if grant is None:
                raise NotFound("Grant not found.")
            if actor.is_operator:
                if grant["operator_id"] != actor.operator_id:
                    raise Forbidden("This grant belongs to another operator.")
            else:
                self.app.authz.require(actor, "support.approve")
                if grant["org_id"] != actor.org_id:
                    raise NotFound("Grant not found.")
            if grant["status"] not in {"approved", "requested"} or grant["ended_at"]:
                raise Conflict("This grant is not active.")
            connection.execute(
                "UPDATE support_access SET status = 'revoked', ended_at = ?, ended_reason = ? WHERE access_id = ?",
                (self.now, reason[:200], access_id),
            )
            connection.execute(
                "UPDATE sessions SET support_access_id = '' WHERE support_access_id = ?",
                (access_id,),
            )
            self.app.audit.record(
                grant["org_id"],
                "support.end",
                actor=actor,
                target_type="support_access",
                target_id=access_id,
                detail={"reason": reason[:200]},
                connection=connection,
            )
            self.app.platform_audit.record(
                "support.end",
                operator=actor if actor.is_operator else None,
                operator_label="" if actor.is_operator else f"owner:{actor.email}",
                org_id=grant["org_id"],
                target_type="support_access",
                target_id=access_id,
                connection=connection,
            )

    def enter_support(self, operator: Principal, access_id: str) -> dict[str, Any]:
        """Switch the operator's session into the tenant, with the granted role, until the grant expires."""
        self.app.authz.require(operator, "platform.support.request")
        with self.db.write() as connection:
            grant = one(
                connection,
                "SELECT * FROM support_access WHERE access_id = ? AND operator_id = ? AND status = 'approved' AND expires_at > ? AND ended_at IS NULL",
                (access_id, operator.operator_id, self.now),
            )
            if grant is None:
                raise Forbidden(
                    "There is no approved, unexpired access for you.",
                    code="no_support_grant",
                )
            connection.execute(
                "UPDATE sessions SET support_access_id = ? WHERE session_id = ?",
                (access_id, operator.session_id),
            )
            self.app.audit.record(
                grant["org_id"],
                "support.enter",
                actor=operator,
                target_type="support_access",
                target_id=access_id,
                detail={"role": grant["role"]},
                connection=connection,
            )
            self.app.platform_audit.record(
                "support.enter",
                operator=operator,
                org_id=grant["org_id"],
                target_type="support_access",
                target_id=access_id,
                connection=connection,
            )
        return {
            "org_id": grant["org_id"],
            "role": grant["role"],
            "expires_at": grant["expires_at"],
        }

    def leave_support(self, operator: Principal) -> None:
        with self.db.write() as connection:
            connection.execute(
                "UPDATE sessions SET support_access_id = '' WHERE session_id = ?",
                (operator.session_id,),
            )
            if operator.support_access_id:
                self.app.audit.record(
                    operator.org_id,
                    "support.leave",
                    actor=operator,
                    target_type="support_access",
                    target_id=operator.support_access_id,
                    connection=connection,
                )

    def expire_support_access(self) -> int:
        with self.db.write() as connection:
            expired = many(
                connection,
                "SELECT access_id, org_id FROM support_access WHERE status = 'approved' AND expires_at <= ? AND ended_at IS NULL",
                (self.now,),
            )
            for grant in expired:
                connection.execute(
                    "UPDATE support_access SET status = 'expired', ended_at = ?, ended_reason = 'expired' WHERE access_id = ?",
                    (self.now, grant["access_id"]),
                )
                connection.execute(
                    "UPDATE sessions SET support_access_id = '' WHERE support_access_id = ?",
                    (grant["access_id"],),
                )
                self.app.audit.record(
                    grant["org_id"],
                    "support.expired",
                    actor_type="system",
                    target_type="support_access",
                    target_id=grant["access_id"],
                    source="scheduler",
                    connection=connection,
                )
            connection.execute(
                "UPDATE support_access SET status = 'expired', ended_at = ?, ended_reason = 'request expired' WHERE status = 'requested' AND requested_at + ? < ?",
                (self.now, SUPPORT_REQUEST_TTL, self.now),
            )
        return len(expired)

    # ------------------------------------------------------------------ announcements

    def announce(
        self,
        operator: Principal,
        title: str,
        body: str = "",
        *,
        scope_type: str = "all",
        scope_id: str = "",
        severity: str = "info",
        hours: int | None = None,
    ) -> str:
        self.app.authz.require(operator, "platform.announcements.manage")
        if scope_type not in {"all", "plan", "org"}:
            raise ValidationFailed("Scope must be all, plan or org.")
        if severity not in {"info", "warning", "danger"}:
            raise ValidationFailed("Severity must be info, warning or danger.")
        if not title.strip():
            raise ValidationFailed("Enter a title.")
        announcement_id = new_id()
        with self.db.write() as connection:
            connection.execute(
                "INSERT INTO announcements (announcement_id, scope_type, scope_id, severity, title, body, starts_at, ends_at, created_by, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    announcement_id,
                    scope_type,
                    scope_id if scope_type != "all" else "",
                    severity,
                    title[:160],
                    body[:1000],
                    self.now,
                    self.now + hours * 3600 if hours else None,
                    operator.operator_id,
                    self.now,
                ),
            )
            self.app.platform_audit.record(
                "announcement.create",
                operator=operator,
                target_type="announcement",
                target_id=announcement_id,
                detail={
                    "scope": scope_type,
                    "scope_id": scope_id,
                    "title": title[:160],
                },
                connection=connection,
            )
        return announcement_id

    def cancel_announcement(self, operator: Principal, announcement_id: str) -> None:
        self.app.authz.require(operator, "platform.announcements.manage")
        with self.db.write() as connection:
            if not connection.execute(
                "UPDATE announcements SET cancelled_at = ? WHERE announcement_id = ? AND cancelled_at IS NULL",
                (self.now, announcement_id),
            ).rowcount:
                raise NotFound("Announcement not found.")
            self.app.platform_audit.record(
                "announcement.cancel",
                operator=operator,
                target_type="announcement",
                target_id=announcement_id,
                connection=connection,
            )

    def list_announcements(self, operator: Principal) -> list[dict[str, Any]]:
        self.app.authz.require(operator, "platform.announcements.manage")
        with self.db.read() as connection:
            return many(
                connection,
                "SELECT * FROM announcements ORDER BY created_at DESC LIMIT 100",
            )

    # ------------------------------------------------------------------ health

    def _org_error_rate(self, org_id: str) -> dict[str, Any]:
        with self.db.read() as connection:
            row = one(
                connection,
                """SELECT COUNT(*) AS total, SUM(CASE WHEN status IN ('failed', 'error') THEN 1 ELSE 0 END) AS errors FROM run_outcomes
                WHERE org_id = ? AND at >= ? AND kind IN ('apply', 'search')""",
                (org_id, self.now - DAY),
            )
        total = int(row["total"] or 0) if row else 0
        errors = int(row["errors"] or 0) if row else 0
        rate = round(errors / total, 3) if total else 0.0
        return {
            "total": total,
            "errors": errors,
            "rate": rate,
            "over_budget": total >= 10 and rate > ERROR_BUDGET,
        }

    def health(self, operator: Principal) -> dict[str, Any]:
        self.app.authz.require(operator, "platform.health.read")
        since = self.now - DAY
        queue = self.app.queue.stats()
        with self.db.read() as connection:
            ats = many(
                connection,
                """SELECT ats, COUNT(*) AS runs, SUM(CASE WHEN status IN ('submitted', 'filled') THEN 1 ELSE 0 END) AS ok FROM run_outcomes
                WHERE kind = 'apply' AND at >= ? AND ats != '' GROUP BY ats ORDER BY runs DESC""",
                (since,),
            )
            sources = many(
                connection,
                """SELECT source, COUNT(*) AS calls, SUM(CASE WHEN status = 'error' THEN 1 ELSE 0 END) AS errors FROM run_outcomes
                WHERE kind = 'search' AND at >= ? AND source != '' GROUP BY source""",
                (since,),
            )
            latencies = [
                r["duration_ms"]
                for r in many(
                    connection,
                    "SELECT duration_ms FROM run_outcomes WHERE kind = 'model' AND at >= ? ORDER BY duration_ms",
                    (since,),
                )
            ]
            orgs = many(
                connection,
                "SELECT DISTINCT org_id FROM run_outcomes WHERE at >= ?",
                (since,),
            )
            blocked = many(
                connection, "SELECT source, reason, blocked_at FROM blocked_sources"
            )
        budgets = []
        for org in orgs:
            if not org["org_id"]:
                continue
            state = self._org_error_rate(org["org_id"])
            if state["total"]:
                budgets.append({"org_id": org["org_id"], **state})
        capacity, busy = queue["worker_capacity"], queue["worker_busy"]
        return {
            "queue": {
                "depth": queue["depth"],
                "running": queue["running"],
                "oldest_wait_seconds": queue["oldest_wait_seconds"],
                "by_kind": queue["by_kind"],
            },
            "workers": {
                "count": len(queue["workers"]),
                "capacity": capacity,
                "busy": busy,
                "utilisation": round(busy / capacity, 2) if capacity else None,
            },
            "apply_success_by_ats": [
                {
                    "ats": r["ats"],
                    "runs": r["runs"],
                    "success_rate": round((r["ok"] or 0) / r["runs"], 3),
                }
                for r in ats
            ],
            "source_error_rates": [
                {
                    "source": r["source"],
                    "calls": r["calls"],
                    "error_rate": round((r["errors"] or 0) / r["calls"], 3),
                }
                for r in sources
            ],
            "model_latency_ms": {
                "calls": len(latencies),
                "p50": int(statistics.median(latencies)) if latencies else None,
                "p95": int(
                    latencies[min(len(latencies) - 1, int(len(latencies) * 0.95))]
                )
                if latencies
                else None,
            },
            "tenant_error_budgets": sorted(budgets, key=lambda b: -b["rate"])[:20],
            "blocked_sources": blocked,
        }

    # ------------------------------------------------------------------ abuse and compliance

    def rate_limit_tenant(
        self, operator: Principal, org_id: str, per_minute: int, hours: int = 24
    ) -> None:
        self.app.authz.require(operator, "platform.abuse.manage")
        self.app.tenancy.org(org_id)
        if not 0 <= int(per_minute) <= 10_000:
            raise ValidationFailed("Requests per minute must be between 0 and 10,000.")
        with self.db.write() as connection:
            connection.execute(
                "UPDATE organizations SET rate_limit_per_minute = ?, rate_limited_until = ? WHERE org_id = ?",
                (int(per_minute), self.now + int(hours) * 3600, org_id),
            )
            self.app.platform_audit.record(
                "tenant.rate_limit",
                operator=operator,
                org_id=org_id,
                detail={"per_minute": int(per_minute), "hours": int(hours)},
                connection=connection,
            )

    def clear_rate_limit(self, operator: Principal, org_id: str) -> None:
        self.app.authz.require(operator, "platform.abuse.manage")
        with self.db.write() as connection:
            connection.execute(
                "UPDATE organizations SET rate_limit_per_minute = NULL, rate_limited_until = NULL WHERE org_id = ?",
                (org_id,),
            )
            self.app.platform_audit.record(
                "tenant.rate_limit_clear",
                operator=operator,
                org_id=org_id,
                connection=connection,
            )

    def block_source(self, operator: Principal, source: str, reason: str) -> None:
        """Switch a source off for every tenant (for example when an employer system changes its form)."""
        self.app.authz.require(operator, "platform.abuse.manage")
        if not source or len(source) > 40:
            raise ValidationFailed("Enter a source name.")
        with self.db.write() as connection:
            connection.execute(
                "INSERT INTO blocked_sources (source, reason, blocked_by, blocked_at) VALUES (?, ?, ?, ?) ON CONFLICT(source) DO UPDATE SET reason = excluded.reason, blocked_by = excluded.blocked_by, blocked_at = excluded.blocked_at",
                (source, reason[:200], operator.operator_id, self.now),
            )
            self.app.platform_audit.record(
                "source.block",
                operator=operator,
                target_type="source",
                target_id=source,
                detail={"reason": reason[:200]},
                connection=connection,
            )

    def unblock_source(self, operator: Principal, source: str) -> None:
        self.app.authz.require(operator, "platform.abuse.manage")
        with self.db.write() as connection:
            if not connection.execute(
                "DELETE FROM blocked_sources WHERE source = ?", (source,)
            ).rowcount:
                raise NotFound("That source is not blocked.")
            self.app.platform_audit.record(
                "source.unblock",
                operator=operator,
                target_type="source",
                target_id=source,
                connection=connection,
            )

    def export_tenant_audit(
        self, operator: Principal, org_id: str
    ) -> list[dict[str, Any]]:
        self.app.authz.require(operator, "platform.abuse.manage")
        self.app.tenancy.org(org_id)
        rows = self.app.audit.export_lines(org_id)
        self.app.platform_audit.record(
            "tenant.audit_export",
            operator=operator,
            org_id=org_id,
            detail={"rows": len(rows)},
        )
        return rows

    # ------------------------------------------------------------------ operators

    def list_operators(self, operator: Principal) -> list[dict[str, Any]]:
        self.app.authz.require(operator, "platform.operators.manage")
        with self.db.read() as connection:
            return many(
                connection,
                "SELECT operator_id, email, name, role, status, mfa_enabled_at, last_sign_in_at, created_at FROM operators ORDER BY created_at",
            )

    def set_operator_status(
        self, operator: Principal, operator_id: str, status: str
    ) -> None:
        self.app.authz.require(operator, "platform.operators.manage")
        if status not in {"active", "disabled"}:
            raise ValidationFailed("Status must be active or disabled.")
        if operator_id == operator.operator_id and status == "disabled":
            raise Conflict("You cannot disable yourself.")
        with self.db.write() as connection:
            if not connection.execute(
                "UPDATE operators SET status = ? WHERE operator_id = ?",
                (status, operator_id),
            ).rowcount:
                raise NotFound("Operator not found.")
            if status == "disabled":
                connection.execute(
                    "UPDATE sessions SET revoked_at = ? WHERE operator_id = ? AND revoked_at IS NULL",
                    (self.now, operator_id),
                )
            self.app.platform_audit.record(
                "operator.status",
                operator=operator,
                target_type="operator",
                target_id=operator_id,
                detail={"status": status},
                connection=connection,
            )


__all__ = ["PlatformAdmin", "dumps", "COACH", "OWNER"]
