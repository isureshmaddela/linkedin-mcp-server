"""Subscriptions: trial, checkout, upgrade, downgrade, cancellation, dunning and webhooks.

States: ``trialing``, ``active``, ``past_due``, ``read_only``, ``cancelled``, ``deleted``.
Every transition is written to the subscription event table and to the organization's audit
log with who or what caused it (a person, a webhook, the scheduler). Webhooks are verified,
processed once (``billing_events`` is keyed by the provider's event id) and take effect
synchronously, so an organization is active the moment its payment webhook is processed.
"""

from __future__ import annotations

import logging
import sqlite3
from typing import TYPE_CHECKING, Any

from aira import apps as app_catalog
from aira_cloud import crypto
from aira_cloud.billing_providers import (
    BillingProvider,
    StandInProvider,
    StripeProvider,
)
from aira_cloud.config import DAY
from aira_cloud.db import dumps, loads, many, new_id, one
from aira_cloud.errors import (
    Conflict,
    Forbidden,
    NotFound,
    PlanRequired,
    SubscriptionInactive,
    Unavailable,
    ValidationFailed,
)
from aira_cloud.plans import (
    PLAN_ORDER,
    app_price,
    app_state,
    month_key,
    plan_for_app,
    with_later_rates,
)
from aira_cloud.rbac import BILLING, OWNER, Principal

if TYPE_CHECKING:
    from aira_cloud.app import CloudApp

logger = logging.getLogger("aira_cloud.billing")

PERIOD_SECONDS = 30 * DAY
TRANSITIONS: dict[str, set[str]] = {
    "trialing": {"active", "cancelled", "deleted"},
    "active": {"past_due", "cancelled", "deleted"},
    "past_due": {"active", "read_only", "cancelled", "deleted"},
    "read_only": {"active", "cancelled", "deleted"},
    "cancelled": {"active", "trialing", "deleted"},
    "deleted": set(),
}
PAID_PLANS = ("starter", "team")


CUSTOM_LIMIT_NAMES = (
    "candidate_seats",
    "staff_seats",
    "apps_per_candidate_day",
    "apps_per_org_month",
    "search_plans_per_candidate",
    "cover_letters_per_month",
    "api_searches_per_month",
    "pooled_searches_per_month",
    "leads_stored",
    "lead_enrichments_per_month",
    "lead_emails_per_month",
    "lead_email_daily_cap",
    "api_v1_keys",
    "api_v1_per_minute",
    "monitors_per_candidate",
    "storage_bytes",
    "retention_days",
    "audit_days",
)
COACH_SCOPES = ("view", "act", "approve")


def validate_overrides(values: Any) -> dict[str, Any]:
    """Contract limits an operator may set: known names, whole numbers (``null`` means unlimited)."""
    if not isinstance(values, dict):
        raise ValidationFailed("Limits must be an object of name to number.")
    clean: dict[str, Any] = {}
    for name, value in values.items():
        if name == "features":
            if not isinstance(value, dict) or set(value) - {"add", "remove"}:
                raise ValidationFailed("features takes {add: [...], remove: [...]}.")
            clean[name] = {
                key: [str(item) for item in value.get(key, [])]
                for key in ("add", "remove")
                if value.get(key)
            }
        elif name == "coach_max_scope":
            if value not in COACH_SCOPES:
                raise ValidationFailed(
                    f"coach_max_scope must be one of {', '.join(COACH_SCOPES)}."
                )
            clean[name] = value
        elif name in CUSTOM_LIMIT_NAMES:
            if value is None:
                clean[name] = None
            elif (
                isinstance(value, bool)
                or not isinstance(value, int)
                or not 0 <= value <= 10**13
            ):
                raise ValidationFailed(f"{name} must be a whole number or null.")
            else:
                clean[name] = value
        else:
            raise ValidationFailed(f"{name} is not a limit that can be customized.")
    return clean


class Billing:
    def __init__(self, app: CloudApp) -> None:
        self.app = app
        self.providers: dict[str, BillingProvider] = {}
        secret = self._standin_secret()
        self.standin = StandInProvider(app.db, app.clock, app.config, secret)
        self.providers["standin"] = self.standin
        if app.config.stripe_secret_key or app.config.billing_provider == "stripe":
            self.providers["stripe"] = StripeProvider(app.config, app.clock)

    # -- helpers ----------------------------------------------------------------------

    @property
    def db(self):  # noqa: ANN201
        return self.app.db

    @property
    def now(self) -> int:
        return self.app.clock.now()

    def _standin_secret(self) -> str:
        with self.app.db.write() as connection:
            row = one(
                connection,
                "SELECT value FROM cloud_kv WHERE key = 'standin_webhook_secret'",
            )
            if row:
                return str(row["value"])
            secret = crypto.new_token(24)
            connection.execute(
                "INSERT INTO cloud_kv (key, value) VALUES ('standin_webhook_secret', ?)",
                (secret,),
            )
            return secret

    @property
    def provider(self) -> BillingProvider:
        name = self.app.config.billing_provider
        if name not in self.providers:
            raise Unavailable(
                f"The billing provider '{name}' is not configured.",
                code="billing_not_configured",
            )
        return self.providers[name]

    def subscription(self, org_id: str) -> dict[str, Any]:
        with self.db.read() as connection:
            row = one(
                connection, "SELECT * FROM subscriptions WHERE org_id = ?", (org_id,)
            )
        if row is None:
            raise NotFound("Subscription not found.")
        row["overrides"] = loads(row["overrides"], {})
        row["over_limits"] = loads(row["over_limits"], [])
        return row

    # -- creation ---------------------------------------------------------------------

    def create_trial(self, org_id: str, connection: sqlite3.Connection) -> None:
        plan = self.app.catalog.get("trial")
        now = self.now
        trial_end = now + self.app.config.trial_days * DAY
        connection.execute(
            """INSERT INTO subscriptions (org_id, plan_id, plan_version, status, candidate_seats, period_start, period_end, trial_end, updated_at, created_at)
            VALUES (?, 'trial', ?, 'trialing', ?, ?, ?, ?, ?, ?)""",
            (
                org_id,
                plan["version"],
                plan["entitlements"]["candidate_seats"],
                now,
                trial_end,
                trial_end,
                now,
                now,
            ),
        )
        connection.execute(
            "INSERT INTO subscription_events (org_id, from_status, to_status, reason, trigger_type, trigger_id, at) VALUES (?, '', 'trialing', 'signup', 'person', '', ?)",
            (org_id, now),
        )

    # -- state machine ----------------------------------------------------------------

    def transition(
        self,
        org_id: str,
        to_status: str,
        *,
        reason: str,
        trigger_type: str,
        trigger_id: str = "",
        fields: dict[str, Any] | None = None,
        connection: sqlite3.Connection | None = None,
    ) -> str:
        """Move the subscription to ``to_status`` (a no-op if it is already there) and record why."""

        def apply(db_connection: sqlite3.Connection) -> str:
            row = one(
                db_connection,
                "SELECT status FROM subscriptions WHERE org_id = ?",
                (org_id,),
            )
            if row is None:
                raise NotFound("Subscription not found.")
            current = row["status"]
            values = dict(fields or {})
            if current != to_status:
                if to_status not in TRANSITIONS.get(current, set()):
                    raise Conflict(
                        f"A subscription cannot move from {current} to {to_status}."
                    )
                values["status"] = to_status
            if not values:
                return current
            values["updated_at"] = self.now
            sets = ", ".join(f"{column} = ?" for column in values)
            db_connection.execute(
                f"UPDATE subscriptions SET {sets} WHERE org_id = ?",
                (*values.values(), org_id),
            )
            if current != to_status:
                db_connection.execute(
                    "INSERT INTO subscription_events (org_id, from_status, to_status, reason, trigger_type, trigger_id, at) VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (
                        org_id,
                        current,
                        to_status,
                        reason[:200],
                        trigger_type,
                        trigger_id,
                        self.now,
                    ),
                )
                self.app.audit.record(
                    org_id,
                    f"subscription.{current}_to_{to_status}",
                    actor_type={
                        "person": "member",
                        "webhook": "webhook",
                        "scheduler": "system",
                    }.get(trigger_type, trigger_type),
                    actor_id=trigger_id,
                    actor_label=f"{trigger_type}:{trigger_id}"
                    if trigger_id
                    else trigger_type,
                    target_type="subscription",
                    target_id=org_id,
                    detail={"reason": reason[:200]},
                    source=trigger_type,
                    connection=db_connection,
                )
            return current

        if connection is not None:
            previous = apply(connection)
        else:
            with self.db.write() as write_connection:
                previous = apply(write_connection)
        self.app.entitlements.invalidate(org_id)
        return previous

    # -- pricing ----------------------------------------------------------------------

    def public_plans(self) -> list[dict[str, Any]]:
        out = []
        for plan in self.app.catalog.current().values():
            if not plan["public"]:
                continue
            ent, pricing = plan["entitlements"], plan["pricing"]
            out.append(
                {
                    "plan_id": plan["plan_id"],
                    "name": plan["name"],
                    "seat_cents_month": pricing.get("seat_cents_month", 0),
                    "min_seats": pricing.get("min_seats", 0),
                    "contact_sales": bool(pricing.get("contact_sales")),
                    "currency": pricing.get("currency", "usd"),
                    "overage": pricing.get("overage", {}),
                    "limits": {k: v for k, v in ent.items() if k != "features"},
                    "features": ent.get("features", []),
                }
            )
        return out

    def quote(
        self,
        org_id: str,
        plan_id: str,
        seats: int,
        *,
        enforce_usage: bool = True,
        apps: list[str] | None = None,
    ) -> dict[str, Any]:
        """Monthly price for ``plan_id`` with ``seats``, with the organization's app add-ons carried along.

        ``apps`` names the add-ons to price (default: the ones the organization has bought that the target
        plan offers); an add-on the target plan does not offer is listed in ``dropped_apps``.
        """
        if plan_id == "enterprise":
            raise ValidationFailed(
                "Enterprise is arranged with our sales team; it is activated for you.",
                code="contact_sales",
            )
        if plan_id not in PAID_PLANS:
            raise ValidationFailed("Choose Starter or Team.")
        plan = self.app.catalog.get(plan_id)
        pricing, ent = plan["pricing"], plan["entitlements"]
        try:
            seats = int(seats)
        except (TypeError, ValueError):
            raise ValidationFailed("Enter the number of candidate seats.") from None
        minimum = int(pricing.get("min_seats", 0))
        maximum = ent.get("candidate_seats")
        if seats < max(minimum, 1):
            raise ValidationFailed(
                f"{plan['name']} starts at {max(minimum, 1)} candidate seats."
            )
        if maximum is not None and seats > maximum:
            larger = next(
                (
                    p
                    for p in PAID_PLANS
                    if p != plan_id
                    and (
                        self.app.catalog.get(p)["entitlements"].get("candidate_seats")
                        or 0
                    )
                    >= seats
                ),
                None,
            )
            raise PlanRequired(
                f"{plan['name']} includes up to {maximum} candidate seats."
                + (
                    f" Choose {larger.title()} for {seats}."
                    if larger
                    else " Contact sales for more."
                ),
                limit="candidate_seats",
                limit_value=maximum,
                required_plan=larger,
                current_plan=plan_id,
            )
        used = self.app.tenancy.seat_usage(org_id)["candidates"]
        if enforce_usage and seats < used:
            raise Conflict(
                f"{used} candidate seats are in use or invited. Remove people first, or choose at least {used} seats.",
                used=used,
            )
        current_addons = self.active_addons(org_id)
        wanted = (
            current_addons
            if apps is None
            else [a for a in apps if a in app_catalog.BY_ID]
        )
        kept = [a for a in wanted if app_state(plan_id, a) == "addon"]
        dropped = [a for a in wanted if app_state(plan_id, a) == "unavailable"]
        addon_lines = self._addon_lines(plan_id, seats, kept)
        base_monthly = seats * int(pricing["seat_cents_month"])
        monthly = base_monthly + sum(line["amount_cents"] for line in addon_lines)
        quote: dict[str, Any] = {
            "plan_id": plan_id,
            "plan_name": plan["name"],
            "seats": seats,
            "seat_cents_month": pricing["seat_cents_month"],
            "plan_monthly_cents": base_monthly,
            "addons": addon_lines,
            "dropped_apps": dropped,
            "monthly_cents": monthly,
            "currency": pricing.get("currency", "usd"),
            "overage": pricing.get("overage", {}),
        }
        subscription = self.subscription(org_id)
        if subscription["status"] == "active" and subscription["period_end"]:
            old_monthly = self._monthly_cents(subscription)
            period = max(
                1,
                subscription["period_end"]
                - (subscription["period_start"] or self.now - PERIOD_SECONDS),
            )
            fraction = max(
                0.0, min(1.0, (subscription["period_end"] - self.now) / period)
            )
            quote["prorated_charge_cents"] = max(
                0, round((monthly - old_monthly) * fraction)
            )
            quote["previous_monthly_cents"] = old_monthly
        return quote

    # -- checkout and plan changes ------------------------------------------------------

    def start_checkout(
        self, actor: Principal, plan_id: str, seats: int
    ) -> dict[str, Any]:
        self.app.authz.require(actor, "billing.manage")
        quote = self.quote(actor.org_id, plan_id, seats)
        subscription = self.subscription(actor.org_id)
        if subscription["status"] == "active":
            raise Conflict(
                "The organization already has a paid plan. Change plan instead.",
                code="already_subscribed",
            )
        if subscription["status"] == "deleted":
            raise Conflict("This organization was deleted.")
        org = self.app.tenancy.org(actor.org_id)
        provider = self.provider
        customer = subscription["billing_customer_id"]
        if not customer or subscription["billing_provider"] != provider.name:
            owner = self._owner_email(actor.org_id)
            customer = provider.create_customer(actor.org_id, org["name"], owner)
            with self.db.write() as connection:
                connection.execute(
                    "UPDATE subscriptions SET billing_customer_id = ?, billing_provider = ?, updated_at = ? WHERE org_id = ?",
                    (customer, provider.name, self.now, actor.org_id),
                )
        base = self.app.config.base_url
        session = provider.create_checkout(
            org_id=actor.org_id,
            customer_id=customer,
            plan_id=plan_id,
            plan_name=quote["plan_name"],
            seats=quote["seats"],
            seat_cents=int(quote["seat_cents_month"]),
            currency=quote["currency"],
            success_url=f"{base}/#/org/billing?checkout=success",
            cancel_url=f"{base}/#/org/billing?checkout=cancelled",
        )
        self.app.audit.record(
            actor.org_id,
            "billing.checkout_start",
            actor=actor,
            detail={"plan": plan_id, "seats": quote["seats"]},
        )
        return {
            "checkout_url": session["url"],
            "session_id": session["id"],
            "quote": quote,
        }

    def _owner_email(self, org_id: str) -> str:
        with self.db.read() as connection:
            row = one(
                connection,
                "SELECT email FROM members WHERE org_id = ? AND role = 'owner' AND removed_at IS NULL ORDER BY created_at LIMIT 1",
                (org_id,),
            )
        return row["email"] if row else ""

    def change_plan(
        self, actor: Principal, plan_id: str, seats: int | None = None
    ) -> dict[str, Any]:
        """Upgrade (and add seats) now with a prorated charge; downgrade (and remove seats) at period end."""
        self.app.authz.require(actor, "billing.manage")
        subscription = self.subscription(actor.org_id)
        if subscription["status"] != "active":
            raise Conflict(
                "Choose a plan through checkout first.", code="not_subscribed"
            )
        target_seats = (
            int(seats) if seats is not None else subscription["candidate_seats"]
        )
        # A downgrade may go below current usage: it applies at period end and automation pauses until within limits.
        quote = self.quote(actor.org_id, plan_id, target_seats, enforce_usage=False)
        current_rank = (
            PLAN_ORDER.index(subscription["plan_id"])
            if subscription["plan_id"] in PLAN_ORDER
            else 0
        )
        target_rank = PLAN_ORDER.index(plan_id)
        immediate = target_rank > current_rank or (
            target_rank == current_rank
            and target_seats > subscription["candidate_seats"]
        )
        if (
            target_rank == current_rank
            and target_seats == subscription["candidate_seats"]
        ):
            raise ValidationFailed("That is already the current plan and seat count.")
        if not immediate:
            effective = subscription["period_end"] or self.now
            with self.db.write() as connection:
                connection.execute(
                    "UPDATE subscriptions SET pending_plan_id = ?, pending_seats = ?, pending_effective_at = ?, updated_at = ? WHERE org_id = ?",
                    (plan_id, target_seats, effective, self.now, actor.org_id),
                )
                self.app.audit.record(
                    actor.org_id,
                    "billing.downgrade_scheduled",
                    actor=actor,
                    detail={
                        "plan": plan_id,
                        "seats": target_seats,
                        "effective_at": effective,
                    },
                    connection=connection,
                )
            return {
                "scheduled": True,
                "effective_at": effective,
                "would_exceed": self.over_limits_for(
                    actor.org_id, plan_id, target_seats
                ),
                "dropped_apps": quote["dropped_apps"],
                "quote": quote,
            }
        plan = self.app.catalog.get(plan_id)
        kept = [line["app_id"] for line in quote["addons"]]
        if subscription["billing_subscription_id"]:
            self._provider_for(subscription).update_subscription(
                subscription["billing_subscription_id"],
                plan_id=plan_id,
                plan_name=plan["name"],
                seats=target_seats,
                seat_cents=int(plan["pricing"]["seat_cents_month"]),
                currency=plan["pricing"].get("currency", "usd"),
                prorate=True,
                addons=self._provider_addons(plan_id, target_seats, kept),
            )
        charge = int(quote.get("prorated_charge_cents", 0))
        with self.db.write() as connection:
            connection.execute(
                """UPDATE subscriptions SET plan_id = ?, plan_version = ?, candidate_seats = ?, pending_plan_id = '', pending_seats = NULL,
                pending_effective_at = NULL, updated_at = ? WHERE org_id = ?""",
                (plan_id, plan["version"], target_seats, self.now, actor.org_id),
            )
            self._cancel_apps(
                connection,
                actor.org_id,
                quote["dropped_apps"],
                reason=f"not available on {plan['name']}",
                actor=actor,
            )
            if charge:
                connection.execute(
                    """INSERT INTO invoices (invoice_id, org_id, kind, amount_cents, currency, status, period_start, period_end, lines, created_at, paid_at)
                    VALUES (?, ?, 'proration', ?, ?, 'paid', ?, ?, ?, ?, ?)""",
                    (
                        new_id(),
                        actor.org_id,
                        charge,
                        quote["currency"],
                        self.now,
                        subscription["period_end"],
                        dumps(
                            [
                                {
                                    "description": f"Upgrade to {plan['name']} ({target_seats} seats), prorated",
                                    "amount_cents": charge,
                                }
                            ]
                        ),
                        self.now,
                        self.now,
                    ),
                )
            self.app.audit.record(
                actor.org_id,
                "billing.upgrade",
                actor=actor,
                detail={
                    "from": subscription["plan_id"],
                    "to": plan_id,
                    "seats": target_seats,
                    "prorated_cents": charge,
                },
                connection=connection,
            )
        self.app.entitlements.invalidate(actor.org_id)
        self.app.lifecycle.recompute_over_limits(actor.org_id)
        return {
            "scheduled": False,
            "applied": True,
            "prorated_charge_cents": charge,
            "dropped_apps": quote["dropped_apps"],
            "quote": quote,
        }

    def cancel_pending_change(self, actor: Principal) -> None:
        self.app.authz.require(actor, "billing.manage")
        with self.db.write() as connection:
            connection.execute(
                "UPDATE subscriptions SET pending_plan_id = '', pending_seats = NULL, pending_effective_at = NULL, updated_at = ? WHERE org_id = ?",
                (self.now, actor.org_id),
            )
            self.app.audit.record(
                actor.org_id,
                "billing.downgrade_cancelled",
                actor=actor,
                connection=connection,
            )

    def set_overage(self, actor: Principal, enabled: bool) -> dict[str, Any]:
        """Let an organization keep working past its monthly caps and pay for the excess (Team, Enterprise)."""
        self.app.authz.require(actor, "billing.manage")
        self.app.entitlements.require_state(actor.org_id, write=True)
        if enabled:
            self.app.entitlements.require_feature(
                actor.org_id, "overage", label="Overage billing"
            )
        with self.db.write() as connection:
            connection.execute(
                "UPDATE subscriptions SET overage_enabled = ?, updated_at = ? WHERE org_id = ?",
                (1 if enabled else 0, self.now, actor.org_id),
            )
            self.app.audit.record(
                actor.org_id,
                "billing.overage_enabled" if enabled else "billing.overage_disabled",
                actor=actor,
                connection=connection,
            )
        self.app.entitlements.invalidate(actor.org_id)
        return {"overage_enabled": enabled}

    def cancel(self, actor: Principal) -> dict[str, Any]:
        """Stop renewal; service continues to the period end, then the export window starts."""
        self.app.authz.require(actor, "billing.manage")
        subscription = self.subscription(actor.org_id)
        if subscription["status"] not in {"active", "trialing"}:
            raise Conflict("There is no active subscription to cancel.")
        if subscription["billing_subscription_id"]:
            self.provider.cancel_subscription(
                subscription["billing_subscription_id"], at_period_end=True
            )
        with self.db.write() as connection:
            connection.execute(
                "UPDATE subscriptions SET cancel_at_period_end = 1, updated_at = ? WHERE org_id = ?",
                (self.now, actor.org_id),
            )
            connection.execute(
                "UPDATE org_apps SET cancel_at_period_end = 1 WHERE org_id = ? AND status = 'active'",
                (actor.org_id,),
            )
            self.app.audit.record(
                actor.org_id,
                "billing.cancel",
                actor=actor,
                detail={"effective_at": subscription["period_end"]},
                connection=connection,
            )
        self.app.entitlements.invalidate(actor.org_id)
        return {
            "cancel_at_period_end": True,
            "effective_at": subscription["period_end"],
        }

    def reactivate(self, actor: Principal) -> dict[str, Any]:
        self.app.authz.require(actor, "billing.manage")
        subscription = self.subscription(actor.org_id)
        if subscription["status"] == "active" and subscription["cancel_at_period_end"]:
            with self.db.write() as connection:
                connection.execute(
                    "UPDATE subscriptions SET cancel_at_period_end = 0, updated_at = ? WHERE org_id = ?",
                    (self.now, actor.org_id),
                )
                connection.execute(  # keeping the plan keeps its add-ons too
                    "UPDATE org_apps SET cancel_at_period_end = 0 WHERE org_id = ? AND status = 'active'",
                    (actor.org_id,),
                )
                self.app.audit.record(
                    actor.org_id,
                    "billing.reactivate",
                    actor=actor,
                    connection=connection,
                )
            return {"reactivated": True}
        raise Conflict(
            "Choose a plan through checkout to start the subscription again.",
            code="needs_checkout",
        )

    def portal(self, actor: Principal) -> dict[str, str]:
        self.app.authz.require(actor, "billing.manage")
        subscription = self.subscription(actor.org_id)
        if not subscription["billing_customer_id"]:
            raise Conflict("There is no billing account yet. Choose a plan first.")
        return {
            "url": self.providers[
                subscription["billing_provider"] or self.provider.name
            ].portal_url(
                subscription["billing_customer_id"],
                f"{self.app.config.base_url}/#/org/billing",
            )
        }

    def invoices(self, actor: Principal, limit: int = 50) -> list[dict[str, Any]]:
        self.app.authz.require(actor, "billing.manage")
        with self.db.read() as connection:
            rows = many(
                connection,
                "SELECT * FROM invoices WHERE org_id = ? ORDER BY created_at DESC LIMIT ?",
                (actor.org_id, limit),
            )
        for row in rows:
            row["lines"] = loads(row["lines"], [])
        return rows

    def summary(self, org_id: str) -> dict[str, Any]:
        subscription = self.subscription(org_id)
        entitlements = self.app.entitlements.resolve(org_id)
        plan = self.app.catalog.get(
            subscription["plan_id"], subscription["plan_version"]
        )
        usage = self.app.tenancy.seat_usage(org_id)
        return {
            "plan_id": subscription["plan_id"],
            "plan_name": plan["name"],
            "status": subscription["status"],
            "candidate_seats": subscription["candidate_seats"],
            "seat_usage": usage,
            "period_start": subscription["period_start"],
            "period_end": subscription["period_end"],
            "trial_end": subscription["trial_end"],
            "days_to_renewal": max(
                0, ((subscription["period_end"] or self.now) - self.now) // DAY
            ),
            "cancel_at_period_end": bool(subscription["cancel_at_period_end"]),
            "pending": {
                "plan_id": subscription["pending_plan_id"],
                "seats": subscription["pending_seats"],
                "effective_at": subscription["pending_effective_at"],
            }
            if subscription["pending_plan_id"]
            else None,
            "past_due_since": subscription["past_due_since"],
            "read_only_at": subscription["read_only_at"],
            "deletion_scheduled_at": subscription["deletion_scheduled_at"],
            "export_until": subscription["export_until"],
            "over_limits": subscription["over_limits"],
            "entitlements": entitlements.as_dict(),
            "provider": subscription["billing_provider"],
            "overage_enabled": entitlements.overage_enabled,
            "apps": self.apps_overview(org_id),
            "monthly_cents": self._monthly_cents(subscription)
            if subscription["status"] in {"active", "past_due", "read_only"}
            else 0,
            "addons_monthly_cents": sum(
                line["amount_cents"]
                for line in self._addon_lines(
                    subscription["plan_id"],
                    int(subscription["candidate_seats"]),
                    self.active_addons(org_id, subscription["plan_id"]),
                )
            ),
        }

    # -- apps: subscriptions per app, on top of the plan ------------------------------------

    def _provider_for(self, subscription: dict[str, Any]) -> BillingProvider:
        return self.providers.get(
            subscription["billing_provider"] or self.provider.name, self.provider
        )

    def _app_rows(self, org_id: str) -> dict[str, dict[str, Any]]:
        with self.db.read() as connection:
            rows = many(
                connection, "SELECT * FROM org_apps WHERE org_id = ?", (org_id,)
            )
        return {row["app_id"]: row for row in rows}

    def active_addons(self, org_id: str, plan_id: str | None = None) -> list[str]:
        """App add-ons the organization pays for: bought rows that are add-ons on the plan (grants cost nothing)."""
        plan = plan_id or self.subscription(org_id)["plan_id"]
        rows = self._app_rows(org_id)
        return [
            app.id
            for app in app_catalog.APPS
            if rows.get(app.id, {}).get("status") == "active"
            and app_state(plan, app.id) == "addon"
        ]

    def _addon_lines(
        self, plan_id: str, seats: int, apps: list[str]
    ) -> list[dict[str, Any]]:
        lines = []
        for app_id in apps:
            cents = app_price(plan_id, app_id) or 0
            if cents <= 0:
                continue
            lines.append(
                {
                    "app_id": app_id,
                    "name": app_catalog.BY_ID[app_id].name,
                    "seat_cents_month": cents,
                    "seats": int(seats),
                    "amount_cents": cents * int(seats),
                }
            )
        return lines

    def _provider_addons(
        self, plan_id: str, seats: int, apps: list[str]
    ) -> list[dict[str, Any]]:
        return [
            {
                "app_id": line["app_id"],
                "name": line["name"],
                "seat_cents": line["seat_cents_month"],
                "seats": line["seats"],
            }
            for line in self._addon_lines(plan_id, seats, apps)
        ]

    def apps_overview(self, org_id: str) -> list[dict[str, Any]]:
        """One card per app: how the plan treats it, what it costs here, and whether it is on."""
        subscription = self.subscription(org_id)
        plan_id = subscription["plan_id"]
        seats = int(subscription["candidate_seats"])
        rows = self._app_rows(org_id)
        entitlements = self.app.entitlements.resolve(org_id)
        cards = []
        for app in app_catalog.APPS:
            state = app_state(plan_id, app.id)
            row = rows.get(app.id)
            price = app_price(plan_id, app.id)
            if row and row["status"] == "granted":
                status = "granted"
            elif state == "included":
                status = "included"
            elif row and row["status"] == "active":
                status = "cancelling" if row["cancel_at_period_end"] else "active"
            elif state == "unavailable":
                status = "unavailable"
            else:
                status = "off"
            monthly = (price or 0) * seats if status in {"active", "cancelling"} else 0
            cards.append(
                {
                    "id": app.id,
                    "name": app.name,
                    "tagline": app.tagline,
                    "description": app.description,
                    "icon": app.icon,
                    "home": app.home,
                    "state": state,
                    "status": status,
                    "enabled": entitlements.has_app(app.id),
                    "seat_cents_month": price,
                    "seats": seats,
                    "monthly_cents": monthly,
                    "currency": "usd",
                    "activated_at": row["activated_at"] if row else None,
                    "cancel_at_period_end": bool(row and row["cancel_at_period_end"]),
                    "effective_until": subscription["period_end"]
                    if row and row["cancel_at_period_end"]
                    else None,
                    "required_plan": plan_for_app(app.id)
                    if state == "unavailable"
                    else None,
                    "note": (row or {}).get("note", ""),
                }
            )
        return cards

    def apps_for_member(self, org_id: str, entitlements: Any) -> list[dict[str, Any]]:
        """What the app switcher needs: each app, whether this organization may open it, and why not."""
        out = []
        for app in app_catalog.APPS:
            enabled = entitlements.has_app(app.id)
            state = app_state(entitlements.plan_id, app.id)
            reason = ""
            if not enabled:
                reason = (
                    "Not turned on for your organization."
                    if state == "addon"
                    else f"Not available on the {entitlements.plan_id.title()} plan."
                )
            out.append(
                {"id": app.id, "name": app.name, "enabled": enabled, "reason": reason}
            )
        return out

    def app_quote(self, org_id: str, app_id: str) -> dict[str, Any]:
        """What turning an app on costs now: the monthly line and the prorated charge for the rest of this period."""
        if app_id not in app_catalog.BY_ID:
            raise NotFound("App not found.")
        subscription = self.subscription(org_id)
        plan_id = subscription["plan_id"]
        state = app_state(plan_id, app_id)
        app = app_catalog.BY_ID[app_id]
        if state == "unavailable":
            needed = plan_for_app(app_id)
            raise PlanRequired(
                f"{app.name} is not available on the {plan_id.title()} plan."
                + (f" Upgrade to {needed.title()} to turn it on." if needed else ""),
                feature=app.feature,
                required_plan=needed,
                current_plan=plan_id,
                app=app_id,
            )
        seats = int(subscription["candidate_seats"])
        price = app_price(plan_id, app_id) or 0
        monthly = price * seats
        fraction = 1.0
        if subscription["period_end"]:
            period = max(
                1,
                subscription["period_end"]
                - (subscription["period_start"] or self.now - PERIOD_SECONDS),
            )
            fraction = max(
                0.0, min(1.0, (subscription["period_end"] - self.now) / period)
            )
        return {
            "app_id": app_id,
            "name": app.name,
            "plan_id": plan_id,
            "state": state,
            "seats": seats,
            "seat_cents_month": price,
            "monthly_cents": monthly,
            "prorated_charge_cents": round(monthly * fraction),
            "currency": "usd",
            "period_end": subscription["period_end"],
        }

    def _require_active_for_apps(self, org_id: str) -> dict[str, Any]:
        """App changes ride on an active paid subscription: dunning, read-only and cancelled refuse them."""
        self.app.entitlements.require_state(org_id, write=True)
        subscription = self.subscription(org_id)
        if subscription["status"] in {"past_due", "read_only", "cancelled"}:
            raise SubscriptionInactive(
                f"The subscription is {subscription['status'].replace('_', ' ')}: apps cannot be changed until payment is restored.",
                status=subscription["status"],
            )
        return subscription

    def subscribe_app(self, actor: Principal, app_id: str) -> dict[str, Any]:
        """Turn an app on for the organization: billed per candidate seat with the plan, prorated now."""
        self.app.authz.require(actor, "billing.manage")
        subscription = self._require_active_for_apps(actor.org_id)
        quote = self.app_quote(actor.org_id, app_id)
        app = app_catalog.BY_ID[app_id]
        if quote["state"] == "included":
            raise Conflict(f"{app.name} is included in your plan.", code="included")
        if subscription["status"] != "active":
            raise Conflict(
                "Choose a plan through checkout first; the trial includes every app.",
                code="not_subscribed",
            )
        rows = self._app_rows(actor.org_id)
        row = rows.get(app_id)
        if row and row["status"] == "granted":
            raise Conflict(
                f"{app.name} was granted to your organization.", code="granted"
            )
        if row and row["status"] == "active" and not row["cancel_at_period_end"]:
            raise Conflict(f"{app.name} is already on.", code="already_subscribed")
        if row and row["status"] == "active" and row["cancel_at_period_end"]:
            return self.keep_app(actor, app_id)
        seats = int(subscription["candidate_seats"])
        apps = [*self.active_addons(actor.org_id), app_id]
        if subscription["billing_subscription_id"]:
            self._provider_for(subscription).update_subscription(
                subscription["billing_subscription_id"],
                plan_id=subscription["plan_id"],
                plan_name=self.app.catalog.get(subscription["plan_id"])["name"],
                seats=seats,
                seat_cents=int(
                    self.app.catalog.get(
                        subscription["plan_id"], subscription["plan_version"]
                    )["pricing"]["seat_cents_month"]
                ),
                currency=quote["currency"],
                prorate=True,
                addons=self._provider_addons(subscription["plan_id"], seats, apps),
            )
        charge = int(quote["prorated_charge_cents"])
        with self.db.write() as connection:
            connection.execute(
                """INSERT INTO org_apps (org_id, app_id, status, seats_at_start, activated_at, cancelled_at, cancel_at_period_end, granted_by, note)
                VALUES (?, ?, 'active', ?, ?, NULL, 0, '', '')
                ON CONFLICT(org_id, app_id) DO UPDATE SET status = 'active', seats_at_start = excluded.seats_at_start,
                activated_at = excluded.activated_at, cancelled_at = NULL, cancel_at_period_end = 0, granted_by = '', note = ''""",
                (actor.org_id, app_id, seats, self.now),
            )
            if charge:
                connection.execute(
                    """INSERT INTO invoices (invoice_id, org_id, kind, amount_cents, currency, status, period_start, period_end, lines, created_at, paid_at)
                    VALUES (?, ?, 'proration', ?, ?, 'paid', ?, ?, ?, ?, ?)""",
                    (
                        new_id(),
                        actor.org_id,
                        charge,
                        quote["currency"],
                        self.now,
                        subscription["period_end"],
                        dumps(
                            [
                                {
                                    "description": f"{app.name} app ({seats} seats), prorated",
                                    "amount_cents": charge,
                                    "app_id": app_id,
                                }
                            ]
                        ),
                        self.now,
                        self.now,
                    ),
                )
            self.app.audit.record(
                actor.org_id,
                "billing.app_subscribed",
                actor=actor,
                target_type="app",
                target_id=app_id,
                detail={
                    "seats": seats,
                    "seat_cents_month": quote["seat_cents_month"],
                    "prorated_cents": charge,
                },
                connection=connection,
            )
        self.app.entitlements.invalidate(actor.org_id)
        return {
            "app_id": app_id,
            "status": "active",
            "prorated_charge_cents": charge,
            "quote": quote,
        }

    def unsubscribe_app(self, actor: Principal, app_id: str) -> dict[str, Any]:
        """Turn an app off at the period end; it keeps working until then."""
        self.app.authz.require(actor, "billing.manage")
        subscription = self._require_active_for_apps(actor.org_id)
        if app_id not in app_catalog.BY_ID:
            raise NotFound("App not found.")
        app = app_catalog.BY_ID[app_id]
        row = self._app_rows(actor.org_id).get(app_id)
        if app_state(subscription["plan_id"], app_id) == "included":
            raise Conflict(
                f"{app.name} is included in your plan and cannot be turned off.",
                code="included",
            )
        if row is None or row["status"] != "active":
            raise Conflict(f"{app.name} is not on.", code="not_subscribed")
        with self.db.write() as connection:
            connection.execute(
                "UPDATE org_apps SET cancel_at_period_end = 1 WHERE org_id = ? AND app_id = ?",
                (actor.org_id, app_id),
            )
            self.app.audit.record(
                actor.org_id,
                "billing.app_cancelled",
                actor=actor,
                target_type="app",
                target_id=app_id,
                detail={"effective_at": subscription["period_end"]},
                connection=connection,
            )
        return {
            "app_id": app_id,
            "status": "cancelling",
            "effective_at": subscription["period_end"],
        }

    def keep_app(self, actor: Principal, app_id: str) -> dict[str, Any]:
        """Undo a cancellation that has not taken effect yet."""
        self.app.authz.require(actor, "billing.manage")
        self._require_active_for_apps(actor.org_id)
        row = self._app_rows(actor.org_id).get(app_id)
        if row is None or row["status"] != "active" or not row["cancel_at_period_end"]:
            raise Conflict(
                "That app is not scheduled to be turned off.", code="not_cancelling"
            )
        with self.db.write() as connection:
            connection.execute(
                "UPDATE org_apps SET cancel_at_period_end = 0 WHERE org_id = ? AND app_id = ?",
                (actor.org_id, app_id),
            )
            self.app.audit.record(
                actor.org_id,
                "billing.app_kept",
                actor=actor,
                target_type="app",
                target_id=app_id,
                connection=connection,
            )
        self.app.entitlements.invalidate(actor.org_id)
        return {"app_id": app_id, "status": "active"}

    def _cancel_apps(
        self,
        connection: sqlite3.Connection,
        org_id: str,
        app_ids: list[str],
        *,
        reason: str,
        actor: Principal | None = None,
    ) -> None:
        for app_id in app_ids:
            changed = connection.execute(
                "UPDATE org_apps SET status = 'cancelled', cancelled_at = ?, cancel_at_period_end = 0 WHERE org_id = ? AND app_id = ? AND status = 'active'",
                (self.now, org_id, app_id),
            ).rowcount
            if changed:
                self.app.audit.record(
                    org_id,
                    "billing.app_cancelled",
                    actor=actor,
                    actor_type="system" if actor is None else "",
                    target_type="app",
                    target_id=app_id,
                    detail={"reason": reason, "effective_at": self.now},
                    connection=connection,
                )
        if app_ids:
            self.app.entitlements.invalidate(org_id)

    def _apply_app_cancellations(self, sub: dict[str, Any]) -> None:
        """At the period end, apps turned off during the period stop; the provider's subscription follows."""
        org_id = sub["org_id"]
        rows = self._app_rows(org_id)
        due = [
            app_id
            for app_id, row in rows.items()
            if row["status"] == "active" and row["cancel_at_period_end"]
        ]
        if not due:
            return
        with self.db.write() as connection:
            self._cancel_apps(
                connection, org_id, due, reason="turned off at period end"
            )
        if sub["billing_subscription_id"]:
            plan = self.app.catalog.get(sub["plan_id"], sub["plan_version"])
            try:
                self._provider_for(sub).update_subscription(
                    sub["billing_subscription_id"],
                    plan_id=sub["plan_id"],
                    plan_name=plan["name"],
                    seats=int(sub["candidate_seats"]),
                    seat_cents=int(plan["pricing"]["seat_cents_month"]),
                    currency=plan["pricing"].get("currency", "usd"),
                    prorate=False,
                    addons=self._provider_addons(
                        sub["plan_id"],
                        int(sub["candidate_seats"]),
                        self.active_addons(org_id, sub["plan_id"]),
                    ),
                )
            except Exception:  # noqa: BLE001 - keep our state moving; reconcile reports the drift
                logger.exception(
                    "Could not drop the app add-on at the provider for %s", org_id
                )

    def tenant_apps(self, operator: Principal, org_id: str) -> dict[str, Any]:
        self.app.authz.require(operator, "platform.tenants.read")
        self.app.tenancy.org(org_id)
        return {"org_id": org_id, "apps": self.apps_overview(org_id)}

    def set_app_grant(
        self,
        operator: Principal,
        org_id: str,
        app_id: str,
        status: str,
        note: str = "",
    ) -> dict[str, Any]:
        """An operator grants an app to a tenant without charge (a contract), or takes a grant back."""
        self.app.authz.require(operator, "platform.billing.manage")
        self.app.tenancy.org(org_id)
        if app_id not in app_catalog.BY_ID:
            raise NotFound("App not found.")
        if status not in {"granted", "revoked"}:
            raise ValidationFailed("status must be granted or revoked.")
        row = self._app_rows(org_id).get(app_id)
        with self.db.write() as connection:
            if status == "granted":
                if row and row["status"] == "active":
                    raise Conflict(
                        "The organization pays for this app; turn it off first to grant it.",
                        code="already_subscribed",
                    )
                connection.execute(
                    """INSERT INTO org_apps (org_id, app_id, status, seats_at_start, activated_at, cancelled_at, cancel_at_period_end, granted_by, note)
                    VALUES (?, ?, 'granted', 0, ?, NULL, 0, ?, ?)
                    ON CONFLICT(org_id, app_id) DO UPDATE SET status = 'granted', activated_at = excluded.activated_at,
                    cancelled_at = NULL, cancel_at_period_end = 0, granted_by = excluded.granted_by, note = excluded.note""",
                    (org_id, app_id, self.now, operator.operator_id, note[:300]),
                )
            else:
                if not row or row["status"] != "granted":
                    raise Conflict(
                        "That app is not granted to the organization.",
                        code="not_granted",
                    )
                connection.execute(
                    "UPDATE org_apps SET status = 'cancelled', cancelled_at = ?, note = ? WHERE org_id = ? AND app_id = ?",
                    (self.now, note[:300], org_id, app_id),
                )
            self.app.platform_audit.record(
                "billing.app_granted" if status == "granted" else "billing.app_revoked",
                operator=operator,
                org_id=org_id,
                detail={"app": app_id, "note": note[:300]},
                connection=connection,
            )
            self.app.audit.record(
                org_id,
                "billing.app_granted" if status == "granted" else "billing.app_revoked",
                actor=operator,
                target_type="app",
                target_id=app_id,
                detail={"note": note[:300]},
                connection=connection,
            )
        self.app.entitlements.invalidate(org_id)
        return {"org_id": org_id, "apps": self.apps_overview(org_id)}

    # -- limits after a plan change ---------------------------------------------------------

    def over_limits_for(self, org_id: str, plan_id: str, seats: int) -> list[str]:
        """Which limits the organization would exceed on ``plan_id`` with ``seats`` candidate seats."""
        plan = self.app.catalog.get(plan_id)
        ent = plan["entitlements"]
        used = self.app.tenancy.seat_usage(org_id)
        over = []
        if used["candidates"] > min(seats, ent.get("candidate_seats") or seats):
            over.append("candidate seats")
        if ent.get("staff_seats") is not None and used["staff"] > ent["staff_seats"]:
            over.append("coach and admin seats")
        max_plans = ent.get("search_plans_per_candidate")
        if (
            max_plans is not None
            and self.app.lifecycle.max_plans_per_candidate(org_id) > max_plans
        ):
            over.append("search plans per candidate")
        return over

    # -- webhooks -----------------------------------------------------------------------------

    def handle_webhook(
        self, provider_name: str, payload: bytes, signature: str
    ) -> dict[str, Any]:
        provider = self.providers.get(provider_name)
        if provider is None:
            raise NotFound("Unknown billing provider.")
        event = provider.verify_webhook(payload, signature)
        neutral = provider.normalize(event)
        event_id = str(event.get("id") or "")
        if not event_id:
            raise ValidationFailed("The event has no id.")
        with self.db.write() as connection:
            existing = one(
                connection,
                "SELECT processed_at FROM billing_events WHERE event_id = ?",
                (event_id,),
            )
            if existing and existing["processed_at"]:
                return {"duplicate": True}
            if not existing:
                connection.execute(
                    "INSERT INTO billing_events (event_id, provider, type, org_id, payload, received_at) VALUES (?, ?, ?, ?, ?, ?)",
                    (
                        event_id,
                        provider_name,
                        str(event.get("type", ""))[:80],
                        (neutral or {}).get("org_id", ""),
                        payload.decode("utf-8", "replace")[:20000],
                        self.now,
                    ),
                )
        if neutral is None:
            self._finish_event(event_id, "")
            return {"ignored": True}
        try:
            self.apply_event(neutral, provider_name)
        except Exception as error:  # noqa: BLE001 - recorded so the provider's retry can succeed later
            with self.db.write() as connection:
                connection.execute(
                    "UPDATE billing_events SET error = ? WHERE event_id = ?",
                    (f"{type(error).__name__}: {error}"[:300], event_id),
                )
            raise
        self._finish_event(event_id, "")
        return {"processed": True, "type": neutral["type"]}

    def _finish_event(self, event_id: str, error: str) -> None:
        with self.db.write() as connection:
            connection.execute(
                "UPDATE billing_events SET processed_at = ?, error = ? WHERE event_id = ?",
                (self.now, error, event_id),
            )

    def _org_for_event(self, neutral: dict[str, Any]) -> str:
        org_id = neutral.get("org_id") or ""
        with self.db.read() as connection:
            if org_id and one(
                connection,
                "SELECT 1 AS x FROM subscriptions WHERE org_id = ?",
                (org_id,),
            ):
                return org_id
            subscription_id = neutral.get("subscription_id") or ""
            if subscription_id:
                row = one(
                    connection,
                    "SELECT org_id FROM subscriptions WHERE billing_subscription_id = ?",
                    (subscription_id,),
                )
                if row:
                    return str(row["org_id"])
            customer = neutral.get("customer_id") or ""
            if customer:
                row = one(
                    connection,
                    "SELECT org_id FROM subscriptions WHERE billing_customer_id = ?",
                    (customer,),
                )
                if row:
                    return str(row["org_id"])
        raise NotFound("The event does not belong to a known organization.")

    def apply_event(self, neutral: dict[str, Any], provider_name: str) -> None:
        org_id = self._org_for_event(neutral)
        kind = neutral["type"]
        trigger: dict[str, Any] = {
            "trigger_type": "webhook",
            "trigger_id": neutral.get("id", ""),
        }
        subscription = self.subscription(org_id)
        if subscription["status"] == "deleted":
            return
        if kind == "checkout.completed":
            plan_id, seats = (
                neutral.get("plan_id") or subscription["plan_id"],
                int(neutral.get("seats") or subscription["candidate_seats"]),
            )
            plan = self.app.catalog.get(plan_id)
            self.transition(
                org_id,
                "active",
                reason="checkout completed",
                fields={
                    "plan_id": plan_id,
                    "plan_version": plan["version"],
                    "candidate_seats": seats,
                    "period_start": self.now,
                    "period_end": self.now + PERIOD_SECONDS,
                    "trial_end": None,
                    "cancel_at_period_end": 0,
                    "billing_provider": provider_name,
                    "billing_customer_id": neutral.get("customer_id")
                    or subscription["billing_customer_id"],
                    "billing_subscription_id": neutral.get("subscription_id")
                    or subscription["billing_subscription_id"],
                    "past_due_since": None,
                    "read_only_at": None,
                    "deletion_scheduled_at": None,
                    "export_until": None,
                    "pending_plan_id": "",
                    "pending_seats": None,
                    "pending_effective_at": None,
                },
                **trigger,
            )
            self.app.lifecycle.recompute_over_limits(org_id)
        elif kind == "invoice.paid":
            self._record_invoice(org_id, neutral, "paid")
            fields: dict[str, Any] = {
                "past_due_since": None,
                "read_only_at": None,
                "deletion_scheduled_at": None,
                "deletion_notice_at": None,
            }
            if (
                neutral.get("period_start")
                and neutral.get("period_end")
                and neutral["period_end"] > neutral["period_start"]
            ):
                fields.update(
                    {
                        "period_start": neutral["period_start"],
                        "period_end": neutral["period_end"],
                    }
                )
            if subscription["status"] in {"past_due", "read_only"}:
                self.transition(
                    org_id,
                    "active",
                    reason="payment received",
                    fields=fields,
                    **trigger,
                )
            elif subscription["status"] == "active":
                self.transition(
                    org_id, "active", reason="renewal paid", fields=fields, **trigger
                )
        elif kind == "invoice.payment_failed":
            self._record_invoice(org_id, neutral, "failed")
            if subscription["status"] == "active":
                self.transition(
                    org_id,
                    "past_due",
                    reason="payment failed",
                    fields={"past_due_since": self.now},
                    **trigger,
                )
                self._notify_payment_failed(org_id)
            elif subscription["status"] == "trialing":
                return
        elif kind == "subscription.updated":
            fields = {}
            if neutral.get("period_end"):
                fields["period_end"] = neutral["period_end"]
            if neutral.get("period_start"):
                fields["period_start"] = neutral["period_start"]
            fields["cancel_at_period_end"] = int(
                bool(neutral.get("cancel_at_period_end"))
            )
            self.transition(
                org_id,
                subscription["status"],
                reason="provider update",
                fields=fields,
                **trigger,
            )
        elif kind == "subscription.deleted":
            if subscription["status"] in {
                "active",
                "past_due",
                "read_only",
                "trialing",
            }:
                self._enter_cancelled(
                    org_id, reason="subscription ended at the provider", **trigger
                )

    def _record_invoice(
        self, org_id: str, neutral: dict[str, Any], status: str
    ) -> None:
        invoice_id = neutral.get("invoice_id") or ""
        with self.db.write() as connection:
            if invoice_id and one(
                connection,
                "SELECT 1 AS x FROM invoices WHERE org_id = ? AND provider_invoice_id = ?",
                (org_id, invoice_id),
            ):
                connection.execute(
                    "UPDATE invoices SET status = ?, paid_at = ? WHERE org_id = ? AND provider_invoice_id = ?",
                    (
                        status,
                        self.now if status == "paid" else None,
                        org_id,
                        invoice_id,
                    ),
                )
                return
            lines = (
                neutral.get("lines") if isinstance(neutral.get("lines"), list) else []
            )
            connection.execute(
                """INSERT INTO invoices (invoice_id, org_id, provider_invoice_id, kind, amount_cents, currency, status, period_start, period_end, hosted_url, lines, created_at, paid_at)
                VALUES (?, ?, ?, 'subscription', ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    new_id(),
                    org_id,
                    invoice_id,
                    int(neutral.get("amount_cents") or 0),
                    neutral.get("currency", "usd"),
                    status,
                    neutral.get("period_start"),
                    neutral.get("period_end"),
                    neutral.get("hosted_url", ""),
                    dumps(lines),
                    self.now,
                    self.now if status == "paid" else None,
                ),
            )

    # -- notifications ----------------------------------------------------------------------------

    def billing_contacts(self, org_id: str) -> list[str]:
        with self.db.read() as connection:
            rows = many(
                connection,
                "SELECT DISTINCT email FROM members WHERE org_id = ? AND role IN ('owner', 'billing') AND removed_at IS NULL AND status = 'active'",
                (org_id,),
            )
        return [row["email"] for row in rows]

    def notice(
        self,
        org_id: str,
        level: str,
        title: str,
        body: str,
        link: str = "#/org/billing",
    ) -> None:
        with self.db.write() as connection:
            connection.execute(
                "INSERT INTO org_notices (org_id, level, title, body, link, created_at) VALUES (?, ?, ?, ?, ?, ?)",
                (org_id, level, title[:160], body[:600], link, self.now),
            )

    def _notify_payment_failed(self, org_id: str) -> None:
        import time

        org = self.app.tenancy.org(org_id)
        read_only_on = time.strftime(
            "%Y-%m-%d",
            time.gmtime(self.now + self.app.config.past_due_read_only_days * DAY),
        )
        for email in self.billing_contacts(org_id):
            self.app.mail.send(
                org_id,
                email,
                "payment_failed",
                org=org["name"],
                read_only_on=read_only_on,
                link=f"{self.app.config.base_url}/#/org/billing",
            )
        self.notice(
            org_id,
            "danger",
            "Payment failed",
            f"Automation stopped. The organization becomes read-only on {read_only_on} unless payment is updated.",
        )

    def _enter_cancelled(
        self, org_id: str, *, reason: str, trigger_type: str, trigger_id: str = ""
    ) -> None:
        import time

        subscription = self.subscription(org_id)
        window = (
            self.app.config.trial_retention_days
            if subscription["plan_id"] == "trial"
            else self.app.config.cancelled_export_days
        )
        export_until = self.now + window * DAY
        with self.db.write() as connection:
            self._cancel_apps(
                connection, org_id, self.active_addons(org_id), reason=reason
            )
        self.transition(
            org_id,
            "cancelled",
            reason=reason,
            trigger_type=trigger_type,
            trigger_id=trigger_id,
            fields={
                "cancelled_at": self.now,
                "export_until": export_until,
                "cancel_at_period_end": 0,
                "pending_plan_id": "",
                "pending_seats": None,
                "pending_effective_at": None,
            },
        )
        org = self.app.tenancy.org(org_id)
        for email in self.billing_contacts(org_id):
            self.app.mail.send(
                org_id,
                email,
                "subscription_cancelled",
                org=org["name"],
                export_until=time.strftime("%Y-%m-%d", time.gmtime(export_until)),
                link=f"{self.app.config.base_url}/#/org/data",
            )
        self.notice(
            org_id,
            "warning",
            "Subscription ended",
            f"Data is read-only and can be exported until {time.strftime('%Y-%m-%d', time.gmtime(export_until))}.",
            "#/org/data",
        )

    # -- scheduler --------------------------------------------------------------------------------

    def process_due(self) -> dict[str, list[str]]:
        """Everything time moves forward: trial end, plan changes at period end, renewals, dunning, deletion."""
        import time

        done: dict[str, list[str]] = {
            k: []
            for k in (
                "trial_ended",
                "trial_notice",
                "changed",
                "cancelled",
                "renewed",
                "read_only",
                "deletion_scheduled",
                "deleted",
            )
        }
        now = self.now
        with self.db.read() as connection:
            subs = many(
                connection, "SELECT * FROM subscriptions WHERE status != 'deleted'"
            )
        for sub in subs:
            org_id = sub["org_id"]
            try:
                status = sub["status"]
                if status == "trialing":
                    if sub["trial_end"] and sub["trial_end"] <= now:
                        self._enter_cancelled(
                            org_id,
                            reason="trial ended without a plan",
                            trigger_type="scheduler",
                        )
                        done["trial_ended"].append(org_id)
                    elif (
                        sub["trial_end"]
                        and sub["trial_end"] - now <= 3 * DAY
                        and not self._flag(f"trial_notice:{org_id}")
                    ):
                        org = self.app.tenancy.org(org_id)
                        for email in self.billing_contacts(org_id):
                            self.app.mail.send(
                                org_id,
                                email,
                                "trial_ending",
                                org=org["name"],
                                trial_end=time.strftime(
                                    "%Y-%m-%d", time.gmtime(sub["trial_end"])
                                ),
                                link=f"{self.app.config.base_url}/#/org/billing",
                            )
                        self.notice(
                            org_id,
                            "warning",
                            "Your trial is ending",
                            "Pick a plan to keep automation running.",
                        )
                        self._set_flag(f"trial_notice:{org_id}")
                        done["trial_notice"].append(org_id)
                elif status == "active":
                    if (
                        sub["cancel_at_period_end"]
                        and sub["period_end"]
                        and sub["period_end"] <= now
                    ):
                        self._enter_cancelled(
                            org_id,
                            reason="cancelled at period end",
                            trigger_type="scheduler",
                        )
                        done["cancelled"].append(org_id)
                    elif sub["period_end"] and sub["period_end"] <= now:
                        self._period_rollover(sub, done)
                elif status in {"past_due", "read_only"}:
                    self._dunning(sub, done)
                elif status == "cancelled":
                    if (
                        sub["export_until"]
                        and sub["export_until"] <= now
                        and not self.app.lifecycle.on_legal_hold(org_id)
                    ):
                        self.app.lifecycle.delete_org(
                            org_id,
                            reason="export window ended",
                            trigger_type="scheduler",
                        )
                        done["deleted"].append(org_id)
                        continue
                if (
                    sub["deletion_scheduled_at"]
                    and sub["deletion_scheduled_at"] <= now
                    and sub["status"] != "deleted"
                    and not self.app.lifecycle.on_legal_hold(org_id)
                ):
                    self.app.lifecycle.delete_org(
                        org_id, reason="scheduled deletion", trigger_type="scheduler"
                    )
                    done["deleted"].append(org_id)
            except Exception:  # noqa: BLE001 - one organization must not stop the sweep
                logger.exception("Lifecycle step failed for %s", org_id)
        return done

    def _period_rollover(self, sub: dict[str, Any], done: dict[str, list[str]]) -> None:
        org_id = sub["org_id"]
        if (
            sub["pending_plan_id"]
            and sub["pending_effective_at"]
            and sub["pending_effective_at"] <= self.now
        ):
            self._apply_pending(sub)
            done["changed"].append(org_id)
            sub = self.subscription(org_id)
        self._bill_overage(sub)
        self._apply_app_cancellations(sub)
        sub = self.subscription(org_id)
        if sub["billing_provider"] == "standin" or not sub["billing_provider"]:
            payload, signature = self.standin.make_event(
                "invoice.paid",
                {
                    "org_id": org_id,
                    "invoice_id": f"in_standin_{new_id()[:12]}",
                    "amount_cents": self._monthly_cents(sub),
                    "currency": "usd",
                    "period_start": sub["period_end"],
                    "period_end": sub["period_end"] + PERIOD_SECONDS,
                    "lines": self.invoice_lines(sub),
                },
            )
            self.handle_webhook("standin", payload, signature)
            done["renewed"].append(org_id)

    def _monthly_cents(self, sub: dict[str, Any]) -> int:
        """The plan's seats plus every app add-on the organization has bought, for one month."""
        plan = self.app.catalog.get(sub["plan_id"], sub["plan_version"])
        seats = int(sub["candidate_seats"])
        base = seats * int(plan["pricing"].get("seat_cents_month", 0))
        addons = self._addon_lines(
            sub["plan_id"], seats, self.active_addons(sub["org_id"], sub["plan_id"])
        )
        return base + sum(line["amount_cents"] for line in addons)

    def invoice_lines(self, sub: dict[str, Any]) -> list[dict[str, Any]]:
        """What a renewal invoice is made of: the plan line and one line per app add-on."""
        plan = self.app.catalog.get(sub["plan_id"], sub["plan_version"])
        seats = int(sub["candidate_seats"])
        lines = [
            {
                "description": f"{plan['name']} ({seats} candidate seats)",
                "amount_cents": seats * int(plan["pricing"].get("seat_cents_month", 0)),
            }
        ]
        for line in self._addon_lines(
            sub["plan_id"], seats, self.active_addons(sub["org_id"], sub["plan_id"])
        ):
            lines.append(
                {
                    "description": f"{line['name']} app ({seats} candidate seats)",
                    "amount_cents": line["amount_cents"],
                    "app_id": line["app_id"],
                }
            )
        return lines

    def _apply_pending(self, sub: dict[str, Any]) -> None:
        org_id = sub["org_id"]
        plan_id = sub["pending_plan_id"]
        seats = int(sub["pending_seats"] or sub["candidate_seats"])
        plan = self.app.catalog.get(plan_id)
        current = self.active_addons(org_id)
        kept = [a for a in current if app_state(plan_id, a) == "addon"]
        dropped = [a for a in current if app_state(plan_id, a) == "unavailable"]
        if sub["billing_subscription_id"]:
            try:
                self._provider_for(sub).update_subscription(
                    sub["billing_subscription_id"],
                    plan_id=plan_id,
                    plan_name=plan["name"],
                    seats=seats,
                    seat_cents=int(plan["pricing"]["seat_cents_month"]),
                    currency=plan["pricing"].get("currency", "usd"),
                    prorate=False,
                    addons=self._provider_addons(plan_id, seats, kept),
                )
            except Exception:  # noqa: BLE001 - keep our state moving; reconcile reports the drift
                logger.exception(
                    "Could not apply the plan change at the provider for %s", org_id
                )
        self.transition(
            org_id,
            "active",
            reason=f"downgrade to {plan_id} at period end",
            trigger_type="scheduler",
            fields={
                "plan_id": plan_id,
                "plan_version": plan["version"],
                "candidate_seats": seats,
                "pending_plan_id": "",
                "pending_seats": None,
                "pending_effective_at": None,
            },
        )
        if dropped:
            with self.db.write() as connection:
                self._cancel_apps(
                    connection,
                    org_id,
                    dropped,
                    reason=f"not available on {plan['name']}",
                )
            self.notice(
                org_id,
                "warning",
                "Apps switched off",
                "Not available on the new plan: "
                + ", ".join(app_catalog.BY_ID[a].name for a in dropped)
                + ".",
                "#/org/apps",
            )
        over = self.app.lifecycle.recompute_over_limits(org_id)
        if over:
            org = self.app.tenancy.org(org_id)
            for email in self.billing_contacts(org_id):
                self.app.mail.send(
                    org_id,
                    email,
                    "downgrade_pause",
                    org=org["name"],
                    limits=", ".join(over),
                    link=f"{self.app.config.base_url}/#/org/members",
                )
            self.notice(
                org_id,
                "warning",
                "Automation paused",
                "The organization is over its new plan limits: "
                + ", ".join(over)
                + ".",
                "#/org/members",
            )

    def _bill_overage(self, sub: dict[str, Any]) -> None:
        """Invoice last month's metered overage (Team and Enterprise) once."""
        org_id = sub["org_id"]
        previous = month_key(self.now - 31 * DAY)
        flag = f"overage_billed:{org_id}:{previous}"
        if self._flag(flag):
            return
        counters = self.app.usage.snapshot(org_id, previous)
        plan = self.app.catalog.get(sub["plan_id"], sub["plan_version"])
        rates = with_later_rates(sub["plan_id"], plan["pricing"].get("overage", {}))
        mapping = {
            "applications:overage": ("application_cents", "applications"),
            "searches:overage": ("search_cents", "searches"),
            "cover_letters:overage": ("cover_letter_cents", "AI cover letters"),
            "api_searches:overage": ("api_search_cents", "API searches"),
            "pooled_searches:overage": (
                "pooled_search_cents",
                "pooled SerpApi searches",
            ),
        }
        lines = []
        for metric, (rate_name, label) in mapping.items():
            units = int(counters.get(metric, 0))
            rate = int(rates.get(rate_name, 0))
            if units and rate:
                lines.append(
                    {
                        "description": f"{units} extra {label} in {previous}",
                        "amount_cents": units * rate,
                    }
                )
        self._set_flag(flag)
        if not lines:
            return
        total = sum(line["amount_cents"] for line in lines)
        if (
            sub["billing_customer_id"]
            and sub["billing_provider"]
            and sub["billing_provider"] != "standin"
        ):
            try:
                self.providers[sub["billing_provider"]].add_invoice_item(
                    sub["billing_customer_id"], total, f"Aira overage {previous}", "usd"
                )
            except Exception:  # noqa: BLE001
                logger.exception(
                    "Could not add the overage invoice item for %s", org_id
                )
        with self.db.write() as connection:
            connection.execute(
                "INSERT INTO invoices (invoice_id, org_id, kind, amount_cents, currency, status, lines, created_at) VALUES (?, ?, 'overage', ?, 'usd', 'open', ?, ?)",
                (new_id(), org_id, total, dumps(lines), self.now),
            )

    def _dunning(self, sub: dict[str, Any], done: dict[str, list[str]]) -> None:
        import time

        org_id = sub["org_id"]
        since = sub["past_due_since"]
        if not since:
            return
        age_days = (self.now - since) // DAY
        config = self.app.config
        if sub["status"] == "past_due" and age_days >= config.past_due_read_only_days:
            self.transition(
                org_id,
                "read_only",
                reason=f"{age_days} days unpaid",
                trigger_type="scheduler",
                fields={"read_only_at": self.now},
            )
            org = self.app.tenancy.org(org_id)
            deletion_on = time.strftime(
                "%Y-%m-%d",
                time.gmtime(
                    since
                    + config.past_due_deletion_days * DAY
                    + config.deletion_grace_days * DAY
                ),
            )
            for email in self.billing_contacts(org_id):
                self.app.mail.send(
                    org_id,
                    email,
                    "read_only",
                    org=org["name"],
                    days=age_days,
                    deletion_on=deletion_on,
                    link=f"{self.app.config.base_url}/#/org/billing",
                )
            self.notice(
                org_id,
                "danger",
                "Read-only",
                f"Payment is {age_days} days overdue. Data is read-only.",
            )
            done["read_only"].append(org_id)
            sub = self.subscription(org_id)
        if (
            sub["status"] in {"past_due", "read_only"}
            and age_days >= config.past_due_deletion_days
            and not sub["deletion_scheduled_at"]
        ):
            scheduled = self.now + config.deletion_grace_days * DAY
            with self.db.write() as connection:
                connection.execute(
                    "UPDATE subscriptions SET deletion_scheduled_at = ?, deletion_notice_at = ?, updated_at = ? WHERE org_id = ?",
                    (scheduled, self.now, self.now, org_id),
                )
                self.app.audit.record(
                    org_id,
                    "subscription.deletion_scheduled",
                    actor_type="system",
                    source="scheduler",
                    detail={"deletion_at": scheduled},
                    connection=connection,
                )
            org = self.app.tenancy.org(org_id)
            for email in self.billing_contacts(org_id):
                self.app.mail.send(
                    org_id,
                    email,
                    "deletion_notice",
                    org=org["name"],
                    deletion_on=time.strftime("%Y-%m-%d", time.gmtime(scheduled)),
                    link=f"{self.app.config.base_url}/#/org/billing",
                )
            self.notice(
                org_id,
                "danger",
                "Scheduled for deletion",
                f"Unless payment is restored, the organization is deleted on {time.strftime('%Y-%m-%d', time.gmtime(scheduled))}.",
            )
            self.app.entitlements.invalidate(org_id)
            done["deletion_scheduled"].append(org_id)

    def _flag(self, key: str) -> bool:
        with self.db.read() as connection:
            return (
                one(connection, "SELECT 1 AS x FROM cloud_kv WHERE key = ?", (key,))
                is not None
            )

    def _set_flag(self, key: str) -> None:
        with self.db.write() as connection:
            connection.execute(
                "INSERT OR IGNORE INTO cloud_kv (key, value) VALUES (?, '1')", (key,)
            )

    # -- operator actions -------------------------------------------------------------------------

    def extend_trial(self, operator: Principal, org_id: str, days: int) -> int:
        self.app.authz.require(operator, "platform.billing.manage")
        if not 1 <= int(days) <= 90:
            raise ValidationFailed("Extend a trial by 1 to 90 days.")
        subscription = self.subscription(org_id)
        if (
            subscription["status"] not in {"trialing", "cancelled"}
            or subscription["plan_id"] != "trial"
        ):
            raise Conflict("Only a trial can be extended.")
        base = max(subscription["trial_end"] or self.now, self.now)
        new_end = base + int(days) * DAY
        if subscription["status"] == "cancelled":
            self.transition(
                org_id,
                "trialing",
                reason="trial reopened by operator",
                trigger_type="operator",
                trigger_id=operator.operator_id,
                fields={"export_until": None, "cancelled_at": None},
            )
        with self.db.write() as connection:
            connection.execute(
                "UPDATE subscriptions SET trial_end = ?, period_end = ?, updated_at = ? WHERE org_id = ?",
                (new_end, new_end, self.now, org_id),
            )
            self.app.platform_audit.record(
                "billing.extend_trial",
                operator=operator,
                org_id=org_id,
                detail={"days": int(days), "trial_end": new_end},
                connection=connection,
            )
        self.app.entitlements.invalidate(org_id)
        return new_end

    def custom_limits(self, operator: Principal, org_id: str) -> dict[str, Any]:
        """The plan's limits, the organization's overrides and what actually applies."""
        self.app.authz.require(operator, "platform.billing.manage")
        subscription = self.subscription(org_id)
        plan = self.app.catalog.get(
            subscription["plan_id"], subscription["plan_version"]
        )
        entitlements = self.app.entitlements.resolve(org_id, fresh=True)
        return {
            "plan_id": subscription["plan_id"],
            "available": entitlements.has("custom_limits"),
            "plan_limits": {
                name: plan["entitlements"].get(name) for name in CUSTOM_LIMIT_NAMES
            },
            "overrides": subscription["overrides"],
            "effective": {
                name: entitlements.limit(name) for name in CUSTOM_LIMIT_NAMES
            },
        }

    def set_custom_limits(
        self,
        operator: Principal,
        org_id: str,
        limits: dict[str, Any],
        reason: str,
        *,
        replace: bool = False,
    ) -> dict[str, Any]:
        """Override plan limits for one organization (Enterprise contracts), recorded in both audit logs."""
        self.app.authz.require(operator, "platform.billing.manage")
        reason = str(reason or "").strip()
        if len(reason) < 5:
            raise ValidationFailed("Say why (a contract or ticket reference).")
        clean = validate_overrides(limits)
        subscription = self.subscription(org_id)
        if subscription["status"] == "deleted":
            raise Conflict("This organization was deleted.")
        self.app.entitlements.require_feature(
            org_id, "custom_limits", label="Custom limits"
        )
        before = dict(subscription["overrides"])
        after = clean if replace else {**before, **clean}
        with self.db.write() as connection:
            connection.execute(
                "UPDATE subscriptions SET overrides = ?, updated_at = ? WHERE org_id = ?",
                (dumps(after), self.now, org_id),
            )
            self.app.platform_audit.record(
                "billing.custom_limits",
                operator=operator,
                org_id=org_id,
                detail={"before": before, "after": after, "reason": reason[:200]},
                connection=connection,
            )
            self.app.audit.record(
                org_id,
                "billing.custom_limits",
                actor=operator,
                detail={"limits": clean, "reason": reason[:200]},
                connection=connection,
            )
        self.app.entitlements.invalidate(org_id)
        self.app.lifecycle.recompute_over_limits(org_id)
        return self.custom_limits(operator, org_id)

    def issue_credit(
        self, operator: Principal, org_id: str, amount_cents: int, reason: str
    ) -> None:
        self.app.authz.require(operator, "platform.billing.manage")
        if not 1 <= int(amount_cents) <= 10_000_000:
            raise ValidationFailed("Enter a credit amount in cents.")
        self.subscription(org_id)
        with self.db.write() as connection:
            connection.execute(
                "INSERT INTO billing_credits (org_id, amount_cents, reason, created_by, created_at) VALUES (?, ?, ?, ?, ?)",
                (
                    org_id,
                    int(amount_cents),
                    reason[:300],
                    operator.operator_id,
                    self.now,
                ),
            )
            self.app.platform_audit.record(
                "billing.credit",
                operator=operator,
                org_id=org_id,
                detail={"amount_cents": int(amount_cents), "reason": reason[:200]},
                connection=connection,
            )

    def activate_enterprise(
        self,
        operator: Principal,
        org_id: str,
        *,
        seats: int,
        overrides: dict[str, Any] | None = None,
        period_days: int = 365,
        invoice_cents: int = 0,
        reference: str = "",
    ) -> dict[str, Any]:
        """Enterprise goes through sales: an operator activates the plan with custom limits and an invoice."""
        self.app.authz.require(operator, "platform.billing.manage")
        plan = self.app.catalog.get("enterprise")
        subscription = self.subscription(org_id)
        if subscription["status"] == "deleted":
            raise Conflict("This organization was deleted.")
        fields = {
            "plan_id": "enterprise",
            "plan_version": plan["version"],
            "candidate_seats": int(seats),
            "period_start": self.now,
            "period_end": self.now + int(period_days) * DAY,
            "trial_end": None,
            "billing_provider": "invoice",
            "cancel_at_period_end": 0,
            "overrides": dumps(validate_overrides(overrides or {})),
            "past_due_since": None,
            "read_only_at": None,
            "deletion_scheduled_at": None,
            "export_until": None,
            "overage_enabled": 1,
        }
        self.transition(
            org_id,
            "active",
            reason="enterprise activated by operator",
            trigger_type="operator",
            trigger_id=operator.operator_id,
            fields=fields,
        )
        if invoice_cents:
            with self.db.write() as connection:
                connection.execute(
                    "INSERT INTO invoices (invoice_id, org_id, provider_invoice_id, kind, amount_cents, currency, status, period_start, period_end, created_at) VALUES (?, ?, ?, 'enterprise', ?, 'usd', 'open', ?, ?, ?)",
                    (
                        new_id(),
                        org_id,
                        reference,
                        int(invoice_cents),
                        self.now,
                        self.now + int(period_days) * DAY,
                        self.now,
                    ),
                )
        self.app.platform_audit.record(
            "billing.enterprise_activate",
            operator=operator,
            org_id=org_id,
            detail={
                "seats": int(seats),
                "period_days": int(period_days),
                "invoice_cents": int(invoice_cents),
            },
        )
        self.app.lifecycle.recompute_over_limits(org_id)
        return self.summary(org_id)

    def mark_invoice_paid(
        self, operator: Principal, org_id: str, invoice_id: str
    ) -> None:
        self.app.authz.require(operator, "platform.billing.manage")
        with self.db.write() as connection:
            changed = connection.execute(
                "UPDATE invoices SET status = 'paid', paid_at = ? WHERE invoice_id = ? AND org_id = ? AND status != 'paid'",
                (self.now, invoice_id, org_id),
            ).rowcount
            if not changed:
                raise NotFound("Invoice not found, or already paid.")
            self.app.platform_audit.record(
                "billing.invoice_paid",
                operator=operator,
                org_id=org_id,
                target_type="invoice",
                target_id=invoice_id,
                connection=connection,
            )
        subscription = self.subscription(org_id)
        if subscription["status"] in {"past_due", "read_only"}:
            self.transition(
                org_id,
                "active",
                reason="invoice marked paid by operator",
                trigger_type="operator",
                trigger_id=operator.operator_id,
                fields={
                    "past_due_since": None,
                    "read_only_at": None,
                    "deletion_scheduled_at": None,
                    "deletion_notice_at": None,
                },
            )

    def reconcile(self, operator: Principal, org_id: str) -> dict[str, Any]:
        """Compare our subscription with the provider's and report drift."""
        self.app.authz.require(operator, "platform.billing.manage")
        subscription = self.subscription(org_id)
        report: dict[str, Any] = {
            "org_id": org_id,
            "local_status": subscription["status"],
            "plan_id": subscription["plan_id"],
            "provider": subscription["billing_provider"],
        }
        provider = self.providers.get(subscription["billing_provider"] or "")
        if (
            provider is not None
            and subscription["billing_subscription_id"]
            and hasattr(provider, "_call")
        ):
            try:
                remote = getattr(provider, "_call")(
                    "GET",
                    f"/v1/subscriptions/{subscription['billing_subscription_id']}",
                )
                report["remote_status"] = remote.get("status")
                report["in_sync"] = {
                    "active": "active",
                    "past_due": "past_due",
                    "canceled": "cancelled",
                    "unpaid": "read_only",
                }.get(remote.get("status", ""), "") == subscription["status"]
            except Exception as error:  # noqa: BLE001
                report["error"] = str(error)[:200]
        else:
            report["in_sync"] = True
        self.app.platform_audit.record(
            "billing.reconcile",
            operator=operator,
            org_id=org_id,
            detail={k: v for k, v in report.items() if k != "org_id"},
        )
        return report


__all__ = ["Billing", "TRANSITIONS", "PERIOD_SECONDS", "Forbidden", "OWNER", "BILLING"]
